"""x-axis tick stubs render only when the vertical gridlines can't reach the labels.

Rule (RJ, 2026-08-25): a tick stub bridges the gap between where a
label-gridline visually ends and the labels themselves.

    visible = True   if x-gridlines are hidden        (nothing else marks position)
            = True   if the lowest y-gridline lands on the plot's bottom edge (it caps)
            = False  otherwise                        (gridlines reach the labels)

``style.axis_x.ticks.visible`` defaults to ``"auto"`` in the base theme and
resolves to a concrete bool per chart from the y-axis's own baked geometry
(``build_cartesian_axes`` in ``compile/resolve/chart/_plan.py``). An authored
``true``/``false`` always wins over the auto rule. Bar and histogram pin
``ticks.visible: false`` explicitly (out of scope: they always zero-anchor),
so the auto rule never runs for them.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from .conftest import theme_without_tick_count

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

# ratio = min/max <= 0.25 -> smart-zero heuristic extends to zero (zero-anchored).
_ZERO_ANCHORED_DATA = [10.0, 20.0, 100.0]
# ratio = min/max ~ 0.98 -> smart-zero heuristic keeps the domain zoomed.
_ZOOMED_DATA = [9800.0, 9850.0, 9900.0, 10000.0]
# Same ratio/shape as _ZOOMED_DATA, six orders of magnitude smaller (a ppm-scale
# measure column) -- regression data for the absolute-epsilon bug: an absolute
# epsilon that size dwarfs the whole domain span here (~6e-8).
_PPM_ZOOMED_DATA = [2.30e-6, 2.32e-6, 2.34e-6, 2.36e-6]


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _chart(
    chart_type: str, style: Any = None, multiples: dict[str, Any] | None = None
) -> Chart:
    return TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "x",
            "y": "y",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": style,
            "multiples": multiples,
        }
    )


def _x_ticks_visible(
    chart_type: str,
    y_values: list[float],
    style: Any = None,
    *,
    theme_name: str | None = None,
    compiled_style: Any = None,
    multiples: dict[str, Any] | None = None,
    panel_field: str | None = None,
) -> bool:
    data = [{"x": float(i), "y": v} for i, v in enumerate(y_values)]
    if panel_field is not None:
        for i, row in enumerate(data):
            row[panel_field] = "a" if i % 2 == 0 else "b"
    chart = _chart(chart_type, style, multiples)
    board_style, chart_style_context = (
        (_BOARD_STYLE, _BOARD_CTX)
        if theme_name is None
        else resolve_style_and_context(
            get_theme_style(theme_name) if compiled_style is None else compiled_style
        )
    )
    spec = generate_vega_lite_spec(
        chart,
        data,
        width=400,
        board_style=board_style,
        chart_style_context=chart_style_context,
    )
    # A faceted (small-multiples) spec nests the per-panel encoding under
    # "spec" -- the top-level object carries only "facet".
    encoding = spec["spec"]["encoding"] if "facet" in spec else spec["encoding"]
    visible = encoding["x"]["axis"]["ticks"]
    assert isinstance(visible, bool)
    return visible


def test_scatter_zoomed_y_domain_hides_x_ticks() -> None:
    """Gridlines already reach the labels unimpeded -- the stub is pure noise."""
    assert _x_ticks_visible("scatter", _ZOOMED_DATA) is False


def test_line_zero_anchored_y_domain_keeps_x_ticks() -> None:
    """The zero gridline caps the vertical gridlines short of the labels."""
    assert _x_ticks_visible("line", _ZERO_ANCHORED_DATA) is True


def test_hidden_x_grid_keeps_x_ticks_even_when_zoomed() -> None:
    """No gridlines at all -- nothing else marks position, so the stub stands in."""
    style = {"axis_x": {"grid": {"visible": False}}}
    assert _x_ticks_visible("scatter", _ZOOMED_DATA, style) is True


def test_explicit_ticks_visible_true_wins_on_zoomed_scatter() -> None:
    """An authored true always beats the auto rule."""
    style = {"axis_x": {"ticks": {"visible": True}}}
    assert _x_ticks_visible("scatter", _ZOOMED_DATA, style) is True


def test_explicit_ticks_visible_false_wins_on_zero_anchored_line() -> None:
    """An authored false always beats the auto rule."""
    style = {"axis_x": {"ticks": {"visible": False}}}
    assert _x_ticks_visible("line", _ZERO_ANCHORED_DATA, style) is False


def test_headroom_zero_scatter_keeps_x_ticks() -> None:
    """headroom: 0 still bakes a real ladder (nice_tick_values over the exact
    data extent) -- it just leaves ``domain_min`` unbaked, since Vega-Lite
    auto-fits that edge. Rendering this exact shape puts the lowest y-gridline
    at y=255.5 and the plot bottom at y=255.5 too: VL's own auto-fit lands on
    the same "nice" floor our ladder already computed, so it caps.
    """
    style = {"axis_y": {"scale": {"headroom": 0}}}
    assert _x_ticks_visible("scatter", _ZOOMED_DATA, style) is True


def test_log_y_axis_keeps_x_ticks() -> None:
    """A log measure axis never bakes a tick ladder (``_resolve_cartesian_ticks``
    rejects it unconditionally) and Vega-Lite draws gridlines anyway -- no
    ladder means no floor to compare, so the undecidable case shows the stub
    rather than silently answering "gridlines reach"."""
    style = {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
    assert _x_ticks_visible("scatter", _ZOOMED_DATA, style) is True


def test_unset_tick_count_keeps_x_ticks() -> None:
    """With no ``axis_quantitative.ticks.count``, ``_resolve_cartesian_ticks``
    bakes no ladder for any cartesian chart -- same undecidable case as log,
    reached through a different door."""
    assert (
        _x_ticks_visible(
            "scatter",
            _ZOOMED_DATA,
            theme_name="stark",
            compiled_style=theme_without_tick_count("stark"),
        )
        is True
    )


def test_independent_scale_small_multiples_keeps_x_ticks() -> None:
    """``scale: independent`` bakes no shared ladder by design (each panel
    gets its own auto-fit scale) -- undecidable at the shared-axis level, so
    the stub shows rather than assuming every panel's gridlines reach."""
    assert (
        _x_ticks_visible(
            "scatter",
            _ZOOMED_DATA,
            multiples={"columns": "panel", "scale": "independent"},
            panel_field="panel",
        )
        is True
    )


