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
        return self.catalog.pgettext("requester", "Запросил: {name}").format(name=name)

    def volume(self, *, percent: int) -> str:
        """Display committed or queried playback volume."""
        return self.catalog.pgettext("volume", "Громкость: {percent:d}%").format(
            percent=percent
        )

    def playlist_added(self, *, name: str) -> str:
        """Use the caller's bounded Markdown-safe playlist name."""
        return self.catalog.pgettext(
            "playlist_added", "Добавлен плейлист **{name}**"
        ).format(name=name)

    def track_count(self, *, count: int) -> str:
        """Display the size of an enqueue or undo operation."""
        return self.catalog.pgettext("track_count", "Треков: {count:d}").format(
            count=count
        )

    def session_tracks(self, *, count: int) -> str:
        """Keep the session's established summary wording."""
        return self.catalog.pgettext(
            "session_tracks", "**Всего:** {count:d} шт."
        ).format(count=count)

    def session_skips(self, *, count: int) -> str:
        """Label skipped tracks separately from the total."""
        return self.catalog.pgettext("session_skips", " (скипов: {count:d})").format(
            count=count
        )

    def session_requester(self, *, user_id: int) -> str:
        """Preserve Discord's mention syntax in a one-person session."""
        return self.catalog.pgettext(
            "session_requester", "\n**Заказчик:** <@{user_id:d}>"
        ).format(user_id=user_id)

    def session_people(self, *, count: int) -> str:
        """Display the number of distinct requesters."""
        return self.catalog.pgettext(
            "session_people", "\n**Заказчиков:** {count:d} чел."
        ).format(count=count)

    def queue_footer(self, *, page: int, pages: int, count: int, repeat: str) -> str:
        """Format one paginator snapshot without reading live playback state."""
        return self.catalog.pgettext(
            "queue_footer",
            "Стр. {page:d}/{pages:d} • В очереди: {count:d} • Повтор: {repeat}",
        ).format(page=page, pages=pages, count=count, repeat=repeat)

    def history_footer(self, *, page: int, pages: int, count: int) -> str:
        """Label a frozen session history page."""
        return self.catalog.pgettext(
            "history_footer", "Стр. {page:d}/{pages:d} • {count:d} всего"
        ).format(page=page, pages=pages, count=count)

    def stuck(self, *, requester: str) -> str:
        """Add the observed playback status to an already formatted requester."""
        return self.catalog.pgettext(
            "stuck", "Трек застрял, жду продолжения • {requester}"
        ).format(requester=requester)

    def track_exception(self, *, link: str) -> str:
        """Display an escaped track link without exposing external exception text."""
        return self.catalog.pgettext(
            "track_exception",
            "{link}\n**Причина:** Источник временно недоступен или не ответил.",
        ).format(link=link)

    def days(self, *, count: int) -> str:
        """Select complete Russian and English day-count phrases."""
        return self.catalog.npgettext(
            "days", "{count:d} день", "{count:d} дня", count
        ).format(count=count)

    def failure(self, reason: MusicResultReason | MusicErrorCode) -> str:
        """Translate the semantic reason, independently of MusicResult.message."""
        messages = {
            MusicResultReason.RECOVERING: self.catalog.pgettext(
                "recovering",
                "Восстанавливаю соединение. Попробуй ещё раз после завершения.",
            ),
            MusicErrorCode.MUSIC_NODE_UNAVAILABLE: self.catalog.pgettext(
                "unavailable", "Музыкальный сервис сейчас недоступен. Попробуйте позже."
            ),
            MusicErrorCode.SOURCE_UNAVAILABLE: self.catalog.pgettext(
                "source_unavailable",
                "Не удалось загрузить трек. "
                "Источник временно недоступен или не ответил.",
            ),
            MusicErrorCode.PLAYER_DISCONNECTED: self.catalog.pgettext(
                "player_disconnected",
                "Плеер потерял соединение. Попробуйте запустить трек ещё раз.",
            ),
            MusicResultReason.PLAYER_LOST: self.catalog.pgettext(
                "player_lost",
                "Плеер потерял соединение. Попробуй запустить трек ещё раз.",
            ),
            MusicResultReason.PLAYER_CHANGED: self.catalog.pgettext(
                "player_changed",
                "Плеер изменился во время поиска. Попробуй запустить трек ещё раз.",
            ),
            MusicResultReason.DISCONNECT_FAILED: self.catalog.pgettext(
                "disconnect_failed", "Не удалось отключиться от голосового канала."
            ),
            MusicResultReason.NOT_CONNECTED: self.catalog.pgettext(
                "not_connected", "Нет подключения"
            ),
            MusicResultReason.CONNECTION_FAILED: self.catalog.pgettext(
                "connection_failed", "Не удалось подключиться"
            ),
            MusicResultReason.NO_PLAYER: self.catalog.pgettext(
                "no_player", "Нет проигрывателя"
            ),
            MusicResultReason.NOTHING_FOUND: self.catalog.pgettext(
                "nothing_found", "Ничего не нашлось. Попробуйте ещё раз."
            ),
        }
        return messages.get(
            reason,
            self.catalog.pgettext(
                "internal_error",
                "Внутренняя ошибка музыкального модуля. Детали записаны в лог.",
            ),
        )

    def connection(
        self, result: VoiceCheckResult, *, destination: str, origin: str
    ) -> str:
        """Format a voice-join outcome using Discord mentions supplied by the caller."""
        templates = {
            VoiceCheckResult.ALREADY_CONNECTED: self.catalog.pgettext(
                "already_connected", "Уже подключён к {destination}"
            ),
            VoiceCheckResult.CHANNEL_EMPTY: self.catalog.pgettext(
                "channel_empty", "Голосовой канал {destination} пуст!"
            ),
            VoiceCheckResult.CONNECTION_FAILED: self.catalog.pgettext(
                "join_failed", "Ошибка подключения к {destination}"
            ),
            VoiceCheckResult.MUSIC_SERVICE_UNAVAILABLE: self.failure(
                MusicErrorCode.MUSIC_NODE_UNAVAILABLE
            ),
            VoiceCheckResult.TIMEOUT: self.catalog.pgettext(
                "join_timeout",
                "Время подключения к {destination} **истекло**\n"
                "*Попробуйте сменить регион этого канала!*",
            ),
            VoiceCheckResult.MOVED_CHANNELS: self.catalog.pgettext(
                "moved", "Переместился {origin} -> {destination}"
            ),
            VoiceCheckResult.SUCCESS: self.catalog.pgettext(
                "joined", "Успешно подключился к {destination}"
            ),
            VoiceCheckResult.USER_NOT_IN_VOICE: self.catalog.pgettext(
                "not_in_voice", "Вы должны быть в голосовом канале!"
            ),
            VoiceCheckResult.USER_NOT_MEMBER: self.catalog.pgettext(
                "not_member", "Неверный тип пользователя"
            ),
            VoiceCheckResult.RECOVERING: self.failure(MusicResultReason.RECOVERING),
        }
        return templates[result].format(destination=destination, origin=origin)
