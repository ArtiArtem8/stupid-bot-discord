"""Typed music presentation; result text never decides behavior or translation."""

from dataclasses import dataclass

from stupidbot.features.music.results import (
    MusicErrorCode,
    MusicResultReason,
    VoiceCheckResult,
)
from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class MusicMessages:
    """Format complete phrases with typed inputs after selecting the audience."""

    catalog: Catalog

    def requester(self, *, name: str) -> str:
        """Label an unmodified Discord display name."""
        return self.catalog.pgettext("requester", "Requested by: {name}").format(
            name=name
        )

    def volume(self, *, percent: int) -> str:
        """Display committed or queried playback volume."""
        return self.catalog.pgettext("volume", "Volume: {percent:d}%").format(
            percent=percent
        )

    def playlist_added(self, *, name: str) -> str:
        """Use the caller's bounded Markdown-safe playlist name."""
        return self.catalog.pgettext(
            "playlist_added", "Added playlist **{name}**"
        ).format(name=name)

    def track_count(self, *, count: int) -> str:
        """Display the size of an enqueue or undo operation."""
        return self.catalog.pgettext("track_count", "Tracks: {count:d}").format(
            count=count
        )

    def session_tracks(self, *, count: int) -> str:
        """Keep the session's established summary wording."""
        return self.catalog.pgettext(
            "session_tracks", "**Total:** {count:d} tracks"
        ).format(count=count)

    def session_skips(self, *, count: int) -> str:
        """Label skipped tracks separately from the total."""
        return self.catalog.pgettext("session_skips", " (skipped: {count:d})").format(
            count=count
        )

    def session_requester(self, *, user_id: int) -> str:
        """Preserve Discord's mention syntax in a one-person session."""
        return self.catalog.pgettext(
            "session_requester", "\n**Requester:** <@{user_id:d}>"
        ).format(user_id=user_id)

    def session_people(self, *, count: int) -> str:
        """Display the number of distinct requesters."""
        return self.catalog.pgettext(
            "session_people", "\n**Requesters:** {count:d}"
        ).format(count=count)

    def queue_footer(self, *, page: int, pages: int, count: int, repeat: str) -> str:
        """Format one paginator snapshot without reading live playback state."""
        return self.catalog.pgettext(
            "queue_footer",
            "Page {page:d}/{pages:d} • Queued: {count:d} • Repeat: {repeat}",
        ).format(page=page, pages=pages, count=count, repeat=repeat)

    def history_footer(self, *, page: int, pages: int, count: int) -> str:
        """Label a frozen session history page."""
        return self.catalog.pgettext(
            "history_footer", "Page {page:d}/{pages:d} • {count:d} total"
        ).format(page=page, pages=pages, count=count)

    def stuck(self, *, requester: str) -> str:
        """Add the observed playback status to an already formatted requester."""
        return self.catalog.pgettext(
            "stuck", "Track stalled, waiting to resume • {requester}"
        ).format(requester=requester)

    def track_exception(self, *, link: str) -> str:
        """Display an escaped track link without exposing external exception text."""
        return self.catalog.pgettext(
            "track_exception",
            "{link}\n**Reason:** The source is temporarily unavailable or did not "
            "respond.",
        ).format(link=link)

    def days(self, *, count: int) -> str:
        """Select complete Russian and English day-count phrases."""
        return self.catalog.npgettext(
            "days", "{count:d} day", "{count:d} days", count
        ).format(count=count)

    def failure(self, reason: MusicResultReason | MusicErrorCode) -> str:
        """Translate the semantic reason, independently of MusicResult.message."""
        messages = {
            MusicResultReason.RECOVERING: self.catalog.pgettext(
                "recovering",
                "Restoring the connection. Try again once recovery finishes.",
            ),
            MusicErrorCode.MUSIC_NODE_UNAVAILABLE: self.catalog.pgettext(
                "unavailable",
                "The music service is currently unavailable. Please try again later.",
            ),
            MusicErrorCode.SOURCE_UNAVAILABLE: self.catalog.pgettext(
                "source_unavailable",
                "Could not load the track. The source is temporarily unavailable or "
                "did not respond.",
            ),
            MusicErrorCode.PLAYER_DISCONNECTED: self.catalog.pgettext(
                "player_disconnected",
                "The player lost its connection. Try playing the track again.",
            ),
            MusicResultReason.PLAYER_LOST: self.catalog.pgettext(
                "player_lost",
                "The player lost its connection. Try playing the track again.",
            ),
            MusicResultReason.PLAYER_CHANGED: self.catalog.pgettext(
                "player_changed",
                "The player changed during the search. Try playing the track again.",
            ),
            MusicResultReason.DISCONNECT_FAILED: self.catalog.pgettext(
                "disconnect_failed", "Could not disconnect from the voice channel."
            ),
            MusicResultReason.NOT_CONNECTED: self.catalog.pgettext(
                "not_connected", "Not connected"
            ),
            MusicResultReason.CONNECTION_FAILED: self.catalog.pgettext(
                "connection_failed", "Could not connect"
            ),
            MusicResultReason.NO_PLAYER: self.catalog.pgettext(
                "no_player", "No player"
            ),
            MusicResultReason.NOTHING_FOUND: self.catalog.pgettext(
                "nothing_found", "Nothing found. Please try again."
            ),
        }
        return messages.get(
            reason,
            self.catalog.pgettext(
                "internal_error",
                "An internal music error occurred. Details have been logged.",
            ),
        )

    def connection(
        self, result: VoiceCheckResult, *, destination: str, origin: str
    ) -> str:
        """Format a voice-join outcome using Discord mentions supplied by the caller."""
        templates = {
            VoiceCheckResult.ALREADY_CONNECTED: self.catalog.pgettext(
                "already_connected", "Already connected to {destination}"
            ),
            VoiceCheckResult.CHANNEL_EMPTY: self.catalog.pgettext(
                "channel_empty", "Voice channel {destination} is empty!"
            ),
            VoiceCheckResult.CONNECTION_FAILED: self.catalog.pgettext(
                "join_failed", "Could not connect to {destination}"
            ),
            VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE: self.failure(
                MusicErrorCode.MUSIC_NODE_UNAVAILABLE
            ),
            VoiceCheckResult.TIMEOUT: self.catalog.pgettext(
                "join_timeout",
                "Connection to {destination} **timed out**\n*Try changing this "
                "channel's region!*",
            ),
            VoiceCheckResult.MOVED_CHANNELS: self.catalog.pgettext(
                "moved", "Moved {origin} -> {destination}"
            ),
            VoiceCheckResult.SUCCESS: self.catalog.pgettext(
                "joined", "Connected to {destination}"
            ),
            VoiceCheckResult.USER_NOT_IN_VOICE: self.catalog.pgettext(
                "not_in_voice", "You must be in a voice channel!"
            ),
            VoiceCheckResult.USER_NOT_MEMBER: self.catalog.pgettext(
                "not_member", "Invalid user type"
            ),
            VoiceCheckResult.RECOVERING: self.failure(MusicResultReason.RECOVERING),
        }
        return templates[result].format(destination=destination, origin=origin)
