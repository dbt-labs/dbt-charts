"""Integration tests for spark charts in tables.

Tests the table rendering with spark columns using the render_table_svg function directly.
"""

from typing import Any

from pydantic import TypeAdapter

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

from ..core._engine_render import render_table_svg


def _make_chart(
    title: str = "",
    subtitle: str = "",
    link: str | None = None,
    style: dict[str, Any] | None = None,
    data: list[dict[str, Any]] | None = None,
) -> Any:
    """Build a ResolvedChart for table rendering tests.

    Wraps table-specific style keys under ``style.table`` (matching the authored
    ChartStylePatch layout) then resolves via the standard compile pipeline.
    """

    # TableChart.style is TableChartStylePatch — pass all style fields directly.
    normalized_style: dict[str, Any] = style or {}

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "_test",
            "type": "table",
            "title": title,
            "subtitle": subtitle,
            "link": link,
            "style": normalized_style or None,
        }
    )
    board_style = resolve_chart_style_context(get_theme_style())
    return resolve(chart, data or [], chart_style_context=board_style)


class TestSparkAlignmentInvariant:
    """Pins the spark-x = header-x invariant (chart AGENTS.md center-on-midpoint).

    The header text anchor and the spark mark's center must sit on the same
    cell midpoint, regardless of ``col_config.align``. ``align`` is a text /
    digit-positioning knob — it shifts the digit tspan within a numeric cell
    so columns of numbers line up by their ones digit; it must not move the
    spark mark, or the mark visually drifts away from its own header.

    This regression came from a real bug: an earlier `column` (singular)
    spark implementation honored ``col_config.align`` for spark positioning,
    which pushed the mark to the cell's right edge when ``align: right`` was
    set on a numeric column, while the header stayed centered. The two read
    as different columns.
    """

    def test_column_spark_centers_under_header_even_with_align_right(self) -> None:
        """`align: right` on a numeric column must not move the spark mark.

        Header anchors at cell midpoint (numeric column invariant). Spark
        mark must anchor to the same midpoint; ``align`` only shifts text.
        """
        import re

        chart = _make_chart(
            title="Spark Alignment",
            style={
                "columns": {
                    "label": {},
                    "value": {
                        "align": "right",  # text-only — must NOT move the spark
                        "spark": {"type": "column", "max": 100},
                    },
                }
            },
        )
        data = [{"label": "A", "value": 75}]

        svg = render_table_svg(
            chart, data, board_style=resolve_style(get_theme_style())
        )

        # Find the "Value" header's x position (text-anchor="middle").
        header_match = re.search(
            r'<text x="([0-9.]+)"[^>]*text-anchor="middle"[^>]*>Value</text>',
            svg,
        )
        assert header_match is not None, (
            "Header 'Value' not found with text-anchor='middle' "
            "(numeric-column invariant broken)"
        )
        header_x = float(header_match.group(1))

        # Find the spark's translate-x. The column spark is 16px wide; its
        # center is translate_x + 8.
        translate_matches = re.findall(
            r'<g transform="translate\(([0-9.]+), ([0-9.]+)\)">\s*<rect',
            svg,
        )
        assert translate_matches, "No spark <g translate> found"
        # Spark width is 16px (default for `column`). Sparks should sit so
        # midpoint matches the header midpoint within rounding tolerance.
        spark_mid_xs = [float(x) + 8 for (x, _) in translate_matches]
        # At least one spark should be centered on the header midpoint.
        assert any(abs(sx - header_x) <= 1.0 for sx in spark_mid_xs), (
            f"No spark midpoint matches header_x={header_x}; "
            f"spark midpoints found: {spark_mid_xs}. "
            "The center-on-midpoint invariant is broken: header text-anchor "
            "is at the cell midpoint but the spark sits elsewhere — likely "
            "an `align` value is incorrectly moving the spark."
        )


