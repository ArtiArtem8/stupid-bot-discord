"""Delivery policy and owned renderer lifecycle, without native tools."""

import asyncio
import unittest
from dataclasses import replace
from io import BytesIO
from threading import Event
from unittest.mock import MagicMock, patch

from PIL import Image

from api.progression.appearance import LevelAppearancePolicy
from api.voice.profile.build import build_profile
from api.voice.profile.model import VoiceProfile
from api.voice.timeline import VoiceTimeline
from cogs.voice.profile import media as media_module
from cogs.voice.profile.avatar import load_avatar
from cogs.voice.profile.design import CardIdentity, normalized_png
from cogs.voice.profile.media import ProfileMedia, ProfileMediaRenderer, encode_webp
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
            encode.assert_not_called()
            for level in (20, 35, 50, 75, 100):
                result = await renderer.render(profile_at(level), identity)
                self.assertEqual(result, ProfileMedia(b"webp", "webp", b"png"))
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
