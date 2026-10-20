"""Music Cog Controller."""

import asyncio
import logging
from typing import TYPE_CHECKING, override

import discord
from discord import Interaction, Member, app_commands
from discord.ext import commands, tasks

from stupidbot import settings as config
from stupidbot.app.health import ComponentHealth, TaskHealth
from stupidbot.discord_ui.base_cog import BaseCog
from stupidbot.discord_ui.exceptions import CommandFeedbackError
from stupidbot.discord_ui.feedback import FeedbackUI
from stupidbot.discord_ui.interaction_flow import defer_command
from stupidbot.features.music.composition import create_music_components
from stupidbot.features.music.contracts import TrackExceptionPayload
from stupidbot.features.music.discord.access import (
    MusicAction,
    require_music_access,
)
from stupidbot.features.music.discord.feedback import (
    send_error,
    send_info,
    send_success,
    send_warning,
    send_warning_no_player,
)
from stupidbot.features.music.discord.presentation import (
    build_playlist_added_embed,
    build_repeat_embed,
    build_rotate_embed,
    build_session_summary_embed,
    build_skip_embed,
    build_track_added_embed,
    build_track_exception_embed,
)
from stupidbot.features.music.discord.views import (
    QueuePaginationAdapter,
    QueuePaginator,
    QueueUndoView,
    SessionSummaryView,
)
from stupidbot.features.music.messages import MusicMessages
from stupidbot.features.music.playback.models import (
    QueuePlacement,
    QueueSnapshot,
    RepeatMode,
)
from stupidbot.features.music.repository import VolumeStore
from stupidbot.features.music.results import (
    MusicErrorCode,
    MusicResult,
    MusicResultReason,
    MusicResultStatus,
    PlayResponseData,
    VoiceCheckResult,
    VoiceJoinResult,
)
from stupidbot.features.music.runtime.session import MusicSession
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.discord import error_context, guild_context, interaction_context
from stupidbot.i18n.policy import Language

if TYPE_CHECKING:
    from stupidbot.app.bot import StupidBot

logger = logging.getLogger(__name__)


def _format_voice_result_message(
    result: VoiceCheckResult,
    to_channel: discord.abc.GuildChannel | None,
    from_channel: discord.abc.GuildChannel | None,
    *,
    language: Language = Language.RU,
) -> str:
    messages = MusicMessages(catalog("music", language))
    unknown = messages.catalog.pgettext("unknown_channel", "Unknown channel")
    return messages.connection(
        result,
        destination=to_channel.mention if to_channel else unknown,
        origin=from_channel.mention if from_channel else unknown,
    )