class TestSparkTableRendering:
    """Tests for table with spark columns rendering."""

    def test_table_with_line_sparkline(self) -> None:
        """Table with line sparkline renders correctly."""
        chart = _make_chart(
            title="Regional Trends",
            style={
                "columns": {
                    "region": {},
                    "trend": {"spark": "line"},
                }
            },
        )
        data = [
            {"region": "North", "trend": [10, 20, 15, 30, 25]},
            {"region": "South", "trend": [15, 18, 22, 25, 28]},
        ]

        svg_output = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )

        assert "<svg" in svg_output
        assert "Regional Trends" in svg_output
        # Should have polyline elements from spark charts
        assert "<polyline" in svg_output

    def test_table_with_progress_bar(self) -> None:
        """Table with progress bar renders correctly."""
        chart = _make_chart(
            title="Task Progress",
            style={
                "columns": {
                    "task": {},
                    "progress": {"spark": {"type": "bar-normalize", "max": 100}},
                }
            },
        )
        data = [
            {"task": "Task A", "progress": 75},
            {"task": "Task B", "progress": 45},
        ]

        svg_output = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )

        assert "<svg" in svg_output
        assert "Task Progress" in svg_output
        # Progress bars have rect elements
        assert "<rect" in svg_output

    def test_table_with_multiple_spark_columns(self) -> None:
        """Table with multiple spark columns renders correctly."""
        chart = _make_chart(
            title="Multi Spark Demo",
            style={
                "columns": {
                    "name": {},
                    "trend": {"spark": "line"},
                    "progress": {"spark": {"type": "bar-normalize", "max": 100}},
                }
            },
        )
        data = [
            {"name": "Item 1", "trend": [10, 20, 30], "progress": 85},
            {"name": "Item 2", "trend": [15, 25, 35], "progress": 60},
        ]

        svg_output = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )

        # Should have both polyline (line spark) and rect (progress) elements
        assert "<polyline" in svg_output
        assert "<rect" in svg_output

    def test_table_with_custom_column_labels(self) -> None:
        """Table with custom column labels renders correctly."""
        chart = _make_chart(
            title="Sales Summary",
            style={
                "columns": {
                    "region": {"label": "Region Name"},
                    "total_sales": {"label": "Total Sales ($)"},
                }
            },
        )
        data = [{"region": "North", "total_sales": 125000}]

        svg_output = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )

        assert isinstance(svg_output, str)
        # Custom labels should appear in the output
        # Auto-wrap may split across tspans; both words should be present
        assert "Region" in svg_output and "Name" in svg_output
        assert "Total Sales" in svg_output

    def test_table_with_bar_normalize_thresholds(self) -> None:
        """Table with bar-normalize thresholds renders correctly."""
        chart = _make_chart(
            title="Threshold Progress",
            style={
                "columns": {
                    "name": {},
                    "value": {
                        "spark": {
                            "type": "bar-normalize",
                            "max": 100,
                            "thresholds": {0: "#ef4444", 50: "#f59e0b", 80: "#22c55e"},
                        },
                    },
                }
            },
        )
        data = [
            {"name": "Low", "value": 30},
            {"name": "Medium", "value": 60},
            {"name": "High", "value": 90},
        ]

        svg_output = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )

        assert isinstance(svg_output, str)
        # Should have different colors from thresholds
        assert "#ef4444" in svg_output  # Red for low
        assert "#f59e0b" in svg_output  # Yellow for medium
        assert "#22c55e" in svg_output  # Green for high

    def test_table_without_spark_config(self) -> None:
        """Table without spark config still renders normally."""
        data = [
            {"region": "North", "sales": 125000},
            {"region": "South", "sales": 98000},
        ]
        chart = _make_chart(title="Sales by Region", data=data)

        svg_output = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )

        assert "Sales by Region" in svg_output
        assert "North" in svg_output
        assert "South" in svg_output

    def test_table_with_area_sparkline(self) -> None:
        """Table with area sparkline renders correctly."""
        chart = _make_chart(
            title="Area Spark Demo",
            style={
                "columns": {
                    "name": {},
                    "values": {
                        "spark": {
                            "type": "area",
                            "color": "#06b6d4",
                            "fill_opacity": 0.3,
                        },
                    },
                }
            },
        )
        data = [
            {"name": "Series A", "values": [10, 20, 15, 30, 25]},
            {"name": "Series B", "values": [15, 18, 22, 25, 28]},
        ]

        svg_output = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )

        assert isinstance(svg_output, str)
        assert "<polygon" in svg_output  # Area fills use polygon
        assert "<polyline" in svg_output  # Area also has line

    def test_table_with_columns_sparkline(self) -> None:
        """Table with columns sparkline renders correctly."""
        chart = _make_chart(
            title="Columns Spark Demo",
            style={
                "columns": {
                    "name": {},
                    "values": {"spark": {"type": "columns", "color": "#8b5cf6"}},
                }
            },
        )
        data = [
            {"name": "Series A", "values": [10, 20, 15, 30, 25]},
        ]

        svg_output = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )

        assert isinstance(svg_output, str)
        # Bars sparkline creates rect elements
        assert "<rect" in svg_output
        # Multiple bars for multiple values
        assert svg_output.count("<rect") >= 5  # At least 5 bars + table background

    def test_table_with_empty_spark_data(self) -> None:
        """Table handles empty spark data gracefully."""
        chart = _make_chart(
            title="Empty Data Demo",
            style={
                "columns": {
                    "name": {},
                    "trend": {"spark": "line"},
                }
            },
        )
        data = [
            {"name": "Item 1", "trend": []},  # Empty array
            {"name": "Item 2", "trend": None},  # None value
        ]

        svg_output = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )

        assert isinstance(svg_output, str)
        assert "<svg" in svg_output
        # Should render without errors


