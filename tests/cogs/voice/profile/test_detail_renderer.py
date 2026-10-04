"""Binding contracts at the native renderer boundary, without subprocesses."""

import re
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import override
from unittest.mock import MagicMock

from defusedxml.ElementTree import fromstring
from PIL import Image

from api.voice.model import VoiceCheckpoint, VoiceSnapshot
from api.voice.prediction import VoiceHoursEstimate
from api.voice.profile.build import build_profile
from api.voice.profile.calendar import ClockSpan
from api.voice.profile.details import (
    build_activity_detail,
    build_people_detail,
    build_xp_detail,
)
from api.voice.read_models import VoiceXpBreakdown
from api.voice.scope import TimeRange
from api.voice.timeline import VoiceTimeline, build_timeline
from cogs.voice.profile.design import ASSETS, CardIdentity, property_value, theme_tokens
from cogs.voice.profile.detail_models import DetailIdentity, PeoplePresentation
from cogs.voice.profile.detail_renderer import DetailCardRenderer
from cogs.voice.profile.raster import Box, NativeRasterizer
from tests.api.voice.examples import at, example, human, record
from tests.cogs.voice.profile.test_media import profile_at


class TestDetailBindings(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.raster = MagicMock(spec=NativeRasterizer)
        self.raster.query.return_value = {}

        def layers(_documents: tuple[bytes, ...]) -> tuple[Image.Image, ...]:
            return (Image.new("RGBA", (960, 480)),)

        self.raster.layers.side_effect = layers
        self.renderer = DetailCardRenderer(self.raster)
        self.identity = DetailIdentity(
            CardIdentity("Александр " * 20, "Guild " * 40), profile_at(35).appearance
        )
        self.as_of = datetime(2026, 9, 21, 12, tzinfo=UTC)

    def test_each_template_uses_shared_theme_and_emblem_accent(self) -> None:
        timeline = VoiceTimeline((), (), ())
        activity = build_activity_detail(timeline, 1, 1, self.as_of)
        people = PeoplePresentation(
            build_people_detail(timeline, 1, 1, self.as_of), {}, {}
        )
        xp = build_xp_detail(timeline, 1, 1, self.as_of)
        renders = (
            lambda: self.renderer.render_activity(activity, self.identity),
            lambda: self.renderer.render_people(people, self.identity),
            lambda: self.renderer.render_xp(xp, self.identity),
        )
        tokens = theme_tokens(ASSETS / "themes.json", self.identity.appearance)
        for render in renders:
            render()
            svg = fromstring(self.raster.layers.call_args.args[0][0])
            nodes = {node.get("id"): node for node in svg.iter()}
            self.assertEqual(
                property_value(nodes["card-shell"], "fill", ""), tokens["surface"]
            )
            self.assertEqual(
                property_value(nodes["emblem-art"], "fill", ""), tokens["accent"]
            )
            self.assertEqual(nodes["tier-name"].text, "MYTHIC")
            self.assertEqual(
                property_value(nodes["user-avatar"], "display", ""), "none"
            )
            self.assertEqual(
                nodes["display-name"].text, self.identity.card.display_name.strip()
            )

    def test_people_names_are_escaped_and_color_is_supplied_tier(self) -> None:
        detail = build_people_detail(example(), 1, 1, self.as_of)
        presentation = PeoplePresentation(detail, {2: "<Mira & Alex>"}, {2: 0xABCDEF})
        self.renderer.render_people(presentation, self.identity)
        root = fromstring(self.raster.layers.call_args.args[0][0])
        nodes = {node.get("id"): node for node in root.iter()}
        self.assertEqual(nodes["person-0-name"].text, "1. <Mira & Alex>")
        self.assertEqual(property_value(nodes["person-0-name"], "fill", ""), "#abcdef")
        self.assertEqual(nodes["person-0-shared"].text, "0h 10m")
        self.assertEqual(nodes["person-0-private"].text, "0h 05m")

    def test_unknown_activity_hatches_only_elapsed_time(self) -> None:
        detail = build_activity_detail(VoiceTimeline((), (), ()), 1, 1, self.as_of)
        self.renderer.render_activity(detail, self.identity)
        root = fromstring(self.raster.layers.call_args.args[0][0])
        nodes = {node.get("id"): node for node in root.iter()}
        for i in range(30):
            self.assertTrue(nodes[f"day-{i}-missing"].get("d"))
            self.assertEqual(nodes[f"day-{i}-voice"].get("d"), "")
            self.assertEqual(bool(nodes[f"day-{i}-future"].get("d")), i == 29)
        self.assertEqual(nodes["stat-2"].text, "—")
        self.assertEqual(nodes["stat-3"].text, "—")

    def test_short_markers_are_visible_union_overlaps_and_stay_in_column(self) -> None:
        detail = build_activity_detail(VoiceTimeline((), (), ()), 1, 1, self.as_of)
        day = replace(
            detail.days[0],
            voice_spans=(
                ClockSpan(0, 0.01),
                ClockSpan(0.02, 0.03),
                ClockSpan(720, 720.01),
                ClockSpan(1439.99, 1440),
            ),
        )
        self.renderer.render_activity(
            replace(detail, days=(day, *detail.days[1:])), self.identity
        )
        root = fromstring(self.raster.layers.call_args.args[0][0])
        nodes = {node.get("id"): node for node in root.iter()}
        track = nodes["day-0-track"]
        path = nodes["day-0-voice"]
        shapes = re.findall(
            r"M([\d.]+) ([\d.]+)h([\d.]+)v([\d.]+)h-([\d.]+)Z", path.get("d", "")
        )
        self.assertEqual(len(shapes), 3)
        previous_bottom = float(track.attrib["y"])
        for x, y, width, height, back in shapes:
            self.assertEqual(float(x), float(track.attrib["x"]))
            self.assertEqual(float(width), float(track.attrib["width"]))
            self.assertEqual(width, back)
            self.assertGreaterEqual(
                float(height), float(path.attrib["data-min-height"])
            )
            self.assertGreaterEqual(float(y), previous_bottom)
            previous_bottom = float(y) + float(height)
            self.assertLessEqual(
                previous_bottom,
                float(track.attrib["y"]) + float(track.attrib["height"]),
            )
        self.assertEqual(nodes["day-1-voice"].get("d"), "")
        self.assertEqual(
            nodes["day-0-bands"].get("clip-path"), "url(#clock-column-clip)"
        )
        self.assertEqual(nodes["chart-title"].text, "Daily activity")

    def test_xp_truncation_does_not_balance_components_and_large_values_stay_readable(
        self,
    ) -> None:
        detail = replace(
            build_xp_detail(VoiceTimeline((), (), ()), 1, 1, self.as_of),
            breakdown=VoiceXpBreakdown(
                solo_base=Fraction(3, 5), social_base=Fraction(3, 5)
            ),
        )
        self.renderer.render_xp(detail, self.identity)
        root = fromstring(self.raster.layers.call_args.args[0][0])
        nodes = {node.get("id"): node for node in root.iter()}
        self.assertEqual(nodes["xp-solo"].text, "<1")
        self.assertEqual(nodes["xp-social"].text, "<1")
        self.assertEqual(nodes["xp-total"].text, "1 XP")
        large = replace(
            detail, breakdown=VoiceXpBreakdown(social_base=Fraction(10**12, 7))
        )
        self.renderer.render_xp(large, self.identity)
        root = fromstring(self.raster.layers.call_args.args[0][0])
        nodes = {node.get("id"): node for node in root.iter()}
        self.assertEqual(nodes["xp-total"].text, "142,857,142,857 XP")
        self.assertEqual(nodes["xp-social"].text, "+142.8B")

    def test_xp_zero_is_neutral_and_subunit_signs_are_omitted(self) -> None:
        detail = replace(
            build_xp_detail(VoiceTimeline((), (), ()), 1, 1, self.as_of),
            breakdown=VoiceXpBreakdown(
                social_base=Fraction(10), mute_reduction=Fraction(1, 2)
            ),
        )
        self.renderer.render_xp(detail, self.identity)
        root = fromstring(self.raster.layers.call_args.args[0][0])
        nodes = {node.get("id"): node for node in root.iter()}
        tokens = theme_tokens(ASSETS / "themes.json", self.identity.appearance)
        for key in ("solo", "group", "stream", "video", "deaf", "cap"):
            self.assertEqual(nodes[f"xp-{key}"].text, "0")
            self.assertEqual(
                property_value(nodes[f"xp-{key}"], "fill", ""), tokens["muted"]
            )
        self.assertEqual(nodes["recent-xp"].text, "0 XP")
        self.assertEqual(
            property_value(nodes["recent-xp"], "fill", ""), tokens["muted"]
        )
        self.assertEqual(nodes["xp-mute"].text, "<1")
        self.assertEqual(
            property_value(nodes["xp-mute"], "fill", ""), tokens["negative"]
        )
        self.assertEqual(nodes["xp-social"].text, "+10")
        self.assertEqual(
            property_value(nodes["xp-social"], "fill", ""), tokens["positive"]
        )

    def test_audio_categories_are_shown_as_separate_reductions(self) -> None:
        detail = replace(
            build_xp_detail(VoiceTimeline((), (), ()), 1, 1, self.as_of),
            breakdown=VoiceXpBreakdown(
                social_base=Fraction(2000),
                mute_reduction=Fraction(210),
                deaf_reduction=Fraction(450),
            ),
        )
        self.renderer.render_xp(detail, self.identity)
        root = fromstring(self.raster.layers.call_args.args[0][0])
        nodes = {node.get("id"): node for node in root.iter()}
        for key, value in (("mute", "210"), ("deaf", "450")):
            self.assertEqual(nodes[f"xp-{key}"].text, f"−{value}")
        self.assertEqual(nodes["xp-total"].text, "1,340 XP")

    def test_coverage_has_two_decimals_without_rounding_up_to_complete(self) -> None:
        detail = build_xp_detail(VoiceTimeline((), (), ()), 1, 1, self.as_of)
        window = TimeRange(self.as_of - timedelta(seconds=1000), self.as_of)
        for observed, label in (
            (0, "0.00%"),
            (996, "99.60%"),
            (999.999, "99.99%"),
            (1000, "100.00%"),
        ):
            with self.subTest(observed=observed):
                period = replace(
                    detail.period, time_range=window, observed_seconds=observed
                )
                self.renderer.render_xp(
                    replace(detail, period=period, lifetime_period=period),
                    self.identity,
                )
                root = fromstring(self.raster.layers.call_args.args[0][0])
                nodes = {node.get("id"): node for node in root.iter()}
                self.assertEqual(
                    nodes["coverage-label"].text, f"30-day coverage {label}"
                )
                self.assertEqual(
                    nodes["lifetime-coverage"].text, f"Lifetime coverage {label}"
                )

    def test_subminute_presence_is_not_formatted_as_zero(self) -> None:
        timeline = build_timeline(
            [record(0, VoiceSnapshot((human(),))), record(30, VoiceCheckpoint())]
        )
        self.renderer.render_activity(
            build_activity_detail(timeline, 1, 1, at(30)), self.identity
        )
        root = fromstring(self.raster.layers.call_args.args[0][0])
        nodes = {node.get("id"): node for node in root.iter()}
        self.assertEqual(nodes["metric-0"].text, "<1m")
        self.assertEqual(nodes["metric-1"].text, "0h 00m")
        self.assertEqual(nodes["stat-0"].text, "<1m")

    def test_peak_day_labels_show_close_days_without_confidence_claims(self) -> None:
        detail = build_activity_detail(VoiceTimeline((), (), ()), 1, 1, self.as_of)
        cases = (
            ((0,), "Monday"),
            ((0, 3), "Mon, Thu"),
            ((0, 2, 4), "Mon, Wed, Fri"),
            ((0, 1, 2, 3, 4), "Mon–Fri"),
            ((0, 2, 4, 6), "Mo We Fr Su"),
            (tuple(range(7)), "All days"),
        )
        for days, label in cases:
            with self.subTest(days=days):
                self.renderer.render_activity(
                    replace(detail, peak_weekdays=days), self.identity
                )
                root = fromstring(self.raster.layers.call_args.args[0][0])
                nodes = {node.get("id"): node for node in root.iter()}
                self.assertEqual(nodes["stat-3"].text, label)

    def test_weekdays_abbreviate_only_when_full_names_exceed_svg_budget(self) -> None:
        detail = build_activity_detail(VoiceTimeline((), (), ()), 1, 1, self.as_of)
        for width, expected in ((225, "Monday, Friday"), (227, "Mon, Fri")):
            with self.subTest(width=width):
                self.raster.query.side_effect = [{"stat-3": Box(0, 0, width, 26)}, {}]
                self.renderer.render_activity(
                    replace(detail, peak_weekdays=(0, 4)), self.identity
                )
                root = fromstring(self.raster.layers.call_args.args[0][0])
                nodes = {node.get("id"): node for node in root.iter()}
                self.assertEqual(nodes["stat-3"].text, expected)

    def test_recent_solo_xp_matches_lifetime_total_without_rounding_up(self) -> None:
        for seconds, label in ((167, "13"), (168, "14")):
            with self.subTest(seconds=seconds):
                timeline = build_timeline(
                    [
                        record(0, VoiceSnapshot((human(1),))),
                        record(seconds, VoiceCheckpoint()),
                    ]
                )
                profile = build_profile(timeline, 1, 1, "UTC")
                detail = build_xp_detail(timeline, 1, 1, at(seconds))
                self.renderer.render_xp(detail, self.identity)
                root = fromstring(self.raster.layers.call_args.args[0][0])
                nodes = {node.get("id"): node for node in root.iter()}
                self.assertEqual(profile.total_xp, int(label))
                self.assertEqual(nodes["xp-total"].text, f"{label} XP")
                self.assertEqual(nodes["recent-xp"].text, f"+{label} XP")
                self.assertEqual(nodes["xp-solo"].text, f"+{label}")
                self.assertEqual(detail.breakdown.solo_base, Fraction(seconds, 12))
                self.assertEqual(detail.recent_xp, detail.breakdown.total)

    def test_lifetime_total_matches_main_before_and_at_level_threshold(self) -> None:
        for seconds, level, label in ((6749, 1, "2,249 XP"), (6750, 2, "2,250 XP")):
            with self.subTest(seconds=seconds):
                timeline = build_timeline(
                    [
                        record(0, VoiceSnapshot((human(1), human(2)))),
                        record(seconds, VoiceCheckpoint()),
                    ]
                )
                profile = build_profile(timeline, 1, 1, "UTC")
                detail = build_xp_detail(timeline, 1, 1, at(seconds))
                self.renderer.render_xp(detail, self.identity)
                root = fromstring(self.raster.layers.call_args.args[0][0])
                nodes = {node.get("id"): node for node in root.iter()}
                self.assertEqual(profile.level, level)
                self.assertEqual(nodes["xp-total"].text, label)
                self.assertEqual(nodes["xp-total"].text, f"{profile.total_xp:,} XP")
                self.assertEqual(detail.breakdown.total, Fraction(seconds, 3))

    def test_long_lifetime_durations_use_compact_hours(self) -> None:
        detail = build_people_detail(example(), 1, 1, self.as_of)
        detail = replace(
            detail,
            companions=(replace(detail.companions[0], shared_seconds=5000 * 3600),),
        )
        self.renderer.render_people(PeoplePresentation(detail, {}, {}), self.identity)
        root = fromstring(self.raster.layers.call_args.args[0][0])
        nodes = {node.get("id"): node for node in root.iter()}
        self.assertEqual(nodes["person-0-shared"].text, "5.0k h")

    def test_next_level_estimate_uses_voice_hours_and_missing_data_is_omitted(
        self,
    ) -> None:
        detail = build_xp_detail(VoiceTimeline((), (), ()), 1, 1, self.as_of)
        cases = (
            (None, ""),
            (
                VoiceHoursEstimate(Fraction(29, 5), Fraction(1200), Fraction(8), 3),
                "~5.8 h in voice to next level",
            ),
            (
                VoiceHoursEstimate(Fraction(7, 12), Fraction(1200), Fraction(8), 3),
                "~35 min in voice to next level",
            ),
            (
                VoiceHoursEstimate(Fraction(122, 10), Fraction(1200), Fraction(8), 3),
                "~12 h in voice to next level",
            ),
            (
                VoiceHoursEstimate(Fraction(1, 100), Fraction(1200), Fraction(8), 3),
                "<1 min in voice to next level",
            ),
        )
        for estimate, label in cases:
            with self.subTest(label=label):
                self.renderer.render_xp(
                    replace(detail, estimate=estimate), self.identity
                )
                root = fromstring(self.raster.layers.call_args.args[0][0])
                nodes = {node.get("id"): node for node in root.iter()}
                self.assertEqual(nodes["xp-estimate"].text or "", label)
