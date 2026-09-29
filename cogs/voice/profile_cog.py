"""Discord orchestration and lifetime owner of voice profile media."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from typing import override
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

import config
from api.voice.profile.build import build_profile
from api.voice.timeline import VoiceTimeline, build_timeline
from cogs.voice.profile.cache import MediaKey, ProfileMediaCache
from cogs.voice.profile.design import CardIdentity
from cogs.voice.profile.media import (
    MEDIA_LIMIT,
    ProfileMedia,
    ProfileMediaRenderer,
    RenderBusyError,
)
from cogs.voice.profile.view import VoiceProfileView
from framework.feedback_ui import FeedbackType, FeedbackUI
from repositories.voice_journal import VoiceJournal

logger = logging.getLogger(__name__)
_TIMELINE_CACHE_ENTRIES = 4
_DATA_TIMEOUT = 10
_ASSET_TIMEOUT = 5


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    """An immutable timeline paired with its owner and persisted generation."""

    timeline: VoiceTimeline
    epoch: int
    generation: int


class VoiceProfileCog(commands.Cog):
    """Own one media runtime/cache; collector replacement advances the epoch."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._timeline_cache: OrderedDict[int, tuple[int, VoiceTimeline]] = (
            OrderedDict()
        )
        self._cache_lock = asyncio.Lock()
        self._journal_owner: VoiceJournal | None = None
        self._epoch = 0
        self._media_renderer = ProfileMediaRenderer()
        self._media_cache = ProfileMediaCache()
        self._revision: str | None = None
        self._close_task: asyncio.Task[None] | None = None

    @override
    async def cog_load(self) -> None:
        try:
            self._revision = await self._media_renderer.astart()
        except asyncio.CancelledError:
            await self.cog_unload()
            raise
        except Exception as error:
            logger.warning(
                "Voice profile unavailable: %s: %.180s",
                type(error).__name__,
                str(error).splitlines()[0] if str(error) else "",
            )
            logger.debug("Voice profile initialization failed", exc_info=True)

    @override
    async def cog_unload(self) -> None:
        self._revision = None
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        await self._media_cache.aclose()
        await self._media_renderer.aclose()

    @app_commands.command(
        name="voice-profile", description="Ваш голосовой профиль на этом сервере"
    )
    @app_commands.describe(private="Показать карточку только вам")
    @app_commands.guild_only()
    async def voice_profile(
        self,
        interaction: discord.Interaction,
        private: bool = False,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Команда доступна только на сервере.", ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=private)
        guild_id, user_id = interaction.guild.id, interaction.user.id

        async def refresh(button_interaction: discord.Interaction) -> None:
            await self._deliver(button_interaction, guild_id, user_id, view)

        view = VoiceProfileView(user_id, refresh)
        await self._deliver(interaction, guild_id, user_id, view)

    async def _deliver(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        user_id: int,
        view: VoiceProfileView,
    ) -> None:
        if self._revision is None:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description="Голосовая карточка временно недоступна.",
                ephemeral=True,
            )
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise RuntimeError("Profile guild is unavailable")
        user = guild.get_member(user_id) or await guild.fetch_member(user_id)
        try:
            attachment = await self._attachment(guild, user, interaction.filesize_limit)
        except RenderBusyError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description="Карточки сейчас заняты. Попробуйте чуть позже.",
                ephemeral=True,
            )
            return
        try:
            await interaction.edit_original_response(
                attachments=[attachment], view=view
            )
        finally:
            attachment.close()

    async def _timeline(self, guild_id: int) -> ProfileSnapshot:
        async with self._cache_lock:
            collector = self.bot.get_cog("VoiceCollectorCog")
            # Extension reload replaces the Cog class, but not the journal type.
            journal: object = getattr(collector, "journal", None)
            if not isinstance(journal, VoiceJournal):
                raise RuntimeError("Voice collector is unavailable")
            if self._journal_owner is not journal:
                self._timeline_cache.clear()
                self._journal_owner = journal
                self._epoch += 1
                self._media_cache.invalidate(self._epoch)
            return await self._read_snapshot(journal, guild_id)

    async def _read_snapshot(
        self, journal: VoiceJournal, guild_id: int
    ) -> ProfileSnapshot:
        cached = self._timeline_cache.get(guild_id)
        if cached is not None and cached[0] == journal.counts.persisted:
            self._timeline_cache.move_to_end(guild_id)
            return ProfileSnapshot(cached[1], self._epoch, cached[0])
        snapshot = await journal.snapshot_for_guild(guild_id)
        timeline = await asyncio.to_thread(
            build_timeline, (*snapshot.guild_records, *snapshot.session_records)
        )
        self._timeline_cache[guild_id] = (snapshot.generation, timeline)
        self._timeline_cache.move_to_end(guild_id)
        while len(self._timeline_cache) > _TIMELINE_CACHE_ENTRIES:
            self._timeline_cache.popitem(last=False)
        return ProfileSnapshot(timeline, self._epoch, snapshot.generation)

    async def _attachment(
        self,
        guild: discord.Guild,
        user: discord.Member,
        byte_limit: int,
    ) -> discord.File:
        if self._revision is None:
            raise RuntimeError("Profile renderer is unavailable")
        timezone_name = config.VOICE_PROFILE_TIMEZONE
        try:
            _ = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "Invalid voice profile timezone %s; using UTC", timezone_name
            )
            timezone_name = "UTC"
        snapshot = await asyncio.wait_for(self._timeline(guild.id), _DATA_TIMEOUT)
        avatar_asset = user.display_avatar.with_format(
            "gif" if user.display_avatar.is_animated() else "png"
        )
        guild_asset = guild.icon.with_format("png") if guild.icon else None
        display_name, guild_name = user.display_name, guild.name
        key = MediaKey(
            guild.id,
            user.id,
            snapshot.epoch,
            snapshot.generation,
            timezone_name,
            display_name,
            guild_name,
            str(avatar_asset),
            str(guild_asset) if guild_asset else None,
            self._revision,
        )

        async def build_media() -> ProfileMedia:
            profile = await asyncio.wait_for(
                asyncio.to_thread(
                    build_profile,
                    snapshot.timeline,
                    key.user_id,
                    key.guild_id,
                    timezone_name,
                ),
                _DATA_TIMEOUT,
            )
            avatar, guild_icon = await asyncio.gather(
                self._asset_bytes(avatar_asset, "avatar"),
                self._asset_bytes(guild_asset, "guild icon"),
            )
            identity = CardIdentity(display_name, guild_name, avatar, guild_icon)
            return await self._media_renderer.render(profile, identity)

        media = (await self._media_cache.get(key, build_media)).within(
            min(MEDIA_LIMIT, byte_limit)
        )
        return discord.File(
            BytesIO(media.data),
            filename=f"voice-profile.{media.extension}",
            description=f"Голосовой профиль {display_name} на сервере {guild_name}",
        )

    async def _asset_bytes(
        self, asset: discord.Asset | None, label: str
    ) -> bytes | None:
        if asset is None:
            return None
        try:
            data = await asyncio.wait_for(asset.read(), _ASSET_TIMEOUT)
            if len(data) > 2 * 1024 * 1024:
                logger.warning("Voice profile %s exceeds image budget", label)
                return None
            return data
        except Exception:
            logger.warning("Voice profile %s unavailable", label)
            logger.debug("Asset read traceback", exc_info=True)
            return None


async def setup(bot: commands.Bot) -> None:
    """Load the profile command independently of collector load order."""
    await bot.add_cog(VoiceProfileCog(bot))
