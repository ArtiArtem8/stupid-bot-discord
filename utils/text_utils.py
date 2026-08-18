import math
from collections.abc import Iterable
from functools import lru_cache
from string import ascii_lowercase, digits
from typing import Literal


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
    k = 1
    for v, i in enumerate(text.lower()):
        if v == 1:
            k += ord(i)
        if ord(i) % 7 == 0:
            k *= ord(i)
        elif ord(i) % 3 == 0:
            k += ord(i) ** 2 // 2
        elif ord(i) % 5 == 0 or v % 17 == 0:
            k += hash(ord(i))
        else:
            k += ord(i)
    return answers[k % len(answers)]


@lru_cache(maxsize=10)
def str_local(text: str) -> str:
    """Normalize text to lowercase Russian, ASCII letters, and digits.

    Whitespace, punctuation, and characters outside that fixed mask are removed.
    Results are cached for the ten most recently used input strings.
    """
    mask = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" + ascii_lowercase + digits
    return "".join(i for i in text.lower() if i in mask)


def format_list(strlist: list[str], cut: int, theme: bool = True) -> list[str]:
    """Insert separators and line breaks into a list of display fragments.

    The input list is mutated in place and returned. ``cut`` is a soft running
    width: it decides where to append a comma and newline but does not truncate
    long items.
    When ``theme`` is true and the first two items would cross ``cut``, the first
    item receives a trailing newline instead of a comma.

    Args:
        strlist: Fragments to modify.
        cut: Soft line-width threshold in characters.
        theme: Apply the special first-item handling.

    Returns:
        The same list instance after separator insertion.
    """
    jolen = 0
    char = " "
    last = len(strlist) - 1
    for v, i in enumerate(strlist):
        jolen += len(i)
        if v == 0 and theme:
            if v + 1 < len(strlist) and jolen + len(strlist[v + 1]) > cut:
                strlist[v] += "\n"
                jolen = 0
        elif v == last:
            break
        elif jolen > cut:
            jolen = 0
            strlist[v] += ",\n"
        else:
            strlist[v] += "," + char
    return strlist


def truncate_text(
    text: str,
    width: int,
    *,
    placeholder: str = "...",
    mode: Literal["end", "start", "middle"] = "end",
) -> str:
    """Truncate text to ``width`` characters using one of three cut positions.

    Args:
        text: Source text.
        width: Hard maximum result length.
        placeholder: Marker inserted at the cut.
        mode: Cut the end, start, or middle of the source.

    Returns:
        ``text`` unchanged when it already fits, otherwise a string no longer
        than ``width``.
    """
    if len(text) <= width:
        return text

    if width < len(placeholder):
        return placeholder[:width]
    content_len = width - len(placeholder)

    if mode == "middle":
        left_len = math.ceil(content_len / 2)
        right_len = math.floor(content_len / 2)
        right_part = text[-right_len:] if right_len > 0 else ""
        return f"{text[:left_len]}{placeholder}{right_part}"

    if mode == "start":
        return f"{placeholder}{text[-content_len:]}"
    return f"{text[:content_len]}{placeholder}"


def truncate_sequence(
    items: Iterable[str],
    max_length: int,
    *,
    separator: str = "\n",
    placeholder: str = "...",
) -> str:
    """Join complete items up to a hard length limit.

    Truncation prefers an item boundary. Only an oversized first item is cut
    internally with :func:`truncate_text`. The placeholder is included in the
    length budget whenever items are omitted.

    Args:
        items: Strings to join; the iterable is consumed once.
        max_length: Hard result length limit.
        separator: Text placed between complete items.
        placeholder: Marker appended when content is omitted.

    Returns:
        A string no longer than ``max_length`` when the limit is non-negative.
    """
    if max_length <= 0:
        return ""

    item_list = list(items)
    if not item_list:
        return ""

    full_text = separator.join(item_list)
    if len(full_text) <= max_length:
        return full_text

    budget = max_length - len(placeholder)

    if not item_list:
        return placeholder

    current_len = 0
    valid_items: list[str] = []

    for i, item in enumerate(item_list):
        sep_cost = len(separator) if i > 0 else 0
        item_cost = len(item)
        total_cost = sep_cost + item_cost

        if current_len + total_cost > budget:
            if i == 0:
                return truncate_text(
                    item, max_length, placeholder=placeholder, mode="end"
                )
            break

        valid_items.append(item)
        current_len += total_cost

    return separator.join(valid_items) + placeholder


class TextPaginator:
    """Split pre-formatted lines by both item count and character budget.

    Each overlong input item is truncated before placement. Empty input produces
    no pages. The returned page list is owned by the paginator.
    """

    __slots__ = ("_pages", "_total_count")

    def __init__(
        self,
        lines: Iterable[str],
        *,
        page_size: int = 25,
        max_length: int = 1024,
        separator: str = "\n",
    ) -> None:
        self._pages: list[str] = []
        input_lines = list(lines)
        self._total_count = len(input_lines)

        current_page: list[str] = []
        current_len = 0
        sep_len = len(separator)

        for line in input_lines:
            display_line = (
                truncate_text(line, width=max_length)
                if len(line) > max_length
                else line
            )

            line_len = len(display_line)
            cost = sep_len + line_len if current_page else line_len

            is_full_len = (current_len + cost) > max_length
            is_full_count = len(current_page) >= page_size

            if is_full_len or is_full_count:
                if current_page:
                    self._pages.append(separator.join(current_page))
                current_page = [display_line]
                current_len = line_len
            else:
                current_page.append(display_line)
                current_len += cost

        if current_page:
            self._pages.append(separator.join(current_page))

    @property
    def pages(self) -> list[str]:
        """Pages in display order."""
        return self._pages

    @property
    def total_items(self) -> int:
        """Number of input items before pagination."""
        return self._total_count
