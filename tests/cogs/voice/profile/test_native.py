"""Opt-in smoke against the production native runtime and bundled artwork."""

import asyncio
import os
import unittest
from io import BytesIO

import pytest
from PIL import Image

from cogs.voice.profile.design import CardIdentity
from cogs.voice.profile.media import ProfileMediaRenderer
from tests.cogs.voice.profile.test_media import profile_at


@pytest.mark.profile_native
@unittest.skipUnless(
    os.environ.get("VOICE_PROFILE_NATIVE") == "1",
    "Set VOICE_PROFILE_NATIVE=1 with Inkscape installed to run native integration",
)
class TestNativeProfile(unittest.IsolatedAsyncioTestCase):
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
