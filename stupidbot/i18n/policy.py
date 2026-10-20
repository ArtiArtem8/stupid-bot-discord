"""Resolve language and calendar policy for the audience of one response."""

from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo


class Language(StrEnum):
    """Languages for which every shipped catalog must be complete."""

    RU = "ru"
    EN = "en"
    KO = "ko"

    @property
    def native_name(self) -> str:
        """Name the language in its own writing system for settings controls."""
        return {Language.RU: "Русский", Language.EN: "English", Language.KO: "한국어"}[
            self
        ]


@dataclass(frozen=True, slots=True)
class PresentationContext:
    """Freeze one response's language and timezone, including later callbacks."""

    language: Language
    timezone: ZoneInfo


def resolve_language(override: Language | None, locale: str | None) -> Language:
    """Prefer explicit settings, then a supported Discord locale, then Russian."""
    if override is not None:
        return override
    if locale in {"en", "en-US", "en-GB"}:
        return Language.EN
    if locale == "ko":
        return Language.KO
    return Language.RU


def resolve_context(
    *,
    language: Language | None,
    timezone: str | None,
    locale: str | None,
    fallback_timezone: ZoneInfo,
) -> PresentationContext:
    """Resolve language and timezone for the audience selected by the caller.

    Personal and guild preferences are deliberately not merged. Only None
    resets an override; malformed timezone identifiers propagate as errors.
    """
    return PresentationContext(
        resolve_language(language, locale),
        fallback_timezone if timezone is None else ZoneInfo(timezone),
    )
