"""Application presentation for Discord's single bot-wide presence."""

from dataclasses import dataclass

from stupidbot.common.duration import TimeUnit, calculate_duration
from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class AppMessages:
    """Use the existing uptime breakdown without introducing a second clock."""

    catalog: Catalog

    def unit(self, *, unit: TimeUnit, count: int) -> str:
        """Apply catalog plural rules to one calculated duration part."""
        templates = {
            TimeUnit.YEAR: self.catalog.npgettext(
                "year", "{count:d} год", "{count:d} года", count
            ),
            TimeUnit.MONTH: self.catalog.npgettext(
                "month", "{count:d} месяц", "{count:d} месяца", count
            ),
            TimeUnit.WEEK: self.catalog.npgettext(
                "week", "{count:d} неделю", "{count:d} недели", count
            ),
            TimeUnit.DAY: self.catalog.npgettext(
                "day", "{count:d} день", "{count:d} дня", count
            ),
            TimeUnit.HOUR: self.catalog.npgettext(
                "hour", "{count:d} час", "{count:d} часа", count
            ),
            TimeUnit.MINUTE: self.catalog.npgettext(
                "minute", "{count:d} минуту", "{count:d} минуты", count
            ),
            TimeUnit.SECOND: self.catalog.npgettext(
                "second", "{count:d} секунду", "{count:d} секунды", count
            ),
        }
        return templates[unit].format(count=count)

    def presence(self, *, seconds: int) -> str:
        """Format monotonic uptime; the caller chooses the shared audience language."""
        breakdown = calculate_duration(seconds, depth=2)
        parts = [
            self.unit(unit=part.unit, count=part.value) for part in breakdown.parts
        ]
        if not parts:
            duration = self.unit(unit=TimeUnit.SECOND, count=0)
        elif len(parts) == 1:
            duration = parts[0]
        else:
            duration = self.catalog.pgettext("join", "{first} и {last}").format(
                first=", ".join(parts[:-1]), last=parts[-1]
            )
        return self.catalog.pgettext("presence", "жизнь уже {duration}.").format(
            duration=duration
        )
