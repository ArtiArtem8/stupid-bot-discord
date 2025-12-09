from discord import Interaction, app_commands
from discord.ext import commands


def is_owner_app():
    """Slash-command owner check (uses Bot.is_owner)."""

    async def predicate(interaction: Interaction) -> bool:
        bot = interaction.client
        if not isinstance(bot, commands.Bot):
            return False
        return await bot.is_owner(interaction.user)

    return app_commands.check(predicate)
