"""Voice profile command privacy, owner control and attachment behavior."""

import asyncio
import unittest
from collections.abc import Awaitable, Callable
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import config
from api.voice.model import VoiceCheckpoint, VoiceSnapshot
from api.voice.timeline import VoiceTimeline
from cogs.voice.collector_cog import VoiceCollectorCog
from cogs.voice.profile.view import VoiceProfileView
from cogs.voice.profile_cog import VoiceProfileCog
from repositories.voice_journal import VoiceJournal
from tests.api.voice.examples import human, record


def interaction(user_id: int = 10, *, guild: bool = True) -> MagicMock:
    item = MagicMock(spec=discord.Interaction)
    item.user = MagicMock(spec=discord.Member)
    item.user.id = user_id
    item.user.name = "listener"
    item.user.display_name = "Listener"
    item.user.display_avatar.with_static_format.return_value.read = AsyncMock(
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
                self.assertEqual(empty.rooms, ())
                await cog._timeline(1)
                self.assertEqual(reading.await_count, 2)
                journal.start()
                journal.submit(record(0, VoiceSnapshot((human(),))))
                journal.submit(record(3600, VoiceCheckpoint()))
                await journal.close()
                updated = await cog._timeline(1)
                self.assertEqual(len(updated.rooms), 1)
                await cog._timeline(1)
                self.assertEqual(reading.await_count, 4)

    async def test_guild_only_failure_is_ephemeral_without_defer(self) -> None:
        cog = VoiceProfileCog(MagicMock())
        item = interaction(guild=False)
        await invoke(cog, item)
        item.response.send_message.assert_awaited_once()
        self.assertTrue(item.response.send_message.call_args.kwargs["ephemeral"])
        item.response.defer.assert_not_awaited()

    async def test_public_and_private_defer_before_same_message_attachment(
        self,
    ) -> None:
        cog = VoiceProfileCog(MagicMock())
        for private in (False, True):
            with self.subTest(private=private):
                item = interaction()
                attachment = discord.File(BytesIO(b"png"), filename="profile.png")
                with patch.object(
                    cog, "_attachment", AsyncMock(return_value=attachment)
                ) as build:
                    await invoke(cog, item, private)
                item.response.defer.assert_awaited_once_with(
                    thinking=True, ephemeral=private
                )
                build.assert_awaited_once_with(item.guild, item.user)
                item.edit_original_response.assert_awaited_once()
                self.assertEqual(
                    item.edit_original_response.call_args.kwargs["attachments"],
                    [attachment],
                )
                view = item.edit_original_response.call_args.kwargs["view"]
                self.assertIsInstance(view, VoiceProfileView)
                self.assertEqual(len(view.children), 1)

    async def test_empty_history_avatar_failure_still_produces_file(self) -> None:
        cog = VoiceProfileCog(MagicMock())
        item = interaction()
        empty = VoiceTimeline((), (), ())
        with patch.object(cog, "_timeline", AsyncMock(return_value=empty)):
            with self.assertLogs("cogs.voice.profile_cog", level="WARNING"):
                attachment = await cog._attachment(item.guild, item.user)
        self.assertEqual(attachment.filename, "voice-profile.png")
        self.assertIsNotNone(attachment.description)
        attachment.close()

    async def test_empty_history_command_succeeds_with_invalid_timezone(self) -> None:
        cog = VoiceProfileCog(MagicMock())
        item = interaction()
        with patch.object(
            cog, "_timeline", AsyncMock(return_value=VoiceTimeline((), (), ()))
        ):
            with patch.object(config, "VOICE_PROFILE_TIMEZONE", "Invalid/Somewhere"):
                with self.assertLogs("cogs.voice.profile_cog", level="WARNING"):
                    await invoke(cog, item)
        item.edit_original_response.assert_awaited_once()
        attachment = item.edit_original_response.call_args.kwargs["attachments"][0]
        self.assertEqual(attachment.filename, "voice-profile.png")
        attachment.close()

    async def test_corrupt_history_gets_ephemeral_error(self) -> None:
        cog = VoiceProfileCog(MagicMock())
        item = interaction()
        with patch.object(
            cog, "_attachment", AsyncMock(side_effect=ValueError("corrupt"))
        ):
            with self.assertLogs("cogs.voice.profile_cog", level="ERROR"):
                await invoke(cog, item)
        item.delete_original_response.assert_awaited_once()
        self.assertTrue(item.followup.send.call_args.kwargs["ephemeral"])

    async def test_refresh_from_command_replaces_attachment_on_same_message(
        self,
    ) -> None:
        cog = VoiceProfileCog(MagicMock())
        first = interaction()
        button = interaction()
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
