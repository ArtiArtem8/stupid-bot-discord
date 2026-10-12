"""Typed access presentation templates."""

from dataclasses import dataclass

from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class AccessMessages:
    """Format semantic values after selecting the response audience."""

    catalog: Catalog

    def already_blocked(self, *, mention: str) -> str:
        """Report an already blocked member."""
        return self.catalog.pgettext(
            "already_blocked", "{mention} уже заблокирован."
        ).format(mention=mention)

    def not_blocked(self, *, mention: str) -> str:
        """Report an already unblocked member."""
        return self.catalog.pgettext(
            "not_blocked", "{mention} не заблокирован."
        ).format(mention=mention)

    def no_history(self, *, mention: str) -> str:
        """Report an absent block history."""
        return self.catalog.pgettext(
            "no_history", "{mention} не имеет истории блокировок."
        ).format(mention=mention)

    def blocked_status(self, *, admin_id: int, timestamp: str, reason: str) -> str:
        """Describe the latest committed block."""
        return self.catalog.pgettext(
            "blocked_status",
            "**Заблокирован**\n• Администратор: <@{admin_id:d}>\n"
            "• Дата: {timestamp}\n• Причина: {reason}\n",
        ).format(admin_id=admin_id, timestamp=timestamp, reason=reason)

    def blocks(self, *, count: int) -> str:
        """Count recorded blocks."""
        return self.catalog.pgettext("blocks", "• Всего блокировок: {count:d}").format(
            count=count
        )

    def unblocks(self, *, count: int) -> str:
        """Count recorded unblock events."""
        return self.catalog.pgettext(
            "unblocks", "• Всего разблокировок: {count:d}"
        ).format(count=count)

    def first_block(self, *, timestamp: str) -> str:
        """Display a Discord-local timestamp."""
        return self.catalog.pgettext(
            "first_block", "• Первая блокировка: {timestamp}"
        ).format(timestamp=timestamp)

    def last_unblock(self, *, timestamp: str) -> str:
        """Display the latest unblock timestamp."""
        return self.catalog.pgettext(
            "last_unblock", "• Последняя разблокировка: {timestamp}"
        ).format(timestamp=timestamp)

    def danger(self, *, level: str) -> str:
        """Label the existing block-count classification."""
        return self.catalog.pgettext("danger", "Уровень проблемности: {level}").format(
            level=level
        )

    def list_title(self, *, count: int) -> str:
        """Label the full frozen list count."""
        return self.catalog.pgettext(
            "list_title", "Заблокированные пользователи ({count:d})"
        ).format(count=count)

    def list_footer(self, *, page: int, pages: int, detail: str) -> str:
        """Label the current page and detail mode."""
        return self.catalog.pgettext(
            "list_footer", "Страница {page:d}/{pages:d}{detail}"
        ).format(page=page, pages=pages, detail=detail)

    def blocked(self, *, mention: str) -> str:
        """Confirm the committed block action."""
        return self.catalog.pgettext("blocked", "{mention} был заблокирован").format(
            mention=mention
        )

    def unblocked(self, *, mention: str) -> str:
        """Confirm the committed unblock action."""
        return self.catalog.pgettext("unblocked", "{mention} был разблокирован").format(
            mention=mention
        )

    def event(
        self, *, icon: str, action: str, timestamp: str, admin_id: int, reason: str
    ) -> str:
        """Format one bounded audit event without translating its reason."""
        return self.catalog.pgettext(
            "event",
            "{icon} **{action}** {timestamp}\n"
            "• Админ: <@{admin_id:d}>\n• Причина: {reason}",
        ).format(
            icon=icon,
            action=action,
            timestamp=timestamp,
            admin_id=admin_id,
            reason=reason,
        )

    def name_event(self, *, timestamp: str, name: str) -> str:
        """Format an observed name without translating it."""
        return self.catalog.pgettext(
            "name_event", "{timestamp}:\n• Имя: {name}"
        ).format(timestamp=timestamp, name=name)

    def departed(self, *, user_id: int) -> str:
        """Identify a member no longer present."""
        return self.catalog.pgettext(
            "departed", "Пользователь покинул сервер `{user_id:d}`"
        ).format(user_id=user_id)

    def user(self, *, identity: str) -> str:
        """Label an unchanged Discord identity."""
        return self.catalog.pgettext("user", "**Пользователь:** {identity}").format(
            identity=identity
        )

    def current_name(self, *, name: str) -> str:
        """Label the current display name."""
        return self.catalog.pgettext("current_name", "• Текущее имя: {name}").format(
            name=name
        )

    def last_block(self, *, timestamp: str) -> str:
        """Label the most recent block timestamp."""
        return self.catalog.pgettext(
            "last_block", "• Последняя блокировка: {timestamp}"
        ).format(timestamp=timestamp)

    def reason(self, *, reason: str) -> str:
        """Label an unchanged administrator-supplied reason."""
        return self.catalog.pgettext("reason", "• Причина: {reason}").format(
            reason=reason
        )

    def admin(self, *, admin_id: int) -> str:
        """Keep the Discord administrator mention intact."""
        return self.catalog.pgettext(
            "admin", "• Администратор: <@{admin_id:d}>"
        ).format(admin_id=admin_id)