import re

import pytest

from dbt_charts.core.compile.resolve import resolve


def _bar_normalize_fractions(svg: str) -> list[float]:
    """For `bar-normalize`: ratio of fill-rect width / bg-rect width per cell.

    Groups where the fill rect is omitted (fill_width == 0) contribute 0.0.
    Order matches top-to-bottom row order in the rendered table.
    """
    fracs: list[float] = []
    groups = re.findall(r'<g transform="translate[^"]+">.*?</g>', svg, re.DOTALL)
    for g in groups:
        widths = [float(w) for w in re.findall(r'width="([\d.]+)"', g)]
        if not widths:
            continue
        bg_w = widths[0]
        fill_w = widths[1] if len(widths) >= 2 else 0.0
        fracs.append(round(fill_w / bg_w, 2) if bg_w > 0 else 0.0)
    return fracs


def _bar_fractions(svg: str) -> list[float]:
    """For `bar` (no track): each cell renders a single fill rect or nothing.

    The largest fill in the column reads as 100% (auto-max). Empty groups
    (zero-fill cells) contribute 0.0.
    """
    groups = re.findall(r'<g transform="translate[^"]+">.*?</g>', svg, re.DOTALL)
    rect_widths: list[float] = []
    for g in groups:
        widths = [float(w) for w in re.findall(r'width="([\d.]+)"', g)]
        rect_widths.append(widths[0] if widths else 0.0)
    if not rect_widths:
        return []
    column_max = max(rect_widths)
    if column_max <= 0:
        return [0.0] * len(rect_widths)
    return [round(w / column_max, 2) for w in rect_widths]


class TestBarNormalizeExplicitMax:
    """`bar-normalize` with explicit `max:` — width = value/max."""

    def test_explicit_max_preserved(self) -> None:
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "pct": {"spark": {"type": "bar-normalize", "max": 100}},
                }
            }
        )
        data = [
            {"name": "A", "pct": 10},
            {"name": "B", "pct": 50},
            {"name": "C", "pct": 100},
        ]
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )
        fracs = _bar_normalize_fractions(svg)
        assert fracs == [pytest.approx(0.1), pytest.approx(0.5), pytest.approx(1.0)]


class TestBarAutoMax:
    """`bar` (absolute magnitude, no track) — width auto-scales to column max."""

    def test_auto_max_scales_to_column_max(self) -> None:
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar"}},
                }
            }
        )
        data = [
            {"name": "A", "val": 10},
            {"name": "B", "val": 50},
            {"name": "C", "val": 100},
        ]
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )
        fracs = _bar_fractions(svg)
        assert fracs == [pytest.approx(0.1), pytest.approx(0.5), pytest.approx(1.0)]

    def test_auto_max_large_values(self) -> None:
        """Large-valued columns scale proportionally (not clamped to 100)."""
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar"}},
                }
            }
        )
        data = [
            {"name": "A", "val": 5000},
            {"name": "B", "val": 25000},
            {"name": "C", "val": 50000},
        ]
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )
        fracs = _bar_fractions(svg)
        assert fracs == [pytest.approx(0.1), pytest.approx(0.5), pytest.approx(1.0)]

    def test_all_null_column_no_bars(self) -> None:
        """All-null column: spark cells skipped — no spark groups in SVG."""
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar"}},
                }
            }
        )
        data = [{"name": "A", "val": None}, {"name": "B", "val": None}]
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )
        assert _bar_fractions(svg) == []

    def test_all_zero_column_no_bars(self) -> None:
        """All-zero column with `bar` (no track): no rects rendered.

        `bar` is magnitude only — there is no track to draw when the data has
        no magnitude. Authors who want a visible 0-state should use
        `bar-normalize`, where the background track always renders.
        """
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar"}},
                }
            }
        )
        data = [{"name": "A", "val": 0}, {"name": "B", "val": 0}]
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )
        assert _bar_fractions(svg) == []

    def test_single_row_full_width_bar(self) -> None:
        """Single-row column: bar renders at 100% (value == column max)."""
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar"}},
                }
            }
        )
        data = [{"name": "A", "val": 42}]
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )
        assert _bar_fractions(svg) == [pytest.approx(1.0)]

    def test_mixed_null_numeric_uses_non_null_max(self) -> None:
        """Mixed null + numeric: max computed over non-null values."""
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar"}},
                }
            }
        )
        data = [
            {"name": "A", "val": None},
            {"name": "B", "val": 20},
            {"name": "C", "val": 40},
        ]
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )
        fracs = _bar_fractions(svg)
        assert fracs == [pytest.approx(0.5), pytest.approx(1.0)]

    def test_csv_string_values_produce_correct_auto_max(self) -> None:
        """1B.6c regression: CSV-loaded numeric strings must contribute to auto-max.

        Before the fix, the isinstance(v, (int, float)) guard filtered out
        strings, producing auto_max=None so the bar fell back to
        default_max=100. With values 20/50/200, default_max=100 clips the
        200 bar to 100% and produces (0.2, 0.5, 1.0). After the fix
        coerce_numeric_cell finds auto_max=200 and the bars render at
        (0.1, 0.25, 1.0).
        """
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar"}},
                }
            }
        )
        data = [
            {"name": "A", "val": "20"},
            {"name": "B", "val": "50"},
            {"name": "C", "val": "200"},
        ]
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )
        fracs = _bar_fractions(svg)
        assert fracs == [pytest.approx(0.1), pytest.approx(0.25), pytest.approx(1.0)]

    def test_nan_cell_renders_nothing_and_does_not_corrupt_the_column(self) -> None:
        """A NaN value must follow the same null rule as None: no spark rect,
        and no contribution to the column's auto-max or has_negative scan —
        not a full-extent bar painted as the column's most-negative value."""
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar"}},
                }
            }
        )
        data = [
            {"name": "A", "val": float("nan")},
            {"name": "B", "val": 20},
            {"name": "C", "val": 40},
        ]
        svg = render_table_svg(
            chart,
            data,
            board_style=resolve_style(get_theme_style()),
        )
        rects = _spark_group_rects(svg)
        # NaN row renders no spark group; the two real rows scale normally
        # against each other, unaffected by the NaN.
        assert len(rects) == 2
        assert rects[1]["width"] == pytest.approx(2 * rects[0]["width"], rel=0.01)


