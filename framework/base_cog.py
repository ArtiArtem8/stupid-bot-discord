"""Shared interaction checks and guild validation for Discord cogs."""

import logging
from typing import override

import discord
from discord.ext import commands
from discord.utils import maybe_coroutine

from api.blocking import block_manager
from framework.exceptions import BlockedUserError, NoGuildError

logger = logging.getLogger(__name__)


class GenericBaseCog[BotT: commands.Bot](commands.Cog):
    """Own blocked-user enforcement shared by application-command cogs."""

    def __init__(self, bot: BotT) -> None:
        super().__init__()
        self.bot = bot
        self._cog = self.__class__.__name__

    def should_bypass_block(self, interaction: discord.Interaction) -> bool:  # pyright: ignore[reportUnusedParameter]
        """Return whether to skip blocking; overrides may be sync or async."""
        return False

    @override
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Log and reject blocked interactions before command dispatch.

        Subclasses can override :meth:`should_bypass_block` for commands that
        blocked users must still be able to invoke.

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
        """Return the interaction guild or reject direct-message use.

        Raises:
            NoGuildError: If the interaction has no guild.
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
