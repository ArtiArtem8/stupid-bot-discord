from __future__ import annotations

import unittest
from typing import Any, cast
from unittest.mock import patch

from utils.logging_setup import setup_logging


class TestLoggingSetup(unittest.TestCase):
    def test_rotating_file_retention_is_not_reduced(self) -> None:
        with patch("utils.logging_setup.logging.config.dictConfig") as dict_config:
            setup_logging()

        logging_config = cast(dict[str, Any], dict_config.call_args.args[0])
        handlers = cast(dict[str, dict[str, Any]], logging_config["handlers"])
        main = handlers["file_handler"]
        debug = handlers["debug_file_handler"]

        self.assertEqual(main["class"], "logging.handlers.RotatingFileHandler")
        self.assertEqual(main["maxBytes"], 25 * 1024 * 1024)
        self.assertEqual(main["backupCount"], 5)
        self.assertEqual(debug["class"], "logging.handlers.RotatingFileHandler")
        self.assertEqual(debug["maxBytes"], 100 * 1024 * 1024)
        self.assertEqual(debug["backupCount"], 3)
