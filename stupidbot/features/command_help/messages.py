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
            "alternatives", "-# возможно, вы искали {primary} или {alternative}"
        ).format(primary=primary, alternative=alternative)

    def suggestion(self, *, command: str, confident: bool) -> str:
        """Retain the existing distinction between strong and weak matches."""
        template = (
            self.catalog.pgettext("try", "-# попробуйте {command}")
            if confident
            else self.catalog.pgettext("maybe", "-# возможно {command}")
        )
        return template.format(command=command)
