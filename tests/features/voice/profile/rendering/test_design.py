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
)
from stupidbot.features.voice.profile.rendering.raster import NativeRasterizer
from tests.features.voice.profile.test_media import profile_at


class TestDesignXml(unittest.TestCase):
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
                    CardIdentity("Name", "Guild"),
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
                            profile_at(), CardIdentity("N", "G"), template, raster
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
