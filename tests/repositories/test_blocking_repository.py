"""Block state, audit and observed names commit together without cross-guild leakage."""

import asyncio
import unittest
from datetime import UTC, datetime
from typing import override

from sqlalchemy import event

from repositories.sqlite_blocking_repository import SQLiteBlockingRepository
from tests.storage import temporary_database


class TestBlockingRepository(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        _, self.database = await temporary_database(self)
        self.repo = SQLiteBlockingRepository(self.database)
        self.now = datetime(2026, 10, 6, tzinfo=UTC)

    async def change(
        self, guild: int, *, blocked: bool, name: str = "Nickname"
    ) -> bool:
        return await self.repo.change(
            guild,
            2,
            blocked=blocked,
            display_name=name,
            username="username",
            admin_id=3,
            reason="reason",
            observed_at=self.now,
        )

    async def test_missing_state_and_independent_guild_contexts(self) -> None:
        self.assertIsNone(await self.repo.get((1, 2)))
        self.assertFalse(await self.repo.is_blocked(1, 2))
        await self.change(1, blocked=True)
        await self.change(4, blocked=False, name="Other guild nickname")
        self.assertTrue(await self.repo.is_blocked(1, 2))
        self.assertFalse(await self.repo.is_blocked(4, 2))
        self.assertEqual(len(await self.repo.get_all_for_guild(1)), 1)
        self.assertEqual(await self.repo.get_all_for_guild(99), [])

    async def test_concurrent_repeats_create_one_transition_and_preserve_histories(
        self,
    ) -> None:
        changed = await asyncio.gather(
            *(self.change(1, blocked=True) for _ in range(8))
        )
        self.assertEqual(sum(changed), 1)
        await self.change(1, blocked=False, name="Renamed")
        await self.change(1, blocked=True, name="Renamed")
        user = await self.repo.get((1, 2))
        if user is None:
            self.fail("Missing user")
        self.assertTrue(user.is_blocked)
        self.assertEqual(len(user.block_history), 2)
        self.assertEqual(len(user.unblock_history), 1)
        self.assertEqual(
            [entry.username for entry in user.name_history], ["Nickname", "Renamed"]
        )
        self.assertEqual(user.block_history[0].timestamp, self.now)
        user.block_history.clear()
        reloaded = await self.repo.get((1, 2))
        self.assertIsNotNone(reloaded)
        if reloaded is not None:
            self.assertEqual(len(reloaded.block_history), 2)

    async def test_audit_write_failure_rolls_back_state_and_name_observation(
        self,
    ) -> None:
        await self.change(1, blocked=True)
        before = await self.repo.get((1, 2))

        def fail(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _many: object,
        ) -> None:
            if statement.startswith("INSERT INTO block_events"):
                raise OSError("injected write failure")

        event.listen(self.database.engine.sync_engine, "before_cursor_execute", fail)
        try:
            with self.assertRaises(OSError):
                await self.change(1, blocked=False, name="Must roll back")
        finally:
            event.remove(
                self.database.engine.sync_engine, "before_cursor_execute", fail
            )
        self.assertEqual(await self.repo.get((1, 2)), before)
        self.assertTrue(await self.change(1, blocked=False))

    async def test_unavailable_database_does_not_allow_access(self) -> None:
        await self.database.close()
        with self.assertRaises(RuntimeError):
            await self.repo.is_blocked(1, 2)
