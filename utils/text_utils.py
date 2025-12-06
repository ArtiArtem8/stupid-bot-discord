import math
from functools import lru_cache
from string import ascii_lowercase, digits
from typing import Literal


def random_answer(text: str, answers: list[str]) -> str:
    """Generates a deterministic pseudo-random answer from a list of possible answers
    based on the input text.

    This function calculates a pseudo-random index using the characters of the
    input text. The index is used to select an answer from the provided list of
    answers.

    Args:
        text (str): The input text which influences the pseudo-random selection.
        answers (list[str]): A list of potential answers to choose from.

    Returns:
        str: A pseudo-randomly selected answer from the list of answers.

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
    """Filter a string by removing characters not
    in the local alphabet.

    Args:
        text (str): The string to filter.

    Returns:
        str: The filtered string.

    """
    mask = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" + ascii_lowercase + digits
    return "".join(i for i in text.lower() if i in mask)


def reverse_date(date_str: str) -> str:
    """Reverses a date string in the format "DD-MM-YYYY".

    Args:
        date_str (str): The date string to reverse.

    Returns:
        str: The reversed date string if the input is valid, otherwise the same string.

    """
    try:
        parts = date_str.split("-")
        return "-".join(parts[::-1])
    except Exception as err:
        print("Error in reverse_date: ", err)
        return date_str


def format_list(strlist: list[str], cut: int, theme: bool = True) -> list[str]:
    """Format a list of strings into a single string with a given cut-off length (cut).

    Args:
        strlist (list[str]): The list of strings to format.
        cut (int): The maximum length of the output string. If this is exceeded by the
        sum of the lengths of the strings in strlist, strings are concatenated with a
        comma and newline.
        theme (bool, optional): If True, the first string is checked for length against
        cut and if it exceeds it, it is terminated with a newline. Defaults to True.

    Returns:
        list[str]: The formatted list of strings.

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
    """Truncates a string to a maximum width using a configurable strategy.

    Args:
        text: The source string to be shortened.
        width: The maximum allowed length of the result.
        placeholder: The string to append/insert where text is cut. Defaults to "...".
        mode: The truncation strategy.
            - "end": Truncates the tail (e.g., "filename...").
            - "middle": Truncates the center (e.g., "file...ame").
            - "start": Truncates the head (e.g., "...name").
            Defaults to "end".

    Returns:
        The truncated string fitting strictly within the specified width.

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

    elif mode == "start":
        return f"{placeholder}{text[-content_len:]}"
    return f"{text[:content_len]}{placeholder}"
