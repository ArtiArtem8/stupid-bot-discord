"""Typed voice-profile labels, durations and calendar presentation."""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Literal

from stupidbot.features.voice.profile.appearance import LevelTier
from stupidbot.i18n.catalog import Catalog, pgettext_noop

MONTHS = (
    pgettext_noop("month", "янв."),
    pgettext_noop("month", "февр."),
    pgettext_noop("month", "мар."),
    pgettext_noop("month", "апр."),
    pgettext_noop("month", "мая"),
    pgettext_noop("month", "июн."),
    pgettext_noop("month", "июл."),
    pgettext_noop("month", "авг."),
    pgettext_noop("month", "сен."),
    pgettext_noop("month", "окт."),
    pgettext_noop("month", "ноя."),
    pgettext_noop("month", "дек."),
)
WEEKDAYS = (
    pgettext_noop("weekday", "Понедельник"),
    pgettext_noop("weekday", "Вторник"),
    pgettext_noop("weekday", "Среда"),
    pgettext_noop("weekday", "Четверг"),
    pgettext_noop("weekday", "Пятница"),
    pgettext_noop("weekday", "Суббота"),
    pgettext_noop("weekday", "Воскресенье"),
)
WEEKDAY_ABBR = (
    pgettext_noop("weekday_short", "Пн"),
    pgettext_noop("weekday_short", "Вт"),
    pgettext_noop("weekday_short", "Ср"),
    pgettext_noop("weekday_short", "Чт"),
    pgettext_noop("weekday_short", "Пт"),
    pgettext_noop("weekday_short", "Сб"),
    pgettext_noop("weekday_short", "Вс"),
)
TIER_LABELS = {
    LevelTier.STARTER: pgettext_noop("tier", "НАЧАЛЬНЫЙ"),
    LevelTier.UNCOMMON: pgettext_noop("tier", "НЕОБЫЧНЫЙ"),
    LevelTier.RARE: pgettext_noop("tier", "РЕДКИЙ"),
    LevelTier.EPIC: pgettext_noop("tier", "ЭПИЧЕСКИЙ"),
    LevelTier.MYTHIC: pgettext_noop("tier", "МИФИЧЕСКИЙ"),
    LevelTier.LEGENDARY: pgettext_noop("tier", "ЛЕГЕНДАРНЫЙ"),
    LevelTier.ASCENDANT: pgettext_noop("tier", "ВОЗНЕСЁННЫЙ"),
    LevelTier.TRANSCENDENT: pgettext_noop("tier", "ЗАПРЕДЕЛЬНЫЙ"),
}