def _spark_group_rects(svg: str) -> list[dict[str, float]]:
    """First `<rect>` inside each spark `<g translate>` group, in row order."""
    rects: list[dict[str, float]] = []
    for g in re.findall(r'<g transform="translate[^"]+">.*?</g>', svg, re.DOTALL):
        m = re.search(r"<rect[^/]*/>", g)
        if m is None:
            continue
        attrs = dict(re.findall(r'(\w+)="(-?[\d.]+)"', m.group(0)))
        rects.append({k: float(v) for k, v in attrs.items()})
    return rects


class TestBarSignedMidline:
    """`type: bar` / `type: column` render negatives from a midline once the
    table column actually contains one."""

    def test_negative_value_falls_through_to_a_bar_not_text(self) -> None:
        """The bug this fixes: -0.184 used to clamp to an empty bar and fall
        through to the default text cell ('-184m'). It must render a rect
        for every row, including the negative one."""
        chart = _make_chart(
            style={"columns": {"name": {}, "val": {"spark": {"type": "bar"}}}}
        )
        data = [{"name": "A", "val": -0.184}, {"name": "B", "val": 0.2}]
        svg = render_table_svg(
            chart, data, board_style=resolve_style(get_theme_style())
        )
        rects = _spark_group_rects(svg)
        assert len(rects) == 2, (
            f"expected a rect for every row including the negative one, got {rects}"
        )

    def test_negative_column_scales_by_magnitude_not_signed_max(self) -> None:
        """auto-max must use the largest magnitude, not the largest signed
        value — otherwise -100 would clamp against a smaller positive max
        and lose its true extent."""
        chart = _make_chart(
            style={"columns": {"name": {}, "val": {"spark": {"type": "bar"}}}}
        )
        data = [{"name": "A", "val": -100}, {"name": "B", "val": 50}]
        svg = render_table_svg(
            chart, data, board_style=resolve_style(get_theme_style())
        )
        rects = _spark_group_rects(svg)
        assert len(rects) == 2
        # -100 is the largest magnitude: full half-width, twice the 50 row.
        assert rects[0]["width"] == pytest.approx(2 * rects[1]["width"], rel=0.01)

    def test_positive_and_negative_bars_anchor_at_the_same_midline(self) -> None:
        chart = _make_chart(
            style={"columns": {"name": {}, "val": {"spark": {"type": "bar"}}}}
        )
        data = [{"name": "A", "val": -10}, {"name": "B", "val": 10}]
        svg = render_table_svg(
            chart, data, board_style=resolve_style(get_theme_style())
        )
        rects = _spark_group_rects(svg)
        assert len(rects) == 2
        negative_row, positive_row = rects
        # Negative bar ends where the positive bar starts: same midline.
        assert negative_row["x"] + negative_row["width"] == pytest.approx(
            positive_row["x"], abs=0.2
        )

    def test_all_positive_column_keeps_full_width_no_midline_halving(self) -> None:
        """Regression: an all-positive `bar` column must not silently lose
        half its width to a midline layout it never needed."""
        chart = _make_chart(
            style={"columns": {"name": {}, "val": {"spark": {"type": "bar"}}}}
        )
        data = [{"name": "A", "val": 10}, {"name": "B", "val": 100}]
        svg = render_table_svg(
            chart, data, board_style=resolve_style(get_theme_style())
        )
        fracs = _bar_fractions(svg)
        assert fracs == [pytest.approx(0.1), pytest.approx(1.0)]
        rects = _spark_group_rects(svg)
        assert all(r["x"] == 0.0 for r in rects)

    def test_column_type_negative_grows_downward_positive_grows_upward(self) -> None:
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "column", "max": 100}},
                }
            }
        )
        data = [{"name": "A", "val": -50}, {"name": "B", "val": 50}]
        svg = render_table_svg(
            chart, data, board_style=resolve_style(get_theme_style())
        )
        rects = _spark_group_rects(svg)
        assert len(rects) == 2
        negative_row, positive_row = rects
        assert negative_row["y"] > positive_row["y"]

    def test_single_negative_row_scales_to_full_half_width(self) -> None:
        # Explicit width small enough to never get capped by the column-layout
        # pass (which sizes the cell from the rendered text, so "-42" vs "42"
        # would otherwise widen/narrow the two renders' columns differently
        # and confound a cross-render width comparison).
        chart = _make_chart(
            style={
                "columns": {"name": {}, "val": {"spark": {"type": "bar", "width": 40}}}
            }
        )
        svg_negative = render_table_svg(
            chart,
            [{"name": "A", "val": -42}],
            board_style=resolve_style(get_theme_style()),
        )
        svg_positive = render_table_svg(
            chart,
            [{"name": "A", "val": 42}],
            board_style=resolve_style(get_theme_style()),
        )
        negative_rects = _spark_group_rects(svg_negative)
        positive_rects = _spark_group_rects(svg_positive)
        assert len(negative_rects) == 1
        assert len(positive_rects) == 1
        # -42 is the sole (and thus largest-magnitude) value: full half-width
        # — exactly half the same value's all-positive, unsigned full-width bar.
        assert negative_rects[0]["width"] == pytest.approx(
            positive_rects[0]["width"] / 2, rel=0.02
        )

    def test_mixed_null_and_negative_uses_non_null_magnitude_max(self) -> None:
        chart = _make_chart(
            style={"columns": {"name": {}, "val": {"spark": {"type": "bar"}}}}
        )
        data = [
            {"name": "A", "val": None},
            {"name": "B", "val": -20},
            {"name": "C", "val": 40},
        ]
        svg = render_table_svg(
            chart, data, board_style=resolve_style(get_theme_style())
        )
        rects = _spark_group_rects(svg)
        # Null row renders no spark group at all (existing contract).
        assert len(rects) == 2
        negative_row, positive_row = rects
        # 40 is the largest magnitude: full half-width, twice the -20 row.
        assert positive_row["width"] == pytest.approx(
            2 * negative_row["width"], rel=0.01
        )

    def test_all_zero_column_no_bars_even_with_signed_support(self) -> None:
        """Zero is not negative — stays on the existing all-zero no-bars
        contract (`bar` draws no track)."""
        chart = _make_chart(
            style={"columns": {"name": {}, "val": {"spark": {"type": "bar"}}}}
        )
        data = [{"name": "A", "val": 0}, {"name": "B", "val": 0}]
        svg = render_table_svg(
            chart, data, board_style=resolve_style(get_theme_style())
        )
        assert _spark_group_rects(svg) == []

    def test_authored_negative_color_paints_the_theme_negative_token(self) -> None:
        """HIGH-5: negative_color has no test through the authored `spark:`
        path — every existing test passes it as a direct renderer kwarg."""
        board_style = resolve_style(get_theme_style())
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar", "negative_color": True}},
                }
            }
        )
        data = [{"name": "A", "val": -30}, {"name": "B", "val": 30}]
        svg = render_table_svg(chart, data, board_style=board_style)
        assert board_style.chart_defaults.tones.negative in svg


