"""Typed member roles presentation templates."""

from dataclasses import dataclass

from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class RoleMessages:
    """Format role-restoration messages with counts in the selected language."""

    catalog: Catalog

    def ttl(self, *, days: int) -> str:
        return self.catalog.npgettext(
            "ttl", "TTL: {days:d} day", "TTL: {days:d} days", days
        ).format(days=days)

    def enabled(self, *, retention: str) -> str:
        """Explain the committed restoration policy."""
        return self.catalog.pgettext(
            "enabled",
            "The bot will restore members' roles when they rejoin ({retention}).",
        ).format(retention=retention)

    def status(self, *, guild: str) -> str:
        """Label the current guild without translating its name."""
        return self.catalog.pgettext("status", "Monitoring status: {guild}").format(
            guild=guild
        )

    def days(self, *, days: int) -> str:
        return self.catalog.npgettext(
            "days", "{days:d} day", "{days:d} days", days
        ).format(days=days)

    def snapshot(self, *, name: str, count: int, timestamp: str) -> str:
        return self.catalog.npgettext(
            "snapshot",
            "- **{name}** — {count:d} role, left {timestamp}",
            "- **{name}** — {count:d} roles, left {timestamp}",
            count,
        ).format(name=name, count=count, timestamp=timestamp)

    def recent(self, *, shown: int, total: int) -> str:
        return self.catalog.pgettext(
            "recent", "Recent members ({shown:d} of {total:d})"
        ).format(shown=shown, total=total)

    def footer(self, *, total: int) -> str:
        return self.catalog.pgettext(
            "footer", "Showing the first 10 of {total:d} snapshots"
        ).format(total=total)

    def deleted(self, *, mention: str) -> str:
        """Confirm deletion after the repository commits."""
        return self.catalog.pgettext(
            "deleted", "Role snapshot for {mention} deleted."
        ).format(mention=mention)

    def missing(self, *, mention: str) -> str:
        return self.catalog.pgettext(
            "missing", "No saved roles found for {mention}."
        ).format(mention=mention)

    def restored(self, *, count: int) -> str:
        """Count Discord-confirmed role restorations."""
        return self.catalog.pgettext("restored", "Roles restored: {count:d}").format(
            count=count
        )

    def skipped(self, *, count: int) -> str:
        """Explain partial restoration and retained retry work."""
        return self.catalog.npgettext(
            "skipped",
            "\n\nSkipped {count:d} role (deleted, inaccessible or the request "
            "failed). Existing roles not yet granted have been kept for another "
            "attempt.",
            "\n\nSkipped {count:d} roles (deleted, inaccessible or the request "
            "failed). Existing roles not yet granted have been kept for another "
            "attempt.",
            count,
        ).format(count=count)

    def restoration(self, *, name: str) -> str:
        return self.catalog.pgettext("restoration", "Role restoration: {name}").format(
            name=name
        )
