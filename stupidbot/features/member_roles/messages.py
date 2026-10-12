"""Typed member roles presentation templates."""

from dataclasses import dataclass

from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class RoleMessages:
    """Format semantic values after selecting the response audience."""

    catalog: Catalog

    def ttl(self, *, days: int) -> str:
        """Label finite snapshot retention."""
        return self.catalog.pgettext("ttl", "TTL: {days:d} дней").format(days=days)

    def enabled(self, *, retention: str) -> str:
        """Explain the committed restoration policy."""
        return self.catalog.pgettext(
            "enabled",
            "Бот будет восстанавливать роли участникам при повторном входе "
            "({retention}).",
        ).format(retention=retention)

    def status(self, *, guild: str) -> str:
        """Label the current guild without translating its name."""
        return self.catalog.pgettext("status", "Статус мониторинга: {guild}").format(
            guild=guild
        )

    def days(self, *, days: int) -> str:
        """Display a finite retention period."""
        return self.catalog.pgettext("days", "{days:d} дней").format(days=days)

    def snapshot(self, *, name: str, count: int, timestamp: str) -> str:
        """Describe one retained role snapshot."""
        return self.catalog.pgettext(
            "snapshot", "- **{name}** — {count:d} ролей, вышел {timestamp}"
        ).format(name=name, count=count, timestamp=timestamp)

    def recent(self, *, shown: int, total: int) -> str:
        """Label the bounded snapshot preview."""
        return self.catalog.pgettext(
            "recent", "Недавние участники ({shown:d} из {total:d})"
        ).format(shown=shown, total=total)

    def footer(self, *, total: int) -> str:
        """Explain the preview limit."""
        return self.catalog.pgettext(
            "footer", "Показаны первые 10 из {total:d} снимков"
        ).format(total=total)

    def deleted(self, *, mention: str) -> str:
        """Confirm deletion after the repository commits."""
        return self.catalog.pgettext(
            "deleted", "Снимок ролей для {mention} удалён."
        ).format(mention=mention)

    def missing(self, *, mention: str) -> str:
        """Report an absent snapshot."""
        return self.catalog.pgettext(
            "missing", "Для {mention} не найдено сохранённых ролей."
        ).format(mention=mention)

    def restored(self, *, count: int) -> str:
        """Count Discord-confirmed role restorations."""
        return self.catalog.pgettext(
            "restored", "Восстановлено ролей: {count:d}"
        ).format(count=count)

    def skipped(self, *, count: int) -> str:
        """Explain partial restoration and retained retry work."""
        return self.catalog.pgettext(
            "skipped",
            "\n\nПропущено {count:d} ролей "
            "(удалены, недоступны или запрос завершился ошибкой). "
            "Существующие невыданные роли сохранены для повторной попытки.",
        ).format(count=count)

    def restoration(self, *, name: str) -> str:
        """Label the member whose roles were processed."""
        return self.catalog.pgettext(
            "restoration", "Восстановление ролей: {name}"
        ).format(name=name)
