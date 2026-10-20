from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Self, override

import discord
from discord import DMChannel, Interaction
from discord.ui import Label, Modal, TextInput

from stupidbot.discord_ui.access import check_command_access, check_component_access
from stupidbot.discord_ui.base_cog import MODAL_ACCESS_TIMEOUT
from stupidbot.discord_ui.embeds import SafeEmbed
from stupidbot.discord_ui.errors import handle_app_command_error
from stupidbot.discord_ui.exceptions import (
    AccessCheckUnavailableError,
    InteractionAcknowledgementError,
)
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.discord_ui.interaction_flow import defer_command
from stupidbot.discord_ui.limits import MAX_EMBED_FIELD_LENGTH
from stupidbot.discord_ui.theme import Color
from stupidbot.features.reports.messages import ReportMessages
from stupidbot.features.reports.models import ReportDataDict
from stupidbot.features.reports.repository import ReportRepository
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import error_context, guild_context
from stupidbot.i18n.policy import Language, PresentationContext

logger = logging.getLogger(__name__)


def _build_report_data(interaction: Interaction, reason: str) -> ReportDataDict:
    """Snapshot actual names; unnamed locations receive labels at presentation."""
    create_date = datetime.now(UTC).isoformat()

    channel_name = None
    if interaction.channel and not isinstance(interaction.channel, DMChannel):
        channel_name = interaction.channel.name

    user = interaction.user
    guild = interaction.guild

    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "avatar": user.avatar.url if user.avatar else None,
        },
        "guild": {
            "id": guild.id if guild else None,
            "name": guild.name if guild else None,
        },
        "channel": {
            "id": interaction.channel.id if interaction.channel else None,
            "name": channel_name,
        },
        "reason": reason,
        "created_at": create_date,
        "report_id": str(uuid.uuid4()),
    }


def _create_report_embed(
    report: ReportDataDict, *, language: Language = Language.RU
) -> discord.Embed:
    """Format a stored report for Discord."""
    messages = ReportMessages(catalog("reports", language))
    embed = SafeEmbed(
        title=messages.catalog.pgettext("ui", "Report"),
        color=Color.INFO,
        timestamp=datetime.now(UTC),
    )
    embed.safe_add_field(
        name=messages.catalog.pgettext("ui", "Description"),
        value=report["reason"],
        inline=False,
    )
    embed.safe_add_field(
        name=messages.catalog.pgettext("ui", "Sender"),
        value=f"{report['user']['name']} (`{report['user']['id']}`)",
        inline=True,
    )

    locs: list[str] = []
    if report["guild"]["name"]:
        locs.append(messages.guild(name=report["guild"]["name"]))
    channel_name = report["channel"]["name"]
    if channel_name is None:
        channel_name = (
            messages.catalog.pgettext("ui", "Direct messages")
            if report["channel"]["id"] is not None and report["guild"]["id"] is None
            else messages.catalog.pgettext("ui", "Unknown channel")
        )
    locs.append(messages.channel(name=channel_name))
    embed.safe_add_field(
        name=messages.catalog.pgettext("ui", "Location"),
        value="\n".join(locs),
        inline=False,
    )

    embed.set_footer(text=f"ID: {report['report_id']}")
    if report["user"]["avatar"]:
        embed.set_thumbnail(url=report["user"]["avatar"])
    return embed


async def submit_report(
    repository: ReportRepository, interaction: Interaction, reason: str
) -> tuple[ReportDataDict, int | None]:
    """Commit a deduplicated report before reporting success or notifying Discord."""
    result = await repository.submit(
        _build_report_data(interaction, reason), request_key=str(interaction.id)
    )
    logger.info("Report persisted: %s", result[0]["report_id"])
    return result


