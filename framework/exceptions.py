from discord.app_commands import CheckFailure


class BlockedUserError(CheckFailure):
    """Raised when a blocked user attempts to use a command."""
