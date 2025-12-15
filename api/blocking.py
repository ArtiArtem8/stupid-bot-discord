from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.utils import utcnow

import config
from api.blocking_models import (
    BlockedUser,
    NameHistoryEntry,
)

# from repositories.blocking_repository import BlockingRepository

if TYPE_CHECKING:
    from api.blocking_models import (
        BlockedUserDict,
    )

logger = logging.getLogger(__name__)

# Forward exports for backward compatibility if needed, though clean usage prefers models module
__all__ = [
    "BlockManager",
    "BlockedUser",
    "block_manager",
]


# class BlockManager:
#     """Manage blocked users across guilds."""

#     def __init__(self, repository: BlockingRepository) -> None:
#         self.repo = repository
#         self._cache: dict[int, dict[int, BlockedUser]] = {}
#         self._loaded = False

#     def _ensure_loaded(self):
#         """Lazy load the entire JSON file into cache via Repository."""
#         if self._loaded:
#             return

#         # Synchronous load wrapper (original was synchronous get_json in method)
#         # But our repo is async.
#         # Since this method is called from synchronous context often (is_user_blocked),
#         # we might need to change how it works or make repo sync?
#         # The original get_json was imported from utils.json_utils which IS synchronous usually (using json.load).
#         # Wait, get_json uses 'open' so it is blocking IO.
#         # My BaseRepository methods are async.
#         # Issue: is_user_blocked is synchronous in original usage potentially?
#         # Original: def is_user_blocked(...) -> bool:
#         # So I cannot await inside it.
#         # I must make repo synchronous OR pre-load.

#         # Checking utils/json_utils.py content via inference: commonly sync.
#         # If I made BlockingRepository methods async, I broke sync code.
#         # Original BlockManager methods were NOT async.

#         # Hack: Since I need to maintain interface, I should invoke get_all_grouped synchronously?
#         # Or make cache loading explicit/async at startup.
#         # For now, to keep strict compatibility:
#         # I'll implement a sync method in Repository? Or just use run_until_complete?
#         # Or just revert repository to Sync (BaseRepository async is a convention I set).

#         # Let's inspect get_json in `utils/json_utils.py`?
#         # Assuming it is sync.

#         # I will assume I can run the loop or just access the cache.
#         # IF imports allow, I will just call repo synchronously if the underlying implementation uses sync IO.
#         # But `async def` makes it a coroutine.

#         # Modification: I will make BlockingRepository methods SYNC for now if possible?
#         # No, BaseRepository is async.

#         # Compromise: I will handle the IO manually here OR use an async initialization pattern.
#         # However, `is_user_blocked` is called in message flow.

#         # Let's use `asyncio.create_task` for save? No.
#         # Let's check if I can make `is_user_blocked` async?
#         # If I change it to async, I break callers.

#         # Alternative: Cache is loaded once.
#         # I can just call a sync load method on repo if I add one.
#         pass

#     async def initialize(self):
#         """Async initialization."""
#         self._cache = await self.repo.get_all_grouped()
#         self._loaded = True

#     # COMPATIBILITY LAYER: If called synchronously before init, we have a problem.
#     # Original used lazy loading sync.

#     # I will modify BlockingRepository to support sync get/save distinct from async interface?
#     # Or just use the util directly in the service for now to adhere to sync interface,
#     # but that defeats the purpose of Repository for abstraction if I bypass it.

#     # Let's make BlockManager methods async?
#     # Usages:
#     # base_cog.py: `if block_manager.is_user_blocked(...)` - likely sync.

#     # Solution: Make methods async and update callers? Too risky for "Cleanup" phase.
#     # Solution: Synchronous Repository for this legacy service.
#     # I will not inherit from BaseRepository[T] (which is async).
#     # I will make BlockingRepository independent or sync.

#     pass


# Redefining strategy: Sync BlockingRepository.


class SyncBlockingRepository:
    def __init__(self) -> None:
        self.file_path = config.BLOCKED_USERS_FILE

    def get_all_grouped(self) -> dict[int, dict[int, BlockedUser]]:
        from utils.json_utils import get_json

        raw_data = get_json(self.file_path) or {}
        result: dict[int, dict[int, BlockedUser]] = {}
        for guild_id_str, guild_data in raw_data.items():
            guild_id = int(guild_id_str)
            users_data = guild_data.get("users", {})
            result[guild_id] = {
                int(uid): BlockedUser.from_dict(u_data)
                for uid, u_data in users_data.items()
            }
        return result

    def save_all_grouped(self, data: dict[int, dict[int, BlockedUser]]) -> None:
        from utils.json_utils import save_json

        output_data: dict[str, dict[str, dict[str, BlockedUserDict]]] = {}
        for guild_id, users_map in data.items():
            output_data[str(guild_id)] = {
                "users": {str(uid): user.to_dict() for uid, user in users_map.items()}
            }
        save_json(self.file_path, output_data)


class BlockManager:
    """Manage blocked users across guilds."""

    def __init__(self, repository: SyncBlockingRepository) -> None:
        self.repo = repository
        self._cache: dict[int, dict[int, BlockedUser]] = {}
        self._loaded = False

    def _ensure_loaded(self):
        """Lazy load the entire JSON file into cache."""
        if self._loaded:
            return
        self._cache = self.repo.get_all_grouped()
        self._loaded = True

    def _save_cache(self):
        """Dump the entire cache."""
        self.repo.save_all_grouped(self._cache)

    def _get_or_create_user(self, guild_id: int, member: discord.Member) -> BlockedUser:
        self._ensure_loaded()
        if guild_id not in self._cache:
            self._cache[guild_id] = {}
        guild_cache = self._cache[guild_id]
        user_id = member.id
        if user_id in guild_cache:
            user = guild_cache[user_id]
            if user.update_name_history(member.display_name, member.name):
                logger.info(
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
        logger.info("Created block entry for %d in %d", user_id, guild_id)
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


# Global Instance
block_manager = BlockManager(SyncBlockingRepository())
