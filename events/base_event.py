from dataclasses import asdict, dataclass


@dataclass
class BaseEvent:
    """Base class for all domain events."""

    @property
    def event_name(self) -> str:
        return self.__class__.__name__

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
