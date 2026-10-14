"""Birthday list formatting with externally supplied display names."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date, datetime
from typing import TypedDict

import discord

from stupidbot.common.text import TextPaginator, truncate_text
from stupidbot.discord_ui.embeds import DEFAULT_LIMITS
from stupidbot.i18n.catalog import catalog, pgettext_noop
from stupidbot.i18n.policy import Language

from .dates import DATE_FORMAT
from .messages import BirthdayMessages
from .models import BirthdayGuildConfig

MONTH_NAMES = {
    1: pgettext_noop("month", "January"),
    2: pgettext_noop("month", "February"),
    3: pgettext_noop("month", "March"),
    4: pgettext_noop("month", "April"),
    5: pgettext_noop("month", "May"),
    6: pgettext_noop("month", "June"),
    7: pgettext_noop("month", "July"),
    8: pgettext_noop("month", "August"),
    9: pgettext_noop("month", "September"),
    10: pgettext_noop("month", "October"),
    11: pgettext_noop("month", "November"),
    12: pgettext_noop("month", "December"),
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
    except ValueError:
        return None
    messages = BirthdayMessages(catalog("birthdays", language))
    return messages.date(
        day=birthday.day,
        month=messages.catalog.pgettext("month", MONTH_NAMES[birthday.month]),
    )


def build_birthday_list(
    config: BirthdayGuildConfig,
    display_names: Mapping[int, str],
    reference_date: date,
    *,
    language: Language = Language.RU,
) -> list[BirthdayListEntry]:
    """Format upcoming birthdays, using saved names when display names are absent."""
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
    max_field_length: int = DEFAULT_LIMITS.field_value,
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
    title = truncate_text(messages.title(guild=guild_name), width=DEFAULT_LIMITS.title)
    embed = discord.Embed(title=title, color=discord.Color.gold())

    if not entries:
        embed.description = messages.catalog.pgettext(
            "ui", "No birthdays have been added."
        )
        embed.set_footer(text=messages.total(count=0))
        return embed

    footer = messages.total(count=len(entries))
    embed.set_footer(text=truncate_text(footer, width=DEFAULT_LIMITS.footer))

    lines: list[str] = []
    for i, entry in enumerate(entries, 1):
        days_until = entry["days_until"]
        days_text = (
            messages.catalog.pgettext("ui", "today")
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

    for page_num, page_text in enumerate(paginator.pages, 1):
        if len(embed.fields) >= DEFAULT_LIMITS.max_fields:
            break

        field_name = (
            messages.catalog.pgettext("ui", "Upcoming birthdays")
            if page_num == 1
            else messages.page(page=page_num)
        )
        field_name = truncate_text(field_name, width=DEFAULT_LIMITS.field_name)

        projected_total = len(embed) + len(field_name) + len(page_text)
        if projected_total > DEFAULT_LIMITS.max_total:
            break

        embed.add_field(name=field_name, value=page_text, inline=False)

    return embed


BIRTHDAY_WISHES = (
    pgettext_noop(
        "wish",
        "On your birthday, I wish you a life full of happy, joyful moments, a "
        "heart warmed by kind and loving words, and loyal friends by your side!",
    ),
    pgettext_noop(
        "wish",
        "May happiness always be in your hands and all sorrows pass you by! May "
        "your cherished dreams come true easily, and every moment bring joy and "
        "positivity! Discover new horizons, stride forward confidently and "
        "achieve all your goals!",
    ),
    pgettext_noop(
        "wish",
        "A heartfelt happy birthday! Wishing you lots of love and happiness, and "
        "many pleasant surprises. May your life be interesting and full, with all "
        "the strength you need to reach your goals!",
    ),
    pgettext_noop(
        "wish",
        "Happy birthday to you! Wishing you a wonderful, amazing life, an ocean "
        "of love, endless happy times, good health and a great mood!",
    ),
    pgettext_noop(
        "wish",
        "Happy birthday! May your wildest wishes come true, with bright, "
        "unforgettable experiences and success in everything you start. May your "
        "days be sunny and bright, filled with smiles and happiness. Positivity "
        "in everything!",
    ),
    pgettext_noop(
        "wish",
        "Happy birthday! Wishing you a positive mood and plenty of joyful days. "
        "May you greet every morning with a smile and hope for the best. May your "
        "dreams have wings, and your plans come true!",
    ),
    pgettext_noop(
        "wish",
        "Happy birthday! May you always live in joy, surrounded by peace and "
        "lasting happiness. Wishing you a little of everything, and may even "
        "ordinary days sometimes fill with magical events.",
    ),
    pgettext_noop(
        "wish",
        "Happy birthday! Wishing you a wonderful day surrounded by the people who "
        "love and appreciate you most, and lots of fun. Smile, enjoy being "
        "together, and may happiness fill your life.",
    ),
    pgettext_noop(
        "wish",
        "Happy birthday! An ocean of happiness and smiles, and inspiration to "
        "achieve your goals. May life's colours and surprises delight you. Enjoy "
        "every day and share those feelings with others. Stay yourself: you are "
        "one of the best people on the planet!",
    ),
    pgettext_noop(
        "wish",
        "Happy birthday! Wishing you more inspiration and energy this year. May "
        "everything you take on end in success. And plenty of good health, "
        "because with it you can do anything!",
    ),
)
