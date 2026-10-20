"""Typed report labels; submitted user text is never translated."""

from dataclasses import dataclass

from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class ReportMessages:
    """Render report metadata after selecting its audience."""

    catalog: Catalog

    def receipt(self, *, report_id: str) -> str:
        """Identify a committed report in the private confirmation."""
        return self.catalog.pgettext(
            "receipt", "-# Your personal ID: `{report_id}`"
        ).format(report_id=report_id)

    def guild(self, *, name: str) -> str:
        return self.catalog.pgettext("guild", "**Server:** {name}").format(name=name)

    def channel(self, *, name: str) -> str:
        return self.catalog.pgettext("channel", "**Channel:** {name}").format(name=name)

    def destination(self, *, mention: str) -> str:
        """Confirm the committed notification destination."""
        return self.catalog.pgettext(
            "destination", "Report channel set to {mention}"
        ).format(mention=mention)
