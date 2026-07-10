"""Shared factories for music tests."""

from __future__ import annotations

import mafic


def make_track(identifier: str, *, length: int = 1000) -> mafic.Track:
    """Create a Mafic track for music tests."""
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
    """Create a Mafic playlist from already-built test tracks."""
    playlist = mafic.Playlist.__new__(mafic.Playlist)
    playlist.name = name
    playlist.selected_track = -1
    playlist.tracks = tracks
    playlist.plugin_info = {}
    return playlist
