"""One-snapshot refresh, focused reveals and configured button emojis."""

import asyncio
import unittest
from io import BytesIO
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from api.voice.profile.build import build_profile
from cogs.voice import profile_cog as cog_module
from cogs.voice.profile.detail_models import ProfileLook
from cogs.voice.profile.media import ProfileMedia, RenderBusyError
from cogs.voice.profile.view import ProfileAction, ProfileDetail, VoiceProfileView
from cogs.voice.profile_cog import VoiceProfileCog
from framework.feedback_ui import FeedbackUI
from tests.api.voice.examples import example
from tests.cogs.voice.profile.test_details_support import profile_request
from tests.cogs.voice.profile.test_media import profile_at
from tests.cogs.voice.test_profile_cog import interaction


class TestDetailDelivery(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.bot = MagicMock()
        self.cog = VoiceProfileCog(self.bot)
        self.cog._revision = "test"
        self.item = interaction()
        self.bot.get_guild.return_value = self.item.guild
        self.item.guild.get_member.return_value = self.item.user
        self.request = profile_request(self.item.guild, self.item.user)
        self.view = VoiceProfileView(
            10, self._refresh, reveal=AsyncMock(), private=False
        )
        self.view.message = MagicMock(spec=discord.InteractionMessage)
        self.view.message.attachments = [MagicMock(spec=discord.Attachment)]
        self.view.look = self.request.look
        self.view.revealed_order = [ProfileDetail.XP, ProfileDetail.ACTIVITY]
        self.addAsyncCleanup(self.cog.cog_unload)

    async def _refresh(self, item: discord.Interaction) -> None:
        await self.cog._deliver(item, 42, 10, self.view)

    async def test_matching_requests_share_preparation_and_reuse_cached_identity(
        self,
    ) -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def asset(_asset: discord.Asset | None, _label: str) -> bytes:
            started.set()
            await release.wait()
            return b"asset"

        with (
            patch.object(
                self.cog, "_timeline", AsyncMock(return_value=self.request.snapshot)
            ),
            patch.object(self.cog, "_asset_bytes", side_effect=asset) as assets,
            patch.object(cog_module, "build_profile", wraps=build_profile) as build,
            patch.object(
                self.cog._media_renderer,
                "render",
                AsyncMock(return_value=ProfileMedia(b"main", "png")),
            ) as render,
        ):
            first = asyncio.create_task(
                self.cog._prepare(self.item.guild, self.item.user)
            )
            await started.wait()
            followers = [
                asyncio.create_task(self.cog._prepare(self.item.guild, self.item.user))
                for _ in range(2)
            ]
            release.set()
            requests = await asyncio.gather(first, *followers)
            cached = await self.cog._prepare(self.item.guild, self.item.user)
        build.assert_called_once()
        self.assertEqual(assets.await_count, 2)
        render.assert_awaited_once()
        for request in requests:
            self.assertIs(request.identity, cached.identity)
            self.assertIs(request.profile, cached.profile)
            self.assertIs(request.media, cached.media)
        self.assertEqual(cached.look.appearance, cached.profile.appearance)

    async def test_six_requests_admit_four_and_reject_two_before_preparation(
        self,
    ) -> None:
        started, release, rejected = asyncio.Event(), asyncio.Event(), asyncio.Event()
        rejected_count = 0

        async def asset(_asset: discord.Asset | None, _label: str) -> None:
            started.set()
            await release.wait()

        async def request(user_id: int) -> None:
            nonlocal rejected_count
            item = interaction(user_id=user_id)
            try:
                await self.cog._prepare(self.item.guild, item.user)
            except RenderBusyError:
                rejected_count += 1
                if rejected_count == 2:
                    rejected.set()

        with (
            patch.object(
                self.cog, "_timeline", AsyncMock(return_value=self.request.snapshot)
            ),
            patch.object(self.cog, "_asset_bytes", side_effect=asset) as assets,
            patch.object(cog_module, "build_profile", wraps=build_profile) as build,
            patch.object(
                self.cog._media_renderer,
                "render",
                AsyncMock(return_value=ProfileMedia(b"main", "png")),
            ) as render,
        ):
            tasks = [asyncio.create_task(request(user_id)) for user_id in range(1, 7)]
            try:
                await asyncio.wait_for(rejected.wait(), 5)
                await started.wait()
                self.assertEqual(build.call_count, 1)
                self.assertEqual(assets.await_count, 2)
                render.assert_not_awaited()
                with patch.object(cog_module, "build_xp_detail") as detail:
                    with self.assertRaises(RenderBusyError):
                        await self.cog._reveal(
                            self.item, 42, 10, self.view, ProfileDetail.XP
                        )
                    detail.assert_not_called()
                self.assertEqual(assets.await_count, 2)
            finally:
                release.set()
                await asyncio.gather(*tasks)
        self.assertEqual(rejected_count, 2)
        self.assertEqual(build.call_count, 4)
        self.assertEqual(render.await_count, 4)

    async def test_refresh_uses_one_snapshot_and_identity_and_only_open_details(
        self,
    ) -> None:
        with (
            patch.object(
                self.cog, "_timeline", AsyncMock(return_value=self.request.snapshot)
            ) as timeline,
            patch.object(self.cog, "_asset_bytes", AsyncMock(return_value=None)),
            patch.object(
                self.cog._media_renderer,
                "render",
                AsyncMock(return_value=ProfileMedia(b"main", "png")),
            ) as main,
            patch.object(
                self.cog._media_renderer,
                "render_xp",
                AsyncMock(return_value=ProfileMedia(b"xp", "png")),
            ) as xp,
            patch.object(
                self.cog._media_renderer,
                "render_activity",
                AsyncMock(return_value=ProfileMedia(b"activity", "png")),
            ) as activity,
            patch.object(
                self.cog._media_renderer, "render_people", AsyncMock()
            ) as people,
            patch.object(
                cog_module, "build_profile", return_value=profile_at()
            ) as build,
        ):
            await self._refresh(self.item)
        timeline.assert_awaited_once_with(42)
        build.assert_called_once()
        self.assertIs(build.call_args.args[0], self.request.snapshot.timeline)
        self.assertIs(xp.call_args.args[1], activity.call_args.args[1])
        self.assertEqual(xp.call_args.args[0].period, activity.call_args.args[0].period)
        self.assertIs(main.call_args.args[1], xp.call_args.args[1].card)
        people.assert_not_awaited()
        self.item.edit_original_response.assert_awaited_once()
        files = self.item.edit_original_response.call_args.kwargs["attachments"]
        self.assertEqual(
            [file.filename for file in files],
            ["voice-profile.png", "voice-xp.png", "voice-activity.png"],
        )
        self.assertTrue(all(file.fp.closed for file in files))

    async def test_failed_refresh_keeps_old_message_and_identity(self) -> None:
        message, look = self.view.message, self.view.look
        file = discord.File(BytesIO(b"main"), filename="voice-profile.png")
        with (
            patch.object(self.cog, "_prepare", AsyncMock(return_value=self.request)),
            patch.object(self.cog, "_attachment", MagicMock(return_value=file)),
            patch.object(
                self.cog,
                "_detail_attachment",
                AsyncMock(side_effect=RuntimeError("render")),
            ),
            patch.object(FeedbackUI, "send", AsyncMock()) as feedback,
        ):
            await self.view.perform(ProfileAction.REFRESH, self.item)
        self.item.edit_original_response.assert_not_awaited()
        self.assertIs(self.view.message, message)
        self.assertIs(self.view.look, look)
        self.assertEqual(
            self.view.revealed_order, [ProfileDetail.XP, ProfileDetail.ACTIVITY]
        )
        self.assertTrue(file.fp.closed)
        feedback.assert_awaited_once()

    async def test_xp_reveal_keeps_displayed_tier_without_rebuilding_main(self) -> None:
        old_look = ProfileLook(
            "Old name",
            "Old guild",
            profile_at(35).appearance,
            self.item.user.display_avatar,
            "UTC",
        )
        self.view.look = old_look
        with (
            patch.object(
                self.cog, "_timeline", AsyncMock(return_value=self.request.snapshot)
            ),
            patch.object(self.cog, "_asset_bytes", AsyncMock(return_value=None)),
            patch.object(cog_module, "build_profile") as build,
            patch.object(cog_module, "build_people_detail") as people,
            patch.object(cog_module, "build_activity_detail") as activity,
            patch.object(
                self.cog._media_renderer,
                "render_xp",
                AsyncMock(return_value=ProfileMedia(b"xp", "png")),
            ) as render,
        ):
            file = await self.cog._reveal(
                self.item, 42, 10, self.view, ProfileDetail.XP
            )
        build.assert_not_called()
        people.assert_not_called()
        activity.assert_not_called()
        self.assertEqual(render.call_args.args[1].appearance, old_look.appearance)
        self.assertEqual(render.call_args.args[1].card.display_name, "Old name")
        file.close()
        file.fp.close()

    async def test_name_resolution_uses_caches_and_unknown_fallback(self) -> None:
        guild = self.item.guild
        member = MagicMock(spec=discord.Member, display_name="Member name")
        user = MagicMock(spec=discord.User, display_name="User name")
        guild.get_member.side_effect = [member, None, None]
        self.bot.get_user.side_effect = [user, None]
        self.assertEqual(
            [self.cog._display_name(guild, i) for i in range(3)],
            ["Member name", "User name", "Unknown user"],
        )
        guild.fetch_member.assert_not_called()

    def test_companion_colors_use_lifetime_profile_tier(self) -> None:
        timeline = example()
        with patch.object(
            cog_module, "build_profile", return_value=profile_at(50)
        ) as build:
            colors = cog_module._companion_colors(timeline, [2], 1, "UTC")
        self.assertEqual(colors, {2: profile_at(50).appearance.color})
        build.assert_called_once_with(timeline, 2, 1, "UTC")


class TestProfileButtonEmojis(unittest.IsolatedAsyncioTestCase):
    async def test_buttons_use_supplied_custom_emojis_without_text_labels(self) -> None:
        view = VoiceProfileView(10, AsyncMock(), reveal=AsyncMock(), private=False)
        expected = {
            ProfileAction.ACTIVITY: "<:statistic:1555684012809388173>",
            ProfileAction.PEOPLE: "<:social:1555684011177672725>",
            ProfileAction.XP: "<:xp:1555684015057543229>",
            ProfileAction.REFRESH: "<:restart:1447913966939406366>",
            ProfileAction.DELETE: "<:trash:1554958233444032653>",
        }
        for action, emoji in expected.items():
            with self.subTest(action=action):
                button = view.buttons[action]
                self.assertEqual(str(button.emoji), emoji)
                self.assertIsNone(button.label)
