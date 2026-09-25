"""Every chart-shape recipe the error formatter hands out must actually draw.

RJ's probe of adjacent shapes produced two recipes that passed `dct validate` and
rendered wrong — a lollipop with no stems, a bullet with its target markers piled in
one corner. A recipe nobody rendered is worse than no recipe, so each noun in
`_CHART_SHAPE_RECIPES` is pinned here against the emitted Vega-Lite spec, and
`test_every_recipe_is_render_verified` fails if a row is added without one.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.authored import (
    BarChartBarLayer,
    LayerAxisYStyle,
    LineLayer,
    ScatterLayer,
)
from dbt_charts.core.compile.models.chart.authored._base import MultiplesConfig
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    BarChartStylePatch,
    LineChartStylePatch,
)
from dbt_charts.core.compile.parse.yaml_error_formatter import (
    _CHART_SHAPE_RECIPES,
    _NORMALIZED_UNSUPPORTED_SHAPES,
    _RECIPE_BY_NORMALIZED_NOUN,
    _shape_noun_keys,
    get_valid_chart_types,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_DATA = [
    {"month": "Jan", "segment": "A", "revenue": 2},
    {"month": "Jan", "segment": "B", "revenue": 10},
    {"month": "Feb", "segment": "A", "revenue": 4},
    {"month": "Feb", "segment": "B", "revenue": 8},
]

_PANEL_DATA = [
    {"month": m, "region": r, "revenue": 100 + m * 5}
    for r in ("West", "East")
    for m in range(1, 4)
]

_SINGLE_PANEL_DATA = [{"month": m, "revenue": 100 + m * 5} for m in range(1, 4)]

_LAYER_DATA = [
    {"month": "Jan", "revenue": 100.0, "target": 4000.0},
    {"month": "Feb", "revenue": 200.0, "target": 5000.0},
    {"month": "Mar", "revenue": 150.0, "target": 4500.0},
]

_SHAPE_DATA = [
    {"region": "West", "revenue": 100.0, "target": 80.0},
    {"region": "East", "revenue": 60.0, "target": 90.0},
    {"region": "North", "revenue": 140.0, "target": 110.0},
]

_SLOPE_DATA = [
    {"period": p, "region": r, "revenue": v}
    for p, r, v in (
        ("Before", "West", 40.0),
        ("Before", "East", 70.0),
        ("After", "West", 90.0),
        ("After", "East", 55.0),
    )
]

_RANK_DATA = [
    {"year": y, "region": r, "rank": rk}
    for y, r, rk in (
        (2019, "West", 1),
        (2019, "East", 2),
        (2020, "West", 2),
        (2020, "East", 1),
    )
]


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_config()
    yield
    reset_config()


def _chart_pane(spec: dict[str, Any]) -> dict[str, Any]:
    """The chart pane of a spec that endpoint labels may have wrapped.

    Same unwrap as `chart_pane` in tests/core/conftest.py, inlined because that
    module is only importable from the package dirs directly beneath it.
    """
    if "hconcat" in spec:
        return spec["hconcat"][0]
    if "vconcat" in spec:
        return spec["vconcat"][1]
    return spec


def _encoding(chart: AreaChart | BarChart) -> dict[str, Any]:
    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    spec = generate_vega_lite_spec(
        chart, _DATA, board_style=board_style, chart_style_context=board_ctx
    )
    return _chart_pane(spec)["encoding"]


def _area(stack: str) -> dict[str, Any]:
    return _encoding(
        AreaChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="area",
            x="month",
            y="revenue",
            color="segment",
            style=AreaChartStylePatch(stack=stack),
        )
    )


def _bar(**patch: Any) -> dict[str, Any]:
    return _encoding(
        BarChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="month",
            y="revenue",
            color="segment",
            style=BarChartStylePatch(**patch),
        )
    )


def _stack_mode(encoding: dict[str, Any]) -> str | None:
    """The stack mode off whichever axis carries the measure.

    Read from both axes rather than pinned to one: bar orientation defaults are
    tunable, and this assertion is about the stack mode, not the axis it lands on.
    """
    for channel in ("x", "y"):
        stack = encoding.get(channel, {}).get("stack")
        if stack is not None:
            return str(stack)
    return None


def test_streamgraph_recipe_center_stacks() -> None:
    assert _stack_mode(_area("center")) == "center"


def test_stacked_area_recipe_zero_stacks() -> None:
    assert _stack_mode(_area("zero")) == "zero"


def test_stacked_bar_recipe_zero_stacks() -> None:
    assert _stack_mode(_bar(stack="zero")) == "zero"


def test_grouped_bar_recipe_offsets_rather_than_stacks() -> None:
    """`stack: none` must separate the series, not overprint them in place."""
    encoding = _bar(stack="none")

    assert _stack_mode(encoding) is None
    offsets = {"xOffset", "yOffset"} & set(encoding)
    assert offsets, (
        f"grouped bars would overprint: no offset channel in {sorted(encoding)}"
    )


def test_horizontal_bar_recipe_puts_the_measure_on_x() -> None:
    encoding = _bar(orientation="horizontal", stack="zero")

    assert encoding["x"]["field"] == "revenue"
    assert encoding["y"]["field"] == "month"


def test_column_recipe_puts_the_measure_on_y() -> None:
    encoding = _bar(orientation="vertical", stack="zero")

    assert encoding["x"]["field"] == "month"
    assert encoding["y"]["field"] == "revenue"


def test_percent_stacked_recipe_normalizes_bar_and_area() -> None:
    """Covers `100% stacked` / `percent_stacked_bar` / `normalized_bar`."""
    assert _stack_mode(_bar(stack="normalize")) == "normalize"
    assert _stack_mode(_area("normalize")) == "normalize"


@pytest.mark.parametrize(
    ("noun", "stack_patch", "expect_stack"),
    [
        ("stacked_column", "zero", "zero"),
        ("grouped_column", "none", None),
        ("clustered_column", "none", None),
    ],
)
def test_column_variant_recipes_stay_vertical(
    noun: str, stack_patch: str, expect_stack: str | None
) -> None:
    """The `_column` nouns promise a vertical layout. `_DATA.x` (`month`) is a
    string, which auto-resolves to horizontal when orientation is left unset —
    so the recipe must say `style.orientation: vertical` explicitly, not just
    reuse the `_bar` recipe text verbatim."""
    encoding = _bar(orientation="vertical", stack=stack_patch)

    assert encoding["x"]["field"] == "month", noun
    assert encoding["y"]["field"] == "revenue", noun
    assert _stack_mode(encoding) == expect_stack, noun
    if expect_stack is None:
        assert {"xOffset", "yOffset"} & set(encoding), noun


def _multiples_facet(rows: str | None) -> dict[str, Any] | None:
    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    chart = LineChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="revenue",
        multiples=MultiplesConfig(rows=rows) if rows else None,
    )
    data = _PANEL_DATA if rows else _SINGLE_PANEL_DATA
    spec = generate_vega_lite_spec(
        chart, data, board_style=board_style, chart_style_context=board_ctx
    )
    return spec.get("facet")


@pytest.mark.parametrize("noun", ["small_multiples", "trellis", "faceted"])
def test_multiples_recipe_partitions_into_panels(noun: str) -> None:
    facet = _multiples_facet("region")

    assert facet is not None, noun
    assert facet["row"]["field"] == "region"
    # Load-bearing: omitting `multiples:` produces no facet at all.
    assert _multiples_facet(None) is None


def _find_axis_orient(layer: dict) -> str | None:
    """The y-axis orient this VL layer carries, checking its own encoding and,
    recursively, any nested sub-layers. A dual-axis zero-baseline rule now
    nests one level inside the entry whose scale it shares (see
    ``emitters/_cartesian.py``'s ``nest_zero_rule``), so the base/overlay's
    own axis config can sit one level deeper than a bare top-level read."""
    axis = layer.get("encoding", {}).get("y", {}).get("axis")
    if isinstance(axis, dict) and "orient" in axis:
        return axis["orient"]
    for sub in layer.get("layer", []):
        found = _find_axis_orient(sub)
        if found is not None:
            return found
    return None


def _dual_axis_orients(position: str | None) -> tuple[str | None, str | None]:
    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    axis_y = LayerAxisYStyle(position=position) if position else None
    layer = LineLayer(type="line", y="target", axis_y=axis_y)
    chart = BarChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        layers=[layer],
    )
    spec = generate_vega_lite_spec(
        chart, _LAYER_DATA, board_style=board_style, chart_style_context=board_ctx
    )
    layers = spec["layer"]
    base_orient = _find_axis_orient(layers[0])
    overlay_orient = _find_axis_orient(layers[1])
    return base_orient, overlay_orient


@pytest.mark.parametrize("noun", ["dual_axis", "combo", "bar_and_line"])
def test_dual_axis_recipe_splits_the_y_axis(noun: str) -> None:
    base_orient, overlay_orient = _dual_axis_orients("right")

    assert base_orient is not None, noun
    assert overlay_orient is not None, noun
    assert overlay_orient == "right", noun
    assert base_orient != overlay_orient, f"{noun}: both axes on {base_orient!r}"
    # Load-bearing: omitting axis_y.position collapses both layers onto the
    # same side instead of splitting left/right.
    base_default, overlay_default = _dual_axis_orients(None)
    assert base_default is not None, noun
    assert overlay_default is not None, noun
    assert base_default == overlay_default, noun


def _spec(chart: Any, data: list[dict[str, Any]]) -> dict[str, Any]:
    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    return generate_vega_lite_spec(
        chart, data, board_style=board_style, chart_style_context=board_ctx
    )


def _transforms(spec: Any) -> list[dict[str, Any]]:
    """Every transform dict anywhere in the spec tree."""
    found: list[dict[str, Any]] = []
    if isinstance(spec, dict):
        for item in spec.get("transform", []) or []:
            if isinstance(item, dict):
                found.append(item)
        for value in spec.values():
            found += _transforms(value)
    elif isinstance(spec, list):
        for value in spec:
            found += _transforms(value)
    return found


def _marks(spec: Any, mark_type: str) -> list[dict[str, Any]]:
    """Every sub-spec drawing ``mark_type``, however deep the layering goes."""
    found: list[dict[str, Any]] = []
    if isinstance(spec, dict):
        mark = spec.get("mark")
        name = mark if isinstance(mark, str) else (mark or {}).get("type")
        if name == mark_type:
            found.append(spec)
        for value in spec.values():
            found += _marks(value, mark_type)
    elif isinstance(spec, list):
        for value in spec:
            found += _marks(value, mark_type)
    return found


def _texts(spec: Any) -> list[dict[str, Any]]:
    """Every text-mark node, with the transforms visible to it."""
    found: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], transforms: list[dict[str, Any]]) -> None:
        seen = [*transforms, *node.get("transform", [])]
        mark = node.get("mark")
        mark_type = mark.get("type") if isinstance(mark, dict) else mark
        if mark_type == "text":
            found.append({**node, "transform": seen})
        for child in node.get("layer", []):
            walk(child, seen)

    walk(spec, [])
    return found


def _shape_bar(measure: str, **style: Any) -> dict[str, Any]:
    return _spec(
        BarChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="region",
            y="revenue",
            layers=[ScatterLayer(type="scatter", y=measure)],
            style=BarChartStylePatch.model_validate(
                {"orientation": "horizontal", **style}
            ),
        ),
        _SHAPE_DATA,
    )


def test_lollipop_recipe_draws_a_stem_and_a_terminal_dot() -> None:
    """A stem, not a bar: the band fraction must reach the mark, and the dot
    must sit on the same measure the stem ends at."""
    spec = _shape_bar("revenue", marks={"bar": {"band_width": 0.06}})

    bars = _marks(spec, "bar")
    assert len(bars) == 1
    # Horizontal, so the bar's thickness is its height.
    assert bars[0]["mark"]["height"] == {"band": 0.06}, (
        "band_width must thin the bar to a stem; a full-width bar is not a "
        f"lollipop (got {bars[0]['mark']})"
    )

    points = _marks(spec, "point")
    assert len(points) == 1
    assert points[0]["encoding"]["x"]["field"] == "revenue"
    assert "y" not in points[0]["encoding"], (
        "the dot must share the bars' category band, not open its own axis"
    )


def test_lollipop_recipe_size_is_also_a_working_knob() -> None:
    """`style.marks.bar.size` is a literal fixed-pixel override on ANY scale —
    it wins outright over band_width, so it's an equally valid way to draw a
    thin lollipop stem (an exact pixel width rather than a band fraction)."""
    spec = _shape_bar("revenue", marks={"bar": {"size": 2}})

    assert _marks(spec, "bar")[0]["mark"]["height"] == 2


_BULLET_BANDS = [
    {"region": r, "band": b, "size": sz}
    for r in ("West", "East")
    for b, sz in (("Poor", 45.0), ("Satisfactory", 25.0), ("Good", 30.0))
]

# One row per category — the ranges have three, and a layer sharing their rows
# would sum each measure three times over.
_BULLET_MEASURES = [
    {"region": "West", "actual": 70.0, "goal": 80.0},
    {"region": "East", "actual": 55.0, "goal": 65.0},
]


def _bullet_spec(*, layer_query: str | None) -> dict[str, Any]:
    """The bullet recipe, rendered through the emitter so the layers can read
    their own dataset — which is the clause under test.

    ``layer_query=None`` is the mistake the recipe exists to prevent: the layers
    fall back to the ranges' rows and each measure accumulates once per band.
    """
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.spec import RenderBox
    from dbt_charts.core.render.chart.translate import translate_to_vl

    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    chart = BarChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="ranges",
        type="bar",
        x="region",
        y="size",
        color="band",
        layers=[
            BarChartBarLayer.model_validate(
                {
                    "type": "bar",
                    "y": "actual",
                    "label": "Actual",
                    "query": layer_query,
                    "style": {"marks": {"bar": {"band_width": 0.35}}},
                }
            ),
            LineLayer.model_validate(
                {
                    "type": "line",
                    "y": "goal",
                    "label": "Goal",
                    "query": layer_query,
                    "style": {"marks": {"line": {"curve": "step", "connect": False}}},
                }
            ),
        ],
        style=BarChartStylePatch.model_validate(
            {"orientation": "vertical", "stack": "zero", "stack_order": "data"}
        ),
    )
    resolved = resolve(
        chart, _BULLET_BANDS, board_ctx, datasets={"measures": _BULLET_MEASURES}
    )
    return translate_to_vl(
        BarEmitter().emit(
            resolved,
            RenderBox(width=400, height=300),
            regroup((), _BULLET_BANDS),
            datasets={"measures": _BULLET_MEASURES},
        )
    )


def _value_bar_rows(spec: Any) -> list[dict[str, Any]] | None:
    """The rows the value bar actually draws from, or None when it inherits.

    Read off the emitted spec rather than reasoned about: a layer that carries
    its own ``.data`` draws exactly those rows, and one that does not inherits
    the ranges' — three rows per category, which Vega-Lite stacks into three
    times the value.
    """
    for sub in _marks(spec, "bar"):
        if sub.get("encoding", {}).get("y", {}).get("field") != "actual":
            continue
        data = sub.get("data")
        values = data.get("values") if isinstance(data, dict) else None
        return list(values) if values is not None else None
    raise AssertionError("no value bar in the spec")


def test_bullet_recipe_draws_ranges_a_value_bar_and_a_goal_tick() -> None:
    """All three parts of a bullet graph, each reading the right rows.

    A bar plus a dot is not a bullet: the qualitative ranges behind the value
    and the perpendicular goal marker are the shape. The ranges are one row per
    (category, band), so the value and target layers need their own
    one-row-per-category source — sharing the ranges' rows stacks each measure
    once per band and the value bar runs off the top of its own scale.
    """
    spec = _bullet_spec(layer_query="measures")

    bars = _marks(spec, "bar")
    ranges = [b for b in bars if b["encoding"].get("color", {}).get("field") == "band"]
    assert ranges, "the qualitative ranges must be a color-split stacked bar"
    assert _stack_mode(ranges[0]["encoding"]) == "zero"

    # The ramp's order IS its meaning: poor, satisfactory, good. Without
    # `stack_order: data` the stack falls back to descending global sum, which
    # on these band sizes (45 / 25 / 30) renders the MIDDLE step on top.
    order_exprs = [
        str(t["calculate"])
        for t in _transforms(spec)
        if "band" in str(t.get("calculate", ""))
    ]
    assert order_exprs, "the stacked ranges must carry a series-order transform"
    ranks = {
        band: int(found[0])
        for band in ("Poor", "Satisfactory", "Good")
        if (found := re.findall(rf'"{band}" \? (\d+)', order_exprs[0]))
    }
    assert (
        ranks.get("Poor", -1) < ranks.get("Satisfactory", -1) < ranks.get("Good", -1)
    ), f"bands must stack in the query's own order, got {ranks}"

    value = [b for b in bars if b["encoding"].get("y", {}).get("field") == "actual"]
    assert len(value) == 1, "expected one value bar"
    width = value[0]["mark"]["width"]
    assert "0.35" in str(width.get("expr", "")) or width.get("band") == 0.35, (
        f"a full-width value bar covers the ranges it is read against: {width}"
    )

    goal = _marks(spec, "line")
    assert goal, "the goal marker must be drawn"


def test_bullet_recipe_value_bar_reads_its_own_value() -> None:
    """The value layer draws its measure, not its measure times the band count.

    Sharing the ranges' rows is the natural mistake — a layer inherits the base
    dataset unless `layers[].query` points it elsewhere — and the ranges are one
    row per (category, band). West's 70 then stacks into 210 against a scale the
    engine capped near 108: the bar leaves the plot and is drawn wider than the
    ranges it is meant to be read against.
    """
    own = _value_bar_rows(_bullet_spec(layer_query="measures"))
    assert own == _BULLET_MEASURES, (
        f"the value bar must draw its own one-row-per-category source: {own}"
    )

    # The mistake the clause exists to prevent, pinned so the recipe cannot
    # quietly drop it again: no own rows means the ranges' three-per-category.
    assert _value_bar_rows(_bullet_spec(layer_query=None)) is None
    assert len(_BULLET_BANDS) == 3 * len(_BULLET_MEASURES)


@pytest.mark.parametrize("noun", ["slope"])
def test_slope_recipe_draws_two_point_lines_over_a_category_pair(noun: str) -> None:
    """Two positions, not a continuous run: a temporal x would fill in the
    span between the pair and stop being a slope chart."""
    spec = _spec(
        LineChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="line",
            x="period",
            y="revenue",
            color="region",
        ),
        _SLOPE_DATA,
    )
    x_enc = _chart_pane(spec)["encoding"]["x"]

    assert x_enc["field"] == "period", noun
    assert x_enc["type"] in ("nominal", "ordinal"), (
        f"{noun}: a two-category x must not be promoted to {x_enc['type']!r}"
    )
    assert _chart_pane(spec)["encoding"]["color"]["field"] == "region", noun


@pytest.mark.parametrize("noun", ["bump"])
def test_bump_recipe_puts_rank_one_on_top(noun: str) -> None:
    """Rank ascends downward, so the domain has to run high-to-low. Without it
    the chart is upside down and the leader sits at the bottom."""
    spec = _spec(
        LineChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="line",
            x="year",
            y="rank",
            color="region",
            style=LineChartStylePatch.model_validate(
                {"axis_y": {"scale": {"continuous": {"domain": [2, 1]}}}}
            ),
        ),
        _RANK_DATA,
    )
    encoding = _chart_pane(spec)["encoding"]

    assert list(encoding["y"]["scale"]["domain"]) == [2, 1], (
        f"{noun}: expected a descending domain, got {encoding['y']['scale']['domain']}"
    )
    # Named because the recipe has to name it: the data is entity x period x
    # rank, so without a series channel the rows collide on the period key and
    # the chart raises rather than drawing.
    assert encoding["color"]["field"] == "region", noun


_LONG_DOT_DATA = [
    {"region": r, "series": s, "value": v}
    for r, vals in (("West", (40.0, 60.0, 75.0)), ("East", (35.0, 45.0, 55.0)))
    for s, v in zip(("2019", "2024", "2029"), vals, strict=True)
]


def _dot_plot(rotated: bool) -> dict[str, Any]:
    """The multi-series recipe, drawn either way round.

    Long-format rows with a color split, deliberately not `layers:` — a layer
    carries its measure on its own channel, so a rotated base leaves each layer
    opening its own axis across the category labels.
    """
    return _spec(
        ScatterChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="scatter",
            x="value" if rotated else "region",
            y="region" if rotated else "value",
            color="series",
        ),
        _LONG_DOT_DATA,
    )


@pytest.mark.parametrize("noun", ["dot_plot", "cleveland_dot_plot"])
def test_dot_plot_recipe_draws_dots_and_no_bars(noun: str) -> None:
    encoding = _chart_pane(_dot_plot(rotated=False))["encoding"]

    assert encoding["x"]["type"] in ("nominal", "ordinal"), noun
    assert encoding["y"]["field"] == "value", noun
    assert encoding["color"]["field"] == "series", noun
    assert not _marks(_dot_plot(rotated=False), "bar"), f"{noun}: draws no bars"
    assert _marks(_dot_plot(rotated=False), "point"), noun


@pytest.mark.parametrize("noun", ["dot_plot", "cleveland_dot_plot"])
def test_dot_plot_recipe_survives_rotation(noun: str) -> None:
    """Rotating is swapping the two fields, and the recipe has to hold up.

    The layered construction does not: with the category on `y`, each layer
    opens its own measure axis down the category labels. Carrying the series in
    the data instead means there are no layers to fall out of step.
    """
    spec = _dot_plot(rotated=True)
    encoding = _chart_pane(spec)["encoding"]

    assert encoding["y"]["type"] in ("nominal", "ordinal"), noun
    assert encoding["x"]["field"] == "value", noun
    assert not spec.get("layer"), (
        f"{noun}: the recipe must not need layers — that is what breaks rotation"
    )


def _bar_encodings(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Every encoding that draws a bar mark, merged with its ancestors' encodings.

    Mixed rise/fall data makes the emitter split a span into sign-filtered
    sub-layers (for corner-radius rounding) that carry only their own y/y2 —
    color and the rest of the shared encoding live on an ancestor node. A raw
    per-node read misses those; this walk merges parent encoding down first,
    matching ``_bar_encodings`` in ``tests/core/test_bar_y_start_render.py``.
    """
    found: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], inherited: dict[str, Any]) -> None:
        enc = {**inherited, **node.get("encoding", {})}
        mark = node.get("mark")
        mark_type = mark.get("type") if isinstance(mark, dict) else mark
        if mark_type == "bar":
            found.append(enc)
        for child in node.get("layer", []):
            walk(child, enc)

    walk(spec, {})
    return found


