"""SVG parsing rejects DTDs before handing input to the native renderer."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from defusedxml.common import DTDForbidden
from defusedxml.ElementTree import fromstring

from cogs.voice.profile.design import CardIdentity, bind_design, document_bytes
from cogs.voice.profile.raster import NativeRasterizer
from tests.cogs.voice.profile.test_media import profile_at


class TestDesignXml(unittest.TestCase):
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
