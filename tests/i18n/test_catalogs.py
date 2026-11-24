"""Verify release catalogs, placeholder contracts and startup integrity."""

from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from unittest import TestCase

from babel.messages.catalog import Catalog as BabelCatalog
from babel.messages.catalog import Message

from stupidbot.i18n.catalog import DOMAINS, ROOT, load_catalogs
from stupidbot.i18n.policy import Language
from tools.catalogs import compile_owner, validate_message, validate_template


class CatalogTests(TestCase):
    def test_english_source_convention_rejects_mixed_keys_and_translation_drift(
        self,
    ) -> None:
        for message in (
            Message("Сохранить", "Save", context="save"),
            Message("Save", "Save", context="сохранение"),
            Message("Save", "Save changes", context="save"),
            Message(
                ("{count:d} item", "{total:d} items"),
                ("{count:d} item", "{total:d} items"),
            ),
            Message(
                ("{count:d} item", "{count:d} items"),
                ("{count:d} item", "{count:d} things"),
            ),
        ):
            with self.subTest(message=message.id), self.assertRaises(ValueError):
                validate_message(message, BabelCatalog(locale="en"))

    def test_english_sources_allow_unicode_symbols_and_russian_plural_forms(
        self,
    ) -> None:
        validate_message(
            Message("Calculate π →", "Calculate π →"), BabelCatalog(locale="en")
        )
        validate_message(
            Message(
                ("{count:d} item", "{count:d} items"),
                ("{count:d} предмет", "{count:d} предмета", "{count:d} предметов"),
            ),
            BabelCatalog(locale="ru"),
        )

    def test_shipped_catalogs_load_translations_for_every_language(self) -> None:
        loaded = load_catalogs(ROOT)
        self.assertEqual(len(loaded), len(DOMAINS) * len(Language))
        self.assertEqual(
            loaded["preferences", Language.EN].pgettext("save", "Save"), "Save"
        )
        self.assertEqual(
            loaded["preferences", Language.RU].pgettext("save", "Save"),
            "Сохранить",
        )
        self.assertEqual(
            loaded["preferences", Language.KO].pgettext("save", "Save"), "저장"
        )

    def test_korean_uses_one_plural_form(self) -> None:
        validate_message(
            Message(("{count:d} item", "{count:d} items"), ("{count:d}개",)),
            BabelCatalog(locale="ko"),
        )

    def test_load_catalogs_rejects_changed_artifacts_or_missing_source(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for owner in DOMAINS:
                copytree(ROOT / owner / "locales", root / owner / "locales")
            locales = root / DOMAINS[0] / "locales"
            for name in ("en.po", "en.mo", "index.json"):
                with self.subTest(artifact=name):
                    artifact = locales / name
                    original = artifact.read_bytes()
                    artifact.write_bytes(original + b"\n")
                    try:
                        with self.assertRaisesRegex(ValueError, "digest mismatch"):
                            load_catalogs(root)
                    finally:
                        artifact.write_bytes(original)

            (locales / "en.po").unlink()
            with self.assertRaises(FileNotFoundError):
                load_catalogs(root)

    def test_bad_placeholders_empty_fuzzy_and_incomplete_plurals_are_rejected(
        self,
    ) -> None:
        for translated in (
            "{other:d}",
            "{count}",
            "{count.real}",
            "",
            "{count:{width}}",
        ):
            with self.subTest(translated=translated), self.assertRaises(ValueError):
                validate_template("{count:d}", translated)
        catalog = BabelCatalog(locale="ru")
        for message in (
            Message("text", "перевод", flags=["fuzzy"]),
            Message(("{count:d} item", "{count:d} items"), ("{count:d}", "{count:d}")),
            Message(
                ("{count:d} item", "{count:d} items"), ("{count:d}", "{count:d}", "")
            ),
        ):
            with self.subTest(message=message.id), self.assertRaises(ValueError):
                validate_message(message, catalog)

    def test_duplicate_po_entries_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            owner = Path(temporary) / "preferences"
            copytree(ROOT / "features/preferences", owner)
            source = owner / "locales/ru.po"
            with source.open("a", encoding="utf-8") as stream:
                stream.write('\nmsgctxt "save"\nmsgid "Save"\nmsgstr "Сохранить"\n')
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                compile_owner(owner / "locales", check=False)
