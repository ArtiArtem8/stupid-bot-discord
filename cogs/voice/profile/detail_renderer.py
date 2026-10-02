"""Three static SVG consumers sharing the profile's native rasterizer.

Layout and text budgets belong to SVG. This module binds labels and normalizes
chart lengths within authored tracks; it never calculates voice or XP policy.
"""

from __future__ import annotations

import base64
from calendar import day_name
from collections.abc import Mapping
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from api.voice.prediction import VoiceHoursEstimate
from api.voice.profile.details import (
    ActivityDetail,
    DetailPeriod,
    XpDetail,
)
from cogs.voice.profile.design import (
    ASSETS,
    XLINK,
    apply_tokens,
    bind_emblem,
    check_template,
    document_bytes,
    fit_texts,
    format_duration,
    normalized_png,
    replace_text,
    safe_label,
    style,
    theme_tokens,
)
from cogs.voice.profile.detail_models import DetailIdentity, PeoplePresentation
from cogs.voice.profile.raster import NativeRasterizer

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element


class DetailCardRenderer:
    """Borrow one native resource; the media owner schedules work and closes it."""

    def __init__(
        self, raster: NativeRasterizer, assets: Path = ASSETS / "details"
    ) -> None:
        self.raster = raster
        self.assets = assets

    def _document(
        self, name: str, identity: DetailIdentity, period: DetailPeriod
    ) -> tuple[Element, dict[str, Element]]:
        root, nodes = check_template(
            (self.assets / f"{name}.svg").read_bytes(),
            frozenset(
                {
                    "card-shell",
                    "display-name",
                    "guild-name",
                    "tier-name",
                    "emblem-art",
                    "user-avatar",
                    "avatar-fallback",
                    "period-label",
                    "coverage-label",
                }
            ),
        )
        if (root.get("width"), root.get("height"), root.get("viewBox")) != (
            "960",
            "480",
            "0 0 960 480",
        ):
            raise ValueError("Detail cards require a 960 by 480 canvas")
        tokens = theme_tokens(ASSETS / "themes.json", identity.appearance)
        apply_tokens(root, tokens)
        bind_emblem(nodes, identity.appearance, tokens)
        _labels(
            nodes,
            {
                "display-name": safe_label(identity.card.display_name),
                "guild-name": safe_label(identity.card.guild_name),
                "tier-name": identity.appearance.tier.value.upper(),
                "period-label": (
                    f"{period.first_date:%b %d} – {period.last_date:%b %d}"
                    f" · {period.timezone_label}"
                ),
                "coverage-label": f"Coverage {period.coverage_ratio:.0%}",
            },
        )
        try:
            avatar = normalized_png(identity.card.avatar_bytes, size=64)
        except ValueError:
            avatar = None
        if avatar:
            nodes["user-avatar"].set(
                f"{{{XLINK}}}href",
                "data:image/png;base64," + base64.b64encode(avatar).decode(),
            )
            style(nodes["avatar-fallback"], "display", "none")
        else:
            style(nodes["user-avatar"], "display", "none")
        return root, nodes

    def _png(self, root: Element, nodes: dict[str, Element]) -> bytes:
        fit_texts(root, nodes, self.raster)
        (image,) = self.raster.layers((document_bytes(root),))
        with image:
            if image.size != (960, 480):
                raise RuntimeError("Rasterizer changed the detail canvas")
            output = BytesIO()
            image.save(output, "PNG", compress_level=3)
            return output.getvalue()

    def render_activity(
        self, detail: ActivityDetail, identity: DetailIdentity
    ) -> bytes:
        """Render activity heights independently from observation coverage."""
        root, nodes = self._document("activity", identity, detail.period)
        presence = detail.presence
        values = (
            format_duration(presence.total_seconds, compact=True),
            format_duration(presence.group_seconds, compact=True),
            format_duration(presence.solo_seconds, compact=True),
            f"{presence.session_count:,}",
        )
        stats = (
            format_duration(presence.average_session_seconds, compact=True),
            format_duration(presence.median_session_seconds, compact=True),
            f"{detail.peak_hour:02}:00" if detail.peak_hour is not None else "—",
            day_name[detail.peak_weekday] if detail.peak_weekday is not None else "—",
        )
        _labels(nodes, {f"metric-{i}": value for i, value in enumerate(values)})
        _labels(nodes, {f"stat-{i}": value for i, value in enumerate(stats)})
        _activity_chart(nodes, detail)
        return self._png(root, nodes)

    def render_people(
        self, presentation: PeoplePresentation, identity: DetailIdentity
    ) -> bytes:
        """Render aligned shared and one-on-one durations, without avatars."""
        detail = presentation.detail
        root, nodes = self._document("people", identity, detail.period)
        if detail.companions:
            style(nodes["empty-people"], "display", "none")
        for i in range(5):
            _person_row(nodes, presentation, i)
        summary = (
            f"{detail.unique_people:,}",
            format_duration(detail.presence.group_seconds, compact=True),
            format_duration(detail.private_seconds, compact=True),
            format_duration(detail.bots.any_bot_seconds, compact=True),
        )
        _labels(nodes, {f"summary-{i}": value for i, value in enumerate(summary)})
        top = min(
            detail.bots.by_bot, key=lambda item: (-item[1], item[0]), default=None
        )
        _labels(
            nodes,
            {
                "top-bot-name": safe_label(
                    presentation.names.get(top[0], "Unknown user")
                )
                if top
                else "—",
                "top-bot-time": format_duration(top[1], compact=True) if top else "",
            },
        )
        recent_voice = format_duration(
            detail.recent_presence.group_seconds, compact=True
        )
        replace_text(
            nodes["recent-summary"],
            (f"{recent_voice} together · {detail.recent_unique_people:,} people"),
        )
        _lifetime_labels(nodes, detail.lifetime_period)
        return self._png(root, nodes)

    def render_xp(self, detail: XpDetail, identity: DetailIdentity) -> bytes:
        """Format exact policy components only at the presentation boundary."""
        root, nodes = self._document("xp", identity, detail.period)
        xp = detail.breakdown
        positive = {
            "solo": xp.solo_base,
            "social": xp.social_base,
            "group": xp.large_group_bonus,
            "stream": xp.stream_bonus,
            "video": xp.video_bonus,
        }
        negative = {"audio": xp.audio_reduction, "cap": xp.bonus_cap_reduction}
        values = {f"xp-{key}": f"+{_xp(value)}" for key, value in positive.items()}
        values.update(
            {f"xp-{key}": f"−{_xp(value)}" for key, value in negative.items()}
        )
        values["xp-total"] = f"{int(xp.total):,} XP"
        values["recent-xp"] = f"+{_xp(detail.recent_xp)} XP"
        values["xp-estimate"] = _estimate_label(detail.estimate)
        _labels(nodes, values)
        _lifetime_labels(nodes, detail.lifetime_period)
        return self._png(root, nodes)


