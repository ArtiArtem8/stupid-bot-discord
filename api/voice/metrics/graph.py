"""Rebuildable human social edges; no graph storage or algorithms."""

from itertools import combinations

from api.voice.scope import GLOBAL_SCOPE, VoiceScope, observed_rooms
from api.voice.timeline import VoiceTimeline


def shared_seconds(
    timeline: VoiceTimeline, scope: VoiceScope = GLOBAL_SCOPE
) -> dict[tuple[int, int], float]:
    """Return canonical (smaller ID, larger ID) edges weighted in seconds."""
    edges: dict[tuple[int, int], float] = {}
    for room in observed_rooms(timeline, scope):
        for pair in combinations(room.humans, 2):
            edges[pair] = edges.get(pair, 0.0) + room.seconds
    return edges
