"""Typed templates for the settings editor and optional reminders."""

from dataclasses import dataclass

from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class PreferenceMessages:
    """Format settings values after gettext has selected the whole message."""

    catalog: Catalog

    def preview(self, *, language: str, timezone: str, local_time: str) -> str:
        """Show the pending values without changing the editor's frozen language."""
        return self.catalog.pgettext(
            "preview",
            "Language: **{language}**\nTimezone: **{timezone}**\nCurrent time: "
            "{local_time}",
        ).format(language=language, timezone=timezone, local_time=local_time)

    def conflict(self, *, language: str, timezone: str) -> str:
        """Report current values after another editor wins a CAS race."""
        return self.catalog.pgettext(
            "conflict",
            "Settings have changed. Current values: {language}, {timezone}. Open the "
            "panel again.",
        ).format(language=language, timezone=timezone)
