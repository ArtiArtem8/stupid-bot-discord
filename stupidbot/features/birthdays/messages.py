"""Typed birthday presentation without changing the scheduler's calendar."""

from dataclasses import dataclass

from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class BirthdayMessages:
    """Format calendar data, names and counts for one selected audience."""

    catalog: Catalog

    def date(self, *, day: int, month: str) -> str:
        """Format the saved calendar date independently of the process locale."""
        return self.catalog.pgettext("date", "{day:d} {month}").format(
            day=day, month=month
        )

    def title(self, *, guild: str) -> str:
        """Label the server's birthday list."""
        return self.catalog.pgettext("title", "Дни рождения на сервере {guild}").format(
            guild=guild
        )

    def total(self, *, count: int) -> str:
        """Label the complete snapshot count even when display is truncated."""
        return self.catalog.pgettext("total", "Всего дней рождений: {count:d}").format(
            count=count
        )

    def days_until(self, *, days: int) -> str:
        """Keep the existing compact countdown unit."""
        return self.catalog.pgettext("days_until", "через {days:d} д.").format(
            days=days
        )

    def page(self, *, page: int) -> str:
        """Label continuation fields."""
        return self.catalog.pgettext(
            "page", "Ближайшие дни рождения (стр. {page:d})"
        ).format(page=page)

    def saved(self, *, date: str) -> str:
        """Confirm a committed normalized birthday."""
        return self.catalog.pgettext(
            "saved", "Ваш день рождения записан: {date}"
        ).format(date=date)

    def channel(self, *, mention: str) -> str:
        """Confirm the committed congratulation channel."""
        return self.catalog.pgettext(
            "channel", "Настройки обновлены:\n- Канал: {mention}"
        ).format(mention=mention)

    def role(self, *, mention: str) -> str:
        """Append the configured birthday role."""
        return self.catalog.pgettext("role", "\n- Роль: {mention}").format(
            mention=mention
        )

    def congratulations(self, *, name: str) -> str:
        """Greet a saved user name without translating it."""
        return self.catalog.pgettext(
            "congratulations", "🎉 ПОЗДРАВЛЕНИЯ {name}"
        ).format(name=name)
