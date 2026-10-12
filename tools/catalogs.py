"""Validate owner-local gettext catalogs and compile reproducible release files."""

import argparse
import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from string import Formatter
from unicodedata import name

from babel import Locale
from babel.messages.catalog import Catalog, Message
from babel.messages.extract import extract
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parents[1] / "stupidbot"


def fields(template: str) -> dict[str, tuple[str, str | None]]:
    """Reject traversal and preserve names, conversions and format specifications."""
    result: dict[str, tuple[str, str | None]] = {}
    for _, field, spec, conversion in Formatter().parse(template):
        if field is None:
            continue
        if not field.isidentifier() or conversion not in {None, "s", "r"}:
            raise ValueError(f"Unsafe placeholder: {field}")
        format_spec = spec or ""
        if "{" in format_spec or "}" in format_spec:
            raise ValueError("Nested placeholders are not supported")
        contract = format_spec, conversion
        if field in result and result[field] != contract:
            raise ValueError(f"Conflicting placeholder: {field}")
        result[field] = contract
    return result


def validate_template(source: str, translated: str) -> None:
    """Require nonempty translations with exactly the same placeholder contract."""
    if not translated or fields(source) != fields(translated):
        raise ValueError(f"Translation placeholder mismatch: {source!r}")


def validate_message(message: Message, catalog: Catalog) -> None:
    """Validate all forms against the same source template contract."""
    if not message.id:
        return
    if message.fuzzy:
        raise ValueError(f"Fuzzy message: {message.id}")
    validate_source(message, catalog)
    original = message.id if isinstance(message.id, str) else message.id[0]
    translations = (
        (message.string,) if isinstance(message.string, str) else message.string
    )
    if not translations:
        raise ValueError(f"Missing translation: {message.id}")
    if not isinstance(message.id, str) and len(translations) != catalog.num_plurals:
        raise ValueError(f"Missing plural form: {message.id}")
    for translated in translations:
        validate_template(original, translated)


def validate_source(message: Message, catalog: Catalog) -> None:
    """Keep English source text canonical and plural placeholder contracts equal."""
    sources = (message.id,) if isinstance(message.id, str) else message.id
    for source in sources:
        if any("CYRILLIC" in name(character, "") for character in source):
            raise ValueError(f"Use English source text: {source!r}")
        validate_template(sources[0], source)
    if message.context and not message.context.isascii():
        raise ValueError(f"Use an English context: {message.context!r}")
    locale = catalog.locale
    if isinstance(locale, Locale) and locale.language == "en":
        translations = (
            (message.string,) if isinstance(message.string, str) else message.string
        )
        if tuple(sources) != tuple(translations or ()):
            raise ValueError(f"English translation differs from source: {message.id!r}")


def extract_owner(directory: Path) -> set[tuple[str | None, str | tuple[str, ...]]]:
    """Extract owner literals including command metadata for Discord's translator."""
    expected: set[tuple[str | None, str | tuple[str, ...]]] = set()
    for path in directory.parent.rglob("*.py"):
        with path.open("rb") as stream:
            for _, message, _, context in extract(
                "python",
                stream,
                keywords={
                    "pgettext": ((1, "c"), 2),
                    "pgettext_noop": ((1, "c"), 2),
                    "npgettext": ((1, "c"), 2, 3),
                    "locale_str": (1,),
                },
            ):
                expected.add((context or "command", message))
    return expected


def compile_owner(directory: Path, *, check: bool) -> None:
    """Validate extracted literals, every plural form, and committed MO/digest files."""
    expected = extract_owner(directory)
    index = (
        json.dumps(
            {
                "keys": sorted(
                    f"{context}\x04"
                    f"{message if isinstance(message, str) else message[0]}"
                    for context, message in expected
                )
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        + b"\n"
    )
    _publish(directory / "index.json", index, check=check)
    for language in ("ru", "en"):
        source = directory / f"{language}.po"
        with source.open("rb") as stream:
            catalog = read_po(stream, locale=language, abort_invalid=True)
        declarations = sum(
            line.lstrip().startswith("msgid ")
            for line in source.read_text(encoding="utf-8").splitlines()
        )
        if declarations != len(catalog) + 1:
            raise ValueError(f"Duplicate message or missing catalog header: {source}")
        actual = {(message.context, message.id) for message in catalog if message.id}
        if actual != expected:
            raise ValueError(
                f"Catalog extraction mismatch: {source}: {actual ^ expected}"
            )
        if list(catalog.check()):
            raise ValueError(f"Invalid catalog: {source}")
        for message in catalog:
            validate_message(message, catalog)
        output = BytesIO()
        write_mo(output, catalog)
        compiled = output.getvalue()
        digest = (
            sha256(source.read_bytes() + compiled + index).hexdigest() + "\n"
        ).encode()
        for target, content in (
            (source.with_suffix(".mo"), compiled),
            (source.with_suffix(".sha256"), digest),
        ):
            _publish(target, content, check=check)


def _publish(target: Path, content: bytes, *, check: bool) -> None:
    if check:
        if target.read_bytes() != content:
            raise ValueError(f"Stale compiled catalog: {target}")
    else:
        target.write_bytes(content)


class Arguments(argparse.Namespace):
    """Typed catalog maintenance arguments."""

    check: bool = False


def main() -> None:
    """Compile catalogs, or use --check for read-only release verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(namespace=Arguments())
    for directory in sorted(ROOT.rglob("locales")):
        compile_owner(directory, check=args.check)


if __name__ == "__main__":
    main()
