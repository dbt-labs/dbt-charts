"""``legend.position.edge: endpoints`` names series on the chart; any concrete edge a legend.

The default (``auto``) is per chart shape: where the endpoint rail can name every
series it replaces the legend, elsewhere the legend ladder decides. A concrete edge
from any tier is a placed legend, so it never also prints endpoint labels.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedChart,
    ResolvedLineChart,
)
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.diagnostics import WARN_LEGEND_POSITION_WIDTH_FALLBACK
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_LEGEND_POSITION_UNSUPPORTED
from dbt_charts.core.render.warnings import (
    WarningContext,
    legend_position_width_fallback,
)

from ..._board_utils import make_test_resolved_board

_ROWS: list[dict[str, Any]] = [
    {"m": "2024-01-01", "c": "A", "v": 1.0, "w": 2.0, "k": 7.0},
    {"m": "2024-02-01", "c": "A", "v": 3.0, "w": 1.0, "k": 7.0},
    {"m": "2024-01-01", "c": "B", "v": 2.0, "w": 4.0, "k": 7.0},
    {"m": "2024-02-01", "c": "B", "v": 4.0, "w": 3.0, "k": 7.0},
]

_WIDE = 900.0
_TINY = 200.0

_SHAPES: dict[str, dict[str, Any]] = {
    "line": {"type": "line", "query": "q", "x": "m", "y": "v", "color": "c"},
    "area": {"type": "area", "query": "q", "x": "m", "y": "v", "color": "c"},
    "stacked_bar": {
        "type": "bar",
        "query": "q",
        "x": "m",
        "y": "v",
        "color": "c",
        "style": {"stack": "zero"},
    },
    "grouped_bar": {
        "type": "bar",
        "query": "q",
        "x": "m",
        "y": "v",
        "color": "c",
        "style": {"stack": "none"},
    },
    "layered_line": {
        "type": "line",
        "query": "q",
        "x": "m",
        "y": "v",
        "layers": [{"type": "line", "y": "k"}],
    },
}

_SCATTER: dict[str, Any] = {
    "type": "scatter",
    "query": "q",
    "x": "v",
    "y": "w",
    "color": "c",
}


def _resolve(
    chart_def: dict[str, Any],
    *,
    edge: str | None = None,
    board_edge: str | None = None,
    family_edge: str | None = None,
    visible: bool | None = None,
    width: float = _WIDE,
    position: dict[str, Any] | None = None,
    board_position: dict[str, Any] | None = None,
) -> ResolvedChart:
    style = dict(chart_def.get("style", {}))
    legend: dict[str, Any] = {}
    if edge is not None or position is not None:
        legend["position"] = {**({"edge": edge} if edge else {}), **(position or {})}
    if visible is not None:
        legend["visible"] = visible
    if legend:
        style["legend"] = legend
    flat = normalize_chart(
        "c1", {**chart_def, "style": style}, {"q": ValuesQuery(rows=_ROWS)}, sources={}
    )
    charts: dict[str, Any] = {}
    if board_edge is not None:
        charts["legend"] = {"position": {"edge": board_edge}}
    if board_position is not None:
        charts["legend"] = {
            "position": {
                **board_position,
                **(charts.get("legend", {}).get("position", {})),
            }
        }
    if family_edge is not None:
        charts[chart_def["type"]] = {"legend": {"position": {"edge": family_edge}}}
    board = StylePatch.model_validate({"charts": charts}) if charts else None
    context = resolve_chart_style_context(get_theme_style(), board)
    return resolve(flat, _ROWS, chart_style_context=context, width=width)


def _names_series_on_chart(resolved: ResolvedChart) -> bool:
    assert isinstance(
        resolved, (ResolvedLineChart, ResolvedAreaChart, ResolvedBarChart)
    )
    return resolved.style.endpoint_labels.visible


@pytest.mark.parametrize(
    ("shape", "endpoints"),
    [
        ("line", True),
        ("area", True),
        ("stacked_bar", True),
        ("grouped_bar", False),
        ("layered_line", False),
    ],
)
def test_auto_resolves_per_shape(shape: str, endpoints: bool) -> None:
    resolved = _resolve(_SHAPES[shape])

    assert _names_series_on_chart(resolved) is endpoints
    assert resolved.legend.visible is not endpoints


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_tiny_card_resolves_auto_to_the_top_legend(shape: str) -> None:
    resolved = _resolve(_SHAPES[shape], width=_TINY)

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.visible is True
    assert resolved.legend.position.edge == "top"


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
@pytest.mark.parametrize("edge", ["top", "bottom", "left", "right"])
def test_a_concrete_edge_is_a_legend_and_no_endpoint_labels(
    shape: str, edge: str
) -> None:
    resolved = _resolve(_SHAPES[shape], edge=edge)

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.visible is True
    assert resolved.legend.position.edge == edge


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_a_board_tier_edge_wins_over_the_default(shape: str) -> None:
    """A project's meta.yml `edge: top` is a placed legend, not a second one."""
    resolved = _resolve(_SHAPES[shape], board_edge="top")

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.position.edge == "top"


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_a_chart_edge_wins_over_a_board_endpoints(shape: str) -> None:
    resolved = _resolve(_SHAPES[shape], edge="left", board_edge="endpoints")

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.position.edge == "left"


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
@pytest.mark.parametrize("tier", ["board_edge", "family_edge"])
def test_a_chart_auto_edge_overrides_a_broader_concrete_edge(
    shape: str, tier: str
) -> None:
    """The migration writes `edge: auto` for a chart that overrode a broader `visible`."""
    resolved = _resolve(_SHAPES[shape], edge="auto", **{tier: "bottom"})

    assert _names_series_on_chart(resolved) is True
    assert resolved.legend.visible is False


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_endpoints_names_series_on_the_chart_with_no_legend(shape: str) -> None:
    resolved = _resolve(_SHAPES[shape], edge="endpoints")

    assert _names_series_on_chart(resolved) is True
    assert resolved.legend.visible is False


