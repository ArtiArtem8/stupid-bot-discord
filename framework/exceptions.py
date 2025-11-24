from discord.app_commands import CheckFailure
from discord.ext import commands


class BlockedUserError(CheckFailure):
    """Raised when a blocked user attempts to use a command."""

    pass


class NoGuildError(commands.CommandError):
    """Raised when a command is invoked outside of a guild."""

    pass


class MusicError(Exception):
    """Base exception for Music API errors."""

    pass


class NodeNotConnectedError(MusicError):
    """Raised when Lavalink node is not connected."""

    pass


class PlayerNotFoundError(MusicError):
    """Raised when player is not found for a guild."""

    pass
