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
                "year", "{count:d} year", "{count:d} years", count
            ),
            TimeUnit.MONTH: self.catalog.npgettext(
                "month", "{count:d} month", "{count:d} months", count
            ),
            TimeUnit.WEEK: self.catalog.npgettext(
                "week", "{count:d} week", "{count:d} weeks", count
            ),
            TimeUnit.DAY: self.catalog.npgettext(
                "day", "{count:d} day", "{count:d} days", count
            ),
            TimeUnit.HOUR: self.catalog.npgettext(
                "hour", "{count:d} hour", "{count:d} hours", count
            ),
            TimeUnit.MINUTE: self.catalog.npgettext(
                "minute", "{count:d} minute", "{count:d} minutes", count
            ),
            TimeUnit.SECOND: self.catalog.npgettext(
                "second", "{count:d} second", "{count:d} seconds", count
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
            duration = self.catalog.pgettext("join", "{first} and {last}").format(
                first=", ".join(parts[:-1]), last=parts[-1]
            )
        return self.catalog.pgettext("presence", "life for {duration}.").format(
            duration=duration
        )
