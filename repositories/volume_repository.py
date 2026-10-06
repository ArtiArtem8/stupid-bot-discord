from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VolumeData:
    """Persisted playback volume for one guild."""

    guild_id: int
    volume: int


class VolumeStore(Protocol):
    """Music's persisted volume capability, shared by service and healer."""

    async def get_volume(self, guild_id: int) -> int:
        """Return the persisted volume or the configured default."""
        ...

    async def save(self, entity: VolumeData) -> None:
        """Persist the entity's guild volume before returning."""
        ...