def test_authored_zero_anchor_ladder_hides_x_ticks() -> None:
    """An authored ``scale.values`` ladder may never source a zero-anchored
    scale's domain floor (``zero_anchor_domain_floor``) -- ``y_zero_scale``
    falls back to the literal ``domainMin: 0`` instead. The lowest authored
    rung (25) sits well above that real floor (0), so the gridline doesn't
    cap: rendering this exact shape puts the lowest y-gridline 62px above the
    plot's bottom edge."""
    style = {"axis_y": {"scale": {"values": [25, 50, 75, 100]}}}
    assert _x_ticks_visible("scatter", _ZERO_ANCHORED_DATA, style) is False


def test_authored_domain_matching_data_floor_keeps_x_ticks() -> None:
    """An authored ``scale.continuous.domain`` pins the floor outright (its
    own low bound, taken literally) instead of leaving it to headroom/zero
    logic. Authoring the domain to the tight data extent turns the ordinarily-
    hidden zoomed-scatter case (see ``test_scatter_zoomed_y_domain_hides_x_ticks``)
    into a capping one: ``nice_tick_values(9800, 10000, 6)`` starts exactly at
    9800, the authored floor itself."""
    style = {"axis_y": {"scale": {"continuous": {"domain": [9800, 10000]}}}}
    assert _x_ticks_visible("scatter", _ZOOMED_DATA, style) is True


def test_ppm_scale_domain_edge_hides_x_ticks() -> None:
    """Regression: an absolute ``abs_tol`` epsilon answers a geometric
    question in the y column's own units. At ppm scale
    (domain span ~6e-8), 1e-6 dwarfs the whole span and always reads "close",
    wrongly capping. domain_min - ticks[0] here is ~1.5e-8 -- genuinely not
    the same point -- and the real render puts the lowest gridline 18px above
    the plot's bottom edge, clipped outside the domain entirely."""
    assert _x_ticks_visible("scatter", _PPM_ZOOMED_DATA) is False


