"""Presentation identity and resolved names for static detail cards."""

from __future__ import annotations

from dataclasses import dataclass, field

from stupidbot.features.voice.profile.appearance import LevelAppearance
from stupidbot.features.voice.profile.details import PeopleDetail
from stupidbot.i18n.policy import Language


@dataclass(frozen=True, slots=True)
class DetailIdentity:
    """Keep every reveal visually consistent with the displayed lifetime card."""

    card: CardIdentity
    appearance: LevelAppearance


@dataclass(frozen=True, slots=True)
class PeoplePresentation:
    """Names resolved from Discord caches, keyed by the read model's IDs."""

    detail: PeopleDetail
    names: dict[int, str]
    colors: dict[int, int]


@dataclass(frozen=True, slots=True)
class CardIdentity:
    """Display labels and bounded CDN bytes; avatars may contain animated GIF."""

    display_name: str
    guild_name: str
    avatar_bytes: bytes | None = None
    guild_icon_png: bytes | None = None
    language: Language = field(kw_only=True)
