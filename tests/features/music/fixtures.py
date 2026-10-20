"""Shared factories for music tests."""

from __future__ import annotations

import mafic

from stupidbot.features.music.playback.models import QueueEntry, TrackRequester


def make_track(identifier: str, *, length: int = 1000) -> mafic.Track:
    return mafic.Track(
        track_id=f"encoded-{identifier}",
        identifier=identifier,
        seekable=True,
        author="artist",
        length=length,
        stream=False,
        position=0,
        title=f"Track {identifier}",
        uri=f"https://example.com/{identifier}",
        artwork_url=None,
        isrc=None,
        source="test",
    )


def make_playlist(name: str, tracks: list[mafic.Track]) -> mafic.Playlist:
    playlist = mafic.Playlist.__new__(mafic.Playlist)
    playlist.name = name
    playlist.selected_track = -1
    playlist.tracks = tracks
    playlist.plugin_info = {}
    return playlist


def make_entry(
    identifier: str,
    *,
    entry_id: int = 1,
    requester_id: int | None = None,
    length: int = 1000,
) -> QueueEntry:
    requester = TrackRequester(requester_id) if requester_id is not None else None
    return QueueEntry(entry_id, make_track(identifier, length=length), requester)
