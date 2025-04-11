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

MORNING_QUEST = [
    "дзень добры",
    "Утречко доброе",
    "день добрый",
    "утро утреннее",
    "солнце взошло",
    "утро доброе",
    "доброе утро",
]

MORNING_ANSWERS = [
    "Желаю доброго, бодрого, позитивного, солнечного, восхитительного, радостного и счастливого утра!",
    "Доброго утра! Желаю хорошего настроения, бодрости и выполнения всех планов.",
    "С добрым утром, с новым днем! Пускай день будет легким, веселым и плодотворным.",
    "С добрым утром! Просыпайся, потянись, улыбнись, взбодрись и новый день скорей встречай! Ура!",
    "Утро доброе, бл*ть!",
    "Пусть утро будет добрым, ароматным и бодрящим! А вся неделя легкой, вдохновленной и продуктивной!",
    "Утром один час лучше, чем два вечером. Таджикская пословица",
    "Что утром не сделаешь, то вечером не наверстаешь.",
    "https://tenor.com/view/anime-smile-beautiful-cute-happy-gif-16596386",
    "https://tenor.com/view/little-witch-academia-witch-good-morning-yawn-stretching-gif-16843917",
    "https://tenor.com/view/shrek-donkey-good-morning-good-morning-good-morning-morning-gif-18326987",
]

EVENING_QUEST = [
    "доброй ночи",
    "сон",
    "спать",
    "Дозавтра",
    "пора спать",
    "отрубаюсь",
    "наступила ночь",
    "Спокойной ночи",
    "ночь пришла",
]
EVENING_ANSWERS = [
    "Пусть ваш ангел-хранитель присмотрит за вами, пока вы спите",
    "Сон – это отражение нашего сердца и души",
    "Пришло время попрощаться, но только на сегодня",
    "Сегодня был отличный день, но завтра, как обычно, будет ещё лучше",
    "https://tenor.com/view/shrek-mehdi-shrek-dance-wati-by-night-maitre-gims-gif-19789528",
    "https://tenor.com/view/animu-anime-good-night-good-night-peeps-gif-14037283",
    "https://tenor.com/view/anime-night-gif-13617044",
]
