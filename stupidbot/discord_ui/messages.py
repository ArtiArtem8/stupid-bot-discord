"""Typed shared feedback templates."""

from dataclasses import dataclass

from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class FeedbackMessages:
    """Format already localized Discord timestamps, never wall-clock text."""

    catalog: Catalog

    def page(self, *, name: str, page: int) -> str:
        """Label a continuation field without modifying its original name."""
        return self.catalog.pgettext("page", "{name} (page {page:d})").format(
            name=name, page=page
        )

    def cooldown(self, *, relative_time: str) -> str:
        """Show Discord's viewer-local relative timestamp."""
        return self.catalog.pgettext("cooldown", "Please wait: {relative_time}").format(
            relative_time=relative_time
        )

    def deletion(self, *, relative_time: str) -> str:
        """Keep the removal notice a complete phrase when truncating embeds."""
        return self.catalog.pgettext("deletion", "-# Deletes {relative_time}").format(
            relative_time=relative_time
        )
