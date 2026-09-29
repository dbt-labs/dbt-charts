"""A layer must follow the base chart's orientation, not assume a vertical one.

`render_cartesian_overlay` hoists the base spec's category encoding as the outer
shared channel and builds every layer's measure on the opposite one. Which VL
channel each of those is depends entirely on the base chart's orientation: a
vertical bar puts the category on `x` and the measure on `y`, a horizontal bar
swaps them. Reading the pair as a literal `("x", "y")` shared the *measure* with
every layer on a horizontal base and bound the layer's own field to a second,
unmerged y scale — a chart that validated clean and plotted every layer point at
the base's value.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.authored import (
    AreaLayer,
    BarLayer,
    LayerAxisYStyle,
    LineLayer,
    ScatterLayer,
)
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_DATA = [
    {"month": "Jan", "revenue": 100.0, "target": 4000.0},
    {"month": "Feb", "revenue": 200.0, "target": 5000.0},
    {"month": "Mar", "revenue": 150.0, "target": 4500.0},
]


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_config()
    yield
    reset_config()


def _layered_spec(orientation: str) -> dict[str, Any]:
    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    chart = BarChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        layers=[ScatterLayer(type="scatter", y="target")],
        style=BarChartStylePatch(orientation=orientation),
    )
    return generate_vega_lite_spec(
        chart, _DATA, board_style=board_style, chart_style_context=board_ctx
    )


def _overlay_encoding(spec: dict[str, Any]) -> dict[str, Any]:
    """The scatter overlay's own encoding, found by mark rather than by index.

    Slot 0 is the base bar and the trailing slots are engine-owned (a baseline
    rule, value-label text); indexing past the base would pin this test to that
    running order instead of to the overlay it is about.
    """
    points = [
        layer
        for layer in spec["layer"]
        if (layer["mark"] if isinstance(layer["mark"], str) else layer["mark"]["type"])
        == "point"
    ]
    assert len(points) == 1, f"expected one point overlay, got {len(points)}"
    return points[0]["encoding"]


@pytest.mark.parametrize(
    ("orientation", "category_channel", "measure_channel"),
    [("vertical", "x", "y"), ("horizontal", "y", "x")],
)
def test_layer_measures_on_the_base_s_measure_channel(
    orientation: str, category_channel: str, measure_channel: str
) -> None:
    """The layer's own field lands on whichever channel the base measures on."""
    spec = _layered_spec(orientation)

    assert spec["encoding"][category_channel]["field"] == "month", (
        "the shared outer channel must be the category, not the base's measure"
    )
    assert category_channel not in _overlay_encoding(spec), (
        "the overlay must inherit the shared category, not redeclare it"
    )
    assert _overlay_encoding(spec)[measure_channel]["field"] == "target"


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_layer_shares_one_measure_scale_with_the_base(orientation: str) -> None:
    """Base and layer measure against ONE scale, and the layer never opens its
    own axis on the category channel.

    Reading the base spec alone would not show this — the overlay never
    rewrites it. What changes is whether the layer redeclares the category
    channel with a quantitative field of its own, which is what Vega-Lite turns
    into a second ruler drawn over the category names.
    """
    spec = _layered_spec(orientation)
    measure_channel = "y" if orientation == "vertical" else "x"
    category_channel = "x" if orientation == "vertical" else "y"
    overlay = _overlay_encoding(spec)

    assert overlay[measure_channel]["field"] == "target"
    assert category_channel not in overlay, (
        f"{orientation}: the layer opened its own scale on the category "
        f"channel: {overlay}"
    )
    assert spec["layer"][0]["encoding"][measure_channel]["field"] == "revenue"
    assert spec.get("resolve", {}).get("scale", {}).get(measure_channel) != (
        "independent"
    ), "base and layer must share one measure scale when no layer pins a side"


def _horizontal_with(layer: Any) -> None:
    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    chart = BarChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        layers=[layer],
        style=BarChartStylePatch(orientation="horizontal"),
    )
    generate_vega_lite_spec(
        chart, _DATA, board_style=board_style, chart_style_context=board_ctx
    )


