"""Typed profile fixtures shared by command and detail interaction tests."""

import discord

from api.voice.timeline import VoiceTimeline
from cogs.voice.profile.design import CardIdentity
from cogs.voice.profile.detail_models import ProfileLook
from cogs.voice.profile.media import ProfileMedia
from cogs.voice.profile_cog import ProfileRequest, ProfileSnapshot
from tests.api.voice.examples import at
from tests.cogs.voice.profile.test_media import profile_at


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