# Consistently rising (high always above low) so a dumbbell/ranged-dot/
# floating-bar span never trips the engine's sign-split (that split is the
# waterfall/candlestick shapes' own concern below, not this one's).
_SPAN_DATA = [
    {"region": "West", "low": 60.0, "high": 100.0},
    {"region": "East", "low": 40.0, "high": 90.0},
    {"region": "North", "low": 80.0, "high": 140.0},
]


def _dumbbell_spec() -> dict[str, Any]:
    return _spec(
        BarChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="region",
            y="high",
            y_start="low",
            layers=[
                ScatterLayer(type="scatter", y="high"),
                ScatterLayer(type="scatter", y="low"),
            ],
            style=BarChartStylePatch.model_validate(
                {"orientation": "horizontal", "marks": {"bar": {"band_width": 0.06}}}
            ),
        ),
        _SPAN_DATA,
    )


@pytest.mark.parametrize("noun", ["dumbbell", "barbell", "connected_dot_plot"])
def test_dumbbell_recipe_draws_a_thinned_span_and_two_end_dots(noun: str) -> None:
    """A span, not two independent bars: the band must be thinned to a stem
    and a dot must sit at each end the span connects."""
    spec = _dumbbell_spec()

    (enc,) = _bar_encodings(spec)
    assert enc["x2"] == {"field": "low"}, noun
    bars = _marks(spec, "bar")
    assert len(bars) == 1, noun
    assert bars[0]["mark"]["height"] == {"band": 0.06}, (
        f"{noun}: band_width must thin the bar to a stem, got {bars[0]['mark']}"
    )

    points = _marks(spec, "point")
    assert len(points) == 2, noun
    fields = {p["encoding"]["x"]["field"] for p in points}
    assert fields == {"high", "low"}, noun
    for point in points:
        assert "y" not in point["encoding"], (
            f"{noun}: each dot must share the bars' category band, not open its own axis"
        )


