from __future__ import annotations

import json
import logging
import logging.config
import multiprocessing
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from stupidbot.app.logging import CredentialSafeFormatter, setup_logging


def _write_log_samples(directory: str) -> None:
    os.chdir(directory)
    setup_logging()
    driver = logging.getLogger("aiosqlite")
    driver.debug("private SQL value marker")
    driver.warning("driver warning marker")
    logging.getLogger("stupidbot.test").debug("application debug marker")
    logging.shutdown()


class TestLoggingSetup(unittest.TestCase):
    def test_driver_payloads_stay_out_of_logs_without_disabling_app_debug(self) -> None:
        with TemporaryDirectory() as directory:
            process = multiprocessing.get_context("spawn").Process(
                target=_write_log_samples, args=(directory,)
            )
            process.start()
            try:
                process.join(timeout=15)
                self.assertFalse(process.is_alive(), "Logging child did not finish")
                self.assertEqual(process.exitcode, 0)
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                process.close()
            main = (Path(directory) / "discord-bot.log").read_text(encoding="utf-8")
            debug = (Path(directory) / "discord-bot-debug.log").read_text(
                encoding="utf-8"
            )
            for content in (main, debug):
                self.assertNotIn("private SQL value marker", content)
                self.assertIn("driver warning marker", content)
            self.assertIn("application debug marker", debug)

    def test_transport_payload_preserves_chat_and_track_text(self) -> None:
        content = 'token: chat-token; "password": "chat-password"; don\'t erase this'
        payload = {
            "content": content,
            "track": {"title": "Authorization: Bearer song-title"},
            "voice": {"token": "private-voice-token", "sessionId": "private-id"},
            "token_count": 42,
        }
        for value in (repr(payload), json.dumps(payload)):
            with self.subTest(value=value):
                record = logging.LogRecord(
                    "discord.gateway", logging.DEBUG, "", 1, "Event: %s", (value,), None
                )
                rendered = CredentialSafeFormatter().format(record)
                expected = value.replace("private-voice-token", "[REDACTED]").replace(
                    "private-id", "[REDACTED]"
                )
                expected = expected.replace('"[REDACTED]"', "'[REDACTED]'")
                self.assertEqual(rendered, f"Event: {expected}")

    def test_credential_names_do_not_match_other_field_suffixes(self) -> None:
        text = "retry_token=keep; previous-password=keep; session_id_suffix=keep"
        record = logging.LogRecord("mafic.node", logging.DEBUG, "", 1, text, (), None)
        self.assertEqual(CredentialSafeFormatter().format(record), text)

    def test_chat_payload_and_application_logs_remain_verbatim(self) -> None:
        content = (
            "секрет: мой пароль; token: chat-token; Authorization: Bearer chat-auth"
        )
        for logger_name in (
            "stupidbot.app.message_events_cog",
            "stupidbot.features.reports.ui",
            "mafication.app",
        ):
            with self.subTest(logger=logger_name):
                record = logging.LogRecord(
                    logger_name, logging.INFO, "", 1, "%s", (content,), None
                )
                self.assertEqual(CredentialSafeFormatter().format(record), content)

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
        with patch.object(logging.config, "dictConfig") as dict_config:
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
