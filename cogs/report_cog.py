"""Report system provides a way to report issues with the bot.
the report saves in a json file and send into a private devs channel to review.

admin cog is not universal, so interactions checks are manually added here.

This cog requires setting an owner ID to use this command :
- via configuration file - DISCORD_BOT_OWNER_ID
- or manually - self.bot.owner_id: int = ?
- for multiple owners only - self.bot.owner_ids: Collection[int]
  don't set both at the same time
"""

import logging
import uuid
from datetime import datetime
from textwrap import shorten
from typing import TypedDict

import discord
from discord import DMChannel, Interaction, app_commands
from discord.abc import PrivateChannel
from discord.ext import commands
from discord.ui import Label, TextInput

from config import REPORT_FILE
from utils import BaseCog, get_json, save_json


class UserInfoDict(TypedDict):
    """User information for report."""

    id: int
    name: str
    avatar: str | None


class GuildInfoDict(TypedDict):
    """Guild information for report."""

    id: int | None
    name: str | None


class ChannelInfoDict(TypedDict):
    """Channel information for report."""

    id: int | None
    name: str | None


class ReportDataDict(TypedDict):
    """Complete report data structure."""

    user: UserInfoDict
    guild: GuildInfoDict
    channel: ChannelInfoDict
    reason: str
    created_at: str
    report_id: str


def get_cooldown_key(interaction: Interaction) -> tuple[int | None, int]:
    """Generate cooldown key for rate limiting.

    Args:
        interaction: Command interaction

    Returns:
        Tuple of (guild_id, user_id) for cooldown tracking

    """
    return (
        interaction.guild.id if interaction.guild else None,
        interaction.user.id,
    )


class ReportModal(discord.ui.Modal, title="Отправить отчёт о баге"):
    """Modal dialog for submitting bug reports.

    Provides a text area for users to describe issues or bugs
    they've encountered with the bot.
    """

    report: Label["ReportModal"] = Label(
        text="Описание проблемы",
        component=TextInput(
            style=discord.TextStyle.paragraph,
            custom_id="report_reason",
            placeholder="Опишите, что случилось...",
            required=True,
            max_length=1024,
            min_length=10,
        ),
    )

    def __init__(self, report_cog: "ReportCog"):
        super().__init__()
        self.cog = report_cog

    async def on_submit(self, interaction: Interaction):
        # satisfy type checker
        if not isinstance(self.report.component, discord.ui.TextInput):
            return

        message = self.report.component.value
        report = self.cog.build_report_data(interaction, message)

        await self.cog.send_report_to_devs(report)
        await interaction.response.send_message(
            "Ваш отчет отправлен. Спасибо!", ephemeral=True
        )


class ReportCog(BaseCog):
    """Bug reporting system with developer notification.

    Configuration:
        Set report channel using /set-report-channel (owner only)
    """

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.logger = logging.getLogger("ReportCog")

    def _save_report_to_file(self, report: ReportDataDict):
        """Persist report to JSON file."""
        report_data = get_json(REPORT_FILE) or {}
        report_data.setdefault("reports", [])
        report_data["reports"].append(report)

        save_json(REPORT_FILE, report_data)
        self.logger.info(
            "New report from %s - %s", report["user"]["name"], report["report_id"]
        )
        self.logger.debug(f"Report saved: {report}")

    def build_report_data(
        self, interaction: Interaction, reason: str
    ) -> ReportDataDict:
        """Build structured report data from interaction.

        Args:
            interaction: Command interaction
            reason: User-provided report text

        Returns:
            Structured report dictionary

        """
        create_date = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        if isinstance(interaction.channel, DMChannel):
            channel_name = f"DM with {interaction.channel.recipient}"
        else:
            channel_name = interaction.channel.name if interaction.channel else None
        report_id = str(uuid.uuid4())
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
            "report_id": report_id,
        }

    async def send_report_to_devs(self, report: ReportDataDict):
        """Send a formatted report to the developers channel."""
        report_channel_id = (get_json(REPORT_FILE) or {}).get("report_channel_id")
        if not report_channel_id:
            self.logger.warning("Report channel ID not found.")
            return

        report_channel = self.bot.get_channel(report_channel_id)
        if not report_channel or isinstance(
            report_channel,
            (discord.CategoryChannel, discord.ForumChannel, PrivateChannel),
        ):
            self.logger.error("Report channel not found or invalid.")
            return

        embed = self._create_report_embed(report)

        await report_channel.send(embed=embed)
        self._save_report_to_file(report)

    def _create_report_embed(self, report: ReportDataDict) -> discord.Embed:
        """Create formatted embed for report display."""
        embed = discord.Embed(
            title="Отчёт",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="Описание",
            value=shorten(report["reason"], width=1024) or "Нет описания",
            inline=False,
        )
        embed.add_field(
            name="Отправитель",
            value=f"{report['user']['name']} (`{report['user']['id']}`)",
            inline=True,
        )
        location_lines: list[str] = []
        if report["guild"]["name"]:
            location_lines.append(
                f"**Сервер:** {report['guild']['name']} (`{report['guild']['id']}`)"
            )
        if report["channel"]["name"]:
            location_lines.append(
                f"**Канал:** {report['channel']['name']} (`{report['channel']['id']}`)"
            )

        if location_lines:
            embed.add_field(
                name="Локация",
                value="\n".join(location_lines),
                inline=False,
            )
        embed.set_footer(text=f"ID: {report['report_id']} • {report['created_at']}")
        if report["user"]["avatar"]:
            embed.set_thumbnail(url=report["user"]["avatar"])

        return embed

    @app_commands.command(
        name="report", description="Отправить отчет о баге или проблеме"
    )
    @app_commands.checks.cooldown(1, 60, key=get_cooldown_key)
    async def report(self, interaction: Interaction):
        await interaction.response.send_modal(ReportModal(self))

    @app_commands.command(
        name="set-report-channel",
        description="Установить канал для жалоб (для разработчиков)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.describe(channel="Report channel")
    @commands.is_owner()
    async def set_report_channel(
        self, interaction: Interaction, channel: discord.TextChannel
    ):
        report_data = get_json(REPORT_FILE) or {}
        report_data["report_channel_id"] = channel.id
        save_json(REPORT_FILE, report_data)
        await interaction.response.send_message(
            f"Report channel set to {channel.mention}", ephemeral=True, silent=True
        )


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(ReportCog(bot))
