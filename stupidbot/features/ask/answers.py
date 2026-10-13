from functools import lru_cache
from unicodedata import category, normalize

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
    """Return a canonical question key, preserving ordinary RU/EN keys.

    NFC and lowercase preserve Unicode letters, numbers and attached combining
    marks without compatibility folding. Spacing and punctuation are ignored.
    Input without letters/numbers uses an exact, separate ``raw:v1:`` namespace;
    the unrecoverable historical empty key is never reused.
    """
    normalized = normalize("NFC", normalize("NFC", text).lower())
    result: list[str] = []
    attached = False
    for character in normalized:
        kind = category(character)[0]
        if kind in ("L", "N"):
            result.append(character)
            attached = True
        elif kind == "M" and attached:
            result.append(character)
        else:
            attached = False
    return "".join(result) if result else f"raw:v1:{text}"


CAPABILITIES = [
    pgettext_noop("answer", "Probably not"),
    pgettext_noop("answer", "Highly debatable"),
    pgettext_noop("answer", "Maybe"),
    pgettext_noop("answer", "Yes!"),
    pgettext_noop("answer", "You should not have asked"),
    pgettext_noop("answer", "I do not want to answer"),
    pgettext_noop("answer", "No!"),
    pgettext_noop("answer", "Almost certainly"),
    pgettext_noop("answer", "It is decided"),
    pgettext_noop("answer", "More likely yes"),
    pgettext_noop("answer", "I do not know"),
] * 2 + [
    pgettext_noop("answer", "..."),
    pgettext_noop(
        "answer",
        "Insanity is doing the exact same thing over and over again, expecting things "
        "to change. That is insanity.",
    ),
    pgettext_noop(
        "answer", "You are one of those people who are a pleasure to talk to"
    ),
    pgettext_noop(
        "answer", "You are one of those people who are a pleasure to say goodbye to"
    ),
    pgettext_noop("answer", "Sorry, I am too busy for pointless conversation today"),
]