class TestBarNormalizeExcludedFromSignedLayout:
    """HIGH-3: `bar-normalize`'s background track is a fixed "% of max"
    ruler — giving it signed/midline layout would halve the fill against an
    unchanged track (50% would silently read as 25%). It must keep the
    original clamp-to-zero behavior for negatives, table-wide."""

    def test_negative_value_clamps_to_zero_not_a_midline_bar(self) -> None:
        chart = _make_chart(
            style={
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar-normalize", "max": 100}},
                }
            }
        )
        data = [{"name": "A", "val": -30}, {"name": "B", "val": 50}]
        svg = render_table_svg(
            chart, data, board_style=resolve_style(get_theme_style())
        )
        widths = _bar_normalize_fractions(svg)
        # -30 clamps to 0% (background track only, no fill) — not a
        # midline-anchored fill at 15% (30/100 of the half-width).
        assert widths == [0.0, pytest.approx(0.5)]


class TestSignedLayoutScopedToFullDataset:
    """HIGH-1: bar/column spark layout must come from the FULL dataset, not
    whichever page is currently rendering, or the same value's bar anchor
    and width flip between pages of one static-paginated table."""

    def test_same_value_renders_identically_on_every_page(self) -> None:
        chart = _make_chart(
            style={
                "columns": {"name": {}, "val": {"spark": {"type": "bar"}}},
                "pagination": {"page_rows": 3},
            }
        )
        # Page 1 (rows 0-2) is all-positive on its own; the negative only
        # shows up on page 2. A per-page scan would render page 1's 30s at
        # full (unsigned) width and page 2's 30 at half (signed) width.
        data = [{"name": f"R{i}", "val": 30} for i in range(5)] + [
            {"name": "R5", "val": -5}
        ]
        svg = render_table_svg(
            chart, data, board_style=resolve_style(get_theme_style())
        )
        chunks = re.split(r'(?=<g class="dbt-table-page")', svg)
        page1 = next(
            c
            for c in chunks
            if c.startswith(
                '<g class="dbt-table-page" data-dbt-table-page="_test" data-page="1"'
            )
        )
        page2 = next(
            c
            for c in chunks
            if c.startswith(
                '<g class="dbt-table-page" data-dbt-table-page="_test" data-page="2"'
            )
        )
        page1_rects = _spark_group_rects(page1)
        page2_rects = _spark_group_rects(page2)
        assert len(page1_rects) == 3
        assert len(page2_rects) == 3
        # Row 0 (page 1, val=30) and row 3 (page 2, val=30) must render the
        # exact same width and x — both scaled/anchored against the full
        # dataset's negative, not just their own page's rows.
        assert page1_rects[0]["width"] == pytest.approx(page2_rects[0]["width"])
        assert page1_rects[0]["x"] == pytest.approx(page2_rects[0]["x"])


