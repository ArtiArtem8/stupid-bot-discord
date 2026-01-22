from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Self, TypedDict, cast, override

import discord
from discord import DMChannel, Interaction
from discord.ui import Modal, TextInput

import config
from utils import SafeEmbed
from utils.json_types import JsonObject, JsonValue
from utils.json_utils import get_json, save_json

logger = logging.getLogger(__name__)


class UserInfoDict(TypedDict):
    id: int
    name: str
    avatar: str | None


class GuildInfoDict(TypedDict):
    id: int | None
    name: str | None


class ChannelInfoDict(TypedDict):
    id: int | None
    name: str | None


class ReportDataDict(TypedDict):
    user: UserInfoDict
    guild: GuildInfoDict
    channel: ChannelInfoDict
    reason: str
    created_at: str
    report_id: str


def _build_report_data(interaction: Interaction, reason: str) -> ReportDataDict:
    """Constructs the report dictionary."""
    create_date = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

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
    """Formats the report for Discord."""
    embed = SafeEmbed(
        title="Отчёт",
        color=config.Color.INFO,
        timestamp=datetime.now(),
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


async def submit_report(interaction: Interaction, reason: str) -> str:
    """Main entry point: Saves report and notifies devs."""
    report = _build_report_data(interaction, reason)

    data: JsonObject = get_json(config.REPORT_FILE) or {}
    reports = data.get("reports")
    if not isinstance(reports, list):
        reports = []
        data["reports"] = reports
    reports.append(cast(JsonValue, cast(object, report)))
    save_json(config.REPORT_FILE, data)
    logger.info("New report: %s", report["report_id"])

    report_channel_id = data.get("report_channel_id")
    if isinstance(report_channel_id, int):
        channel = interaction.client.get_channel(report_channel_id)
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(embed=_create_report_embed(report))
    return report["report_id"]


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

    def __init__(self, error_info: str | None = None) -> None:
        super().__init__()
        if error_info:
            self.reason.default = error_info

    @override
    async def on_submit(self, interaction: Interaction):
        report_id = await submit_report(interaction, self.reason.value)

        if interaction.message:
            try:
                await interaction.message.edit(view=None)
            except discord.HTTPException:
                logger.warning(
                    "Failed to remove report button from message %s",
                    interaction.message.id,
                )

        embed = SafeEmbed(
            title="Спасибо за отчёт!",
            description=f"-# Ваш персональный ID: `{report_id}`",
            color=config.Color.SUCCESS,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def handle_report_button(
    interaction: discord.Interaction, error_info: str | None = None
) -> None:
    """Callback handler for report button - shows modal."""
    await interaction.response.send_modal(ReportModal(error_info))
