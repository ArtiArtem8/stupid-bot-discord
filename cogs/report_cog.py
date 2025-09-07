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

import utils
from config import REPORT_FILE
from utils import BlockedUserError, BlockManager

# command for setting up a report channel (dev only)
# command for sending a report (everyone can use if not blocked)
# save reports with all information in json file
# send necessary message to a private channel


class UserInfo(TypedDict):
    id: int
    name: str
    avatar: str | None


class GuildInfo(TypedDict):
    id: int | None
    name: str | None


class ChannelInfo(TypedDict):
    id: int | None
    name: str | None


class ReportDict(TypedDict):
    user: UserInfo
    guild: GuildInfo
    channel: ChannelInfo
    reason: str
    created_at: str
    report_id: str


class ReportCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("ReportCog")

    async def interaction_check(self, interaction: Interaction):  # type: ignore
        if interaction.guild and BlockManager.is_user_blocked(
            interaction.guild.id, interaction.user.id
        ):
            self.logger.debug(f"User {interaction.user} is blocked.")
            raise BlockedUserError()
        return True

    def _log_report(self, report: ReportDict):
        report_file = utils.get_json(REPORT_FILE) or {}
        report_file["reports"] = [*report_file.get("reports", []), report]
        utils.save_json(REPORT_FILE, report_file)
        self.logger.debug(f"Report saved: {report}")

    def _build_report_message(
        self, interaction: Interaction, reason: str
    ) -> ReportDict:
        create_date = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        if isinstance(interaction.channel, DMChannel):
            channel_name = f"DM with {interaction.channel.recipient}"
        else:
            channel_name = interaction.channel.name if interaction.channel else None
        report_id = str(uuid.uuid4())
        return {
            "user": {
                "id": interaction.user.id,
                "name": interaction.user.name,
                "avatar": interaction.user.avatar.url
                if interaction.user.avatar
                else None,
            },
            "guild": {
                "id": interaction.guild.id if interaction.guild else None,
                "name": interaction.guild.name if interaction.guild else None,
            },
            "channel": {
                "id": interaction.channel.id if interaction.channel else None,
                "name": channel_name,
            },
            "reason": reason,
            "created_at": create_date,
            "report_id": report_id,
        }

    async def _send_report_to_devs(self, report: ReportDict):
        """Send a formatted report to the developers channel."""
        report_channel_id = (utils.get_json(REPORT_FILE) or {}).get("report_channel_id")
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

        embed = discord.Embed(
            title="Жалоба",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="",
            value=shorten(report["reason"], width=1024) or "Нет жалобы",
            inline=False,
        )
        embed.add_field(
            name="От:",
            value=f"{report['user']['name']} (`{report['user']['id']}`)",
            inline=True,
        )

        location: list[str] = []
        if report["guild"] and report["guild"]["name"]:
            location.append(
                f"**Сервер:** {report['guild']['name']} (`{report['guild']['id']}`)"
            )
        if report["channel"]:
            location.append(
                f"**Канал:** {report['channel']['name']} (`{report['channel']['id']}`)"
            )

        if location:
            embed.add_field(name="", value="\n".join(location), inline=False)

        embed.set_footer(text=f"{report['report_id']} • {report['created_at']}")

        # Set thumbnail to user avatar if available
        if report["user"]["avatar"]:
            embed.set_thumbnail(url=report["user"]["avatar"])

        await report_channel.send(embed=embed)

        # Log the full report to a file or database
        self._log_report(report)

    @staticmethod
    def _cooldown_id(interaction: Interaction):
        return (
            interaction.guild.id if interaction.guild else None,
            interaction.user.id,
        )

    @app_commands.command(
        name="report", description="Отправить отчет о баге или проблеме"
    )
    @app_commands.describe(message="Сообщение о жалобе")
    @app_commands.checks.cooldown(1, 60, key=_cooldown_id)
    async def report(self, interaction: Interaction, message: str = ""):
        report = self._build_report_message(interaction, message)
        await self._send_report_to_devs(report)
        await interaction.response.send_message(
            "Жалоба отправлена. Спасибо!", ephemeral=True, silent=True
        )

    @report.error
    async def report_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"Пожалуйста, подождите {error.retry_after:.0f} секунд.",
                ephemeral=True,
                silent=True,
            )

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
        report_file = utils.get_json(REPORT_FILE) or {}
        report_file["report_channel_id"] = channel.id
        utils.save_json(REPORT_FILE, report_file)
        await interaction.response.send_message(
            f"Report channel set to {channel.mention}", ephemeral=True, silent=True
        )


async def setup(bot: commands.Bot):
    """Setup.

    :param commands.Bot bot: BOT ITSELF
    """
    await bot.add_cog(ReportCog(bot))
