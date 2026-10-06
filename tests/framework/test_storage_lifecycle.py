"""Offline application assembly requires one prepared database for every feature."""

import asyncio
import unittest
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from cogs.birthday_cog import BirthdayCog
from cogs.birthday_cog import setup as setup_birthdays
from cogs.music.music_cog import MusicCog
from cogs.music.music_cog import setup as setup_music
from framework.bot import StupidBot
from framework.cog_loader import CogLoader
from repositories.sqlite.database import Database, migrate, open_engine
from repositories.volume_repository import VolumeData
from utils.asyncio_utils import run_in_thread


class TestStorageLifecycle(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncSetUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "application % local.sqlite"

    def make_bot(self, path: Path) -> StupidBot:
        loader = MagicMock(spec=CogLoader)
        bot = StupidBot(cog_loader=loader, database_path=path)

        async def load() -> None:
            await setup_birthdays(bot)
            await setup_music(bot)

        loader.load_cogs.side_effect = load
        self.enterContext(patch.object(bot.tree, "sync", new=AsyncMock()))
        self.enterContext(
            patch.object(
                bot, "wait_until_ready", new=AsyncMock(side_effect=asyncio.Event().wait)
            )
        )
        self.addAsyncCleanup(bot.close)
        return bot

    async def test_normal_assembly_shares_storage_and_reopens_saved_settings(
        self,
    ) -> None:
        await run_in_thread(partial(migrate, self.path))
        bot = self.make_bot(self.path)
        await bot.setup_hook()
        birthday = bot.get_cog("BirthdayCog")
        music = bot.get_cog("MusicCog")
        self.assertIsInstance(birthday, BirthdayCog)
        if not isinstance(music, MusicCog):
            self.fail("Missing music cog")
        self.assertIs(music.components.volumes, bot.volume_repository)
        self.assertIs(
            music.components.healer.volume_settings,
            music.components.service.volume_settings,
        )
        await bot.birthday_manager.set_user_birthday(
            1, "Guild", 10, 2, "Member", "01-01-2000"
        )
        await bot.volume_repository.save(VolumeData(1, 0))
        await bot.close()
        reopened = self.make_bot(self.path)
        await reopened.restore_state()
        self.assertEqual(await reopened.volume_repository.get_volume(1), 0)
        birthday = await reopened.birthday_manager.get_guild_config(1)
        if birthday is None:
            self.fail("Birthday not persisted")
        self.assertEqual(birthday.users[2].birthday, "01-01-2000")
        self.assertEqual(list(self.directory.glob("*.json")), [])

    async def test_unprepared_or_incomplete_database_stops_before_loading_cogs(
        self,
    ) -> None:
        for state in ("missing", "empty", "wrong-revision", "building"):
            with self.subTest(state=state):
                path = self.directory / f"{state}.sqlite"
                if state == "empty":
                    await run_in_thread(path.touch)
                elif state != "missing":
                    await run_in_thread(partial(migrate, path))
                    database = Database(open_engine(path))
                    try:
                        async with database.transaction() as connection:
                            statement = (
                                "UPDATE storage_state SET status='BUILDING'"
                                if state == "building"
                                else "UPDATE alembic_version SET version_num='future'"
                            )
                            await connection.execute(text(statement))
                    finally:
                        await database.close()
                bot = self.make_bot(path)
                with patch.object(bot.cog_loader, "load_cogs", new=AsyncMock()) as load:
                    with self.assertRaises((OperationalError, RuntimeError)):
                        await bot.setup_hook()
                    load.assert_not_awaited()
                await bot.close()
                if state == "missing":
                    self.assertFalse(path.exists())

    async def test_partial_startup_failure_disposes_shared_connection(self) -> None:
        await run_in_thread(partial(migrate, self.path))
        bot = self.make_bot(self.path)
        closed = asyncio.Event()
        event.listen(bot._database.engine.sync_engine, "close", lambda *_: closed.set())
        with patch.object(
            bot.tree, "sync", side_effect=RuntimeError("offline failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "offline failure"):
                await bot.setup_hook()
        await bot.close()
        self.assertTrue(closed.is_set())
        self.assertEqual(bot.cogs, {})
