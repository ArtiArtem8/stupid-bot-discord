from functools import lru_cache
from string import ascii_lowercase, digits

from stupidbot.i18n.catalog import pgettext_noop


def random_answer(text: str, answers: list[str]) -> str:
    """Choose a repeatable answer derived from normalized message text.

    This is presentation logic, not secure or statistically random selection.
    Equal input text produces the same index within and across processes.

    Args:
        text: Text that determines the selection.
        answers: Non-empty answer choices.

    Returns:
        One item from ``answers``.

    Raises:
        ZeroDivisionError: If ``answers`` is empty.
    """
    score = 1
    for index, character in enumerate(text.lower()):
        codepoint = ord(character)
        if index == 1:
            score += codepoint
        if codepoint % 7 == 0:
            score *= codepoint
        elif codepoint % 3 == 0:
            score += codepoint**2 // 2
        elif codepoint % 5 == 0 or index % 17 == 0:
            score += hash(codepoint)
        else:
            score += codepoint
    return answers[score % len(answers)]


@lru_cache(maxsize=10)
def str_local(text: str) -> str:
    """Normalize text to lowercase Russian, ASCII letters, and digits.

    Whitespace, punctuation, and characters outside that fixed mask are removed.
    Results are cached for the ten most recently used input strings.
    """
    mask = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" + ascii_lowercase + digits
    return "".join(i for i in text.lower() if i in mask)


CAPABILITIES = [
    pgettext_noop("answer", "Вероятно нет"),
    pgettext_noop("answer", "Весьма спорно"),
    pgettext_noop("answer", "Возможно"),
    pgettext_noop("answer", "Да!"),
    pgettext_noop("answer", "Зря ты спросил"),
    pgettext_noop("answer", "Не хочу отвечать"),
    pgettext_noop("answer", "Нет!"),
    pgettext_noop("answer", "Почти наверняка"),
    pgettext_noop("answer", "Предрешено"),
    pgettext_noop("answer", "Скорее да"),
    pgettext_noop("answer", "Я не знаю"),
] * 2 + [
    pgettext_noop("answer", "..."),
    pgettext_noop(
        "answer",
        "Безумие — это точное повторение одного и того же действия. Раз "
        "за разом, в надежде на изменение. Это есть безумие.",
    ),
    pgettext_noop("answer", "Вы из тех людей, с кем очень приятно общаться"),
    pgettext_noop("answer", "Вы из тех людей, с кем очень приятно прощаться"),
    pgettext_noop(
        "answer", "Извините, но я сегодня слишком занят для бесполезного общения"
    ),
]
