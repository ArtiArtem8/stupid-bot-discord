"""Issue-report commands and owner-only report-channel configuration."""

import logging
from typing import TYPE_CHECKING

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from api.reporting import ReportModal
from framework.base_cog import BaseCog
from framework.checks import is_owner_app
from framework.feedback_ui import FeedbackType, FeedbackUI
from repositories.report_repository import ReportRepository

if TYPE_CHECKING:
    from framework.bot import StupidBot

logger = logging.getLogger(__name__)


def get_cooldown_key(interaction: Interaction) -> tuple[int | None, int]:
    """Share report cooldowns per user within each guild or DM context."""
    return (
        interaction.guild.id if interaction.guild else None,
        interaction.user.id,
    )


class ReportCog(BaseCog):
    """Bug-report submission and owner-only channel configuration."""

    def __init__(self, bot: commands.Bot, repository: ReportRepository) -> None:
        super().__init__(bot)
        self.repository = repository

    @app_commands.command(
        name="report", description="Отправить отчет о баге или проблеме"
    )
    @app_commands.checks.cooldown(1, 60, key=get_cooldown_key)
    async def report(self, interaction: Interaction) -> None:
        await interaction.response.send_modal(ReportModal(self.repository))

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
    ) -> None:
        await self.repository.set_channel(channel.id)
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description=f"Report channel set to {channel.mention}",
            ephemeral=True,
        )


async def setup(bot: "StupidBot") -> None:
    """Register the reporting cog."""
    await bot.add_cog(ReportCog(bot, bot.report_repository))
