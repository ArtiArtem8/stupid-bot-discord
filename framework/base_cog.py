"""Base class for some cogs in StupidBot.

This provides centralized functionality like blocked user checks and guild
validation, reducing repetition across cogs.
"""

import abc
import logging
from typing import override

import discord
from discord.ext import commands
from discord.utils import maybe_coroutine

from api.blocking import block_manager
from framework.exceptions import BlockedUserError, NoGuildError

logger = logging.getLogger(__name__)


class CogABCMeta(commands.CogMeta, abc.ABCMeta):
    """Custom metaclass combining commands.CogMeta and abc.ABCMeta.

    Allows BaseCog to inherit from both commands.Cog and abc.ABC without
    metaclass conflicts.
    """


class GenericBaseCog[BotT: commands.Bot](abc.ABC, commands.Cog, metaclass=CogABCMeta):
    """Abstract base class for StupidBot cogs.

    Provides:
    - Centralized interaction_check for blocked users (raises BlockedUserError).
    - Helper for requiring guild context in commands.

    """

    def __init__(self, bot: BotT):
        """Initialize the base cog.

        Args:
            bot: The bot instance.

        """
        super().__init__()
        self.bot = bot
        self._cog = self.__class__.__name__

    def should_bypass_block(self, interaction: discord.Interaction) -> bool:
        """Return True to skip the blocked-user check for this interaction.

        This function **can** be a coroutine
        """
        return False

    @override
    async def interaction_check(self, interaction: discord.Interaction) -> bool:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Centralized check for blocked users.

        Runs before every app command interaction. If the user is blocked in the
        guild, raises :py:exc:`BlockedUserError` (handled globally in main.py).

        Logs the command attempt before checking for blocked users.

        Subclasses can override should_bypass_block to bypass block checks.

        Args:
            interaction: The Discord interaction.

        Returns:
            `True` if the check passes.

        Raises:
            BlockedUserError: If the user is blocked.

        """
        self._log_command(interaction)

        if await maybe_coroutine(self.should_bypass_block, interaction):
            return True
        if interaction.guild and await block_manager.is_user_blocked(
            interaction.guild.id, interaction.user.id
        ):
            logger.debug(
                "[%s] Blocked user %s attempted command in guild %s (%s)",
                self._cog,
                interaction.user.id,
                interaction.guild.id,
                interaction.command.name if interaction.command else None,
            )
            raise BlockedUserError()
        return True

    async def _require_guild(self, interaction: discord.Interaction) -> discord.Guild:
        """Ensure the interaction is in a guild and raise :py:exc:`NoGuildError` if not.

        Args:
            interaction: The Discord interaction.

        Returns:
            The guild object.

        Raises:
            NoGuildError: If no guild.

        """
        if not (guild := interaction.guild):
            logger.debug(
                "[%s] Command used outside guild by user %s",
                self._cog,
                interaction.user.id,
            )
            raise NoGuildError()
        return guild

    def _log_command(self, interaction: discord.Interaction) -> None:
        """Log command invocation details.

        Currently logs the command name, user ID, and guild/channel context

        Log level: `INFO`

        Args:
            interaction: The interaction context

        """
        user = interaction.user
        user_display = user.global_name or user.name

        command_name = interaction.command.name if interaction.command else "unknown"
        if interaction.guild:
            guild_name = interaction.guild.name
            channel_name = (
                interaction.channel.name
                if not isinstance(interaction.channel, discord.DMChannel)
                and interaction.channel
                else "Unknown"
            )
            context = f"guild {guild_name} (ID: {interaction.guild.id}), {channel_name}"
        else:
            context = "DM"
        logger.info(
            "[%s] Command '%s' invoked by %s (ID: %s) in %s",
            self._cog,
            command_name,
            user_display,
            user.id,
            context,
        )


class BaseCog(GenericBaseCog[commands.Bot]):
    """Default BaseCog locked to standard commands.Bot."""

    pass
