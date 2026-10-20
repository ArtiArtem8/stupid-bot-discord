"""Discord orchestration and lifetime owner of voice profile media."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING, override
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from stupidbot.app.health import ComponentHealth
from stupidbot.discord_ui.base_cog import GenericBaseCog
from stupidbot.discord_ui.feedback import FeedbackType, FeedbackUI
from stupidbot.discord_ui.interaction_flow import defer_command
from stupidbot.features.preferences.reminders import maybe_remind
from stupidbot.features.voice.discord.attachments import detail_embed
from stupidbot.features.voice.discord.view import (
    ProfileDetail,
    ProfileLook,
    VoiceProfileView,
)
from stupidbot.features.voice.messages import VoiceMessages
from stupidbot.features.voice.profile.asset_cache import ProfileAssetCache
from stupidbot.features.voice.profile.build import build_profile
from stupidbot.features.voice.profile.cache import (
    MediaKey,
    ProfileMediaCache,
    RenderedProfile,
)
from stupidbot.features.voice.profile.details import (
    build_activity_detail,
    build_people_detail,
    build_xp_detail,
)
from stupidbot.features.voice.profile.media import (
    MEDIA_BYTE_LIMIT,
    ProfileMedia,
    ProfileMediaRenderer,
    RenderBusyError,
)
from stupidbot.features.voice.profile.model import VoiceProfile
from stupidbot.features.voice.profile.presentation import (
    CardIdentity,
    DetailIdentity,
    PeoplePresentation,
)
from stupidbot.features.voice.runtime import AnalyticsUnavailableError, VoiceAnalytics
from stupidbot.features.voice.timeline import VoiceTimeline
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import interaction_context, profile_context
from stupidbot.i18n.policy import Language, PresentationContext

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot

logger = logging.getLogger(__name__)
_TIMELINE_REQUEST_LIMIT = 4
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


class VoiceProfileCog(GenericBaseCog["StupidBot"]):
    """Own one media runtime/cache; analytics replacement advances the epoch."""

    def __init__(self, bot: StupidBot) -> None:
        super().__init__(bot)
        self._snapshot_lock = asyncio.Lock()
        self._timeline_admitted = 0
        self._analytics_owner: VoiceAnalytics | None = None
        self._epoch = 0
        self._media_renderer = ProfileMediaRenderer()
        self._media_cache = ProfileMediaCache()
        self._asset_cache = ProfileAssetCache()
        self._revision: str | None = None
        self._close_task: asyncio.Task[None] | None = None

    @override
    def command_response_ephemeral(self, _interaction: discord.Interaction) -> bool:
        options: dict[str, object] = dict(_interaction.namespace)
        return options.get("private", True) is not False

    @property
    def runtime_health(self) -> ComponentHealth:
        """Report completed renderer initialization without performing native work."""
        return ComponentHealth(self._revision is not None and self._close_task is None)

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
        name="voice-profile",
        description=app_commands.locale_str(
            "Your voice profile on this server", domain="voice"
        ),
    )
    @app_commands.describe(
        private=app_commands.locale_str("Show the card only to you", domain="voice")
    )
    @app_commands.guild_only()
    async def voice_profile(
        self,
        interaction: discord.Interaction,
        private: bool = True,
    ) -> None:
        if interaction.guild is None:
            context = await interaction_context(interaction, private=True)
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=catalog("voice", context.language).pgettext(
                    "guild_only", "This command is only available in a server."
                ),
                ephemeral=True,
            )
            return
        await defer_command(interaction, ephemeral=private)
        # Visibility controls delivery; the profile always belongs to its requester.
        context = await profile_context(interaction)
        guild_id, user_id = interaction.guild.id, interaction.user.id

        async def refresh(button_interaction: discord.Interaction) -> None:
            await self._deliver(button_interaction, guild_id, user_id, view)

        async def reveal(
            button_interaction: discord.Interaction, detail: ProfileDetail
        ) -> discord.File:
            return await self._reveal(
                button_interaction, guild_id, user_id, view, detail
            )

        view = VoiceProfileView(
            user_id, refresh, reveal=reveal, private=private, context=context
        )
        delivered = await self._deliver(interaction, guild_id, user_id, view)
        if private and delivered:
            await maybe_remind(interaction, context)

    async def _deliver(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        user_id: int,
        view: VoiceProfileView,
    ) -> bool:
        if self._revision is None:
            context = await interaction_context(interaction, private=True)
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.WARNING,
                description=catalog("voice", context.language).pgettext(
                    "unavailable", "Voice profiles are temporarily unavailable."
                ),
                ephemeral=True,
            )
            return False
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise RuntimeError("Profile guild is unavailable")
        user = guild.get_member(user_id) or await guild.fetch_member(user_id)
        attachments: list[discord.File] = []
        try:
            request = await self._prepare(guild, user, context=view.context)
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
                attachments=attachments,
                embeds=[detail_embed(file.filename) for file in attachments[1:]],
                view=view,
            )
            view.message = message
            view.look = request.look
        except RenderBusyError:
            context = await interaction_context(interaction, private=True)
            await FeedbackUI.send(
                interaction,
                feedback_type=FeedbackType.INFO,
                description=catalog("voice", context.language).pgettext(
                    "busy", "Cards are busy right now. Please try again later."
                ),
                ephemeral=True,
            )
            return False
        finally:
            for attachment in attachments:
                attachment.close()
                attachment.fp.close()
        return True

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
                    CardIdentity(
                        resolved.display_name,
                        resolved.guild_name,
                        avatar,
                        language=resolved.language,
                    ),
                    resolved.appearance,
                )
            return await self._detail_media(
                guild, snapshot, as_of, user_id, resolved, timezone_label, detail
            )

        media = await self._media_cache.render_detail(build)
        media = media.within(min(MEDIA_BYTE_LIMIT, byte_limit))
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
                    identifier: self._display_name(
                        guild, identifier, identity.card.language
                    )
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

    def _display_name(
        self, guild: discord.Guild, user_id: int, language: Language
    ) -> str:
        user = guild.get_member(user_id) or self.bot.get_user(user_id)
        return (
            user.display_name
            if user is not None
            else catalog("voice", language).pgettext("unknown_user", "Unknown user")
        )

    async def _timeline(self, guild_id: int) -> ProfileSnapshot:
        if (
            self._timeline_admitted >= _TIMELINE_REQUEST_LIMIT
            or self._close_task is not None
        ):
            raise RenderBusyError("Voice history is busy")
        self._timeline_admitted += 1
        try:
            async with self._snapshot_lock:
                analytics = self.bot.voice_analytics
                if self._analytics_owner is not analytics:
                    self._analytics_owner = analytics
                    self._epoch += 1
                    self._media_cache.invalidate(self._epoch)
                return await self._read_snapshot(analytics, guild_id)
        finally:
            self._timeline_admitted -= 1

    async def _read_snapshot(
        self, analytics: VoiceAnalytics, guild_id: int
    ) -> ProfileSnapshot:
        try:
            snapshot = await analytics.snapshot(guild_id)
        except AnalyticsUnavailableError as error:
            raise RenderBusyError("Voice analytics is recovering") from error
        return ProfileSnapshot(snapshot.timeline, self._epoch, snapshot.generation)

    async def _prepare(
        self,
        guild: discord.Guild,
        user: discord.Member,
        *,
        context: PresentationContext,
    ) -> ProfileRequest:
        if self._revision is None:
            raise RuntimeError("Profile renderer is unavailable")
        timezone_name = context.timezone.key
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
            language=context.language,
            catalog_revision=catalog("voice", context.language).revision,
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
                key.display_name,
                key.guild_name,
                avatar,
                guild_icon,
                language=context.language,
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
            language=context.language,
        )
        return ProfileRequest(
            snapshot, as_of, card.profile, card.identity, look, card.media
        )

    @staticmethod
    def _attachment(request: ProfileRequest, byte_limit: int) -> discord.File:
        media = request.media.within(min(MEDIA_BYTE_LIMIT, byte_limit))
        return discord.File(
            BytesIO(media.data),
            filename=f"voice-profile.{media.extension}",
            description=VoiceMessages(
                catalog("voice", request.identity.language)
            ).attachment(
                display_name=request.identity.display_name,
                guild_name=request.identity.guild_name,
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
    return {
        user_id: build_profile(
            timeline, user_id, guild_id, timezone_label
        ).appearance.color
        for user_id in ids
    }


async def setup(bot: StupidBot) -> None:
    """Load the profile command independently of collector load order."""
    await bot.add_cog(VoiceProfileCog(bot))
