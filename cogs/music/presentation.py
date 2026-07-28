"""Pure presentation builders for music command and session output."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from itertools import groupby

import discord

import config
from api.music import MusicSession, RepeatMode, Track, TrackInfo
from api.music.models import (
    PlaylistResponseData,
    TrackExceptionPayload,
    TrackResponseData,
)
from utils import truncate_sequence, truncate_text

MAX_TIMEDELTA_DAYS = 999_999_999
_MARKDOWN_LINK_BRACKET_RE = re.compile(r"([\[\]])")


@dataclass(frozen=True, slots=True)
class _TrackGroup:
    """Consecutive tracks grouped only for session-summary presentation."""

    title: str
    uri: str
    skipped: bool
    count: int


def format_duration(ms: int | float) -> str:
    """Convert milliseconds to a timedelta string without microseconds."""
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
        return str(total.days) + " days"
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


def format_track_link(title: str, uri: str | None) -> str:
    """Format a safe track title, linking it only when a URI is available."""
    escaped_title = _escape_markdown_link_label(title)
    if not uri:
        return escaped_title
    return f"[{escaped_title}]({uri})"


def _format_session_stats(session: MusicSession) -> str:
    """Format session statistics."""
    total_tracks = len(session.tracks)
    skipped_tracks = sum(1 for track in session.tracks if track.skipped)

    stats_parts = [f"**Всего:** {total_tracks} шт."]
    if skipped_tracks:
        stats_parts.append(f" (скипов: {skipped_tracks})")

    if len(session.participants) == 1:
        stats_parts.append(f"\n**Заказчик:** <@{next(iter(session.participants))}>")
    else:
        stats_parts.append(f"\n**Заказчиков:** {len(session.participants)} чел.")
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


def _format_recent_tracks(tracks: Sequence[TrackInfo]) -> tuple[str, int]:
    """Format recent session tracks with consecutive duplicates grouped."""
    grouped = _group_consecutive_tracks(tracks)
    formatted_groups = [_format_track_group(group) for group in grouped]
    result = truncate_sequence(
        reversed(formatted_groups),
        max_length=config.MAX_EMBED_FIELD_LENGTH,
        separator="\n",
        placeholder="\n...",
    )
    return (result or "*(пусто)*", len(formatted_groups))


def build_session_summary_embed(session: MusicSession) -> discord.Embed:
    """Build the summary shown when a music session ends."""
    embed = discord.Embed(
        title="Сессия закончена",
        color=config.Color.INFO,
        timestamp=session.start_time,
    )
    embed.add_field(
        name="В общем:",
        value=_format_session_stats(session),
        inline=True,
    )

    tracks_text, text_lines = _format_recent_tracks(session.tracks)
    if text_lines == 1:
        embed.set_thumbnail(url=session.tracks[-1].thumbnail_url)
        embed.add_field(name="Трек:", value=tracks_text, inline=False)
    else:
        embed.add_field(name="Недавние треки:", value=tracks_text, inline=False)
    return embed


def build_track_added_embed(
    data: TrackResponseData,
    *,
    requester_name: str,
    requester_avatar_url: str,
) -> discord.Embed:
    """Build feedback for an added or immediately started track."""
    track = data["track"]
    title_by_placement = {
        "now": "Сейчас играет",
        "next": "Добавлено в начало очереди",
        "end": "Добавлено в очередь",
    }
    embed = discord.Embed(
        title=title_by_placement[data["placement"]],
        description=format_track_link(track.title, track.uri),
        color=config.Color.INFO,
    )
    if track.artwork_url:
        embed.set_thumbnail(url=track.artwork_url)
    embed.add_field(name="Длительность", value=format_duration(track.length))
    embed.set_footer(
        text=f"Запросил: {requester_name}",
        icon_url=requester_avatar_url,
    )
    return embed


def build_playlist_added_embed(
    data: PlaylistResponseData,
    *,
    requester_name: str,
    requester_avatar_url: str,
) -> discord.Embed:
    """Build feedback for an added or immediately started playlist."""
    playlist = data["playlist"]
    playlist_name = _escape_markdown_text(playlist.name)
    title_by_placement = {
        "now": "Плейлист запущен",
        "next": "Плейлист добавлен в начало очереди",
        "end": f"Добавлен плейлист **{playlist_name}**",
    }
    description = f"Треков: {len(playlist.tracks)}"
    if data["placement"] != "end":
        description = f"**{playlist_name}**\n{description}"
    embed = discord.Embed(
        title=title_by_placement[data["placement"]],
        description=description,
        color=config.Color.INFO,
    )
    duration = sum(track.length for track in playlist.tracks)
    embed.add_field(name="Длительность", value=format_duration(duration))
    if playlist.tracks:
        embed.set_thumbnail(url=playlist.tracks[0].artwork_url or "")
    embed.set_footer(
        text=f"Запросил: {requester_name}",
        icon_url=requester_avatar_url,
    )
    return embed


def build_skip_embed(
    skipped: Track | None,
    next_track: Track | None,
) -> discord.Embed:
    """Build feedback for a successful skip."""
    embed = discord.Embed(
        title="Трек пропущен",
        description=(
            format_track_link(skipped.title, skipped.uri) if skipped else "???"
        ),
        color=config.Color.INFO,
    )
    if next_track:
        embed.add_field(
            name="Далее",
            value=format_track_link(next_track.title, next_track.uri),
            inline=False,
        )
    embed.set_thumbnail(url=skipped.artwork_url if skipped else None)
    return embed


def build_rotate_embed(
    moved_track: Track,
    next_track: Track | None,
) -> discord.Embed:
    """Build feedback for moving the current track to the queue end."""
    embed = discord.Embed(
        title="Трек перемещён в конец",
        description=format_track_link(moved_track.title, moved_track.uri),
        color=config.Color.INFO,
    )
    embed.add_field(
        name="Далее",
        value=(
            format_track_link(next_track.title, next_track.uri)
            if next_track
            else "*Тот же самый трек*"
        ),
        inline=False,
    )
    embed.set_thumbnail(url=moved_track.artwork_url)
    return embed


def build_repeat_embed(new_mode: RepeatMode | str | None) -> discord.Embed:
    """Build feedback for a repeat-mode change."""
    match new_mode:
        case RepeatMode.OFF:
            message = "Повтор **отключён**"
        case RepeatMode.QUEUE:
            message = "Повтор очереди **включён**"
        case RepeatMode.TRACK:
            message = "Повтор трека **включён**"
        case _:
            message = "Режим повтора **неизвестен**"
    color = config.Color.WARNING if new_mode is RepeatMode.OFF else config.Color.SUCCESS
    return discord.Embed(
        title="Залупливание",
        description=message,
        color=color,
    )


def build_track_exception_embed(payload: TrackExceptionPayload) -> discord.Embed:
    """Build the user-facing notification for a track exception."""
    embed = discord.Embed(
        title="Не удалось воспроизвести трек",
        description=(
            f"{format_track_link(payload.track.title, payload.track.uri)}\n"
            "**Причина:** Источник временно недоступен или не ответил."
        ),
        color=config.Color.WARNING,
    )
    if payload.track.artwork_url:
        embed.set_thumbnail(url=payload.track.artwork_url)
    return embed
