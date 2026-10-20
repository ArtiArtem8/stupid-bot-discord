"""Issue-report commands and owner-only report-channel configuration."""

from typing import TYPE_CHECKING, override

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.discord_ui.checks import is_owner_app
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.features.reports.messages import ReportMessages
from stupidbot.features.reports.repository import ReportRepository
from stupidbot.features.reports.ui import handle_report_button
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import interaction_context

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot


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

    @override
    def command_response_ephemeral(self, _interaction: Interaction) -> bool | None:
        return None if _interaction.command is self.report else True

    @app_commands.command(
        name="report",
        description=app_commands.locale_str(
            "Report a bug or problem", domain="reports"
        ),
    )
    @app_commands.checks.cooldown(1, 60, key=get_cooldown_key)
    async def report(self, interaction: Interaction) -> None:
        await handle_report_button(self.repository, interaction)

    @app_commands.command(
        name="set-report-channel",
        description=app_commands.locale_str(
            "Set the report channel (developers only)", domain="reports"
        ),
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.describe(
        channel=app_commands.locale_str("Report channel", domain="reports")
    )
    @is_owner_app()
    async def set_report_channel(
        self, interaction: Interaction, channel: discord.TextChannel
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = ReportMessages(catalog("reports", context.language))
        await self.repository.set_channel(channel.id)
        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description=messages.destination(mention=channel.mention),
            ephemeral=True,
        )


async def setup(bot: "StupidBot") -> None:
    """Register the reporting cog."""
    await bot.add_cog(ReportCog(bot, bot.report_repository))
