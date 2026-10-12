"""Birthday list formatting with externally supplied display names."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date, datetime
from typing import TypedDict

import discord

from stupidbot.common.text import TextPaginator, truncate_text
from stupidbot.i18n.catalog import catalog, pgettext_noop
from stupidbot.i18n.policy import Language

from .dates import DATE_FORMAT
from .messages import BirthdayMessages
from .models import BirthdayGuildConfig

MONTH_NAMES_RU = {
    1: pgettext_noop("month", "января"),
    2: pgettext_noop("month", "февраля"),
    3: pgettext_noop("month", "марта"),
    4: pgettext_noop("month", "апреля"),
    5: pgettext_noop("month", "мая"),
    6: pgettext_noop("month", "июня"),
    7: pgettext_noop("month", "июля"),
    8: pgettext_noop("month", "августа"),
    9: pgettext_noop("month", "сентября"),
    10: pgettext_noop("month", "октября"),
    11: pgettext_noop("month", "ноября"),
    12: pgettext_noop("month", "декабря"),
}


class BirthdayListEntry(TypedDict):
    """One presentation-ready birthday list row."""

    days_until: int
    date: str
    name: str
    user_id: int


def format_birthday_date(
    birthday_str: str, *, language: Language = Language.RU
) -> str | None:
    """Format a stored birthday using the selected language.

    Args:
        birthday_str: Stored birthday in :data:`DATE_FORMAT`.
        language: Language for the month name and date order.

    Returns:
        A phrase such as ``"15 марта"``, or ``None`` for an empty, malformed,
        or unsupported value.
    """
    if not birthday_str:
        return None

    try:
        birthday = datetime.strptime(birthday_str, DATE_FORMAT).date()

        if birthday.month not in MONTH_NAMES_RU:
            return None

        return BirthdayMessages(catalog("birthdays", language)).date(
            day=birthday.day,
            month=catalog("birthdays", language).pgettext(
                "month", MONTH_NAMES_RU[birthday.month]
            ),
        )
    except ValueError:
        return None


def build_birthday_list(
    config: BirthdayGuildConfig,
    display_names: Mapping[int, str],
    reference_date: date,
    *,
    language: Language = Language.RU,
) -> list[BirthdayListEntry]:
    """Format a numeric projection with the existing saved-name fallback."""
    entries: list[BirthdayListEntry] = []
    for occurrence in config.upcoming_birthdays(reference_date):
        user = config.users[occurrence.user_id]
        date_text = format_birthday_date(user.birthday, language=language)
        if date_text is None:
            continue
        entries.append(
            {
                "days_until": occurrence.days_until,
                "date": date_text,
                "name": display_names.get(occurrence.user_id, user.name),
                "user_id": occurrence.user_id,
            }
        )
    return entries


def create_birthday_list_embed(
    guild_name: str,
    entries: Collection[BirthdayListEntry],
    max_field_length: int = 1024,
    *,
    language: Language = Language.RU,
) -> discord.Embed:
    """Create a Discord embed representing a list of birthdays.

    Args:
        guild_name: Name of the guild for which the embed is being generated.
        entries: `BirthdayListEntry` objects, each representing a user's birthday.
        max_field_length: Maximum length of a single field in the generated embed.
        language: Language for labels and relative-day text.

    Returns:
        A Discord embed containing the list of birthdays.

    Note:
        The generated embed is trimmed to Discord's constraints on embed length
        (6000 characters) and field count (25 fields).
        If the input list is too long, the generated embed may be truncated.

    """
    messages = BirthdayMessages(catalog("birthdays", language))
    title = truncate_text(messages.title(guild=guild_name), width=256)
    embed = discord.Embed(title=title, color=discord.Color.gold())

    if not entries:
        embed.description = messages.catalog.pgettext(
            "ui", "Нет добавленных дней рождений."
        )
        embed.set_footer(text=messages.total(count=0))
        return embed

    footer = messages.total(count=len(entries))
    embed.set_footer(text=truncate_text(footer, width=2048))

    lines: list[str] = []
    for i, entry in enumerate(entries, 1):
        days_until = entry["days_until"]
        days_text = (
            messages.catalog.pgettext("ui", "сегодня")
            if days_until == 0
            else messages.days_until(days=days_until)
        )

        safe_date = truncate_text(entry["date"], width=32)
        safe_name = truncate_text(entry["name"], width=128)

        line = f"{i}. **{safe_date}** - {safe_name} ({days_text})"
        lines.append(truncate_text(line, width=max_field_length, mode="end"))

    paginator = TextPaginator(
        lines,
        page_size=20,
        max_length=max_field_length,
        separator="\n",
    )

    max_fields = 25
    max_total = 6000

    for page_num, page_text in enumerate(paginator.pages, 1):
        if len(embed.fields) >= max_fields:
            break

        field_name = (
            messages.catalog.pgettext("ui", "Ближайшие дни рождения")
            if page_num == 1
            else messages.page(page=page_num)
        )
        field_name = truncate_text(field_name, width=256)

        projected_total = len(embed) + len(field_name) + len(page_text)
        if projected_total > max_total:
            break

        embed.add_field(name=field_name, value=page_text, inline=False)

    return embed


BIRTHDAY_WISHES = (
    (
        pgettext_noop(
            "wish",
            "В день твоего рождения хочу пожелать, чтобы в жизни было всегда "
            "больше счастливых и радостных минут, чтобы сердце наполнялось "
            "радостью от тёплых и ласковых слов, а рядом находились верные друзья!",
        )
    ),
    (
        pgettext_noop(
            "wish",
            "Желаю, чтобы птица счастья всегда была в твоих руках, а все печали "
            "пролетали мимо! Пускай заветные мечты осуществляются легко и просто, "
            "а каждый миг наполняется радостью и позитивом! Открывай новые "
            "горизонты, шагай уверенно вперед и достигай всех целей!",
        )
    ),
    (
        pgettext_noop(
            "wish",
            "Поздравляем от души с днем рождения! Желаем много любви и счастья, "
            "чтобы судьба чаще дарила приятные сюрпризы. Пусть жизнь будет "
            "интересной и насыщенной, пусть всегда хватает сил для достижения "
            "желаемых высот!",
        )
    ),
    (
        pgettext_noop(
            "wish",
            "Поздравляю с днём рождения тебя! Желаю прекрасной и удивительной "
            "жизни, океан безумной любви, бесконечно счастливого времени, только "
            "крепкого здоровья и отличного настроения!",
        )
    ),
    (
        pgettext_noop(
            "wish",
            "Поздравляю с Днем рождения! Желаю исполнения самых невероятных и "
            "головокружительных желаний, ярких и незабываемых впечатлений, "
            "успехов во всех твоих начинаниях. Пусть дни будут солнечными и "
            "яркими, наполненными улыбками и счастьем. В общем, позитива во всем!",
        )
    ),
    (
        pgettext_noop(
            "wish",
            "Поздравляю с днем рождения! Желаю позитивного настроения, побольше "
            "радостных дней. Желаю Вам встречать каждое утро с улыбкой, верой в "
            "лучшее. Пусть у Вашей мечты будут крылья! Свершения Вам задуманных "
            "планов!",
        )
    ),
    (
        pgettext_noop(
            "wish",
            "С днем рождения! Желаю жить всегда в радости, в окружении "
            "спокойствия и постоянного счастья, желаю вам всего и понемногу, и "
            "пусть самые простые дни иногда наполняются сказочными событиями.",
        )
    ),
    (
        pgettext_noop(
            "wish",
            "С днем рождения! Желаю прекрасного дня в окружении людей, которые "
            "любят и ценят тебя больше всего, и много веселья. Улыбайся, "
            "наслаждайся радостью общения, и пусть счастье наполнит твою жизнь.",
        )
    ),
    (
        pgettext_noop(
            "wish",
            "С днем рождения! Море счастья и улыбок. Вдохновения к воплощению "
            "поставленных целей. Пусть жизнь радует своей красочностью и "
            "непредсказуемостью. Радуйся каждому дню и дари эти эмоции другим. "
            "Оставайся собой, ты один из лучших людей на планете!",
        )
    ),
    (
        pgettext_noop(
            "wish",
            "С днем рождения! Хочется пожелать тебе в этом году побольше "
            "вдохновения и энергии. Пускай все, за что бы ты не взялся, имеет "
            "успешный финал. Здоровья побольше, ведь с ним ты сможешь все!",
        )
    ),
)
