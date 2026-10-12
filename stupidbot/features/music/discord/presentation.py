"""Pure presentation builders for music command and session output."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from itertools import groupby

import discord

from stupidbot.common.text import truncate_sequence, truncate_text
from stupidbot.discord_ui.embeds import DEFAULT_LIMITS, SafeEmbed
from stupidbot.discord_ui.limits import MAX_EMBED_FIELD_LENGTH
from stupidbot.discord_ui.theme import Color
from stupidbot.features.music.contracts import TrackExceptionPayload
from stupidbot.features.music.messages import MusicMessages
from stupidbot.features.music.playback.models import RepeatMode, Track
from stupidbot.features.music.results import PlaylistResponseData, TrackResponseData
from stupidbot.features.music.runtime.session import MusicSession, TrackInfo
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.policy import Language

MAX_TIMEDELTA_DAYS = 999_999_999
_MARKDOWN_LINK_BRACKET_RE = re.compile(r"([\[\]])")


@dataclass(frozen=True, slots=True)
class _TrackGroup:
    """Consecutive tracks grouped only for session-summary presentation."""

    title: str
    uri: str
    skipped: bool
    count: int


def format_duration(ms: float, *, language: Language = Language.RU) -> str:
    """Convert milliseconds to a timedelta string without microseconds."""
    messages = MusicMessages(catalog("music", language))
    try:
        total = timedelta(seconds=ms / 1_000.0)
    except OverflowError:
        total = timedelta(days=min(MAX_TIMEDELTA_DAYS, ms // 86_400_000))
    except ValueError:
        return "NaN"
    total -= timedelta(microseconds=total.microseconds)
    if total.days >= MAX_TIMEDELTA_DAYS - 1_000_000:
        return "∞"
    if total.days >= 14:
        return messages.days(count=total.days)
    if total.days:
        return (
            f"{messages.days(count=total.days)}, {total - timedelta(days=total.days)}"
        )
    return str(total)


def _normalize_inline_text(text: str) -> str:
    """Collapse line breaks in user-controlled inline text."""
    return " ".join(text.splitlines())


def _escape_markdown_text(text: str) -> str:
    """Escape untrusted single-line Discord Markdown text."""
    return discord.utils.escape_markdown(
        _normalize_inline_text(text),
        ignore_links=False,
    )


def _escape_markdown_link_label(text: str) -> str:
    """Escape untrusted text embedded inside a Markdown link label."""
    normalized = _normalize_inline_text(text)
    parts = _MARKDOWN_LINK_BRACKET_RE.split(normalized)

    return "".join(
        f"\\{part}"
        if part in ("[", "]")
        else discord.utils.escape_markdown(part, ignore_links=False)
        for part in parts
    )


def format_track_link(title: str, uri: str | None, *, max_length: int = 1024) -> str:
    """Bound a track label while keeping its link intact when it fits."""
    escaped_title = _escape_markdown_link_label(title)
    if not uri or len(uri) + 5 >= max_length:
        return truncate_text(escaped_title, max_length)
    label = truncate_text(escaped_title, max_length - len(uri) - 4)
    return f"[{label}]({uri})"


def _format_session_stats(
    session: MusicSession, *, language: Language = Language.RU
) -> str:
    """Format session statistics."""
    messages = MusicMessages(catalog("music", language))
    total_tracks = len(session.tracks)
    skipped_tracks = sum(1 for track in session.tracks if track.skipped)

    stats_parts = [messages.session_tracks(count=total_tracks)]
    if skipped_tracks:
        stats_parts.append(messages.session_skips(count=skipped_tracks))

    if len(session.participants) == 1:
        stats_parts.append(
            messages.session_requester(user_id=next(iter(session.participants)))
        )
    else:
        stats_parts.append(messages.session_people(count=len(session.participants)))
    return "".join(stats_parts)


def _group_consecutive_tracks(tracks: Sequence[TrackInfo]) -> list[_TrackGroup]:
    """Group consecutive tracks with the same title, URI, and skipped state."""

    def key(track: TrackInfo) -> tuple[str, str, bool]:
        return (track.title, track.uri, track.skipped)

    return [
        _TrackGroup(title, uri, skipped, count=sum(1 for _ in group))
        for (title, uri, skipped), group in groupby(tracks, key)
    ]


def _format_track_group(group: _TrackGroup) -> str:
    """Format one grouped track for a session summary."""
    status_marker = "~~" if group.skipped else ""
    count_str = f" **×{group.count}**" if group.count > 1 else ""
    truncated_title = truncate_text(group.title, 45, placeholder="...")
    track_str = format_track_link(truncated_title, group.uri)
    return f"{status_marker}{track_str}{count_str}{status_marker}"


def _format_recent_tracks(
    tracks: Sequence[TrackInfo], *, language: Language = Language.RU
) -> tuple[str, int]:
    """Format recent session tracks with consecutive duplicates grouped."""
    messages = MusicMessages(catalog("music", language))
    grouped = _group_consecutive_tracks(tracks)
    formatted_groups = [_format_track_group(group) for group in grouped]
    result = truncate_sequence(
        reversed(formatted_groups),
        max_length=MAX_EMBED_FIELD_LENGTH,
        separator="\n",
        placeholder="\n...",
    )
    return (
        result or messages.catalog.pgettext("ui", "*(пусто)*"),
        len(formatted_groups),
    )


def build_session_summary_embed(
    session: MusicSession, *, language: Language = Language.RU
) -> discord.Embed:
    """Build the summary shown when a music session ends."""
    messages = MusicMessages(catalog("music", language))
    embed = SafeEmbed(
        title=messages.catalog.pgettext("ui", "Сессия закончена"),
        color=Color.INFO,
        timestamp=session.start_time,
    )
    embed.safe_add_field(
        name=messages.catalog.pgettext("ui", "В общем:"),
        value=_format_session_stats(session, language=language),
        inline=True,
    )

    tracks_text, text_lines = _format_recent_tracks(session.tracks, language=language)
    if text_lines == 1:
        embed.set_thumbnail(url=session.tracks[-1].thumbnail_url)
        embed.safe_add_field(
            name=messages.catalog.pgettext("ui", "Трек:"),
            value=tracks_text,
            inline=False,
        )
    else:
        embed.safe_add_field(
            name=messages.catalog.pgettext("ui", "Недавние треки:"),
            value=tracks_text,
            inline=False,
        )
    return embed


def build_track_added_embed(
    data: TrackResponseData,
    *,
    requester_name: str,
    requester_avatar_url: str,
    language: Language = Language.RU,
) -> discord.Embed:
    """Build feedback for an added or immediately started track."""
    messages = MusicMessages(catalog("music", language))
    track = data["track"]
    title_by_placement = {
        "now": messages.catalog.pgettext("ui", "Сейчас играет"),
        "next": messages.catalog.pgettext("ui", "Добавлено в начало очереди"),
        "end": messages.catalog.pgettext("ui", "Добавлено в очередь"),
    }
    embed = SafeEmbed(
        title=title_by_placement[data["placement"]],
        description=format_track_link(track.title, track.uri),
        color=Color.INFO,
    )
    if track.artwork_url:
        embed.set_thumbnail(url=track.artwork_url)
    embed.safe_add_field(
        name=messages.catalog.pgettext("ui", "Длительность"),
        value=format_duration(track.length, language=language),
    )
    embed.set_footer(
        text=messages.requester(name=requester_name),
        icon_url=requester_avatar_url,
    )
    return embed


def build_playlist_added_embed(
    data: PlaylistResponseData,
    *,
    requester_name: str,
    requester_avatar_url: str,
    language: Language = Language.RU,
) -> discord.Embed:
    """Build feedback for an added or immediately started playlist."""
    messages = MusicMessages(catalog("music", language))
    playlist = data["playlist"]
    playlist_name = truncate_text(
        _escape_markdown_text(playlist.name),
        DEFAULT_LIMITS.title - len(messages.playlist_added(name="")),
    )
    title_by_placement = {
        "now": messages.catalog.pgettext("ui", "Плейлист запущен"),
        "next": messages.catalog.pgettext("ui", "Плейлист добавлен в начало очереди"),
        "end": messages.playlist_added(name=playlist_name),
    }
    description = messages.track_count(count=len(playlist.tracks))
    if data["placement"] != "end":
        description = f"**{playlist_name}**\n{description}"
    embed = SafeEmbed(
        title=title_by_placement[data["placement"]],
        description=description,
        color=Color.INFO,
    )
    duration = sum(track.length for track in playlist.tracks)
    embed.safe_add_field(
        name=messages.catalog.pgettext("ui", "Длительность"),
        value=format_duration(duration, language=language),
    )
    if playlist.tracks:
        embed.set_thumbnail(url=playlist.tracks[0].artwork_url or "")
    embed.set_footer(
        text=messages.requester(name=requester_name),
        icon_url=requester_avatar_url,
    )
    return embed


def build_skip_embed(
    skipped: Track | None, next_track: Track | None, *, language: Language = Language.RU
) -> discord.Embed:
    """Build feedback for a successful skip."""
    messages = MusicMessages(catalog("music", language))
    embed = SafeEmbed(
        title=messages.catalog.pgettext("ui", "Трек пропущен"),
        description=(
            format_track_link(skipped.title, skipped.uri) if skipped else "???"
        ),
        color=Color.INFO,
    )
    if next_track:
        embed.safe_add_field(
            name=messages.catalog.pgettext("ui", "Далее"),
            value=format_track_link(next_track.title, next_track.uri),
            inline=False,
        )
    embed.set_thumbnail(url=skipped.artwork_url if skipped else None)
    return embed


def build_rotate_embed(
    moved_track: Track, next_track: Track | None, *, language: Language = Language.RU
) -> discord.Embed:
    """Build feedback for moving the current track to the queue end."""
    messages = MusicMessages(catalog("music", language))
    embed = SafeEmbed(
        title=messages.catalog.pgettext("ui", "Трек перемещён в конец"),
        description=format_track_link(moved_track.title, moved_track.uri),
        color=Color.INFO,
    )
    embed.safe_add_field(
        name=messages.catalog.pgettext("ui", "Далее"),
        value=(
            format_track_link(next_track.title, next_track.uri)
            if next_track
            else messages.catalog.pgettext("ui", "*Тот же самый трек*")
        ),
        inline=False,
    )
    embed.set_thumbnail(url=moved_track.artwork_url)
    return embed


def build_repeat_embed(
    new_mode: RepeatMode | str | None, *, language: Language = Language.RU
) -> discord.Embed:
    """Build feedback for a repeat-mode change."""
    messages = MusicMessages(catalog("music", language))
    match new_mode:
        case RepeatMode.OFF:
            message = messages.catalog.pgettext("ui", "Повтор **отключён**")
        case RepeatMode.QUEUE:
            message = messages.catalog.pgettext("ui", "Повтор очереди **включён**")
        case RepeatMode.TRACK:
            message = messages.catalog.pgettext("ui", "Повтор трека **включён**")
        case _:
            message = messages.catalog.pgettext("ui", "Режим повтора **неизвестен**")
    return discord.Embed(
        title=messages.catalog.pgettext("ui", "Залупливание"),
        description=message,
        color=Color.INFO,
    )


def build_track_exception_embed(
    payload: TrackExceptionPayload, *, language: Language = Language.RU
) -> discord.Embed:
    """Build the user-facing notification for a track exception."""
    messages = MusicMessages(catalog("music", language))
    embed = SafeEmbed(
        title=messages.catalog.pgettext("ui", "Не удалось воспроизвести трек"),
        description=(
            messages.track_exception(
                link=format_track_link(payload.track.title, payload.track.uri)
            )
        ),
        color=Color.WARNING,
    )
    if payload.track.artwork_url:
        embed.set_thumbnail(url=payload.track.artwork_url)
    return embed
