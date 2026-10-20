"""Music controls share listener and narrowly scoped remote permissions."""

import unittest
from datetime import UTC, datetime
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord

from stupidbot.app.bot import StupidBot
from stupidbot.discord_ui.feedback import FeedbackUI
from stupidbot.features.music.discord import music_cog
from stupidbot.features.music.discord.access import MusicAction, can_control_music
from stupidbot.features.music.discord.music_cog import MusicCog
from stupidbot.features.music.discord.views.controller import TrackControllerView
from stupidbot.features.music.playback.models import PlaybackAttempt
from stupidbot.features.music.playback.player import MusicPlayer
from stupidbot.features.music.repository import VolumeStore
from stupidbot.features.music.results import (
    MusicResult,
    MusicResultReason,
    MusicResultStatus,
)
from stupidbot.features.preferences.models import Preferences
from stupidbot.i18n.discord import interaction_context
from stupidbot.i18n.policy import Language, PresentationContext
from tests.features.music.fixtures import make_entry


def _member(
    user_id: int, channel_id: int | None, *, manager: bool = False
) -> MagicMock:
    member = MagicMock(spec=discord.Member, id=user_id)
    member.voice = (
        MagicMock(channel=MagicMock(id=channel_id)) if channel_id is not None else None
    )
    member.guild_permissions = discord.Permissions(manage_guild=manager)
    return member


class TestMusicPermissions(unittest.TestCase):
    def test_listener_can_use_every_control(self) -> None:
        member = _member(10, 20)
        actions: tuple[MusicAction, ...] = ("control", "skip", "manage_session", "join")
        for action in actions:
            with self.subTest(action=action):
                self.assertTrue(can_control_music(member, 20, action=action))

    def test_requester_outside_channel_can_only_skip_own_attempt(self) -> None:
        member = _member(10, None)
        own = PlaybackAttempt(1, make_entry("own", requester_id=10))
        other = PlaybackAttempt(2, make_entry("other", requester_id=11))
        self.assertTrue(can_control_music(member, 20, action="skip", attempt=own))
        self.assertFalse(can_control_music(member, 20, action="skip", attempt=other))
        self.assertFalse(can_control_music(member, 20, action="control", attempt=own))
        self.assertFalse(can_control_music(member, 20, action="manage_session"))
        self.assertFalse(can_control_music(member, 20, action="join"))

    def test_manager_remote_exception_is_limited_to_session_management(self) -> None:
        member = _member(10, None, manager=True)
        self.assertTrue(can_control_music(member, 20, action="manage_session"))
        self.assertFalse(can_control_music(member, 20, action="control"))
        self.assertFalse(can_control_music(member, 20, action="skip"))
        self.assertFalse(can_control_music(member, 20, action="join"))

    def test_join_requires_voice_and_cannot_take_another_channel(self) -> None:
        self.assertTrue(can_control_music(_member(10, 20), None, action="join"))
        self.assertFalse(can_control_music(_member(10, None), None, action="join"))
        self.assertFalse(can_control_music(_member(10, 30), 20, action="join"))


class TestSkipAuthorization(unittest.IsolatedAsyncioTestCase):
    async def test_command_passes_authorized_attempt_after_track_changes(self) -> None:
        with patch.object(music_cog, "create_music_components"):
            cog = MusicCog(MagicMock(), MagicMock(spec=VolumeStore))
        own = PlaybackAttempt(1, make_entry("own", requester_id=10))
        next_attempt = PlaybackAttempt(2, make_entry("next", requester_id=11))
        player = MagicMock(
            current_attempt=own, channel=MagicMock(spec=discord.VoiceChannel, id=20)
        )
        cog.service.get_player = MagicMock(return_value=player)
        cog.service.skip = AsyncMock(
            return_value=MusicResult(
                MusicResultStatus.SUCCESS,
                "Skipped",
                data={"before": None, "after": None},
                reason=MusicResultReason.SKIPPED,
            )
        )
        interaction = MagicMock(
            user=_member(10, None), guild=MagicMock(spec=discord.Guild, id=1)
        )

        async def change_track(
            target: discord.Interaction, *, private: bool
        ) -> PresentationContext:
            player.current_attempt = next_attempt
            return await interaction_context(target, private=private)

        with (
            patch.object(music_cog, "interaction_context", side_effect=change_track),
            patch.object(music_cog, "send_warning", new=AsyncMock()),
        ):
            await cog.skip._do_call(interaction, {})
        cog.service.skip.assert_awaited_once_with(
            1, 10, interaction.channel_id, expected=own
        )


