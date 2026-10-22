"""Birthday list formatting with externally supplied display names."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import TypedDict

import discord
from discord.utils import format_dt

from stupidbot.common.text import TextPaginator, truncate_text
from stupidbot.discord_ui.embeds import DEFAULT_LIMITS
from stupidbot.i18n.catalog import catalog, pgettext_noop
from stupidbot.i18n.policy import Language

from .dates import DATE_FORMAT, format_storage_date
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
    """One presentation-ready birthday list row.

    ``starts_at`` is ``None`` while the member's local celebration is in
    progress; otherwise it is the UTC start used for Discord's relative time.
    """

    date: str
    name: str
    user_id: int
    starts_at: datetime | None


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
    local_times: Mapping[int, datetime],
    *,
    language: Language = Language.RU,
) -> list[BirthdayListEntry]:
    """Format upcoming celebrations, using saved names when display names are absent.

    Dates name the celebration day, so Feb. 29 birthdays show Feb. 28 in
    non-leap years.
    """
    entries: list[BirthdayListEntry] = []
    for occurrence in config.upcoming_birthdays(local_times):
        user = config.users[occurrence.user_id]
        date_text = format_birthday_date(
            format_storage_date(occurrence.celebration), language=language
        )
        if date_text is None:
            continue
        entries.append(
            {
                "date": date_text,
                "name": display_names.get(occurrence.user_id, user.name),
                "user_id": occurrence.user_id,
                "starts_at": None if occurrence.today else occurrence.starts_at,
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
    """List birthdays within Discord's embed and field limits.

    ``max_field_length`` limits characters per field. Entries that exceed the
    remaining embed budget are omitted; the footer still counts all entries.
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
        starts_at = entry["starts_at"]
        # Discord renders the countdown for each viewer; the date stays text
        # because a timestamp date would follow the viewer's timezone.
        when = (
            messages.catalog.pgettext("ui", "today")
            if starts_at is None
            else format_dt(starts_at, "R")
        )
        safe_date = truncate_text(entry["date"], width=32)
        prefix = f"{i}. **{safe_date}** — "
        suffix = f" · {when}"
        # Cut only the name so the timestamp markup is never split.
        name_width = min(128, max_field_length - len(prefix) - len(suffix))
        safe_name = truncate_text(entry["name"], width=max(name_width, 1))
        lines.append(f"{prefix}{safe_name}{suffix}")

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