async def _notify_report(
    interaction: Interaction, report: ReportDataDict, report_channel_id: int | None
) -> None:
    if report_channel_id is None:
        return

    channel = interaction.client.get_channel(report_channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        logger.warning(
            "Report notification skipped: report=%s channel=%s unavailable in cache",
            report["report_id"],
            report_channel_id,
        )
        return
    try:
        guild: object = getattr(channel, "guild", None)
        context = (
            await guild_context(interaction.client, guild.id)
            if isinstance(guild, discord.Guild)
            else await error_context(interaction)
        )
        await channel.send(
            embed=_create_report_embed(report, language=context.language)
        )
    except discord.HTTPException as exc:
        logger.warning(
            "Failed to notify report channel %s for report %s (HTTP %s, code %s)",
            report_channel_id,
            report["report_id"],
            exc.status,
            exc.code,
        )
    except Exception:
        logger.warning(
            "Report committed but notification failed: %s", report["report_id"]
        )
        logger.debug("Report notification failed", exc_info=True)


async def _remove_report_button(message: discord.Message | None) -> None:
    if message is None:
        return
    try:
        await message.edit(view=None)
    except discord.HTTPException:
        logger.warning("Failed to remove report button from message %s", message.id)


class ReportModal(Modal):
    """Collect a report and confirm only after it has been committed."""

    def __init__(
        self,
        repository: ReportRepository,
        error_info: str | None = None,
        *,
        context: PresentationContext,
    ) -> None:
        self.context = context
        self.messages = ReportMessages(catalog("reports", context.language))
        super().__init__(
            title=self.messages.catalog.pgettext("ui", "Submit a bug report")
        )
        self.reason = TextInput[Self](
            style=discord.TextStyle.paragraph,
            placeholder=self.messages.catalog.pgettext(
                "ui", "Describe what happened..."
            ),
            required=True,
            max_length=MAX_EMBED_FIELD_LENGTH,
            min_length=10,
        )
        self.add_item(
            Label(
                text=self.messages.catalog.pgettext("ui", "Problem description"),
                component=self.reason,
            )
        )
        self._repository = repository
        if error_info:
            self.reason.default = error_info

    @override
    async def on_submit(self, interaction: Interaction) -> None:
        await defer_command(interaction, ephemeral=True)
        if not await check_component_access(interaction):
            return
        report, report_channel_id = await submit_report(
            self._repository, interaction, self.reason.value
        )
        embed = SafeEmbed(
            title=self.messages.catalog.pgettext("ui", "Thank you for the report!"),
            description=self.messages.receipt(report_id=report["report_id"]),
            color=Color.SUCCESS,
        )
        try:
            await interaction.edit_original_response(embed=embed)
            await _remove_report_button(interaction.message)
        finally:
            await _notify_report(interaction, report, report_channel_id)

    @override
    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        """Resolve modal failures outside application-command error routing."""
        self.stop()
        if isinstance(error, InteractionAcknowledgementError):
            await handle_app_command_error(interaction, error)
            return
        logger.error(
            "Report modal failed: interaction=%s user=%s",
            interaction.id,
            interaction.user.id,
            exc_info=error,
        )
        await error_context(interaction)
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.ERROR,
            description=self.messages.catalog.pgettext(
                "ui",
                "Could not finish processing the report. Details have been logged.",
            ),
            ephemeral=True,
            disable_report_btn=True,
        )


async def handle_report_button(
    repository: ReportRepository,
    interaction: discord.Interaction,
    error_info: str | None = None,
) -> None:
    """Open the modal; old report buttons must recheck current guild access."""
    if interaction.type is discord.InteractionType.component:
        try:
            async with asyncio.timeout(MODAL_ACCESS_TIMEOUT):
                await check_command_access(interaction)
        except TimeoutError:
            await handle_app_command_error(interaction, AccessCheckUnavailableError())
            return
        except Exception as error:
            await handle_app_command_error(interaction, error)
            return
    context = await error_context(interaction)
    await interaction.response.send_modal(
        ReportModal(repository, error_info, context=context)
    )
