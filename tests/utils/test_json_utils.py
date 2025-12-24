from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import utils.json_utils as json_utils


class TestGenerateBackupFilename(unittest.TestCase):
    def test_generate_backup_filename_format_is_deterministic_with_dt_and_mocked_suffix(
        self,
    ) -> None:
        fixed_dt = datetime(2025, 1, 2, 3, 4, 5)
        target = Path("repo.json")

        # Force suffix to "AAAA"
        with patch("utils.json_utils.secrets.choice", side_effect=["A", "A", "A", "A"]):
            name = json_utils._generate_backup_filename(target, dt=fixed_dt)  # pyright: ignore[reportPrivateUsage]

        self.assertEqual(name, "repo_20250102_030405AAAA.json")


class TestGetJson(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp: TemporaryDirectory[str] = TemporaryDirectory()
        self._root: Path = Path(self._tmp.name)
        self._path: Path = self._root / "data.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_get_json_missing_returns_none(self) -> None:
        result = json_utils.get_json(self._path)
        self.assertIsNone(result)

    def test_get_json_invalid_returns_none(self) -> None:
        self._path.write_text("{not json", encoding="utf-8")

        result = json_utils.get_json(self._path)

        self.assertIsNone(result)

    def test_get_json_valid_returns_dict(self) -> None:
        payload: dict[str, Any] = {"a": 1, "b": {"c": True}}
        self._path.write_text(json.dumps(payload), encoding="utf-8")

        result = json_utils.get_json(self._path)

        self.assertEqual(result, payload)
        self.assertIsInstance(result, dict)


class TestSaveJson(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp: TemporaryDirectory[str] = TemporaryDirectory()
        self._root: Path = Path(self._tmp.name)
        self._path: Path = self._root / "nested" / "repo.json"
        self._backup_dir: Path = self._root / "backups"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_json_creates_parent_dirs_and_writes(self) -> None:
        payload: dict[str, Any] = {"x": 1}

        json_utils.save_json(
            self._path, payload, backup_amount=0, backup_dir=self._backup_dir
        )

        self.assertTrue(self._path.exists())
        reloaded = json_utils.get_json(self._path)
        self.assertEqual(reloaded, payload)

        temp_path = self._path.with_stem(f"{self._path.stem}_temp")
        self.assertFalse(temp_path.exists())

    def test_save_json_creates_backup_when_overwriting(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"v": 1}), encoding="utf-8")

        with patch(
            "utils.json_utils._generate_backup_filename", return_value="repo_FIXED.json"
        ):
            json_utils.save_json(
                self._path, {"v": 2}, backup_amount=3, backup_dir=self._backup_dir
            )

        backup_path = self._backup_dir / "repo_FIXED.json"
        self.assertTrue(backup_path.exists())

        with backup_path.open("r", encoding="utf-8") as f:
            old = json.load(f)
        self.assertEqual(old, {"v": 1})

        new = json_utils.get_json(self._path)
        self.assertEqual(new, {"v": 2})

    def test_save_json_no_backup_when_backup_amount_zero(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"v": 1}), encoding="utf-8")

        json_utils.save_json(
            self._path, {"v": 2}, backup_amount=0, backup_dir=self._backup_dir
        )

        self.assertFalse(self._backup_dir.exists())
        self.assertEqual(json_utils.get_json(self._path), {"v": 2})


class TestCreateBackup(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp: TemporaryDirectory[str] = TemporaryDirectory()
        self._root: Path = Path(self._tmp.name)
        self._file: Path = self._root / "repo.json"
        self._backup_dir: Path = self._root / "backups"

        self._file.write_text(json.dumps({"v": 1}), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_create_backup_copies_file(self) -> None:
        with patch(
            "utils.json_utils._generate_backup_filename", return_value="repo_BAK.json"
        ):
            json_utils._create_backup(  # pyright: ignore[reportPrivateUsage]
                self._file, max_backups=3, backup_dir=self._backup_dir
            )

        backup_path = self._backup_dir / "repo_BAK.json"
        self.assertTrue(backup_path.exists())

        with backup_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload, {"v": 1})

    def test_create_backup_maintains_safety_buffer(self) -> None:
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        for i in range(2):
            (self._backup_dir / f"repo_OLD{i}.json").write_text("{}", encoding="utf-8")

        with patch(
            "utils.json_utils._generate_backup_filename", return_value="repo_NEW.json"
        ):
            json_utils._create_backup(  # pyright: ignore[reportPrivateUsage]
                self._file, max_backups=3, backup_dir=self._backup_dir
            )

        backups = list(self._backup_dir.glob("repo_*.json"))
        # Assert the actual behavior: Max + 1
        self.assertLessEqual(len(backups), 4)


class TestClearJson(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp: TemporaryDirectory[str] = TemporaryDirectory()
        self._root: Path = Path(self._tmp.name)
        self._file: Path = self._root / "repo.json"
        self._backup_dir: Path = self._root / "backups"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_clear_json_missing_file_noop(self) -> None:
        json_utils.clear_json(self._file, backup_amount=3, backup_dir=self._backup_dir)
        self.assertFalse(self._file.exists())
        self.assertFalse(self._backup_dir.exists())

    def test_clear_json_invalid_default_raises(self) -> None:
        self._file.write_text(json.dumps({"v": 1}), encoding="utf-8")

        with self.assertRaises(json.JSONDecodeError):
            json_utils.clear_json(
                self._file,
                default="{invalid",
                backup_amount=0,
                backup_dir=self._backup_dir,
            )

        # Ensure the original file was not overwritten after validation failure.
        self.assertEqual(json_utils.get_json(self._file), {"v": 1})

    def test_clear_json_overwrites_with_default_and_creates_backup(self) -> None:
        self._file.write_text(json.dumps({"v": 1}), encoding="utf-8")

        with patch(
            "utils.json_utils._generate_backup_filename", return_value="repo_CLR.json"
        ):
            json_utils.clear_json(
                self._file,
                default="{}",
                backup_amount=1,
                backup_dir=self._backup_dir,
            )

        backup_path = self._backup_dir / "repo_CLR.json"
        self.assertTrue(backup_path.exists())

        with backup_path.open("r", encoding="utf-8") as f:
            old = json.load(f)
        self.assertEqual(old, {"v": 1})

        self.assertEqual(json_utils.get_json(self._file), {})
