from typing import Literal

from discord.app_commands import AppCommandError, CheckFailure


class CommandFeedbackError(CheckFailure):
    """Delegate an expected refusal with safe, localized text to global feedback."""

    def __init__(
        self, description: str, *, severity: Literal["info", "warning"] = "warning"
    ) -> None:
        super().__init__(description)
        self.description = description
        self.severity = severity


class BlockedUserError(CheckFailure):
    """Raised when a blocked user attempts to use a command."""


class AccessCheckUnavailableError(CheckFailure):
    """A modal's access check exceeded its initial response budget."""


class InteractionAcknowledgementError(AppCommandError):
    """Discord did not accept acknowledgement; the action must not start."""
