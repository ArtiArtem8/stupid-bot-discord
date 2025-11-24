import logging
import uuid
from datetime import datetime
from textwrap import shorten
from typing import Self, TypedDict

import discord
from discord import DMChannel, Interaction
from discord.ui import Modal, TextInput

import config
from utils.json_utils import get_json, save_json

LOGGER = logging.getLogger(__name__)


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
    embed = discord.Embed(
        title="Отчёт",
        color=config.Color.INFO,
        timestamp=datetime.now(),
    )
    embed.add_field(
        name="Описание", value=shorten(report["reason"], width=1024), inline=False
    )
    embed.add_field(
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
        embed.add_field(name="Локация", value="\n".join(locs), inline=False)

    embed.set_footer(text=f"ID: {report['report_id']}")
    if report["user"]["avatar"]:
        embed.set_thumbnail(url=report["user"]["avatar"])
    return embed


async def submit_report(interaction: Interaction, reason: str) -> None:
    """Main entry point: Saves report and notifies devs."""
    report = _build_report_data(interaction, reason)

    data = get_json(config.REPORT_FILE) or {}
    data.setdefault("reports", []).append(report)
    save_json(config.REPORT_FILE, data)
    LOGGER.info("New report: %s", report["report_id"])

    report_channel_id = data.get("report_channel_id")
    if report_channel_id:
        channel = interaction.client.get_channel(report_channel_id)
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(embed=_create_report_embed(report))


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

    async def on_submit(self, interaction: Interaction):
        await submit_report(interaction, self.reason.value)
        await interaction.response.send_message(
            "Ваш отчет отправлен. Спасибо!", ephemeral=True
        )


async def handle_report_button(interaction: discord.Interaction) -> None:
    """Callback handler for report button - shows modal."""
    await interaction.response.send_modal(ReportModal())
