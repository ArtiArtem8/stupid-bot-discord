"""Shared interaction checks and guild validation for Discord cogs."""

import asyncio
import logging
from typing import override

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import maybe_coroutine

from stupidbot.discord_ui.access import check_command_access
from stupidbot.discord_ui.exceptions import AccessCheckUnavailableError
from stupidbot.discord_ui.interaction_flow import defer_command

logger = logging.getLogger(__name__)
MODAL_ACCESS_TIMEOUT = 1.0


class GenericBaseCog[BotT: commands.Bot](commands.Cog):
    """Own blocked-user enforcement shared by application-command cogs."""

    def __init__(self, bot: BotT) -> None:
        super().__init__()
        self.bot = bot
        self._cog = self.__class__.__name__

    def should_bypass_block(self, interaction: discord.Interaction) -> bool:  # pyright: ignore[reportUnusedParameter]
        """Return whether to skip blocking; overrides may be sync or async."""
        return False

    def command_response_ephemeral(
        self, _interaction: discord.Interaction
    ) -> bool | None:
        """Select the initial audience; ``None`` reserves the response for a modal."""
        return True

    @override
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Acknowledge before checking access; modal opening has a bounded check.

        Subclasses can override :meth:`should_bypass_block` for commands that
        blocked users must still be able to invoke.

        Raises:
            BlockedUserError: If the user is blocked.
        """
        self._log_command(interaction)
        private = self.command_response_ephemeral(interaction)
        if private is not None:
            await defer_command(interaction, ephemeral=private)
            return await self._check_access(interaction)
        try:
            async with asyncio.timeout(MODAL_ACCESS_TIMEOUT):
                return await self._check_access(interaction)
        except TimeoutError as error:
            raise AccessCheckUnavailableError() from error

    async def _check_access(self, interaction: discord.Interaction) -> bool:
        try:
            if await maybe_coroutine(self.should_bypass_block, interaction):
                return True
            return await check_command_access(interaction)
        except app_commands.AppCommandError:
            raise
        except Exception as error:
            # discord.py wraps callback exceptions, but not exceptions from checks.
            command = interaction.command
            if command is None:
                raise
            raise app_commands.CommandInvokeError(command, error) from error

    async def _require_guild(self, interaction: discord.Interaction) -> discord.Guild:
        """Return the interaction guild or reject direct-message use.

        Raises:
            app_commands.NoPrivateMessage: If the interaction has no guild.
        """
        if not (guild := interaction.guild):
            logger.debug(
                "[%s] Command used outside guild by user %s",
                self._cog,
                interaction.user.id,
            )
            raise app_commands.NoPrivateMessage()
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
