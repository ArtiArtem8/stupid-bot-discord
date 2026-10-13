"""Typed voice-profile labels, durations and calendar presentation."""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Literal

from stupidbot.features.voice.profile.appearance import LevelTier
from stupidbot.i18n.catalog import Catalog, pgettext_noop

MONTHS = (
    pgettext_noop("month", "Jan"),
    pgettext_noop("month", "Feb"),
    pgettext_noop("month", "Mar"),
    pgettext_noop("month", "Apr"),
    pgettext_noop("month", "May"),
    pgettext_noop("month", "Jun"),
    pgettext_noop("month", "Jul"),
    pgettext_noop("month", "Aug"),
    pgettext_noop("month", "Sep"),
    pgettext_noop("month", "Oct"),
    pgettext_noop("month", "Nov"),
    pgettext_noop("month", "Dec"),
)
WEEKDAYS = (
    pgettext_noop("weekday", "Monday"),
    pgettext_noop("weekday", "Tuesday"),
    pgettext_noop("weekday", "Wednesday"),
    pgettext_noop("weekday", "Thursday"),
    pgettext_noop("weekday", "Friday"),
    pgettext_noop("weekday", "Saturday"),
    pgettext_noop("weekday", "Sunday"),
)
WEEKDAY_ABBR = (
    pgettext_noop("weekday_short", "Mon"),
    pgettext_noop("weekday_short", "Tue"),
    pgettext_noop("weekday_short", "Wed"),
    pgettext_noop("weekday_short", "Thu"),
    pgettext_noop("weekday_short", "Fri"),
    pgettext_noop("weekday_short", "Sat"),
    pgettext_noop("weekday_short", "Sun"),
)
TIER_LABELS = {
    LevelTier.STARTER: pgettext_noop("tier", "STARTER"),
    LevelTier.UNCOMMON: pgettext_noop("tier", "UNCOMMON"),
    LevelTier.RARE: pgettext_noop("tier", "RARE"),
    LevelTier.EPIC: pgettext_noop("tier", "EPIC"),
    LevelTier.MYTHIC: pgettext_noop("tier", "MYTHIC"),
    LevelTier.LEGENDARY: pgettext_noop("tier", "LEGENDARY"),
    LevelTier.ASCENDANT: pgettext_noop("tier", "ASCENDANT"),
    LevelTier.TRANSCENDENT: pgettext_noop("tier", "TRANSCENDENT"),
}


STATIC_LABELS: dict[str, dict[str, str]] = {
    "profile": {
        "level-prefix": pgettext_noop("profile.label", "LVL"),
        "text5": pgettext_noop("profile.label", "LEVEL PROGRESS"),
        "text6": pgettext_noop("profile.label", "VOICE TIME"),
        "text7": pgettext_noop("profile.label", "SESSIONS"),
    },
    "activity": {
        "card-title": pgettext_noop("activity.label", "Activity"),
        "period-badge": pgettext_noop("activity.label", "Last 30 days"),
        "metric-label-0": pgettext_noop("activity.label", "Total voice"),
        "metric-label-1": pgettext_noop("activity.label", "With people"),
        "metric-label-2": pgettext_noop("activity.label", "Solo"),
        "metric-label-3": pgettext_noop("activity.label", "Sessions"),
        "stat-label-0": pgettext_noop("activity.label", "Average session"),
        "stat-label-1": pgettext_noop("activity.label", "Median session"),
        "stat-label-2": pgettext_noop("activity.label", "Peak hours"),
        "stat-label-3": pgettext_noop("activity.label", "Top days"),
        "chart-title": pgettext_noop("activity.label", "Daily activity"),
        "clock-note": pgettext_noop("activity.label", "* DST"),
        "voice-legend": pgettext_noop("activity.label", "Voice"),
        "missing-legend": pgettext_noop("activity.label", "No data"),
    },
    "people": {
        "card-title": pgettext_noop("people.label", "People"),
        "period-badge": pgettext_noop("people.label", "Lifetime"),
        "people-title": pgettext_noop("people.label", "Most time together"),
        "together-heading": pgettext_noop("people.label", "Together"),
        "private-heading": pgettext_noop("people.label", "One-on-one"),
        "empty-people": pgettext_noop("people.label", "No observed time together"),
        "summary-label-0": pgettext_noop("people.label", "People"),
        "summary-label-1": pgettext_noop("people.label", "Together"),
        "summary-label-2": pgettext_noop("people.label", "One-on-one"),
        "summary-label-3": pgettext_noop("people.label", "With bots"),
        "top-bot-title": pgettext_noop("people.label", "Top bot"),
        "recent-heading": pgettext_noop("people.label", "Last 30 days"),
    },
    "xp": {
        "card-title": pgettext_noop("xp.label", "Voice XP"),
        "period-badge": pgettext_noop("xp.label", "Lifetime"),
        "recent-heading": pgettext_noop("xp.label", "Last 30 days"),
        "xp-heading-0": pgettext_noop("xp.label", "Base"),
        "xp-heading-1": pgettext_noop("xp.label", "Bonuses"),
        "xp-heading-2": pgettext_noop("xp.label", "Reductions"),
        "xp-label-solo": pgettext_noop("xp.label", "Solo"),
        "xp-label-social": pgettext_noop("xp.label", "With people"),
        "xp-label-group": pgettext_noop("xp.label", "5+ people"),
        "xp-label-stream": pgettext_noop("xp.label", "Streaming"),
        "xp-label-video": pgettext_noop("xp.label", "Camera"),
        "xp-label-mute": pgettext_noop("xp.label", "Muted"),
        "xp-label-deaf": pgettext_noop("xp.label", "Deafened"),
        "xp-label-cap": pgettext_noop("xp.label", "Bonus limit"),
    },
}


