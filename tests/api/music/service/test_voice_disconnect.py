import asyncio
import unittest
from typing import TYPE_CHECKING, override
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import mafic
from discord.ext import commands

from api.music.models import PLAYBACK_USER_DATA_KEY, PlaybackAttempt, TrackRequester
from api.music.player import MusicPlayer
from api.music.service.connection_manager import ConnectionManager
from api.music.service.playback_events import PlaybackEventHandlers
from api.music.service.state_manager import StateManager
from api.music.service.ui_orchestrator import UIOrchestrator
from api.music.service.voice_lifecycle import VoiceLifecycleHandlers
from cogs.music.views.controller import TrackControllerManager
from tests.api.music.helpers import make_track

if TYPE_CHECKING:
    from discord.types.channel import VoiceChannel as VoiceChannelPayload
    from discord.types.member import MemberWithUser as MemberPayload
    from discord.types.user import User as UserPayload
    from discord.types.voice import GuildVoiceState as GuildVoiceStatePayload
    from discord.types.voice import VoiceState as VoiceStatePayload


class TestVoiceDisconnect(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.bot = MagicMock(spec=commands.Bot)
        self.bot.user = MagicMock(id=999)
        self.guild = MagicMock(spec=discord.Guild, id=123)
        self.guild.get_member.return_value = None
        self.bot.get_guild.return_value = self.guild
        self.voice_channel = MagicMock(
            spec=discord.VoiceChannel, guild=self.guild, members=[]
        )
        self.text_channel = MagicMock(spec=discord.TextChannel, id=789)
        self.text_channel.send = AsyncMock(
            side_effect=[
                MagicMock(spec=discord.Message, id=900, channel=self.text_channel),
                MagicMock(spec=discord.Message, id=901, channel=self.text_channel),
            ]
        )
        self.text_channel.get_partial_message.return_value.delete = AsyncMock()
        self.bot.get_channel.return_value = self.text_channel
        self.node = MagicMock(label="ready", available=True)
        self.pool = MagicMock(nodes=[self.node], label_to_node={"ready": self.node})
        with patch.object(mafic, "NodePool", return_value=self.pool):
            self.connection = ConnectionManager(self.bot)
        self.state = StateManager()
        self.controllers = TrackControllerManager(self.bot, self.connection)
        self.ui = UIOrchestrator(self.bot, self.controllers, self.state)
        self.lifecycle = VoiceLifecycleHandlers(
            self.bot, self.connection, self.state, self.ui, MagicMock()
        )
        self.playback = PlaybackEventHandlers(
            self.bot, self.connection, self.state, self.ui, self.lifecycle.is_healing
        )
        self.addAsyncCleanup(self.controllers.cleanup)

    async def _start_track(
        self, identifier: str, *, length: int = 60_000
    ) -> tuple[MusicPlayer, PlaybackAttempt]:
        player = MusicPlayer(self.bot, self.voice_channel)
        player._node = self.node
        player._connected = True
        self.guild.voice_client = player
        track = make_track(identifier, length=length)
        with patch.object(player, "play", new=AsyncMock()):
            outcome = await player.enqueue_tracks(
                [track], TrackRequester(456, 789), placement="end"
            )
        attempt = outcome.started_attempt
        if attempt is None:
            self.fail("Playback did not start")
        track.user_data[PLAYBACK_USER_DATA_KEY] = attempt.event_token
        await self.playback._on_track_start(MagicMock(player=player, track=track))
        return player, attempt

    async def _disconnect_voice_state(self) -> None:
        member = MagicMock(spec=discord.Member, id=999, guild=self.guild, voice=None)
        before = MagicMock(spec=discord.VoiceState, channel=self.voice_channel)
        after = MagicMock(spec=discord.VoiceState, channel=None)
        await self.lifecycle._on_voice_state_update(member, before, after)

    async def _assert_reconnect_survives_disconnect(self, *, detached: bool) -> None:
        old, _ = await self._start_track("old")
        entered, release = asyncio.Event(), asyncio.Event()

        async def delete() -> None:
            entered.set()
            await release.wait()

        if detached:
            self.guild.voice_client = None
            disconnect = self._disconnect_voice_state()
        else:
            disconnect = self.lifecycle._on_websocket_closed(
                MagicMock(player=old, code=4014, by_discord=True, reason="disconnect")
            )

        with patch.object(
            self.text_channel.get_partial_message.return_value,
            "delete",
            new=AsyncMock(side_effect=delete),
        ):
            cleanup = asyncio.create_task(disconnect)
            try:
                await asyncio.wait_for(entered.wait(), 5)
                _, attempt = await self._start_track("replacement", length=1000)
                session = self.state.get_session(123)
                self.assertIsNotNone(session)
                if session is None:
                    self.fail("TrackStart did not create a session")
                await self.lifecycle._update_channel_timer(123, self.voice_channel)
                self.assertTrue(self.state.is_timer_active(123))
                release.set()
                await cleanup
                self.state.record_history(123, attempt, mafic.EndReason.FINISHED)
                with self.subTest(state="session"):
                    self.assertIs(self.state.get_session(123), session)
                with self.subTest(state="timer"):
                    self.assertTrue(self.state.is_timer_active(123))
                with self.subTest(state="history"):
                    self.assertEqual(
                        [track.title for track in session.tracks], ["Track replacement"]
                    )
            finally:
                release.set()
                await asyncio.gather(cleanup, return_exceptions=True)

    async def test_websocket_disconnect_preserves_reconnect_during_message_delete(
        self,
    ) -> None:
        await self._assert_reconnect_survives_disconnect(detached=False)

    async def test_detached_disconnect_preserves_reconnect_during_message_delete(
        self,
    ) -> None:
        await self._assert_reconnect_survives_disconnect(detached=True)

    async def test_old_disconnect_after_replacement_preserves_current_playback(
        self,
    ) -> None:
        old, _ = await self._start_track("old")
        _, attempt = await self._start_track("replacement")
        session = self.state.get_session(123)
        self.assertIsNotNone(session)
        if session is None:
            self.fail("TrackStart did not create a session")
        controller = self.controllers.controllers[123]
        await self.lifecycle._update_channel_timer(123, self.voice_channel)

        await self.lifecycle._cleanup_after_disconnect(123, player=old)

        self.state.record_history(123, attempt, mafic.EndReason.FINISHED)
        with self.subTest(state="session"):
            self.assertIs(self.state.get_session(123), session)
        with self.subTest(state="timer"):
            self.assertTrue(self.state.is_timer_active(123))
        with self.subTest(state="history"):
            self.assertEqual(
                [track.title for track in session.tracks], ["Track replacement"]
            )
        with self.subTest(state="controller"):
            self.assertIs(self.controllers.controllers.get(123), controller)
            self.assertFalse(controller.is_finished())

    async def test_detached_disconnect_finishes_old_session(self) -> None:
        _, attempt = await self._start_track("old")
        session = self.state.get_session(123)
        self.assertIsNotNone(session)
        if session is None:
            self.fail("TrackStart did not create a session")
        session.record_interaction(789, 456)
        self.state.record_history(123, attempt, mafic.EndReason.FINISHED)
        self.state.start_timer(123, "empty")
        self.guild.voice_client = None

        await self._disconnect_voice_state()

        self.assertIsNone(self.state.get_session(123))
        self.assertFalse(self.state.is_timer_active(123))
        self.assertNotIn(123, self.controllers.controllers)
        self.bot.dispatch.assert_called_once_with(
            "music_session_end", 123, session, 789
        )

    async def test_node_disconnect_preserves_replacement_after_discord_await(
        self,
    ) -> None:
        old, _ = await self._start_track("old")
        old_node = self.node
        old_node.available = False
        old_node.players = [old]
        entered, release = asyncio.Event(), asyncio.Event()

        async def remove_node(_node: object, *, transfer_players: bool) -> None:
            self.assertFalse(transfer_players)
            self.pool.nodes = []
            self.pool.label_to_node.clear()

        async def disconnect_voice(*, channel: object) -> None:
            self.assertIsNone(channel)
            entered.set()
            await release.wait()

        self.pool.remove_node = AsyncMock(side_effect=remove_node)
        self.guild.change_voice_state = AsyncMock(side_effect=disconnect_voice)
        cleanup = asyncio.create_task(self.lifecycle.on_node_unavailable(old_node))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            self.assertTrue(old.is_stale)
            self.node = MagicMock(label="replacement", available=True)
            self.pool.nodes = [self.node]
            self.pool.label_to_node[self.node.label] = self.node
            replacement, _ = await self._start_track("replacement")
            controller = self.controllers.controllers[123]
            await self.lifecycle._update_channel_timer(123, self.voice_channel)
            self.assertTrue(self.state.is_timer_active(123))

            release.set()
            await cleanup

            self.assertIs(self.connection.get_player(123), replacement)
            with self.subTest(state="controller"):
                self.assertIs(self.controllers.controllers.get(123), controller)
                self.assertFalse(controller.is_finished())
            with self.subTest(state="timer"):
                self.assertTrue(self.state.is_timer_active(123))
        finally:
            release.set()
            await asyncio.gather(cleanup, return_exceptions=True)

    async def test_queued_discord_disconnect_preserves_reconnected_session(
        self,
    ) -> None:
        client = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        await client._async_setup_hook()
        self.addAsyncCleanup(client.close)
        discord_state = client._connection
        user: UserPayload = {
            "id": "999",
            "username": "bot",
            "discriminator": "0",
            "avatar": None,
            "global_name": None,
            "bot": True,
        }
        discord_state.user = discord.ClientUser(state=discord_state, data=user)
        guild = discord.Guild._create_unavailable(
            state=discord_state, guild_id=123, data=None
        )
        guild.unavailable = False
        discord_state._add_guild(guild)
        channel_data: VoiceChannelPayload = {
            "id": "456",
            "guild_id": "123",
            "name": "voice",
            "type": 2,
            "position": 0,
            "bitrate": 64_000,
            "user_limit": 0,
            "permission_overwrites": [],
            "nsfw": False,
            "parent_id": None,
        }
        channel = discord.VoiceChannel(
            state=discord_state,
            guild=guild,
            data=channel_data,
        )
        guild._add_channel(channel)
        member_data: MemberPayload = {
            "user": user,
            "roles": [],
            "flags": 0,
            "joined_at": None,
            "deaf": False,
            "mute": False,
        }
        member = discord.Member(
            data=member_data,
            guild=guild,
            state=discord_state,
        )
        guild._add_member(member)
        packet: GuildVoiceStatePayload = {
            "channel_id": "456",
            "user_id": "999",
            "session_id": "old",
            "deaf": False,
            "mute": False,
            "self_deaf": False,
            "self_mute": False,
            "self_video": False,
            "suppress": False,
        }
        guild._update_voice_state(packet, 456)
        old = MusicPlayer(client, channel)
        discord_state._add_voice_client(123, old)
        self.state.get_or_create_session(123)
        old.cleanup()
        self.bot.get_guild.return_value = guild
        self.lifecycle.bot = client
        finished = asyncio.Event()

        async def on_voice_state_update(
            event_member: discord.Member,
            before: discord.VoiceState,
            after: discord.VoiceState,
        ) -> None:
            try:
                await self.lifecycle._on_voice_state_update(event_member, before, after)
            finally:
                if after.channel is None:
                    finished.set()

        client.add_listener(on_voice_state_update, "on_voice_state_update")
        disconnect_packet: VoiceStatePayload = {
            **packet,
            "guild_id": "123",
            "channel_id": None,
        }
        discord_state.parsers["VOICE_STATE_UPDATE"](disconnect_packet)
        replacement = MusicPlayer(client, channel)
        replacement._node = self.node
        replacement._connected = True
        discord_state._add_voice_client(123, replacement)
        reconnect_packet: VoiceStatePayload = {
            **packet,
            "guild_id": "123",
            "session_id": "new",
        }
        discord_state.parsers["VOICE_STATE_UPDATE"](reconnect_packet)
        track = make_track("replacement")
        with patch.object(replacement, "play", new=AsyncMock()):
            outcome = await replacement.enqueue_tracks(
                [track], TrackRequester(456, 789), placement="end"
            )
        attempt = outcome.started_attempt
        if attempt is None:
            self.fail("Replacement playback did not start")
        track.user_data[PLAYBACK_USER_DATA_KEY] = attempt.event_token
        await self.playback._on_track_start(MagicMock(player=replacement, track=track))
        session = self.state.get_session(123)
        if session is None:
            self.fail("TrackStart did not create a session")
        await self.lifecycle._update_channel_timer(123, channel)
        live_voice = member.voice
        if live_voice is None:
            self.fail("Reconnect did not update the member voice cache")
        self.assertIs(live_voice.channel, channel)
        self.assertFalse(finished.is_set())

        await asyncio.wait_for(finished.wait(), 5)

        self.state.record_history(123, attempt, mafic.EndReason.FINISHED)
        with self.subTest(state="session"):
            self.assertIs(self.state.get_session(123), session)
        with self.subTest(state="timer"):
            self.assertTrue(self.state.is_timer_active(123))
        with self.subTest(state="history"):
            self.assertEqual(
                [item.title for item in session.tracks], ["Track replacement"]
            )
