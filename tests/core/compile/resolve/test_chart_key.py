"""``style.key`` decides how a chart names its series: a legend or on the chart itself.

``type: auto`` is per chart shape: where the endpoint rail can name every
series it replaces the legend, elsewhere the legend ladder decides. A placed legend
edge under ``auto`` is a legend, so it never also prints endpoint labels.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import get_theme_style
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
    key_type: str | None = None,
    visible: bool | None = None,
    board_key: dict[str, Any] | None = None,
    family_key: dict[str, Any] | None = None,
    width: float = _WIDE,
    position: dict[str, Any] | None = None,
) -> ResolvedChart:
    style = dict(chart_def.get("style", {}))
    key: dict[str, Any] = {}
    if edge is not None or position is not None:
        key["legend"] = {
            "position": {**({"edge": edge} if edge else {}), **(position or {})}
        }
    if key_type is not None:
        key["type"] = key_type
    if visible is not None:
        key["visible"] = visible
    if key:
        style["key"] = key
    flat = normalize_chart(
        "c1", {**chart_def, "style": style}, {"q": ValuesQuery(rows=_ROWS)}, sources={}
    )
    charts: dict[str, Any] = {}
    if board_key is not None:
        charts["key"] = board_key
    if family_key is not None:
        charts[chart_def["type"]] = {"key": family_key}
    board = StylePatch.model_validate({"charts": charts}) if charts else None
    context = resolve_chart_style_context(get_theme_style(), board)
    return resolve(flat, _ROWS, chart_style_context=context, width=width)


def _names_series_on_chart(resolved: ResolvedChart) -> bool:
    assert isinstance(
        resolved, (ResolvedLineChart, ResolvedAreaChart, ResolvedBarChart)
    )
    return resolved.style.endpoint_labels.visible


@pytest.mark.parametrize(
    ("shape", "direct"),
    [
        ("line", True),
        ("area", True),
        ("stacked_bar", True),
        ("grouped_bar", False),
        ("layered_line", False),
    ],
)
def test_auto_resolves_per_shape(shape: str, direct: bool) -> None:
    resolved = _resolve(_SHAPES[shape])

    assert _names_series_on_chart(resolved) is direct
    assert resolved.legend.visible is not direct


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_tiny_card_resolves_auto_to_the_top_legend(shape: str) -> None:
    resolved = _resolve(_SHAPES[shape], width=_TINY)

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.visible is True
    assert resolved.legend.position.edge == "top"


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
@pytest.mark.parametrize("edge", ["top", "bottom", "left", "right"])
def test_a_placed_edge_under_auto_is_a_legend_and_no_direct_labels(
    shape: str, edge: str
) -> None:
    resolved = _resolve(_SHAPES[shape], edge=edge)

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.visible is True
    assert resolved.legend.position.edge == edge


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_a_board_tier_edge_wins_over_the_default(shape: str) -> None:
    """A project's meta.yml `edge: top` is a placed legend, not a second one."""
    resolved = _resolve(
        _SHAPES[shape], board_key={"legend": {"position": {"edge": "top"}}}
    )

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.position.edge == "top"


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_type_direct_names_series_on_the_chart_with_no_legend(shape: str) -> None:
    resolved = _resolve(_SHAPES[shape], key_type="direct")

    assert _names_series_on_chart(resolved) is True
    assert resolved.legend.visible is False


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_type_legend_is_a_legend_and_no_direct_labels(shape: str) -> None:
    resolved = _resolve(_SHAPES[shape], key_type="legend")

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.visible is True


@pytest.mark.parametrize("tier", ["board", "family"])
def test_a_chart_type_overrides_a_broader_type(tier: str) -> None:
    resolved = _resolve(
        _SHAPES["line"],
        key_type="legend",
        **{f"{tier}_key": {"type": "direct"}},
    )

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.visible is True


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
@pytest.mark.parametrize("key_type", [None, "legend", "direct"])
def test_a_hidden_key_names_nothing(shape: str, key_type: str | None) -> None:
    resolved = _resolve(_SHAPES[shape], visible=False, key_type=key_type)

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.visible is False


def test_direct_on_a_tiny_card_falls_back_to_the_top_legend() -> None:
    resolved = _resolve(_SHAPES["line"], key_type="direct", width=_TINY)

    assert _names_series_on_chart(resolved) is False
    assert resolved.legend.position.edge == "top"
    assert resolved.legend.position_overridden_by_width == "direct"
    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={resolved.id: resolved}),
        chart_results={resolved.id: _ROWS},
        layout_charts={resolved.id: resolved},
    )
    (warning,) = legend_position_width_fallback.detect(ctx)
    assert warning.code == WARN_LEGEND_POSITION_WIDTH_FALLBACK.code
    assert "direct" in warning.message


