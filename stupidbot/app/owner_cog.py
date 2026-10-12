"""Administrative commands for user blocking and management.

Provides:
- Blocking/unblocking users from bot access
- Viewing detailed block history
- Listing all blocked users
- Tracking name changes over time

"""

import logging
from typing import TYPE_CHECKING, NoReturn, override

import discord
from discord import app_commands

from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.discord_ui.checks import is_owner_app
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot

logger = logging.getLogger(__name__)


class OwnerCog(BaseCog):
    """Owner-only diagnostics and message management."""

    @override
    def should_bypass_block(self, interaction: discord.Interaction) -> bool:
        """Allow admin commands to bypass block checks."""
        return True

    @app_commands.command(name="error-test", description="Тестирование ошибок")
    @is_owner_app()
    @app_commands.default_permissions(administrator=True)
    async def error(self, _: discord.Interaction) -> NoReturn:
        raise RuntimeError("Test error")

    @app_commands.command(
        name="del", description="Удалить сообщение по ID (только владелец)."
    )
    @is_owner_app()
    @app_commands.describe(message_id="ID сообщения для удаления")
    @app_commands.default_permissions(administrator=True)
    async def delete_message(
        self, interaction: discord.Interaction, message_id: str
    ) -> None:
        channel = interaction.channel
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Невозможно удалить сообщение в этом канале.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            msg = await channel.fetch_message(int(message_id))
            await msg.delete()
        except (discord.NotFound, ValueError):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Сообщение не найдено.",
                ephemeral=True,
            )
            return
        except discord.Forbidden:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Нет прав.",
                ephemeral=True,
            )
            return

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description="Удалено.",
            ephemeral=True,
            delete_after=1.0,
        )


async def setup(bot: "StupidBot") -> None:
    """Register owner commands."""
    await bot.add_cog(OwnerCog(bot))
