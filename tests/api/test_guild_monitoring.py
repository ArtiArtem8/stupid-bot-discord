"""Behavioral tests for role snapshot persistence and restoration."""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast, override
from unittest.mock import AsyncMock, patch

import discord

from api.guild_monitoring import MemberSnapshot, ServerMonitoringManager
from utils.json_types import JsonObject, is_json_object


def make_role(
    role_id: int,
    *,
    default: bool = False,
    managed: bool = False,
    premium: bool = False,
) -> SimpleNamespace:
    """Build a role-shaped test double."""
    return SimpleNamespace(
        id=role_id,
        managed=managed,
        is_default=lambda: default,
        is_premium_subscriber=lambda: premium,
    )


class TestGuildMonitoring(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.manager = ServerMonitoringManager(self.root)
        self.backup_patch = patch("utils.json_utils.BACKUP_DIR", self.root / "backups")
        self.backup_patch.start()

    @override
    def tearDown(self) -> None:
        self.backup_patch.stop()
        self.temp_dir.cleanup()

    def _write_guild(self, guild_id: int, data: object) -> None:
        path = self.root / f"guild_{guild_id}.json"
        path.write_text(json.dumps(data), encoding="utf-8")

    def _read_guild(self, guild_id: int) -> JsonObject:
        path = self.root / f"guild_{guild_id}.json"
        raw: object = json.loads(path.read_text(encoding="utf-8"))

        if not is_json_object(raw):
            self.fail("expected guild data to be a JSON object")

        return raw

    def _read_members(self, guild_id: int) -> JsonObject:
        data = self._read_guild(guild_id)
        members = data.get("members")

        if not is_json_object(members):
            self.fail("expected members to be a JSON object")

        return members

    async def test_save_snapshot_disabled_returns_zero_without_write(self) -> None:
        member = cast(
            discord.Member,
            cast(
                object,
                SimpleNamespace(
                    bot=False,
                    id=1,
                    guild=SimpleNamespace(id=10),
                    roles=[make_role(5)],
                ),
            ),
        )

        count = await self.manager.save_snapshot(member)

        self.assertEqual(count, 0)
        self.assertFalse((self.root / "guild_10.json").exists())

    async def test_enable_without_ttl_replaces_old_finite_ttl(self) -> None:
        await self.manager.set_enabled(10, True, 3)
        await self.manager.set_enabled(10, False)
        await self.manager.set_enabled(10, True)

        self.assertTrue(await self.manager.is_enabled(10))
        self.assertIsNone(await self.manager.get_ttl(10))

    async def test_disable_preserves_ttl_for_truthful_status(self) -> None:
        await self.manager.set_enabled(10, True, 3)

        await self.manager.set_enabled(10, False)

        self.assertFalse(await self.manager.is_enabled(10))
        self.assertEqual(await self.manager.get_ttl(10), 3)

    async def test_cleanup_expired_removes_only_valid_old_snapshot(self) -> None:
        fixed = datetime(2025, 1, 10, tzinfo=timezone.utc)
        old = (fixed - timedelta(days=10)).isoformat()
        new = (fixed - timedelta(days=1)).isoformat()
        self._write_guild(
            10,
            {
                "enabled": True,
                "ttl_days": 3,
                "members": {
                    "1": {
                        "roles": [1],
                        "username": "u",
                        "left_at": old,
                    },
                    "2": {
                        "roles": [2],
                        "username": "v",
                        "left_at": new,
                    },
                },
            },
        )

        with patch("api.guild_monitoring.utcnow", return_value=fixed):
            removed = await self.manager.cleanup_expired(10)

        self.assertEqual(removed, 1)
        snapshots = await self.manager.get_all_snapshots(10)
        self.assertEqual([snapshot.user_id for snapshot in snapshots], [2])

    async def test_malformed_snapshot_user_key_is_skipped_and_preserved(self) -> None:
        fixed = datetime(2025, 1, 10, tzinfo=timezone.utc)
        self._write_guild(
            10,
            {
                "enabled": True,
                "ttl_days": 3,
                "members": {
                    "bad": {
                        "roles": [1],
                        "username": "invalid-key",
                        "left_at": fixed.isoformat(),
                    },
                    "2": {
                        "roles": [2],
                        "username": "valid",
                        "left_at": fixed.isoformat(),
                    },
                },
            },
        )

        with patch("api.guild_monitoring.utcnow", return_value=fixed):
            removed = await self.manager.cleanup_expired(10)

        snapshots = await self.manager.get_all_snapshots(10)
        members = self._read_members(10)

        self.assertEqual(removed, 0)
        self.assertEqual([snapshot.user_id for snapshot in snapshots], [2])
        self.assertIn("bad", members)

    async def test_malformed_snapshot_fields_are_skipped_and_preserved(self) -> None:
        fixed = datetime(2025, 1, 10, tzinfo=timezone.utc)
        valid_left_at = fixed.isoformat()
        naive_left_at = datetime(2025, 1, 10).isoformat()

        malformed_snapshots: tuple[tuple[str, object], ...] = (
            (
                "roles is not a list",
                {
                    "roles": "not-a-list",
                    "username": "user",
                    "left_at": valid_left_at,
                },
            ),
            (
                "role id is not an integer",
                {
                    "roles": ["not-an-id"],
                    "username": "user",
                    "left_at": valid_left_at,
                },
            ),
            (
                "boolean is not accepted as a role id",
                {
                    "roles": [True],
                    "username": "user",
                    "left_at": valid_left_at,
                },
            ),
            (
                "username is not a string",
                {
                    "roles": [1],
                    "username": 123,
                    "left_at": valid_left_at,
                },
            ),
            (
                "left_at is not a string",
                {
                    "roles": [1],
                    "username": "user",
                    "left_at": 123,
                },
            ),
            (
                "left_at is not ISO formatted",
                {
                    "roles": [1],
                    "username": "user",
                    "left_at": "not-a-datetime",
                },
            ),
            (
                "left_at is timezone naive",
                {
                    "roles": [1],
                    "username": "user",
                    "left_at": naive_left_at,
                },
            ),
        )

        for description, malformed in malformed_snapshots:
            with self.subTest(description):
                self._write_guild(
                    10,
                    {
                        "enabled": True,
                        "ttl_days": 3,
                        "members": {
                            "1": malformed,
                            "2": {
                                "roles": [2],
                                "username": "valid",
                                "left_at": valid_left_at,
                            },
                        },
                    },
                )

                with patch("api.guild_monitoring.utcnow", return_value=fixed):
                    removed = await self.manager.cleanup_expired(10)

                snapshots = await self.manager.get_all_snapshots(10)
                members = self._read_members(10)

                self.assertEqual(removed, 0)
                self.assertEqual(
                    [snapshot.user_id for snapshot in snapshots],
                    [2],
                )
                self.assertEqual(members["1"], malformed)

    async def test_invalid_envelope_fails_closed(self) -> None:
        invalid: JsonObject = {
            "enabled": "yes",
            "ttl_days": None,
            "members": {},
        }
        self._write_guild(10, invalid)

        with self.assertRaises(ValueError):
            await self.manager.set_enabled(10, True)

        self.assertEqual(self._read_guild(10), invalid)

    async def test_restore_snapshot_validates_roles_and_deletes_exact_snapshot(
        self,
    ) -> None:
        guild = SimpleNamespace(id=1)
        add_roles = AsyncMock()
        member = cast(
            discord.Member,
            cast(
                object,
                SimpleNamespace(
                    guild=guild,
                    id=5,
                    add_roles=add_roles,
                ),
            ),
        )
        snapshot = MemberSnapshot(
            user_id=5,
            username="u",
            roles=[10, 20],
            left_at=datetime.now(timezone.utc),
        )
        get_snapshot = AsyncMock(return_value=snapshot)
        delete_snapshot = AsyncMock(return_value=True)
        validate_role = AsyncMock(side_effect=[object(), None])

        with (
            patch.object(self.manager, "get_snapshot", get_snapshot),
            patch.object(self.manager, "delete_snapshot", delete_snapshot),
            patch.object(self.manager, "_validate_role", validate_role),
        ):
            restored, skipped = await self.manager.restore_snapshot(member)

        self.assertEqual(len(restored), 1)
        self.assertEqual(skipped, [20])
        add_roles.assert_awaited_once()
        delete_snapshot.assert_awaited_once_with(
            1,
            5,
            expected_left_at=snapshot.left_at,
        )

    async def test_restore_does_not_delete_newer_concurrent_snapshot(self) -> None:
        old_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self._write_guild(
            10,
            {
                "enabled": True,
                "ttl_days": None,
                "members": {
                    "5": {
                        "roles": [7],
                        "username": "member",
                        "left_at": old_time.isoformat(),
                    },
                },
            },
        )
        role = make_role(7)
        add_started = asyncio.Event()
        release_add = asyncio.Event()

        async def add_roles(*_roles: object, **_kwargs: object) -> None:
            add_started.set()
            await release_add.wait()

        def get_role(_role_id: int) -> SimpleNamespace:
            return role

        def get_member(_member_id: int) -> object:
            return object()

        guild = SimpleNamespace(
            id=10,
            me=SimpleNamespace(id=99),
            get_role=get_role,
            get_member=get_member,
        )
        member = cast(
            discord.Member,
            cast(
                object,
                SimpleNamespace(
                    bot=False,
                    id=5,
                    guild=guild,
                    roles=[role],
                    add_roles=AsyncMock(side_effect=add_roles),
                ),
            ),
        )

        restore = asyncio.create_task(self.manager.restore_snapshot(member))
        await add_started.wait()

        await self.manager.save_snapshot(member)

        release_add.set()
        await restore

        current = await self.manager.get_snapshot(10, 5)

        self.assertIsNotNone(current)
        if current is None:
            self.fail("expected the newer snapshot to remain")

        self.assertNotEqual(current.left_at, old_time)
