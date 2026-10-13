"""Typed suffixes for suggestions based on the live command tree."""

from dataclasses import dataclass

from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class CommandHelpMessages:
    """Keep Discord command mentions intact during translation."""

    catalog: Catalog

    def alternatives(self, *, primary: str, alternative: str) -> str:
        """Offer two similarly ranked visible commands."""
        return self.catalog.pgettext(
            "alternatives", "-# perhaps you were looking for {primary} or {alternative}"
        ).format(primary=primary, alternative=alternative)

    def suggestion(self, *, command: str, confident: bool) -> str:
        """Choose suggestion wording based on match confidence."""
        template = (
            self.catalog.pgettext("try", "-# try {command}")
            if confident
            else self.catalog.pgettext("maybe", "-# perhaps {command}")
        )
        return template.format(command=command)
