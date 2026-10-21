"""Owner-only diagnostics and message management commands."""

from typing import TYPE_CHECKING, NoReturn, override

import discord
from discord import app_commands

from stupidbot.discord_ui.base_cog import GenericBaseCog
from stupidbot.discord_ui.checks import is_owner_app
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.discord_ui.interaction_flow import defer_command
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import error_context, interaction_context

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot


class OwnerCog(GenericBaseCog["StupidBot"]):
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
        name="health",
        description=app_commands.locale_str(
            "Show runtime health (owner only)", domain="app"
        ),
    )
    @is_owner_app()
    @app_commands.default_permissions(administrator=True)
    async def health(self, interaction: discord.Interaction) -> None:
        await defer_command(interaction, ephemeral=True)
        context = await error_context(interaction)
        messages = catalog("app", context.language)
        snapshot = self.bot.health_snapshot()
        embed = discord.Embed(
            title=messages.pgettext("ui", "Runtime health"),
            description=messages.pgettext(
                "ui", "Local state; no external checks are performed."
            ),
            color=discord.Color.orange()
            if snapshot.degraded
            else discord.Color.green(),
        )
        embed.add_field(
            name=messages.pgettext("ui", "Status"),
            value="```\n" + "\n".join(snapshot.lines()) + "\n```",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

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
        await defer_command(interaction, ephemeral=True)
        context = await interaction_context(interaction, private=True)
        messages = catalog("app", context.language)
        channel = interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=messages.pgettext(
                    "ui", "Cannot delete messages in this channel."
                ),
                ephemeral=True,
            )
            return

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
