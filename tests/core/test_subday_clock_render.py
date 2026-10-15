"""Render-level verification for the sub-day clock vocabulary's tick-cadence gate.

Pure-function coverage of ``default_subday_label_expr_for`` lives in
test_subday_clock_label_expr.py. These tests render a real chart through the
full pipeline and read the x-axis tick label TEXT Vega itself drew — proving
the gate's effect on what a viewer actually sees, not just on the labelExpr
string the function returns: the rendered ticks are the contract, not the
input data.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re

import vl_convert as vlc

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import LineChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

_WIDTH = 600.0


_AXIS_LABEL_GROUP_RE = re.compile(
    r'<g class="mark-text role-axis-label"[^>]*>(.*?)</g>', re.S
)
_TICK_TEXT_POSITION_RE = re.compile(
    r'<text[^>]*transform="translate\(([-\d.]+),([-\d.]+)\)"'
)


def _tick_position_spread(group_markup: str) -> tuple[float, float]:
    """(x-spread, y-spread) across a role-axis-label group's own tick texts."""
    positions = [
        (float(x), float(y)) for x, y in _TICK_TEXT_POSITION_RE.findall(group_markup)
    ]
    xs = [x for x, _y in positions]
    ys = [y for _x, y in positions]
    return (max(xs) - min(xs) if xs else 0.0, max(ys) - min(ys) if ys else 0.0)


def _x_axis_label_group(svg: str) -> str:
    """The inner markup of the X axis's own ``role-axis-label`` group.

    Both axes emit an identically-classed label group, and Vega's own
    aria-label prose ("X-axis titled ...") is NOT a stable handle: it is
    absent whenever the axis renders with no title, and this theme hides an
    unauthored axis title by default for a line chart's x-axis (only
    scatter/histogram opt into a visible default), so slicing on that string
    throws before a single assertion runs. This instead identifies "the x
    axis" the way it actually looks: a row of tick labels that vary widely
    in their own X position but share a near-constant Y (a horizontal band),
    versus the y-axis's labels, which vary in Y and share a near-constant X
    (a vertical band) -- true regardless of title, locale, or which side of
    the plot either axis renders on.
    """
    groups = _AXIS_LABEL_GROUP_RE.findall(svg)
    assert len(groups) >= 2, (
        f"expected at least 2 axis label groups, found {len(groups)}"
    )
    return max(
        groups, key=lambda g: _tick_position_spread(g)[0] - _tick_position_spread(g)[1]
    )


def _rendered_x_tick_labels(data: list[dict], width: float = _WIDTH) -> list[str]:
    """The x-axis tick text Vega actually drew for a continuous temporal line chart."""
    chart = LineChart(id="line", type="line", x="ts", y="value", style=None)
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE, width=width)
    svg = vlc.vegalite_to_svg(json.dumps(artifact.payload))
    group_markup = _x_axis_label_group(svg)
    # A two-row label renders as nested <tspan> elements inside one <text>,
    # e.g. '<tspan>Midnight</tspan><tspan x="0" dy="13">Aug 11</tspan>' — strip
    # the markup down to its visible text (joining rows with a space) so a
    # substring check below reads what a viewer actually sees, not raw SVG.
    return [
        html.unescape(re.sub(r"<[^>]+>", " ", raw)).strip()
        for raw in re.findall(r"<text[^>]*>(.*?)</text>", group_markup, re.S)
    ]


