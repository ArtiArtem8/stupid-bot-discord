"""Presentation identity and resolved names for static detail cards."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from api.progression.appearance import LevelAppearance
from api.voice.profile.details import PeopleDetail
from cogs.voice.profile.design import CardIdentity

if TYPE_CHECKING:
    import discord


@dataclass(frozen=True, slots=True)
class ProfileLook:
    """Displayed identity retained by the View; contains no image bytes."""

    display_name: str
    guild_name: str
    appearance: LevelAppearance
    avatar: "discord.Asset"
    timezone_label: str


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
