from stupidbot.i18n.catalog import pgettext_noop

MORNING_QUEST = (
    "день добрый",
    "дзень добры",
    "доброе утро",
    "солнце взошло",
    "Утречко доброе",
    "утро доброе",
    "утро утреннее",
)


MORNING_ANSWERS = (
    "https://tenor.com/view/anime-smile-beautiful-cute-happy-gif-16596386",
    "https://tenor.com/view/little-witch-academia-witch-good-morning-yawn-stretching-gif-16843917",
    "https://tenor.com/view/shrek-donkey-good-morning-good-morning-good-morning-morning-gif-18326987",
    pgettext_noop(
        "answer",
        "Доброго утра! Желаю хорошего настроения, бодрости и выполнения всех планов.",
    ),
    (
        pgettext_noop(
            "answer",
            "Желаю доброго, бодрого, позитивного, солнечного, "
            "восхитительного, радостного и счастливого утра!",
        )
    ),
    (
        pgettext_noop(
            "answer",
            "Пусть утро будет добрым, ароматным и бодрящим! А вся неделя "
            "легкой, вдохновленной и продуктивной!",
        )
    ),
    pgettext_noop(
        "answer",
        "С добрым утром, с новым днем! Пускай день будет легким, "
        "веселым и плодотворным.",
    ),
    (
        pgettext_noop(
            "answer",
            "С добрым утром! Просыпайся, потянись, улыбнись, взбодрись и "
            "новый день скорей встречай! Ура!",
        )
    ),
    pgettext_noop("answer", "Утро доброе, бл*ть!"),
    pgettext_noop(
        "answer", "Утром один час лучше, чем два вечером. Таджикская пословица"
    ),
    pgettext_noop("answer", "Что утром не сделаешь, то вечером не наверстаешь."),
)


EVENING_QUEST = (
    "доброй ночи",
    "сон",
    "спать",
    "Дозавтра",
    "пора спать",
    "отрубаюсь",
    "наступила ночь",
    "Спокойной ночи",
    "ночь пришла",
)


EVENING_ANSWERS = (
    "https://tenor.com/view/anime-night-gif-13617044",
    "https://tenor.com/view/animu-anime-good-night-good-night-peeps-gif-14037283",
    "https://tenor.com/view/shrek-mehdi-shrek-dance-wati-by-night-maitre-gims-gif-19789528",
    pgettext_noop("answer", "Пришло время попрощаться, но только на сегодня"),
    pgettext_noop(
        "answer", "Пусть ваш ангел-хранитель присмотрит за вами, пока вы спите"
    ),
    pgettext_noop(
        "answer", "Сегодня был отличный день, но завтра, как обычно, будет ещё лучше"
    ),
    pgettext_noop("answer", "Сон – это отражение нашего сердца и души"),
)
