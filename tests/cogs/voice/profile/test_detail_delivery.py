"""One-snapshot refresh, focused reveals and application emoji lookup."""

import unittest
from io import BytesIO
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.voice import profile_cog as cog_module
from cogs.voice.profile.detail_models import ProfileLook
from cogs.voice.profile.media import ProfileMedia
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
            patch.object(self.cog, "_attachment", AsyncMock(return_value=file)),
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


class TestApplicationEmojis(unittest.IsolatedAsyncioTestCase):
    async def test_name_lookup_is_cached_and_only_missing_buttons_use_text(
        self,
    ) -> None:
        for missing in (None, ProfileAction.PEOPLE):
            with self.subTest(missing=missing):
                emojis: list[MagicMock] = []
                for action in ProfileAction:
                    if action == missing:
                        continue
                    emoji = MagicMock(spec=discord.Emoji)
                    emoji.name = f"voice_{action.value}"
                    emojis.append(emoji)
                bot = MagicMock()
                bot.fetch_application_emojis = AsyncMock(return_value=emojis)
                cog = VoiceProfileCog(bot)
                await cog._load_emojis()
                for _ in range(2):
                    view = VoiceProfileView(
                        10,
                        AsyncMock(),
                        reveal=AsyncMock(),
                        emojis=cog._emojis,
                        private=False,
                    )
                    for action, button in view.buttons.items():
                        self.assertEqual(
                            button.label,
                            action.value.title() if action == missing else None,
                        )
                        self.assertEqual(button.emoji is None, action == missing)
                bot.fetch_application_emojis.assert_awaited_once()

    async def test_lookup_failure_leaves_feature_available_with_one_warning(
        self,
    ) -> None:
        bot = MagicMock()
        bot.fetch_application_emojis = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=503), "unavailable")
        )
        cog = VoiceProfileCog(bot)
        with self.assertLogs(cog_module.logger, level="WARNING") as logs:
            await cog._load_emojis()
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(cog._emojis, {})
