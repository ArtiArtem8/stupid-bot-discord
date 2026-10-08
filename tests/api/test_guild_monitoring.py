"""Role restoration preserves partial progress and fences stale snapshots."""

import asyncio
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import override
from unittest.mock import MagicMock, patch

import discord

from api.guild_monitoring import ServerMonitoringManager
from api.monitor_models import MemberSnapshot
from repositories.monitor_repository import MonitorRepository
from tests.storage import temporary_database


@dataclass(frozen=True, slots=True)
class SnapshotInput:
    user_id: int
    username: str
    roles: list[int]
    left_at: datetime


def make_role(
    role_id: int,
    *,
    default: bool = False,
    managed: bool = False,
    premium: bool = False,
    assignable: bool = True,
) -> SimpleNamespace:
    """Build a role-shaped test double."""
    return SimpleNamespace(
        id=role_id,
        managed=managed,
        is_default=lambda: default,
        is_premium_subscriber=lambda: premium,
        is_assignable=lambda: assignable,
    )


class TestGuildMonitoring(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, database = await temporary_database(self)
        self.repository = MonitorRepository(database)
        self.manager = ServerMonitoringManager(self.repository)

    async def _store_snapshot(
        self, snapshot: SnapshotInput, ttl: int | None = None
    ) -> MemberSnapshot:
        await self.repository.set_enabled(10, True, ttl)
        await self.repository.save(
            10, snapshot.user_id, snapshot.username, snapshot.roles, snapshot.left_at
        )
        stored = await self.manager.get_snapshot(10, snapshot.user_id)
        if stored is None:
            self.fail("Missing snapshot")
        return stored

    async def test_enable_without_ttl_replaces_old_finite_ttl(self) -> None:
        await self.manager.set_enabled(10, True, 3)
        await self.manager.set_enabled(10, False)
        await self.manager.set_enabled(10, True)

        self.assertTrue(await self.manager.is_enabled(10))
        self.assertIsNone(await self.manager.get_ttl(10))

    async def test_empty_leave_replaces_previously_retained_roles(self) -> None:
        previous = await self._store_snapshot(
            SnapshotInput(20, "member", [7], datetime.now(UTC))
        )
        member = MagicMock(spec=discord.Member)
        member.bot = False
        member.guild.id = 10
        member.id = 20
        member.roles = [make_role(10, default=True)]
        self.assertEqual(await self.manager.save_snapshot(member), 0)
        current = await self.manager.get_snapshot(10, 20)
        if current is None:
            self.fail("Missing replacement snapshot")
        self.assertNotEqual(current, previous)
        self.assertEqual(current.roles, [])

    async def test_disable_preserves_ttl_for_truthful_status(self) -> None:
        await self.manager.set_enabled(10, True, 3)

        await self.manager.set_enabled(10, False)

        self.assertFalse(await self.manager.is_enabled(10))
        self.assertEqual(await self.manager.get_ttl(10), 3)

    async def test_restore_snapshot_validates_roles_and_deletes_exact_snapshot(
        self,
    ) -> None:
        member = self._restore_member()
        snapshot = SnapshotInput(
            user_id=5,
            username="u",
            roles=[10, 20],
            left_at=datetime.now(UTC),
        )
        snapshot = await self._store_snapshot(snapshot)
        role = make_role(10)

        def get_role(role_id: int) -> SimpleNamespace | None:
            return role if role_id == 10 else None

        member.guild.get_role.side_effect = get_role
        restored, skipped = await self.manager.restore_snapshot(member)

        self.assertEqual(len(restored), 1)
        self.assertEqual(skipped, [20])
        member.add_roles.assert_awaited_once_with(
            role, reason="Автовосстановление ролей"
        )
        self.assertIsNone(await self.manager.get_snapshot(10, 5))

    def _restore_member(self) -> MagicMock:
        member = MagicMock(spec=discord.Member, id=5, roles=[])
        member.guild = MagicMock(spec=discord.Guild, id=10)
        member.guild.me.guild_permissions.manage_roles = True
        return member

    async def test_restore_enforces_ttl_including_boundary_and_infinite_retention(
        self,
    ) -> None:
        now = datetime(2026, 10, 1, tzinfo=UTC)
        for age, ttl, expired in ((30, 1, True), (1, 1, False), (30, None, False)):
            with self.subTest(age=age, ttl=ttl):
                member = self._restore_member()
                role = make_role(7)
                member.guild.get_role.return_value = role
                await self._store_snapshot(
                    SnapshotInput(5, "u", [7], now - timedelta(days=age)), ttl
                )
                with patch("api.guild_monitoring.utcnow", return_value=now):
                    restored, skipped = await self.manager.restore_snapshot(member)
                self.assertEqual(restored, [] if expired else [role])
                self.assertEqual(skipped, [])
                self.assertEqual(member.add_roles.await_count, 0 if expired else 1)
                self.assertIsNone(await self.manager.get_snapshot(10, 5))

    async def test_partial_failure_preserves_success_and_retries_only_missing_roles(
        self,
    ) -> None:
        member = self._restore_member()
        roles = {7: make_role(7), 9: make_role(9)}
        member.guild.get_role.side_effect = roles.get
        snapshot = SnapshotInput(5, "u", [7, 9], datetime.now(UTC))
        snapshot = await self._store_snapshot(snapshot)
        error = discord.HTTPException(
            MagicMock(status=503, reason="unavailable"), "retry"
        )

        async def add(role: discord.Role, **_kwargs: object) -> None:
            if role.id == 9:
                raise error
            member.roles.append(role)

        member.add_roles.side_effect = add
        with self.assertLogs("api.guild_monitoring", level="WARNING"):
            restored, skipped = await self.manager.restore_snapshot(member)
        self.assertEqual(restored, [roles[7]])
        self.assertEqual(skipped, [9])
        self.assertEqual(await self.manager.get_snapshot(10, 5), snapshot)
        member.add_roles.reset_mock(side_effect=True)
        restored, skipped = await self.manager.restore_snapshot(member)
        self.assertEqual(restored, [roles[9]])
        self.assertEqual(skipped, [])
        member.add_roles.assert_awaited_once_with(
            roles[9], reason="Автовосстановление ролей"
        )
        self.assertIsNone(await self.manager.get_snapshot(10, 5))

    async def test_unassignable_roles_and_missing_permission_retain_snapshot(
        self,
    ) -> None:
        for assignable, permission in ((False, True), (True, False)):
            with self.subTest(assignable=assignable, permission=permission):
                member = self._restore_member()
                member.guild.get_role.return_value = make_role(7, assignable=assignable)
                member.guild.me.guild_permissions.manage_roles = permission
                await self._store_snapshot(
                    SnapshotInput(5, "u", [7], datetime.now(UTC))
                )
                self.assertEqual(await self.manager.restore_snapshot(member), ([], [7]))
                member.add_roles.assert_not_awaited()
                self.assertIsNotNone(await self.manager.get_snapshot(10, 5))

    async def test_restore_cannot_delete_replacement_even_at_identical_timestamp(
        self,
    ) -> None:
        moment = datetime.now(UTC)
        old = await self._store_snapshot(SnapshotInput(5, "u", [7], moment))
        member = self._restore_member()
        member.guild.get_role.return_value = make_role(7)
        entered, release = asyncio.Event(), asyncio.Event()

        async def add(*_roles: object, **_kwargs: object) -> None:
            entered.set()
            await release.wait()

        member.add_roles.side_effect = add
        restore = asyncio.create_task(self.manager.restore_snapshot(member))
        await entered.wait()
        await self.repository.save(10, 5, "new", [8], moment)
        release.set()
        await restore
        current = await self.manager.get_snapshot(10, 5)
        if current is None:
            self.fail("Restore deleted a replacement")
        self.assertNotEqual(current.snapshot_id, old.snapshot_id)
        self.assertEqual(current.roles, [8])
        self.assertFalse(await self.repository.delete(10, old))

    async def test_cleanup_uses_current_ttl_and_keeps_recent_snapshot(self) -> None:
        now = datetime.now(UTC)
        await self._store_snapshot(
            SnapshotInput(5, "old", [7], now - timedelta(days=10)), 3
        )
        await self.repository.save(10, 6, "new", [8], now - timedelta(days=1))
        self.assertEqual(await self.repository.cleanup_expired(10, now), 1)
        self.assertEqual(
            [row.user_id for row in await self.repository.snapshots(10)], [6]
        )
