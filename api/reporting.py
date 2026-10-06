from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Self, override

import discord
from discord import DMChannel, Interaction
from discord.ui import Modal, TextInput

import config
from api.report_models import ReportDataDict
from repositories.report_repository import ReportRepository
from utils.embeds import SafeEmbed

logger = logging.getLogger(__name__)


def _build_report_data(interaction: Interaction, reason: str) -> ReportDataDict:
    """Construct the stored report data."""
    create_date = datetime.now(UTC).isoformat()

    channel_name = "Unknown"
    if isinstance(interaction.channel, DMChannel):
        channel_name = f"DM with {interaction.channel.recipient}"
    elif interaction.channel:
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


def _create_report_embed(report: ReportDataDict) -> discord.Embed:
    """Format a stored report for Discord."""
    embed = SafeEmbed(
        title="Отчёт",
        color=config.Color.INFO,
        timestamp=datetime.now(UTC),
    )
    embed.safe_add_field(
        name="Описание",
        value=report["reason"],
        inline=False,
    )
    embed.safe_add_field(
        name="Отправитель",
        value=f"{report['user']['name']} (`{report['user']['id']}`)",
        inline=True,
    )

    locs: list[str] = []
    if report["guild"]["name"]:
        locs.append(f"**Сервер:** {report['guild']['name']}")
    if report["channel"]["name"]:
        locs.append(f"**Канал:** {report['channel']['name']}")

    if locs:
        embed.safe_add_field(name="Локация", value="\n".join(locs), inline=False)

    embed.set_footer(text=f"ID: {report['report_id']}")
    if report["user"]["avatar"]:
        embed.set_thumbnail(url=report["user"]["avatar"])
    return embed


async def submit_report(
    repository: ReportRepository, interaction: Interaction, reason: str
) -> tuple[ReportDataDict, int | None]:
    """Commit a deduplicated report before acknowledging or notifying Discord."""
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
    if isinstance(channel, discord.abc.Messageable):
        try:
            await channel.send(embed=_create_report_embed(report))
        except discord.HTTPException as exc:
            logger.warning(
                "Failed to notify report channel %s for report %s (HTTP %s, code %s)",
                report_channel_id,
                report["report_id"],
                exc.status,
                exc.code,
            )


class ReportModal(Modal, title="Отправить отчёт о баге"):
    """Modal dialog for submitting bug reports.

    Provides a text area for users to describe issues or bugs
    they've encountered with the bot.
    """

    reason = TextInput[Self](
        label="Описание проблемы",
        style=discord.TextStyle.paragraph,
        placeholder="Опишите, что случилось...",
        required=True,
        max_length=config.MAX_EMBED_FIELD_LENGTH,
        min_length=10,
    )

    def __init__(
        self, repository: ReportRepository, error_info: str | None = None
    ) -> None:
        super().__init__()
        self._repository = repository
        if error_info:
            self.reason.default = error_info

    @override
    async def on_submit(self, interaction: Interaction) -> None:
        report, report_channel_id = await submit_report(
            self._repository, interaction, self.reason.value
        )
        embed = SafeEmbed(
            title="Спасибо за отчёт!",
            description=f"-# Ваш персональный ID: `{report['report_id']}`",
            color=config.Color.SUCCESS,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        if interaction.message:
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                logger.warning(
                    "Failed to remove report button from message %s",
                    interaction.message.id,
                )
        await _notify_report(interaction, report, report_channel_id)


async def handle_report_button(
    repository: ReportRepository,
    interaction: discord.Interaction,
    error_info: str | None = None,
) -> None:
    """Open the report-submission modal."""
    await interaction.response.send_modal(ReportModal(repository, error_info))
