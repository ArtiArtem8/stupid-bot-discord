"""Load verified gettext catalogs without a process-global language setting."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from gettext import GNUTranslations
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import MappingProxyType

from stupidbot.common.json import get_json
from stupidbot.i18n.policy import Language

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ("features/preferences", "features/ask", "discord_ui", "features/voice")


def pgettext_noop(_context: str, message: str) -> str:
    """Mark a static choice for translation once its audience is known."""
    return message


@dataclass(frozen=True, slots=True)
class Catalog:
    """A fixed language catalog; parameterized messages belong in typed facades."""

    translation: GNUTranslations
    revision: str
    keys: frozenset[str]
    domain: str

    def pgettext(self, context: str, message: str) -> str:
        """Translate a literal message in its owning context."""
        if f"{context}\x04{message}" not in self.keys:
            _warn_missing(self.domain, context, message)
        return self.translation.pgettext(context, message)

    def npgettext(self, context: str, singular: str, plural: str, count: int) -> str:
        """Select the complete plural phrase before typed interpolation."""
        if f"{context}\x04{singular}" not in self.keys:
            _warn_missing(self.domain, context, singular)
        return self.translation.npgettext(context, singular, plural, count)


def load_catalogs(root: Path) -> Mapping[tuple[str, Language], Catalog]:
    """Verify compiled catalogs against their checked release digest before use.

    The development compiler checks extraction completeness and placeholders.
    Release digests cover PO and MO together, so editing either without
    recompilation fails startup. Run outside the event loop before producers.
    """
    catalogs: dict[tuple[str, Language], Catalog] = {}
    for owner in DOMAINS:
        directory = root / owner / "locales"
        domain = directory.parent.name
        keys = _catalog_keys(directory)
        index = (directory / "index.json").read_bytes()
        for language in Language:
            source = (directory / f"{language}.po").read_bytes()
            compiled = (directory / f"{language}.mo").read_bytes()
            revision = sha256(source + compiled + index).hexdigest()
            if revision != (directory / f"{language}.sha256").read_text().strip():
                raise ValueError(
                    f"Catalog release digest mismatch: {domain}/{language}"
                )
            key = domain, language
            if key in catalogs:
                raise ValueError(f"Duplicate catalog domain: {domain}")
            catalogs[key] = Catalog(
                GNUTranslations(BytesIO(compiled)), revision, keys, domain
            )
    if not catalogs:
        raise ValueError("No translation catalogs were shipped")
    return MappingProxyType(catalogs)


def _catalog_keys(directory: Path) -> frozenset[str]:
    index = get_json(directory / "index.json")
    entries = None if index is None else index.get("keys")
    if not isinstance(entries, list):
        raise ValueError(f"Missing catalog index: {directory}")
    keys: set[str] = set()
    for entry in entries:
        if not isinstance(entry, str) or entry in keys:
            raise ValueError(f"Invalid catalog key: {directory}")
        keys.add(entry)
    return frozenset(keys)


@cache
def _warn_missing(domain: str, context: str, _message: str) -> None:
    logger.warning(
        "Missing translation in %s/%s; using Russian source", domain, context
    )


@cache
def catalogs() -> Mapping[tuple[str, Language], Catalog]:
    """Load the immutable release once; startup eagerly calls this off-loop."""
    return load_catalogs(ROOT)


def catalog(domain: str, language: Language) -> Catalog:
    """Select an already loaded catalog without changing other responses."""
    return catalogs()[domain, language]