def test_direct_on_a_horizontal_grouped_bar_falls_back_to_the_top_legend() -> None:
    grouped = _SHAPES["grouped_bar"]
    horizontal = {**grouped, "style": {**grouped["style"], "orientation": "horizontal"}}

    resolved = _resolve(horizontal, key_type="direct")

    assert resolved.legend.visible is True
    assert resolved.legend.position.edge == "top"


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
@pytest.mark.parametrize("key_type", [None, "direct"])
def test_a_layer_declining_the_key_does_not_hold_back_the_rail(
    shape: str, key_type: str | None
) -> None:
    """A layer with `key: false` is not named, so only the base series is: the
    rail names it and retires the legend. A keyed layer needs the legend."""
    declined = _resolve(
        {**_SHAPES[shape], "layers": [{"type": "line", "y": "k", "key": False}]},
        key_type=key_type,
    )
    keyed = _resolve(
        {**_SHAPES[shape], "layers": [{"type": "line", "y": "k", "key": True}]},
        key_type=key_type,
    )

    assert _names_series_on_chart(declined) is True
    assert declined.legend.visible is False
    assert _names_series_on_chart(keyed) is (key_type == "direct")
    assert keyed.legend.visible is True


@pytest.mark.parametrize("kind", ["line", "area", "bar"])
def test_a_declined_layer_with_its_own_color_does_not_block_the_layered_rail(
    kind: str,
) -> None:
    """The layered rail names layers that share the base color; a colored layer
    that declines the key is not named, so it cannot block the rail."""

    def resolve_with(layer_key: bool) -> ResolvedChart:
        shape = {
            **_SHAPES["layered_line"],
            "type": kind,
            "layers": [
                {"type": "line", "y": "k"},
                {"type": "line", "y": "w", "color": "c", "key": layer_key},
            ],
        }
        return _resolve(shape, key_type="direct")

    assert resolve_with(False).legend.visible is False
    assert resolve_with(True).legend.visible is True


@pytest.mark.parametrize("chart_type", ["scatter", "heatmap", "histogram"])
def test_a_family_that_cannot_name_series_on_the_chart_refuses_type_direct(
    chart_type: str,
) -> None:
    with pytest.raises(ValidationError) as caught:
        StylePatch.model_validate({"charts": {chart_type: {"key": {"type": "direct"}}}})

    [error] = caught.value.errors()
    assert "'auto' or 'legend'" in error["msg"]


def test_a_histogram_chart_with_type_direct_is_a_compile_error() -> None:
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
      key:
        type: direct
rows:
  - h
