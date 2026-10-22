"""Predicate tests for whether the endpoint-label pane will actually fire.

Two computations must agree on this condition:

- Baking ``axis_y.position: auto`` to ``"left"`` or ``"right"`` at compile
  time (``_bake_ay_orient`` in compile/resolve/chart/_axes.py) when the pane
  will collide with a right-side y-axis.
- Deciding whether to actually emit the hconcat/vconcat label pane at
  render time (``EndpointLabelFeature.applies_to`` in
  render/chart/features/endpoint_labels.py).

Both must key off the full firing condition (chart_type ∈ {line, area,
bar} + enabled + not-horizontal-bar + multi-series), not just
``endpoint_labels.visible`` alone — otherwise single-series charts and
horizontal bars that authored ``endpoint_labels.visible: true`` get their
y-axis silently flipped to the left without a pane to justify the flip.

These tests exercise both paths together via the public render pipeline
to confirm the axis orient always matches whether the pane actually
fired.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import BarChart, Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    BarChartStylePatch,
    LineChartStylePatch,
)

_CHART_V2_ADAPTER = TypeAdapter(Chart)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_RS, _CTX = resolve_style_and_context(get_theme_style())


def _single_series_data():
    return [
        {"month": "Jan", "rev": 100},
        {"month": "Feb", "rev": 200},
    ]


def _multi_series_data():
    return [
        {"month": "Jan", "rev": 100, "series": "A"},
        {"month": "Feb", "rev": 200, "series": "A"},
        {"month": "Jan", "rev": 50, "series": "B"},
        {"month": "Feb", "rev": 80, "series": "B"},
    ]


def _spec(chart_type, data, color=None, style=None, stack=None):
    """Build a VL spec via the public render pipeline."""
    reset_config()
    chart_data: dict[str, Any] = {
        "id": "t",
        "type": chart_type,
        "x": "month",
        "y": "rev",
        "color": color,
        "query": SqlQuery(sql="SELECT 1", source="src"),
        "query_name": "q",
        "style": style,
    }
    if stack is not None:
        chart_data["stack"] = stack
    chart = _CHART_V2_ADAPTER.validate_python(chart_data)
    return generate_vega_lite_spec(
        chart, data, width=400, board_style=_RS, chart_style_context=_CTX
    )


def _y_orient(spec):
    """Pull y-axis orient from an unwrapped spec, hconcat pane[0], or a
    layered pane's base sub-layer (layered specs carry no top-level "y" —
    each sub-layer has its own y encoding, and the base's is the one
    _bake_ay_orient resolved).
    """
    chart_pane = spec["hconcat"][0] if "hconcat" in spec else spec
    orient = chart_pane.get("encoding", {}).get("y", {}).get("axis", {}).get("orient")
    if orient is not None:
        return orient
    for layer in chart_pane.get("layer", []):
        orient = layer.get("encoding", {}).get("y", {}).get("axis", {}).get("orient")
        if orient is not None:
            return orient
    return None


# --------------------------------------------------------------------------
# Single-series + endpoint_labels.visible → pane does NOT fire → orient stays right
# --------------------------------------------------------------------------


def test_single_series_line_with_endpoint_labels_keeps_axis_right():
    """Single-series line chart with endpoint_labels.visible stays right.

    No `color:` channel in series mode means the label pane won't render.
    The orient resolver should mirror that — no flip.
    """
    patch = LineChartStylePatch.model_validate({"endpoint_labels": {"visible": True}})
    spec = _spec("line", _single_series_data(), style=patch)
    # No hconcat pane — pane didn't fire.
    assert "hconcat" not in spec, (
        "Endpoint-label pane fired on a single-series line — guard regressed."
    )
    assert _y_orient(spec) == "right", (
        f"Single-series line with endpoint_labels.visible flipped axis_y to "
        f"{_y_orient(spec)!r}; expected 'right' (pane doesn't fire on "
        f"single-series, orient should mirror)."
    )


def test_single_series_area_with_endpoint_labels_keeps_axis_right():
    """Same, area family."""
    patch = AreaChartStylePatch.model_validate({"endpoint_labels": {"visible": True}})
    spec = _spec("area", _single_series_data(), style=patch)
    assert "hconcat" not in spec
    assert _y_orient(spec) == "right"


# --------------------------------------------------------------------------
# Multi-series + endpoint_labels.visible → pane fires → orient flips left
# --------------------------------------------------------------------------


def test_multi_series_line_with_endpoint_labels_flips_axis_left():
    """Multi-series line chart with endpoint_labels.visible flips axis_y left.

    The pane will render to the right of the chart, so the y-axis must
    move to the left to avoid colliding with it.
    """
    patch = LineChartStylePatch.model_validate({"endpoint_labels": {"visible": True}})
    spec = _spec("line", _multi_series_data(), color="series", style=patch)
    # Pane fires — spec wrapped in hconcat with the chart on pane[0].
    assert "hconcat" in spec, (
        "Endpoint-label pane did not fire on a multi-series line — "
        "regression in the firing predicate."
    )
    assert _y_orient(spec) == "left"


def test_multi_series_area_with_endpoint_labels_flips_axis_left():
    """Same, area family."""
    patch = AreaChartStylePatch.model_validate({"endpoint_labels": {"visible": True}})
    spec = _spec("area", _multi_series_data(), color="series", style=patch)
    assert "hconcat" in spec
    assert _y_orient(spec) == "left"


# --------------------------------------------------------------------------
# Horizontal stacked bar emits the top-row rail (vconcat), not the right-side
# hconcat pane. Grouped horizontal (stack: none) does not fire either pane.
# --------------------------------------------------------------------------


def test_horizontal_stacked_multi_series_bar_emits_vconcat_rail():
    """Horizontal stacked + multi-series + endpoint_labels.visible → vconcat rail."""
    patch = BarChartStylePatch.model_validate(
        {
            "endpoint_labels": {"visible": True},
            "orientation": "horizontal",
        }
    )
    # Explicit stack="zero" required — stack=None + color is now grouped (no rail).
    spec = _spec("bar", _multi_series_data(), color="series", style=patch, stack="zero")
    assert "vconcat" in spec, (
        "Horizontal stacked multi-series bar with endpoint_labels.visible must "
        "emit the top-row rail (vconcat); regression in firing predicate."
    )
    assert "hconcat" not in spec, (
        "Horizontal bars never emit the right-side hconcat pane (that's the "
        "vertical-stacked variant)."
    )


def test_horizontal_grouped_by_default_multi_series_bar_no_vconcat_rail():
    """Grouped-by-default horizontal (stack=None + color) bars do not fire the rail.

    stack=None with a data color field now produces side-by-side grouped bars.
    Grouped bars don't form stacks to label, so the rail must not fire.
    """
    patch = BarChartStylePatch.model_validate(
        {
            "endpoint_labels": {"visible": True},
            "orientation": "horizontal",
        }
    )
    spec = _spec("bar", _multi_series_data(), color="series", style=patch)
    assert "vconcat" not in spec, (
        "Horizontal grouped-by-default bar must not fire the top-row rail (vconcat)."
    )
    assert "hconcat" not in spec


def test_horizontal_grouped_explicit_multi_series_bar_no_wrap():
    """Explicit stack: none (overlapping) horizontal bars do not fire either label pane."""
    patch = BarChartStylePatch.model_validate(
        {
            "endpoint_labels": {"visible": True},
            "orientation": "horizontal",
        }
    )
    spec = _spec("bar", _multi_series_data(), color="series", style=patch, stack="none")
    assert "vconcat" not in spec
    assert "hconcat" not in spec


# --------------------------------------------------------------------------
# Multi-series vertical bar with endpoint_labels.visible → pane fires →
# orient flips. The currently-authored column_stacked_review and
# column_stacked_100_review chart-lab specimens live in this mode.
# --------------------------------------------------------------------------


def test_multi_series_vertical_bar_with_endpoint_labels_flips_axis_left():
    """Vertical multi-series bar fires the pane and flips axis_y.

    Explicit stack="zero" — stack=None + color is grouped, which never fires
    the pane (see the horizontal grouped-by-default test above).
    """
    patch = BarChartStylePatch.model_validate(
        {
            "endpoint_labels": {"visible": True},
            "orientation": "vertical",
        }
    )
    spec = _spec("bar", _multi_series_data(), color="series", style=patch, stack="zero")
    assert "hconcat" in spec
    assert _y_orient(spec) == "left"


# --------------------------------------------------------------------------
# Layered charts with no base color-series channel: the rail fires only when
# x and y are both plain scalar columns (layered_endpoint_rail_shape, in
# dbt_charts.core.utils). compile's has_layers term (_bake_ay_orient) and
# render's applies_to() share that one leaf so a list y (folded wide) or an
# absent y — which has no single base endpoint to anchor a label on — can't
# flip the axis for a pane that never renders.
# --------------------------------------------------------------------------


def _layered_chart_spec(y, data, layer_y="cost"):
    patch = LineChartStylePatch.model_validate({"endpoint_labels": {"visible": True}})
    chart_data: dict[str, Any] = {
        "id": "t",
        "type": "line",
        "x": "month",
        "y": y,
        "query": SqlQuery(sql="SELECT 1", source="src"),
        "query_name": "q",
        "style": patch,
        "layers": [{"type": "line", "y": layer_y, "label": "Cost"}],
    }
    chart = _CHART_V2_ADAPTER.validate_python(chart_data)
    reset_config()
    return generate_vega_lite_spec(
        chart, data, width=400, board_style=_RS, chart_style_context=_CTX
    )


def test_layered_line_scalar_xy_fires_pane_and_flips_axis_left():
    """A layered chart with plain scalar x/y fires the rail (base + overlay)."""
    spec = _layered_chart_spec(
        "rev",
        [
            {"month": "Jan", "rev": 100, "cost": 40},
            {"month": "Feb", "rev": 200, "cost": 90},
        ],
    )
    assert "hconcat" in spec, (
        "layered chart with scalar x/y and endpoint_labels.visible did not "
        "fire the rail."
    )
    assert _y_orient(spec) == "left"


def test_layered_line_list_y_with_layers_raises_at_resolve():
    """A folded-wide (list) base y combined with layers: raises CompilationError
    at resolve time — the two encodings are mutually exclusive.

    The old behavior (silently render without layers, no rail) is replaced by
    an early error so authors get a clear message.
    """
    from dbt_charts.core.compile.errors import CompilationError

    with pytest.raises(CompilationError):
        _layered_chart_spec(
            ["rev", "cost2"],
            [
                {"month": "Jan", "rev": 100, "cost2": 40, "cost": 40},
                {"month": "Feb", "rev": 200, "cost2": 90, "cost": 90},
            ],
        )


def test_layered_line_absent_y_does_not_fire_pane_or_flip_axis():
    """No base y at all (layers only) has the same non-firing shape as the
    list-y case above — no base endpoint to anchor a label on.
    """
    spec = _layered_chart_spec(
        None,
        [
            {"month": "Jan", "cost": 40},
            {"month": "Feb", "cost": 90},
        ],
    )
    assert "hconcat" not in spec
    assert _y_orient(spec) == "right"


# --------------------------------------------------------------------------
# Endpoint-labels theme defaults — `line/area/bar.endpoint_labels.visible: auto`
# lands on _base.yaml (the structural root) and is inherited by every theme.
# Line/area label directly everywhere; bar labels directly when stacked and
# falls back to a top legend when grouped (the grouped exclusion lives in
# `_bar_endpoint_labels_for_stack`, resolve-time — not a theme default at all).
# --------------------------------------------------------------------------


def test_clarity_line_endpoint_labels_on_by_default():
    """clarity inherits line endpoint labels on from `_base`."""
    from dbt_charts.core.compile.config import get_theme_style

    compiled = get_theme_style("clarity")
    assert compiled.charts.line.endpoint_labels.visible == "auto", (
        "clarity.charts.line.endpoint_labels.visible should be 'auto', "
        "inherited from _base"
    )


def test_paper_line_endpoint_labels_on_by_default():
    """`paper` extends clarity; inherits `line.endpoint_labels.visible: auto`."""
    from dbt_charts.core.compile.config import get_theme_style

    compiled = get_theme_style("paper")
    assert compiled.charts.line.endpoint_labels.visible == "auto", (
        f"paper.charts.line.endpoint_labels.visible is "
        f"{compiled.charts.line.endpoint_labels.visible!r}; expected 'auto' (inherited from _base)."
    )


def test_clarity_and_paper_bar_endpoint_labels_on_by_default():
    """`clarity` and `paper` inherit bar/area `endpoint_labels.visible: auto` from `_base`."""
    from dbt_charts.core.compile.config import get_theme_style

    for theme_name in ("clarity", "paper"):
        compiled = get_theme_style(theme_name)
        for family in ("bar", "area"):
            visible = getattr(compiled.charts, family).endpoint_labels.visible
            assert visible == "auto", (
                f"{theme_name}.charts.{family}.endpoint_labels.visible is "
                f"{visible!r}; expected 'auto'."
            )


def test_stark_bar_endpoint_labels_on_by_default():
    """stark inherits bar endpoint labels on from `_base`.

    Whether a given bar actually renders them depends on stack mode — see
    ``_bar_endpoint_labels_for_stack`` — this only pins the theme value.
    """
    from dbt_charts.core.compile.config import get_theme_style

    compiled = get_theme_style("stark")
    assert compiled.charts.bar.endpoint_labels.visible == "auto", (
        f"stark.charts.bar.endpoint_labels.visible is "
        f"{compiled.charts.bar.endpoint_labels.visible!r}; expected 'auto'."
    )


def test_single_series_paper_bar_pane_does_not_fire():
    """Single-series bar in paper: pane does NOT fire.

    Theme defaults `bar.endpoint_labels.visible: auto`, but the color-channel
    gate in endpoint_label_pane_will_fire() suppresses the pane when there is
    no multi-series color encoding.
    """
    from dbt_charts.core.compile.config import get_theme_style, reset_config
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    reset_config()
    rs, ctx = resolve_style_and_context(get_theme_style("paper"))
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="rev",
        color=None,
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, _single_series_data(), width=400, board_style=rs, chart_style_context=ctx
    )
    assert "hconcat" not in spec, (
        "Endpoint-label pane fired on a single-series paper bar — "
        "the color-channel gate should suppress it when no color is encoded."
    )


def test_multi_series_grouped_bar_pane_does_not_fire_by_default():
    """Multi-series bar in paper, no explicit stack: pane does NOT fire.

    bar.endpoint_labels.visible is True by theme default, but an unstacked
    chart resolves to grouped (`stack: none`), which never carries endpoint
    labels regardless of the theme setting — see
    ``_bar_endpoint_labels_for_stack``.
    """
    from dbt_charts.core.compile.config import get_theme_style, reset_config
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    reset_config()
    rs, ctx = resolve_style_and_context(get_theme_style("paper"))
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="rev",
        color="series",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(
        chart, _multi_series_data(), width=400, board_style=rs, chart_style_context=ctx
    )
    assert "hconcat" not in spec, (
        "Endpoint-label pane fired on a multi-series paper bar "
        "without explicit authoring — theme default is False."
    )
