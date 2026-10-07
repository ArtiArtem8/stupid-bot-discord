"""Delivery policy and owned renderer lifecycle, without native tools."""

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from threading import Event
from unittest.mock import MagicMock, patch

from PIL import Image

from api.progression.appearance import LevelAppearancePolicy
from api.voice.profile.build import build_profile
from api.voice.profile.details import XpDetail, build_xp_detail
from api.voice.profile.model import VoiceProfile
from api.voice.timeline import VoiceTimeline
from cogs.voice.profile import media as media_module
from cogs.voice.profile.avatar import load_avatar
from cogs.voice.profile.design import CardIdentity, normalized_png
from cogs.voice.profile.detail_models import DetailIdentity
from cogs.voice.profile.detail_renderer import DetailCardRenderer
from cogs.voice.profile.media import (
    ProfileMedia,
    ProfileMediaRenderer,
    RenderBusyError,
    encode_webp,
)
from cogs.voice.profile.raster import Box
from cogs.voice.profile.svg_renderer import PreparedCard, SvgProfileRenderer


def profile_at(level: int = 1) -> VoiceProfile:
    profile = build_profile(
        VoiceTimeline((), (), ()),
        10,
        42,
        "UTC",
    )
    return replace(
        profile, level=level, appearance=LevelAppearancePolicy().for_level(level)
    )


