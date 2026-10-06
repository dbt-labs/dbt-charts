"""Regression: horizontal 100% stacked bars must pin stack on the measure axis.

When ``style.orientation: horizontal`` is combined with ``style.stack: normalize``,
the orientation routing makes encoding.x the measure axis and encoding.y the
categorical axis. The stack mode must land on the measure encoding (encoding.x
for horizontal, encoding.y for vertical) — putting it on the categorical
encoding leaves VL with no stacking instruction on the measure axis, so:

  - bars don't normalize to [0, 1] and clip past the manually-pinned domain,
    showing as solid full-width rectangles with no visible segments;
  - the auto-percent format VL applies when ``stack: normalize`` sits on the
    quantitative axis never fires, so the measure axis renders raw decimals
    (0.0, 0.1, ...) instead of percentages;
  - the categorical axis carries an unsupported ``stack: normalize`` field
    that causes VL to render every category label as ``NaN%``.

The vertical 100% case must continue to pin stack on encoding.y (no regression).
"""

from __future__ import annotations

import xml.etree.ElementTree as element_tree

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
)
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    WIDE_LABEL_FIELD,
    WIDE_VALUE_FIELD,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_style(get_theme_style())


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


_DATA = [
    {"priority": "high", "status": "new", "ticket_count": 10},
    {"priority": "high", "status": "solved", "ticket_count": 8},
    {"priority": "low", "status": "new", "ticket_count": 5},
    {"priority": "low", "status": "solved", "ticket_count": 3},
    {"priority": "normal", "status": "new", "ticket_count": 7},
    {"priority": "normal", "status": "solved", "ticket_count": 4},
    {"priority": "urgent", "status": "new", "ticket_count": 6},
    {"priority": "urgent", "status": "solved", "ticket_count": 2},
]


def _spec(orientation: str | None, stack: str | None) -> dict:
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="priority",
        y="ticket_count",
        color="status",
        style=BarChartStylePatch(orientation=orientation, stack=stack),
    )
    return generate_vega_lite_spec(chart, _DATA, width=400, height=300)


def _chart_pane_encoding(spec: dict) -> dict:
    """Pull the bar layer's encoding out, regardless of vconcat/hconcat wrapping.

    Horizontal-bar specs may be wrapped in a ``vconcat`` (for the endpoint-label
    rail) or render flat. The bar encoding always lives on the chart pane —
    either at ``spec["encoding"]`` directly, or under ``spec["vconcat"][1]``
    when the rail wraps the chart.
    """
    if "vconcat" in spec:
        return spec["vconcat"][1]["encoding"]
    if "hconcat" in spec:
        return spec["hconcat"][0]["encoding"]
    return spec["encoding"]