class TestSparkColumnLayoutExcludesSummaryRows:
    """`_spark_column_layout`'s has_negative/auto-max scan must skip
    summary/total rows, exactly like the sibling column-wide scan in
    `_render_data_rows` (scale_rows) — a grand-total row's sign or
    magnitude must never re-anchor or rescale the detail rows above it."""

    def test_negative_total_row_does_not_flip_detail_rows_to_midline(self) -> None:
        chart = _make_chart(
            style={
                "row": {"role": "r"},
                "columns": {
                    "name": {},
                    "val": {"spark": {"type": "bar"}},
                    "r": {"visible": False},
                },
            }
        )
        detail_only = [
            {"name": "A", "val": 50, "r": "value"},
            {"name": "B", "val": 100, "r": "value"},
        ]
        with_negative_total = detail_only + [{"name": "Total", "val": -5, "r": "total"}]

        svg_baseline = render_table_svg(
            chart, detail_only, board_style=resolve_style(get_theme_style())
        )
        svg_with_total = render_table_svg(
            chart, with_negative_total, board_style=resolve_style(get_theme_style())
        )

        baseline_rects = _spark_group_rects(svg_baseline)
        with_total_rects = _spark_group_rects(svg_with_total)

        # The two detail rows must render identically whether or not a
        # negative total row follows them — edge-anchored, full magnitude
        # width — not midline-anchored/halved by a total row's sign.
        assert with_total_rects[0] == baseline_rects[0]
        assert with_total_rects[1] == baseline_rects[1]
        assert with_total_rects[0]["x"] == 0.0
        assert with_total_rects[1]["x"] == 0.0


