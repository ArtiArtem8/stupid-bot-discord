"""Detached role-restoration state; snapshot identity fences stale deletion."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MonitorSettings:
    enabled: bool = False
    ttl_days: int | None = None


@dataclass(frozen=True, slots=True)
class MemberSnapshot:
    user_id: int
    username: str
    roles: list[int]
    left_at: datetime
    snapshot_id: int
    version: int
