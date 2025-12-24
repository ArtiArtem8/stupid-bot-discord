from __future__ import annotations

import unittest
from typing import Any, Mapping

from utils.embeds import EmbedLimits, SafeEmbed
from utils.text_utils import truncate_text


class TestSafeEmbed(unittest.TestCase):
    def test_init_truncates_title_and_description(self) -> None:
        limits = EmbedLimits(title=5, description=7)
        e = SafeEmbed(limits=limits, title="123456789", description="abcdefghi")
        self.assertEqual(e.title, truncate_text("123456789", 5))
        self.assertEqual(e.description, truncate_text("abcdefghi", 7))

    def test_init_no_title_description(self) -> None:
        limits = EmbedLimits(title=5, description=7)
        e = SafeEmbed(limits=limits)
        self.assertIsNone(e.title)
        self.assertIsNone(e.description)

    def test_set_footer_truncates_and_returns_self(self) -> None:
        limits = EmbedLimits(footer=5)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.set_footer(text="123456789", icon_url=None)
        self.assertIs(ret, e)

        d: Mapping[str, Any] = e.to_dict()
        footer = d.get("footer")
        self.assertIsNotNone(footer)
        text = footer.get("text") if footer else None
        self.assertIsNotNone(text)
        self.assertEqual(text, truncate_text("123456789", 5))

    def test_set_footer_text_none(self) -> None:
        limits = EmbedLimits(footer=5)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.set_footer(text=None, icon_url=None)
        self.assertIs(ret, e)

    def test_set_author_truncates_and_returns_self(self) -> None:
        limits = EmbedLimits(author_name=4)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.set_author(name="ABCDEFG", url=None, icon_url=None)
        self.assertIs(ret, e)

        d: Mapping[str, Any] = e.to_dict()
        author = d.get("author")
        self.assertIsNotNone(author)
        name = author.get("name") if author else None
        self.assertIsNotNone(name)
        self.assertEqual(name, truncate_text("ABCDEFG", 4))

    def test_safe_add_field_truncates_name_and_value(self) -> None:
        limits = EmbedLimits(field_name=4, field_value=6, max_fields=25, max_total=6000)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.safe_add_field(
            name="1234567", value="abcdefghi", inline=False
        )
        self.assertIs(ret, e)

        d: Mapping[str, Any] = e.to_dict()
        last = (d.get("fields") or [None])[-1]
        self.assertIsNotNone(last)
        if last is None:
            self.fail("No fields found in embed after safe_add_field")
        self.assertEqual(last["name"], truncate_text("1234567", 4))
        self.assertEqual(last["value"], truncate_text("abcdefghi", 6))
        if "inline" not in last:
            self.fail("No inline found in embed after safe_add_field")
        self.assertFalse(last["inline"])

    def test_safe_add_field_max_fields_strict_raises(self) -> None:
        limits = EmbedLimits(max_fields=0)
        e = SafeEmbed(limits=limits)

        with self.assertRaises(ValueError):
            e.safe_add_field(name="n", value="v", strict=True)

        self.assertEqual(len(e.fields), 0)

    def test_safe_add_field_max_fields_non_strict_noop(self) -> None:
        limits = EmbedLimits(max_fields=0)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.safe_add_field(name="n", value="v", strict=False)
        self.assertIs(ret, e)
        self.assertEqual(len(e.fields), 0)

    def test_safe_add_field_max_total_strict_raises(self) -> None:
        limits = EmbedLimits(field_name=10, field_value=10, max_total=15)
        e = SafeEmbed(limits=limits)

        with self.assertRaises(ValueError):
            e.safe_add_field(name="N" * 10, value="V" * 10, strict=True)

    def test_safe_add_field_max_total_non_strict_squeezes_value(self) -> None:
        limits = EmbedLimits(field_name=10, field_value=10, max_total=15)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.safe_add_field(name="N" * 10, value="V" * 10, strict=False)
        self.assertIs(ret, e)

        d: Mapping[str, Any] = e.to_dict()
        last = (d.get("fields") or [None])[-1]
        self.assertIsNotNone(last)
        if last is None:
            self.fail("No fields found in embed after safe_add_field")
        self.assertEqual(last["name"], "N" * 10)
        self.assertLessEqual(len(last["value"]), 5)
        self.assertLessEqual(len(e), limits.max_total)

    def test_add_field_pages_non_strict_stops_at_max_fields(self) -> None:
        limits = EmbedLimits(field_value=12, max_fields=1)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.add_field_pages(
            name="P",
            lines=["line1", "line2", "line3", "line4"],
            page_size=1,
            separator="\n",
            strict=False,
        )
        self.assertIs(ret, e)
        self.assertEqual(len(e.fields), 1)

    def test_add_field_pages_strict_raises_on_max_fields(self) -> None:
        limits = EmbedLimits(field_value=12, max_fields=1)
        e = SafeEmbed(limits=limits)

        with self.assertRaises(ValueError):
            e.add_field_pages(
                name="P",
                lines=["line1", "line2"],
                page_size=1,
                separator="\n",
                strict=True,
            )

        self.assertEqual(len(e.fields), 1)

    def test_add_code_field_truncates_inside_codeblock(self) -> None:
        limits = EmbedLimits(
            field_value=20, field_name=256, max_total=6000, max_fields=25
        )
        e = SafeEmbed(limits=limits)
        lang = "py"
        ret: SafeEmbed = e.add_code_field(
            name="code",
            value="X" * 50,
            lang=lang,
            inline=False,
            strict=True,
        )
        self.assertIs(ret, e)

        d: Mapping[str, Any] = e.to_dict()
        last = (d.get("fields") or [None])[-1]
        self.assertIsNotNone(last)
        if last is None:
            self.fail("No fields found in embed after safe_add_field")
        self.assertTrue(last["value"].startswith(f"```{lang}\n"))
        self.assertTrue(last["value"].endswith("\n```"))
        self.assertLessEqual(len(last["value"]), limits.field_value)

    def test_add_field_if_true_adds_field(self) -> None:
        limits = EmbedLimits(max_fields=25)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.add_field_if(
            True,
            name="n",
            value="v",
            inline=False,
            strict=True,
        )
        self.assertIs(ret, e)
        self.assertEqual(len(e.fields), 1)

    def test_add_field_if_false_noop(self) -> None:
        limits = EmbedLimits(max_fields=25)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.add_field_if(
            False,
            name="n",
            value="v",
            inline=False,
            strict=True,
        )
        self.assertIs(ret, e)
        self.assertEqual(len(e.fields), 0)

    def test_fluent_chain_is_typed(self) -> None:
        e: SafeEmbed = (
            SafeEmbed(title="t")
            .set_footer(text="footer")
            .set_author(name="author")
            .safe_add_field(name="n", value="v", strict=True)
        )
        self.assertIsInstance(e, SafeEmbed)

    def test_add_field_pages_adds_second_page_name_suffix(self) -> None:
        # Ensures idx > 1 branch runs and the "(стр. N)" suffix is used.
        limits = EmbedLimits(field_value=12, max_fields=5)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.add_field_pages(
            name="P",
            lines=["line1", "line2"],
            page_size=1,  # 2 pages
            separator="\n",
            strict=True,  # no raise because max_fields allows it
        )
        self.assertIs(ret, e)

        d: Mapping[str, Any] = e.to_dict()
        fields = d.get("fields")
        self.assertIsInstance(fields, list)
        if not isinstance(fields, list):
            self.fail("No fields found in embed after add_field_pages")
        self.assertGreaterEqual(len(fields), 2)

        self.assertEqual(fields[0]["name"], "P")
        self.assertEqual(fields[1]["name"], "P (стр. 2)")

    def test_add_code_field_no_truncation_path(self) -> None:
        limits = EmbedLimits(field_value=50)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.add_code_field(
            name="code",
            value="print(1)",
            lang="py",
            inline=False,
            strict=True,
        )
        self.assertIs(ret, e)

        d: Mapping[str, Any] = e.to_dict()
        last = (d.get("fields") or [None])[-1]
        self.assertIsNotNone(last)
        if last is None:
            self.fail("No fields found in embed after safe_add_field")
        self.assertIn("print(1)", last["value"])
        self.assertLessEqual(len(last["value"]), limits.field_value)

    def test_safe_add_field_max_total_non_strict_remaining_zero(self) -> None:
        limits = EmbedLimits(field_name=10, field_value=10, max_total=10)
        e = SafeEmbed(limits=limits)

        ret: SafeEmbed = e.safe_add_field(name="N" * 10, value="V" * 10, strict=False)
        self.assertIs(ret, e)

        d: Mapping[str, Any] = e.to_dict()
        last = (d.get("fields") or [None])[-1]
        self.assertIsNotNone(last)
        if last is None:
            self.fail("No fields found in embed after safe_add_field")
        self.assertEqual(last["name"], "N" * 10)
        self.assertEqual(last["value"], "")
        self.assertLessEqual(len(e), limits.max_total)