STATIC_LABELS: dict[str, dict[str, str]] = {
    "profile": {
        "level-prefix": pgettext_noop("profile.label", "УР."),
        "text5": pgettext_noop("profile.label", "ПРОГРЕСС УРОВНЯ"),
        "text6": pgettext_noop("profile.label", "ВРЕМЯ В ГОЛОСЕ"),
        "text7": pgettext_noop("profile.label", "СЕССИИ"),
    },
    "activity": {
        "card-title": pgettext_noop("activity.label", "Активность"),
        "period-badge": pgettext_noop("activity.label", "Последние 30 дней"),
        "metric-label-0": pgettext_noop("activity.label", "Всего в голосе"),
        "metric-label-1": pgettext_noop("activity.label", "С людьми"),
        "metric-label-2": pgettext_noop("activity.label", "В одиночку"),
        "metric-label-3": pgettext_noop("activity.label", "Сессии"),
        "stat-label-0": pgettext_noop("activity.label", "Средняя сессия"),
        "stat-label-1": pgettext_noop("activity.label", "Медианная сессия"),
        "stat-label-2": pgettext_noop("activity.label", "Часы пик"),
        "stat-label-3": pgettext_noop("activity.label", "Активные дни"),
        "chart-title": pgettext_noop("activity.label", "Активность по дням"),
        "clock-note": pgettext_noop("activity.label", "* Смена часов"),
        "voice-legend": pgettext_noop("activity.label", "В голосе"),
        "missing-legend": pgettext_noop("activity.label", "Нет данных"),
    },
    "people": {
        "card-title": pgettext_noop("people.label", "Люди"),
        "period-badge": pgettext_noop("people.label", "За всё время"),
        "people-title": pgettext_noop("people.label", "Больше всего времени вместе"),
        "together-heading": pgettext_noop("people.label", "Вместе"),
        "private-heading": pgettext_noop("people.label", "Вдвоём"),
        "empty-people": pgettext_noop(
            "people.label", "Совместное время не наблюдалось"
        ),
        "summary-label-0": pgettext_noop("people.label", "Люди"),
        "summary-label-1": pgettext_noop("people.label", "Вместе"),
        "summary-label-2": pgettext_noop("people.label", "Вдвоём"),
        "summary-label-3": pgettext_noop("people.label", "С ботами"),
        "top-bot-title": pgettext_noop("people.label", "Чаще всего с ботом"),
        "recent-heading": pgettext_noop("people.label", "Последние 30 дней"),
    },
    "xp": {
        "card-title": pgettext_noop("xp.label", "Голосовой XP"),
        "period-badge": pgettext_noop("xp.label", "За всё время"),
        "recent-heading": pgettext_noop("xp.label", "Последние 30 дней"),
        "xp-heading-0": pgettext_noop("xp.label", "База"),
        "xp-heading-1": pgettext_noop("xp.label", "Бонусы"),
        "xp-heading-2": pgettext_noop("xp.label", "Снижения"),
        "xp-label-solo": pgettext_noop("xp.label", "В одиночку"),
        "xp-label-social": pgettext_noop("xp.label", "С людьми"),
        "xp-label-group": pgettext_noop("xp.label", "5+ человек"),
        "xp-label-stream": pgettext_noop("xp.label", "Трансляция"),
        "xp-label-video": pgettext_noop("xp.label", "Камера"),
        "xp-label-mute": pgettext_noop("xp.label", "Микрофон выкл."),
        "xp-label-deaf": pgettext_noop("xp.label", "Звук выкл."),
        "xp-label-cap": pgettext_noop("xp.label", "Лимит бонусов"),
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
            return self.catalog.pgettext("date", "{day:02d} {month} {year:d}").format(
                day=value.day, month=month, year=value.year
            )
        return self.catalog.pgettext("short_date", "{day:02d} {month}").format(
            day=value.day, month=month
        )

    def tier(self, value: LevelTier) -> str:
        """Translate a display grade while retaining technical enum values."""
        return self.catalog.pgettext("tier", TIER_LABELS[value])

    def duration(self, seconds: float, *, compact: bool = False) -> str:
        """Preserve elapsed-time rounding and existing large-hour abbreviations."""
        if 0 < seconds < 60:
            return self.catalog.pgettext("under_minute", "<1м")
        minutes = int(seconds // 60)
        if compact and minutes >= 60_000:
            hours = minutes / 60
            if hours >= 1_000_000:
                return self.catalog.pgettext(
                    "million_hours", "{hours:.1f} млн ч"
                ).format(hours=hours / 1_000_000)
            return self.catalog.pgettext("thousand_hours", "{hours:.1f} тыс ч").format(
                hours=hours / 1000
            )
        return self.catalog.pgettext("duration", "{hours:,d}ч {minutes:02d}м").format(
            hours=minutes // 60, minutes=minutes % 60
        )

    def scope(self, since: date | None) -> str:
        """Describe the server scope and earliest observed calendar date."""
        if since is None:
            return self.catalog.pgettext(
                "scope_empty", "На этом сервере · история не наблюдалась"
            )
        return self.catalog.pgettext(
            "scope", "На этом сервере · история с {date}"
        ).format(date=self.date_label(since))

    def history(self, since: date | None) -> str:
        """Label lifetime observations separately from the recent calendar window."""
        if since is None:
            return self.catalog.pgettext("history_empty", "История не наблюдалась")
        return self.catalog.pgettext("history", "История с {date}").format(
            date=self.date_label(since)
        )

    def coverage(
        self, *, percentage: str, scope: Literal["period", "recent", "lifetime"]
    ) -> str:
        """Label observation coverage without deriving it from presence time."""
        match scope:
            case "period":
                template = self.catalog.pgettext("coverage", "Наблюдение {percentage}")
            case "recent":
                template = self.catalog.pgettext(
                    "recent_coverage", "За 30 дней: {percentage}"
                )
            case "lifetime":
                template = self.catalog.pgettext(
                    "lifetime_coverage", "За всё время: {percentage}"
                )
        return template.format(percentage=percentage)

    def recent_people(self, *, duration: str, count: int) -> str:
        """Select a complete plural phrase for recent shared activity."""
        return self.catalog.npgettext(
            "recent_people",
            "{duration} вместе · {count:,d} человек",
            "{duration} вместе · {count:,d} человека",
            count,
        ).format(duration=duration, count=count)

    def remaining_voice(self, hours: Fraction | None) -> str:
        """Format an existing estimate without changing its prediction model."""
        if hours is None:
            return ""
        if hours < Fraction(1, 60):
            return self.catalog.pgettext(
                "estimate_short", "<1 мин в голосе до следующего уровня"
            )
        if hours < 1:
            return self.catalog.pgettext(
                "estimate_minutes", "~{minutes:d} мин в голосе до следующего уровня"
            ).format(minutes=round(hours * 60))
        if hours < 10:
            return self.catalog.pgettext(
                "estimate_fractional", "~{hours:.1f} ч в голосе до следующего уровня"
            ).format(hours=float(hours))
        return self.catalog.pgettext(
            "estimate_hours", "~{hours:,d} ч в голосе до следующего уровня"
        ).format(hours=round(hours))

    def attachment(self, *, display_name: str, guild_name: str) -> str:
        """Describe an attachment without translating either display name."""
        return self.catalog.pgettext(
            "attachment", "Голосовой профиль {display_name} на сервере {guild_name}"
        ).format(display_name=display_name, guild_name=guild_name)

    def labels(
        self, card: Literal["profile", "activity", "people", "xp"]
    ) -> dict[str, str]:
        """Bind translated labels to existing SVG identifiers."""
        return {
            identifier: self.catalog.pgettext(f"{card}.label", source)
            for identifier, source in STATIC_LABELS[card].items()
        }
