from abc import ABC, abstractmethod
from typing import Self


class UnitOfWork(ABC):
    """Abstract definition of a Unit of Work for transactional integrity."""

    async def __aenter__(self) -> Self:
        """Enter the runtime context for the unit of work."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Exit the runtime context for the unit of work.

        Args:
            exc_type: The exception type if an exception was raised in the context
            exc_val: The exception instance if an exception was raised in the context
            exc_tb: The traceback if an exception was raised in the context

        """
        if exc_type:
            await self.rollback()
        else:
            await self.commit()

    @abstractmethod
    async def commit(self) -> None:
        """Commit the current transaction."""
        pass

    @abstractmethod
    async def rollback(self) -> None:
        """Rollback the current transaction."""
        pass
