"""Voice profile command privacy, owner control and attachment behavior."""

import asyncio
import unittest
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from api.voice.model import VoiceCheckpoint, VoiceSnapshot
from api.voice.timeline import VoiceTimeline
from cogs.voice import profile_cog as cog_module
from cogs.voice.collector_cog import VoiceCollectorCog
from cogs.voice.profile.media import ProfileMedia, RenderBusyError
from cogs.voice.profile.view import VoiceProfileView
from cogs.voice.profile_cog import ProfileSnapshot, VoiceProfileCog
from framework.feedback_ui import FeedbackUI
from repositories.voice_journal import VoiceJournal
from tests.api.voice.examples import human, record
from tests.cogs.voice.profile.test_media import profile_at


def interaction(user_id: int = 10, *, guild: bool = True) -> MagicMock:
    item = MagicMock(spec=discord.Interaction)
    item.user = MagicMock(spec=discord.Member)
    item.user.id = user_id
    item.user.name = "listener"
    item.user.display_name = "Listener"
    item.filesize_limit = 5 * 1024 * 1024
    item.user.display_avatar.is_animated.return_value = False
    item.user.display_avatar.with_format.return_value.read = AsyncMock(
        side_effect=OSError("CDN unavailable")
    )
    item.guild = MagicMock(spec=discord.Guild) if guild else None
    if item.guild is not None:
        item.guild.id = 42
        item.guild.name = "Guild"
        item.guild.get_member.return_value = None
    item.response.defer = AsyncMock()
    item.response.send_message = AsyncMock()
    item.edit_original_response = AsyncMock()
    item.delete_original_response = AsyncMock()
    item.followup.send = AsyncMock()
    return item


async def invoke(cog: VoiceProfileCog, item: MagicMock, private: bool = False) -> None:
    # discord.py types this callback as either bound or unbound; a Cog stores
    # the unbound variant until it is registered with the bot.
    callback = cast(
        Callable[[VoiceProfileCog, discord.Interaction, bool], Awaitable[None]],
        cog.voice_profile.callback,
    )
    await callback(cog, item, private)


