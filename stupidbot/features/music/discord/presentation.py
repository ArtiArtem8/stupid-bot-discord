"""Pure presentation builders for music command and session output."""

from __future__ import annotations

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


def _escape_markdown_text(text: str) -> str:
    """Escape untrusted single-line Discord Markdown text."""
    return discord.utils.escape_markdown(
        " ".join(text.splitlines()),
        ignore_links=False,
    )


def _has_balanced_brackets(text: str) -> bool:
    """Discord accepts nested link-label brackets only when they are balanced."""
    depth = 0
    for character in text:
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _truncate_link_label(text: str, width: int) -> str:
    """Shorten a balanced label, reserving space to close every open bracket."""
    if len(text) <= width:
        return text
    placeholder = "..."
    if width <= len(placeholder):
        return placeholder[:width]
    prefix: list[str] = []
    depth = 0
    for character in text:
        next_depth = depth
        if character == "[":
            next_depth += 1
        elif character == "]":
            next_depth -= 1
        if len(prefix) + 1 + len(placeholder) + next_depth > width:
            break
        prefix.append(character)
        depth = next_depth
    return "".join(prefix) + placeholder + "]" * depth


def format_track_link(
    title: str,
    uri: str | None,
    *,
    max_length: int = 1024,
    max_title_length: int | None = None,
) -> str:
    """Bound a track title and keep its destination intact when it fits.

    Discord displays escaped brackets literally inside link labels. Use balanced
    brackets as-is and preserve their balance when shortening the label. Titles
    with unmatched brackets or nested link syntax remain plain text, as do links
    whose destination cannot fit. Limits count the emitted Markdown characters.
    """
    escaped_title = _escape_markdown_text(title)
    title_budget = (
        max_length if max_title_length is None else min(max_length, max_title_length)
    )
    if (
        not uri
        or len(uri) + 5 >= max_length
        or not _has_balanced_brackets(escaped_title)
        or "](" in escaped_title
    ):
        return truncate_text(escaped_title, title_budget)
    label = _truncate_link_label(
        escaped_title, min(title_budget, max_length - len(uri) - 4)
    )
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


def _format_track_group(group: _TrackGroup, *, max_length: int) -> str:
    """Format one grouped track for a session summary."""
    status_marker = "~~" if group.skipped else ""
    count_str = f" **×{group.count}**" if group.count > 1 else ""
    track_str = format_track_link(
        group.title,
        group.uri,
        max_length=max_length - 2 * len(status_marker) - len(count_str),
        max_title_length=45,
    )
    return f"{status_marker}{track_str}{count_str}{status_marker}"


def _format_recent_tracks(
    tracks: Sequence[TrackInfo], *, language: Language = Language.RU
) -> tuple[str, int]:
    """Format recent session tracks with consecutive duplicates grouped."""
    messages = MusicMessages(catalog("music", language))
    grouped = _group_consecutive_tracks(tracks)
    placeholder = "\n..."
    # Reserve the omission marker before building links, so truncation only drops
    # whole groups and never cuts a URL or a closing Markdown delimiter.
    group_budget = MAX_EMBED_FIELD_LENGTH
    if len(grouped) > 1:
        group_budget -= len(placeholder)
    formatted_groups = [
        _format_track_group(group, max_length=group_budget) for group in grouped
    ]
    result = truncate_sequence(
        reversed(formatted_groups),
        max_length=MAX_EMBED_FIELD_LENGTH,
        separator="\n",
        placeholder=placeholder,
    )
    return (
        result or messages.catalog.pgettext("ui", "*(empty)*"),
        len(formatted_groups),
    )


def build_session_summary_embed(
    session: MusicSession, *, language: Language = Language.RU
) -> discord.Embed:
    """Build the summary shown when a music session ends."""
    messages = MusicMessages(catalog("music", language))
    embed = SafeEmbed(
        title=messages.catalog.pgettext("ui", "Session ended"),
        color=Color.INFO,
        timestamp=session.start_time,
    )
    embed.safe_add_field(
        name=messages.catalog.pgettext("ui", "Summary:"),
        value=_format_session_stats(session, language=language),
        inline=True,
    )

    tracks_text, text_lines = _format_recent_tracks(session.tracks, language=language)
    if text_lines == 1:
        embed.set_thumbnail(url=session.tracks[-1].thumbnail_url)
        embed.safe_add_field(
            name=messages.catalog.pgettext("ui", "Track:"),
            value=tracks_text,
            inline=False,
        )
    else:
        embed.safe_add_field(
            name=messages.catalog.pgettext("ui", "Recent tracks:"),
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
        "now": messages.catalog.pgettext("ui", "Now playing"),
        "next": messages.catalog.pgettext("ui", "Added to the front of the queue"),
        "end": messages.catalog.pgettext("ui", "Added to the queue"),
    }
    embed = SafeEmbed(
        title=title_by_placement[data["placement"]],
        description=format_track_link(track.title, track.uri),
        color=Color.INFO,
    )
    if track.artwork_url:
        embed.set_thumbnail(url=track.artwork_url)
    embed.safe_add_field(
        name=messages.catalog.pgettext("ui", "Duration"),
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
        "now": messages.catalog.pgettext("ui", "Playlist started"),
        "next": messages.catalog.pgettext(
            "ui", "Playlist added to the front of the queue"
        ),
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
        name=messages.catalog.pgettext("ui", "Duration"),
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
        title=messages.catalog.pgettext("ui", "Track skipped"),
        description=(
            format_track_link(skipped.title, skipped.uri) if skipped else "???"
        ),
        color=Color.INFO,
    )
    if next_track:
        embed.safe_add_field(
            name=messages.catalog.pgettext("ui", "Up next"),
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
        title=messages.catalog.pgettext("ui", "Track moved to the end"),
        description=format_track_link(moved_track.title, moved_track.uri),
        color=Color.INFO,
    )
    embed.safe_add_field(
        name=messages.catalog.pgettext("ui", "Up next"),
        value=(
            format_track_link(next_track.title, next_track.uri)
            if next_track
            else messages.catalog.pgettext("ui", "*The same track*")
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
            message = messages.catalog.pgettext("ui", "Repeat is **off**")
        case RepeatMode.QUEUE:
            message = messages.catalog.pgettext("ui", "Queue repeat is **on**")
        case RepeatMode.TRACK:
            message = messages.catalog.pgettext("ui", "Track repeat is **on**")
        case _:
            message = messages.catalog.pgettext("ui", "Repeat mode is **unknown**")
    return discord.Embed(
        title=messages.catalog.pgettext("ui", "Repeat mode"),
        description=message,
        color=Color.INFO,
    )


def build_track_exception_embed(
    payload: TrackExceptionPayload, *, language: Language = Language.RU
) -> discord.Embed:
    """Build the user-facing notification for a track exception."""
    messages = MusicMessages(catalog("music", language))
    embed = SafeEmbed(
        title=messages.catalog.pgettext("ui", "Could not play the track"),
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
