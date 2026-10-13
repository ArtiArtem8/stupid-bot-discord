"""Real Mafic transitions composed with MusicPlayer and application handlers."""

import asyncio
import copy
import unittest
from collections.abc import Mapping
from typing import Literal, override
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import mafic
from discord.ext import commands
from mafic.typings import JSONObject, TrackWithInfo
from mafic.typings import Player as PlayerPayload

from stupidbot.features.music.playback.player import MusicPlayer
from stupidbot.features.music.runtime.connection import ConnectionManager
from stupidbot.features.music.runtime.playback_events import PlaybackEventHandlers
from tests.features.music.fixtures import make_track


def track_payload(metadata: Mapping[str, str]) -> TrackWithInfo:
    user_data: JSONObject = {}
    for key, value in metadata.items():
        user_data[key] = value
    return {
        "encoded": "same",
        "info": {
            "identifier": "same",
            "isSeekable": True,
            "author": "artist",
            "length": 60000,
            "isStream": False,
            "position": 0,
            "title": "same",
            "uri": "https://example.com/track",
            "sourceName": "test",
        },
        "userData": user_data,
        "pluginInfo": {},
    }


def player_payload(
    metadata: Mapping[str, str], *, connected: bool = True
) -> PlayerPayload:
    return {
        "guildId": "1",
        "track": track_payload(metadata),
        "paused": False,
        "volume": 100,
        "filters": {},
        "state": {"time": 1000, "position": 250, "connected": connected, "ping": 1},
        "voice": {
            "sessionId": "session",
            "token": "token",
            "endpoint": "voice.example.com",
            "channelId": "10",
        },
    }