class TestVoiceProfileCog(unittest.IsolatedAsyncioTestCase):
    async def test_real_identity_reaches_media_and_cached_webp_attachment(self) -> None:
        bot = MagicMock()
        cog = VoiceProfileCog(bot)
        cog._revision = "test"
        item = interaction()
        profile = profile_at(20)
        item.user.display_avatar.is_animated.return_value = True
        avatar = item.user.display_avatar.with_format.return_value
        avatar.read = AsyncMock(return_value=b"avatar-bytes")
        icon = item.guild.icon.with_format.return_value
        icon.read = AsyncMock(return_value=b"guild-icon-bytes")
        media = ProfileMedia(b"webp", "webp")
        with (
            patch.object(
                cog,
                "_timeline",
                AsyncMock(
                    return_value=ProfileSnapshot(
                        VoiceTimeline((), (), ()),
                        0,
                        0,
                        datetime(2026, 9, 29, tzinfo=UTC),
                    )
                ),
            ),
            patch.object(cog_module, "build_profile", return_value=profile),
            patch.object(
                cog._media_renderer, "render", AsyncMock(return_value=media)
            ) as render,
        ):
            first = await cog._attachment(item.guild, item.user, item.filesize_limit)
            second = await cog._attachment(item.guild, item.user, item.filesize_limit)
        self.assertEqual(first.filename, "voice-profile.webp")
        self.assertEqual(second.filename, "voice-profile.webp")
        render.assert_awaited_once()
        identity = render.call_args.args[1]
        self.assertEqual(identity.avatar_bytes, b"avatar-bytes")
        self.assertEqual(identity.guild_icon_png, b"guild-icon-bytes")
        item.user.display_avatar.with_format.assert_called_with("gif")
        avatar.read.assert_awaited_once()
        icon.read.assert_awaited_once()
        first.close()
        second.close()

    async def test_timeline_cache_reuses_and_invalidates_on_persisted_count(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            bot = MagicMock()
            journal = VoiceJournal(Path(directory))
            bot.get_cog.return_value = VoiceCollectorCog(bot, journal=journal)
            cog = VoiceProfileCog(bot)
            with patch.object(journal, "read_all", wraps=journal.read_all) as reading:
                empty = await cog._timeline(1)
                self.assertEqual(empty.timeline.rooms, ())
                await cog._timeline(1)
                self.assertEqual(reading.await_count, 2)
                journal.start()
                journal.submit(record(0, VoiceSnapshot((human(),))))
                journal.submit(record(3600, VoiceCheckpoint()))
                await journal.close()
                updated = await cog._timeline(1)
                self.assertEqual(len(updated.timeline.rooms), 1)
                await cog._timeline(1)
                self.assertEqual(reading.await_count, 4)

    async def test_guild_only_failure_is_ephemeral_without_defer(self) -> None:
        bot = MagicMock()
        cog = VoiceProfileCog(bot)
        cog._revision = "test"
        item = interaction(guild=False)
        await invoke(cog, item)
        item.response.send_message.assert_awaited_once()
        self.assertTrue(item.response.send_message.call_args.kwargs["ephemeral"])
        item.response.defer.assert_not_awaited()

    async def test_public_and_private_defer_before_same_message_attachment(
        self,
    ) -> None:
        bot = MagicMock()
        cog = VoiceProfileCog(bot)
        cog._revision = "test"
        for private in (False, True):
            with self.subTest(private=private):
                item = interaction()
                bot.get_guild.return_value = item.guild
                item.guild.get_member.return_value = item.user
                attachment = discord.File(BytesIO(b"png"), filename="profile.png")
                with patch.object(
                    cog, "_attachment", AsyncMock(return_value=attachment)
                ) as build:
                    await invoke(cog, item, private)
                item.response.defer.assert_awaited_once_with(
                    thinking=True, ephemeral=private
                )
                build.assert_awaited_once_with(
                    item.guild, item.user, item.filesize_limit
                )
                item.edit_original_response.assert_awaited_once()
                self.assertEqual(
                    item.edit_original_response.call_args.kwargs["attachments"],
                    [attachment],
                )
                view = item.edit_original_response.call_args.kwargs["view"]
                self.assertIsInstance(view, VoiceProfileView)
                self.assertEqual(len(view.children), 1)

    async def test_refresh_from_command_replaces_attachment_on_same_message(
        self,
    ) -> None:
        bot = MagicMock()
        cog = VoiceProfileCog(bot)
        cog._revision = "test"
        first = interaction()
        button = interaction()
        bot.get_guild.return_value = first.guild
        first.guild.get_member.side_effect = [first.user, button.user]
        initial = discord.File(BytesIO(b"first"), filename="first.png")
        updated = discord.File(BytesIO(b"second"), filename="second.png")
        with patch.object(
            cog, "_attachment", AsyncMock(side_effect=[initial, updated])
        ):
            await invoke(cog, first)
            view = first.edit_original_response.call_args.kwargs["view"]
            await view.refresh_button.callback(button)
        button.response.defer.assert_awaited_once()
        button.edit_original_response.assert_awaited_once_with(
            attachments=[updated], view=view
        )
        button.followup.send.assert_not_awaited()
        initial.close()
        updated.close()

    async def test_native_start_failure_leaves_cog_loaded_with_safe_feedback(
        self,
    ) -> None:
        bot = MagicMock()
        cog = VoiceProfileCog(bot)
        with patch.object(
            cog._media_renderer, "start", side_effect=RuntimeError("private host path")
        ):
            with self.assertLogs(cog_module.logger, level="WARNING"):
                await cog.cog_load()
        self.assertIsNone(cog._revision)
        item = interaction()
        with patch.object(FeedbackUI, "send", AsyncMock()) as send:
            await invoke(cog, item)
        self.assertIn("недоступна", send.call_args.kwargs["description"])
        self.assertNotIn("private host", send.call_args.kwargs["description"])
        await cog.cog_unload()
        await cog.cog_unload()

    async def test_refresh_resolves_member_again_and_honors_new_upload_limit(
        self,
    ) -> None:
        bot = MagicMock()
        cog = VoiceProfileCog(bot)
        cog._revision = "test"
        first, second = interaction(), interaction()
        second.filesize_limit = 3
        bot.get_guild.return_value = first.guild
        first.guild.get_member.side_effect = [first.user, second.user]
        files = [discord.File(BytesIO(b"png"), filename="card.png") for _ in range(2)]
        with patch.object(cog, "_attachment", AsyncMock(side_effect=files)) as build:
            await invoke(cog, first)
            view = first.edit_original_response.call_args.kwargs["view"]
            await view.refresh_button.callback(second)
        self.assertIs(build.await_args_list[1].args[1], second.user)
        self.assertEqual(build.await_args_list[1].args[2], 3)
        second.edit_original_response.assert_awaited_once()

    async def test_full_queue_uses_safe_busy_feedback(self) -> None:
        bot = MagicMock()
        cog = VoiceProfileCog(bot)
        cog._revision = "test"
        item = interaction()
        bot.get_guild.return_value = item.guild
        item.guild.get_member.return_value = item.user
        with (
            patch.object(cog, "_attachment", AsyncMock(side_effect=RenderBusyError())),
            patch.object(FeedbackUI, "send", AsyncMock()) as send,
        ):
            await invoke(cog, item)
        self.assertIn("заняты", send.call_args.kwargs["description"])

    async def test_real_profile_build_and_upload_fallback(self) -> None:
        cog = VoiceProfileCog(MagicMock())
        cog._revision = "test"
        item = interaction()
        item.guild.icon = None
        avatar = item.user.display_avatar.with_format.return_value
        avatar.read = AsyncMock(return_value=b"avatar")
        snapshot = ProfileSnapshot(
            VoiceTimeline((), (), ()), 0, 0, datetime(2026, 9, 29, tzinfo=UTC)
        )
        with (
            patch.object(cog, "_timeline", AsyncMock(return_value=snapshot)),
            patch.object(
                cog._media_renderer,
                "render",
                AsyncMock(return_value=ProfileMedia(b"animated", "webp", b"png")),
            ) as render,
        ):
            first = await cog._attachment(item.guild, item.user, 3)
            second = await cog._attachment(item.guild, item.user, 10)
        self.assertEqual(first.filename, "voice-profile.png")
        self.assertEqual(second.filename, "voice-profile.webp")
        self.assertIsNot(first, second)
        profile = render.call_args.args[0]
        self.assertEqual(
            (profile.guild_id, profile.user_id, profile.level), (42, 10, 1)
        )
        item.user.display_avatar.with_format.assert_called_with("png")
        first.close()
        second.close()
        await cog.cog_unload()

    async def test_collector_replacement_changes_snapshot_epoch(self) -> None:
        with TemporaryDirectory() as directory:
            bot = MagicMock()
            old = VoiceJournal(Path(directory) / "old")
            new = VoiceJournal(Path(directory) / "new")
            cog = VoiceProfileCog(bot)
            bot.get_cog.return_value = VoiceCollectorCog(bot, journal=old)
            first = await cog._timeline(42)
            bot.get_cog.return_value = VoiceCollectorCog(bot, journal=new)
            second = await cog._timeline(42)
            self.assertNotEqual(first.epoch, second.epoch)
            self.assertEqual(first.generation, second.generation)

    def test_command_exposes_only_private(self) -> None:
        self.assertEqual(
            [p.name for p in VoiceProfileCog.voice_profile.parameters], ["private"]
        )


class TestVoiceProfileView(unittest.IsolatedAsyncioTestCase):
    async def test_other_user_cannot_refresh(self) -> None:
        refresh = AsyncMock()
        view = VoiceProfileView(10, refresh)
        item = interaction(user_id=11)
        await view.refresh_button.callback(item)
        refresh.assert_not_awaited()
        item.response.send_message.assert_awaited_once_with(
            "Это не ваша карточка.", ephemeral=True
        )

    async def test_refresh_edits_same_message_and_concurrent_click_is_ignored(
        self,
    ) -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def refresh(item: discord.Interaction) -> None:
            started.set()
            await release.wait()
            await item.edit_original_response(attachments=[updated])

        view = VoiceProfileView(10, refresh)
        updated = discord.File(BytesIO(b"image"), filename="updated.png")
        first = interaction()
        second = interaction()
        running = asyncio.create_task(view.refresh_button.callback(first))
        await started.wait()
        await view.refresh_button.callback(second)
        second.response.send_message.assert_awaited_once()
        self.assertIn("уже обновляется", second.response.send_message.call_args.args[0])
        release.set()
        await running
        first.response.defer.assert_awaited_once()
        first.edit_original_response.assert_awaited_once_with(attachments=[updated])
        updated.close()