class TestSubdayClockRenderedTicks:
    def test_weekly_09_00_domain_does_not_render_midnight_everywhere(self) -> None:
        """26 weekly points at 09:00, ~175-day span, rendered. Before the fix, every tick read "Midnight"
        (Vega's own generator lands on weekly, day-grain ticks at this span
        and width) and the domain never named a year. After the fix the
        gate withholds the vocabulary and Vega's own default format draws
        the axis instead.
        """
        start = dt.date(2024, 1, 1)
        data = [
            {"ts": (start + dt.timedelta(weeks=i)).isoformat() + "T09:00:00", "y": i}
            for i in range(26)
        ]
        labels = _rendered_x_tick_labels(
            [{"ts": d["ts"], "value": d["y"]} for d in data]
        )
        assert not any("Midnight" in label for label in labels)
        assert labels, "expected Vega to draw some ticks for this domain"

    def test_two_minute_domain_has_no_adjacent_duplicate_tick_labels(self) -> None:
        """The second-grain repro, rendered: Vega subdivides a 2-minute
        domain into sub-minute ticks regardless of what our vocabulary can
        describe. Falling back to Vega's own default format (rather than
        forcing our minute-only vocabulary onto ticks finer than a minute)
        must not render two *adjacent* ticks with identical text — the same
        minor-repeats-under-a-major structure our own vocabulary uses (e.g.
        ":15" recurring under different hours) is fine; two neighboring
        ticks reading the same thing is the actual defect.
        """
        data = [
            {"ts": "2026-08-11T09:00:00", "value": 0},
            {"ts": "2026-08-11T09:01:00", "value": 1},
            {"ts": "2026-08-11T09:02:00", "value": 2},
        ]
        labels = _rendered_x_tick_labels(data)
        adjacent_dupes = [
            (a, b) for a, b in zip(labels, labels[1:], strict=False) if a == b
        ]
        assert not adjacent_dupes, labels

    def test_lower_gate_boundary_band_at_600px_has_no_adjacent_duplicate_tick_labels(
        self,
    ) -> None:
        """At a declared 600px card, predicted_tick_count
        under-counts Vega's real tickCount (13 vs the real 14, confirmed by
        the rendered plot rectangle: 556px of real plot for a 600px declared
        width). Using that under-counted floor for BOTH boundary checks
        opened a ~42s band of domains -- [551.5s, 594.0s) -- that cleared
        the (too-lenient) minute-grain floor while Vega's own generator
        already subdivides them into sub-minute ticks. A 9m30s domain sits
        inside that band. After the fix, the lower gate reads a separate,
        safe UPPER-bound tick count for this check, so the vocabulary
        withholds here too and Vega's own default format draws the axis.
        """
        data = [
            {"ts": "2026-08-11T09:00:00", "value": 0},
            {"ts": "2026-08-11T09:09:30", "value": 1},
        ]
        labels = _rendered_x_tick_labels(data)
        adjacent_dupes = [
            (a, b) for a, b in zip(labels, labels[1:], strict=False) if a == b
        ]
        assert not adjacent_dupes, labels

    def test_three_hourly_cadence_with_midnight_readings_renders_the_words(
        self,
    ) -> None:
        """A real 3-hourly ladder crossing
        several midnights must render the "Midnight"/"Noon" words, not
        Vega's bare zero-padded default (the defect rules 1-4 exist to fix).
        """
        start = dt.datetime(2026, 8, 11, 0, 0)
        data = [
            {"ts": (start + dt.timedelta(hours=3 * i)).isoformat(), "value": i}
            for i in range(16)
        ]
        labels = _rendered_x_tick_labels(data)
        assert any("Midnight" in label for label in labels)
        assert any("Noon" in label for label in labels)
        adjacent_dupes = [
            (a, b) for a, b in zip(labels, labels[1:], strict=False) if a == b
        ]
        assert not adjacent_dupes, labels

    def test_ten_day_hourly_domain_at_600px_does_not_render_midnight_everywhere(
        self,
    ) -> None:
        """A plain 10-day hourly line chart at 600px — the most ordinary
        sub-day span there is — used to render every tick as the bare word
        "Midnight": the gate's
        predicted tick count was read off the card width Vega declares, not
        the smaller plot rectangle vl_convert's own ``autosize: fit``
        actually draws into (confirmed by inspecting the rendered SVG's
        plot-rectangle path: 551px of real plot for a 600px declared width,
        with no endpoint-rail chrome in play). After the fix the gate
        withholds the vocabulary here and Vega's own default format
        (``Tue 11, Wed 12, …``) draws the axis instead.
        """
        start = dt.datetime(2026, 8, 11, 0, 0)
        data = [
            {"ts": (start + dt.timedelta(hours=h)).isoformat(), "value": h}
            for h in range(0, 10 * 24 + 1, 3)
        ]
        labels = _rendered_x_tick_labels(data)
        assert not any("Midnight" in label for label in labels)
        assert labels, "expected Vega to draw some ticks for this domain"

    def test_domain_never_reaching_an_hour_anchors_the_first_tick(self) -> None:
        """09:05 -> 09:55 never crosses an hour,
        so rule 3's plain minutes gate would give every drawn tick the bare
        minute-only form (":05 :10 ... :55") -- no hour and no meridiem
        anywhere on the axis, so a viewer cannot tell 9am from 9pm. The
        anchor rule forces the first tick to a full label regardless of its
        own minutes -- and that full label must include the minutes: the
        first drawn tick is 09:10 (ticks sit on the clock, not on the first
        data point), so the anchor must read "9:10am", never the bare "9am" a
        reader would mis-scale the axis against (the anchor's own first fix
        printed "9am" for a tick that is not on the hour).
        """
        start = dt.datetime(2026, 8, 11, 9, 5)
        data = [
            {"ts": (start + dt.timedelta(minutes=m)).isoformat(), "value": m}
            for m in range(51)
        ]
        labels = _rendered_x_tick_labels(data)
        assert labels, "expected Vega to draw some ticks for this domain"
        assert labels[0] == "9:10am"
        assert all(label.startswith(":") for label in labels[1:])

    def test_four_minute_readings_tick_on_the_clock_and_name_the_hour(self) -> None:
        """Readings every 4 minutes used to tick every 5 minutes from the first
        reading and drop alternate labels, so no hour was ever named. The ladder
        picks the 10-minute rung for this width and span: every tick sits on a
        multiple of ten minutes and each whole hour prints its hour."""
        start = dt.datetime(2026, 3, 2, 9, 5)
        data = [
            {"ts": (start + dt.timedelta(minutes=4 * i)).isoformat(), "value": i}
            for i in range(31)
        ]
        labels = _rendered_x_tick_labels(data, width=1000.0)
        assert labels == [
            ":10", ":20", ":30", ":40", ":50", "10am", ":10", ":20", ":30",
            ":40", ":50", "11am",
        ]  # fmt: skip

    def test_thirteen_hour_series_ticks_hourly_with_the_hour_shown(self) -> None:
        """A series opening at 08:30 used to label every tick ":30" with no hour."""
        start = dt.datetime(2026, 3, 2, 8, 30)
        data = [
            {"ts": (start + dt.timedelta(minutes=15 * i)).isoformat(), "value": i}
            for i in range(53)
        ]
        labels = _rendered_x_tick_labels(data, width=1000.0)
        assert labels[:3] == ["9am", "10am", "11am"]
        assert not any(label.startswith(":") for label in labels)

    def test_two_row_date_context_case_wrap_keeps_both_rows_separate(self) -> None:
        """Regression: `labels.font.case: upper` used to stringify the
        two-row array labelExpr via JS's Array#toString before Vega's own
        `upper()` call could see individual rows -- collapsing "Midnight" /
        "Aug 11" onto one comma-joined line. Each row must stay on its own
        line (a separate <text> node's raw markup carries a <tspan> for the
        second row), uppercased, and never comma-joined.
        """
        start = dt.datetime(2026, 8, 11, 0, 0)
        data = [
            {"ts": (start + dt.timedelta(hours=h)).isoformat(), "value": h}
            for h in range(0, 28, 3)
        ]
        chart = LineChart.model_validate(
            {
                "id": "line",
                "type": "line",
                "x": "ts",
                "y": "value",
                "style": {"axis_x": {"labels": {"font": {"case": "upper"}}}},
            }
        )
        resolved = resolve(
            chart,
            [{"ts": d["ts"], "value": d["value"]} for d in data],
            chart_style_context=_BOARD_CTX,
        )
        artifact = render_resolved_chart(
            resolved,
            [{"ts": d["ts"], "value": d["value"]} for d in data],
            _BOARD_STYLE,
            width=_WIDTH,
        )
        svg = vlc.vegalite_to_svg(json.dumps(artifact.payload))
        group_markup = _x_axis_label_group(svg)
        raw_texts = re.findall(r"<text[^>]*>(.*?)</text>", group_markup, re.S)
        assert any("<tspan" in t for t in raw_texts), raw_texts
        assert any("MIDNIGHT" in t for t in raw_texts), raw_texts
        # No row was collapsed into a comma-joined single line.
        assert not any(
            "," in html.unescape(re.sub(r"<[^>]+>", "", t)) for t in raw_texts
        )