class TestRegisteredMusicPermissions(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.bot = StupidBot()
        self.addAsyncCleanup(self.bot.close)
        with patch.object(music_cog, "create_music_components"):
            self.cog = MusicCog(self.bot, self.bot.volume_repository)
        self.service = self.cog.service
        self.service.voice_lifecycle.is_healing = MagicMock(return_value=False)
        self.attempt = PlaybackAttempt(1, make_entry("own", requester_id=10))
        self.channel = MagicMock(spec=discord.VoiceChannel, id=20)
        self.player = MagicMock(current_attempt=self.attempt, channel=self.channel)
        self.service.get_player = MagicMock(return_value=self.player)
        self.pause = AsyncMock(
            return_value=MusicResult(
                MusicResultStatus.SUCCESS, "Paused", reason=MusicResultReason.PAUSED
            )
        )
        self.leave = AsyncMock(
            return_value=MusicResult(
                MusicResultStatus.SUCCESS,
                "Disconnected",
                reason=MusicResultReason.DISCONNECTED,
            )
        )
        self.heal = AsyncMock(return_value=True)
        self.play = AsyncMock()
        self.skip = AsyncMock(
            return_value=MusicResult(
                MusicResultStatus.SUCCESS,
                "Skipped",
                data={"before": None, "after": None},
                reason=MusicResultReason.SKIPPED,
            )
        )
        self.service.pause = self.pause
        self.service.leave = self.leave
        self.service.heal = self.heal
        self.service.play = self.play
        self.service.skip = self.skip
        for command in (
            self.cog.pause,
            self.cog.leave,
            self.cog.heal,
            self.cog.play,
            self.cog.skip,
        ):
            self.bot.tree.add_command(command)
        for target, name, replacement in (
            (self.bot, "dispatch", MagicMock()),
            (self.bot.block_manager, "is_user_blocked", AsyncMock(return_value=False)),
            (
                self.bot.preferences_repository,
                "get",
                AsyncMock(return_value=Preferences()),
            ),
        ):
            patcher = patch.object(target, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.item = MagicMock(spec=discord.Interaction)
        self.item._state = self.bot._connection
        self.item.client = self.bot
        self.item.guild = MagicMock(
            spec=discord.Guild,
            id=42,
            name="Guild",
            voice_client=MagicMock(channel=self.channel),
        )
        self.item.guild_id = 42
        self.item.channel_id = 44
        self.item.user = _member(10, None)
        self.item.created_at = datetime.now(UTC)
        self.item.type = discord.InteractionType.application_command
        self.item.command_failed = False
        self.item.extras = {}
        self.item.response = MagicMock(spec=discord.InteractionResponse)
        self.item.response.is_done.return_value = False
        self.item.response.type = None
        self.item.followup.send = AsyncMock()
        self.item.edit_original_response = AsyncMock()

        async def defer(*, thinking: bool, ephemeral: bool) -> None:
            del ephemeral
            self.item.response.is_done.return_value = True
            self.item.response.type = (
                discord.InteractionResponseType.deferred_channel_message
                if thinking
                else discord.InteractionResponseType.deferred_message_update
            )

        self.item.response.defer.side_effect = defer
        feedback = patch.object(FeedbackUI, "send", new=AsyncMock())
        self.feedback = feedback.start()
        self.addCleanup(feedback.stop)

    async def test_registered_pause_denies_remote_requester_before_service(
        self,
    ) -> None:
        self.item.command = self.cog.pause
        self.item.data = {"name": "pause", "type": 1, "options": []}

        await self.bot.tree._call(self.item)

        self.pause.assert_not_awaited()
        self.feedback.assert_awaited_once()
        self.assertTrue(self.feedback.call_args.kwargs["ephemeral"])

    async def test_registered_pause_allows_another_listener(self) -> None:
        self.item.user = _member(11, 20)
        self.item.command = self.cog.pause
        self.item.data = {"name": "pause", "type": 1, "options": []}

        await self.bot.tree._call(self.item)

        self.pause.assert_awaited_once_with(42)
        self.assertFalse(self.item.command_failed)

    async def test_registered_healing_denial_has_one_private_response(self) -> None:
        self.service.voice_lifecycle.is_healing = MagicMock(return_value=True)
        self.item.user = _member(11, 20)
        self.item.command = self.cog.pause
        self.item.data = {"name": "pause", "type": 1, "options": []}

        await self.bot.tree._call(self.item)

        self.pause.assert_not_awaited()
        self.feedback.assert_awaited_once()
        self.assertTrue(self.feedback.call_args.kwargs["ephemeral"])

    async def test_registered_remote_manager_cannot_pause(self) -> None:
        self.item.user = _member(11, None, manager=True)
        self.item.command = self.cog.pause
        self.item.data = {"name": "pause", "type": 1, "options": []}

        await self.bot.tree._call(self.item)

        self.pause.assert_not_awaited()
        self.feedback.assert_awaited_once()

    async def test_registered_remote_manager_can_leave_and_reconnect(self) -> None:
        self.item.user = _member(11, None, manager=True)
        self.item.command = self.cog.leave
        self.item.data = {"name": "leave", "type": 1, "options": []}
        await self.bot.tree._call(self.item)
        self.leave.assert_awaited_once_with(self.item.guild)

        self.item.command = self.cog.heal
        self.item.data = {"name": "reconnect", "type": 1, "options": []}
        await self.bot.tree._call(self.item)
        self.heal.assert_awaited_once_with(42)

    async def test_registered_remote_skip_is_limited_to_current_requester(self) -> None:
        self.item.command = self.cog.skip
        self.item.data = {"name": "skip", "type": 1, "options": []}
        await self.bot.tree._call(self.item)
        self.skip.assert_awaited_once_with(42, 10, 44, expected=self.attempt)

        self.skip.reset_mock()
        self.feedback.reset_mock()
        self.player.current_attempt = PlaybackAttempt(
            2, make_entry("other", requester_id=11)
        )
        await self.bot.tree._call(self.item)
        self.skip.assert_not_awaited()
        self.feedback.assert_awaited_once()
        self.assertTrue(self.feedback.call_args.kwargs["ephemeral"])

    async def test_registered_play_does_not_take_another_voice_channel(self) -> None:
        self.item.user = _member(10, 30)
        self.item.command = self.cog.play
        self.item.data = {
            "name": "play",
            "type": 1,
            "options": [{"name": "query", "type": 3, "value": "track"}],
        }

        await self.bot.tree._call(self.item)

        self.play.assert_not_awaited()
        self.feedback.assert_awaited_once()

    async def test_controller_allows_same_voice_listener_who_did_not_request_track(
        self,
    ) -> None:
        player = MagicMock(
            spec=MusicPlayer, current_attempt=self.attempt, channel=self.channel
        )
        player.seek_attempt = AsyncMock(return_value=True)
        view = TrackControllerView(
            user_id=10,
            context=PresentationContext(Language.RU, ZoneInfo("UTC")),
            player=player,
            guild_id=42,
            attempt=self.attempt,
            on_stop_callback=None,
            on_player_failure=AsyncMock(),
        )
        self.addCleanup(view.stop)
        self.item.type = discord.InteractionType.component
        self.item.user = _member(11, 20)
        self.item.data = {"component_type": 2, "custom_id": "btn_restart"}
        button = next(
            child
            for child in view.children
            if isinstance(child, discord.ui.Button) and child.custom_id == "btn_restart"
        )
        with patch.object(view, "_safe_update", new=AsyncMock()):
            await view._scheduled_task(button, self.item)
        player.seek_attempt.assert_awaited_once_with(self.attempt, 0)
        self.feedback.assert_not_awaited()