@pytest.mark.parametrize("noun", ["ranged_dot"])
def test_ranged_dot_recipe_draws_a_span_and_a_point_estimate(noun: str) -> None:
    spec = _spec(
        BarChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="region",
            y="high",
            y_start="low",
            layers=[ScatterLayer(type="scatter", y="high")],
            style=BarChartStylePatch.model_validate({"orientation": "horizontal"}),
        ),
        _SPAN_DATA,
    )

    (enc,) = _bar_encodings(spec)
    assert enc["x2"] == {"field": "low"}, noun

    points = _marks(spec, "point")
    assert len(points) == 1, noun
    assert points[0]["encoding"]["x"]["field"] == "high", noun


@pytest.mark.parametrize("noun", ["floating_bar", "range_bar"])
def test_floating_bar_recipe_spans_without_stacking_or_forcing_zero(noun: str) -> None:
    spec = _spec(
        BarChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="region",
            y="high",
            y_start="low",
            style=BarChartStylePatch.model_validate({"orientation": "vertical"}),
        ),
        _SPAN_DATA,
    )

    (enc,) = _bar_encodings(spec)
    assert enc["y2"] == {"field": "low"}, noun
    assert enc["y"].get("stack") is None, noun
    # The data (40-140) is far from zero; the recipe's whole point is that the
    # axis is not forced down to include it.
    assert enc["y"].get("scale", {}).get("zero") is not True, noun


