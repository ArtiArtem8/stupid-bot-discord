"""Exercise birthday storage through offline application and extension lifecycle."""

import asyncio
import sqlite3
import unittest
from contextlib import closing
from datetime import date
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from api.birthday import birthday_manager
from cogs import birthday_cog
from cogs.birthday_cog import BirthdayCog, ConfirmDeleteView, setup
from framework.bot import StupidBot
from framework.cog_loader import CogLoader
from framework.feedback_ui import FeedbackUI
from repositories.birthday_sqlite.__main__ import Arguments, _run
from repositories.birthday_sqlite.database import migrate
from repositories.birthday_sqlite.repository import SQLiteBirthdayRepository
from utils.asyncio_utils import run_in_thread


class TestBirthdayStorage(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.database = self.directory / "birthdays % local.sqlite"

    def make_bot(self, database: Path | None) -> StupidBot:
        loader = MagicMock(spec=CogLoader)
        bot = StupidBot(cog_loader=loader, birthday_database=database)

        async def load() -> None:
            await setup(bot)

        loader.load_cogs.side_effect = load
        self.enterContext(patch.object(bot.tree, "sync", new=AsyncMock()))
        self.enterContext(
            patch.object(
                bot, "wait_until_ready", new=AsyncMock(side_effect=asyncio.Event().wait)
            )
        )
        self.addAsyncCleanup(bot.close)
        return bot

    async def test_default_application_keeps_existing_json_owner(self) -> None:
        bot = self.make_bot(None)
        await bot.setup_hook()
        cog = bot.get_cog("BirthdayCog")
        self.assertIsInstance(cog, BirthdayCog)
        if isinstance(cog, BirthdayCog):
            self.assertIs(cog.manager, birthday_manager)
        self.assertIs(bot.birthday_manager, birthday_manager)
        self.assertIsNone(bot._birthday_engine)

    async def test_import_app_mutations_close_and_reopen_leave_json_unchanged(
        self,
    ) -> None:
        source = self.directory / "birthdays.json"
        original = b'{"1":{"Server_name":"Guild","Channel_id":"2","Users":{}}}'
        await run_in_thread(lambda: source.write_bytes(original))
        args = Arguments()
        args.database = self.database
        args.command = "migrate"
        await _run(args)
        args.command = "import-json"
        args.source = source
        await _run(args)
        await _run(args)

        bot = self.make_bot(self.database)
        await bot.setup_hook()
        cog = bot.get_cog("BirthdayCog")
        self.assertIsInstance(cog, BirthdayCog)
        if not isinstance(cog, BirthdayCog):
            self.fail("Birthday extension was not loaded")
        manager = cog.manager
        self.assertIs(manager, bot.birthday_manager)
        self.assertIsInstance(manager.repo, SQLiteBirthdayRepository)
        await manager.set_user_birthday(1, "Guild", 2, 3, "Member", "06-10-2000")
        self.assertTrue(await manager.record_congratulation(1, 3, date(2026, 10, 6)))
        self.assertFalse(await manager.record_congratulation(1, 3, date(2026, 10, 6)))
        view = ConfirmDeleteView(3, 1, manager)
        self.assertIs(view.manager, manager)
        interaction = MagicMock()
        interaction.user.id = 3
        with (
            patch.object(
                birthday_cog, "check_component_access", new=AsyncMock(return_value=True)
            ),
            patch.object(FeedbackUI, "send", new=AsyncMock()),
        ):
            await view.confirm.callback(interaction)
        view.stop()
        expected = await manager.get_guild_config(1)
        if expected is None:
            self.fail("Deletion must retain guild settings and congratulation history")
        self.assertFalse(expected.users[3].has_birthday())
        self.assertEqual(len(expected.users[3].was_congrats), 1)
        with self.assertRaises(ValueError):
            await _run(args)

        engine = bot._birthday_engine
        if engine is None:
            self.fail("SQLite engine was not owned by the application")
        closed = asyncio.Event()
        event.listen(engine.sync_engine, "close", lambda *_: closed.set())
        original_unload = cog.cog_unload

        async def unload() -> None:
            self.assertFalse(closed.is_set())
            self.assertEqual(await manager.get_all_guild_ids(), [1])
            await original_unload()

        with patch.object(cog, "cog_unload", side_effect=unload):
            await bot.close()
        self.assertTrue(closed.is_set())
        self.assertEqual(bot.cogs, {})
        await bot.close()

        reopened = self.make_bot(self.database)
        await reopened.setup_hook()
        self.assertIsNot(reopened.birthday_manager, manager)
        self.assertEqual(
            await reopened.birthday_manager.get_guild_config(1),
            expected,
        )
        self.assertEqual(await run_in_thread(source.read_bytes), original)

    async def test_unprepared_databases_abort_before_loading_cogs(self) -> None:
        for state in ("missing", "empty", "wrong-revision"):
            with self.subTest(state=state):
                path = self.directory / f"{state}.sqlite"
                if state == "empty":
                    await run_in_thread(path.touch)
                elif state == "wrong-revision":
                    await run_in_thread(partial(migrate, path))

                    def change_revision(database: Path = path) -> None:
                        with closing(sqlite3.connect(database)) as connection:
                            connection.execute(
                                "UPDATE alembic_version SET version_num='future'"
                            )
                            connection.commit()

                    await run_in_thread(change_revision)
                bot = self.make_bot(path)
                with patch.object(bot.cog_loader, "load_cogs", new=AsyncMock()) as load:
                    with self.assertRaises((OperationalError, RuntimeError)):
                        await bot.setup_hook()
                    load.assert_not_awaited()
                await bot.close()
                if state == "missing":
                    self.assertFalse(await run_in_thread(path.exists))

    async def test_partial_startup_failure_disposes_sqlite_connection(self) -> None:
        await run_in_thread(lambda: migrate(self.database))
        bot = self.make_bot(self.database)
        engine = bot._birthday_engine
        if engine is None:
            self.fail("Missing engine")
        closed = asyncio.Event()
        event.listen(engine.sync_engine, "close", lambda *_: closed.set())
        with patch.object(
            bot.tree, "sync", side_effect=RuntimeError("offline failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "offline failure"):
                await bot.setup_hook()
        await bot.close()
        self.assertTrue(closed.is_set())
        self.assertEqual(bot.cogs, {})
