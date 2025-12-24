from abc import ABC, abstractmethod


class BaseRepository[T, K](ABC):
    """Abstract Base Repository using Python 3.12 Generics.

    T: The entity type this repository manages.
    K: The type of the key used to identify the entity.
    """

    @abstractmethod
    async def get(self, key: K) -> T | None:
        """Retrieve an entity by its Key."""
        ...

    @abstractmethod
    async def get_all(self) -> list[T]:
        """Retrieve all entities."""
        ...

    @abstractmethod
    async def save(self, entity: T, key: K | None = None) -> None:
        """Save or update an entity.

        Args:
            entity: The entity to save.
            key: Optional key, useful if the entity does not contain its own ID.

        """
        ...

    @abstractmethod
    async def delete(self, key: K) -> None:
        """Delete an entity by its Key."""
        ...