class TestHorizontal100StackOnMeasureAxis:
    """horizontal_100: stack=normalize must land on encoding.x (the measure axis)."""

    def test_stack_normalize_lands_on_measure_x(self):
        spec = _spec(orientation="horizontal", stack="normalize")
        enc = _chart_pane_encoding(spec)
        assert enc["x"].get("stack") == "normalize", (
            "horizontal stack=normalize must land on encoding.x (the measure "
            "axis after orientation routing); VL stacks bars across this axis "
            "and auto-applies percent format from this property"
        )

    def test_categorical_y_does_not_carry_stack(self):
        spec = _spec(orientation="horizontal", stack="normalize")
        enc = _chart_pane_encoding(spec)
        assert "stack" not in enc["y"], (
            "horizontal: encoding.y is the categorical axis after routing; "
            "stack on a nominal encoding is meaningless and triggers VL's "
            "NaN% rendering of category labels"
        )

    def test_measure_x_scale_pins_domain_max_to_one_when_rail_wraps(self):
        """The rail-wrap helper explicitly pins x.scale.domain=[0,1] for
        normalize so the shared x-scale propagates correctly to the rail pane.

        Without the rail wrapper VL auto-derives [0,1] from
        ``encoding.x.stack: "normalize"`` — that's covered by the
        stack-on-measure assertion. The explicit pin only fires when the
        chart is wrapped in vconcat with the top-row rail.
        """
        chart = BarChart(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="priority",
            y="ticket_count",
            color="status",
            style=BarChartStylePatch(
                orientation="horizontal",
                stack="normalize",
                endpoint_labels={"visible": True},
            ),
        )
        spec = generate_vega_lite_spec(chart, _DATA, width=400, height=300)
        chart_pane_encoding = spec["vconcat"][1]["encoding"]
        scale = chart_pane_encoding["x"].get("scale", {})
        domain = scale.get("domain")
        assert domain == [0, 1] or scale.get("domainMax") == 1, (
            f"expected x-scale pinned to [0, 1] for horizontal normalize "
            f"with rail wrap; got scale={scale!r}"
        )

    def test_categorical_y_axis_has_no_quantitative_format(self):
        spec = _spec(orientation="horizontal", stack="normalize")
        enc = _chart_pane_encoding(spec)
        y_axis = enc["y"].get("axis", {}) or {}
        assert "format" not in y_axis or not y_axis.get("format"), (
            f"horizontal: encoding.y is the categorical axis; quantitative "
            f"format on it produces NaN% on string category values. "
            f"got y.axis.format={y_axis.get('format')!r}"
        )

    def test_z_order_pin_present(self):
        spec = _spec(orientation="horizontal", stack="normalize")
        enc = _chart_pane_encoding(spec)
        order = enc.get("order", {})
        assert order.get("field") == "__df_series_order"
        assert order.get("sort") == "ascending"
        assert "aggregate" not in order

    def test_endpoint_label_rail_data_is_emitted(self):
        chart = BarChart(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="priority",
            y="ticket_count",
            color="status",
            style=BarChartStylePatch(
                orientation="horizontal",
                stack="normalize",
                endpoint_labels={"visible": True},
            ),
        )
        spec = generate_vega_lite_spec(chart, _DATA, width=400, height=300)
        assert "vconcat" in spec, (
            "horizontal stacked + endpoint_labels.visible must wrap the spec "
            "in vconcat with the top-row label rail above the chart pane"
        )
        rail_pane = spec["vconcat"][0]
        rail_data = rail_pane.get("data", {}).get("values") or []
        assert rail_data, (
            "label rail must emit one data row per series at the top "
            "categorical row; got empty rail data"
        )
        # Two series in the data → two rail labels (one per series at top row
        # midpoint).
        assert len(rail_data) == 2
        assert {row["status"] for row in rail_data} == {"new", "solved"}


class TestVertical100StackUnchanged:
    """Regression: vertical 100% must continue to pin stack on encoding.y.

    The vertical path is the working reference; the refactor must not regress
    it.
    """

    def test_stack_normalize_lands_on_measure_y(self):
        spec = _spec(orientation="vertical", stack="normalize")
        enc = _chart_pane_encoding(spec)
        assert enc["y"].get("stack") == "normalize"

    def test_categorical_x_does_not_carry_stack(self):
        spec = _spec(orientation="vertical", stack="normalize")
        enc = _chart_pane_encoding(spec)
        assert "stack" not in enc["x"]

    def test_z_order_pin_still_present(self):
        spec = _spec(orientation="vertical", stack="normalize")
        enc = _chart_pane_encoding(spec)
        order = enc.get("order", {})
        assert order.get("field") == "__df_series_order"
        assert order.get("sort") == "ascending"
        assert "aggregate" not in order


class TestStackFalsePinsOnMeasureAxisRegardlessOfOrientation:
    """``stack: false`` (grouped) emits ``stack: null`` on the measure axis.

    Same orientation symmetry as normalize — the disable must apply to the
    measure encoding so VL actually disables stacking on the right axis.
    """

    def test_horizontal_stack_false_disables_on_measure_x(self):
        chart = BarChart(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="priority",
            y="ticket_count",
            color="status",
            stack="none",
            style=BarChartStylePatch(orientation="horizontal"),
        )
        spec = generate_vega_lite_spec(chart, _DATA, width=400, height=300)
        enc = _chart_pane_encoding(spec)
        assert enc["x"].get("stack") is None
        assert "stack" not in enc["y"]

    def test_vertical_stack_none_disables_on_measure_y(self):
        chart = BarChart(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="priority",
            y="ticket_count",
            color="status",
            stack="none",
            style=BarChartStylePatch(orientation="vertical"),
        )
        spec = generate_vega_lite_spec(chart, _DATA, width=400, height=300)
        enc = _chart_pane_encoding(spec)
        assert enc["y"].get("stack") is None
        assert "stack" not in enc["x"]


