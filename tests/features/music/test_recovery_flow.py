"""Recovery and leave through the composed music graph with offline transports."""

import asyncio
import copy
import unittest
from collections.abc import Mapping
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import mafic
from discord.ext import commands
from mafic.typings import JSONValue, TrackWithInfo
from mafic.typings import Player as PlayerPayload

from stupidbot.features.music.composition import create_music_components
from stupidbot.features.music.playback.player import MusicPlayer
from stupidbot.features.music.repository import VolumeData, VolumeRepository
from stupidbot.features.music.results import MusicResult
from tests.features.music.fixtures import make_playlist, make_track
from tests.storage.fixtures import temporary_database


def track_payload(
    track: mafic.Track, metadata: Mapping[str, JSONValue]
) -> TrackWithInfo:
    return {
        "encoded": track.id,
        "info": {
            "identifier": track.identifier,
            "isSeekable": track.seekable,
            "author": track.author,
            "length": track.length,
            "isStream": track.stream,
            "position": 0,
            "title": track.title,
            "uri": track.uri,
            "sourceName": track.source,
        },
        "userData": dict(metadata),
        "pluginInfo": {},
    }


class TestComposedSessionRecovery(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, database = await temporary_database(self)
        self.volumes = VolumeRepository(database)
        await self.volumes.save(VolumeData(1, 37))
        self.bot = MagicMock(spec=commands.Bot)
        self.bot.is_closed.return_value = False
        self.guild = MagicMock(spec=discord.Guild, id=1, voice_client=None)
        self.guild.change_voice_state = AsyncMock()
        self.channel = MagicMock(spec=discord.VoiceChannel, id=10, guild=self.guild)
        self.channel._get_voice_client_key.return_value = (1, "guild_id")
        self.channel.connect = AsyncMock(side_effect=self._connect_voice)
        self.guild.get_channel.return_value = self.channel
        self.bot.get_guild.return_value = self.guild
        self.bot.guilds = [self.guild]
        self.bot._connection = MagicMock(
            _remove_voice_client=MagicMock(side_effect=self._remove_voice_client)
        )
        self.text_channel = MagicMock(spec=discord.TextChannel, id=20)
        message = MagicMock(spec=discord.Message, id=30, channel=self.text_channel)
        self.text_channel.send = AsyncMock(return_value=message)
        self.text_channel.get_partial_message.return_value.delete = AsyncMock()
        self.bot.get_channel.return_value = self.text_channel
        self.node = mafic.Node[discord.Client](
            host="localhost",
            port=2333,
            label="recovery",
            password=self.id(),
            client=self.bot,
        )
        self.node._version = 4
        self.node._available = True
        self.enterContext(
            patch.object(mafic.NodePool, "_nodes", {self.node.label: self.node})
        )
        self.enterContext(patch.object(mafic.Node, "destroy", new=AsyncMock()))
        self.playback_requested = asyncio.Event()
        self.playback_cancelled = asyncio.Event()
        self.release_playback = asyncio.Event()
        self.release_playback.set()
        self.enterContext(
            patch.object(
                mafic.Node, "update", new=AsyncMock(side_effect=self._update_player)
            )
        )
        tracks = [
            make_track(name, length=60_000) for name in ("current", "next", "last")
        ]
        self.enterContext(
            patch.object(
                mafic.Node,
                "fetch_tracks",
                new=AsyncMock(
                    side_effect=[make_playlist("queue", tracks), [tracks[0]]]
                ),
            )
        )
        self.components = create_music_components(self.bot, self.volumes)
        self.addAsyncCleanup(self.components.controllers.cleanup)
        self.addAsyncCleanup(self.components.service.cleanup)
        result = await self.components.service.play(
            self.guild, self.channel, "queue", 42, 20
        )
        self.assertTrue(result.is_success)
        self.original = self._current_player()
        await self.components.playback_events._on_track_start(
            MagicMock(player=self.original, track=self.original.current)
        )
        self.assertIn(1, self.components.controllers.controllers)

    def _remove_voice_client(self, _guild_id: int) -> None:
        self.guild.voice_client = None

    async def _connect_voice(self, **_kwargs: object) -> MusicPlayer:
        self.remote: PlayerPayload = {
            "guildId": "1",
            "track": None,
            "paused": False,
            "volume": 100,
            "filters": {},
            "state": {"time": 1000, "position": 0, "connected": True, "ping": 1},
            "voice": {
                "sessionId": "session",
                "token": "token",
                "endpoint": "voice.example.com",
                "channelId": "10",
            },
        }
        player = MusicPlayer(self.bot, self.channel)
        player._node = self.node
        player.set_state(copy.deepcopy(self.remote))
        self.node.add_player(1, player)
        self.guild.voice_client = player
        return player

    async def _update_player(
        self,
        *,
        track: object = None,
        user_data: Mapping[str, JSONValue] | None = None,
        volume: int | None = None,
        position: int | None = None,
        pause: bool | None = None,
        **_kwargs: object,
    ) -> PlayerPayload:
        if isinstance(track, mafic.Track):
            self.playback_requested.set()
            try:
                await self.release_playback.wait()
            except asyncio.CancelledError:
                self.playback_cancelled.set()
                await self.release_playback.wait()
                raise
            self.remote["track"] = track_payload(track, user_data or {})
        if volume is not None:
            self.remote["volume"] = volume
        if position is not None:
            self.remote["state"]["position"] = position
        if pause is not None:
            self.remote["paused"] = pause
        return copy.deepcopy(self.remote)

    def _current_player(self) -> MusicPlayer:
        player = self.components.connection.get_player(1)
        if player is None:
            self.fail("Expected a usable voice player")
        return player

    async def test_heal_restores_current_queue_and_persisted_volume(self) -> None:
        current = self.original.current_entry
        queued = self.original.queue_snapshot()
        session = self.components.state.get_session(1)
        controller = self.components.controllers.controllers[1]
        self.assertIsNotNone(current)
        self.assertEqual(len(queued), 2)
        self.assertIsNotNone(session)
        self.assertTrue((await self.components.service.pause(1)).is_success)
        await self.volumes.save(VolumeData(1, 73))
        self.assertEqual(self.remote["volume"], 37)
        self.original.mark_stale()
        self.assertIsNone(self.components.connection.get_player(1))
        self.assertIs(self.components.healer.get_recoverable_player(1), self.original)

        self.assertTrue(await asyncio.wait_for(self.components.service.heal(1), 10))

        restored = self._current_player()
        self.assertIsNot(restored, self.original)
        self.assertTrue(self.original.is_stale)
        self.assertEqual(restored.current_entry, current)
        self.assertEqual(restored.queue_snapshot(), queued)
        self.assertEqual(self.remote["volume"], 73)
        self.assertTrue(restored.paused)
        self.assertEqual(await self.volumes.get_volume(1), 73)
        self.assertIs(self.components.state.get_session(1), session)
        self.assertIs(self.node.get_player(1), restored)
        self.assertTrue(controller.is_finished())
        replacement = self.components.controllers.controllers[1]
        self.assertIs(replacement.player, restored)
        self.assertIs(replacement.attempt, restored.current_attempt)
        self.assertFalse(self.components.voice_lifecycle.is_healing(1))
        self.assertFalse(self.components.connection.is_recovering(1))

    async def test_leave_drains_pending_restore_without_resurrecting_session(
        self,
    ) -> None:
        self.playback_requested.clear()
        self.release_playback.clear()
        self.original.mark_stale()
        self.assertIsNone(self.components.connection.get_player(1))
        self.assertIs(self.components.healer.get_recoverable_player(1), self.original)
        healing = asyncio.create_task(self.components.service.heal(1))
        leaving: asyncio.Task[MusicResult[None]] | None = None
        try:
            await asyncio.wait_for(self.playback_requested.wait(), 5)
            restoring = self._current_player()
            self.assertIsNot(restoring, self.original)
            self.assertTrue(self.components.connection.is_recovering(1))
            leaving = asyncio.create_task(self.components.service.leave(self.guild))
            await asyncio.wait_for(self.playback_cancelled.wait(), 5)
            self.assertFalse(leaving.done())
            self.release_playback.set()
            self.assertTrue((await asyncio.wait_for(leaving, 5)).is_success)
            with self.assertRaises(asyncio.CancelledError):
                await healing
            self.assertIsNone(self.guild.voice_client)
            self.assertIsNone(self.node.get_player(1))
            self.assertIsNone(self.components.state.get_session(1))
            self.assertNotIn(1, self.components.controllers.controllers)
            self.assertEqual(restoring.queue_snapshot(), ())
            self.assertFalse(self.components.state.is_timer_active(1))
            self.assertFalse(self.components.voice_lifecycle.is_healing(1))
            self.assertFalse(self.components.connection.is_recovering(1))
            self.text_channel.send.assert_awaited_once()
        finally:
            self.release_playback.set()
            tasks: list[asyncio.Task[object]] = [healing]
            if leaving is not None:
                tasks.append(leaving)
            for task in tasks:
                task.cancel()
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=5
            )