def test_layer_axis_position_on_a_horizontal_base_says_so() -> None:
    """`axis_y.position` names a left/right side; a horizontal base measures
    along VL x, whose sides are top and bottom. Mapping one onto the other is a
    design decision, so this raises rather than drawing the layer against a side
    nobody asked for."""
    with pytest.raises(ChartDataError, match="axis_y.position"):
        _horizontal_with(
            LineLayer(type="line", y="target", axis_y=LayerAxisYStyle(position="right"))
        )


def test_layer_step_curve_on_a_horizontal_base_says_so() -> None:
    """The step transform's band offset is `xOffset`, sized by `bandwidth('x')`
    — the channel a horizontal base measures on, not the one it bands."""
    with pytest.raises(ChartDataError, match="curve: step"):
        _horizontal_with(
            LineLayer.model_validate(
                {
                    "type": "line",
                    "y": "target",
                    "style": {"marks": {"line": {"curve": "step"}}},
                }
            )
        )


def _layered_bar_spec(
    orientation: str, *, labels: bool = False, layer_type: str = "bar"
) -> dict[str, Any]:
    """A layer over a bar base, labeled on request.

    ``layer_type`` matters: a bar layer's labels come from
    ``_build_bar_text_layer`` while line/area/scatter route through
    ``build_line_text_layers`` / ``_build_point_text_layer``. They are separate
    builders and each needs its own channel pinned.
    """
    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    # A layer's labels are authored on the LAYER's own mark style — the chart's
    # marks slot only carries its own family's.
    # area authors its labels on the `line` mark slot, same as line.
    layer_mark = {"bar": "bar", "line": "line", "area": "line", "scatter": "point"}[
        layer_type
    ]
    layer_body: dict[str, Any] = {"type": layer_type, "y": "target"}
    if labels:
        layer_body["style"] = {"marks": {layer_mark: {"labels": {"visible": True}}}}
    layer_cls = {
        "bar": BarLayer,
        "line": LineLayer,
        "area": AreaLayer,
        "scatter": ScatterLayer,
    }[layer_type]
    chart = BarChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        layers=[layer_cls.model_validate(layer_body)],
        style=BarChartStylePatch.model_validate({"orientation": orientation}),
    )
    return generate_vega_lite_spec(
        chart, _DATA, board_style=board_style, chart_style_context=board_ctx
    )


