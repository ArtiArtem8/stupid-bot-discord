"""Base class for some cogs in StupidBot.

This provides centralized functionality like blocked user checks and guild
validation, reducing repetition across cogs.
"""

import abc
import logging

import discord
from discord.ext import commands

from .block_manager import block_manager
from .exceptions import BlockedUserError, NoGuildError


class CogABCMeta(commands.CogMeta, abc.ABCMeta):
    """Custom metaclass combining commands.CogMeta and abc.ABCMeta.

    Allows BaseCog to inherit from both commands.Cog and abc.ABC without
    metaclass conflicts.
    """


class BaseCog(abc.ABC, commands.Cog, metaclass=CogABCMeta):
    """Abstract base class for StupidBot cogs.

    Provides:
    - Centralized interaction_check for blocked users (raises BlockedUserError).
    - Helper for requiring guild context in commands.

    Subclasses should set self.logger in __init__ for logging.
    """

    def __init__(self, bot: commands.Bot):
        """Initialize the base cog.

        Args:
            bot: The bot instance.

        """
        super().__init__()
        self.bot = bot
        self.logger = logging.getLogger(self.__class__.__name__)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Centralized check for blocked users.

        Runs before every app command interaction. If the user is blocked in the
        guild, raises BlockedUserError (handled globally in main.py).

        Args:
            interaction: The Discord interaction.

        Returns:
            True if the check passes.

        Raises:
            BlockedUserError: If the user is blocked.

        """
        if interaction.guild and block_manager.is_user_blocked(
            interaction.guild.id, interaction.user.id
        ):
            self.logger.debug(
                "Blocked user %s attempted command in guild %s (%s)",
                interaction.user.id,
                interaction.guild.id,
                interaction.command.name if interaction.command else None,
            )
            raise BlockedUserError()
        return True

    async def _require_guild(self, interaction: discord.Interaction) -> discord.Guild:
        """Ensure the interaction is in a guild and respond if not.

        Args:
            interaction: The Discord interaction.
            logger: Logger for warnings.

        Returns:
            The guild object.

        Raises:
            NoGuildError: If no guild.

        """
        if not (guild := interaction.guild):
            self.logger.debug(
                "Command used outside guild by user %s", interaction.user.id
            )
            raise NoGuildError()
        return guild
