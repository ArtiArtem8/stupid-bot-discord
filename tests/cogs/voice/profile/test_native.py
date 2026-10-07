"""Opt-in smoke against the production native runtime and bundled artwork."""

import asyncio
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from io import BytesIO

import pytest
from PIL import Image

from api.voice.profile.details import (
    build_activity_detail,
    build_people_detail,
    build_xp_detail,
)
from api.voice.read_models import VoiceXpBreakdown
from api.voice.timeline import VoiceTimeline
from cogs.voice.profile.design import CardIdentity
from cogs.voice.profile.detail_models import DetailIdentity, PeoplePresentation
from cogs.voice.profile.media import ProfileMediaRenderer
from tests.cogs.voice.profile.test_media import profile_at


@pytest.mark.profile_native
@unittest.skipUnless(
    os.environ.get("VOICE_PROFILE_NATIVE") == "1",
    "Set VOICE_PROFILE_NATIVE=1 with Inkscape installed to run native integration",
)
class TestNativeProfile(unittest.IsolatedAsyncioTestCase):
    async def test_static_avatar_matches_full_decoder_pixels(self) -> None:
        renderer = ProfileMediaRenderer()
        try:
            await renderer.astart()
            if renderer.svg is None:
                self.fail("Native renderer did not initialize")
            for frames in (1, 2):
                output = BytesIO()
                with Image.new("RGBA", (32, 32), (255, 0, 0, 0)) as first:
                    with Image.new("RGBA", (32, 32), "blue") as second:
                        first.save(
                            output,
                            "GIF",
                            save_all=True,
                            append_images=[second] if frames == 2 else [],
                            duration=100,
                            loop=0,
                        )
                identity = CardIdentity("Name", "Guild", output.getvalue())

                def render_pair(
                    identity: CardIdentity = identity,
                ) -> tuple[bytes, bytes]:
                    svg = renderer.svg
                    if svg is None:
                        raise RuntimeError("Missing renderer")
                    expected = svg.prepare(
                        profile_at(1), identity, avatar_mode="animated"
                    ).png()
                    actual = svg.prepare(
                        profile_at(1), identity, avatar_mode="static"
                    ).png()
                    return expected, actual

                expected, actual = await asyncio.to_thread(render_pair)
                self.assertEqual(actual, expected)
        finally:
            await renderer.aclose()

    async def test_static_details_share_native_shell_and_handle_empty_and_large_labels(
        self,
    ) -> None:
        renderer = ProfileMediaRenderer()
        try:
            await renderer.astart()
            if renderer.svg is None:
                self.fail("Native renderer did not initialize")
            raster = renderer.svg.raster
            pid = raster.process_id
            timeline = VoiceTimeline((), (), ())
            as_of = datetime(2026, 10, 3, 12, tzinfo=UTC)
            identity = DetailIdentity(
                CardIdentity(
                    "Александр Оченьдлиннаяфамилия " * 4,
                    "Очень длинное название сервера " * 4,
                ),
                profile_at(35).appearance,
            )
            activity = build_activity_detail(timeline, 1, 1, as_of)
            people = PeoplePresentation(
                build_people_detail(timeline, 1, 1, as_of), {}, {}
            )
            xp = replace(
                build_xp_detail(timeline, 1, 1, as_of),
                breakdown=VoiceXpBreakdown(social_base=Fraction(10**12, 7)),
            )
            cards = [
                await renderer.render_activity(activity, identity),
                await renderer.render_people(people, identity),
                await renderer.render_xp(xp, identity),
            ]
            for card in cards:
                with self.subTest(bytes=len(card.data)):
                    self.assertEqual(card.extension, "png")
                    with Image.open(BytesIO(card.data)) as image:
                        self.assertEqual(image.size, (960, 480))
                        self.assertEqual(image.mode, "RGBA")
                        self.assertFalse(getattr(image, "is_animated", False))
                        self.assertEqual(image.getchannel("A").getpixel((0, 0)), 0)
            self.assertEqual(raster.process_id, pid)
        finally:
            await renderer.aclose()

    async def test_real_fonts_media_shell_reuse_and_shutdown(self) -> None:
        renderer = ProfileMediaRenderer()
        try:
            await renderer.astart()
            self.assertIsNotNone(renderer.svg)
            if renderer.svg is None:
                self.fail("Native renderer did not initialize")
            raster = renderer.svg.raster
            pid = raster.process_id
            process = raster._process
            self.assertIsNotNone(pid)
            self.assertIsNotNone(process)
            identity = CardIdentity("Voice Listener", "Voice Guild", b"damaged-avatar")
            lower = await renderer.render(profile_at(10), identity)
            self.assertEqual(lower.extension, "png")
            with Image.open(BytesIO(lower.data)) as image:
                self.assertEqual(image.size, (960, 480))
            upper = await renderer.render(profile_at(100), identity)
            self.assertEqual(upper.extension, "webp")
            with Image.open(BytesIO(upper.data)) as image:
                self.assertEqual(image.size, (960, 480))
                self.assertEqual(image.format, "WEBP")
                self.assertTrue(getattr(image, "is_animated", False))
            self.assertEqual(raster.process_id, pid)
        finally:
            await renderer.aclose()
        self.assertIsNone(raster.process_id)
        if process is None:
            self.fail("Native child was not created")
        self.assertIsNotNone(process.poll())
        await renderer.aclose()
        with self.assertRaises(RuntimeError):
            await asyncio.to_thread(raster.start)
