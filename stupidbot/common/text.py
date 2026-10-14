import math
from collections.abc import Iterable
from typing import Literal


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

    if width <= len(placeholder):
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
            display_line = truncate_text(line, width=max_length)

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
