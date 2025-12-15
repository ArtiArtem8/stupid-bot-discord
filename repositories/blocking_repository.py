from __future__ import annotations

import config
from api.blocking_models import BlockedUser, BlockedUserDict
from repositories.base_repository import BaseRepository
from utils.json_utils import get_json, save_json


class BlockingRepository(BaseRepository[BlockedUser]):
    """Repository for managing blocked users.
    Handles persistence to a single JSON file containing all guilds/users.
    """

    def __init__(self) -> None:
        self.file_path = config.BLOCKED_USERS_FILE

    async def get_all_grouped(self) -> dict[int, dict[int, BlockedUser]]:
        """Load all data.
        Returns dict[guild_id, dict[user_id, BlockedUser]].
        """
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

    async def save_all_grouped(self, data: dict[int, dict[int, BlockedUser]]) -> None:
        """Save all data."""
        output_data: dict[str, dict[str, dict[str, BlockedUserDict]]] = {}
        for guild_id, users_map in data.items():
            output_data[str(guild_id)] = {
                "users": {str(uid): user.to_dict() for uid, user in users_map.items()}
            }
        save_json(self.file_path, output_data)

    # BaseRepository methods (hard to map 1:1 if we store grouped by guild)
    # But we can implement get/save for a single user if we load all first.
    # Service layer will likely use get_all_grouped/save_all_grouped for caching efficiency.

    async def get(self, id: str) -> BlockedUser | None:
        # Not efficiently implementable without guild context or full scan
        return None

    async def get_all(self) -> list[BlockedUser]:
        grouped = await self.get_all_grouped()
        all_users: list[BlockedUser] = []
        for guild_map in grouped.values():
            all_users.extend(guild_map.values())
        return all_users

    async def save(self, entity: BlockedUser) -> None:
        # Requires guild_id to save contextually.
        # BaseRepository might not fit perfectly here for 'Contextual' entities.
        # we will rely on save_all_grouped called by Service.
        pass

    async def delete(self, id: str) -> None:
        pass