def test_multi_series_area_with_endpoint_labels_keeps_x_ticks() -> None:
    """Regression: a multi-series (``color``) area whose endpoint-label rail
    creates a shared-scale hconcat (main pane + label pane) with the label
    pane's own y-encoding carrying no scale hint at all. Vega-Lite's
    ``resolve.scale.y: shared`` then silently drops the main pane's baked
    ``domainMin``/``domainMax`` and falls back to its own "nice" auto-fit of
    the raw data -- which, for this data, lands exactly on the tick
    ladder's own extremes (10000..50000), landing the lowest visible
    gridline exactly on the plot's bottom edge. Confirmed by rendering this
    exact shape through ``vl_convert`` directly and reading the emitted
    gridline pixel positions (0, 54, 108, 161, 215 -- evenly spaced across
    the full plot height, not the [14920, 45080] the compile-time bake
    predicts).
    """
    data = [
        {"month": "2025-01-01", "segment": "Direct", "revenue": 24000.0},
        {"month": "2025-01-01", "segment": "Partner", "revenue": 17000.0},
        {"month": "2025-02-01", "segment": "Direct", "revenue": 31000.0},
        {"month": "2025-02-01", "segment": "Partner", "revenue": 21000.0},
        {"month": "2025-03-01", "segment": "Direct", "revenue": 27000.0},
        {"month": "2025-03-01", "segment": "Partner", "revenue": 20000.0},
        {"month": "2025-04-01", "segment": "Direct", "revenue": 38000.0},
        {"month": "2025-04-01", "segment": "Partner", "revenue": 25000.0},
        {"month": "2025-05-01", "segment": "Direct", "revenue": 34000.0},
        {"month": "2025-05-01", "segment": "Partner", "revenue": 24000.0},
        {"month": "2025-06-01", "segment": "Direct", "revenue": 43000.0},
        {"month": "2025-06-01", "segment": "Partner", "revenue": 28000.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "area",
            "x": "month",
            "y": "revenue",
            "color": "segment",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
        }
    )
    board_style, chart_style_context = resolve_style_and_context(
        get_theme_style("vivid")
    )
    spec = generate_vega_lite_spec(
        chart,
        data,
        width=400,
        board_style=board_style,
        chart_style_context=chart_style_context,
    )
    # A faceted spec nests under "spec"; the endpoint-label rail nests the
    # real chart under "hconcat"[0] (a second pane carries the label text).
    if "facet" in spec:
        encoding = spec["spec"]["encoding"]
    elif "hconcat" in spec:
        encoding = spec["hconcat"][0]["encoding"]
    else:
        encoding = spec["encoding"]
    visible = encoding["x"]["axis"]["ticks"]
    assert isinstance(visible, bool)
    assert visible is True


def test_bar_ticks_stay_hidden() -> None:
    """Bar always zero-anchors and pins ticks.visible: false explicitly --
    unaffected by the auto rule (never reaches "auto" at all)."""
    assert _x_ticks_visible("bar", _ZERO_ANCHORED_DATA) is False


def test_hidden_y_grid_hides_x_ticks() -> None:
    """No horizontal gridlines means nothing caps the vertical ones.

    The only arm of the rule that answers False, and the destructive
    direction: the vertical gridlines run unimpeded to the labels, so the
    stub is redundant. Untested, deleting the arm passes the whole suite --
    no golden reaches it either, since the only charted no-horizontal-grid
    cases are bars, which pin ``ticks.visible: false`` and never resolve
    ``auto`` at all.
    """
    style = {"axis_y": {"grid": {"visible": False}}}
    assert _x_ticks_visible("line", _ZERO_ANCHORED_DATA, style) is False


def test_layered_single_series_line_with_endpoint_labels_keeps_x_ticks() -> None:
    """The colorless sibling of the multi-series rail case above.

    Render's own gate (``endpoint_rail_layout``) returns a right pane for a
    layered chart with no color channel at all -- scalar x/y plus at least
    one colorless layer -- so this reaches the identical shared-scale
    ``hconcat`` by a different door. Compile must reach the same answer
    through the same predicate render uses, or the two disagree about
    whether the baked ``domainMin`` survives and the stub is dropped from a
    chart whose gridlines are capped.
    """
    data = [
        {"month": "2025-01-01", "revenue": 24000.0, "target": 22000.0},
        {"month": "2025-02-01", "revenue": 31000.0, "target": 26000.0},
        {"month": "2025-03-01", "revenue": 27000.0, "target": 28000.0},
        {"month": "2025-04-01", "revenue": 38000.0, "target": 30000.0},
        {"month": "2025-05-01", "revenue": 34000.0, "target": 32000.0},
        {"month": "2025-06-01", "revenue": 43000.0, "target": 34000.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "line",
            "x": "month",
            "y": "revenue",
            "layers": [{"type": "line", "y": "target"}],
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {"endpoint_labels": {"visible": True}},
        }
    )
    board_style, chart_style_context = resolve_style_and_context(
        get_theme_style("vivid")
    )
    spec = generate_vega_lite_spec(
        chart,
        data,
        width=400,
        board_style=board_style,
        chart_style_context=chart_style_context,
    )
    if "facet" in spec:
        encoding = spec["spec"]["encoding"]
    elif "hconcat" in spec:
        encoding = spec["hconcat"][0]["encoding"]
    else:
        encoding = spec["encoding"]
    visible = encoding["x"]["axis"]["ticks"]
    assert isinstance(visible, bool)
    assert visible is True