"""
    )

    assert not result.success
    assert "direct" in str(result.errors)


def test_a_board_tier_direct_leaves_a_scatter_legend_alone() -> None:
    """`direct` set for the whole board names series where it can."""
    resolved = _resolve(_SCATTER, board_key={"type": "direct"})

    assert resolved.legend.visible is True
    assert resolved.legend.position.edge == "right"


@pytest.mark.parametrize("leaf", ["align", "overlay"])
def test_a_placed_align_or_overlay_under_auto_is_a_legend(leaf: str) -> None:
    resolved = _resolve(
        _SHAPES["line"], position={leaf: "center" if leaf == "align" else True}
    )

    assert _names_series_on_chart(resolved) is False


@pytest.mark.parametrize("leaf", ["align", "overlay"])
def test_direct_ignores_a_legend_align_or_overlay(leaf: str) -> None:
    resolved = _resolve(
        _SHAPES["line"],
        key_type="direct",
        position={leaf: "center" if leaf == "align" else True},
    )

    assert _names_series_on_chart(resolved) is True


def test_a_broader_align_must_be_one_the_legend_supports() -> None:
    """The rail ignores an inherited `align`, but the placement still resolves."""
    with pytest.raises(ChartDataError) as caught:
        _resolve(
            _SHAPES["line"],
            key_type="direct",
            board_key={"legend": {"position": {"align": "end"}}},
        )

    assert caught.value.code is ERR_LEGEND_POSITION_UNSUPPORTED


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
def test_board_direct_key_reaches_the_direct_labels_and_a_family_overrides_it(
    shape: str,
) -> None:
    board_direct = {"offset": 37.0, "font": {"size": 23.0}}
    resolved = _resolve(
        _SHAPES[shape], key_type="direct", board_key={"direct": board_direct}
    )
    assert isinstance(
        resolved, (ResolvedLineChart, ResolvedAreaChart, ResolvedBarChart)
    )
    assert resolved.style.endpoint_labels.label_offset == 37.0
    assert resolved.style.series_label.font_size == 23.0

    family = _resolve(
        _SHAPES[shape],
        key_type="direct",
        board_key={"direct": board_direct},
        family_key={"direct": {"offset": 41.0, "font": {"size": 19.0}}},
    )
    assert isinstance(family, (ResolvedLineChart, ResolvedAreaChart, ResolvedBarChart))
    assert family.style.endpoint_labels.label_offset == 41.0
    assert family.style.series_label.font_size == 19.0


def test_board_direct_height_reaches_a_bar_and_a_family_overrides_it() -> None:
    board_key = {"direct": {"height": 55.0}}
    resolved = _resolve(_SHAPES["stacked_bar"], key_type="direct", board_key=board_key)
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.style.endpoint_labels.height == 55.0

    family = _resolve(
        _SHAPES["stacked_bar"],
        key_type="direct",
        board_key=board_key,
        family_key={"direct": {"height": 61.0}},
    )
    assert isinstance(family, ResolvedBarChart)
    assert family.style.endpoint_labels.height == 61.0


_LADDER: list[tuple[str, dict[str, Any]]] = [
    ("local", {"direct": {"offset": 71.0, "font": {"size": 29.0}}}),
    ("family", {"direct": {"offset": 61.0, "font": {"size": 19.0}}}),
    ("board", {"direct": {"offset": 41.0, "font": {"size": 13.0}}}),
]


def _ladder_resolve(
    shape: str, tiers: list[str], leaves: dict[str, dict[str, Any]]
) -> ResolvedChart:
    chart_def = {**_SHAPES[shape]}
    chart_def["style"] = {
        **chart_def.get("style", {}),
        **({"key": leaves["local"]} if "local" in tiers else {}),
    }
    return _resolve(
        chart_def,
        board_key=leaves["board"] if "board" in tiers else None,
        family_key=leaves["family"] if "family" in tiers else None,
    )


@pytest.mark.parametrize("shape", ["line", "area", "stacked_bar"])
@pytest.mark.parametrize(
    ("tiers", "offset", "size"),
    [
        (["local", "family", "board"], 71.0, 29.0),
        (["family", "board"], 61.0, 19.0),
        (["board"], 41.0, 13.0),
    ],
)
def test_direct_key_cascades_chart_then_family_then_board(
    shape: str, tiers: list[str], offset: float, size: float
) -> None:
    leaves = {tier: {"type": "direct", **leaf} for tier, leaf in _LADDER}
    resolved = _ladder_resolve(shape, tiers, leaves)

    assert isinstance(
        resolved, (ResolvedLineChart, ResolvedAreaChart, ResolvedBarChart)
    )
    assert resolved.style.endpoint_labels.label_offset == offset
    assert resolved.style.series_label.font_size == size


@pytest.mark.parametrize(
    ("tiers", "size"),
    [
        (["local", "family", "board"], 17.0),
        (["family", "board"], 15.0),
        (["board"], 11.0),
    ],
)
def test_legend_leaves_cascade_chart_then_family_then_board(
    tiers: list[str], size: float
) -> None:
    leaves = {
        tier: {"type": "legend", "legend": {"label": {"font": {"size": value}}}}
        for tier, value in (("local", 17.0), ("family", 15.0), ("board", 11.0))
    }
    resolved = _ladder_resolve("line", tiers, leaves)

    assert resolved.legend.label.font.size == size


@pytest.mark.parametrize(
    ("tiers", "direct"),
    [
        (["local", "family", "board"], False),
        (["family", "board"], True),
        (["board"], False),
    ],
)
def test_key_type_cascades_chart_then_family_then_board(
    tiers: list[str], direct: bool
) -> None:
    leaves = {
        "local": {"type": "legend"},
        "family": {"type": "direct"},
        "board": {"type": "legend"},
    }
    resolved = _ladder_resolve("line", tiers, leaves)

    assert _names_series_on_chart(resolved) is direct


@pytest.mark.parametrize(
    ("tiers", "height"),
    [
        (["local", "family", "board"], 77.0),
        (["family", "board"], 66.0),
        (["board"], 55.0),
    ],
)
def test_bar_direct_height_cascades_chart_then_family_then_board(
    tiers: list[str], height: float
) -> None:
    leaves = {
        tier: {"type": "direct", "direct": {"height": value}}
        for tier, value in (("local", 77.0), ("family", 66.0), ("board", 55.0))
    }
    resolved = _ladder_resolve("stacked_bar", tiers, leaves)

    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.style.endpoint_labels.height == height


@pytest.mark.parametrize("chart_type", ["line", "area"])
def test_a_line_or_area_key_refuses_a_direct_height(chart_type: str) -> None:
    with pytest.raises(ValidationError) as caught:
        StylePatch.model_validate(
            {"charts": {chart_type: {"key": {"direct": {"height": 10}}}}}
        )

    assert caught.value.errors()[0]["type"] == "extra_forbidden"
