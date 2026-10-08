# ruff: noqa: E501
import logging.config
import re
from typing import override


class CredentialSafeFormatter(logging.Formatter):
    """Redact credentials only in known transport loggers and their exceptions.

    Sanitize the formatted copy so logging arguments and network payloads remain
    untouched, and cached exception text cannot bypass another handler's policy.
    Application message/audit logs retain their complete original content.
    """

    _transport_loggers = (
        "mafic",
        "discord.http",
        "discord.gateway",
        "discord.voice_state",
        "discord.voice_client",
        "aiohttp.client",
        "aiohttp.client_ws",
    )

    _credentials = re.compile(
        r"(?i)([\"']?(?:token|session_?id|secret_?key|authorization|password)"
        r"[\"']?\s*[:=]\s*)(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'"
        r"|\[[^\]]*\]|(?:Bearer|Bot|Basic)\s+[^\s,;}]+|[^\s,;}]+)"
    )

    @override
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        if any(
            record.name == name or record.name.startswith(f"{name}.")
            for name in self._transport_loggers
        ):
            return self._credentials.sub(r"\1'[REDACTED]'", rendered)
        return rendered


def setup_logging(encoding: str = "utf-8") -> None:
    """Initialize logging configuration."""
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "detailed": {
                "()": CredentialSafeFormatter,
                "format": "%(asctime)s %(levelname)s [%(name)s]: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "debug_detailed": {
                "()": CredentialSafeFormatter,
                "format": "%(asctime)s %(levelname)s [%(name)s:%(lineno)d]: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "file_handler": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": "discord-bot.log",
                "encoding": encoding,
                "formatter": "detailed",
                "level": "INFO",
                "maxBytes": 25 * 1024 * 1024,
                "backupCount": 5,
            },
            "debug_file_handler": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": "discord-bot-debug.log",
                "encoding": encoding,
                "formatter": "debug_detailed",
                "level": "DEBUG",
                "maxBytes": 100 * 1024 * 1024,
                "backupCount": 3,
            },
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "detailed",
                "level": "INFO",
            },
            "discord_console": {
                "class": "logging.StreamHandler",
                "formatter": "detailed",
                "level": "WARNING",
            },
        },
        "root": {
            "handlers": ["file_handler", "debug_file_handler", "console"],
            "level": "DEBUG",
        },
        "loggers": {
            "discord": {
                "handlers": ["file_handler", "debug_file_handler", "discord_console"],
                "level": "INFO",
                "propagate": False,
            },
            "utils": {
                "level": "DEBUG",
                "propagate": True,
            },
        },
    }

    logging.config.dictConfig(logging_config)
