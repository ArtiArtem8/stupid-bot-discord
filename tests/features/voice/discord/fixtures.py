"""Typed profile fixtures shared by command and detail interaction tests."""

from zoneinfo import ZoneInfo

import discord

from stupidbot.features.voice.discord.profile_cog import ProfileRequest, ProfileSnapshot
from stupidbot.features.voice.discord.view import ProfileLook
from stupidbot.features.voice.profile.media import ProfileMedia
from stupidbot.features.voice.profile.presentation import CardIdentity
from stupidbot.features.voice.timeline import VoiceTimeline
from stupidbot.i18n.policy import Language, PresentationContext
from tests.features.voice.fixtures import at
from tests.features.voice.profile.test_media import profile_at

CONTEXT = PresentationContext(Language.RU, ZoneInfo("UTC"))


def profile_request(
    guild: discord.Guild,
    user: discord.Member,
    *,
    context: PresentationContext = CONTEXT,
) -> ProfileRequest:
    profile = profile_at()
    return ProfileRequest(
        ProfileSnapshot(VoiceTimeline((), (), ()), 0, 0),
        at(1200),
        profile,
        CardIdentity(user.display_name, guild.name, language=context.language),
        ProfileLook(
            user.display_name,
            guild.name,
            profile.appearance,
            user.display_avatar,
            "UTC",
            language=context.language,
        ),
        ProfileMedia(b"png", "png"),
    )
