from abc import ABC, abstractmethod


class BaseRepository[T](ABC):
    """Abstract Base Repository using Python 3.12 Generics.

    T: The entity type this repository manages.
    """

    @abstractmethod
    async def get(self, id: str) -> T | None:
        """Retrieve an entity by its ID."""
        pass

    @abstractmethod
    async def get_all(self) -> list[T]:
        """Retrieve all entities."""
        pass

    @abstractmethod
    async def save(self, entity: T) -> None:
        """Save or update an entity."""
        pass

    @abstractmethod
    async def delete(self, id: str) -> None:
        """Delete an entity by its ID."""
        pass
