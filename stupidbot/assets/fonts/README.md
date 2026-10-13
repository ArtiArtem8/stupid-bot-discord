# Profile fonts

Inter supplies Latin and Cyrillic text: [license](OFL.txt).
`NotoSansKR-UI.ttf` supplies Korean profile labels: [license](NotoSansKR-OFL.txt).
SVG templates prefer Inter, then Noto Sans KR. Host fonts remain the fallback
for user names containing glyphs outside these bundled fonts.

The Korean asset is a regular-weight subset of
[Google Fonts' Noto Sans KR](https://github.com/google/fonts/tree/main/ofl/notosanskr).
The downloaded `NotoSansKR[wght].ttf` has SHA-256
`194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252`.
The subset contains all characters in the Korean voice catalog, including its
source text. It is not a complete Korean system font.

To rebuild from that source, run these commands from the repository root.
FontTools is an offline asset tool, not an application dependency:

```sh
uvx --from fonttools==4.66.1 fonttools varLib.instancer 'NotoSansKR[wght].ttf' wght=400 --output NotoSansKR-Regular.ttf
uvx --from fonttools==4.66.1 fonttools subset NotoSansKR-Regular.ttf --text-file=stupidbot/features/voice/locales/ko.po --no-hinting --output-file=stupidbot/assets/fonts/NotoSansKR-UI.ttf
uv run --locked pytest -q tests/features/voice/profile/rendering/test_fonts.py
```

Rebuild the subset when translated labels add Hangul characters. Keep the
upstream license beside the font and verify native rendering after changes.
