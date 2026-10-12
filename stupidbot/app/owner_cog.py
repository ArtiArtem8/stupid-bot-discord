"""Owner-only diagnostics and message management commands."""

from typing import TYPE_CHECKING, NoReturn, override

import discord
from discord import app_commands

from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.discord_ui.checks import is_owner_app
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import interaction_context

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot


class OwnerCog(BaseCog):
    """Owner-only diagnostics and message management."""

    @override
    def should_bypass_block(self, interaction: discord.Interaction) -> bool:
        """Allow admin commands to bypass block checks."""
        return True

    @app_commands.command(
        name="error-test",
        description=app_commands.locale_str("Test error handling", domain="app"),
    )
    @is_owner_app()
    @app_commands.default_permissions(administrator=True)
    async def error(self, _: discord.Interaction) -> NoReturn:
        raise RuntimeError("Test error")

    @app_commands.command(
        name="del",
        description=app_commands.locale_str(
            "Delete a message by ID (owner only)", domain="app"
        ),
    )
    @is_owner_app()
    @app_commands.describe(
        message_id=app_commands.locale_str("ID of the message to delete", domain="app")
    )
    @app_commands.default_permissions(administrator=True)
    async def delete_message(
        self, interaction: discord.Interaction, message_id: str
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = catalog("app", context.language)
        channel = interaction.channel
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.pgettext(
                    "ui", "Cannot delete messages in this channel."
                ),
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
                description=messages.pgettext("ui", "Message not found."),
                ephemeral=True,
            )
            return
        except discord.Forbidden:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.pgettext("ui", "Insufficient permissions."),
                ephemeral=True,
            )
            return

        await FeedbackUI.send(
            interaction,
            feedback_type=FeedbackType.SUCCESS,
            description=messages.pgettext("ui", "Deleted."),
            ephemeral=True,
            delete_after=1.0,
        )


async def setup(bot: "StupidBot") -> None:
    """Register owner commands."""
    await bot.add_cog(OwnerCog(bot))
