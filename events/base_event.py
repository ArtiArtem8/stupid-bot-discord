from dataclasses import dataclass
from typing import Any


@dataclass
class BaseEvent:
    """Base class for all domain events."""

    @property
    def event_name(self) -> str:
        return self.__class__.__name__

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__
