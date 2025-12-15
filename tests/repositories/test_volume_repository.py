import unittest
from unittest.mock import patch

import config
from repositories.volume_repository import VolumeData, VolumeRepository

# Mock config
config.MUSIC_DEFAULT_VOLUME = 100
config.MUSIC_VOLUME_FILE = "mock_volume.json"


class TestVolumeRepository(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repo = VolumeRepository()

    async def test_get_volume_existing(self):
        with patch(
            "repositories.volume_repository.get_json", return_value={"123": 50}
        ) as mock_get:
            vol = await self.repo.get_volume(123)
            self.assertEqual(vol, 50)
            mock_get.assert_called_with("mock_volume.json")

    async def test_get_volume_default(self):
        with (
            patch(
                "repositories.volume_repository.get_json", return_value={}
            ) as mock_get,
            patch("repositories.volume_repository.save_json") as mock_save,
        ):
            vol = await self.repo.get_volume(999)
            self.assertEqual(vol, 100)
            mock_get.assert_called_with("mock_volume.json")
            mock_save.assert_not_called()

    async def test_save_updates_one_user_preserves_others(self):
        # Setup: File contains user 123 and user 999
        initial_data = {"123": 50, "999": 100}

        with (
            patch("repositories.volume_repository.get_json", return_value=initial_data),
            patch("repositories.volume_repository.save_json") as mock_save,
        ):
            # Action: Update ONLY user 123
            await self.repo.save(VolumeData(123, 75))

            # Assert: User 123 is updated, BUT User 999 is still there
            mock_save.assert_called_with("mock_volume.json", {"123": 75, "999": 100})

    async def test_save_creates_new_file_if_missing(self):
        # Simulate get_json returning empty dict (or handling FileNotFoundError internally)
        with (
            patch("repositories.volume_repository.get_json", return_value={}),
            patch("repositories.volume_repository.save_json") as mock_save,
        ):
            await self.repo.save(VolumeData(123, 50))

            # Should save the first ever entry
            mock_save.assert_called_with("mock_volume.json", {"123": 50})

    async def test_save_new_volume(self):
        with (
            patch(
                "repositories.volume_repository.get_json", return_value={}
            ) as mock_get,
            patch("repositories.volume_repository.save_json") as mock_save,
        ):
            await self.repo.save(VolumeData(456, 80))

            mock_get.assert_called_with("mock_volume.json")
            mock_save.assert_called_with("mock_volume.json", {"456": 80})