_LAYERED_DATA = [
    {"month": "Jan", "revenue": 100, "expenses": 80},
    {"month": "Feb", "revenue": 120, "expenses": 90},
    {"month": "Mar", "revenue": 110, "expenses": 85},
]


_WIDE_STACK_DATA = [
    {"month": "Jan", "named": 10, "non_named": 30},
    {"month": "Feb", "named": 20, "non_named": 40},
]
# The long-form "series" values are spelled with a space, not an underscore,
# on purpose: a wide chart's legend/scale domain is now the HUMANIZED measure
# name (field_title("non_named") == "non named"), so this is what a
# long-form color: chart's raw data would have to say to render byte-identical
# legend text -- the geometry-parity tests below compare rendered SVG paths,
# which shift a hair when the legend text (and thus its pixel width) differs.
_LONG_STACK_DATA = [
    {"month": "Jan", "series": "named", "amount": 10},
    {"month": "Jan", "series": "non named", "amount": 30},
    {"month": "Feb", "series": "named", "amount": 20},
    {"month": "Feb", "series": "non named", "amount": 40},
]


def _rendered_paths(
    spec: dict, *, fill_opacity: str | None = None
) -> list[tuple[str, str]]:
    vl_convert = pytest.importorskip("vl_convert")
    root = element_tree.fromstring(vl_convert.vegalite_to_svg(spec))
    paths = []
    for path in root.iter("{http://www.w3.org/2000/svg}path"):
        if not path.attrib.get("fill") or not path.attrib.get("d"):
            continue
        if fill_opacity is not None and path.attrib.get("fill-opacity") != fill_opacity:
            continue
        paths.append((path.attrib["d"], path.attrib["fill"]))
    return paths


def _stack_chart(
    chart_class,
    chart_type: str,
    y,
    color: str | None,
    stack: str,
    *,
    endpoint_labels: bool = False,
) -> object:
    return chart_class(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type=chart_type,
        x="month",
        y=y,
        color=color,
        stack=stack,
        style={"endpoint_labels": {"visible": endpoint_labels}},
    )


class TestLayersStackNormalize:
    """Wide-form measures share one folded unit spec, so VL can stack them."""

    @pytest.mark.parametrize(
        ("chart_class", "chart_type"), [(BarChart, "bar"), (AreaChart, "area")]
    )
    def test_wide_measure_normalize_uses_one_folded_measure_axis(
        self, chart_class, chart_type
    ):
        chart = chart_class(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type=chart_type,
            x="month",
            y=["revenue", "expenses"],
            stack="normalize",
            style={"endpoint_labels": {"visible": False}},
        )
        spec = generate_vega_lite_spec(chart, _LAYERED_DATA, width=400, height=300)
        chart_pane = spec.get("hconcat", [spec])[0]
        assert any(
            transform.get("fold") == ["revenue", "expenses"]
            for transform in chart_pane.get("transform", [])
        )
        measure_enc = next(
            encoding
            for encoding in chart_pane["encoding"].values()
            if encoding.get("field") == WIDE_VALUE_FIELD
        )
        assert measure_enc.get("stack") == "normalize"
        assert measure_enc["field"] == WIDE_VALUE_FIELD
        assert chart_pane["encoding"]["color"]["field"] == WIDE_LABEL_FIELD

    def test_wide_measure_series_labels_are_humanized(self) -> None:
        """A wide chart's color domain is field_title(measure), not
        the raw column name -- matching the humanized text a long-form
        color: chart of equivalent data would need for byte-identical
        rendering (see _LONG_STACK_DATA's own comment)."""
        wide = _stack_chart(BarChart, "bar", ["named", "non_named"], None, "zero")
        spec = generate_vega_lite_spec(wide, _WIDE_STACK_DATA, width=400, height=300)

        assert spec["encoding"]["color"]["scale"]["domain"] == [
            "non named",
            "named",
        ]


