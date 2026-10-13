"""Tests for the PIE_TOTAL_EXCEEDS_INNER_RADIUS render-warning detector.

Detection rule: fires on donut charts whose formatted center total is wider than
the hole, measured with the repo's font measurer against the real layout
dimensions (width from vega_specs or wheel_width; height from
layout_chart_heights or the theme's continuousHeight for attached-table wheels).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.chart.normalized import BarChart, Chart, PieChart
from dbt_charts.core.compile.models.chart.normalized.pie import PieChartStylePatch
from dbt_charts.core.diagnostics import (
    WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS,
    Diagnostic,
)
from dbt_charts.core.render.warnings import (
    WarningContext,
    pie_total_exceeds_inner_radius as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _make_donut(total_format: str | None = "integer", **kwargs: object) -> Chart:
    total: ChartTotal | None = (
        ChartTotal(visible=True) if total_format is not None else None
    )
    # Use model_validate to avoid pyright's TYPE_CHECKING stub requiring all
    # PieChartStyle fields — at runtime build_patch_model makes every field optional.
    # Slice labels off by default so the fixture's plot widths stay the
    # geometry under test (the label-reach deduction has its own test below).
    total_value: dict[str, Any] = {"font": {"size": 18}}
    if total_format is not None:
        total_value["format"] = total_format
    style = PieChartStylePatch.model_validate(
        {
            "inner_radius": 0.6,
            "total": {"value": total_value},
            "marks": {"slice": {"labels": {"where": "false"}}},
        }
    )
    return PieChart(
        **{
            "id": "c1",
            "type": "donut",
            "query_name": "q",
            "theta": "amount",
            "style": style,
            "total": total,
            **kwargs,
        }
    )


def _make_ctx(
    chart: Chart,
    rows: list[dict[str, Any]],
    plot_width: float,
    plot_height: float,
) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={resolved.id: {"mark": "arc", "width": plot_width}},
        layout_chart_heights={resolved.id: plot_height},
        layout_charts={resolved.id: resolved},
    )


_NARROW_ROWS = [{"category": "a", "amount": 113135}]
_WIDE_ROWS = [{"category": "a", "amount": 5}]
# 1,000,000 integer-formats to "1,000,000" (85px) but compacts to "1M" (23px).
_MILLION_ROWS = [{"category": "a", "amount": 1000000}]


def test_fires_when_total_overflows_hole() -> None:
    """113,135 at 18px ≈ 60px > hole (~47px) in an 88×240 plot box."""
    chart = _make_donut()
    warnings = detector.detect(_make_ctx(chart, _NARROW_ROWS, 88, 240))
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS.code
    assert w.chart == "c1"
    assert "113,135" in w.message
    # The editor anchor must name the field an author can actually set.
    assert w.path == "charts.c1.style.total.value.format"
    assert w.field == "format"


def test_no_fire_when_total_fits() -> None:
    """5 in a wide 400×300 plot box — hole is ~162px, text fits easily."""
    chart = _make_donut(total_format="integer")
    assert detector.detect(_make_ctx(chart, _WIDE_ROWS, 400, 300)) == []


def test_no_fire_when_total_none() -> None:
    chart = _make_donut(total_format=None)
    assert detector.detect(_make_ctx(chart, _NARROW_ROWS, 88, 240)) == []


def test_no_fire_when_total_not_visible() -> None:
    chart = PieChart(
        id="c1",
        type="donut",
        query_name="q",
        theta="amount",
        style=PieChartStylePatch.model_validate(
            {"inner_radius": 0.6, "total": {"value": {"format": "integer"}}}
        ),
        total=ChartTotal(visible=False),
    )
    assert detector.detect(_make_ctx(chart, _NARROW_ROWS, 88, 240)) == []


def test_no_fire_for_non_pie() -> None:
    chart = BarChart(id="c1", type="bar", query_name="q", x="category", y="amount")
    resolved = make_test_resolved_chart(chart, _NARROW_ROWS)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: _NARROW_ROWS},
        vega_specs={resolved.id: {"width": 88, "height": 240}},
        layout_charts={resolved.id: resolved},
    )
    assert detector.detect(ctx) == []


def test_no_fire_when_chart_absent_from_layout_charts() -> None:
    """Charts not reached by the active layout tree are absent from
    layout_charts — the detector must skip them."""
    chart = _make_donut()
    resolved = make_test_resolved_chart(chart, _NARROW_ROWS)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: _NARROW_ROWS},
        vega_specs={resolved.id: {"mark": "arc", "width": 88.0}},
        layout_chart_heights={resolved.id: 240.0},
        layout_charts={},  # chart absent from active layout
    )
    assert detector.detect(ctx) == []


def test_compact_format_fits() -> None:
    """1,000,000 under compact format emits '1M' (23px) — fits the 47.5px hole.
    Under integer format the same value would emit '1,000,000' (86px) and overflow.
    Verifies the detector measures the formatted string, not the raw number."""
    chart = _make_donut(total_format="number")
    assert detector.detect(_make_ctx(chart, _MILLION_ROWS, 88, 240)) == []


_DECIMAL_ROWS = [{"category": "a", "amount": Decimal("56789")}]


def test_fires_with_decimal_theta_values() -> None:
    """Warehouse NUMERIC/DECIMAL columns produce Decimal values in executor rows.
    The detector must include them in the theta sum rather than silently dropping
    them — a Decimal-only sum of 0 would never produce a wide enough string to fire.
    56,789 formatted as integer at 18px is wide enough to overflow the 47px hole."""
    chart = _make_donut()
    warnings = detector.detect(_make_ctx(chart, _DECIMAL_ROWS, 88, 240))
    assert len(warnings) == 1, (
        "Decimal theta values must be included in the sum — expected the "
        "warning to fire for 56,789 overflowing the ~47px hole"
    )


def test_unauthored_total_measures_the_resolve_time_default_format() -> None:
    """A donut with no format authored anywhere in the cascade still gets one:
    _resolve_pie defaults style.total.value.format to the integer preset for
    any donut shape (see test_donut_auto_total_defaults_format_to_integer_preset
    in test_new_compiler.py). The detector must measure that resolved,
    comma-grouped text -- never a raw Python float repr, and never the bare
    digit string a since-closed unformatted branch used to fall back to."""
    chart = PieChart(
        id="c1",
        type="donut",
        query_name="q",
        theta="amount",
        style=PieChartStylePatch.model_validate(
            {"inner_radius": 0.6, "total": {"value": {"font": {"size": 18}}}}
        ),
        total=ChartTotal(visible=True),  # no format authored anywhere
    )
    warnings = detector.detect(_make_ctx(chart, _MILLION_ROWS, 88, 240))
    assert len(warnings) == 1
    assert "1,000,000" in warnings[0].message
    assert "1000000.0" not in warnings[0].message


def test_fires_for_attached_table_donut() -> None:
    """Attached-table donuts render as SVG and are absent from vega_specs.
    The detector sources width from chart.wheel_width and height from
    board_spec.style.chart_defaults.view.continuous_height (the Vega default
    when the wheel is stamped with height=None in _render_arc_attached_table)."""
    from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart

    chart = _make_donut()
    # One slice with < 2% share triggers hybrid/attached-table mode.
    rows = [{"amount": 999990}, {"amount": 10}]
    resolved = make_test_resolved_chart(chart, rows, width=150)
    assert isinstance(resolved, ResolvedPieChart)
    assert resolved.attached_table is not None, (
        "expected hybrid (attached-table) mode: 0.001% share < invisible_slice_share"
    )

    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={},  # attached-table charts absent from vega_specs
        layout_chart_heights={},  # not used for attached-table path
        layout_charts={resolved.id: resolved},
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1, (
        "1,000,000 total must overflow the hole via the attached-table path "
        f"(wheel_width={resolved.wheel_width}px)"
    )
    assert warnings[0].chart == "c1"


def test_slice_label_reach_shrinks_the_measured_hole() -> None:
    """A labeled donut's hole is judged against the label-deducted disk width
    (arc_disk_width), so a total that would fit the bare slot can still warn.
    Same chart and 300x240 slot: label-less passes, labeled fires."""
    bare = _make_donut()
    assert detector.detect(_make_ctx(bare, _NARROW_ROWS, 300, 240)) == []

    labeled_style = PieChartStylePatch.model_validate(
        {
            "inner_radius": 0.6,
            "total": {"value": {"font": {"size": 18}}},
            "marks": {
                "slice": {
                    "labels": {
                        "template": "{{ category }}",
                        "offset": 8,
                        "font": {"family": "Inter", "size": 13},
                    }
                }
            },
        }
    )
    labeled = _make_donut(style=labeled_style)
    # Long enough for the reach to bind the 300px slot, short enough that
    # classification keeps direct mode (a still-longer label flips to
    # full_table, which drops the labels entirely).
    label_rows = [{"category": "Signature reminders", "amount": 113135}]
    warnings = detector.detect(_make_ctx(labeled, label_rows, 300, 240))
    assert len(warnings) == 1, (
        "slice labels reserve reach on both sides of the disk; the deducted "
        "width must bind before the 240px height does"
    )
