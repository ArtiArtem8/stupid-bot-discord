# ruff: noqa: E501
import logging.config


def setup_logging(encoding: str = "utf-8") -> None:
    """Initialize logging configuration."""
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "detailed": {
                "format": "%(asctime)s %(levelname)s [%(name)s]: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "debug_detailed": {
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
