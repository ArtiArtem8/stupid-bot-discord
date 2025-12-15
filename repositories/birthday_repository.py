from __future__ import annotations

import config
from api.birthday_models import BirthdayGuildConfig, BirthdayGuildDict
from utils.json_utils import get_json, save_json


class SyncBirthdayRepository:
    """Repository for managing birthday data.
    Maintains an in-memory cache validated against file modification time.
    """

    def __init__(self) -> None:
        self.file_path = config.BIRTHDAY_FILE
        self._cache: dict[int, BirthdayGuildConfig] = {}
        self._file_mtime_ns: int | None = None

    def _current_file_mtime_ns(self) -> int | None:
        try:
            return self.file_path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _refresh_if_file_changed(self) -> None:
        mtime = self._current_file_mtime_ns()
        if mtime != self._file_mtime_ns:
            self._cache.clear()
            self._file_mtime_ns = mtime

    def get_guild_config(self, guild_id: int) -> BirthdayGuildConfig | None:
        """Get guild config from cache or file."""
        self._refresh_if_file_changed()

        if guild_id in self._cache:
            return self._cache[guild_id]

        # Load from file if not in cache (but cache invalidation happens above,
        # so this implies it wasn't there or cache was cleared)

        # Optimization: We should load ALL from file if cache is cleared?
        # Or load on demand?
        # get_json loads the WHOLE file.
        # So we should probably load everything into cache if cache is empty?
        # But if cache was just cleared, we need to reload.

        # Let's inspect get_json again. It returns the dict.
        # If I call get_json() inside here, it's expensive if done per guild if I don't cache the result of get_json.
        # But wait, get_json reads the file.

        # Logic:
        # If cache is empty (due to invalidation), we reload the whole file map?
        # Implementation:

        raw_data = get_json(self.file_path)
        if not isinstance(raw_data, dict):
            return None

        # Populate cache completely to avoid re-reading for other guilds?
        # Yes, good practice for single-file DB.

        for gid_str, g_data in raw_data.items():
            gid = int(gid_str)
            self._cache[gid] = BirthdayGuildConfig.from_dict(gid, g_data)  # type: ignore

        return self._cache.get(guild_id)

    def save_guild_config(self, guild_config: BirthdayGuildConfig) -> None:
        """Update cache and save to file."""
        self._cache[guild_config.guild_id] = guild_config

        # We need to save ALL data.
        # So we take our cache (assuming it covers everything loaded)
        # BUT we might have partial cache if we only loaded on demand?
        # Above I changed logic to load ALL into cache on miss. So cache is authoritative for the file state.
        # IF the file changed externally, _refresh_if_file_changed invalidates cache, so we reload.

        # Potential race condition: if we invalidate, then load all, then modify one, then save.

        raw_data: dict[str, BirthdayGuildDict] = {}
        # Ensure we have all known data.
        # Use simple get_json to make sure we don't overwrite others if cache was partial?
        # But if we loaded all on miss, cache is full representation.

        for gid, conf in self._cache.items():
            raw_data[str(gid)] = conf.to_dict()

        save_json(self.file_path, raw_data)
        self._file_mtime_ns = self._current_file_mtime_ns()

    def delete_guild_config(self, guild_id: int) -> bool:
        self._refresh_if_file_changed()

        found = False
        if guild_id in self._cache:
            del self._cache[guild_id]
            found = True

        # Even if not in cache (maybe not loaded?), we might need to check file if we didn't force load?
        # But get_guild_config forces load.
        # Let's ensure load.
        if not self._cache:
            self.get_guild_config(guild_id)  # side effect: load
            if guild_id in self._cache:
                del self._cache[guild_id]
                found = True

        if found:
            # Save new state
            raw_data: dict[str, BirthdayGuildDict] = {}
            for gid, conf in self._cache.items():
                raw_data[str(gid)] = conf.to_dict()
            save_json(self.file_path, raw_data)
            self._file_mtime_ns = self._current_file_mtime_ns()
            return True

        return False

    def get_all_guild_ids(self) -> list[int]:
        self._refresh_if_file_changed()
        # Ensure load
        if not self._cache and self.file_path.exists():
            self.get_guild_config(0)  # Logic hack to trigger load

        return list(self._cache.keys())

    def clear_cache(self) -> None:
        self._cache.clear()
