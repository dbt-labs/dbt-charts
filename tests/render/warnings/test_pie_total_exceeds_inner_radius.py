"""Tests for the PIE_TOTAL_EXCEEDS_INNER_RADIUS render-warning detector.

The engine measures the formatted center total against the hole the wheel
draws and records an overflow; the detector turns each record into a warning.
These tests render through the engine, then run the detector on what it
recorded.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.chart.normalized import PieChart
from dbt_charts.core.compile.models.chart.normalized.pie import PieChartStylePatch
from dbt_charts.core.diagnostics import WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS, Diagnostic
from dbt_charts.core.render.chart.pie_total_overflow import (
    PieTotalOverflow,
    collect_pie_total_overflows,
)
from dbt_charts.core.render.warnings import (
    WarningContext,
    pie_total_exceeds_inner_radius as detector,
)

from ...core._board_utils import make_test_resolved_board
from ...core._engine_render import render_pie_svg


def _make_donut(total_format: str | None = "integer", **kwargs: object) -> PieChart:
    total = ChartTotal(visible=True) if total_format is not None else None
    # Slice labels off by default so the plot box stays the geometry under
    # test (label reach has its own test below).
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


def _warnings(
    chart: PieChart, rows: list[dict[str, Any]], width: float, height: float
) -> list[Diagnostic]:
    with collect_pie_total_overflows() as overflows:
        render_pie_svg(chart, rows, width, height)
    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={}),
        chart_results={},
        vega_specs={},
        pie_total_overflows=overflows,
    )
    return detector.detect(ctx)


_NARROW_ROWS = [{"category": "a", "amount": 113135}]
_WIDE_ROWS = [{"category": "a", "amount": 5}]
# 1,000,000 integer-formats to "1,000,000" but compacts to "1M".
_MILLION_ROWS = [{"category": "a", "amount": 1000000}]


def test_a_record_becomes_a_warning_anchored_on_the_total_format() -> None:
    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={}),
        chart_results={},
        vega_specs={},
        pie_total_overflows={
            "c1": PieTotalOverflow(
                formatted_value="113,135",
                text_width=60.0,
                hole_diameter=47.5,
                slot_width=88.0,
                slot_height=240.0,
            )
        },
    )
    [w] = detector.detect(ctx)
    assert w.code == WARN_PIE_TOTAL_EXCEEDS_INNER_RADIUS.code
    assert w.chart == "c1"
    assert "113,135" in w.message
    # The editor anchor must name the field an author can actually set.
    assert w.path == "charts.c1.style.total.value.format"
    assert w.field == "format"


def test_fires_when_total_overflows_hole() -> None:
    [w] = _warnings(_make_donut(), _NARROW_ROWS, 88, 240)
    assert w.chart == "c1"
    assert "113,135" in w.message


def test_no_fire_when_total_fits() -> None:
    assert _warnings(_make_donut(), _WIDE_ROWS, 400, 300) == []


def test_no_fire_when_total_not_visible() -> None:
    chart = _make_donut(total=ChartTotal(visible=False))
    assert _warnings(chart, _NARROW_ROWS, 88, 240) == []


def test_compact_format_fits() -> None:
    """The warning measures the formatted string: "1M" fits where "1,000,000"
    would not."""
    assert _warnings(_make_donut(total_format="number"), _MILLION_ROWS, 88, 240) == []
    assert len(_warnings(_make_donut(), _MILLION_ROWS, 88, 240)) == 1


def test_fires_with_decimal_theta_values() -> None:
    """Warehouse NUMERIC/DECIMAL columns arrive as Decimal; they count toward
    the total."""
    rows = [{"category": "a", "amount": Decimal("56789")}]
    assert len(_warnings(_make_donut(), rows, 88, 240)) == 1


def test_unauthored_total_measures_the_resolve_time_default_format() -> None:
    """A donut with no format authored resolves the integer preset, so the
    measured text is comma-grouped, never a raw float repr."""
    [w] = _warnings(
        _make_donut(total_format=None, total=ChartTotal(visible=True)),
        _MILLION_ROWS,
        88,
        240,
    )
    assert "1,000,000" in w.message
    assert "1000000.0" not in w.message


def test_fires_for_attached_table_donut() -> None:
    """One slice under 2% share puts the donut in attached-table mode; the
    wheel it draws still carries the total."""
    rows = [{"category": "a", "amount": 999990}, {"category": "b", "amount": 10}]
    assert len(_warnings(_make_donut(), rows, 150, 300)) == 1


def test_slice_label_reach_shrinks_the_measured_hole() -> None:
    """Slice labels take reach on both sides of the disk, so a total that fits
    the bare wheel can overflow a labeled one at the same slot."""
    label_rows = [
        {"category": "Sig 0", "amount": 565675},
        {"category": "Sig 1", "amount": 565675},
    ]
    assert _warnings(_make_donut(), label_rows, 200, 400) == []
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
    assert len(_warnings(_make_donut(style=labeled_style), label_rows, 200, 400)) == 1
