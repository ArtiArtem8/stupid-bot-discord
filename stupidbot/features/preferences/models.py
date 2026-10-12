"""Versioned settings and independently reserved reminders."""

from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo

from stupidbot.i18n.policy import Language


class PreferenceScope(StrEnum):
    """Select a global user or a guild, never a guild-specific user override."""

    USER = "user"
    GUILD = "guild"


@dataclass(frozen=True, slots=True)
class Preferences:
    """Nullable overrides; revision zero represents a row that has never existed."""

    language: Language | None = None
    timezone: str | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        """Reject invalid overrides before they reach storage or presentation."""
        if self.timezone is not None:
            _ = ZoneInfo(self.timezone)
        if self.revision < 0:
            raise ValueError("Revision must be nonnegative")


class PreferenceConflictError(Exception):
    """Signal that another editor committed after this panel was opened."""

    def __init__(self, current: Preferences) -> None:
        super().__init__("Preferences changed; reopen the editor")
        self.current = current


@dataclass(frozen=True, slots=True)
class ReminderReservation:
    """A committed at-most-once prompt allowance, retained after uncertain sends."""

    user_id: int
    token: str