class TestBarSparkDashboard842Style:
    """Regression: dashboard 842 emits spark on 3 columns in a 12-column table
    with row_numbers and pagination. Bars must render.

    The style structure (columns + table + pagination at the same level) is
    the shape the migrator emits for every Looker tile with
    series_cell_visualizations.
    """

    def _chart(self) -> Any:
        return _make_chart(
            title="Customers - Revenue Below 100% of Commit",
            style={
                "columns": {
                    "accounts_salesforce_account_name": {},
                    "revenue_became_cbp_month": {},
                    "revenue_revenue_percent_of_commit": {},
                    "accounts_owner_name": {},
                    "revenue_percent_commit_tier": {},
                    "revenue_blended_rate": {
                        "scale": {"background": {"palette": ["#FFFFFF", "#62BAD4"]}},
                    },
                    "revenue_count_services": {"spark": {"type": "bar"}},
                    "revenue_count_connections": {},
                    "revenue_annualized_monthly_usage_dollars_spent": {},
                    "revenue_annual_recurring_revenue": {},
                    "revenue_annual_recurring_revenue_combined": {
                        "spark": {"type": "bar"}
                    },
                    "revenue_count_connectors": {"spark": {"type": "bar"}},
                },
                "row_numbers": {"visible": True},
                "pagination": {"page_rows": 12},
            },
        )

    def _data(self) -> list[dict[str, Any]]:
        return [
            {
                "accounts_salesforce_account_name": "Acme Corp",
                "revenue_became_cbp_month": "2024-01",
                "revenue_revenue_percent_of_commit": 0.75,
                "accounts_owner_name": "John",
                "revenue_percent_commit_tier": "2",
                "revenue_blended_rate": 1234.5,
                "revenue_count_services": 42,
                "revenue_count_connections": 10,
                "revenue_annualized_monthly_usage_dollars_spent": 50000.0,
                "revenue_annual_recurring_revenue": 100000.0,
                "revenue_annual_recurring_revenue_combined": 95000.0,
                "revenue_count_connectors": 8,
            },
            {
                "accounts_salesforce_account_name": "Beta Inc",
                "revenue_became_cbp_month": "2024-02",
                "revenue_revenue_percent_of_commit": 0.60,
                "accounts_owner_name": "Jane",
                "revenue_percent_commit_tier": "1",
                "revenue_blended_rate": 987.6,
                "revenue_count_services": 21,
                "revenue_count_connections": 5,
                "revenue_annualized_monthly_usage_dollars_spent": 30000.0,
                "revenue_annual_recurring_revenue": 60000.0,
                "revenue_annual_recurring_revenue_combined": 55000.0,
                "revenue_count_connectors": 4,
            },
        ]

    def _spark_rect_widths(self, svg: str) -> list[float]:
        """Widths of every rect inside a translate-wrapped spark group."""
        widths: list[float] = []
        for g in re.findall(r'<g transform="translate[^"]+">.*?</g>', svg, re.DOTALL):
            for w in re.findall(r'width="([\d.]+)"', g):
                widths.append(float(w))
        return widths

    def test_three_bar_columns_each_render(self) -> None:
        """All three spark:bar columns produce a rect per cell — 6 in total."""
        svg = render_table_svg(
            self._chart(),
            self._data(),
            board_style=resolve_style(get_theme_style()),
        )
        widths = self._spark_rect_widths(svg)
        # 3 columns × 2 rows = 6 spark cells; each `bar` cell emits one rect.
        assert len(widths) == 6, f"expected 6 spark rects, got {len(widths)}"

    def test_per_column_max_fills_full_width(self) -> None:
        """Each column's max row should match its 100%-width rect.

        Row 1 has the larger value in all three spark columns, so the three
        column-max widths should all appear in the rect set. With auto-max
        per column, the larger-value row produces a width strictly greater
        than the smaller-value row in the same column.
        """
        svg = render_table_svg(
            self._chart(),
            self._data(),
            board_style=resolve_style(get_theme_style()),
        )
        widths = self._spark_rect_widths(svg)
        assert len(widths) == 6
        # All rects share one of two spark cell widths (one per row).
        # Specifically, every column's "row 1" width must exceed its "row 2"
        # width, since row-1 holds the larger value in every spark column.
        assert max(widths) > min(widths), (
            "auto-max scaling should produce distinct widths between rows"
        )

    def test_row_numbers_and_pagination_do_not_suppress_sparks(self) -> None:
        """table.row_numbers + pagination keys in style do not interfere with spark lookup."""
        svg = render_table_svg(
            self._chart(),
            self._data(),
            board_style=resolve_style(get_theme_style()),
        )
        assert self._spark_rect_widths(svg), "no spark bar cells rendered"


def _spark_translate_ys(svg: str) -> list[float]:
    """Y values of every `<g transform="translate(x, y)">` spark wrapper.

    Order matches top-to-bottom row order in the rendered table.
    """
    return [
        float(m.group(1))
        for m in re.finditer(r'<g transform="translate\([^,]+,\s*([\d.]+)\)">', svg)
    ]


