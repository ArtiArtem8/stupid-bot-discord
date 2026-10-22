"""Translate birthday dates, list labels and delivery feedback."""

from dataclasses import dataclass

from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class BirthdayMessages:
    """Format calendar data, names and counts for one selected audience."""

    catalog: Catalog

    def date(self, *, day: int, month: str) -> str:
        """Format the saved calendar date independently of the process locale."""
        return self.catalog.pgettext("date", "{month} {day:d}").format(
            day=day, month=month
        )

    def title(self, *, guild: str) -> str:
        return self.catalog.pgettext("title", "Birthdays on {guild}").format(
            guild=guild
        )

    def total(self, *, count: int) -> str:
        """Label the complete snapshot count even when display is truncated."""
        return self.catalog.pgettext("total", "Total birthdays: {count:d}").format(
            count=count
        )

    def page(self, *, page: int) -> str:
        return self.catalog.pgettext(
            "page", "Upcoming birthdays (page {page:d})"
        ).format(page=page)

    def saved(self, *, date: str) -> str:
        """Confirm a committed normalized birthday."""
        return self.catalog.pgettext(
            "saved", "Your birthday has been saved: {date}"
        ).format(date=date)

    def channel(self, *, mention: str) -> str:
        """Confirm the committed congratulation channel."""
        return self.catalog.pgettext(
            "channel", "Settings updated:\n- Channel: {mention}"
        ).format(mention=mention)

    def role(self, *, mention: str) -> str:
        return self.catalog.pgettext("role", "\n- Role: {mention}").format(
            mention=mention
        )

    def congratulations(self, *, name: str) -> str:
        """Greet a saved user name without translating it."""
        return self.catalog.pgettext(
            "congratulations", "🎉 HAPPY BIRTHDAY {name}"
        ).format(name=name)
