import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Self, TypedDict

import discord
from discord.utils import utcnow

import config
from utils.json_utils import get_json, save_json

LOGGER = logging.getLogger("BlockManager")


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
    block_history: List[BlockHistoryEntryDict]
    unblock_history: List[BlockHistoryEntryDict]
    name_history: List[NameHistoryEntryDict]


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


class BlockManager:
    """Manage blocked users across guilds."""

    def __init__(self) -> None:
        self._cache: dict[int, dict[int, BlockedUser]] = {}
        self._loaded = False

    def _ensure_loaded(self):
        """Lazy load the entire JSON file into cache."""
        if self._loaded:
            return

        raw_data = get_json(config.BLOCKED_USERS_FILE) or {}
        self._cache = {}
        for guild_id_str, guild_data in raw_data.items():
            guild_id = int(guild_id_str)
            users_data = guild_data.get("users", {})
            self._cache[guild_id] = {
                int(uid): BlockedUser.from_dict(u_data)
                for uid, u_data in users_data.items()
            }
        self._loaded = True

    def _save_cache(self):
        """Dump the entire cache to JSON."""
        output_data: dict[str, dict[str, dict[str, BlockedUserDict]]] = {}
        for guild_id, users_map in self._cache.items():
            output_data[str(guild_id)] = {
                "users": {str(uid): user.to_dict() for uid, user in users_map.items()}
            }

        save_json(config.BLOCKED_USERS_FILE, output_data)

    def _get_or_create_user(self, guild_id: int, member: discord.Member) -> BlockedUser:
        """Get existing user entry or create a new one with name tracking.

        Automatically updates name history if the user exists.
        """
        self._ensure_loaded()
        if guild_id not in self._cache:
            self._cache[guild_id] = {}
        guild_cache = self._cache[guild_id]
        user_id = member.id
        if user_id in guild_cache:
            user = guild_cache[user_id]
            if user.update_name_history(member.display_name, member.name):
                LOGGER.info(
                    "Updated name history for user %d in guild %d. Name: %s",
                    user_id,
                    guild_id,
                    member.display_name,
                )
            return user

        new_user = BlockedUser(
            user_id=user_id,
            current_username=member.display_name,
            current_global_name=member.name,
        )
        new_user.name_history.append(
            NameHistoryEntry(
                username=member.display_name,
                timestamp=utcnow(),
            )
        )
        guild_cache[user_id] = new_user
        LOGGER.info("Created block entry for %d in %d", user_id, guild_id)
        return new_user

    def is_user_blocked(self, guild_id: int, user_id: int) -> bool:
        """Check if a user is currently blocked in the guild."""
        self._ensure_loaded()
        user = self._cache.get(guild_id, {}).get(user_id)
        return user.is_blocked if user else False

    def get_guild_users(self, guild_id: int) -> list[BlockedUser]:
        """Get list of all tracked users for a guild."""
        self._ensure_loaded()
        return list(self._cache.get(guild_id, {}).values())

    def get_user(self, guild_id: int, user_id: int) -> BlockedUser | None:
        """Get a specific user (Read-only)."""
        self._ensure_loaded()
        return self._cache.get(guild_id, {}).get(user_id)

    def block_user(
        self, guild_id: int, target: discord.Member, admin_id: int, reason: str
    ) -> BlockedUser:
        """Block a user and save to disk."""
        user = self._get_or_create_user(guild_id, target)

        if user.is_blocked:
            return user

        user.add_block_entry(admin_id, reason)

        self._save_cache()
        return user

    def unblock_user(
        self, guild_id: int, target: discord.Member, admin_id: int, reason: str
    ) -> BlockedUser:
        """Unblock a user and save to disk."""
        user = self._get_or_create_user(guild_id, target)

        if not user.is_blocked:
            return user

        user.add_unblock_entry(admin_id, reason)

        self._save_cache()
        return user

    def reload(self):
        """Force reload from disk (useful if file edited manually)."""
        self._loaded = False
        self._cache.clear()
        self._ensure_loaded()


block_manager = BlockManager()
