import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ENCODING = "utf-8"

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
BOT_PREFIX = "s!"
BOT_ICON = "https://icon-library.com/images/icon-for-discord/icon-for-discord-17.jpg"

BACKUP_DIR = Path(__file__).parent / "backups"
DATA_DIR = Path(__file__).parent / "data"

LOGGING_CONFIG = {
    "version": 1,
    "formatters": {
        "detailed": {
            "format": "%(asctime)s %(levelname)s [%(name)s]: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "file_handler": {
            "class": "logging.FileHandler",
            "filename": "discord-bot.log",
            "encoding": ENCODING,
            "formatter": "detailed",
            "level": "INFO",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "detailed",
            "level": "INFO",
        },
    },
    "root": {
        "handlers": ["file_handler", "console"],
        "level": "INFO",
    },
}

CAPABILITIES = [
    "Вероятно нет",
    "Не хочу отвечать",
    "Весьма спорно",
    "Почти наверняка",
    "Возможно",
    "Скорее да",
    "Зря ты спросил",
    "Я не знаю",
    "Нет!",
    "Предрешено",
    "Да!",
] * 2 + [
    "Вы из тех людей, с кем очень приятно прощаться",
    "Вы из тех людей, с кем очень приятно общаться",
    "Извините, но я сегодня слишком занят для бесполезного общения",
    "...",
    "Безумие — это точное повторение одного и того же действия. Раз за разом, в надежде на изменение. Это есть безумие.",
]

ANSWER_FILE = (DATA_DIR / "user_answers").with_suffix(".json")
