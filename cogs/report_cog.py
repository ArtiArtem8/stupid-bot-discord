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

import discord
from discord import Interaction, app_commands
from discord.ext import commands

import config
from api import ReportModal
from framework import BaseCog, FeedbackType, FeedbackUI, is_owner_app
from utils import get_json, save_json

logger = logging.getLogger(__name__)


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


class ReportCog(BaseCog):
    """Bug reporting system with developer notification.

    Configuration:
        Set report channel using /set-report-channel (owner only)
    """

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)

    @app_commands.command(
        name="report", description="Отправить отчет о баге или проблеме"
    )
    @app_commands.checks.cooldown(1, 60, key=get_cooldown_key)
    async def report(self, interaction: Interaction):
        await interaction.response.send_modal(ReportModal())

    @app_commands.command(
        name="set-report-channel",
        description="Установить канал для жалоб (для разработчиков)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.describe(channel="Report channel")
    @is_owner_app()
    async def set_report_channel(
        self, interaction: Interaction, channel: discord.TextChannel
    ):
        report_data = get_json(config.REPORT_FILE) or {}
        report_data["report_channel_id"] = channel.id
        save_json(config.REPORT_FILE, report_data)
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description=f"Report channel set to {channel.mention}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    """Setup.

    Args:
        bot: BOT ITSELF

    """
    await bot.add_cog(ReportCog(bot))