def test_endpoints_on_a_tiny_card_falls_back_to_the_top_legend() -> None:
    resolved = _resolve(_SHAPES["line"], edge="endpoints", width=_TINY)

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.position.edge == "top"
    assert resolved.legend.position_overridden_by_width == "endpoints"
    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={resolved.id: resolved}),
        chart_results={resolved.id: _ROWS},
        layout_charts={resolved.id: resolved},
    )
    (warning,) = legend_position_width_fallback.detect(ctx)
    assert warning.code == WARN_LEGEND_POSITION_WIDTH_FALLBACK.code
    assert "endpoints" in warning.message


def test_endpoints_on_a_horizontal_grouped_bar_falls_back_to_the_top_legend() -> None:
    grouped = _SHAPES["grouped_bar"]
    horizontal = {**grouped, "style": {**grouped["style"], "orientation": "horizontal"}}

    resolved = _resolve(horizontal, edge="endpoints")

    assert resolved.legend.visible is True
    assert resolved.legend.position.edge == "top"


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
@pytest.mark.parametrize(
    ("visible", "legend_drawn"), [(None, True), (True, True), (False, False)]
)
@pytest.mark.parametrize("edge", ["top", "bottom", "left", "right"])
def test_a_concrete_edge_turns_the_rail_off_whatever_visible_says(
    shape: str, edge: str, visible: bool | None, legend_drawn: bool
) -> None:
    """`visible: false` beside a placed edge names nothing: no labels, no legend."""
    resolved = _resolve(_SHAPES[shape], edge=edge, visible=visible)

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.visible is legend_drawn


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
@pytest.mark.parametrize("tier", ["board_edge", "family_edge"])
def test_a_hidden_legend_under_a_broader_edge_names_nothing(
    shape: str, tier: str
) -> None:
    """Cross-tier, the edge owns the rail too; WARN-LEGEND-HIDDEN-UNDER-EDGE says so."""
    resolved = _resolve(_SHAPES[shape], visible=False, **{tier: "top"})

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.visible is False


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_auto_with_a_hidden_legend_keeps_the_rail(shape: str) -> None:
    resolved = _resolve(_SHAPES[shape], visible=False)

    assert _names_series_on_chart(resolved) is True
    assert resolved.legend.visible is False


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_auto_with_a_shown_legend_is_a_legend_and_no_rail(shape: str) -> None:
    resolved = _resolve(_SHAPES[shape], visible=True)

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.visible is True


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_endpoints_beside_a_shown_legend_prints_both(shape: str) -> None:
    """Both were asked for in writing; neither is retired."""
    resolved = _resolve(_SHAPES[shape], edge="endpoints", visible=True)

    assert _names_series_on_chart(resolved) is True
    assert resolved.legend.visible is True


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_endpoints_beside_a_hidden_legend_prints_the_rail_only(shape: str) -> None:
    resolved = _resolve(_SHAPES[shape], edge="endpoints", visible=False)

    assert _names_series_on_chart(resolved) is True
    assert resolved.legend.visible is False


