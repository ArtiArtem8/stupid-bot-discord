"""Discord orchestration and lifetime owner of voice profile media."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import override
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

import config
from api.voice.profile.build import build_profile
from api.voice.profile.details import (
    build_activity_detail,
    build_people_detail,
    build_xp_detail,
)
from api.voice.profile.model import VoiceProfile
from api.voice.timeline import VoiceTimeline, build_timeline
from cogs.voice.profile.asset_cache import ProfileAssetCache
from cogs.voice.profile.cache import MediaKey, ProfileMediaCache, RenderedProfile
from cogs.voice.profile.design import CardIdentity
from cogs.voice.profile.detail_models import (
    DetailIdentity,
    PeoplePresentation,
    ProfileLook,
)
from cogs.voice.profile.media import (
    MEDIA_LIMIT,
    ProfileMedia,
    ProfileMediaRenderer,
    RenderBusyError,
)
from cogs.voice.profile.view import ProfileDetail, VoiceProfileView
from framework.base_cog import BaseCog
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


@dataclass(frozen=True, slots=True)
class ProfileRequest:
    """Transient consistent refresh inputs; never retained in the View."""

    snapshot: ProfileSnapshot
    as_of: datetime
    profile: VoiceProfile
    identity: CardIdentity
    look: ProfileLook
    media: ProfileMedia


class VoiceProfileCog(BaseCog):
    """Own one media runtime/cache; collector replacement advances the epoch."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(bot)
        self._timeline_cache: OrderedDict[int, tuple[int, VoiceTimeline]] = (
            OrderedDict()
        )
        self._cache_lock = asyncio.Lock()
        self._journal_owner: VoiceJournal | None = None
        self._epoch = 0
        self._media_renderer = ProfileMediaRenderer()
        self._media_cache = ProfileMediaCache()
        self._asset_cache = ProfileAssetCache()
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
        self._asset_cache.clear()
        await self._media_renderer.aclose()

    @app_commands.command(
        name="voice-profile", description="Ваш голосовой профиль на этом сервере"
    )
    @app_commands.describe(private="Показать карточку только вам")
    @app_commands.guild_only()
    async def voice_profile(
        self,
        interaction: discord.Interaction,
        private: bool = True,
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

        async def reveal(
            button_interaction: discord.Interaction, detail: ProfileDetail
        ) -> discord.File:
            return await self._reveal(
                button_interaction, guild_id, user_id, view, detail
            )

        view = VoiceProfileView(user_id, refresh, reveal=reveal, private=private)
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
        attachments: list[discord.File] = []
        try:
            request = await self._prepare(guild, user)
            attachments.append(self._attachment(request, interaction.filesize_limit))
            identity = DetailIdentity(request.identity, request.profile.appearance)
            for detail in view.revealed_order:
                attachments.append(
                    await self._detail_attachment(
                        guild,
                        request.snapshot,
                        request.as_of,
                        user_id,
                        identity,
                        request.profile.timezone_label,
                        detail,
                        interaction.filesize_limit,
                    )
                )
            message = await interaction.edit_original_response(
                attachments=attachments, view=view
            )
            view.message = message
            view.look = request.look
        except RenderBusyError:
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description="Карточки сейчас заняты. Попробуйте чуть позже.",
                ephemeral=True,
            )
        finally:
            for attachment in attachments:
                attachment.close()
                attachment.fp.close()

    async def _reveal(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        user_id: int,
        view: VoiceProfileView,
        detail: ProfileDetail,
    ) -> discord.File:
        guild = self.bot.get_guild(guild_id)
        look = view.look
        if guild is None or look is None or self._revision is None:
            raise RuntimeError("Profile detail context is unavailable")
        snapshot = await self._timeline(guild_id)
        as_of = datetime.now(UTC)
        return await self._detail_attachment(
            guild,
            snapshot,
            as_of,
            user_id,
            look,
            look.timezone_label,
            detail,
            interaction.filesize_limit,
        )

    async def _detail_attachment(
        self,
        guild: discord.Guild,
        snapshot: ProfileSnapshot,
        as_of: datetime,
        user_id: int,
        identity: DetailIdentity | ProfileLook,
        timezone_label: str,
        detail: ProfileDetail,
        byte_limit: int,
    ) -> discord.File:
        async def build() -> ProfileMedia:
            resolved = identity
            if isinstance(resolved, ProfileLook):
                avatar = await self._asset_bytes(resolved.avatar, "avatar")
                resolved = DetailIdentity(
                    CardIdentity(resolved.display_name, resolved.guild_name, avatar),
                    resolved.appearance,
                )
            return await self._detail_media(
                guild, snapshot, as_of, user_id, resolved, timezone_label, detail
            )

        media = await self._media_cache.render_detail(build)
        media = media.within(min(MEDIA_LIMIT, byte_limit))
        return discord.File(BytesIO(media.data), filename=f"voice-{detail.value}.png")

    async def _detail_media(
        self,
        guild: discord.Guild,
        snapshot: ProfileSnapshot,
        as_of: datetime,
        user_id: int,
        identity: DetailIdentity,
        timezone_label: str,
        detail: ProfileDetail,
    ) -> ProfileMedia:
        timezone = ZoneInfo(timezone_label)
        match detail:
            case ProfileDetail.ACTIVITY:
                activity = await asyncio.to_thread(
                    build_activity_detail,
                    snapshot.timeline,
                    user_id,
                    guild.id,
                    as_of,
                    timezone,
                )
                media = await self._media_renderer.render_activity(activity, identity)
            case ProfileDetail.PEOPLE:
                people = await asyncio.to_thread(
                    build_people_detail,
                    snapshot.timeline,
                    user_id,
                    guild.id,
                    as_of,
                    timezone,
                )
                ids = [person.user_id for person in people.companions[:5]]
                colors = await asyncio.to_thread(
                    _companion_colors, snapshot.timeline, ids, guild.id, timezone_label
                )
                names = {
                    identifier: self._display_name(guild, identifier)
                    for identifier in (
                        *ids,
                        *(bot_id for bot_id, _ in people.bots.by_bot),
                    )
                }
                media = await self._media_renderer.render_people(
                    PeoplePresentation(people, names, colors), identity
                )
            case ProfileDetail.XP:
                xp = await asyncio.to_thread(
                    build_xp_detail,
                    snapshot.timeline,
                    user_id,
                    guild.id,
                    as_of,
                    timezone,
                )
                media = await self._media_renderer.render_xp(xp, identity)
        return media

    def _display_name(self, guild: discord.Guild, user_id: int) -> str:
        user = guild.get_member(user_id) or self.bot.get_user(user_id)
        return user.display_name if user is not None else "Unknown user"

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

    async def _prepare(
        self, guild: discord.Guild, user: discord.Member
    ) -> ProfileRequest:
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
        as_of = datetime.now(UTC)
        avatar_asset = user.display_avatar.with_format(
            "gif" if user.display_avatar.is_animated() else "png"
        ).with_size(256)
        guild_asset = (
            guild.icon.with_format("png").with_size(64) if guild.icon else None
        )
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

        async def build() -> RenderedProfile:
            profile = await asyncio.to_thread(
                build_profile,
                snapshot.timeline,
                key.user_id,
                key.guild_id,
                key.timezone,
            )
            avatar, guild_icon = await asyncio.gather(
                self._asset_bytes(avatar_asset, "avatar"),
                self._asset_bytes(guild_asset, "guild icon"),
            )
            identity = CardIdentity(
                key.display_name, key.guild_name, avatar, guild_icon
            )
            media = await self._media_renderer.render(profile, identity)
            return RenderedProfile(media, profile, identity)

        card = await self._media_cache.get(key, build)
        look = ProfileLook(
            display_name,
            guild_name,
            card.profile.appearance,
            avatar_asset,
            timezone_name,
        )
        return ProfileRequest(
            snapshot, as_of, card.profile, card.identity, look, card.media
        )

    @staticmethod
    def _attachment(request: ProfileRequest, byte_limit: int) -> discord.File:
        media = request.media.within(min(MEDIA_LIMIT, byte_limit))
        return discord.File(
            BytesIO(media.data),
            filename=f"voice-profile.{media.extension}",
            description=(
                f"Голосовой профиль {request.identity.display_name} "
                f"на сервере {request.identity.guild_name}"
            ),
        )

    async def _asset_bytes(
        self, asset: discord.Asset | None, label: str
    ) -> bytes | None:
        if asset is None:
            return None
        url = str(asset)
        cached = self._asset_cache.get(url)
        if cached is not None:
            return cached
        try:
            data = await asyncio.wait_for(asset.read(), _ASSET_TIMEOUT)
            if len(data) > 2 * 1024 * 1024:
                logger.warning("Voice profile %s exceeds image budget", label)
                return None
            self._asset_cache.put(url, data)
            return data
        except Exception:
            logger.warning("Voice profile %s unavailable", label)
            logger.debug("Asset read traceback", exc_info=True)
            return None


def _companion_colors(
    timeline: VoiceTimeline, ids: list[int], guild_id: int, timezone_label: str
) -> dict[int, int]:
    # Reuse canonical lifetime progression; no rates or tier boundaries in the Cog.
    return {
        user_id: build_profile(
            timeline, user_id, guild_id, timezone_label
        ).appearance.color
        for user_id in ids
    }


async def setup(bot: commands.Bot) -> None:
    """Load the profile command independently of collector load order."""
    await bot.add_cog(VoiceProfileCog(bot))
