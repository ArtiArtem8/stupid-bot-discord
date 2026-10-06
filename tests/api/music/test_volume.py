"""Volume commands and restoration share ordered intent and remote application."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from api.music.volume import VolumeSettings
from repositories.volume_repository import VolumeRepository
from tests.storage import temporary_database


class TestVolumeOrdering(unittest.IsolatedAsyncioTestCase):
    async def test_second_command_waits_for_first_remote_application(self) -> None:
        _, database = await temporary_database(self)
        repository = VolumeRepository(database)
        settings = VolumeSettings(repository)
        entered, release, second_started = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        applied: list[int] = []

        async def first() -> None:
            async with settings.operation(1, desired=20) as volume:
                entered.set()
                await release.wait()
                applied.append(volume)

        async def second() -> None:
            second_started.set()
            async with settings.operation(1, desired=80) as volume:
                applied.append(volume)

        first_task = asyncio.create_task(first())
        await entered.wait()
        second_task = asyncio.create_task(second())
        await second_started.wait()
        self.assertEqual(await repository.get_volume(1), 20)
        self.assertFalse(second_task.done())
        release.set()
        await asyncio.gather(first_task, second_task)
        async with settings.operation(1) as restored:
            self.assertEqual(restored, 80)
        self.assertEqual(applied, [20, 80])

    async def test_failed_persistence_does_not_admit_remote_application(self) -> None:
        _, database = await temporary_database(self)
        repository = VolumeRepository(database)
        settings = VolumeSettings(repository)
        remote = AsyncMock()
        with patch.object(repository, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                async with settings.operation(1, desired=50) as volume:
                    await remote(volume)
        remote.assert_not_awaited()
