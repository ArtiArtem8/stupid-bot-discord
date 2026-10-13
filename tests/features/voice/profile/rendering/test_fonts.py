"""Keep translated Hangul labels renderable without host-specific CJK fonts."""

from unittest import TestCase

from PIL import Image, ImageDraw, ImageFont

from stupidbot.i18n.catalog import ROOT, catalog
from stupidbot.i18n.policy import Language


class ProfileFontTests(TestCase):
    def test_bundled_korean_font_contains_every_translated_hangul_glyph(self) -> None:
        font = ImageFont.truetype(str(ROOT / "assets/fonts/NotoSansKR-UI.ttf"), 24)

        def render_glyph(character: str) -> bytes:
            with Image.new("L", (48, 48)) as image:
                ImageDraw.Draw(image).text((0, 0), character, font=font, fill=255)
                return image.tobytes()

        missing_glyph = render_glyph("\U0010ffff")
        messages = catalog("voice", Language.KO)
        glyphs: set[str] = set()
        for key in messages.keys:
            context, source = key.split("\x04", 1)
            glyphs.update(
                character
                for character in messages.pgettext(context, source)
                if "가" <= character <= "힣"
            )
        self.assertGreater(len(glyphs), 0)
        for character in sorted(glyphs):
            with self.subTest(character=character):
                self.assertNotEqual(render_glyph(character), missing_glyph)
