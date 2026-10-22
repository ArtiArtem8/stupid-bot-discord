"""Resolve each member's birthday calendar from personal and server timezones."""

from collections.abc import Collection
from datetime import datetime
from zoneinfo import ZoneInfo

from stupidbot.features.preferences.models import PreferenceScope
from stupidbot.features.preferences.repository import PreferencesRepository
from stupidbot.i18n.discord import fallback_timezone


async def member_local_times(
    preferences: PreferencesRepository,
    guild_id: int,
    user_ids: Collection[int],
    now: datetime,
) -> dict[int, datetime]:
    """Return the aware instant ``now`` in each member's timezone.

    A personal override wins; otherwise the server override, then the
    installation fallback applies. Read failures propagate, so callers never
    act on a guessed calendar.
    """
    server = await preferences.get(PreferenceScope.GUILD, guild_id)
    server_zone = (
        fallback_timezone() if server.timezone is None else ZoneInfo(server.timezone)
    )
    personal = await preferences.user_timezones(user_ids)
    local_times: dict[int, datetime] = {}
    for user_id in user_ids:
        zone = ZoneInfo(personal[user_id]) if user_id in personal else server_zone
        local_times[user_id] = now.astimezone(zone)
    return local_times
