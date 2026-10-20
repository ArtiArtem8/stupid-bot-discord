"""Recovery and leave through the composed music graph with offline transports."""

import asyncio
import copy
import secrets
import unittest
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Literal, override
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import mafic
from discord.ext import commands
from mafic.typings import JSONValue, TrackWithInfo
from mafic.typings import Player as PlayerPayload
from mafic.typings.incoming import TrackEndEvent as TrackEndPayload

from stupidbot.features.music.composition import create_music_components
from stupidbot.features.music.playback.models import PlaybackAttempt
from stupidbot.features.music.playback.player import MusicPlayer
from stupidbot.features.music.repository import VolumeData, VolumeRepository
from stupidbot.features.music.results import MusicResult
from stupidbot.features.music.runtime import recovery
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
                "token": secrets.token_hex(16),
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

    @asynccontextmanager
    async def _restoring_confirmation(
        self,
    ) -> AsyncGenerator[tuple[MusicPlayer, asyncio.Event, asyncio.Task[bool]]]:
        entered, release = asyncio.Event(), asyncio.Event()
        confirm = self.components.healer._confirm_restored_track_active

        async def hold_confirmation(
            player: MusicPlayer,
            attempt: PlaybackAttempt,
            *,
            guild_id: int,
            context: str,
        ) -> bool:
            entered.set()
            await release.wait()
            return await confirm(player, attempt, guild_id=guild_id, context=context)

        with (
            patch.object(
                self.components.healer,
                "_confirm_restored_track_active",
                side_effect=hold_confirmation,
            ),
            patch.object(recovery, "RESTORE_CONFIRM_DELAY_SECONDS", 0),
        ):
            healing = asyncio.create_task(self.components.service.heal(1))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                yield self._current_player(), release, healing
            finally:
                release.set()
                if not healing.done():
                    healing.cancel()
                await asyncio.gather(healing, return_exceptions=True)

    async def _dispatch_restore_end(
        self, player: MusicPlayer, reason: Literal["finished", "loadFailed"]
    ) -> mafic.TrackEndEvent[MusicPlayer]:
        track = player.current
        if track is None:
            self.fail("Expected active transport playback")
        await self.components.playback_events._on_track_start(
            mafic.TrackStartEvent(player=player, track=track)
        )
        payload: TrackEndPayload = {
            "op": "event",
            "type": "TrackEndEvent",
            "guildId": "1",
            "track": track_payload(track, track.user_data),
            "reason": reason,
        }
        self.remote["track"] = None
        player.dispatch_event(payload)
        return mafic.TrackEndEvent(player=player, track=track, payload=payload)

    async def test_restore_completion_before_listener_preserves_queue_and_history(
        self,
    ) -> None:
        self.original._position = 59_500
        async with self._restoring_confirmation() as (player, release, healing):
            event = await self._dispatch_restore_end(player, "finished")
            release.set()
            self.assertTrue(await asyncio.wait_for(healing, 5))
            self.assertIs(self._current_player(), player)
            current = player.current_entry
            if current is None:
                self.fail("Expected the next queued entry")
            self.assertEqual(current.track.identifier, "next")
            self.assertEqual(
                [entry.track.identifier for entry in player.queue], ["last"]
            )
            await self.components.playback_events._on_track_end(event)
            await self.components.playback_events._on_track_end(event)
            self.assertIs(player.current_entry, current)
            session = self.components.state.get_session(1)
            if session is None:
                self.fail("Expected preserved session")
            self.assertEqual(
                [track.title for track in session.tracks], ["Track current"]
            )
            controller = self.components.controllers.controllers[1]
            self.assertIs(controller.attempt, player.current_attempt)

    async def test_restore_completion_after_listener_keeps_idle_session_connected(
        self,
    ) -> None:
        self.original.clear_queue()
        async with self._restoring_confirmation() as (player, release, healing):
            event = await self._dispatch_restore_end(player, "finished")
            await self.components.playback_events._on_track_end(event)
            release.set()
            self.assertTrue(await asyncio.wait_for(healing, 5))
            self.assertIs(self._current_player(), player)
            self.assertIsNone(player.current_attempt)
            self.assertEqual(player.queue_snapshot(), ())
            self.assertNotIn(1, self.components.controllers.controllers)

    async def test_warm_completion_and_successor_failure_restore_audible_queue(
        self,
    ) -> None:
        current = self.original.current_entry
        if current is None:
            self.fail("Expected original entry")
        current.track.source = "youtube"
        self.original._position = 59_500
        async with self._restoring_confirmation() as (player, release, healing):
            with patch.object(
                player, "seek_attempt", wraps=player.seek_attempt
            ) as seek:
                finished = await self._dispatch_restore_end(player, "finished")
                await self.components.playback_events._on_track_end(finished)
                self.assertEqual(self.remote["volume"], 0)
                failed = await self._dispatch_restore_end(player, "loadFailed")
                await self.components.playback_events._on_track_end(failed)
                release.set()
                self.assertTrue(await asyncio.wait_for(healing, 5))
                seek.assert_not_awaited()
            successor = player.current_entry
            if successor is None:
                self.fail("Expected the last queued entry")
            self.assertEqual(successor.track.identifier, "last")
            self.assertEqual(player.queue_snapshot(), ())
            self.assertEqual(self.remote["volume"], 37)
            self.assertFalse(player.paused)
            self.assertIs(
                self.components.controllers.controllers[1].attempt,
                player.current_attempt,
            )

    async def test_original_restore_load_failure_still_fails(self) -> None:
        async with self._restoring_confirmation() as (player, release, healing):
            event = await self._dispatch_restore_end(player, "loadFailed")
            await self.components.playback_events._on_track_end(event)
            release.set()
            self.assertFalse(await asyncio.wait_for(healing, 5))
            self.assertIsNone(self.guild.voice_client)
            self.assertNotIn(1, self.components.controllers.controllers)

    async def test_restore_finishing_during_controller_delivery_publishes_successor(
        self,
    ) -> None:
        entered, release_delivery = asyncio.Event(), asyncio.Event()
        spawn = self.components.ui.spawn_controller

        async def hold_original_controller(
            player: MusicPlayer, attempt: PlaybackAttempt
        ) -> None:
            if attempt.entry.track.identifier == "current":
                entered.set()
                await release_delivery.wait()
            await spawn(player, attempt)

        async with self._restoring_confirmation() as (player, release, healing):
            with patch.object(
                self.components.ui,
                "spawn_controller",
                side_effect=hold_original_controller,
            ):
                try:
                    release.set()
                    await asyncio.wait_for(entered.wait(), 5)
                    event = await self._dispatch_restore_end(player, "finished")
                    release_delivery.set()
                    self.assertTrue(await asyncio.wait_for(healing, 5))
                finally:
                    release_delivery.set()

            successor = player.current_entry
            if successor is None:
                self.fail("Expected the next queued entry")
            self.assertEqual(successor.track.identifier, "next")
            self.assertIs(
                self.components.controllers.controllers[1].attempt,
                player.current_attempt,
            )
            await self.components.playback_events._on_track_end(event)
            self.assertIs(player.current_entry, successor)

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

    async def test_unavailable_node_preserves_cached_playback_for_later_recovery(
        self,
    ) -> None:
        current = self.original.current_entry
        queued = self.original.queue_snapshot()
        self.assertIsNotNone(current)
        self.assertEqual(len(queued), 2)
        self.original.mark_stale()
        self.node._available = False
        with patch.object(
            mafic.NodePool,
            "add_node",
            new=AsyncMock(side_effect=AssertionError("Unexpected node connection")),
        ) as connect:
            for deadline in (float("inf"), 0.0):
                with self.subTest(retry_deadline=deadline):
                    self.components.connection._next_connect_retry_at = deadline
                    self.assertFalse(
                        await asyncio.wait_for(self.components.service.heal(1), 5)
                    )
                    self.assertIs(self.guild.voice_client, self.original)
                    self.assertIs(
                        self.components.healer.get_recoverable_player(1), self.original
                    )
                    self.assertEqual(self.original.current_entry, current)
                    self.assertEqual(self.original.queue_snapshot(), queued)
            connect.assert_not_awaited()

        self.node._available = True
        self.assertTrue(await asyncio.wait_for(self.components.service.heal(1), 10))

        restored = self._current_player()
        self.assertIsNot(restored, self.original)
        self.assertEqual(restored.current_entry, current)
        self.assertEqual(restored.queue_snapshot(), queued)

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
