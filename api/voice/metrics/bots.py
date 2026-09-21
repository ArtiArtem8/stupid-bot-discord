"""Bot co-presence, without inferring playback from presence."""

from api.voice.read_models import BotStat
from api.voice.scope import GLOBAL_SCOPE, VoiceScope, observed_rooms
from api.voice.timeline import VoiceTimeline


def bot_presence(
    timeline: VoiceTimeline, user_id: int, scope: VoiceScope = GLOBAL_SCOPE
) -> BotStat:
    """Return time with any bot and each specific bot for a human user."""
    any_bot = 0.0
    totals: dict[int, float] = {}
    for room in observed_rooms(timeline, scope):
        if user_id not in room.humans or not room.bots:
            continue
        any_bot += room.seconds
        for bot_id in room.bots:
            totals[bot_id] = totals.get(bot_id, 0.0) + room.seconds
    return BotStat(any_bot, tuple(sorted(totals.items())))