class TestMaficComposition(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.bot = MagicMock(spec=commands.Bot)
        self.guild = MagicMock(spec=discord.Guild, id=1)
        self.channel = MagicMock(spec=discord.VoiceChannel, id=10, guild=self.guild)
        self.player = MusicPlayer(self.bot, self.channel)
        self.guild.voice_client = self.player
        self.bot.get_guild.return_value = self.guild
        self.source = MagicMock(spec=mafic.Node, available=False, version=4)
        self.target = MagicMock(spec=mafic.Node, available=True, version=4)
        self.player._node = self.source
        self.player.set_state(player_payload({}))
        self.source.players = [self.player]
        self.connection = ConnectionManager(self.bot)
        self.connection.pool = MagicMock(nodes=[self.source, self.target])
        self.connection.mark_node_unavailable = AsyncMock()
        self.local_cleanup = AsyncMock()
        self.connection._cleanup_voice_client_locally = self.local_cleanup

    async def test_real_transfer_waits_for_late_transport_readiness(self) -> None:
        self.source.fetch_player = AsyncMock(
            return_value=player_payload({"__mafic_playback_id": "launch"})
        )
        self.source.destroy = AsyncMock()
        self.target.voice_update = AsyncMock()
        self.target.update = AsyncMock(
            return_value=player_payload(
                {"__mafic_playback_id": "launch"}, connected=False
            )
        )
        waiting, release = asyncio.Event(), asyncio.Event()

        async def transport_tick(_delay: float) -> None:
            waiting.set()
            await release.wait()

        with patch(
            "stupidbot.features.music.runtime.connection.asyncio.sleep",
            side_effect=transport_tick,
        ):
            task = asyncio.create_task(
                self.connection.handle_node_unavailable(self.source)
            )
            await waiting.wait()
            self.assertIs(self.player.assigned_node, self.target)
            self.assertFalse(self.player.connected)
            self.assertTrue(self.connection.is_transitioning(self.player))
            self.assertFalse(task.done())
            self.player.update_state(
                {"time": 2000, "position": 250, "connected": True, "ping": 1}
            )
            release.set()
            self.assertEqual(await task, set())
        self.assertFalse(self.connection.is_transitioning(self.player))
        self.local_cleanup.assert_not_awaited()

    async def test_transfer_timeout_and_abandoned_ownership(self) -> None:
        for outcome in ("timeout", "disconnect", "replacement", "target", "source"):
            with self.subTest(outcome=outcome):
                await self._check_transfer_outcome(outcome)

    async def _check_transfer_outcome(self, outcome: str) -> None:
        self.setUp()
        self.source.fetch_player = AsyncMock(return_value=player_payload({}))
        self.source.destroy = AsyncMock()
        self.target.voice_update = AsyncMock()
        self.target.update = AsyncMock(return_value=player_payload({}, connected=False))
        replacement = MusicPlayer(self.bot, self.channel)

        async def tick(_delay: float) -> None:
            if outcome == "disconnect":
                await self.connection.disconnect(self.guild, force=True)
            elif outcome == "replacement":
                self.guild.voice_client = replacement
            elif outcome == "target":
                self.player._node = MagicMock(spec=mafic.Node)
            elif outcome == "source":
                self.player._node = self.source

        with (
            patch(
                "stupidbot.features.music.runtime.connection.asyncio.sleep",
                side_effect=tick,
            ),
            patch(
                "stupidbot.features.music.runtime.connection.time",
                MagicMock(monotonic=MagicMock(side_effect=[0, 0, 6])),
            ),
        ):
            invalidated = await self.connection.handle_node_unavailable(self.source)
        self.assertEqual(invalidated, {1} if outcome == "timeout" else set())
        self.assertFalse(replacement.is_stale)
        if outcome == "timeout":
            self.assertTrue(self.player.is_stale)
        elif outcome in ("replacement", "target", "source"):
            self.local_cleanup.assert_not_awaited()

    async def test_dispatcher_and_handler_preserve_repeated_playback(self) -> None:
        state = MagicMock()
        ui = MagicMock()
        ui.controller.destroy_for_guild = AsyncMock()
        ui.spawn_controller = AsyncMock()
        handlers = PlaybackEventHandlers(
            self.bot, self.connection, state, ui, lambda _: False
        )
        self.addAsyncCleanup(handlers.cleanup)
        self.source.available = True
        accepted: list[TrackWithInfo] = []
        callbacks: list[asyncio.Task[None]] = []

        async def update(
            *, user_data: Mapping[str, str], **_kwargs: object
        ) -> PlayerPayload:
            response = player_payload(user_data)
            accepted.append(track_payload(user_data))
            return response

        def dispatch(name: str, event: mafic.TrackEndEvent[MusicPlayer]) -> None:
            if name == "track_end":
                callbacks.append(asyncio.create_task(handlers._on_track_end(event)))

        self.source.update = AsyncMock(side_effect=update)
        self.bot.dispatch.side_effect = dispatch
        track = make_track("same", length=60000)
        await self.player.enqueue_tracks([track, track], None, placement="end")
        await self.player.skip()
        attempt = self.player.current_attempt
        current = self.player.current
        reasons: tuple[Literal["stopped", "finished", "replaced"], ...] = (
            "stopped",
            "finished",
            "replaced",
            "finished",
        )
        for reason in reasons:
            self.player.dispatch_event(
                {
                    "op": "event",
                    "type": "TrackEndEvent",
                    "guildId": "1",
                    "track": copy.deepcopy(accepted[0]),
                    "reason": reason,
                }
            )
        await asyncio.gather(*callbacks)
        self.assertIs(self.player.current, current)
        self.assertIs(self.player.current_attempt, attempt)
        self.assertNotEqual(accepted[0].get("userData"), accepted[1].get("userData"))

    async def test_end_before_rest_response_advances_through_application_handler(
        self,
    ) -> None:
        self.source.available = True
        self.player._current = None
        ui = MagicMock()
        ui.controller.destroy_for_guild = AsyncMock()
        ui.spawn_controller = AsyncMock()
        handlers = PlaybackEventHandlers(
            self.bot, self.connection, MagicMock(), ui, lambda _: False
        )
        self.addAsyncCleanup(handlers.cleanup)
        callbacks: list[asyncio.Task[None]] = []
        accepted: list[TrackWithInfo] = []

        def dispatch(name: str, event: mafic.TrackEndEvent[MusicPlayer]) -> None:
            if name == "track_end":
                callbacks.append(asyncio.create_task(handlers._on_track_end(event)))

        async def update(
            *, user_data: Mapping[str, str], **_kwargs: object
        ) -> PlayerPayload:
            accepted.append(track_payload(user_data))
            if len(accepted) == 1:
                self.player.dispatch_event(
                    {
                        "op": "event",
                        "type": "TrackEndEvent",
                        "guildId": "1",
                        "track": accepted[0],
                        "reason": "finished",
                    }
                )
            return player_payload(user_data)

        self.bot.dispatch.side_effect = dispatch
        self.source.update = AsyncMock(side_effect=update)
        track = make_track("same", length=60000)
        await self.player.enqueue_tracks([track, track], None, placement="end")
        self.assertIsNone(self.player.current)
        await asyncio.gather(*callbacks)
        self.assertEqual(len(accepted), 2)
        self.assertIsNotNone(self.player.current)
        if self.player.current is not None:
            self.assertEqual(self.player.current.user_data, accepted[1].get("userData"))
