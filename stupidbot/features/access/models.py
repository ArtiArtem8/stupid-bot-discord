from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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
    """Detached per-guild state; BlockingRepository owns mutations and audit."""

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
