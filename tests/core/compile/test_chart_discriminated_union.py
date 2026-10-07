"""TDD smoke tests for per-family discriminated AuthoredChart union.

Phase 1 of the split-AuthoredChart task. These tests drive the implementation of:
- Per-family Patch classes (BarChart, KpiChart, PieChart, etc.)
- AuthoredChart as a Discriminator("type") union alias (not a BaseModel)
- Structural enforcement: extra fields rejected, required fields enforced
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.chart.authored import (
    AreaChart,
    AuthoredChart,
    BarChart,
    CalloutChart,
    GeoshapeChart,
    HeatmapChart,
    KpiChart,
    LineChart,
    LineLayer,
    PieChart,
    PointMapChart,
    ScatterChart,
    SparkBarChart,
    TableChart,
)


def test_bar_patch_validates():
    p = BarChart(type="bar", x="month", y="revenue")
    assert p.type == "bar"
    assert p.x == "month"


def test_bar_patch_rejects_kpi_field():
    """KPI-only field value: is extra-forbidden on BarChart."""
    with pytest.raises(ValidationError):
        BarChart(type="bar", value="x")  # type: ignore[call-arg]


def test_bar_patch_rejects_theta():
    """Pie-only field theta: is extra-forbidden on BarChart."""
    with pytest.raises(ValidationError):
        BarChart(type="bar", theta="slice")  # type: ignore[call-arg]


def test_kpi_patch_requires_value():
    """KpiChart.value is required (structural, not a validator)."""
    with pytest.raises(ValidationError, match="value"):
        KpiChart(type="kpi")  # type: ignore[call-arg]


def test_kpi_patch_accepts_required_value():
    p = KpiChart(type="kpi", value="revenue")
    assert p.value == "revenue"


def test_pie_patch_requires_theta():
    """PieChart.theta is required (structural)."""
    with pytest.raises(ValidationError, match="theta"):
        PieChart(type="pie")  # type: ignore[call-arg]


def test_pie_patch_rejects_x():
    """x is not declared on PieChart — extra forbidden."""
    with pytest.raises(ValidationError):
        PieChart(type="pie", theta="amount", x="region")  # type: ignore[call-arg]


def test_callout_patch_requires_message():
    """CalloutChart.message is required (structural)."""
    with pytest.raises(ValidationError, match="message"):
        CalloutChart(type="callout")  # type: ignore[call-arg]


def test_chartpatch_union_dispatches_to_bar():
    adapter = TypeAdapter(AuthoredChart)
    result = adapter.validate_python({"type": "bar", "x": "month", "y": "revenue"})
    assert isinstance(result, BarChart)


def test_chartpatch_union_dispatches_to_kpi():
    adapter = TypeAdapter(AuthoredChart)
    result = adapter.validate_python({"type": "kpi", "value": "rev", "query": "q"})
    assert isinstance(result, KpiChart)


def test_chartpatch_union_dispatches_to_pie():
    adapter = TypeAdapter(AuthoredChart)
    result = adapter.validate_python({"type": "pie", "theta": "amount"})
    assert isinstance(result, PieChart)


# --- Regression tests for type: enforcement ---


def test_chartpatch_union_rejects_unknown_type():
    """Unknown type raises ValidationError — no fallback catch-all class exists."""
    adapter = TypeAdapter(AuthoredChart)
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "fuzzbomp"})


def test_chartpatch_union_rejects_missing_type():
    """Missing type: raises ValidationError — type is mandatory."""
    adapter = TypeAdapter(AuthoredChart)
    with pytest.raises(ValidationError):
        adapter.validate_python({})


def test_line_patch_rejects_size():
    """size is structurally impossible on LineChart — extra_forbidden."""
    with pytest.raises(ValidationError):
        LineChart(type="line", x="m", y="v", size="col")  # type: ignore[call-arg]


def test_pie_patch_rejects_conditional_formatting():
    """conditional_formatting is retired on pie — extra_forbidden."""
    with pytest.raises(ValidationError, match="conditional_formatting"):
        PieChart(
            type="pie",
            theta="revenue",
            conditional_formatting={
                "revenue": {"when": [{"gt": 100, "background": "#ff0000"}]}
            },
        )  # type: ignore[call-arg]


def test_donut_patch_rejects_conditional_formatting():
    """donut dispatches to PieChart — same retirement as pie."""
    with pytest.raises(ValidationError, match="conditional_formatting"):
        PieChart(
            type="donut",
            theta="revenue",
            conditional_formatting={
                "revenue": {"when": [{"gt": 100, "background": "#ff0000"}]}
            },
        )  # type: ignore[call-arg]


def test_geoshape_patch_rejects_conditional_formatting():
    """conditional_formatting is retired on geoshape — extra_forbidden."""
    with pytest.raises(ValidationError, match="conditional_formatting"):
        GeoshapeChart(
            type="geoshape",
            conditional_formatting={
                "sales": {"when": [{"gt": 1000, "background": "#ff0000"}]}
            },
        )  # type: ignore[call-arg]


def test_map_patch_rejects_conditional_formatting():
    """map dispatches to GeoshapeChart — same retirement as geoshape."""
    with pytest.raises(ValidationError, match="conditional_formatting"):
        GeoshapeChart(
            type="map",
            conditional_formatting={
                "sales": {"when": [{"gt": 1000, "background": "#ff0000"}]}
            },
        )  # type: ignore[call-arg]


def test_point_map_patch_rejects_conditional_formatting():
    """conditional_formatting is retired on point_map — extra_forbidden."""
    with pytest.raises(ValidationError, match="conditional_formatting"):
        PointMapChart(
            type="point_map",
            conditional_formatting={
                "value": {"when": [{"gt": 50, "background": "#00ff00"}]}
            },
        )  # type: ignore[call-arg]


def test_bubble_map_patch_rejects_conditional_formatting():
    """bubble_map dispatches to PointMapChart — same retirement as point_map."""
    with pytest.raises(ValidationError, match="conditional_formatting"):
        PointMapChart(
            type="bubble_map",
            conditional_formatting={
                "value": {"when": [{"gt": 50, "background": "#0000ff"}]}
            },
        )  # type: ignore[call-arg]


def test_callout_patch_rejects_conditional_formatting():
    with pytest.raises(ValidationError, match="conditional_formatting"):
        CalloutChart(type="callout", message="hi", conditional_formatting={})  # type: ignore[call-arg]


def test_heatmap_patch_rejects_conditional_formatting():
    with pytest.raises(ValidationError, match="conditional_formatting"):
        HeatmapChart(type="heatmap", x="m", y="v", conditional_formatting={})  # type: ignore[call-arg]


def test_bar_patch_rejects_conditional_formatting():
    """bar no longer honors conditional_formatting — extra_forbidden."""
    with pytest.raises(ValidationError, match="conditional_formatting"):
        BarChart(
            type="bar",
            x="month",
            y="revenue",
            conditional_formatting={
                "revenue": {"when": [{"gt": 100, "background": "#00ff00"}]}
            },
        )  # type: ignore[call-arg]


def test_histogram_patch_rejects_conditional_formatting():
    """histogram dispatches to BarChart — same retirement as bar."""
    with pytest.raises(ValidationError, match="conditional_formatting"):
        BarChart(
            type="histogram",
            x="month",
            conditional_formatting={
                "revenue": {"when": [{"gt": 100, "background": "#00ff00"}]}
            },
        )  # type: ignore[call-arg]


def test_line_patch_rejects_conditional_formatting():
    with pytest.raises(ValidationError, match="conditional_formatting"):
        LineChart(
            type="line",
            x="month",
            y="revenue",
            conditional_formatting={
                "revenue": {"when": [{"gt": 100, "background": "#00ff00"}]}
            },
        )  # type: ignore[call-arg]


def test_area_patch_rejects_conditional_formatting():
    with pytest.raises(ValidationError, match="conditional_formatting"):
        AreaChart(
            type="area",
            x="month",
            y="revenue",
            conditional_formatting={
                "revenue": {"when": [{"gt": 100, "background": "#00ff00"}]}
            },
        )  # type: ignore[call-arg]


def test_scatter_patch_rejects_conditional_formatting():
    with pytest.raises(ValidationError, match="conditional_formatting"):
        ScatterChart(
            type="scatter",
            x="month",
            y="revenue",
            conditional_formatting={
                "revenue": {"when": [{"gt": 100, "background": "#00ff00"}]}
            },
        )  # type: ignore[call-arg]


def test_bar_patch_accepts_stack():
    """stack is valid on BarChart via style.bar.stack."""
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    p = BarChart(type="bar", x="m", y="v", style=BarChartStylePatch(stack="zero"))
    assert p.style is not None
    assert p.style.stack == "zero"


@pytest.mark.parametrize("bad_value", [True, False])
def test_bar_patch_rejects_bool_stack(bad_value: bool) -> None:
    """stack: true/false must be rejected — only enum values are valid."""
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    with pytest.raises(ValidationError):
        BarChartStylePatch(stack=bad_value)


@pytest.mark.parametrize(
    ("chart_cls", "type_str"),
    [
        (BarChart, "bar"),
        (LineChart, "line"),
        (AreaChart, "area"),
    ],
)
def test_chart_families_reject_root_stack(chart_cls, type_str):
    """stack is a style-cascade field only; chart-root form is rejected on every family."""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        chart_cls(type=type_str, x="m", y="v", stack="zero")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("chart_cls", "type_str"),
    [
        (BarChart, "bar"),
        (LineChart, "line"),
        (AreaChart, "area"),
        (ScatterChart, "scatter"),
        (HeatmapChart, "heatmap"),
    ],
)
def test_cartesian_families_reject_labels(chart_cls, type_str):
    """labels: has no render path on cartesian families — extra_forbidden, not a silent drop."""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        chart_cls(  # type: ignore[call-arg]
            type=type_str, x="m", y="v", labels={"template": "{{ v }}"}
        )


def test_pie_rejects_chart_level_labels():
    """labels: is no longer accepted on pie/donut — template/where live under
    style.marks.slice.labels after being folded into SliceMarkStyle."""
    with pytest.raises(ValidationError):
        PieChart(type="pie", theta="amount", labels={"template": "{{ amount }}"})


def test_slice_labels_style_rejects_broken_template():
    """SliceLabelsStyle.template validator rejects malformed Jinja."""
    from dbt_charts.core.compile.models.style.theme import (
        LabelsDefaultTemplate,
        SliceLabelsStyle,
    )

    dt = LabelsDefaultTemplate(
        with_color="{{ color }}: {{ value }}", no_color="{{ value }}"
    )
    with pytest.raises(ValidationError):
        SliceLabelsStyle(
            offset=10.0, line_height=16.0, default_template=dt, template="{{ broken"
        )


def test_slice_labels_style_rejects_broken_where():
    """SliceLabelsStyle.where validator rejects malformed Jinja."""
    from dbt_charts.core.compile.models.style.theme import (
        LabelsDefaultTemplate,
        SliceLabelsStyle,
    )

    dt = LabelsDefaultTemplate(
        with_color="{{ color }}: {{ value }}", no_color="{{ value }}"
    )
    with pytest.raises(ValidationError):
        SliceLabelsStyle(
            offset=10.0, line_height=16.0, default_template=dt, where="value >>>> 2"
        )


# --- Regression tests for formerly dropped validations ---


def test_pie_style_inner_radius_rejected_on_bar():
    """BarChartStylePatch has no 'pie' field — extra_forbidden rejects it."""
    with pytest.raises(ValidationError, match="pie"):
        BarChart(type="bar", x="a", y="b", style={"pie": {"inner_radius": 0.5}})


def test_pie_style_inner_radius_accepted_on_pie():
    """style.inner_radius is valid directly on PieChartStylePatch (no nested 'pie' key)."""
    p = PieChart(type="pie", theta="amount", style={"inner_radius": 0.3})
    assert p.style is not None
    assert p.style.inner_radius == 0.3


def test_chartpatch_union_dispatches_already_instantiated_callout():
    """An already-instantiated CalloutChart (plain BaseModel, not _BaseChartFields)
    must still dispatch through the AuthoredChart discriminator."""
    adapter = TypeAdapter(AuthoredChart)
    callout = CalloutChart(type="callout", message="hi")
    result = adapter.validate_python(callout)
    assert isinstance(result, CalloutChart)


# --- link: false is the per-chart auto_link opt-out ---


def test_link_false_accepted_on_authored_chart():
    """link: false is the typed per-chart auto_link opt-out."""
    adapter = TypeAdapter(AuthoredChart)
    result = adapter.validate_python(
        {"type": "table", "query": "orders", "link": False}
    )
    assert result.link is False


def test_link_true_rejected_on_authored_chart():
    """link: true is meaningless — only false suppresses, a string links."""
    adapter = TypeAdapter(AuthoredChart)
    with pytest.raises(ValidationError, match="link"):
        adapter.validate_python({"type": "table", "query": "orders", "link": True})


def test_link_zero_rejected_on_authored_chart():
    """link: 0 / 0.0 must not lax-coerce into the false opt-out sentinel."""
    adapter = TypeAdapter(AuthoredChart)
    for zero in (0, 0.0):
        with pytest.raises(ValidationError, match="link"):
            adapter.validate_python({"type": "table", "query": "orders", "link": zero})


# --- layers: is not supported on type: histogram (bar/histogram share BarChart) ---


def test_histogram_rejects_layers():
    """layers on a histogram used to resolve to an empty tuple silently — no
    error, no warning, overlay just never rendered. Must raise instead."""
    with pytest.raises(ValidationError) as exc_info:
        BarChart(
            type="histogram",
            x="revenue",
            layers=[
                LineLayer(type="line", query="revenue_target", x="month", y="target")
            ],
        )
    assert "chart.layers is not supported for chart type 'histogram'" in str(
        exc_info.value
    )


def test_bar_accepts_layers():
    """Same shape on type: bar is the working case — unaffected by the guard."""
    p = BarChart(
        type="bar",
        x="month",
        y="revenue",
        layers=[LineLayer(type="line", query="revenue_target", x="month", y="target")],
    )
    assert p.layers is not None
    assert len(p.layers) == 1


def test_histogram_accepts_explicit_empty_layers():
    """layers: [] is a no-op, not silently dropped data — nothing to drop."""
    p = BarChart(type="histogram", x="revenue", layers=[])
    assert p.layers == []


# --- style.font / style.border: dead on cartesian + pie/donut, live elsewhere ---
# Decision (2026-08-25, RJ): reject at compile time. No render path on bar,
# line, area, scatter, histogram, heatmap, pie, or donut ever draws a
# per-chart card, so these two fields validated, cascaded, and were silently
# thrown away. Structural narrowing (same mechanism as `size`/`shape` on
# scatter and `conditional_formatting`'s family allowlist) makes the
# combination unrepresentable instead of a runtime no-op.

_CARD_STYLE_DEAD_FAMILIES = [
    (BarChart, "bar", {"x": "m", "y": "v"}),
    (BarChart, "histogram", {"x": "m"}),
    (LineChart, "line", {"x": "m", "y": "v"}),
    (AreaChart, "area", {"x": "m", "y": "v"}),
    (ScatterChart, "scatter", {"x": "m", "y": "v"}),
    (HeatmapChart, "heatmap", {"x": "m", "y": "v"}),
    (PieChart, "pie", {"theta": "v"}),
    (PieChart, "donut", {"theta": "v"}),
]


@pytest.mark.parametrize(
    ("chart_cls", "type_str", "required_fields"), _CARD_STYLE_DEAD_FAMILIES
)
def test_card_style_dead_family_rejects_style_font(
    chart_cls, type_str, required_fields
):
    """style.font has no render consumer on these 8 families — extra_forbidden,
    not a silent no-op."""
    with pytest.raises(ValidationError, match="font"):
        chart_cls(type=type_str, **required_fields, style={"font": {"size": 40}})  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("chart_cls", "type_str", "required_fields"), _CARD_STYLE_DEAD_FAMILIES
)
def test_card_style_dead_family_rejects_style_border(
    chart_cls, type_str, required_fields
):
    """style.border has no render consumer on these 8 families — extra_forbidden,
    not a silent no-op."""
    with pytest.raises(ValidationError, match="border"):
        chart_cls(
            type=type_str,
            **required_fields,
            style={"border": {"width": 2, "color": "#c9c9c9"}},
        )  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("chart_cls", "type_str", "required_fields"), _CARD_STYLE_DEAD_FAMILIES
)
def test_card_style_dead_family_default_path_unaffected(
    chart_cls, type_str, required_fields
):
    """The everyday path — no style block at all — must still validate cleanly.
    Proves the narrowing didn't collaterally break the family's normal shape."""
    chart = chart_cls(type=type_str, **required_fields)
    assert chart.type == type_str
    assert chart.style is None


def test_kpi_still_accepts_style_font():
    """KPI draws its own card by hand — font is a real, consumed field."""
    p = KpiChart(type="kpi", value="revenue", style={"font": {"size": 40}})
    assert p.style is not None
    assert p.style.font is not None
    assert p.style.font.size == 40.0


def test_table_still_accepts_style_font():
    """Table draws its own card by hand — font is a real, consumed field."""
    p = TableChart(type="table", style={"font": {"size": 14}})
    assert p.style is not None
    assert p.style.font is not None


def test_spark_bar_still_accepts_style_border():
    """spark_bar draws its own card by hand — border is a real, consumed field.

    Its slot is corner rounding only (radius, no stroke), so `radius` is the
    field to assert here — `width`/`color` are rejected on spark_bar for a
    separate reason and do not belong in this test.
    """
    p = SparkBarChart(type="spark_bar", style={"border": {"radius": 4}})
    assert p.style is not None
    assert p.style.border is not None
    assert p.style.border.radius == 4.0


def test_point_map_still_accepts_style_font():
    """Geo families are out of this task's scope — unaffected, still accepted."""
    p = PointMapChart(type="point_map", style={"font": {"size": 12}})
    assert p.style is not None
    assert p.style.font is not None


def test_callout_still_accepts_style_border():
    """callout draws its own card by hand — border is a real, consumed field."""
    p = CalloutChart(type="callout", message="hi", style={"border": {"width": 1}})
    assert p.style is not None
    assert p.style.border is not None