def _bar_marks(spec: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(spec, dict):
        mark = spec.get("mark")
        name = mark if isinstance(mark, str) else (mark or {}).get("type")
        if name == "bar":
            found.append(spec)
        for value in spec.values():
            found += _bar_marks(value)
    elif isinstance(spec, list):
        for value in spec:
            found += _bar_marks(value)
    return found


@pytest.mark.parametrize(
    ("orientation", "thickness_key"), [("vertical", "width"), ("horizontal", "height")]
)
def test_bar_layer_takes_its_thickness_from_the_category_channel(
    orientation: str, thickness_key: str
) -> None:
    """A bar layer is sized across the category, which swaps with the base.

    Sized on the wrong axis a bar layer spans the measure instead of the band —
    and this is the combination a bullet chart is built from, so it cannot go
    unpinned.
    """
    bars = _bar_marks(_layered_bar_spec(orientation))

    assert len(bars) == 2, f"expected a base bar and a layer bar, got {len(bars)}"
    for bar in bars:
        assert thickness_key in bar["mark"], (
            f"{orientation}: bar sized on the wrong axis: {bar['mark']}"
        )


@pytest.mark.parametrize("layer_type", ["bar", "line", "area", "scatter"])
@pytest.mark.parametrize(
    ("orientation", "measure_channel"), [("vertical", "y"), ("horizontal", "x")]
)
def test_layer_value_labels_measure_on_the_base_s_measure_channel(
    orientation: str, measure_channel: str, layer_type: str
) -> None:
    """A layer's own value labels follow the base's channel pair.

    Pinned to VL `y`, a layer label lands on the CATEGORY axis of a horizontal
    base and opens a second quantitative scale across the category names — the
    label then reads against a ruler that describes nothing.
    """
    spec = _layered_bar_spec(orientation, labels=True, layer_type=layer_type)

    texts: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            mark = node.get("mark")
            name = mark if isinstance(mark, str) else (mark or {}).get("type")
            if name == "text" and isinstance(node.get("encoding"), dict):
                texts.append(node["encoding"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(spec)
    layer_labels = [t for t in texts if "target" in str(t)]
    assert layer_labels, "the layer's own value labels must render"
    for enc in layer_labels:
        assert enc.get(measure_channel, {}).get("field") == "target", (
            f"{orientation}/{layer_type}: label on the wrong channel: {enc}"
        )


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_layered_base_series_is_named_by_its_measure(orientation: str) -> None:
    """The base's legend entry names the MEASURE, on either orientation.

    A horizontal base draws its measure on VL x, so the plain title to read is
    the x one — `y_plain` there is the category, and the base series would be
    labeled `month`. Nothing else asserts this: a horizontal layered chart
    only gained a base legend entry once base and layers began sharing one
    measure scale.
    """
    spec = _layered_spec(orientation)

    domains: list[list[str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            scale = node.get("scale")
            if isinstance(scale, dict) and isinstance(scale.get("domain"), list):
                domains.append([str(v) for v in scale["domain"]])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(spec)
    named = {name for domain in domains for name in domain}
    assert "revenue" in named, (
        f"{orientation}: base series must be named by its measure, got {sorted(named)}"
    )
    assert "month" not in named, (
        f"{orientation}: base series named by its category axis, got {sorted(named)}"
    )


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_axis_mirror_is_refused_on_a_layered_chart_either_way_round(
    orientation: str,
) -> None:
    """`layers:` moves the measure onto the sublayers, so mirror has nothing to
    mirror — on both orientations.

    The guard used to read "no outer y encoding" as its proxy for that. A
    horizontal base leaves its CATEGORY on the outer y, so the proxy stopped
    holding the moment the overlay began hoisting the category channel, and the
    refusal silently lapsed into mirroring the category axis instead.
    """
    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    chart = BarChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        layers=[LineLayer(type="line", y="target")],
        style=BarChartStylePatch.model_validate(
            {"orientation": orientation, "axis_y": {"mirror": True}}
        ),
    )
    with pytest.raises(ChartDataError, match="mirror"):
        generate_vega_lite_spec(
            chart, _DATA, board_style=board_style, chart_style_context=board_ctx
        )


@pytest.mark.parametrize(
    ("orientation", "category_channel", "measure_channel"),
    [("vertical", "x", "y"), ("horizontal", "y", "x")],
)
def test_layer_authoring_its_own_x_keeps_its_measure(
    orientation: str, category_channel: str, measure_channel: str
) -> None:
    """A layer's own `x` is its CATEGORY, and must not land on the measure.

    The layer encoding is built measure-first and then takes its category. Written
    to the literal `"x"`, that second write lands on the measure channel of a
    horizontal base and overwrites it — the layer then draws against no measure at
    all. Ordinary authoring: `style.orientation: horizontal` with
    `layers: [{type: line, x: month, y: target}]`.
    """
    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    chart = BarChart(
        id="c",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        layers=[LineLayer(type="line", x="month", y="target")],
        style=BarChartStylePatch.model_validate({"orientation": orientation}),
    )
    spec = generate_vega_lite_spec(
        chart, _DATA, board_style=board_style, chart_style_context=board_ctx
    )

    encodings: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            enc = node.get("encoding")
            if isinstance(enc, dict) and enc.get(measure_channel, {}).get("field") == (
                "target"
            ):
                encodings.append(enc)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(spec)
    assert encodings, (
        f"{orientation}: the layer's measure was overwritten — no encoding carries "
        f"'target' on {measure_channel!r}"
    )
    for enc in encodings:
        assert enc.get(category_channel, {}).get("field") == "month", (
            f"{orientation}: the layer's own x must land on {category_channel!r}: {enc}"
        )
