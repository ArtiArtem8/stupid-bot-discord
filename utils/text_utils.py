import random
from functools import lru_cache
from string import ascii_lowercase, digits


def random_answer(text: str, answers: list[str]) -> str:
    k = 1
    for v, i in enumerate(text.lower()):
        if v == 1:
            k += ord(i)
        if ord(i) % 7 == 0:
            k *= ord(i)
        elif ord(i) % 3 == 0:
            k += ord(i) ** 2 // 2
        elif ord(i) % 5 == 0 or v % 17 == 0:
            k += random.randint(123, 2003)
        else:
            k += ord(i)
    return answers[k % len(answers)]


@lru_cache(maxsize=10)
def str_local(text: str) -> str:
    mask = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" + ascii_lowercase + digits
    return "".join(i for i in text.lower() if i in mask)


def reverse_date(date_str: str) -> str:
    try:
        parts = date_str.split("-")
        return "-".join(parts[::-1])
    except Exception as err:
        print("Error in reverse_date: ", err)
        return date_str


def format_list(strlist: list[str], cut: int, theme: bool = True) -> list[str]:
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