@pytest.mark.parametrize("chart_type", ["scatter", "heatmap"])
def test_the_chart_type_forbids_endpoints(chart_type: str) -> None:
    chart_def = {**_SCATTER, "type": chart_type}
    with pytest.raises(CompilationError, match="endpoints"):
        _resolve(chart_def, edge="endpoints")


@pytest.mark.parametrize("family", ["scatter", "heatmap", "histogram"])
def test_a_family_tier_endpoints_is_rejected_for_a_family_that_cannot_use_it(
    family: str,
) -> None:
    with pytest.raises(ValidationError) as caught:
        StylePatch.model_validate(
            {"charts": {family: {"legend": {"position": {"edge": "endpoints"}}}}}
        )

    [error] = caught.value.errors()
    assert "'left', 'right', 'top', 'bottom' or 'auto'" in error["msg"]


def test_a_histogram_chart_with_endpoints_is_a_compile_error() -> None:
    """A histogram shares the bar style model, so a constraint forbids it."""
    result = compile_board(
        """
queries:
  q:
    type: values
    rows:
      - {v: 1}
      - {v: 2}
charts:
  h:
    type: histogram
    query: q
    x: v
    style:
      legend:
        position:
          edge: endpoints
rows:
  - h
"""
    )

    assert not result.success
    assert "endpoints" in str(result.errors)


def test_a_board_tier_endpoints_leaves_a_scatter_legend_alone() -> None:
    """`endpoints` set for the whole board names series where it can."""
    resolved = _resolve(_SCATTER, board_edge="endpoints")

    assert resolved.legend.visible is True
    assert resolved.legend.position.edge == "right"


_SAME_MAPPING_BOARD = """
queries:
  q:
    type: values
    rows:
      - {m: "2024-01-01", c: A, v: 1}
%s
charts:
  c1:
    type: line
    query: q
    x: m
    y: v
    color: c
%s
rows:
  - c1
"""


def _endpoints_with(indent: str, leaf: str, value: str) -> str:
    return f"{indent}position:\n{indent}  edge: endpoints\n{indent}  {leaf}: {value}\n"


