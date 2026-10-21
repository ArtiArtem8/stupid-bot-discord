"""Typed templates for the settings editor and optional reminders."""

from dataclasses import dataclass

from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class PreferenceMessages:
    """Format settings values after gettext has selected the whole message."""

    catalog: Catalog

    def reminder(self, *, command: str) -> str:
        """Invite personal setup with a registered command mention when available."""
        return self.catalog.pgettext(
            "reminder",
            "Choose a language and timezone for personal replies and your "
            "voice profile in {command}.",
        ).format(command=command)

    def reminder_answer(self, *, command: str, never: bool) -> str:
        """Keep the settings command available after dismissing the controls."""
        template = (
            self.catalog.pgettext(
                "reminder_disabled",
                "I will not remind you again. Settings are available in {command}.",
            )
            if never
            else self.catalog.pgettext(
                "reminder_later",
                "Okay. Settings are available in {command} whenever you need them.",
            )
        )
        return template.format(command=command)

    def preview(self, *, language: str, timezone: str, local_time: str) -> str:
        """Show the pending values without changing the editor's frozen language."""
        return self.catalog.pgettext(
            "preview",
            "Language: **{language}**\nTimezone: **{timezone}**\nCurrent time: "
            "{local_time}",
        ).format(language=language, timezone=timezone, local_time=local_time)

    def conflict(self, *, language: str, timezone: str) -> str:
        """Report the saved values when another editor has already changed them."""
        return self.catalog.pgettext(
            "conflict",
            "Settings have changed. Current values: {language}, {timezone}. Open the "
            "panel again.",
        ).format(language=language, timezone=timezone)