def _xp(value: Fraction) -> str:
    for scale, suffix in ((10**12, "T"), (10**9, "B"), (10**6, "M")):
        if value >= max(scale, 10**7):
            return f"{float(value / scale):.1f}{suffix}"
    return f"{round(value):,}"


def _estimate_label(estimate: VoiceHoursEstimate | None) -> str:
    if estimate is None:
        return ""
    hours = estimate.expected_hours
    if hours < Fraction(1, 60):
        return "<1 min in voice to next level"
    if hours < 1:
        return f"~{round(hours * 60)} min in voice to next level"
    if hours < 10:
        return f"~{float(hours):.1f} h in voice to next level"
    return f"~{round(hours):,} h in voice to next level"


def _lifetime_labels(nodes: dict[str, Element], period: DetailPeriod | None) -> None:
    _labels(
        nodes,
        {
            "lifetime-label": f"History since {period.first_date:%b %d, %Y}"
            if period
            else "No observed history",
            "lifetime-coverage": f"Lifetime coverage {period.coverage_ratio:.0%}"
            if period
            else "Lifetime coverage —",
        },
    )
    current = nodes["coverage-label"]
    replace_text(current, (current.text or "").replace("Coverage", "30-day coverage"))


def _labels(nodes: dict[str, Element], values: Mapping[str, str]) -> None:
    for key, value in values.items():
        replace_text(nodes[key], value)


def _activity_chart(nodes: dict[str, Element], detail: ActivityDetail) -> None:
    maximum = max((day.voice_seconds for day in detail.days), default=0) or 3600
    replace_text(nodes["chart-max"], format_duration(maximum, compact=True))
    for i, day in enumerate(detail.days):
        prefix = f"day-{i}"
        track, bar = nodes[f"{prefix}-track"], nodes[f"{prefix}-bar"]
        height = float(track.attrib["height"]) * day.voice_seconds / maximum
        bar.set("height", str(height))
        bar.set(
            "y", str(float(track.attrib["y"]) + float(track.attrib["height"]) - height)
        )
        states = {
            "partial": 0 < day.coverage_ratio < 1,
            "gap": day.observed_seconds == 0,
            "zero": day.voice_seconds == 0 and day.coverage_ratio == 1,
        }
        for suffix, visible in states.items():
            if not visible:
                style(nodes[f"{prefix}-{suffix}"], "display", "none")
        label = nodes.get(f"date-{i}")
        if label is not None:
            replace_text(label, "Today" if i == 29 else f"{day.date:%d}")


def _person_row(
    nodes: dict[str, Element], presentation: PeoplePresentation, index: int
) -> None:
    prefix = f"person-{index}"
    people = presentation.detail.companions
    if index >= len(people):
        style(nodes[f"{prefix}-placement"], "display", "none")
        return
    person = people[index]
    name = safe_label(presentation.names.get(person.user_id, "Unknown user"))
    _labels(
        nodes,
        {
            f"{prefix}-name": f"{index + 1}. {name}",
            f"{prefix}-shared": format_duration(person.shared_seconds, compact=True),
            f"{prefix}-private": format_duration(person.private_seconds, compact=True),
        },
    )
    color = presentation.colors.get(person.user_id)
    if color:
        style(nodes[f"{prefix}-name"], "fill", f"#{color:06x}")
