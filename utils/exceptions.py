from discord.app_commands import CheckFailure
from discord.ext import commands


class BlockedUserError(CheckFailure):
    """Raised when a blocked user attempts to use a command."""

    pass


class NoGuildError(commands.CommandError):
    """Raised when a command is invoked outside of a guild."""

    pass
