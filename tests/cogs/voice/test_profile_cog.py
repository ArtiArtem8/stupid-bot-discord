"""Voice profile command privacy, owner control and attachment behavior."""

import asyncio
import sys
import unittest
from collections.abc import Awaitable, Callable
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands
from PIL import Image

import cogs.voice.collector_cog
import config
from api.voice.model import VoiceCheckpoint, VoiceSnapshot
from api.voice.timeline import VoiceTimeline
from cogs.voice import profile_cog as cog_module
from cogs.voice.profile.avatar import load_avatar
from cogs.voice.profile.media import ProfileMedia, RenderBusyError
from cogs.voice.profile.raster import Box
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
    avatar = item.user.display_avatar.with_format.return_value
    avatar.with_size.return_value = avatar
    item.guild = MagicMock(spec=discord.Guild) if guild else None
    if item.guild is not None:
        item.guild.id = 42
        item.guild.name = "Guild"
        item.guild.get_member.return_value = None
        icon = item.guild.icon.with_format.return_value
        icon.with_size.return_value = icon
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
    async def test_gif_assets_are_reused_across_new_png_and_webp_cards(self) -> None:
        bot = MagicMock()
        cog = VoiceProfileCog(bot)
        cog._revision = "test"
        item = interaction()
        item.user.display_avatar.is_animated.return_value = True
        avatar = item.user.display_avatar.with_format.return_value
        output = BytesIO()
        with Image.new("RGB", (16, 16), "red") as first_frame:
            with Image.new("RGB", (16, 16), "blue") as second_frame:
                first_frame.save(
                    output,
                    "GIF",
                    save_all=True,
                    append_images=[second_frame],
                    duration=[80, 120],
                    loop=0,
                )
        gif = output.getvalue()
        avatar.read = AsyncMock(return_value=gif)
        icon = item.guild.icon.with_format.return_value
        icon.read = AsyncMock(return_value=b"guild-icon-bytes")
        media = ProfileMedia(b"webp", "webp")
        with (
            patch.object(
                cog,
                "_timeline",
                AsyncMock(
                    side_effect=[
                        ProfileSnapshot(VoiceTimeline((), (), ()), 0, generation)
                        for generation in (0, 0, 1, 2)
                    ]
                ),
            ),
            patch.object(
                cog_module,
                "build_profile",
                side_effect=[profile_at(20), profile_at(5), profile_at(20)],
            ),
            patch.object(
                cog._media_renderer,
                "render",
                AsyncMock(side_effect=[media, ProfileMedia(b"png", "png"), media]),
            ) as render,
        ):
            first = await cog._attachment(item.guild, item.user, item.filesize_limit)
            second = await cog._attachment(item.guild, item.user, item.filesize_limit)
            third = await cog._attachment(item.guild, item.user, item.filesize_limit)
            fourth = await cog._attachment(item.guild, item.user, item.filesize_limit)
        self.assertEqual(first.filename, "voice-profile.webp")
        self.assertEqual(second.filename, "voice-profile.webp")
        self.assertEqual(third.filename, "voice-profile.png")
        self.assertEqual(fourth.filename, "voice-profile.webp")
        self.assertEqual(render.await_count, 3)
        for call in render.call_args_list:
            identity = call.args[1]
            self.assertEqual(identity.avatar_bytes, gif)
            self.assertEqual(identity.guild_icon_png, b"guild-icon-bytes")
            decoded = load_avatar(identity.avatar_bytes, Box(0, 0, 92, 92))
            if decoded is None:
                self.fail("Cached GIF lost its animation")
            self.assertEqual(len(decoded.frames), 2)
            self.assertEqual(decoded.ends_ms, (80, 200))
            self.assertNotEqual(
                decoded.frames[0].tobytes(), decoded.frames[1].tobytes()
            )
        item.user.display_avatar.with_format.assert_called_with("gif")
        avatar.with_size.assert_called_with(256)
        icon.with_size.assert_called_with(64)
        avatar.read.assert_awaited_once()
        icon.read.assert_awaited_once()
        first.close()
        second.close()
        third.close()
        fourth.close()

    async def test_guild_icon_is_shared_between_members_cards(self) -> None:
        cog = VoiceProfileCog(MagicMock())
        cog._revision = "test"
        first, second = interaction(), interaction(user_id=11)
        icon = first.guild.icon.with_format.return_value
        icon.read = AsyncMock(return_value=b"icon")
        for item in (first, second):
            item.user.display_avatar.with_format.return_value.read = AsyncMock(
                return_value=b"avatar"
            )
        with (
            patch.object(
                cog,
                "_timeline",
                AsyncMock(
                    return_value=ProfileSnapshot(VoiceTimeline((), (), ()), 0, 0)
                ),
            ),
            patch.object(cog_module, "build_profile", return_value=profile_at(20)),
            patch.object(
                cog._media_renderer,
                "render",
                AsyncMock(return_value=ProfileMedia(b"webp", "webp")),
            ) as render,
        ):
            for item in (first, second):
                attachment = await cog._attachment(
                    first.guild, item.user, item.filesize_limit
                )
                attachment.close()
        self.assertEqual(render.await_count, 2)
        icon.read.assert_awaited_once()

    async def test_changed_asset_url_downloads_new_bytes(self) -> None:
        cog = VoiceProfileCog(MagicMock())
        asset = MagicMock(spec=discord.Asset)
        asset.configure_mock(
            **{
                "__str__.return_value": "https://cdn.discordapp.com/icons/42/old.png?size=64"
            }
        )
        asset.read = AsyncMock(side_effect=[b"old", b"new"])
        self.assertEqual(await cog._asset_bytes(asset, "guild icon"), b"old")
        self.assertEqual(await cog._asset_bytes(asset, "guild icon"), b"old")
        asset.configure_mock(
            **{
                "__str__.return_value": "https://cdn.discordapp.com/icons/42/new.png?size=64"
            }
        )
        self.assertEqual(await cog._asset_bytes(asset, "guild icon"), b"new")
        self.assertEqual(asset.read.await_count, 2)

    async def test_failed_and_oversized_downloads_are_retried(self) -> None:
        cog = VoiceProfileCog(MagicMock())
        for failed in (TimeoutError(), b"x" * (2 * 1024 * 1024 + 1)):
            with self.subTest(failed=type(failed).__name__):
                asset = MagicMock(spec=discord.Asset)
                asset.read = AsyncMock(side_effect=[failed, b"valid"])
                with self.assertLogs(cog_module.logger, level="WARNING"):
                    self.assertIsNone(await cog._asset_bytes(asset, "guild icon"))
                self.assertEqual(await cog._asset_bytes(asset, "guild icon"), b"valid")
                self.assertEqual(asset.read.await_count, 2)

    async def test_format_and_size_are_part_of_the_asset_cache_key(self) -> None:
        cog = VoiceProfileCog(MagicMock())
        original = discord.Asset(
            state=MagicMock(),
            url="https://cdn.discordapp.com/avatars/10/a_hash.gif?size=1024",
            key="a_hash",
            animated=True,
        )
        gif = original.with_format("gif").with_size(256)
        png = original.with_format("png").with_size(256)
        small_png = original.with_format("png").with_size(64)
        self.assertTrue(str(gif).endswith("a_hash.gif?size=256"))
        with patch.object(
            discord.Asset, "read", AsyncMock(side_effect=[b"gif", b"png", b"small"])
        ) as read:
            for asset, expected in (
                (gif, b"gif"),
                (png, b"png"),
                (small_png, b"small"),
            ):
                self.assertEqual(await cog._asset_bytes(asset, "avatar"), expected)
                self.assertEqual(await cog._asset_bytes(asset, "avatar"), expected)
        self.assertEqual(read.await_count, 3)

    async def test_unload_releases_image_cache(self) -> None:
        cog = VoiceProfileCog(MagicMock())
        asset = MagicMock(spec=discord.Asset)
        asset.read = AsyncMock(return_value=b"icon")
        await cog._asset_bytes(asset, "guild icon")
        await cog.cog_unload()
        self.assertIsNone(cog._asset_cache.get(str(asset)))

    async def test_timeline_cache_reuses_and_invalidates_on_persisted_count(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            bot = MagicMock()
            journal = VoiceJournal(Path(directory))
            bot.get_cog.return_value = cogs.voice.collector_cog.VoiceCollectorCog(
                bot, journal=journal
            )
            cog = VoiceProfileCog(bot)
            with patch.object(
                journal, "snapshot_for_guild", wraps=journal.snapshot_for_guild
            ) as reading:
                empty = await cog._timeline(1)
                self.assertEqual(empty.timeline.rooms, ())
                await cog._timeline(1)
                self.assertEqual(reading.await_count, 1)
                journal.start()
                journal.submit(record(0, VoiceSnapshot((human(),))))
                journal.submit(record(3600, VoiceCheckpoint()))
                await journal.close()
                updated = await cog._timeline(1)
                self.assertEqual(len(updated.timeline.rooms), 1)
                await cog._timeline(1)
                self.assertEqual(reading.await_count, 2)

    async def test_timeline_cache_evicts_least_recent_guild(self) -> None:
        with TemporaryDirectory() as directory:
            bot = MagicMock()
            journal = VoiceJournal(Path(directory))
            bot.get_cog.return_value = cogs.voice.collector_cog.VoiceCollectorCog(
                bot, journal=journal
            )
            cog = VoiceProfileCog(bot)
            with (
                patch.object(cog_module, "_TIMELINE_CACHE_ENTRIES", 2),
                patch.object(
                    journal, "snapshot_for_guild", wraps=journal.snapshot_for_guild
                ) as reading,
            ):
                await cog._timeline(1)
                await cog._timeline(2)
                await cog._timeline(1)
                await cog._timeline(3)
                await cog._timeline(1)
                self.assertEqual(reading.await_count, 3)
                await cog._timeline(2)
                self.assertEqual(reading.await_count, 4)
                self.assertEqual(len(cog._timeline_cache), 2)

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
        snapshot = ProfileSnapshot(VoiceTimeline((), (), ()), 0, 0)
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
            bot.get_cog.return_value = cogs.voice.collector_cog.VoiceCollectorCog(
                bot, journal=old
            )
            first = await cog._timeline(42)
            bot.get_cog.return_value = cogs.voice.collector_cog.VoiceCollectorCog(
                bot, journal=new
            )
            second = await cog._timeline(42)
            self.assertNotEqual(first.epoch, second.epoch)
            self.assertEqual(first.generation, second.generation)

    async def test_real_collector_reload_preserves_profile_access(self) -> None:
        # Extension loading replaces sys.modules entries and the package's
        # module attribute; restore both so other tests keep their class refs.
        with (
            TemporaryDirectory() as directory,
            patch.dict(sys.modules),
            patch.object(cogs.voice, "collector_cog", cogs.voice.collector_cog),
            patch.object(config, "VOICE_PROBE_DIR", Path(directory)),
            patch.object(config, "VOICE_PROBE_ENABLED", False),
        ):
            async with commands.Bot(
                command_prefix="!", intents=discord.Intents.none()
            ) as bot:
                cog = VoiceProfileCog(bot)
                await bot.load_extension("cogs.voice.collector_cog")
                first = await cog._timeline(42)
                await bot.reload_extension("cogs.voice.collector_cog")
                second = await cog._timeline(42)
                self.assertGreater(second.epoch, first.epoch)
                self.assertEqual(second.timeline, first.timeline)

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
