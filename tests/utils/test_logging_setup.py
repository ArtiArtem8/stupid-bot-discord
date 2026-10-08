from __future__ import annotations

import logging
import unittest
from typing import Any, cast
from unittest.mock import patch

from utils.logging_setup import CredentialSafeFormatter, setup_logging


class TestLoggingSetup(unittest.TestCase):
    def test_redacts_unquoted_headers_and_secret_key_arrays(self) -> None:
        record = logging.LogRecord(
            "mafic.node",
            logging.ERROR,
            "",
            1,
            "Authorization: Bearer private-auth; secret_key: [11, 22, 33]",
            (),
            None,
        )
        output = CredentialSafeFormatter().format(record)
        self.assertNotIn("private-auth", output)
        self.assertNotIn("22", output)
        self.assertNotIn("33", output)

    def test_redacts_payload_and_exception_without_mutating_arguments(self) -> None:
        payload = {"voice": {"token": "private-token", "sessionId": "private-id"}}
        record = logging.LogRecord(
            "mafic.node",
            logging.DEBUG,
            "",
            1,
            "PATCH guild=42 payload=%s",
            (payload,),
            None,
        )
        record.exc_text = "ValueError: Authorization='private-header'"
        for _ in range(2):
            rendered = CredentialSafeFormatter().format(record)
            for secret in ("private-token", "private-id", "private-header"):
                self.assertNotIn(secret, rendered)
            self.assertIn("guild=42", rendered)
        self.assertEqual(payload["voice"]["token"], "private-token")

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
