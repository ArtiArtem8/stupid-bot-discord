"""Discord shell for the current user's server-local voice profile card."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from io import BytesIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

import config
from api.voice.profile.build import build_profile
from api.voice.timeline import VoiceTimeline, build_timeline
from cogs.voice.collector_cog import VoiceCollectorCog
from cogs.voice.profile.animation import render_attachment
from cogs.voice.profile.renderer import CardIdentity
from cogs.voice.profile.view import VoiceProfileView
from repositories.voice_journal import VoiceJournal

logger = logging.getLogger(__name__)


class VoiceProfileCog(commands.Cog):
    """Read the collector's journal and cache persisted guild timelines."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._timeline_cache: dict[int, tuple[int, VoiceTimeline]] = {}
        self._cache_lock = asyncio.Lock()
        self._journal_owner: VoiceJournal | None = None

    @app_commands.command(
        name="voice-profile", description="Ваш голосовой профиль на этом сервере"
    )
    @app_commands.describe(private="Показать карточку только вам")
    @app_commands.guild_only()
    async def voice_profile(
        self, interaction: discord.Interaction, private: bool = False
    ) -> None:
        """Display current user's lifetime guild profile and 14-day chart."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "Команда доступна только на сервере.", ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=private)
        guild = interaction.guild
        user = interaction.user

        async def refresh(button_interaction: discord.Interaction) -> None:
            try:
                attachment = await self._attachment(guild, user)
                await button_interaction.edit_original_response(
                    attachments=[attachment], view=view
                )
            except Exception:
                logger.exception(
                    "Voice profile refresh failed: guild=%d user=%d", guild.id, user.id
                )
                await button_interaction.followup.send(
                    "Не удалось обновить карточку.", ephemeral=True
                )

        view = VoiceProfileView(user.id, refresh)
        try:
            attachment = await self._attachment(guild, user)
            await interaction.edit_original_response(
                attachments=[attachment], view=view
            )
        except Exception:
            logger.exception(
                "Voice profile failed: guild=%d user=%d", guild.id, user.id
            )
            await interaction.delete_original_response()
            await interaction.followup.send(
                "Не удалось построить голосовой профиль.", ephemeral=True
            )

    async def _timeline(self, guild_id: int) -> VoiceTimeline:
        collector = self.bot.get_cog("VoiceCollectorCog")
        if not isinstance(collector, VoiceCollectorCog):
            raise RuntimeError("Voice collector is unavailable")
        journal = collector.journal
        async with self._cache_lock:
            if self._journal_owner is not journal:
                self._timeline_cache.clear()
                self._journal_owner = journal
            generation = journal.counts.persisted
            cached = self._timeline_cache.get(guild_id)
            if cached is not None and cached[0] == generation:
                return cached[1]
            guild_records, session_records = await asyncio.gather(
                journal.read_all(guild_id), journal.read_all(None)
            )
            timeline = await asyncio.to_thread(
                build_timeline, (*guild_records, *session_records)
            )
            self._timeline_cache[guild_id] = (generation, timeline)
            return timeline

    async def _attachment(
        self, guild: discord.Guild, user: discord.User | discord.Member
    ) -> discord.File:
        timezone_name = config.VOICE_PROFILE_TIMEZONE
        try:
            timezone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "Invalid voice profile timezone %s; using UTC", timezone_name
            )
            timezone_name = "UTC"
            timezone = ZoneInfo("UTC")
        timeline = await self._timeline(guild.id)
        profile = await asyncio.to_thread(
            build_profile,
            timeline,
            user.id,
            guild.id,
            datetime.now(UTC),
            timezone,
            timezone_name,
        )
        companion_id = profile.stats.top_companion_id
        companion = guild.get_member(companion_id) if companion_id is not None else None
        if companion is None and companion_id is not None:
            companion = self.bot.get_user(companion_id)
        if isinstance(companion, discord.Member):
            companion_name = companion.display_name
        elif companion is not None:
            companion_name = companion.name
        elif companion_id is not None:
            companion_name = f"…{str(companion_id)[-6:]}"
        else:
            companion_name = "—"
        avatar: bytes | None
        try:
            avatar = await user.display_avatar.with_static_format("png").read()
        except Exception:
            logger.warning("Voice profile avatar unavailable: user=%d", user.id)
            logger.debug("Avatar read traceback", exc_info=True)
            avatar = None
        identity = CardIdentity(
            user.name, user.display_name, guild.name, companion_name, avatar
        )
        image, extension = await asyncio.to_thread(render_attachment, profile, identity)
        description = (
            f"{user.display_name}, level {profile.level} "
            f"{profile.appearance.tier.value}, {profile.total_xp:,} XP, "
            f"{round(profile.stats.total_voice_seconds / 3600, 1)} hours in voice"
        )
        return discord.File(
            BytesIO(image),
            filename=f"voice-profile.{extension}",
            description=description,
        )


async def setup(bot: commands.Bot) -> None:
    """Load the voice profile command independently of collector load order."""
    await bot.add_cog(VoiceProfileCog(bot))
