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

from stupidbot.features.music.discord.controller_delivery import UIOrchestrator
from stupidbot.features.music.discord.views import (
    TrackControllerManager,
    TrackControllerView,
)
from stupidbot.features.music.playback.models import (
    PlaybackAttempt,
    QueueEntry,
    TrackRequester,
)
from stupidbot.features.music.playback.player import MusicPlayer
from stupidbot.features.music.runtime.connection import ConnectionManager
from stupidbot.features.music.runtime.playback_events import PlaybackEventHandlers
from stupidbot.features.music.runtime.voice_lifecycle import VoiceLifecycleHandlers
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


class TestMaficFailureCleanup(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.bot = MagicMock(spec=commands.Bot)
        self.bot.is_closed.return_value = False
        self.guild = MagicMock(spec=discord.Guild, id=1)
        self.guild.get_member.return_value = None
        self.guild.change_voice_state = AsyncMock()
        self.channel = MagicMock(spec=discord.VoiceChannel, id=10, guild=self.guild)
        self.channel._get_voice_client_key.return_value = (1, "guild_id")
        self.player = MusicPlayer(self.bot, self.channel)
        self.guild.voice_client = self.player
        self.bot.get_guild.return_value = self.guild
        self.bot._connection = MagicMock(
            _remove_voice_client=MagicMock(side_effect=self._remove_voice_client)
        )
        self.source = mafic.Node[discord.Client](
            host="localhost",
            port=2333,
            label="source",
            password=self.id(),
            client=self.bot,
        )
        self.target = mafic.Node[discord.Client](
            host="localhost",
            port=2334,
            label="target",
            password=self.id(),
            client=self.bot,
        )
        self.source._version = self.target._version = 4
        self.target._available = True
        self.player._node = self.source
        self.player.set_state(player_payload({}))
        self.source.add_player(1, self.player)
        nodes: dict[str, mafic.Node[discord.Client]] = {
            "source": self.source,
            "target": self.target,
        }
        self.enterContext(patch.object(mafic.NodePool, "_nodes", nodes))
        self.connection = ConnectionManager(self.bot)
        self.destroy = AsyncMock()

        async def destroy(node: mafic.Node[discord.Client], *, guild_id: int) -> None:
            await self.destroy(node, guild_id=guild_id)

        self.enterContext(patch.object(mafic.Node, "destroy", new=destroy))
        self.enterContext(
            patch.object(
                mafic.Node,
                "fetch_player",
                new=AsyncMock(return_value=player_payload({})),
            )
        )
        self.enterContext(patch.object(mafic.Node, "voice_update", new=AsyncMock()))
        self.enterContext(
            patch.object(
                mafic.Node,
                "update",
                new=AsyncMock(return_value=player_payload({}, connected=False)),
            )
        )

    def _remove_voice_client(self, _guild_id: int) -> None:
        self.guild.voice_client = None

    async def test_transfer_timeout_destroys_only_failed_target_player(self) -> None:
        other = MusicPlayer(
            self.bot, MagicMock(spec=discord.VoiceChannel, guild=MagicMock(id=2))
        )
        other._node = self.target
        self.target.add_player(2, other)
        with patch(
            "stupidbot.features.music.runtime.connection.time",
            MagicMock(monotonic=MagicMock(side_effect=[0, 6, 7])),
        ):
            invalidated = await self.connection.handle_node_unavailable(self.source)

        self.assertEqual(invalidated, {1})
        self.assertIsNone(self.player.assigned_node)
        self.assertIsNone(self.guild.voice_client)
        self.assertIsNone(self.target.get_player(1))
        self.destroy.assert_any_await(self.target, guild_id=1)
        self.assertEqual(self.target.players, [other])
        self.assertIs(other.assigned_node, self.target)
        self.assertFalse(other.is_stale)
        self.assertTrue(self.target.available)
        self.assertEqual(self.destroy.await_count, 2)

    async def test_replacement_during_source_close_survives_failed_transfer_cleanup(
        self,
    ) -> None:
        replacement = MusicPlayer(self.bot, self.channel)
        replacement._node = self.target
        session = MagicMock()
        entered, release = asyncio.Event(), asyncio.Event()

        async def close() -> None:
            entered.set()
            await release.wait()

        session.close = AsyncMock(side_effect=close)
        self.source._ws = session
        with patch(
            "stupidbot.features.music.runtime.connection.time",
            MagicMock(monotonic=MagicMock(side_effect=[0, 6, 7])),
        ):
            task = asyncio.create_task(
                self.connection.handle_node_unavailable(self.source)
            )
            await entered.wait()
            self.guild.voice_client = replacement
            self.target.add_player(1, replacement)
            release.set()
            await task

        self.assertIs(self.guild.voice_client, replacement)
        self.assertIs(self.target.get_player(1), replacement)
        self.assertIs(replacement.assigned_node, self.target)
        self.assertFalse(replacement.is_stale)
        self.guild.change_voice_state.assert_not_awaited()
        self.destroy.assert_awaited_once_with(self.source, guild_id=1)

    async def _make_controller(
        self,
    ) -> tuple[
        VoiceLifecycleHandlers, TrackControllerManager, TrackControllerView, MagicMock
    ]:
        self.source._available = True
        attempt = PlaybackAttempt(
            1, QueueEntry(1, make_track("same", length=60000), TrackRequester(42, 99))
        )
        self.player._current_attempt = attempt
        text_channel = MagicMock(spec=discord.TextChannel, id=99)
        text_channel.send = AsyncMock(
            side_effect=[
                MagicMock(id=101, channel=text_channel),
                MagicMock(id=102, channel=text_channel),
            ]
        )
        text_channel.get_partial_message.return_value.delete = AsyncMock()
        self.bot.get_channel.return_value = text_channel
        state = MagicMock()
        controller = TrackControllerManager(self.bot, self.connection)
        self.addAsyncCleanup(controller.cleanup)
        ui = UIOrchestrator(self.bot, controller, state)
        self.healer = MagicMock(capture_and_heal=AsyncMock(return_value=True))
        handlers = VoiceLifecycleHandlers(
            self.bot, self.connection, state, ui, self.healer
        )
        self.addAsyncCleanup(handlers.cleanup)
        self.enterContext(patch.object(TrackControllerView, "start_updater"))
        await ui.spawn_controller(self.player, attempt)
        return handlers, controller, controller.controllers[1], text_channel

    async def test_heal_keeps_existing_controller_if_player_already_recovered(
        self,
    ) -> None:
        handlers, controller, original, channel = await self._make_controller()
        self.assertFalse(await handlers.heal(1, expected_player=self.player))
        self.assertIs(controller.controllers[1], original)
        self.assertFalse(original.is_finished())
        channel.get_partial_message.return_value.delete.assert_not_awaited()
        channel.send.assert_awaited_once()

    async def test_heal_restores_controller_after_recovery_during_delete(self) -> None:
        await self._check_controller_deletion("recovered")

    async def test_failed_transition_restores_controller_if_cleanup_is_abandoned(
        self,
    ) -> None:
        await self._check_controller_deletion("recovered", invalidation=True)

    async def test_heal_restores_controller_for_current_attempt_after_delete(
        self,
    ) -> None:
        await self._check_controller_deletion("next_attempt")

    async def test_heal_does_not_restore_replaced_session_controller(self) -> None:
        await self._check_controller_deletion("replaced")

    async def test_leave_during_controller_deletion_does_not_restore_session(
        self,
    ) -> None:
        await self._check_controller_deletion("leaving")

    async def _check_controller_deletion(
        self,
        change: Literal["recovered", "next_attempt", "replaced", "leaving"],
        *,
        invalidation: bool = False,
    ) -> None:
        handlers, controller, original, channel = await self._make_controller()
        self.player._connected = False
        entered, release = asyncio.Event(), asyncio.Event()

        async def delete() -> None:
            entered.set()
            await release.wait()

        channel.get_partial_message.return_value.delete.side_effect = delete
        operation = (
            handlers._recover_failed_voice_transition(self.player)
            if invalidation
            else handlers.heal(1, expected_player=self.player)
        )
        task = asyncio.create_task(operation)
        await entered.wait()
        self.assertNotIn(1, controller.controllers)
        self.player._connected = True
        if change == "next_attempt":
            self.player._current_attempt = PlaybackAttempt(2, original.attempt.entry)
        if change == "replaced":
            self.guild.voice_client = MusicPlayer(self.bot, self.channel)
        if change == "leaving":
            async with handlers.leaving(1):
                release.set()
            self.assertTrue(task.cancelled())
        else:
            release.set()
            self.assertFalse(await task)
        self.healer.capture_and_heal.assert_not_called()
        self.assertTrue(original.is_finished())
        if change in ("recovered", "next_attempt"):
            current = controller.controllers[1]
            self.assertIs(current.player, self.player)
            self.assertIs(current.attempt, self.player.current_attempt)
            self.assertFalse(current.is_finished())
            self.assertFalse(self.player.is_stale)
            self.assertEqual(current.message.id if current.message else None, 102)
            self.assertEqual(channel.send.await_count, 2)
        else:
            self.assertNotIn(1, controller.controllers)
            channel.send.assert_awaited_once()
