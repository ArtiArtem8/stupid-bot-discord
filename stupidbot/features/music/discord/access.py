"""Voice-channel permissions shared by music commands and controls."""

from typing import Literal

import discord

from stupidbot.discord_ui.errors import handle_app_command_error
from stupidbot.discord_ui.exceptions import CommandFeedbackError
from stupidbot.features.music.playback.models import PlaybackAttempt
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import error_context

type MusicAction = Literal["control", "skip", "manage_session", "join"]


def can_control_music(
    user: discord.Member,
    channel_id: int | None,
    *,
    action: MusicAction = "control",
    attempt: PlaybackAttempt | None = None,
) -> bool:
    """Allow listeners, with narrow remote skip and session-management exceptions."""
    if action == "manage_session" and user.guild_permissions.manage_guild:
        return True
    if (
        action == "skip"
        and attempt is not None
        and attempt.entry.requester is not None
        and attempt.entry.requester.user_id == user.id
    ):
        return True
    channel = user.voice.channel if user.voice is not None else None
    if channel is None:
        return False
    return channel.id == channel_id or (action == "join" and channel_id is None)


async def require_music_access(
    interaction: discord.Interaction,
    channel: discord.abc.Connectable | None,
    *,
    action: MusicAction = "control",
    attempt: PlaybackAttempt | None = None,
) -> None:
    """Raise a localized refusal for the command tree's single feedback owner."""
    channel_id = (
        channel.id
        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel))
        else None
    )
    if isinstance(interaction.user, discord.Member) and can_control_music(
        interaction.user, channel_id, action=action, attempt=attempt
    ):
        return
    context = await error_context(interaction)
    messages = catalog("music", context.language)
    raise CommandFeedbackError(
        messages.pgettext(
            "voice_control", "Join the bot's voice channel to use this control."
        )
    )


async def check_music_access(
    interaction: discord.Interaction,
    channel: discord.abc.Connectable | None,
    *,
    action: MusicAction = "control",
    attempt: PlaybackAttempt | None = None,
) -> bool:
    """Route component denials explicitly; components do not use the command tree."""
    try:
        await require_music_access(interaction, channel, action=action, attempt=attempt)
    except CommandFeedbackError as error:
        await handle_app_command_error(interaction, error)
        return False
    return True
