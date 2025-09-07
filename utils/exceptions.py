from discord.app_commands import CheckFailure


class BlockedUserError(CheckFailure):
    """Custom exception for blocked users."""

    pass
