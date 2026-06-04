"""Pure prefix-command suggestion selection."""

from collections.abc import Iterable
from dataclasses import dataclass

from discord import app_commands
from discord.ext import commands
from rapidfuzz import fuzz, process
from rapidfuzz.utils import default_process

type Command = (
    app_commands.ContextMenu
    | app_commands.Command[app_commands.Group | commands.Cog, ..., object]
    | app_commands.Group
)


@dataclass(frozen=True)
class Suggestion:
    query: str
    key: str
    root_name: str
    is_guild: bool
    score: float


@dataclass(frozen=True)
class SuggestionPair:
    primary: Suggestion | None = None
    alternative: Suggestion | None = None


def is_guild_command(command: Command) -> bool:
    """Return whether a command is only available in guild contexts."""
    if command.guild_only:
        return True
    contexts = command.allowed_contexts
    return bool(
        contexts
        and contexts.guild
        and not (contexts.dm_channel or contexts.private_channel)
    )


def cutoff_for_query(raw_query: str) -> int:
    """Choose the minimum fuzzy-match score for a raw prefix query."""
    query = default_process(raw_query)
    if len(query) <= 1:
        return 10_000
    if " " not in query and len(query) >= 8:
        return 75
    return 70


def flatten_commands(commands_: Iterable[Command]) -> dict[str, tuple[str, bool]]:
    """Flatten root commands and subcommands into fuzzy-match entries."""
    entries: dict[str, tuple[str, bool]] = {}
    for command in commands_:
        guild_only = is_guild_command(command)
        entries[command.name] = (command.name, guild_only)
        if isinstance(command, app_commands.Group):
            for subcommand in command.commands:
                entries[f"{command.name} {subcommand.name}"] = (
                    command.name,
                    guild_only,
                )
    return entries


def _suggestion(
    query: str, key: str, score: float, entries: dict[str, tuple[str, bool]]
) -> Suggestion:
    root_name, guild_only = entries[key]
    return Suggestion(query, key, root_name, guild_only, float(score))


def _rank_guild_match(
    item: tuple[str, float], entries: dict[str, tuple[str, bool]]
) -> tuple[int, float, int]:
    key, score = item
    _root_name, guild_only = entries[key]
    return (1 if guild_only else 0, score, len(key))


def _select_suggestions(
    query: str,
    kept: list[tuple[str, float]],
    entries: dict[str, tuple[str, bool]],
    *,
    in_guild: bool,
) -> SuggestionPair:
    top_key, top_score = kept[0]
    near = [(key, score) for key, score in kept if top_score - score <= 3.0]
    if in_guild and len(near) >= 2:
        ranked = sorted(
            near, key=lambda item: _rank_guild_match(item, entries), reverse=True
        )
        primary = _suggestion(query, *ranked[0], entries)
        alternative = (
            _suggestion(query, *ranked[1], entries) if len(ranked) >= 2 else None
        )
        return SuggestionPair(primary, alternative)

    primary = _suggestion(query, top_key, top_score, entries)
    alternative = None
    if len(kept) >= 2 and top_score - kept[1][1] <= 8.0:
        alternative = _suggestion(query, *kept[1], entries)
    return SuggestionPair(primary, alternative)


def find_prefix_suggestions(
    raw_query: str, commands_: Iterable[Command], *, in_guild: bool
) -> SuggestionPair:
    """Select the primary and optional alternative slash-command suggestion."""
    entries = flatten_commands(commands_)
    if not entries:
        return SuggestionPair()

    matches = process.extract(
        raw_query,
        list(entries),
        scorer=fuzz.WRatio,
        processor=default_process,
        limit=5,
    )
    cutoff = cutoff_for_query(raw_query)
    kept = [(key, float(score)) for key, score, _index in matches if score >= cutoff]
    if not kept:
        return SuggestionPair()
    return _select_suggestions(raw_query, kept, entries, in_guild=in_guild)
