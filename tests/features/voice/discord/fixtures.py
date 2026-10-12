"""Typed profile fixtures shared by command and detail interaction tests."""

import discord

from stupidbot.features.voice.discord.profile_cog import ProfileRequest, ProfileSnapshot
from stupidbot.features.voice.discord.view import ProfileLook
from stupidbot.features.voice.profile.media import ProfileMedia
from stupidbot.features.voice.profile.presentation import CardIdentity
from stupidbot.features.voice.timeline import VoiceTimeline
from tests.features.voice.fixtures import at
from tests.features.voice.profile.test_media import profile_at


def profile_request(guild: discord.Guild, user: discord.Member) -> ProfileRequest:
    profile = profile_at()
    return ProfileRequest(
        ProfileSnapshot(VoiceTimeline((), (), ()), 0, 0),
        at(1200),
        profile,
        CardIdentity(user.display_name, guild.name),
        ProfileLook(
            user.display_name,
            guild.name,
            profile.appearance,
            user.display_avatar,
            "UTC",
        ),
        ProfileMedia(b"png", "png"),
    )