def test_gantt_recipe_draws_date_spans_in_query_order() -> None:
    plan = [
        {
            "task": "Scope",
            "start_date": dt.date(2026, 1, 5),
            "finish_date": dt.date(2026, 1, 23),
        },
        {
            "task": "Build",
            "start_date": dt.date(2026, 2, 9),
            "finish_date": dt.date(2026, 4, 10),
        },
        {
            "task": "Design",
            "start_date": dt.date(2026, 1, 19),
            "finish_date": dt.date(2026, 2, 20),
        },
    ]
    spec = _spec(
        BarChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="task",
            y="finish_date",
            y_start="start_date",
            style=BarChartStylePatch.model_validate({"orientation": "horizontal"}),
        ),
        plan,
    )

    (enc,) = _bar_encodings(spec)
    assert enc["x"]["type"] == "temporal"
    assert enc["x2"] == {"field": "start_date"}
    assert enc["y"].get("sort") in (None, ["Scope", "Build", "Design"])


_WATERFALL_DATA = [
    {"step": "Start", "start_value": 0, "end_value": 120, "direction": "Total"},
    {"step": "Gain", "start_value": 120, "end_value": 148, "direction": "Increase"},
    {"step": "Loss", "start_value": 148, "end_value": 126, "direction": "Decrease"},
    {"step": "End", "start_value": 0, "end_value": 126, "direction": "Total"},
]


