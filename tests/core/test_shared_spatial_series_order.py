"""Shared spatial series order across legend, tooltip, and direct labels.

Covers the tooltip-fidelity initiative's Phase 1 task: legend `scale.domain`,
JS tooltip (which reads the legend's rendered order — PR #5850), and direct
(endpoint) labels must all agree on ONE per-family spatial order, derived from
the same authority that decides how the marks are drawn — never a second,
parallel re-implementation of the same policy. Reordering DISPLAY order must
never change a series' assigned color.

Families in scope: stacked bar/column/area, grouped bar, multi-line, overlap
(unstacked) area. Pie/donut and combo are verify-only — already correct
before this task.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
    PieChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    BarChartStylePatch,
    LegendStylePatch,
    LineChartStylePatch,
    PieChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._wide_fields import WIDE_LABEL_FIELD
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.emitters._cartesian import (
    distinct_series_values,
    last_nonnull_value_per_series,
    sorted_series_by_last_value,
    spatial_color_scale,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from ._engine_render import render_pie_svg
from ._svg_render import legend_label_order, render_board_to_svg
from .conftest import chart_pane

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

# Legend entry order still has a contract wherever a legend is drawn; stacked
# bars only draw one when direct labeling is off, so pin the order there.
_BOARD_STYLE_NO_ENDPOINT_LABELS, _BOARD_CTX_NO_ENDPOINT_LABELS = (
    resolve_style_and_context(
        get_theme_style().model_copy(
            update={
                "charts": get_theme_style().charts.model_copy(
                    update={
                        "bar": get_theme_style().charts.bar.model_copy(
                            update={
                                "endpoint_labels": get_theme_style().charts.bar.endpoint_labels.model_copy(
                                    update={"visible": False}
                                )
                            }
                        )
                    }
                )
            }
        )
    )
)


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


# ---------------------------------------------------------------------------
# Shared helper unit tests
# ---------------------------------------------------------------------------


def _color_scale(spec: dict) -> dict:
    """Locate the color encoding's scale whether the chart pane sits at the
    spec root (bar) or is wrapped in ``hconcat[0]`` (area/line, mark
    "layered" — always wrapped, whether or not a label pane is present)."""
    encoding = chart_pane(spec)["encoding"]
    return encoding["color"]["scale"]


def _color_legend(spec: dict) -> dict:
    """Locate the color encoding's legend dict (same wrapping rules as
    ``_color_scale``)."""
    encoding = chart_pane(spec)["encoding"]
    return encoding["color"]["legend"]


def test_distinct_series_values_sorted_alphabetically():
    data = [{"s": "zeta"}, {"s": "alpha"}, {"s": "zeta"}, {"s": None}]
    assert distinct_series_values(data, "s") == ["alpha", "zeta"]


def test_distinct_series_values_never_admits_a_none_label():
    # A null (or an absent column) must not become the string "None" in the
    # scale domain — that paints nothing while advertising a legend entry.
    # Families guarded by validate_color_series reject this data upstream;
    # the unguarded ones rely on this drop.
    data = [{"s": "alpha"}, {"s": None}, {}]
    assert distinct_series_values(data, "s") == ["alpha"]


def test_spatial_color_scale_reorders_domain_but_keeps_original_color():
    # Alphabetical ground truth: A -> palette[0], B -> palette[1], C -> palette[2].
    palette = ("#111", "#222", "#333")
    series = ["A", "B", "C"]
    scale = spatial_color_scale(series, palette, order=["C", "A", "B"])
    assert scale["domain"] == ["C", "A", "B"]
    # Each series keeps ITS OWN original color regardless of new position.
    assert scale["range"] == ["#333", "#111", "#222"]


def test_last_nonnull_value_per_series_ignores_trailing_null():
    # "gappy" has no value at x=3 (the global last x) but its real last value
    # (at x=2) must still be used — a trailing null must never drop a series.
    data = [
        {"x": 1, "y": 5, "s": "gappy"},
        {"x": 2, "y": 9, "s": "gappy"},
        {"x": 3, "y": None, "s": "gappy"},
        {"x": 1, "y": 1, "s": "steady"},
        {"x": 2, "y": 2, "s": "steady"},
        {"x": 3, "y": 3, "s": "steady"},
    ]
    values = last_nonnull_value_per_series(data, "x", "y", "s")
    assert values == {"gappy": 9.0, "steady": 3.0}


def test_sorted_series_by_last_value_orders_descending_with_trailing_null():
    data = [
        {"x": 1, "y": 5, "s": "gappy"},
        {"x": 2, "y": 9, "s": "gappy"},
        {"x": 3, "y": None, "s": "gappy"},
        {"x": 1, "y": 1, "s": "steady"},
        {"x": 2, "y": 2, "s": "steady"},
        {"x": 3, "y": 3, "s": "steady"},
    ]
    # "gappy" ends at 9 (its last real value), "steady" ends at 3 — gappy first.
    order = sorted_series_by_last_value(["gappy", "steady"], data, "x", "y", "s")
    assert order == ["gappy", "steady"]


# ---------------------------------------------------------------------------
# Stacked vertical bar: legend must follow "top-of-stack first", and
# must agree with the SAME order the direct labels already use.
# ---------------------------------------------------------------------------

# Data where alphabetical ("A" < "B") and value ("B" > "A") orderings diverge.
_STACK_VALUE_DATA = [
    {"cat": "X", "series": "A", "val": 2},
    {"cat": "X", "series": "B", "val": 10},
    {"cat": "Y", "series": "A", "val": 2},
    {"cat": "Y", "series": "B", "val": 10},
]


def _bar_chart(**kwargs) -> BarChart:
    return BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="cat",
        y="val",
        color="series",
        **kwargs,
    )


def test_stacked_vertical_bar_legend_domain_is_top_of_stack_first():
    # value order (default): B has larger sum (20) -> baseline; A on top.
    # Top-of-stack-first legend must list A before B (non-alphabetical: A<B
    # alphabetically too here, so use stack_order that diverges — flip data).
    chart = _bar_chart(style=BarChartStylePatch(orientation="vertical", stack="zero"))
    spec = generate_vega_lite_spec(
        chart,
        _STACK_VALUE_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = chart_pane(spec)["encoding"]["color"]["scale"]
    # A is on top of the stack (smaller sum), so it must be listed FIRST.
    assert color_scale["domain"] == ["A", "B"]


def test_stacked_vertical_bar_legend_values_pinned_to_display_order():
    """``scale.domain`` alone does not control the RENDERED legend order —
    Vega's legend re-sorts nominal entries independent of an explicit domain
    override, silently falling back to alphabetical. ``legend.values`` must
    carry the same top-of-stack-first order or the legend (and, via the JS
    runtime's legend-DOM read, the tooltip) renders alphabetically instead."""
    chart = _bar_chart(style=BarChartStylePatch(orientation="vertical", stack="zero"))
    spec = generate_vega_lite_spec(
        chart,
        _STACK_VALUE_DATA,
        board_style=_BOARD_STYLE_NO_ENDPOINT_LABELS,
        chart_style_context=_BOARD_CTX_NO_ENDPOINT_LABELS,
    )
    assert chart_pane(spec)["encoding"]["color"]["legend"]["values"] == ["A", "B"]


@pytest.mark.parametrize(
    ("orientation", "stack"),
    [
        ("vertical", "zero"),
        ("vertical", "normalize"),
        ("vertical", "center"),
        ("horizontal", "zero"),
        ("horizontal", "normalize"),
    ],
)
def test_stacked_bar_baseline_series_receives_first_palette_slot(
    orientation: str,
    stack: str,
) -> None:
    """Stack rank and palette rank share the same baseline-first authority."""
    chart = _bar_chart(style=BarChartStylePatch(orientation=orientation, stack=stack))
    resolved = resolve(chart, _STACK_VALUE_DATA, chart_style_context=_BOARD_CTX)
    spec = generate_vega_lite_spec(
        chart,
        _STACK_VALUE_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = chart_pane(spec)["encoding"]["color"]["scale"]
    color_by_series = dict(
        zip(color_scale["domain"], color_scale["range"], strict=True)
    )

    assert color_by_series["B"] == resolved.palette[0]
    assert color_by_series["A"] == resolved.palette[1]


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_one_color_per_category_bar_keeps_alphabetical_palette_slots(
    orientation: str,
) -> None:
    data = [
        {"cat": "X", "series": "Z", "val": 10},
        {"cat": "Y", "series": "A", "val": 1},
    ]
    chart = _bar_chart(style=BarChartStylePatch(orientation=orientation, stack="zero"))
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    spec = generate_vega_lite_spec(
        chart,
        data,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = chart_pane(spec)["encoding"]["color"]["scale"]
    color_by_series = dict(
        zip(color_scale["domain"], color_scale["range"], strict=True)
    )

    assert color_by_series == {
        "A": resolved.palette[0],
        "Z": resolved.palette[1],
    }


def test_stacked_vertical_bar_legend_agrees_with_direct_labels():
    """Legend order and endpoint-label vertical position must agree: the
    series listed FIRST in the legend (top-of-stack) must have the HIGHEST
    label y (topmost on the right-hand label rail)."""
    chart = _bar_chart(
        style=BarChartStylePatch(
            orientation="vertical", stack="zero", endpoint_labels={"visible": True}
        )
    )
    spec = generate_vega_lite_spec(
        chart,
        _STACK_VALUE_DATA,
        width=400,
        height=300,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = _color_scale(spec)
    label_pane = spec["hconcat"][1]
    label_y = {row["series"]: row["__y"] for row in label_pane["data"]["values"]}
    first_in_legend = color_scale["domain"][0]
    second_in_legend = color_scale["domain"][1]
    assert label_y[first_in_legend] > label_y[second_in_legend], (
        "the series listed first in the legend (top-of-stack) must have the "
        "highest endpoint-label y position"
    )


def test_horizontal_stacked_bar_legend_domain_is_left_first():
    chart = _bar_chart(style=BarChartStylePatch(orientation="horizontal", stack="zero"))
    spec = generate_vega_lite_spec(
        chart,
        _STACK_VALUE_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = chart_pane(spec)["encoding"]["color"]["scale"]
    # Left-first = baseline-first = largest sum first for horizontal: B, then A.
    assert color_scale["domain"] == ["B", "A"]


def test_horizontal_stacked_bar_legend_values_pinned_to_display_order():
    chart = _bar_chart(style=BarChartStylePatch(orientation="horizontal", stack="zero"))
    spec = generate_vega_lite_spec(
        chart,
        _STACK_VALUE_DATA,
        board_style=_BOARD_STYLE_NO_ENDPOINT_LABELS,
        chart_style_context=_BOARD_CTX_NO_ENDPOINT_LABELS,
    )
    assert chart_pane(spec)["encoding"]["color"]["legend"]["values"] == ["B", "A"]


def test_stack_order_alphabetical_still_reorders_legend_alphabetically():
    # A degenerate case: alphabetical stack_order legend domain equals plain
    # alphabetical order (reversed for top-of-stack-first display).
    chart = _bar_chart(
        style=BarChartStylePatch(
            orientation="vertical", stack="zero", stack_order="alphabetical"
        )
    )
    spec = generate_vega_lite_spec(
        chart,
        _STACK_VALUE_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = chart_pane(spec)["encoding"]["color"]["scale"]
    # Alphabetical stack_order: A at baseline, B on top -> B listed first.
    assert color_scale["domain"] == ["B", "A"]


# Data where color is a NUMERIC (year) or BOOLEAN field. Vega-Lite infers
# both as "quantitative" (bool is an int subclass in Python, and
# infer_vega_type_from_data treats all-int/float samples as quantitative) —
# a genuinely discrete domain reorder is not meaningful for a continuous
# scale, and Python's `str()` does not match Vega's `toString()` for these
# types (`str(True) == "True"` vs Vega `toString(true) == "true"`;
# `str(2021.0) == "2021.0"` vs Vega `toString(2021.0) == "2021"`), so baking
# a string-keyed order into the mark's calculate transform would silently
# stop matching every row. Skip the override entirely for non-nominal color
# fields instead of trying to replicate Vega's exact string coercion.
_QUANTITATIVE_SERIES_DATA = [
    {"cat": "X", "yr": 2020, "val": 2},
    {"cat": "X", "yr": 2021, "val": 10},
    {"cat": "Y", "yr": 2020, "val": 2},
    {"cat": "Y", "yr": 2021, "val": 10},
]

_BOOLEAN_SERIES_DATA = [
    {"cat": "X", "is_paid": True, "val": 2},
    {"cat": "X", "is_paid": False, "val": 10},
    {"cat": "Y", "is_paid": True, "val": 2},
    {"cat": "Y", "is_paid": False, "val": 10},
]


def test_stacked_bar_numeric_series_field_gets_no_reorder():
    """A stacked bar colored by a numeric (quantitative) field is left native.

    Regression: reordering a quantitative color's domain via a string-keyed
    calculate transform silently breaks — Python's `str()` and Vega's
    `toString()` disagree for non-int-like values, so every row would fall
    through to the transform's -1 default while the legend still showed the
    (wrong) reordered domain. Skipping the override for non-nominal color
    avoids that class of bug entirely.
    """
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="cat",
        y="val",
        color="yr",
        style=BarChartStylePatch(orientation="vertical", stack="zero"),
    )
    spec = generate_vega_lite_spec(
        chart,
        _QUANTITATIVE_SERIES_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert chart_pane(spec)["encoding"]["color"]["type"] == "quantitative"
    assert "scale" not in chart_pane(spec)["encoding"]["color"]
    transforms = spec.get("transform", [])
    assert not any("calculate" in t for t in transforms)


def test_stacked_bar_boolean_series_field_gets_no_reorder():
    """A stacked bar colored by a boolean field is also left native.

    `infer_vega_type_from_data` treats bool as quantitative (bool is an int
    subclass), so this hits the same skip path as the numeric case.
    """
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="cat",
        y="val",
        color="is_paid",
        style=BarChartStylePatch(orientation="vertical", stack="zero"),
    )
    spec = generate_vega_lite_spec(
        chart,
        _BOOLEAN_SERIES_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert chart_pane(spec)["encoding"]["color"]["type"] == "quantitative"
    assert "scale" not in chart_pane(spec)["encoding"]["color"]
    transforms = spec.get("transform", [])
    assert not any("calculate" in t for t in transforms)


def test_stacked_bar_empty_data_emits_no_malformed_order_expression():
    """A stacked bar with zero rows (e.g. a filtered-empty dashboard) must
    not emit a broken Vega expression.

    Regression: with an empty series set, baking `_series_order_expression`
    unconditionally produced `" : -1"` — a ternary with no test/consequent,
    a Vega syntax error. `area.py`/`line.py` already guard this; `bar.py`
    must too.
    """
    chart = _bar_chart(style=BarChartStylePatch(orientation="vertical", stack="zero"))
    spec = generate_vega_lite_spec(
        chart, [], board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    transforms = spec.get("transform", [])
    assert not any(
        t.get("calculate", "").strip().startswith(":") for t in transforms
    ), f"malformed calculate expression from an empty series set: {transforms!r}"
    assert "order" not in chart_pane(spec)["encoding"]


# ---------------------------------------------------------------------------
# Grouped bar: verify-only. No stack_order concept — the legend must use
# whatever order the marks' own xOffset uses (today, VL's shared default),
# never a separately-computed order.
# ---------------------------------------------------------------------------


def test_grouped_bar_legend_has_no_domain_override():
    chart = _bar_chart(style=BarChartStylePatch(orientation="vertical", stack="none"))
    spec = generate_vega_lite_spec(
        chart,
        _STACK_VALUE_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_enc = chart_pane(spec)["encoding"]["color"]
    x_offset_enc = chart_pane(spec)["encoding"]["xOffset"]
    # Neither channel authors an explicit domain/sort override — both read
    # the SAME implicit Vega-Lite default, so they can never diverge. Grouped
    # bar's authored-color path is unaffected by the wide (y: [a, b])
    # symmetry fix: wide's own domain/legend pin lives entirely in
    # _wide.py/fold_wide_measures, a separate code path an authored-color
    # chart never reaches — pinning this one too would be scope creep onto
    # every grouped bar in the repo, not just multi-y ones.
    assert "scale" not in color_enc
    assert "sort" not in x_offset_enc


# ---------------------------------------------------------------------------
# Stacked area: legend and palette follow the same global-total stack order.
# ---------------------------------------------------------------------------


def _area_chart(*, x: str = "cat", **kwargs) -> AreaChart:
    return AreaChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="area",
        x=x,
        y="val",
        color="series",
        **kwargs,
    )


_AREA_STACK_DATA = [
    {"cat": "2024-01-01", "series": "alpha", "val": 2},
    {"cat": "2024-01-01", "series": "zeta", "val": 10},
    {"cat": "2024-02-01", "series": "alpha", "val": 3},
    {"cat": "2024-02-01", "series": "zeta", "val": 11},
]


def test_stacked_area_legend_matches_global_total_order():
    chart = _area_chart(style=AreaChartStylePatch(stack="zero"))
    spec = generate_vega_lite_spec(
        chart,
        _AREA_STACK_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = _color_scale(spec)
    # Zeta has the largest global total and sits at the baseline, so alpha is
    # listed first in the top-of-stack-first legend.
    assert color_scale["domain"] == ["alpha", "zeta"]


def test_stacked_area_legend_values_pinned_to_display_order():
    # Area defaults endpoint labels on (which force the legend off) — disable
    # them and force the legend on to exercise the same Vega legend-resort
    # gap the stacked-bar case hits.
    chart = _area_chart(
        style=AreaChartStylePatch(
            stack="zero",
            legend=LegendStylePatch(visible=True),
            endpoint_labels={"visible": False},
        )
    )
    spec = generate_vega_lite_spec(
        chart,
        _AREA_STACK_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert _color_legend(spec)["values"] == ["alpha", "zeta"]


@pytest.mark.parametrize("stack", ["zero", "normalize", "center"])
def test_stacked_area_baseline_series_receives_first_palette_slot(stack: str):
    chart = _area_chart(style=AreaChartStylePatch(stack=stack))
    resolved = resolve(chart, _AREA_STACK_DATA, chart_style_context=_BOARD_CTX)
    palette = resolved.palette
    spec = generate_vega_lite_spec(
        chart,
        _AREA_STACK_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = _color_scale(spec)
    expected_color = {"zeta": palette[0], "alpha": palette[1]}
    for series, color in zip(color_scale["domain"], color_scale["range"], strict=True):
        assert color == expected_color[series]
    assert "order" in chart_pane(spec)["encoding"]


def test_wide_stacked_area_baseline_measure_receives_first_palette_slot():
    data = [
        {"cat": "2024-01-01", "alpha": 2, "zeta": 10},
        {"cat": "2024-02-01", "alpha": 3, "zeta": 11},
    ]
    chart = AreaChart(
        id="wide_area",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="area",
        x="cat",
        y=["alpha", "zeta"],
        style=AreaChartStylePatch(stack="zero"),
    )
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    spec = generate_vega_lite_spec(
        chart,
        data,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = _color_scale(spec)
    color_by_series = dict(
        zip(color_scale["domain"], color_scale["range"], strict=True)
    )

    assert color_by_series == {
        "Zeta": resolved.palette[0],
        "Alpha": resolved.palette[1],
    }
    assert set(spec["hconcat"][1]["encoding"]["color"]["scale"]["domain"]) == {
        "Alpha",
        "Zeta",
    }
    assert {row[WIDE_LABEL_FIELD] for row in spec["hconcat"][1]["data"]["values"]} == {
        "Alpha",
        "Zeta",
    }
    assert "order" in chart_pane(spec)["encoding"]


@pytest.mark.parametrize("stack", ["zero", "normalize", "center"])
def test_wide_stacked_area_keeps_an_all_null_declared_measure(stack: str) -> None:
    data = [
        {"cat": "2024-01-01", "alpha": None, "zeta": 10},
        {"cat": "2024-02-01", "alpha": None, "zeta": 11},
    ]
    chart = AreaChart(
        id="wide_area",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="area",
        x="cat",
        y=["alpha", "zeta"],
        style=AreaChartStylePatch(stack=stack),
    )
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)

    spec = generate_vega_lite_spec(
        chart,
        data,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )

    color_scale = _color_scale(spec)
    color_by_series = dict(
        zip(color_scale["domain"], color_scale["range"], strict=True)
    )
    assert color_by_series == {
        "Zeta": resolved.palette[0],
        "Alpha": resolved.palette[1],
    }
    assert {row[WIDE_LABEL_FIELD] for row in spec["hconcat"][1]["data"]["values"]} == {
        "Alpha",
        "Zeta",
    }


def test_long_stacked_area_series_order_calculate_is_shared_by_all_layers() -> None:
    chart = _area_chart(style=AreaChartStylePatch(stack="zero"))

    spec = generate_vega_lite_spec(
        chart,
        _AREA_STACK_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )

    pane = chart_pane(spec)
    assert any(
        transform.get("as") == "__df_series_order"
        for transform in pane.get("transform", [])
    )
    assert all(
        not any(
            transform.get("as") == "__df_series_order"
            for transform in layer.get("transform", [])
        )
        for layer in pane["layer"]
    )


@pytest.mark.parametrize("stack", ["zero", "normalize", "center"])
def test_layered_stacked_area_hoists_one_shared_series_order_calculate(
    stack: str,
) -> None:
    data = [{**row, "target": 20} for row in _AREA_STACK_DATA]
    chart = _area_chart(
        style=AreaChartStylePatch(stack=stack),
        layers=[LineLayer(type="line", y="target", label="Target")],
    )

    spec = generate_vega_lite_spec(
        chart,
        data,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )

    pane = chart_pane(spec)
    shared_order_calculates = [
        transform
        for transform in pane.get("transform", [])
        if transform.get("as") == "__df_series_order"
    ]
    assert len(shared_order_calculates) == 1
    assert all(
        not any(
            transform.get("as") == "__df_series_order"
            for transform in layer.get("transform", [])
        )
        for layer in pane["layer"]
    )


# ---------------------------------------------------------------------------
# Overlap (unstacked) area + multi-line: stable order by each series'
# most-recent non-null value, agreeing with endpoint labels.
# ---------------------------------------------------------------------------

_DIVERGING_ENDPOINT_DATA = [
    {"month": "2024-01-01", "series": "rising", "val": 1},
    {"month": "2024-02-01", "series": "rising", "val": 5},
    {"month": "2024-03-01", "series": "rising", "val": 12},
    {"month": "2024-01-01", "series": "falling", "val": 20},
    {"month": "2024-02-01", "series": "falling", "val": 15},
    {"month": "2024-03-01", "series": "falling", "val": 8},
]


def test_overlap_area_legend_matches_last_value_order():
    chart = _area_chart(x="month", style=AreaChartStylePatch(stack="none"))
    spec = generate_vega_lite_spec(
        chart,
        _DIVERGING_ENDPOINT_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = _color_scale(spec)
    # "rising" ends higher (12) than "falling" (8) -> rising listed first.
    assert color_scale["domain"] == ["rising", "falling"]


def test_overlap_area_legend_values_pinned_to_display_order():
    chart = _area_chart(
        x="month",
        style=AreaChartStylePatch(
            stack="none",
            legend=LegendStylePatch(visible=True),
            endpoint_labels={"visible": False},
        ),
    )
    spec = generate_vega_lite_spec(
        chart,
        _DIVERGING_ENDPOINT_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert _color_legend(spec)["values"] == ["rising", "falling"]


def test_multiline_legend_matches_last_value_order_and_agrees_with_labels():
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="val",
        color="series",
        style={"endpoint_labels": {"visible": True}},
    )
    spec = generate_vega_lite_spec(
        chart,
        _DIVERGING_ENDPOINT_DATA,
        width=400,
        height=300,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = _color_scale(spec)
    assert color_scale["domain"] == ["rising", "falling"]
    label_pane = spec["hconcat"][1]
    label_y = {row["series"]: row["__y"] for row in label_pane["data"]["values"]}
    first_in_legend = color_scale["domain"][0]
    second_in_legend = color_scale["domain"][1]
    assert label_y[first_in_legend] > label_y[second_in_legend], (
        "legend-first series must also be the topmost endpoint label"
    )


def test_multiline_legend_values_pinned_to_display_order():
    """Same Vega legend-resort gap as the stacked-bar case: line's endpoint
    order was already wired into ``scale.domain``, but without
    ``legend.values`` the rendered legend (and the JS tooltip, which reads
    the legend's DOM order) falls back to alphabetical."""
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="val",
        color="series",
        style=LineChartStylePatch(
            legend=LegendStylePatch(visible=True),
            endpoint_labels={"visible": False},
        ),
    )
    spec = generate_vega_lite_spec(
        chart,
        _DIVERGING_ENDPOINT_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    assert _color_legend(spec)["values"] == ["rising", "falling"]


def test_multiline_endpoint_label_survives_series_ending_early():
    """A series with no row at the global-last x still gets an endpoint label.

    Feature-level regression for the `_resolve_endpoint_label_positions`
    rewrite: "ending" has no row at 2024-03 (the last x across all series),
    so the OLD "value at the single global-last-x row" scan would have
    silently dropped its label. It must still appear, anchored at its own
    last real value.
    """
    data = [
        {"month": "2024-01-01", "series": "ending", "val": 50},
        {"month": "2024-02-01", "series": "ending", "val": 55},
        {"month": "2024-01-01", "series": "steady", "val": 10},
        {"month": "2024-02-01", "series": "steady", "val": 12},
        {"month": "2024-03-01", "series": "steady", "val": 14},
    ]
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="val",
        color="series",
        style={"endpoint_labels": {"visible": True}},
    )
    spec = generate_vega_lite_spec(
        chart,
        data,
        width=400,
        height=300,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    label_pane = spec["hconcat"][1]
    labeled_series = {row["series"] for row in label_pane["data"]["values"]}
    assert "ending" in labeled_series, (
        "a series ending before the last x lost its label"
    )
    assert "steady" in labeled_series


def test_multiline_legend_range_preserves_original_color():
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="val",
        color="series",
    )
    resolved = resolve(chart, _DIVERGING_ENDPOINT_DATA, chart_style_context=_BOARD_CTX)
    palette = resolved.palette
    spec = generate_vega_lite_spec(
        chart,
        _DIVERGING_ENDPOINT_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_scale = _color_scale(spec)
    expected_color = {"falling": palette[0], "rising": palette[1]}
    for series, color in zip(color_scale["domain"], color_scale["range"], strict=True):
        assert color == expected_color[series]


_NUMERIC_LINE_DATA = [
    {"month": "2024-01-01", "cohort": 2020, "val": 5},
    {"month": "2024-02-01", "cohort": 2020, "val": 9},
    {"month": "2024-01-01", "cohort": 2021, "val": 1},
    {"month": "2024-02-01", "cohort": 2021, "val": 3},
]


def test_multiline_numeric_series_field_gets_no_reorder():
    """A multi-line chart colored by a numeric field is left native — same
    str()/toString() mismatch risk as the stacked-bar case."""
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="val",
        color="cohort",
    )
    spec = generate_vega_lite_spec(
        chart,
        _NUMERIC_LINE_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_enc = spec["hconcat"][0]["encoding"]["color"]
    assert color_enc["type"] == "quantitative"
    assert "scale" not in color_enc


def test_overlap_area_numeric_series_field_gets_no_reorder():
    chart = AreaChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="area",
        x="month",
        y="val",
        color="cohort",
        style=AreaChartStylePatch(stack="none"),
    )
    spec = generate_vega_lite_spec(
        chart,
        _NUMERIC_LINE_DATA,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )
    color_enc = spec["hconcat"][0]["encoding"]["color"]
    assert color_enc["type"] == "quantitative"
    assert "scale" not in color_enc


# ---------------------------------------------------------------------------
# Real-render regression: a VL-JSON assertion on scale.domain/legend.values
# alone would NOT have caught the original bug — Vega's own SVG legend
# renderer re-sorts nominal entries independent of an explicit scale.domain
# override, silently falling back to alphabetical, unless legend.values also
# pins the render order. These render an actual board end-to-end (through
# vl-convert) and read the legend's real DOM text order.
# ---------------------------------------------------------------------------


def test_stacked_bar_real_render_legend_reads_top_of_stack_first():
    svg = render_board_to_svg(
        """
title: Fix1 stacked bar render check
queries:
  q1:
    type: values
    rows:
      - {month: Jan, segment: Enterprise, revenue: 520}
      - {month: Jan, segment: Mid-Market, revenue: 300}
      - {month: Jan, segment: SMB, revenue: 180}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
    color: segment
    style:
      orientation: vertical
      stack: zero
      endpoint_labels:
        visible: false
rows:
  - c1
"""
    )
    # SMB is the smallest segment (180) -> top of the stack -> must render
    # first in the legend, ahead of the larger Mid-Market (300) and
    # Enterprise (520, at the baseline). endpoint_labels: false — a stacked
    # bar otherwise names its series directly and draws no legend at all.
    assert legend_label_order(svg) == ["SMB", "Mid-Market", "Enterprise"]


def test_multiline_real_render_legend_reads_endpoint_order():
    svg = render_board_to_svg(
        """
title: Fix2 multi-line render check
queries:
  q1:
    type: values
    rows:
      - {month: Jan, region: South, revenue: 150}
      - {month: Jan, region: North, revenue: 90}
      - {month: Jan, region: West, revenue: 60}
      - {month: Feb, region: South, revenue: 180}
      - {month: Feb, region: North, revenue: 110}
      - {month: Feb, region: West, revenue: 80}
      - {month: Mar, region: South, revenue: 210}
      - {month: Mar, region: North, revenue: 130}
      - {month: Mar, region: West, revenue: 105}
charts:
  c1:
    query: q1
    type: line
    x: month
    y: revenue
    color: region
    style:
      legend:
        visible: true
      endpoint_labels:
        visible: false
rows:
  - c1
"""
    )
    # Mar (last x) ends South(210) > North(130) > West(105) -> that's the
    # stable endpoint order the legend must read, top to bottom.
    assert legend_label_order(svg) == ["South", "North", "West"]


# ---------------------------------------------------------------------------
# Pie/donut: verify-only. enc["sort"] = False + arc order = the row-index
# field mean the legend and the arc paint order already share ONE authority
# (the data's own row order) — nothing to fix here.
# ---------------------------------------------------------------------------


def test_pie_legend_and_arc_paint_order_share_one_authority():
    """The legend lists and the wedges paint in the data's own row order."""
    chart = PieChart(
        id="t",
        query_name="q",
        type="pie",
        theta="val",
        color="cat",
        style=PieChartStylePatch.model_validate({"legend": {"visible": True}}),
    )
    data = [
        {"cat": "zeta", "val": 10},
        {"cat": "alpha", "val": 30},
    ]
    svg = render_pie_svg(chart, data)
    assert legend_label_order(svg) == ["zeta", "alpha"]
    wedge_headers = re.findall(
        r'aria-label="\u2064([^;]+);[^"]*"[^>]*aria-roledescription="arc mark"', svg
    )
    assert wedge_headers == ["zeta", "alpha"]


# ---------------------------------------------------------------------------
# Combo (layered): verify-only. The base series' OWN legend order must still
# follow its family convention inside a combo — this task does not touch the
# overlay-layer legend/tooltip merge (a separate task's scope).
# ---------------------------------------------------------------------------


def test_combo_base_series_legend_order_unaffected_by_overlay():
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    line_layer = LineLayer(type="line", y="target")
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        color="series",
        style=BarChartStylePatch(orientation="vertical", stack="zero"),
        layers=[line_layer],
    )
    data = [
        {"month": "Jan", "series": "A", "revenue": 2, "target": 15},
        {"month": "Jan", "series": "B", "revenue": 10, "target": 15},
        {"month": "Feb", "series": "A", "revenue": 2, "target": 16},
        {"month": "Feb", "series": "B", "revenue": 10, "target": 16},
    ]
    spec = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    layers = chart_pane(spec)["layer"]
    # Paint order preserved: base bar first, overlay (line/rule) layer last.
    assert layers[0]["mark"]["type"] == "bar"
    assert layers[-1]["mark"]["type"] in ("rule", "line")
    # The base series still follow their family convention (top-of-stack-first:
    # B has the larger sum, so A — on top — is listed first), and the overlay
    # follows them in the now-complete shared scale.
    base_color_scale = layers[0]["encoding"]["color"]["scale"]
    assert base_color_scale["domain"] == ["A", "B", "Target"]


def test_combo_legend_values_includes_overlay_label():
    """Regression: apply_legend_entry_order's explicit legend.values (base
    tiers only) made Vega drop the merged overlay entry entirely -- before
    that fix the combo legend showed the tiers AND the overlay ("target").
    The pinned values must include the overlay's own label too, appended
    after the base tiers, so the merged legend still shows every entry."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    line_layer = LineLayer(type="line", y="target")
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        color="series",
        style=BarChartStylePatch(orientation="vertical", stack="zero"),
        layers=[line_layer],
    )
    data = [
        {"month": "Jan", "series": "A", "revenue": 2, "target": 15},
        {"month": "Jan", "series": "B", "revenue": 10, "target": 15},
        {"month": "Feb", "series": "A", "revenue": 2, "target": 16},
        {"month": "Feb", "series": "B", "revenue": 10, "target": 16},
    ]
    spec = generate_vega_lite_spec(
        chart,
        data,
        board_style=_BOARD_STYLE_NO_ENDPOINT_LABELS,
        chart_style_context=_BOARD_CTX_NO_ENDPOINT_LABELS,
    )
    base_legend = chart_pane(spec)["layer"][0]["encoding"]["color"]["legend"]
    assert base_legend["values"] == ["A", "B", "Target"]