class TestMedia(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_detail_waiter_keeps_worker_owned_until_shutdown(
        self,
    ) -> None:
        svg = MagicMock(spec=SvgProfileRenderer)
        details = MagicMock(spec=DetailCardRenderer)
        started, release = asyncio.Event(), Event()
        loop = asyncio.get_running_loop()
        renderer = ProfileMediaRenderer(svg=svg)
        renderer.details = details
        detail = build_xp_detail(
            VoiceTimeline((), (), ()), 1, 1, datetime(2026, 10, 3, tzinfo=UTC)
        )
        identity = DetailIdentity(CardIdentity("N", "G"), profile_at().appearance)

        def render(_detail: XpDetail, _identity: DetailIdentity) -> bytes:
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise TimeoutError("Test did not release detail worker")
            return b"png"

        details.render_xp.side_effect = render
        waiter = asyncio.create_task(renderer.render_xp(detail, identity))
        await started.wait()
        try:
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            with self.assertRaises(RenderBusyError):
                await renderer.render(profile_at(), identity.card)
            svg.close.assert_not_called()
        finally:
            release.set()
            await renderer.aclose()
        svg.close.assert_called_once()

    async def test_tier_policy_and_prepared_png_fallback(self) -> None:
        svg = MagicMock(spec=SvgProfileRenderer)
        prepared = MagicMock(spec=PreparedCard)
        prepared.png.return_value = b"png"
        prepared.prepare_seconds = 0.1
        svg.prepare.return_value = prepared
        renderer = ProfileMediaRenderer(svg=svg)
        identity = CardIdentity("Name", "Guild")
        with patch.object(media_module, "encode_webp", return_value=b"webp") as encode:
            for level in (1, 5, 10):
                self.assertEqual(
                    (await renderer.render(profile_at(level), identity)).extension,
                    "png",
                )
            svg.prepare.assert_called_with(
                profile_at(10), identity, avatar_mode="static"
            )
            encode.assert_not_called()
            for level in (20, 35, 50, 75, 100):
                result = await renderer.render(profile_at(level), identity)
                self.assertEqual(result, ProfileMedia(b"webp", "webp", b"png"))
        svg.prepare.assert_called_with(
            profile_at(100), identity, avatar_mode="animated"
        )
        svg.prepare.reset_mock()
        with patch.object(media_module, "encode_webp", side_effect=OSError("encoder")):
            with self.assertLogs(media_module.logger, level="WARNING"):
                result = await renderer.render(profile_at(20), identity)
        self.assertEqual(result, ProfileMedia(b"png", "png"))
        svg.prepare.assert_called_once()
        await renderer.aclose()
        await renderer.aclose()
        svg.close.assert_called_once()

    async def test_prepare_failure_is_not_animation_fallback(self) -> None:
        svg = MagicMock(spec=SvgProfileRenderer)
        svg.prepare.side_effect = RuntimeError("native unavailable")
        renderer = ProfileMediaRenderer(svg=svg)
        with patch.object(media_module, "encode_webp") as encode:
            with self.assertRaises(RuntimeError):
                await renderer.render(profile_at(20), CardIdentity("N", "G"))
        encode.assert_not_called()
        await renderer.aclose()

    async def test_close_waits_for_native_worker_after_cancelled_waiter(self) -> None:
        svg = MagicMock(spec=SvgProfileRenderer)
        started = asyncio.Event()
        release = Event()
        loop = asyncio.get_running_loop()
        renderer = ProfileMediaRenderer(svg=svg)

        def render(_profile: VoiceProfile, _identity: CardIdentity) -> ProfileMedia:
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise TimeoutError("Test did not release worker")
            return ProfileMedia(b"png", "png")

        with patch.object(renderer, "_render_sync", side_effect=render):
            waiter = asyncio.create_task(
                renderer.render(profile_at(), CardIdentity("N", "G"))
            )
            await started.wait()
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            svg.close.assert_not_called()
            release.set()
            await renderer.aclose()
        svg.close.assert_called_once()


class TestMediaBytes(unittest.TestCase):
    def test_attachment_limit_uses_existing_still(self) -> None:
        media = ProfileMedia(b"animated", "webp", b"png")
        self.assertIs(media.within(8), media)
        self.assertEqual(media.within(3), ProfileMedia(b"png", "png"))
        with self.assertRaises(ValueError):
            media.within(2)

    def test_encoder_keeps_eighty_frames_four_seconds_and_lossless_options(
        self,
    ) -> None:
        prepared = MagicMock(spec=PreparedCard)
        prepared.design.width, prepared.design.height = 960, 480
        frame = MagicMock(spec=Image.Image)
        prepared.frame.return_value = frame
        encode_webp(prepared)
        self.assertEqual(prepared.frame.call_count, 80)
        self.assertEqual(prepared.frame.call_args.args, (79 / 80,))
        options = frame.save.call_args.kwargs
        self.assertEqual(options["duration"], 50)
        self.assertTrue(options["lossless"])
        self.assertEqual(options["kmax"], 0)
        self.assertEqual(options["method"], 1)

    def test_webp_closes_frames_after_success_or_encoding_failure(self) -> None:
        for fail in (False, True):
            with self.subTest(fail=fail):
                prepared = MagicMock(spec=PreparedCard)
                prepared.design.width, prepared.design.height = 2, 2
                frames = [Image.new("RGBA", (2, 2), "red") for _ in range(80)]
                for frame in frames:
                    self.addCleanup(frame.close)
                prepared.frame.side_effect = frames
                if fail:
                    with patch.object(
                        Image.Image, "save", side_effect=OSError("encode")
                    ):
                        with self.assertRaisesRegex(OSError, "encode"):
                            encode_webp(prepared)
                else:
                    data = encode_webp(prepared)
                    with Image.open(BytesIO(data)) as result:
                        self.assertEqual(
                            result.convert("RGBA").getpixel((0, 0)), (255, 0, 0, 255)
                        )
                for frame in frames:
                    with self.assertRaises(ValueError):
                        frame.getpixel((0, 0))

    def test_webp_closes_partial_frames_when_composition_fails(self) -> None:
        prepared = MagicMock(spec=PreparedCard)
        prepared.design.width, prepared.design.height = 2, 2
        frame = Image.new("RGBA", (2, 2), "red")
        self.addCleanup(frame.close)
        prepared.frame.side_effect = [frame, RuntimeError("composition")]
        with self.assertRaisesRegex(RuntimeError, "composition"):
            encode_webp(prepared)
        with self.assertRaises(ValueError):
            frame.getpixel((0, 0))

    def test_png_closes_temporary_frame_after_success_or_encoding_failure(self) -> None:
        for fail in (False, True):
            with self.subTest(fail=fail):
                prepared = MagicMock(spec=PreparedCard)
                frame = Image.new("RGBA", (2, 2), "blue")
                self.addCleanup(frame.close)
                prepared.frame.return_value = frame
                if fail:
                    with patch.object(
                        Image.Image, "save", side_effect=OSError("encode")
                    ):
                        with self.assertRaisesRegex(OSError, "encode"):
                            PreparedCard.png(prepared)
                else:
                    data = PreparedCard.png(prepared)
                    with Image.open(BytesIO(data)) as result:
                        self.assertEqual(result.getpixel((0, 0)), (0, 0, 255, 255))
                with self.assertRaises(ValueError):
                    frame.getpixel((0, 0))

    def test_animated_avatar_decoder_and_corruption_limits(self) -> None:
        output = BytesIO()
        first = Image.new("RGBA", (16, 16), "red")
        second = Image.new("RGBA", (16, 16), "blue")
        first.save(
            output, "GIF", save_all=True, append_images=[second], duration=100, loop=0
        )
        avatar = load_avatar(output.getvalue(), Box(0, 0, 16, 16))
        self.assertIsNotNone(avatar)
        if avatar is None:
            self.fail("GIF was not decoded")
        self.assertEqual(len(avatar.frames), 2)
        self.assertNotEqual(avatar.at(0).tobytes(), avatar.at(0.025).tobytes())
        with self.assertRaises(ValueError):
            load_avatar(b"bad", Box(0, 0, 16, 16))
        with self.assertRaises(ValueError):
            normalized_png(b"x" * (4 * 1024 * 1024 + 1))