def _waterfall_spec(*, labels_visible: bool = False) -> dict[str, Any]:
    style: dict[str, Any] = {"orientation": "vertical", "overlap": "full"}
    if labels_visible:
        style["marks"] = {"bar": {"labels": {"visible": True}}}
    return _spec(
        BarChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="step",
            y="end_value",
            y_start="start_value",
            color="direction",
            style=BarChartStylePatch.model_validate(style),
        ),
        _WATERFALL_DATA,
    )


@pytest.mark.parametrize("noun", ["waterfall", "bridge_chart"])
def test_waterfall_recipe_spans_and_colors_by_direction_without_stacking(
    noun: str,
) -> None:
    spec = _waterfall_spec()

    # A rise (Start/Gain/End) and a fall (Loss) both appear in the data, so the
    # engine's sign-split rounding fires — every resulting bar still has to
    # carry the shape's own contract, not just one of them.
    encs = _bar_encodings(spec)
    assert encs, noun
    for enc in encs:
        assert enc["y2"] == {"field": "start_value"}, noun
        assert enc["color"]["field"] == "direction", noun
        assert enc["y"].get("stack") is None, noun
        # overlap: full must fill the whole band rather than splitting each
        # step into its own grouped slot.
        assert not ({"xOffset", "yOffset"} & set(enc)), noun


