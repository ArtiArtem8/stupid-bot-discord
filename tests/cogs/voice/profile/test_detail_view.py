"""Atomic progressive disclosure, retry and concurrent action contracts."""

import asyncio
import unittest
from io import BytesIO
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.voice.profile import view as view_module
from cogs.voice.profile.media import RenderBusyError
from cogs.voice.profile.view import ProfileAction, ProfileDetail, VoiceProfileView
from framework.feedback_ui import FeedbackUI
from tests.cogs.voice.test_profile_cog import interaction


def attachment(name: str) -> MagicMock:
    item = MagicMock(spec=discord.Attachment)
    item.filename = name
    item.id = 100 + (
        "voice-profile.webp",
        "voice-xp.png",
        "voice-activity.png",
        "voice-people.png",
    ).index(name)
    return item


class TestDetailView(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        self.refresh = AsyncMock()
        self.reveal = AsyncMock(side_effect=self._render)
        self.view = VoiceProfileView(
            10, self.refresh, reveal=self.reveal, private=False
        )
        self.message = MagicMock(spec=discord.InteractionMessage)
        self.message.attachments = [attachment("voice-profile.webp")]
        self.message.embeds = []
        self.message.channel.id = 42
        self.view.message = self.message
        self.access = patch.object(
            view_module, "check_component_access", AsyncMock(return_value=True)
        )
        self.access.start()
        self.addCleanup(self.access.stop)

    @staticmethod
    async def _render(
        _item: discord.Interaction, detail: ProfileDetail
    ) -> discord.File:
        return discord.File(BytesIO(b"png"), filename=f"voice-{detail.value}.png")

    async def _click(self, action: ProfileAction) -> MagicMock:
        item = interaction()

        async def edit(
            *,
            attachments: list[discord.Attachment | discord.File],
            embeds: list[discord.Embed],
            view: VoiceProfileView,
        ) -> MagicMock:
            self.assertIs(view, self.view)
            self.assertIn(self.view.buttons[action], view.children)
            # The outgoing payload removes the button, while failed upload can
            # still leave the live View unchanged.
            expected = [
                button.to_component_dict()
                for button in view.buttons.values()
                if button in view.children and button.action != action
            ]
            self.assertEqual(
                view.to_components(), [{"type": 1, "components": expected}]
            )
            result = MagicMock(spec=discord.InteractionMessage)
            result.channel.id = 42
            files = {file.filename: file for file in attachments}
            result.embeds = []
            embedded: set[str] = set()
            for embed in embeds:
                name = (embed.image.url or "").removeprefix("attachment://")
                self.assertIn(name, files)
                identifier = attachment(name).id
                reference = files[name]
                if not isinstance(reference, discord.File):
                    self.assertEqual(reference.id, identifier)
                url = f"https://cdn.discordapp.com/attachments/42/{identifier}/{name}"
                result.embeds.append(discord.Embed().set_image(url=url))
                embedded.add(name)
            result.attachments = [
                file for file in attachments if file.filename not in embedded
            ]
            self.assertEqual(
                sum(isinstance(file, discord.File) for file in attachments), 1
            )
            return result

        item.edit_original_response.side_effect = edit
        await self.view.buttons[action].callback(item)
        return item

    async def test_xp_activity_people_preserve_click_order_and_current_attachments(
        self,
    ) -> None:
        original = self.message.attachments[0]
        xp = await self._click(ProfileAction.XP)
        self.assertIs(
            xp.edit_original_response.call_args.kwargs["attachments"][0], original
        )
        await self._click(ProfileAction.ACTIVITY)
        await self._click(ProfileAction.PEOPLE)
        message = self.view.message
        if message is None:
            self.fail("Successful reveals must retain the edited message")
        self.assertEqual(
            [item.filename for item in message.attachments],
            ["voice-profile.webp"],
        )
        self.assertEqual(
            [(embed.image.url or "").rsplit("/", 1)[-1] for embed in message.embeds],
            ["voice-xp.png", "voice-activity.png", "voice-people.png"],
        )
        self.assertEqual(
            self.view.revealed_order,
            [ProfileDetail.XP, ProfileDetail.ACTIVITY, ProfileDetail.PEOPLE],
        )
        self.assertEqual(
            self.view.children,
            [
                self.view.buttons[ProfileAction.REFRESH],
                self.view.buttons[ProfileAction.DELETE],
            ],
        )
        self.refresh.assert_not_awaited()
        self.assertEqual(self.reveal.await_count, 3)

    async def test_activity_then_xp_has_no_people_render(self) -> None:
        await self._click(ProfileAction.ACTIVITY)
        await self._click(ProfileAction.XP)
        self.assertEqual(
            self.view.revealed_order, [ProfileDetail.ACTIVITY, ProfileDetail.XP]
        )
        self.assertIn(self.view.buttons[ProfileAction.PEOPLE], self.view.children)

    async def test_dispatched_click_after_button_removal_is_acknowledged(self) -> None:
        await self._click(ProfileAction.ACTIVITY)
        duplicate = interaction()
        await self.view.buttons[ProfileAction.ACTIVITY].callback(duplicate)
        duplicate.response.defer.assert_awaited_once()
        duplicate.edit_original_response.assert_not_awaited()
        self.reveal.assert_awaited_once()

    async def test_double_click_waits_then_rechecks_revealed_state(self) -> None:
        started, release, deferred = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def render(
            item: discord.Interaction, detail: ProfileDetail
        ) -> discord.File:
            started.set()
            await release.wait()
            return await self._render(item, detail)

        self.reveal.side_effect = render
        first = asyncio.create_task(self._click(ProfileAction.ACTIVITY))
        await started.wait()
        second = interaction()
        second.response.defer.side_effect = deferred.set
        duplicate = asyncio.create_task(
            self.view.perform(ProfileAction.ACTIVITY, second)
        )
        await deferred.wait()
        release.set()
        await asyncio.gather(first, duplicate)
        self.reveal.assert_awaited_once()
        second.edit_original_response.assert_not_awaited()
        self.assertEqual(self.view.revealed_order, [ProfileDetail.ACTIVITY])

    async def test_render_and_busy_failures_keep_button_and_message(self) -> None:
        for error in (RuntimeError("render"), RenderBusyError("busy")):
            with self.subTest(error=type(error).__name__):
                self.reveal.side_effect = error
                item = interaction()
                with patch.object(FeedbackUI, "send", AsyncMock()) as feedback:
                    await self.view.perform(ProfileAction.ACTIVITY, item)
                feedback.assert_awaited_once()
                self.assertTrue(feedback.call_args.kwargs["ephemeral"])
                item.edit_original_response.assert_not_awaited()
                self.assertIs(self.view.message, self.message)
                self.assertEqual(self.view.revealed_order, [])
                self.assertIn(
                    self.view.buttons[ProfileAction.ACTIVITY], self.view.children
                )

    async def test_upload_failure_restores_serialization_and_closes_file(self) -> None:
        file = discord.File(BytesIO(b"png"), filename="voice-activity.png")
        self.reveal.side_effect = None
        self.reveal.return_value = file
        before = self.view.to_components()
        item = interaction()
        item.edit_original_response.side_effect = discord.HTTPException(
            MagicMock(status=503), "Unavailable"
        )
        with patch.object(FeedbackUI, "send", AsyncMock()):
            await self.view.perform(ProfileAction.ACTIVITY, item)
        self.assertTrue(file.fp.closed)
        self.assertEqual(self.view.to_components(), before)
        self.assertEqual(self.view.revealed_order, [])
        self.assertIs(self.view.message, self.message)

    async def test_non_owner_and_blocked_owner_cannot_reveal(self) -> None:
        await self.view.perform(ProfileAction.XP, interaction(user_id=11))
        with patch.object(
            view_module, "check_component_access", AsyncMock(return_value=False)
        ):
            await self.view.perform(ProfileAction.XP, interaction())
        self.reveal.assert_not_awaited()

    async def test_delete_waits_for_reveal(self) -> None:
        started, release, deferred = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def render(
            item: discord.Interaction, detail: ProfileDetail
        ) -> discord.File:
            started.set()
            await release.wait()
            return await self._render(item, detail)

        self.reveal.side_effect = render
        revealing = asyncio.create_task(self._click(ProfileAction.XP))
        await started.wait()
        item = interaction()
        item.response.defer.side_effect = deferred.set
        deleting = asyncio.create_task(self.view.perform(ProfileAction.DELETE, item))
        await deferred.wait()
        item.delete_original_response.assert_not_awaited()
        release.set()
        await asyncio.gather(revealing, deleting)
        item.delete_original_response.assert_awaited_once()
        self.assertTrue(self.view.is_finished())
