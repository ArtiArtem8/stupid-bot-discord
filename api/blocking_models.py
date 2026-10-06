from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from discord.utils import utcnow


@dataclass
class BlockHistoryEntry:
    """One administrator action in a user's block or unblock history."""

    admin_id: int
    reason: str | None
    timestamp: datetime


@dataclass
class NameHistoryEntry:
    """One previously observed Discord username and its observation time."""

    username: str
    timestamp: datetime


@dataclass
class BlockedUser:
    """Per-guild blocking state and audit history for one Discord user."""

    user_id: int
    current_username: str
    current_global_name: str | None
    block_history: list[BlockHistoryEntry] = field(
        default_factory=list[BlockHistoryEntry]
    )
    unblock_history: list[BlockHistoryEntry] = field(
        default_factory=list[BlockHistoryEntry]
    )
    name_history: list[NameHistoryEntry] = field(default_factory=list[NameHistoryEntry])
    blocked: bool = False

    @property
    def is_blocked(self) -> bool:
        return self.blocked

    def add_block_entry(self, admin_id: int, reason: str = "") -> None:
        self.block_history.append(
            BlockHistoryEntry(admin_id=admin_id, reason=reason, timestamp=utcnow())
        )
        self.blocked = True

    def add_unblock_entry(self, admin_id: int, reason: str = "") -> None:
        self.unblock_history.append(
            BlockHistoryEntry(admin_id=admin_id, reason=reason, timestamp=utcnow())
        )
        self.blocked = False

    def update_name_history(self, username: str, global_name: str | None) -> bool:
        """Record a changed username and return whether stored names changed."""
        if self.current_username != username or (
            self.current_global_name != global_name and global_name is not None
        ):
            self.name_history.append(
                NameHistoryEntry(
                    username=username,
                    timestamp=utcnow(),
                )
            )
            self.current_username = username
            if global_name is not None:
                self.current_global_name = global_name
            return True
        return False
