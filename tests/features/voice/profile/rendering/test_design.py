"""SVG parsing rejects DTDs before handing input to the native renderer."""

import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from defusedxml.common import DTDForbidden
from defusedxml.ElementTree import fromstring

from stupidbot.features.voice.profile.presentation import CardIdentity
from stupidbot.features.voice.profile.rendering.design import (
    ASSETS,
    bind_design,
    document_bytes,
    fit_texts,
    property_value,
)
from stupidbot.features.voice.profile.rendering.raster import Box, NativeRasterizer
from stupidbot.i18n.policy import Language
from tests.features.voice.profile.test_media import profile_at


class TestTextFitting(unittest.TestCase):
    def test_all_oversized_labels_are_adjusted_before_remeasurement(self) -> None:
        root = fromstring(
            '<svg><text id="title" data-width="100" data-min-size="10" '
            'font-size="20">Title</text><text id="subtitle" data-width="90" '
            'data-min-size="10" font-size="30">Subtitle</text></svg>'
        )
        nodes = {node.attrib["id"]: node for node in root}
        fitted = {"title": Box(0, 0, 100, 10), "subtitle": Box(0, 0, 89, 14)}
        raster = MagicMock(spec=NativeRasterizer)
        raster.query.side_effect = [
            {"title": Box(0, 0, 200, 20), "subtitle": Box(0, 0, 180, 30)},
            fitted,
        ]

        self.assertEqual(fit_texts(root, nodes, raster), fitted)
        self.assertEqual(float(property_value(nodes["title"], "font-size", "")), 10)
        self.assertEqual(float(property_value(nodes["subtitle"], "font-size", "")), 14)
        self.assertEqual(raster.query.call_count, 2)

    def test_minimum_font_size_truncates_at_grapheme_boundaries(self) -> None:
        root = fromstring(
            '<svg><text id="title" data-width="60" data-min-size="10" '
            'font-size="10">Á👩‍💻가나다라마바</text></svg>'
        )
        nodes = {node.attrib["id"]: node for node in root}
        raster = MagicMock(spec=NativeRasterizer)
        raster.query.side_effect = [
            {"title": Box(0, 0, 100, 10)},
            {"title": Box(0, 0, 55, 10)},
        ]

        fit_texts(root, nodes, raster)

        self.assertEqual(nodes["title"].text, "Á👩‍💻…")
        self.assertEqual(property_value(nodes["title"], "font-size", ""), "10")

    def test_invalid_budget_is_rejected_even_without_a_measured_box(self) -> None:
        for budget in ("0", "-1", "nan", "inf"):
            with self.subTest(budget=budget):
                root = fromstring(
                    f'<svg><text id="title" data-width="{budget}">Title</text></svg>'
                )
                nodes = {node.attrib["id"]: node for node in root}
                raster = MagicMock(spec=NativeRasterizer)
                raster.query.return_value = {}

                with self.assertRaisesRegex(ValueError, "Invalid text budget: title"):
                    fit_texts(root, nodes, raster)

    def test_unfittable_text_fails_after_six_measurements(self) -> None:
        root = fromstring(
            '<svg><text id="title" data-width="60" data-min-size="10" '
            'font-size="10">Long title</text></svg>'
        )
        nodes = {node.attrib["id"]: node for node in root}
        raster = MagicMock(spec=NativeRasterizer)
        raster.query.return_value = {"title": Box(0, 0, 100, 10)}

        with self.assertRaisesRegex(ValueError, "after six passes"):
            fit_texts(root, nodes, raster)

        self.assertEqual(raster.query.call_count, 6)


class TestDesignXml(unittest.TestCase):
    def test_timezone_label_uses_conventional_utc_signs(self) -> None:
        for zone, expected in (
            ("Etc/GMT-3", "UTC+3"),
            ("Etc/GMT+11", "UTC-11"),
            ("UTC", "UTC"),
            ("Europe/Moscow", "Europe/Moscow"),
        ):
            with self.subTest(zone=zone):
                raster = MagicMock(spec=NativeRasterizer)
                raster.query.return_value = {}
                design = bind_design(
                    replace(profile_at(), timezone_label=zone),
                    CardIdentity("Name", "Guild", language=Language.EN),
                    ASSETS / "profile.svg",
                    raster,
                )
                root = fromstring(design.svg)
                label = next(
                    node for node in root.iter() if node.get("id") == "timezone-label"
                )
                self.assertEqual(label.text, expected)

    def test_scope_label_displays_history_date_or_missing_history(self) -> None:
        for since, expected in (
            (date(2026, 9, 21), "On this server · history since Sep 21, 2026"),
            (None, "On this server · no observed history"),
        ):
            with self.subTest(since=since):
                raster = MagicMock(spec=NativeRasterizer)
                raster.query.return_value = {}
                design = bind_design(
                    replace(profile_at(), history_since=since),
                    CardIdentity("Name", "Guild", language=Language.EN),
                    ASSETS / "profile.svg",
                    raster,
                )
                root = fromstring(design.svg)
                label = next(
                    node for node in root.iter() if node.get("id") == "scope-label"
                )
                self.assertEqual(label.text, expected)

    def test_dtd_and_entity_payloads_never_reach_native_renderer(self) -> None:
        declarations = (
            "<!DOCTYPE svg>",
            '<!DOCTYPE svg SYSTEM "https://example.invalid/external.dtd">',
            '<!DOCTYPE svg [<!ENTITY secret SYSTEM "file:///private.txt">]>',
            '<!DOCTYPE svg [<!ENTITY a "1234567890"><!ENTITY b "&a;&a;&a;&a;&a;">]>',
        )
        with TemporaryDirectory() as directory:
            template = Path(directory) / "profile.svg"
            for declaration in declarations:
                with self.subTest(declaration=declaration):
                    template.write_text(
                        declaration + '<svg xmlns="http://www.w3.org/2000/svg"/>',
                        encoding="utf-8",
                    )
                    raster = MagicMock(spec=NativeRasterizer)
                    with self.assertRaises(DTDForbidden):
                        bind_design(
                            profile_at(),
                            CardIdentity("N", "G", language=Language.EN),
                            template,
                            raster,
                        )
                    raster.query.assert_not_called()
                    raster.layers.assert_not_called()

    def test_serialization_preserves_svg_namespace_and_xml_declaration(self) -> None:
        root = fromstring(
            '<svg xmlns="http://www.w3.org/2000/svg"><text>Hi</text></svg>'
        )
        self.assertEqual(
            document_bytes(root),
            b"<?xml version='1.0' encoding='utf-8'?>\n"
            + b'<svg xmlns="http://www.w3.org/2000/svg"><text>Hi</text></svg>',
        )