@pytest.mark.parametrize("noun", ["waterfall", "bridge_chart"])
def test_waterfall_recipe_labels_the_signed_change(noun: str) -> None:
    spec = _waterfall_spec(labels_visible=True)

    (text,) = _texts(spec)
    field = text["encoding"]["text"]["field"]
    (calc,) = [t for t in text["transform"] if t.get("as") == field]
    expr = calc["calculate"]
    assert "datum['end_value'] - datum['start_value']" in expr, noun
    assert "'+'" in expr and "'\\u2212'" in expr, noun


_CANDLESTICK_DATA = [
    {"session": "Mon", "low": 95.0, "high": 110.0, "open": 100.0, "close": 105.0},
    {"session": "Tue", "low": 100.0, "high": 115.0, "open": 105.0, "close": 98.0},
]


def _candlestick_spec(*, color_on_base: bool) -> dict[str, Any]:
    return _spec(
        BarChart(
            id="c",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="session",
            y="high",
            y_start="low",
            color="session" if color_on_base else None,
            layers=[
                BarChartBarLayer.model_validate(
                    {"type": "bar", "y": "close", "y_start": "open", "color": "session"}
                )
            ],
            style=BarChartStylePatch.model_validate(
                {"orientation": "vertical", "marks": {"bar": {"band_width": 0.15}}}
            ),
        ),
        _CANDLESTICK_DATA,
    )


