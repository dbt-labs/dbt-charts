"""Tests for the CATEGORY_COLOR_PIN_UNSEEN render-warning detector.

Detection rule: fires once per FIELD when a `CategoryColorScale` bound to any
chart on the board carries a non-empty `unseen_pins` mapping — a pin
`_seatable_pins` (compile/resolve/style/category_colors.py) dropped because
this render's rows never draw the pinned value.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
)
from dbt_charts.core.diagnostics import WARN_CATEGORY_COLOR_PIN_UNSEEN, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    category_color_pin_unseen as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _chart(chart_id: str, field: str) -> BarChart:
    return BarChart(id=chart_id, type="bar", query_name="q", x="x", y="y", color=field)


def _resolved(chart_id: str, field: str, *scales: CategoryColorScale) -> ResolvedChart:
    resolved = make_test_resolved_chart(_chart(chart_id, field), [])
    return resolved.model_copy(update={"category_colors": scales})


def _ctx(**charts: ResolvedChart) -> WarningContext:
    board = make_test_resolved_board(charts=charts)
    return WarningContext(
        board_spec=board,
        chart_results={chart_id: [] for chart_id in charts},
    )


def test_fires_once_naming_the_unmatched_value() -> None:
    scale = CategoryColorScale(
        field="category",
        slots={"Electronics": 0},
        overrides={},
        unseen_pins={"Growth": "#ff0000"},
    )
    ctx = _ctx(c1=_resolved("c1", "category", scale))
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_CATEGORY_COLOR_PIN_UNSEEN.code
    assert w.field == "category"
    assert "Growth" in w.message
    assert w.fix is not None


def test_no_fire_when_pins_all_match() -> None:
    scale = CategoryColorScale(field="category", slots={"Electronics": 0}, overrides={})
    ctx = _ctx(c1=_resolved("c1", "category", scale))
    assert detector.detect(ctx) == []


def test_no_fire_with_no_bound_scale() -> None:
    ctx = _ctx(c1=_resolved("c1", "category"))
    assert detector.detect(ctx) == []


def test_fires_once_per_field_even_when_bound_to_multiple_charts() -> None:
    """The identical scale is attached to every chart drawing the field —
    a naive per-chart walk would fire once per chart for the same pin."""
    scale = CategoryColorScale(
        field="category",
        slots={"Electronics": 0},
        overrides={},
        unseen_pins={"Growth": "#ff0000"},
    )
    ctx = _ctx(
        c1=_resolved("c1", "category", scale),
        c2=_resolved("c2", "category", scale),
    )
    assert len(detector.detect(ctx)) == 1


def test_two_different_dropped_fields_each_get_their_own_diagnostic() -> None:
    scale_a = CategoryColorScale(
        field="category",
        slots={"Electronics": 0},
        overrides={},
        unseen_pins={"Growth": "#ff0000"},
    )
    scale_b = CategoryColorScale(
        field="region",
        slots={"North": 0},
        overrides={},
        unseen_pins={"Sud": "#00ff00"},
    )
    ctx = _ctx(
        c1=_resolved("c1", "category", scale_a),
        c2=_resolved("c2", "region", scale_b),
    )
    warnings = detector.detect(ctx)
    assert {w.field for w in warnings} == {"category", "region"}
