"""Static JSON asset reading remains available after storage migration."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override

from utils import json_utils
from utils.json_types import JsonObject


class TestGetJson(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self._tmp: TemporaryDirectory[str] = TemporaryDirectory()
        self._root: Path = Path(self._tmp.name)
        self._path: Path = self._root / "data.json"

    @override
    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_get_json_missing_returns_none(self) -> None:
        result = json_utils.get_json(self._path)
        self.assertIsNone(result)

    def test_get_json_invalid_raises(self) -> None:
        self._path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(json.JSONDecodeError):
            json_utils.get_json(self._path)

    def test_get_json_non_object_raises(self) -> None:
        self._path.write_text("[]", encoding="utf-8")

        with self.assertRaises(ValueError):
            json_utils.get_json(self._path)

    def test_get_json_valid_returns_dict(self) -> None:
        payload: JsonObject = {"a": 1, "b": {"c": True}}
        self._path.write_text(json.dumps(payload), encoding="utf-8")

        result = json_utils.get_json(self._path)

        self.assertEqual(result, payload)
        self.assertIsInstance(result, dict)
