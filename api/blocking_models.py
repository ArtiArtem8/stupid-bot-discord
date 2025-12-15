from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Self, TypedDict

from discord.utils import utcnow


def datetime_now_isoformat() -> str:
    """Return current time in isoformat."""
    return utcnow().isoformat()


class BlockHistoryEntryDict(TypedDict):
    admin_id: str
    reason: str
    timestamp: str


class NameHistoryEntryDict(TypedDict):
    username: str
    timestamp: str


class BlockedUserDict(TypedDict):
    user_id: str
    current_username: str
    current_global_name: str | None
    blocked: bool
    block_history: list[BlockHistoryEntryDict]
    unblock_history: list[BlockHistoryEntryDict]
    name_history: list[NameHistoryEntryDict]


@dataclass
class BlockHistoryEntry:
    admin_id: int
    reason: str | None
    timestamp: datetime

    def to_dict(self) -> BlockHistoryEntryDict:
        return {
            "admin_id": str(self.admin_id),
            "reason": self.reason or "",
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: BlockHistoryEntryDict) -> Self:
        return cls(
            admin_id=int(data["admin_id"]),
            reason=data["reason"],
            timestamp=datetime.fromisoformat(
                data.get("timestamp", datetime_now_isoformat())
            ),
        )


@dataclass
class NameHistoryEntry:
    username: str
    timestamp: datetime

    def to_dict(self) -> NameHistoryEntryDict:
        return {
            "username": self.username,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: NameHistoryEntryDict) -> Self:
        return cls(
            username=data.get("username", ""),
            timestamp=datetime.fromisoformat(
                data.get("timestamp", datetime_now_isoformat())
            ),
        )


@dataclass
class BlockedUser:
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
        """Update name history if different from current. Returns True if updated."""
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

    def to_dict(self) -> BlockedUserDict:
        return {
            "user_id": str(self.user_id),
            "current_username": self.current_username,
            "current_global_name": self.current_global_name,
            "blocked": self.blocked,
            "block_history": [e.to_dict() for e in self.block_history],
            "unblock_history": [e.to_dict() for e in self.unblock_history],
            "name_history": [e.to_dict() for e in self.name_history],
        }

    @classmethod
    def from_dict(cls, data: BlockedUserDict) -> Self:
        return cls(
            user_id=int(data.get("user_id", 0)),
            current_username=data.get("current_username", ""),
            current_global_name=data.get("current_global_name"),
            blocked=data.get("blocked", False),
            block_history=[
                BlockHistoryEntry.from_dict(e) for e in data.get("block_history", [])
            ],
            unblock_history=[
                BlockHistoryEntry.from_dict(e) for e in data.get("unblock_history", [])
            ],
            name_history=[
                NameHistoryEntry.from_dict(e) for e in data.get("name_history", [])
            ],
        )