@dataclass(frozen=True, slots=True)
class VoiceMessages:
    """Format display text without changing voice facts, formulas or identifiers."""

    catalog: Catalog

    def date_label(self, value: date, *, year: bool = True) -> str:
        """Use catalog month names independently of the process locale."""
        month = self.catalog.pgettext("month", MONTHS[value.month - 1])
        if year:
            return self.catalog.pgettext("date", "{month} {day:02d}, {year:d}").format(
                day=value.day, month=month, year=value.year
            )
        return self.catalog.pgettext("short_date", "{month} {day:02d}").format(
            day=value.day, month=month
        )

    def tier(self, value: LevelTier) -> str:
        """Translate a display grade while retaining technical enum values."""
        return self.catalog.pgettext("tier", TIER_LABELS[value])

    def duration(self, seconds: float, *, compact: bool = False) -> str:
        """Preserve elapsed-time rounding and existing large-hour abbreviations."""
        if 0 < seconds < 60:
            return self.catalog.pgettext("under_minute", "<1m")
        minutes = int(seconds // 60)
        if compact and minutes >= 60_000:
            hours = minutes / 60
            if hours >= 1_000_000:
                return self.catalog.pgettext("million_hours", "{hours:.1f}M h").format(
                    hours=hours / 1_000_000
                )
            return self.catalog.pgettext("thousand_hours", "{hours:.1f}k h").format(
                hours=hours / 1000
            )
        return self.catalog.pgettext("duration", "{hours:,d}h {minutes:02d}m").format(
            hours=minutes // 60, minutes=minutes % 60
        )

    def scope(self, since: date | None) -> str:
        """Describe the server scope and earliest observed calendar date."""
        if since is None:
            return self.catalog.pgettext(
                "scope_empty", "On this server · no observed history"
            )
        return self.catalog.pgettext(
            "scope", "On this server · history since {date}"
        ).format(date=self.date_label(since))

    def history(self, since: date | None) -> str:
        """Label lifetime observations separately from the recent calendar window."""
        if since is None:
            return self.catalog.pgettext("history_empty", "No observed history")
        return self.catalog.pgettext("history", "History since {date}").format(
            date=self.date_label(since)
        )

    def coverage(
        self, *, percentage: str, scope: Literal["period", "recent", "lifetime"]
    ) -> str:
        """Label observation coverage without deriving it from presence time."""
        match scope:
            case "period":
                template = self.catalog.pgettext("coverage", "Coverage {percentage}")
            case "recent":
                template = self.catalog.pgettext(
                    "recent_coverage", "30-day coverage {percentage}"
                )
            case "lifetime":
                template = self.catalog.pgettext(
                    "lifetime_coverage", "Lifetime coverage {percentage}"
                )
        return template.format(percentage=percentage)

    def recent_people(self, *, duration: str, count: int) -> str:
        """Select a complete plural phrase for recent shared activity."""
        return self.catalog.npgettext(
            "recent_people",
            "{duration} together · {count:,d} person",
            "{duration} together · {count:,d} people",
            count,
        ).format(duration=duration, count=count)

    def remaining_voice(self, hours: Fraction | None) -> str:
        """Format estimated voice time until the next level."""
        if hours is None:
            return ""
        if hours < Fraction(1, 60):
            return self.catalog.pgettext(
                "estimate_short", "<1 min in voice to next level"
            )
        if hours < 1:
            return self.catalog.pgettext(
                "estimate_minutes", "~{minutes:d} min in voice to next level"
            ).format(minutes=round(hours * 60))
        if hours < 10:
            return self.catalog.pgettext(
                "estimate_fractional", "~{hours:.1f} h in voice to next level"
            ).format(hours=float(hours))
        return self.catalog.pgettext(
            "estimate_hours", "~{hours:,d} h in voice to next level"
        ).format(hours=round(hours))

    def attachment(self, *, display_name: str, guild_name: str) -> str:
        """Describe an attachment without translating either display name."""
        return self.catalog.pgettext(
            "attachment", "Voice profile of {display_name} on {guild_name}"
        ).format(display_name=display_name, guild_name=guild_name)

    def labels(
        self, card: Literal["profile", "activity", "people", "xp"]
    ) -> dict[str, str]:
        """Bind translated labels to existing SVG identifiers."""
        return {
            identifier: self.catalog.pgettext(f"{card}.label", source)
            for identifier, source in STATIC_LABELS[card].items()
        }
