"""Opt-in smoke test for the Wolfram plot pipeline.

Manual run::

    RUN_WOLFRAM_LIVE_TESTS=1 \
    WOLFRAM_APP_ID="$WOLFRAM_APP_ID" \
    uv run pytest -m wolfram_live -q
"""

from __future__ import annotations

import asyncio
import io
import os
import unittest

import aiohttp
import pytest
from PIL import Image

import config
from api.wolfram import WolframClient
from utils.image_utils import process_wolfram_plot


@pytest.mark.wolfram_live
class TestWolframLive(unittest.IsolatedAsyncioTestCase):
    async def test_plot_sin_pipeline(self) -> None:
        if os.environ.get("RUN_WOLFRAM_LIVE_TESTS") != "1":
            self.skipTest("RUN_WOLFRAM_LIVE_TESTS=1 is required")
        app_id = os.environ.get("WOLFRAM_APP_ID")
        if not app_id:
            self.skipTest("WOLFRAM_APP_ID is required")

        async with aiohttp.ClientSession() as session:
            client = WolframClient(app_id, session=session)
            result = await client.query("plot sin(x)")
            self.assertTrue(result.success)
            plot_url = result.plot_url
            if plot_url is None:
                self.fail("live Wolfram response did not include a plot URL")
            source = await client.fetch_plot_image(
                plot_url,
                max_bytes=config.WOLFRAM_PLOT_MAX_DOWNLOAD_BYTES,
            )

        with Image.open(io.BytesIO(source)) as source_image:
            source_image.load()
            self.assertGreater(source_image.width, 0)
            self.assertGreater(source_image.height, 0)

        output = await asyncio.to_thread(
            process_wolfram_plot,
            source,
            target_width=config.WOLFRAM_PLOT_TARGET_WIDTH,
            max_size=config.WOLFRAM_PLOT_MAX_SIZE,
            max_source_pixels=config.WOLFRAM_PLOT_MAX_SOURCE_PIXELS,
            max_output_bytes=config.WOLFRAM_PLOT_MAX_UPLOAD_BYTES,
            quality=config.WOLFRAM_PLOT_QUALITY,
            fallback_qualities=config.WOLFRAM_PLOT_FALLBACK_QUALITIES,
        )

        self.assertLessEqual(len(output), config.WOLFRAM_PLOT_MAX_UPLOAD_BYTES)
        with Image.open(io.BytesIO(output)) as processed:
            processed.load()
            self.assertEqual(processed.format, "WEBP")
            self.assertLessEqual(processed.width, config.WOLFRAM_PLOT_MAX_SIZE[0])
            self.assertLessEqual(processed.height, config.WOLFRAM_PLOT_MAX_SIZE[1])