class MusicCog(BaseCog):
    """Music playback controller."""

    @override
    def command_response_ephemeral(self, _interaction: Interaction) -> bool:
        command = _interaction.command
        if command is not None and command.name == "queue":
            options: dict[str, object] = dict(iter(_interaction.namespace))
            return options.get("ephemeral", True) is not False
        return command is not None and command.name in {"join", "leave", "reconnect"}

    @override
    async def interaction_check(self, interaction: Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        if (
            interaction.guild_id is not None
            and self.service.voice_lifecycle.is_healing(interaction.guild_id)
            and interaction.command is not None
            and interaction.command.name not in {"leave", "queue"}
        ):
            context = await error_context(interaction)
            messages = MusicMessages(catalog("music", context.language))
            raise CommandFeedbackError(
                messages.failure(MusicResultReason.RECOVERING),
                severity="info",
            )
        return await self._check_command_voice(interaction)

    def _command_voice_action(self, interaction: Interaction) -> MusicAction | None:
        """Keep read-only commands outside the voice-control permission policy."""
        command = interaction.command
        if command is None or command.name == "queue":
            return None
        options: dict[str, object] = dict(iter(interaction.namespace))
        if command.name == "volume" and options.get("value") is None:
            return None
        if command.name in {"join", "play", "play-next"}:
            return "join"
        if command.name in {"leave", "reconnect"}:
            return "manage_session"
        if command.name == "skip":
            return "skip"
        return "control"

    async def _check_command_voice(self, interaction: Interaction) -> bool:
        """Check current voice policy before and after command preflight awaits."""
        action = self._command_voice_action(interaction)
        if action is None:
            return True
        player = (
            self.service.get_player(interaction.guild_id)
            if interaction.guild_id is not None
            else None
        )
        voice_client = interaction.guild.voice_client if interaction.guild else None
        channel = voice_client.channel if voice_client is not None else None
        await require_music_access(
            interaction,
            channel,
            action=action,
            attempt=player.current_attempt if player is not None else None,
        )
        return True

    def __init__(self, bot: commands.Bot, volumes: VolumeStore) -> None:
        super().__init__(bot)

        self.components = create_music_components(bot, volumes)
        self.service = self.components.service
        self._close_task: asyncio.Task[None] | None = None

    @property
    def runtime_health(self) -> ComponentHealth:
        """Expose cached availability and the owned monitor without external I/O."""
        return ComponentHealth(
            ready=self.service.connection.has_ready_node(),
            tasks=(
                TaskHealth(
                    "auto_leave_monitor",
                    self.auto_leave_monitor.is_running(),
                    self.auto_leave_monitor.failed(),
                ),
            ),
        )

    @override
    async def cog_load(self) -> None:
        if self.bot.is_ready():
            await self.service.initialize()
        if not self.auto_leave_monitor.is_running():
            self.auto_leave_monitor.start()

    @override
    async def cog_unload(self) -> None:
        """Finish owned cleanup before a cancelled reload can release this cog."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await self._wait_for_unload(self._close_task)

    async def _close(self) -> None:
        monitor = self.auto_leave_monitor.get_task()
        if monitor is not None:
            self.auto_leave_monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        try:
            await self.service.cleanup()
        finally:
            await self.components.controllers.cleanup()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.service.initialize()

    @commands.Cog.listener()
    async def on_music_session_end(
        self, _guild_id: int, session: MusicSession, channel_id: int
    ) -> None:
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.abc.Messageable):
            return

        if not session.tracks:
            return

        context = await guild_context(self.bot, _guild_id)
        embed = build_session_summary_embed(session, language=context.language)
        view = SessionSummaryView(session=session, timeout=300.0, context=context)

        try:
            msg = await channel.send(embed=embed, view=view)
            view.message = msg
        except Exception:
            logger.exception("Failed to send session summary to channel %s", channel_id)

    @commands.Cog.listener()
    async def on_music_track_exception(self, payload: TrackExceptionPayload) -> None:
        channel_id = payload.channel_id
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.abc.Messageable):
            return

        context = await guild_context(self.bot, payload.guild_id)
        embed = build_track_exception_embed(payload, language=context.language)

        try:
            await channel.send(embed=embed, delete_after=60)
        except Exception:
            logger.exception("Failed to send track exception message to %s", channel_id)

    @tasks.loop(seconds=config.MUSIC_AUTO_LEAVE_CHECK_INTERVAL)
    async def auto_leave_monitor(self) -> None:
        await self.service.check_auto_leave()

    @auto_leave_monitor.before_loop
    async def before_auto_leave_monitor(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="join",
        description=app_commands.locale_str(
            "Connect to a voice channel", domain="music"
        ),
    )
    @app_commands.guild_only()
    async def join(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)

        channel = await self._get_voice_channel_for_play(interaction)
        if not channel:
            return

        await self._check_command_voice(interaction)
        check_result, from_channel = await self.service.join(guild, channel)
        await self._send_join_feedback(
            interaction,
            result=check_result,
            channel=channel,
            from_channel=from_channel,
            delete_after=60,
            warn_on_failure=True,
            ephemeral=True,
        )

    @app_commands.command(
        name="play",
        description=app_commands.locale_str(
            "Play music from YT, SoundCloud, Y.Music and VK", domain="music"
        ),
    )
    @app_commands.describe(
        query=app_commands.locale_str("URL or title", domain="music")
    )
    @app_commands.guild_only()
    async def play(self, interaction: Interaction, query: str) -> None:
        await self._run_play_command(interaction, query, "end")

    @app_commands.command(
        name="play-next",
        description=app_commands.locale_str(
            "Add a track or playlist to the front of the queue", domain="music"
        ),
    )
    @app_commands.describe(
        query=app_commands.locale_str("URL or title", domain="music")
    )
    @app_commands.guild_only()
    async def play_next(self, interaction: Interaction, query: str) -> None:
        await self._run_play_command(interaction, query, "next")

    async def _run_play_command(
        self,
        interaction: Interaction,
        query: str,
        placement: QueuePlacement,
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = MusicMessages(catalog("music", context.language))
        guild = await self._require_guild(interaction)
        if not query.strip():
            await send_warning(
                interaction,
                messages.catalog.pgettext("ui", "Enter a track title or link."),
                ephemeral=True,
            )
            return

        channel = await self._get_voice_channel_for_play(interaction)
        if not channel:
            return

        await self._check_command_voice(interaction)
        result = await self.service.play(
            guild,
            channel,
            query.strip(),
            interaction.user.id,
            interaction.channel_id,
            placement=placement,
        )

        data = await self._resolve_play_response_data(interaction, result, channel)
        if not data:
            return

        duration_ms = await self.service.get_queue_duration(guild.id)
        delay_sec = (duration_ms / 1000) + 60

        await self._send_play_feedback(interaction, data, delay_sec)

    async def _get_voice_channel_for_play(
        self, interaction: Interaction
    ) -> discord.VoiceChannel | discord.StageChannel | None:
        context = await interaction_context(interaction, private=True)
        messages = MusicMessages(catalog("music", context.language))
        if (
            not isinstance(interaction.user, Member)
            or not interaction.user.voice
            or not interaction.user.voice.channel
        ):
            await send_warning(
                interaction,
                messages.catalog.pgettext("ui", "You must be in a voice channel!"),
                ephemeral=True,
            )
            return None
        return interaction.user.voice.channel

    async def _resolve_play_response_data(
        self,
        interaction: Interaction,
        result: MusicResult[PlayResponseData | VoiceJoinResult],
        channel: discord.abc.GuildChannel,
    ) -> PlayResponseData | None:
        context = await interaction_context(interaction, private=False)
        messages = MusicMessages(catalog("music", context.language))
        data = result.data
        if isinstance(data, tuple):
            check, from_channel = data
            await self._handle_join_for_play(interaction, check, channel, from_channel)
            return None
        if result.status is MusicResultStatus.ERROR:
            await send_error(interaction, messages.failure(result.reason))
            return None
        if (
            result.status is MusicResultStatus.FAILURE
            and result.reason is not MusicResultReason.NOTHING_FOUND
        ):
            await send_error(interaction, messages.failure(result.reason))
            return None
        if not data:
            await send_info(
                interaction,
                messages.catalog.pgettext("ui", "Nothing found. Please try again."),
            )
            return None
        return data

    async def _handle_join_for_play(
        self,
        interaction: Interaction,
        result: VoiceCheckResult,
        channel: discord.abc.GuildChannel,
        from_channel: discord.abc.GuildChannel | None,
    ) -> bool:
        if result.status is MusicResultStatus.SUCCESS:
            return True
        await self._send_join_feedback(
            interaction,
            result=result,
            channel=channel,
            from_channel=from_channel,
            delete_after=60,
            ephemeral=False,
        )
        return False

    async def _send_join_feedback(
        self,
        interaction: Interaction,
        result: VoiceCheckResult,
        channel: discord.abc.GuildChannel,
        from_channel: discord.abc.GuildChannel | None,
        *,
        delete_after: float | None = None,
        warn_on_failure: bool = False,
        ephemeral: bool = False,
    ) -> None:
        context = await interaction_context(interaction, private=ephemeral)
        msg = _format_voice_result_message(
            result, channel, from_channel, language=context.language
        )
        if result.status is MusicResultStatus.ERROR:
            await send_error(interaction, msg, ephemeral=ephemeral)
            return
        if result.status is MusicResultStatus.FAILURE:
            if warn_on_failure:
                await send_warning(
                    interaction,
                    msg,
                    ephemeral=ephemeral,
                    delete_after=delete_after,
                )
            else:
                await send_info(
                    interaction,
                    msg,
                    delete_after=delete_after,
                    ephemeral=ephemeral,
                )
            return
        await send_info(
            interaction,
            msg,
            delete_after=delete_after,
            ephemeral=ephemeral,
        )

    async def _send_no_player_or_unavailable(
        self, interaction: Interaction, result: MusicResult[object]
    ) -> None:
        context = await interaction_context(interaction, private=True)
        messages = MusicMessages(catalog("music", context.language))
        if result.reason is MusicResultReason.RECOVERING:
            await send_info(
                interaction,
                messages.failure(MusicResultReason.RECOVERING),
                ephemeral=True,
                delete_after=60,
            )
            return
        if result.reason is MusicErrorCode.MUSIC_NODE_UNAVAILABLE:
            await send_warning(
                interaction,
                messages.failure(MusicErrorCode.MUSIC_NODE_UNAVAILABLE),
                ephemeral=True,
                delete_after=60,
            )
            return
        await send_warning_no_player(interaction)

    async def _send_play_feedback(
        self, interaction: Interaction, data: PlayResponseData, delay_sec: float
    ) -> None:
        context = await interaction_context(interaction, private=False)
        match data["type"]:
            case "track":
                embed = build_track_added_embed(
                    data,
                    requester_name=interaction.user.display_name,
                    requester_avatar_url=interaction.user.display_avatar.url,
                    language=context.language,
                )
                delete_after = min(delay_sec, 480)
            case "playlist":
                embed = build_playlist_added_embed(
                    data,
                    requester_name=interaction.user.display_name,
                    requester_avatar_url=interaction.user.display_avatar.url,
                    language=context.language,
                )
                delete_after = min(delay_sec, 600)

        view = None
        if data["undo_entries"] and interaction.guild_id is not None:
            view = QueueUndoView(
                guild_id=interaction.guild_id,
                expected_entries=data["undo_entries"],
                requester_id=interaction.user.id,
                remove_callback=self.service.remove_queued_entries,
                timeout=delete_after,
                context=context,
            )
        await FeedbackUI.send(
            interaction,
            embed=embed,
            view=view,
            delete_after=delete_after,
        )

    @app_commands.command(
        name="stop",
        description=app_commands.locale_str(
            "Stop playback and clear the queue", domain="music"
        ),
    )
    @app_commands.guild_only()
    async def stop(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=False)
        messages = MusicMessages(catalog("music", context.language))
        guild = await self._require_guild(interaction)
        await self._check_command_voice(interaction)
        res = await self.service.stop(
            guild.id, interaction.user.id, interaction.channel_id
        )
        if res.is_success:
            await send_info(interaction, messages.catalog.pgettext("ui", "Stopped"))
        else:
            await self._send_no_player_or_unavailable(interaction, res)

    @app_commands.command(
        name="skip",
        description=app_commands.locale_str("Skip the current track", domain="music"),
    )
    @app_commands.guild_only()
    async def skip(self, interaction: Interaction) -> None:
        guild = await self._require_guild(interaction)
        player = self.service.get_player(guild.id)
        expected = player.current_attempt if player is not None else None
        context = await interaction_context(interaction, private=False)
        messages = MusicMessages(catalog("music", context.language))
        voice_client = guild.voice_client
        channel = voice_client.channel if voice_client is not None else None
        await require_music_access(
            interaction,
            channel,
            action="skip",
            attempt=expected,
        )
        if expected is None:
            await send_warning_no_player(interaction)
            return
        res = await self.service.skip(
            guild.id, interaction.user.id, interaction.channel_id, expected=expected
        )
        if res.status is MusicResultStatus.FAILURE:
            await self._send_no_player_or_unavailable(interaction, res)
            return

        if not res.is_success or not res.data:
            await send_error(interaction, messages.failure(res.reason))
            return

        skipped = res.data["before"]
        next_track = res.data["after"]

        if not skipped and not next_track:
            context = await interaction_context(interaction, private=True)
            messages = MusicMessages(catalog("music", context.language))
            await send_warning(
                interaction,
                messages.catalog.pgettext("ui", "Nothing to skip"),
                ephemeral=True,
            )
            return

        embed = build_skip_embed(skipped, next_track, language=context.language)
        await FeedbackUI.send(interaction, embed=embed, delete_after=60)

    @app_commands.command(
        name="queue", description=app_commands.locale_str("Queue", domain="music")
    )
    @app_commands.describe(
        ephemeral=app_commands.locale_str(
            "Show this message only to you", domain="music"
        )
    )
    @app_commands.guild_only()
    async def queue(self, interaction: Interaction, ephemeral: bool = True) -> None:
        context = await interaction_context(interaction, private=ephemeral)
        messages = MusicMessages(catalog("music", context.language))
        guild = await self._require_guild(interaction)

        res = await self.service.get_queue(guild.id)
        data = res.data
        if not data:
            context = await interaction_context(interaction, private=True)
            messages = MusicMessages(catalog("music", context.language))
            if res.reason is MusicErrorCode.MUSIC_NODE_UNAVAILABLE:
                await send_warning(
                    interaction,
                    messages.failure(MusicErrorCode.MUSIC_NODE_UNAVAILABLE),
                    ephemeral=True,
                    delete_after=60,
                )
                return
            await send_warning(
                interaction,
                messages.catalog.pgettext("ui", "The queue is empty"),
                ephemeral=True,
            )
            return

        async def fetch() -> QueueSnapshot | None:
            res = await self.service.get_queue(guild.id)
            return res.data

        adapter = QueuePaginationAdapter(data, context=context)
        view = QueuePaginator(adapter, fetch, interaction.user.id)
        await view.prepare()
        await view.send(interaction, ephemeral=ephemeral)

    @app_commands.command(
        name="volume",
        description=app_commands.locale_str("Set volume (0-200)", domain="music"),
    )
    @app_commands.describe(
        value=app_commands.locale_str("Leave empty to check the volume", domain="music")
    )
    @app_commands.guild_only()
    async def volume(
        self,
        interaction: Interaction,
        value: app_commands.Range[int, 0, 200] | None = None,
    ) -> None:
        context = await interaction_context(interaction, private=False)
        messages = MusicMessages(catalog("music", context.language))
        guild = await self._require_guild(interaction)
        if value is None:
            vol = await self.service.get_volume(guild.id)
            await send_info(interaction, messages.volume(percent=vol))
            return

        await self._check_command_voice(interaction)
        res = await self.service.set_volume(guild.id, value)
        if res.is_success and res.data is not None:
            await send_success(interaction, messages.volume(percent=res.data))
        else:
            await send_error(interaction, messages.failure(res.reason))

    @app_commands.command(
        name="leave", description=app_commands.locale_str("Disconnect", domain="music")
    )
    @app_commands.guild_only()
    async def leave(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=True)
        messages = MusicMessages(catalog("music", context.language))
        guild = await self._require_guild(interaction)
        await self._check_command_voice(interaction)
        res = await self.service.leave(guild)
        match res.status:
            case MusicResultStatus.SUCCESS:
                await send_info(
                    interaction,
                    messages.catalog.pgettext("ui", "Disconnected"),
                    title=messages.catalog.pgettext("ui", "Goodbye ❤️"),
                    ephemeral=True,
                )
            case MusicResultStatus.FAILURE:
                await self._send_no_player_or_unavailable(interaction, res)
            case MusicResultStatus.ERROR:
                await send_error(
                    interaction, messages.failure(res.reason), ephemeral=True
                )

    @app_commands.command(
        name="shuffle",
        description=app_commands.locale_str("Shuffle the queue", domain="music"),
    )
    @app_commands.guild_only()
    async def shuffle(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=False)
        messages = MusicMessages(catalog("music", context.language))
        guild = await self._require_guild(interaction)
        await self._check_command_voice(interaction)
        res = await self.service.shuffle(
            guild.id, interaction.user.id, interaction.channel_id
        )
        if res.is_success:
            await send_success(interaction, messages.catalog.pgettext("ui", "Shuffled"))
        else:
            await self._send_no_player_or_unavailable(interaction, res)

    @app_commands.command(
        name="rotate",
        description=app_commands.locale_str(
            "Move the current track to the end of the queue", domain="music"
        ),
    )
    @app_commands.guild_only()
    async def rotate(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=False)
        messages = MusicMessages(catalog("music", context.language))
        guild = await self._require_guild(interaction)
        await self._check_command_voice(interaction)
        res = await self.service.rotate(
            guild.id, interaction.user.id, interaction.channel_id
        )
        if not res.is_success:
            await self._send_no_player_or_unavailable(interaction, res)
            return
        if not res.data or not res.data["skipped"]:
            context = await interaction_context(interaction, private=True)
            messages = MusicMessages(catalog("music", context.language))
            await send_warning(
                interaction,
                messages.catalog.pgettext("ui", "Nothing to move"),
                ephemeral=True,
            )
            return
        moved_track = res.data["skipped"]
        next_track = res.data["next"]

        embed = build_rotate_embed(moved_track, next_track, language=context.language)
        await FeedbackUI.send(interaction, embed=embed, delete_after=60)

    @app_commands.command(
        name="repeat",
        description=app_commands.locale_str("Enable or disable repeat", domain="music"),
    )
    @app_commands.describe(
        mode=app_commands.locale_str(
            "off: no repeat, queue: repeat queue, track: repeat track",
            domain="music",
        )
    )
    @app_commands.guild_only()
    async def repeat(
        self,
        interaction: Interaction,
        mode: RepeatMode | None = None,
    ) -> None:
        context = await interaction_context(interaction, private=False)
        guild = await self._require_guild(interaction)
        await self._check_command_voice(interaction)
        result = await self.service.set_repeat(
            guild.id,
            mode,
            requester_id=interaction.user.id,
            text_channel_id=interaction.channel_id,
        )

        data = result.data
        if not result.is_success or not data:
            await self._send_no_player_or_unavailable(interaction, result)
            return

        embed = build_repeat_embed(data.get("mode"), language=context.language)
        await FeedbackUI.send(interaction, embed=embed, delete_after=60)

    @app_commands.command(
        name="pause",
        description=app_commands.locale_str("Pause playback", domain="music"),
    )
    @app_commands.guild_only()
    async def pause(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=False)
        messages = MusicMessages(catalog("music", context.language))
        guild = await self._require_guild(interaction)
        await self._check_command_voice(interaction)
        res = await self.service.pause(guild.id)
        if res.is_success:
            await send_info(
                interaction,
                messages.catalog.pgettext("ui", "Playback paused"),
            )
        else:
            await self._send_no_player_or_unavailable(interaction, res)

    @app_commands.command(
        name="resume",
        description=app_commands.locale_str("Resume playback", domain="music"),
    )
    @app_commands.guild_only()
    async def resume(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=False)
        messages = MusicMessages(catalog("music", context.language))
        guild = await self._require_guild(interaction)
        await self._check_command_voice(interaction)
        res = await self.service.resume(guild.id)
        if res.is_success:
            await send_info(
                interaction,
                messages.catalog.pgettext("ui", "Playback resumed"),
            )
        else:
            await self._send_no_player_or_unavailable(interaction, res)

    @app_commands.command(
        name="reconnect",
        description=app_commands.locale_str("Reconnect after an error", domain="music"),
    )
    @app_commands.guild_only()
    async def heal(self, interaction: Interaction) -> None:
        context = await interaction_context(interaction, private=True)
        messages = MusicMessages(catalog("music", context.language))
        if interaction.guild_id is None:
            await send_warning(
                interaction,
                messages.catalog.pgettext("ui", "server not found"),
                ephemeral=True,
            )
            return

        if self.components.healer.get_recoverable_player(interaction.guild_id) is None:
            await send_warning(
                interaction,
                messages.catalog.pgettext("ui", "no player"),
                ephemeral=True,
            )
            return

        await defer_command(interaction, ephemeral=True)

        await self._check_command_voice(interaction)
        restored = await self.service.heal(interaction.guild_id)
        if restored:
            await send_warning(
                interaction,
                title=messages.catalog.pgettext("ui", "Restored"),
                message=messages.catalog.pgettext("ui", "Reconnection attempted"),
                ephemeral=True,
            )
            return

        await send_warning(
            interaction,
            title=messages.catalog.pgettext("ui", "Not restored"),
            message=messages.catalog.pgettext(
                "ui",
                "Could not restore the music session. Please try again.",
            ),
            ephemeral=True,
        )


async def setup(bot: "StupidBot") -> None:
    """Register the music cog."""
    await bot.add_cog(MusicCog(bot, bot.volume_repository))
