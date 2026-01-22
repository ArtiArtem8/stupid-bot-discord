"""Tests for guild monitoring snapshots and cleanup.
Covers role validation, TTL cleanup, and snapshot persistence flows.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.guild_monitoring import MemberSnapshot, ServerMonitoringManager


def mk_role(
    rid: int,
    *,
    default: bool = False,
    managed: bool = False,
    premium: bool = False,
):
    """Create a mock role object with the specified properties."""
    role = SimpleNamespace(
        id=rid,
        managed=managed,
        is_default=lambda: default,
        is_premium_subscriber=lambda: premium,
    )
    return role


class TestGuildMonitoring(unittest.IsolatedAsyncioTestCase):
    async def test_save_snapshot_disabled_returns_zero(self) -> None:
        with TemporaryDirectory() as td:
            mgr = ServerMonitoringManager(Path(td))

            with (
                patch(
                    "api.guild_monitoring.get_json",
                    return_value={"enabled": False, "ttl_days": None, "members": {}},
                ),
                patch("api.guild_monitoring.save_json") as save_mock,
            ):
                member = SimpleNamespace(
                    bot=False, id=1, guild=SimpleNamespace(id=10), roles=[]
                )
                count = mgr.save_snapshot(member)

            self.assertEqual(count, 0)
            save_mock.assert_not_called()

    async def test_cleanup_expired_removes_old(self) -> None:
        fixed = datetime(2025, 1, 10, tzinfo=timezone.utc)
        old = (fixed - timedelta(days=10)).isoformat()
        new = (fixed - timedelta(days=1)).isoformat()

        with TemporaryDirectory() as td:
            mgr = ServerMonitoringManager(Path(td))

            data = {
                "enabled": True,
                "ttl_days": 3,
                "members": {
                    "1": {"roles": [1], "username": "u", "left_at": old},
                    "2": {"roles": [2], "username": "v", "left_at": new},
                },
            }

            with (
                patch("api.guild_monitoring.get_json", return_value=data),
                patch("api.guild_monitoring.save_json") as save_mock,
                patch("api.guild_monitoring.utcnow", return_value=fixed),
            ):
                removed = mgr.cleanup_expired(10)

            self.assertEqual(removed, 1)
            save_mock.assert_called_once()

    async def test_restore_snapshot_validates_roles_and_calls_add_roles(self) -> None:
        with TemporaryDirectory() as td:
            mgr = ServerMonitoringManager(Path(td))

            def get_role(_: int) -> object:
                return object()

            def get_member(_: int) -> object:
                return object()

            guild = SimpleNamespace(
                get_role=get_role,
                get_member=get_member,
                id=SimpleNamespace(id=1),
            )
            member = SimpleNamespace(guild=guild, id=5, add_roles=AsyncMock())

            snapshot = MemberSnapshot(
                user_id=5,
                username="u",
                roles=[10, 20],
                left_at=datetime.now(),
            )

            mgr.get_snapshot = lambda gid, uid: snapshot  # pyright: ignore[reportAttributeAccessIssue]
            mgr.delete_snapshot = lambda gid, uid: True  # pyright: ignore[reportAttributeAccessIssue]
            mgr._validate_role = AsyncMock(side_effect=[object(), None])

            restored, skipped = await mgr.restore_snapshot(member)

            self.assertEqual(len(restored), 1)
            self.assertEqual(skipped, [20])
            member.add_roles.assert_awaited_once()
