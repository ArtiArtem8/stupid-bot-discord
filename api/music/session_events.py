"""Domain helpers for music session lifecycle events."""

from discord.ext import commands

from api.music.models import MusicSession


def main_session_channel_id(session: MusicSession) -> int | None:
    """Return the text channel with the most interactions in the session."""
    if not session.channel_usage:
        return None
    return max(session.channel_usage, key=session.channel_usage.__getitem__)


def dispatch_music_session_end(
    bot: commands.Bot, guild_id: int, session: MusicSession | None
) -> None:
    """Dispatch a completed music session when it has reportable activity."""
    if session is None or not session.tracks:
        return
    channel_id = main_session_channel_id(session)
    if channel_id is None:
        return
    bot.dispatch("music_session_end", guild_id, session, channel_id)
