"""Opt-in checks for the pinned Mafic fork against a real Lavalink v4 node.

Manual run::

    RUN_LAVALINK_LIVE_TESTS=1 \
    LAVALINK_LIVE_TRACK_QUERY="Never Gonna Give You Up" \
    uv run pytest -m lavalink_live -q

The regular Lavalink connection variables are read from ``config.py``. The test
uses generated client and guild IDs and does not require a Discord voice channel.
"""

from __future__ import annotations

import os
import secrets
import unittest
from typing import cast, override
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import mafic
import pytest

import config
from api.music.models import PLAYBACK_USER_DATA_KEY


def _random_snowflake() -> int:
    """Return an ephemeral positive ID accepted by Lavalink's long fields."""
    return secrets.randbelow(2**62 - 1) + 1


@pytest.mark.lavalink_live
class TestLavalinkLive(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        if os.environ.get("RUN_LAVALINK_LIVE_TESTS") != "1":
            self.skipTest("RUN_LAVALINK_LIVE_TESTS=1 is required")
        if not config.LAVALINK_PASSWORD:
            self.skipTest("LAVALINK_PASSWORD is required")

        track_query = os.environ.get("LAVALINK_LIVE_TRACK_QUERY")
        if not track_query:
            self.skipTest("LAVALINK_LIVE_TRACK_QUERY is required")
        self.track_query = track_query

        client = MagicMock()
        client.user.id = _random_snowflake()
        client.wait_until_ready = AsyncMock()
        self.node = mafic.Node(
            host=config.LAVALINK_HOST,
            port=config.LAVALINK_PORT,
            label=f"LIVE-{uuid4().hex}",
            password=config.LAVALINK_PASSWORD,
            client=cast(discord.Client, client),
            secure=config.LAVALINK_SECURE,
        )
        await self.node.connect()

    @override
    async def asyncTearDown(self) -> None:
        node = getattr(self, "node", None)
        if node is not None:
            await node.close()

    async def _load_track(self) -> mafic.Track:
        search_type = os.environ.get("LAVALINK_LIVE_SEARCH_TYPE", "ytsearch")
        result = await self.node.fetch_tracks(
            self.track_query,
            search_type=search_type,
        )
        if result is None:
            self.fail("live Lavalink node returned no track for the configured query")
        if isinstance(result, mafic.Playlist):
            if not result.tracks:
                self.fail("live Lavalink node returned an empty playlist")
            return result.tracks[0]
        if not result:
            self.fail("live Lavalink node returned an empty track list")
        return result[0]

    async def test_connects_to_v4_and_loads_track(self) -> None:
        self.assertTrue(self.node.available)
        self.assertEqual(self.node.version, 4)

        track = await self._load_track()

        self.assertTrue(track.id)
        self.assertTrue(track.identifier)

    async def test_user_data_round_trip_through_player_api(self) -> None:
        self.assertEqual(self.node.version, 4)
        track = await self._load_track()
        guild_id = _random_snowflake()
        token = uuid4().hex

        try:
            updated = await self.node.update(
                guild_id=guild_id,
                track=track,
                pause=True,
                volume=37,
                user_data={PLAYBACK_USER_DATA_KEY: token},
            )
            updated_data = updated["track"]
            if updated_data is None:
                self.fail("live Lavalink update omitted the current track")
            updated_track = mafic.Track.from_data_with_info(updated_data)
            self.assertEqual(updated_track.user_data[PLAYBACK_USER_DATA_KEY], token)

            await self.node.update(guild_id=guild_id, volume=41, pause=True)
            fetched = await self.node.fetch_player(guild_id)
            fetched_data = fetched["track"]
            if fetched_data is None:
                self.fail("live Lavalink fetch omitted the current track")
            fetched_track = mafic.Track.from_data_with_info(fetched_data)

            self.assertEqual(fetched_track.user_data[PLAYBACK_USER_DATA_KEY], token)
            self.assertEqual(fetched["volume"], 41)
            self.assertTrue(fetched["paused"])
        finally:
            await self.node.destroy(guild_id)