@pytest.mark.parametrize("noun", ["candlestick", "ohlc", "stock_chart"])
def test_candlestick_recipe_draws_a_wick_and_a_body_span(noun: str) -> None:
    """The wick (low-high) is the base span; the body (open-close) is a
    second, independently-spanned bar layer — not the same bar redrawn.

    Mon closes above its open, Tue below — the body's own rise/fall mix
    trips the same sign-split as waterfall, so the wick is one merged
    encoding and the body is two (one per sign), all sharing the y2: open
    contract.
    """
    spec = _candlestick_spec(color_on_base=True)

    encs = _bar_encodings(spec)
    wick = [e for e in encs if e.get("y2") == {"field": "low"}]
    body = [e for e in encs if e.get("y2") == {"field": "open"}]
    assert len(wick) == 1, noun
    assert body, noun
    assert wick[0]["y"]["field"] == "high", noun
    assert all(e["y"]["field"] == "close" for e in body), noun


@pytest.mark.parametrize("noun", ["candlestick", "ohlc", "stock_chart"])
def test_candlestick_recipe_supports_the_ink_outline_convention(noun: str) -> None:
    """The ink-outline convention: the base wick carries no `color:` on the
    session column (a layered chart still gives it a synthetic ink-slot
    legend token, but that token is not bound to the data), while the body
    layer alone is colored by session."""
    spec = _candlestick_spec(color_on_base=False)

    encs = _bar_encodings(spec)
    wick = [e for e in encs if e.get("y2") == {"field": "low"}]
    body = [e for e in encs if e.get("y2") == {"field": "open"}]
    assert len(wick) == 1, noun
    assert wick[0].get("color", {}).get("field") != "session", noun
    assert body and all(e["color"]["field"] == "session" for e in body), noun


_RENDER_VERIFIED_NOUNS = {
    "gantt",
    "streamgraph",
    "stacked_area",
    "stacked_bar",
    "grouped_bar",
    "clustered_bar",
    "horizontal_bar",
    "column",
    "100% stacked",
    "percent_stacked_bar",
    "normalized_bar",
    "small_multiples",
    "trellis",
    "faceted",
    "dual_axis",
    "combo",
    "bar_and_line",
    "stacked_column",
    "grouped_column",
    "clustered_column",
    "row_chart",
    "vertical_bar",
    "stream_chart",
    "lollipop",
    "bullet",
    "slope",
    "bump",
    "dot_plot",
    "cleveland_dot_plot",
    "dumbbell",
    "barbell",
    "connected_dot_plot",
    "ranged_dot",
    "floating_bar",
    "range_bar",
    "waterfall",
    "bridge_chart",
    "candlestick",
    "ohlc",
    "stock_chart",
}


