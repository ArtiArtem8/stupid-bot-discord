from functools import lru_cache
from string import ascii_lowercase, digits


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
    "Вероятно нет",
    "Весьма спорно",
    "Возможно",
    "Да!",
    "Зря ты спросил",
    "Не хочу отвечать",
    "Нет!",
    "Почти наверняка",
    "Предрешено",
    "Скорее да",
    "Я не знаю",
] * 2 + [
    "...",
    (
        "Безумие — это точное повторение одного и того же действия. Раз "
        "за разом, в надежде на изменение. Это есть безумие."
    ),
    "Вы из тех людей, с кем очень приятно общаться",
    "Вы из тех людей, с кем очень приятно прощаться",
    "Извините, но я сегодня слишком занят для бесполезного общения",
]