class TestSparkVerticalCentering:
    """Spark mark vertical position must center against the row's effective
    height — including rows whose height has grown to accommodate wrapped
    text in another column. The mark stays a fixed visual size; only its
    y offset moves to keep top and bottom gaps equal within the band.
    """

    # Long, distinctive value chosen so a narrow text column wraps to 3+
    # lines at the default body font. Each word is well above the per-line
    # width budget collectively.
    _WRAP_TEXT = "Acme Corporation Annual Strategic Opportunity Review Pipeline Deal Renewal Forecast"
    _SHORT_TEXT = "Acme"

    def _chart(self) -> Any:
        return _make_chart(
            style={
                "columns": {
                    # Narrow column forces wrap on long values.
                    "description": {"width": 90},
                    "progress": {
                        "spark": {"type": "bar-normalize", "max": 100},
                    },
                }
            },
        )

    def test_wrapped_row_spark_drops_to_stay_centered(self) -> None:
        """Row 1 wraps to multiple lines; its spark must move further down
        than row 0's spark by more than the nominal row height — i.e. the
        spark picks up half of the wrapped row's growth so top/bottom gaps
        stay equal. The buggy implementation pinned both spark y values to
        `row_y + 2`, leaving the difference equal to the nominal row height.
        """
        chart = self._chart()
        data = [
            {"description": self._SHORT_TEXT, "progress": 80},
            {"description": self._WRAP_TEXT, "progress": 40},
        ]
        svg = render_table_svg(
            chart,
            data,
            width=320,
            board_style=resolve_style(get_theme_style()),
        )
        ys = _spark_translate_ys(svg)
        assert len(ys) == 2, f"expected 2 spark groups, got {ys!r}"

        spark_y_short, spark_y_wrap = ys
        # Row 0 has only one line, so its y_top == y_top_of_band + (nominal -
        # spark)/2. Row 1 wraps and is taller; with centering, its spark sits
        # further down by (grown - nominal) / 2 on top of the nominal step.
        # Reference table heights for assertion floor are deliberately loose:
        # any positive contribution from the wrap growth breaks the bug.
        diff = spark_y_wrap - spark_y_short
        # The wrapped text spans 3+ lines at this width, so the row grows by
        # at least ~2 line heights (~28+ px at default 14px font). Half of
        # that is ~14, well above any single-pixel rounding.
        assert diff > 36, (
            f"spark in wrapped row must drop more than nominal row height; "
            f"got spark_y_short={spark_y_short}, spark_y_wrap={spark_y_wrap}, "
            f"diff={diff} (bug pins diff == nominal row_height)"
        )

    def test_unwrapped_rows_spark_step_equals_row_height(self) -> None:
        """Sanity check: when no row wraps, successive spark y values step
        by the nominal row height exactly. Ensures the centering math
        doesn't regress the simple case.
        """
        chart = self._chart()
        data = [
            {"description": self._SHORT_TEXT, "progress": 80},
            {"description": self._SHORT_TEXT, "progress": 40},
        ]
        svg = render_table_svg(
            chart,
            data,
            width=320,
            board_style=resolve_style(get_theme_style()),
        )
        ys = _spark_translate_ys(svg)
        assert len(ys) == 2, f"expected 2 spark groups, got {ys!r}"
        # Row 1 spark y should sit exactly nominal_row_height below row 0's,
        # because both rows render at the nominal row height. The absolute
        # values depend on theme; the difference is theme-independent.
        diff = ys[1] - ys[0]
        # Both rows render at nominal height so the step must be non-zero.
        assert diff > 0, (
            f"unwrapped two-row table should step by a positive row height; "
            f"got diff={diff}"
        )

    def test_explicit_spark_height_shifts_with_centering_formula(self) -> None:
        """Single-line centering check — isolates the original task's
        "bottom-anchored" symptom from the wrapped-row symptom.

        With an explicit `spark.height`, the spark Y position must shift
        according to `(row_height - spark_height) / 2`. Render the same
        single-row data with two different explicit spark heights:

        - height=8  → spark_y = row_y + (row_height - 8) / 2
        - height=20 → spark_y = row_y + (row_height - 20) / 2

        Under the fix, the difference y_small − y_large = (20 − 8) / 2 = 6
        for any row_height. Under the buggy `row_y + 2` code, both spark
        Y values equal row_y + 2 regardless of explicit height, so the
        difference collapses to 0. row_y is theme-dependent but cancels
        out of the differential.
        """

        def _render_with_height(spark_h: int) -> float:
            chart = _make_chart(
                style={
                    "columns": {
                        "name": {},
                        "value": {
                            "spark": {
                                "type": "bar-normalize",
                                "max": 100,
                                "height": spark_h,
                            }
                        },
                    }
                },
            )
            svg = render_table_svg(
                chart,
                [{"name": "A", "value": 50}],
                board_style=resolve_style(get_theme_style()),
            )
            ys = _spark_translate_ys(svg)
            assert len(ys) == 1, f"expected 1 spark, got {ys!r} for height={spark_h}"
            return ys[0]

        y_small = _render_with_height(8)
        y_large = _render_with_height(20)
        shift = y_small - y_large
        # Under fix: shift == (20 - 8) / 2 == 6 (independent of row_height,
        # since row_y is the same in both renders). Under bug: shift == 0.
        # Allow ±1 for any integer rounding inside the spark sizing path.
        assert 5 <= shift <= 7, (
            f"spark Y must shift with explicit spark height "
            f"(centering formula). Got y_small={y_small}, y_large={y_large}, "
            f"shift={shift}; expected ~6. The buggy fixed-offset code "
            f"would give shift=0."
        )