def test_every_recipe_is_render_verified() -> None:
    """No recipe ships without a spec assertion above proving it draws."""
    assert set(_CHART_SHAPE_RECIPES) == _RENDER_VERIFIED_NOUNS


def test_recipes_stay_out_of_the_type_enum() -> None:
    """A recipe names a composition; promoting one to a `type:` tag is a separate
    decision, gated on the measurement in the task that added this map."""
    assert not set(_CHART_SHAPE_RECIPES) & set(get_valid_chart_types())


# Nouns that name the same chart and must therefore hand out the same recipe.
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("streamgraph", "stream_chart"),
    ("grouped_bar", "clustered_bar"),
    ("grouped_column", "clustered_column"),
    ("horizontal_bar", "row_chart"),
    ("column", "vertical_bar"),
    ("small_multiples", "trellis", "faceted"),
    ("dual_axis", "combo", "bar_and_line"),
    ("dot_plot", "cleveland_dot_plot"),
    ("dumbbell", "barbell", "connected_dot_plot"),
    ("floating_bar", "range_bar"),
    ("waterfall", "bridge_chart"),
    ("candlestick", "ohlc", "stock_chart"),
)


@pytest.mark.parametrize("group", _SYNONYM_GROUPS, ids=lambda group: group[0])
def test_synonym_nouns_hand_out_their_primary_recipe(group: tuple[str, ...]) -> None:
    """A synonym exists so a different word finds the same chart.

    The spec assertions above prove one recipe per shape; they run again under
    each synonym without reading which noun they ran for, so an edited synonym
    string would sail past them. Requiring the group to agree is what catches it.
    """
    recipes = {noun: _CHART_SHAPE_RECIPES[noun] for noun in group}
    assert len(set(recipes.values())) == 1, (
        f"synonyms disagree, so one of them is unverified: {recipes}"
    )


@pytest.mark.parametrize(
    ("authored", "expected_key"),
    [
        ("bullet", "bullet"),
        ("bullet graph", "bullet"),
        ("bullet chart", "bullet"),
        ("Bullet Graph", "bullet"),
        ("lollipop chart", "lollipop"),
        ("lollipop-chart", "lollipop"),
        ("slope chart", "slope"),
        ("bump chart", "bump"),
        # Its own last word is a generic tail, and it must not fold to "dot".
        ("dot plot", "dotplot"),
        ("Cleveland dot plot", "clevelanddotplot"),
        ("dumbbell chart", "dumbbell"),
        ("barbell graph", "barbell"),
        ("ranged dot plot", "rangeddot"),
        ("connected dot plot", "connecteddotplot"),
        ("floating bar chart", "floatingbar"),
        ("range bar graph", "rangebar"),
        ("waterfall chart", "waterfall"),
        ("bridge chart", "bridgechart"),
        ("candlestick chart", "candlestick"),
        ("stock chart", "stockchart"),
        ("gantt chart", "gantt"),
    ],
)
def test_shape_nouns_resolve_however_they_are_spelled(
    authored: str, expected_key: str
) -> None:
    """Nobody types the bare noun. An author asking for a "bullet graph" and one
    asking for a "bullet chart" have asked the same question, and a table keyed
    on "bullet" alone answers neither."""
    keys = _shape_noun_keys(authored)
    hit = next((k for k in keys if k in _RECIPE_BY_NORMALIZED_NOUN), None)

    assert hit == expected_key, f"{authored!r} resolved to {hit!r}"


@pytest.mark.parametrize(
    "authored",
    ["sankey diagram", "treemap chart", "gauge chart", "radar chart", "violin plot"],
)
def test_unsupported_nouns_resolve_however_they_are_spelled(authored: str) -> None:
    keys = _shape_noun_keys(authored)

    assert any(k in _NORMALIZED_UNSUPPORTED_SHAPES for k in keys), authored


def test_generic_tail_stripping_never_invents_a_match() -> None:
    """The fallback only ever reaches an entry that already exists.

    `box plot` must stay unknown rather than folding onto a `box` entry — the
    strip is a spelling tolerance, not a fuzzy matcher.
    """
    keys = _shape_noun_keys("box plot")

    assert not any(k in _RECIPE_BY_NORMALIZED_NOUN for k in keys)
    assert not any(k in _NORMALIZED_UNSUPPORTED_SHAPES for k in keys)