def test_wide_zero_stack_uses_summed_measure_domain() -> None:
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y=["revenue", "expenses"],
        stack="zero",
        style={"endpoint_labels": {"visible": False}},
    )
    spec = generate_vega_lite_spec(chart, _LAYERED_DATA, width=400, height=300)
    chart_pane = spec.get("hconcat", [spec])[0]
    measure_enc = next(
        encoding
        for encoding in chart_pane["encoding"].values()
        if encoding.get("field") == WIDE_VALUE_FIELD
    )
    assert measure_enc["scale"]["domainMax"] >= 210


def test_wide_bar_without_x_fails_instead_of_dropping_every_encoding() -> None:
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        y=["named", "non_named"],
    )
    with pytest.raises(ChartDataError, match="x field"):
        generate_vega_lite_spec(
            chart,
            [{"named": 10, "non_named": 30}],
            width=400,
            height=300,
        )


@pytest.mark.parametrize("stack", ["zero", "none"])
def test_wide_bar_renders_the_same_segments_as_long_form(stack: str) -> None:
    from dbt_charts.core.render.chart.features.bar_hover_band import hover_band_layers

    wide = _stack_chart(BarChart, "bar", ["named", "non_named"], None, stack)
    long = _stack_chart(BarChart, "bar", "amount", "series", stack)
    wide_spec = generate_vega_lite_spec(wide, _WIDE_STACK_DATA, width=400, height=300)
    long_spec = generate_vega_lite_spec(long, _LONG_STACK_DATA, width=400, height=300)

    # Wide-form bars are excluded from BarHoverBandFeature (y is a list, not a scalar).
    # Long-form bars get a hover band.  Strip the band from both sides before comparing
    # rendered paths -- the band is an implementation detail neither authoring surface
    # authors see; the parity contract is about visible segment geometry only.
    def _strip_band(spec: dict) -> dict:
        layers = spec.get("layer")
        if not layers:
            return spec
        band_ids = {id(la) for la in hover_band_layers(iter(layers))}
        return {**spec, "layer": [la for la in layers if id(la) not in band_ids]}

    assert _rendered_paths(_strip_band(wide_spec)) == _rendered_paths(
        _strip_band(long_spec)
    )


@pytest.mark.parametrize("stack", ["normalize", "none"])
def test_wide_area_renders_identically_to_long_form_with_endpoint_labels(
    stack: str,
) -> None:
    wide = _stack_chart(
        AreaChart,
        "area",
        ["named", "non_named"],
        None,
        stack,
        endpoint_labels=True,
    )
    long = _stack_chart(
        AreaChart,
        "area",
        "amount",
        "series",
        stack,
        endpoint_labels=True,
    )
    wide_spec = generate_vega_lite_spec(wide, _WIDE_STACK_DATA, width=400, height=300)
    long_spec = generate_vega_lite_spec(long, _LONG_STACK_DATA, width=400, height=300)
    assert "hconcat" in wide_spec
    assert "hconcat" in long_spec
    # Segment geometry (position/size/color) must match exactly. DOM/paint
    # SEQUENCE need not: for "none" (unstacked/overlap), wide now folds in
    # display_order (last-value order, matching an authored color: chart's
    # own intended front-to-back sequence per _area_spatial_order) rather
    # than authored y: list order -- see fold_wide_measures's docstring.
    # Comparing sorted() isolates the geometry-parity invariant this test
    # actually cares about from that intentional, unrelated reordering.
    assert sorted(_rendered_paths(wide_spec, fill_opacity="1")) == sorted(
        _rendered_paths(long_spec, fill_opacity="1")
    )


@pytest.mark.parametrize(
    ("chart_class", "chart_type"),
    [(BarChart, "bar"), (LineChart, "line"), (AreaChart, "area")],
)
def test_wide_form_duplicate_categories_raise(chart_class, chart_type) -> None:
    chart = chart_class(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type=chart_type,
        x="month",
        y=["revenue", "expenses"],
    )
    duplicated = [
        {"month": "Jan", "revenue": 10, "expenses": 30},
        {"month": "Jan", "revenue": 20, "expenses": 40},
    ]
    with pytest.raises(ChartDataError, match="duplicate"):
        generate_vega_lite_spec(chart, duplicated, width=400, height=300)