@pytest.mark.parametrize("tier", ["chart", "family", "board"])
@pytest.mark.parametrize(
    ("leaf", "value", "code"),
    [
        ("align", "end", "ERR-LEGEND-ALIGN-WITH-ENDPOINTS"),
        ("overlay", "true", "ERR-LEGEND-OVERLAY-WITH-ENDPOINTS"),
        ("overlay", "false", "ERR-LEGEND-OVERLAY-WITH-ENDPOINTS"),
    ],
)
def test_align_or_overlay_in_the_endpoints_mapping_is_an_error(
    tier: str, leaf: str, value: str, code: str
) -> None:
    board_style = {
        "chart": "",
        "family": "style:\n  charts:\n    line:\n      legend:\n"
        + _endpoints_with(" " * 8, leaf, value),
        "board": "style:\n  charts:\n    legend:\n"
        + _endpoints_with(" " * 6, leaf, value),
    }[tier]
    chart_style = (
        "    style:\n      legend:\n" + _endpoints_with(" " * 8, leaf, value)
        if tier == "chart"
        else ""
    )
    result = compile_board(_SAME_MAPPING_BOARD % (board_style, chart_style))

    assert not result.success
    [error] = result.errors
    assert error.code == code


@pytest.mark.parametrize("endpoints_at", ["chart", "family"])
@pytest.mark.parametrize(
    ("leaf", "value"), [("align", "center"), ("overlay", True), ("overlay", False)]
)
def test_a_broader_tier_align_or_overlay_does_not_stop_endpoints(
    endpoints_at: str, leaf: str, value: Any
) -> None:
    resolved = _resolve(
        _SHAPES["line"],
        edge="endpoints" if endpoints_at == "chart" else None,
        family_edge="endpoints" if endpoints_at == "family" else None,
        board_position={leaf: value},
    )

    assert _names_series_on_chart(resolved) is True


@pytest.mark.parametrize(("leaf", "value"), [("align", "start"), ("overlay", True)])
def test_a_chart_align_or_overlay_under_a_board_endpoints_rides_the_rail(
    leaf: str, value: Any
) -> None:
    resolved = _resolve(_SHAPES["line"], board_edge="endpoints", position={leaf: value})

    assert _names_series_on_chart(resolved) is True


@pytest.mark.parametrize("chart_type", ["scatter", "heatmap"])
@pytest.mark.parametrize(("leaf", "value"), [("align", "start"), ("overlay", True)])
def test_a_board_endpoints_leaves_a_legend_family_its_own_align_and_overlay(
    chart_type: str, leaf: str, value: Any
) -> None:
    resolved = _resolve(
        {**_SCATTER, "type": chart_type},
        board_edge="endpoints",
        position={leaf: value},
    )

    assert resolved.legend.visible is True
    assert resolved.legend.position.model_dump()[leaf] == value


@pytest.mark.parametrize("leaf", ["align", "overlay"])
def test_auto_align_or_overlay_beside_endpoints_is_fine(leaf: str) -> None:
    resolved = _resolve(_SHAPES["line"], edge="endpoints", position={leaf: "auto"})

    assert _names_series_on_chart(resolved) is True


@pytest.mark.parametrize("endpoints_at", ["chart", "family"])
def test_a_broader_align_must_be_one_the_legend_supports(endpoints_at: str) -> None:
    """The rail ignores an inherited `align`, but the placement still resolves."""
    with pytest.raises(ChartDataError) as caught:
        _resolve(
            _SHAPES["line"],
            edge="endpoints" if endpoints_at == "chart" else None,
            family_edge="endpoints" if endpoints_at == "family" else None,
            board_position={"align": "end"},
        )

    assert caught.value.code is ERR_LEGEND_POSITION_UNSUPPORTED


def test_a_legend_shown_beside_the_rail_keeps_a_broader_align() -> None:
    resolved = _resolve(
        _SHAPES["line"],
        edge="endpoints",
        visible=True,
        board_position={"align": "center"},
    )

    assert _names_series_on_chart(resolved) is True
    assert resolved.legend.visible is True
    assert resolved.legend.position.align == "center"


def test_a_legend_shown_beside_the_rail_still_needs_a_supported_placement() -> None:
    with pytest.raises(ChartDataError) as caught:
        _resolve(
            _SHAPES["line"],
            edge="endpoints",
            visible=True,
            board_position={"align": "end"},
        )

    assert caught.value.code is ERR_LEGEND_POSITION_UNSUPPORTED
