"""TDD tests for unified emit (all surfaces: axis, legend, mark).

After unification:
- axis → encoding.x.axis.* / encoding.y.axis.* (ALL variants incl. quantitative/band/global)
- legend → encoding.color.legend.* / encoding.size.legend.*
- mark → spec.mark.{...} extended object

Legitimately stays at config.*:
- config.view (no per-encoding equivalent)
- config.title, config.range.category, config.font
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AxisTicksStylePatch,
    AxisXStylePatch,
    AxisYStylePatch,
    BandAxisStylePatch,
    BarChartStylePatch,
    BaseAxisGridStylePatch,
    EndpointLabelsConfigPatch,
    LegendStylePatch,
    QuantitativeAxisStylePatch,
    ScatterChartStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_DATA = [{"month": "Jan", "rev": 100}, {"month": "Feb", "rev": 200}]


@pytest.fixture(autouse=True)
def _isolate_config_style(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin theme to 'stark' for this module; reset config cache around each test.

    Most tests in this file inspect the stark legend/axis encoding shape.
    set_default_theme_name() is gone — DCT_DEFAULT_THEME drives get_default_theme_name().
    """
    reset_config()
    monkeypatch.setenv("DCT_DEFAULT_THEME", "stark")
    yield
    reset_config()


def _spec(style: BarChartStylePatch | None = None) -> dict:
    """Render a single-chart spec against `stark`.

    Pinned to stark so the legend-emission tests in this file observe the
    legend at encoding.color.legend.*. The shipped `clarity` default sets
    legend.disable=true, which would null out the color legend before the
    tests get to inspect it.

    DCT_DEFAULT_THEME=stark is set by the autouse _isolate_config_style fixture.
    """
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="rev",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=style,
        color="month",
    )
    return generate_vega_lite_spec(chart, _DATA, width=400)


# ── Axis ──────────────────────────────────────────────────────────────────────


def test_theme_axis_y_grid_color_at_encoding():
    """Theme axis_y grid color at encoding.y.axis, not config.axisY."""
    spec = _spec()
    y_axis = spec.get("encoding", {}).get("y", {}).get("axis", {})
    assert "gridColor" in y_axis
    assert "gridColor" not in spec.get("config", {}).get("axisY", {})


def test_chart_local_axis_y_grid_color_at_encoding():
    """Chart-local axis_y.grid.color at encoding.y.axis.gridColor."""

    patch = BarChartStylePatch(
        orientation="vertical",
        axis_y=AxisYStylePatch(grid=BaseAxisGridStylePatch(color="#dd0000")),
    )
    spec = _spec(style=patch)
    grid_color = spec.get("encoding", {}).get("y", {}).get("axis", {}).get("gridColor")
    assert grid_color == "#dd0000"
    assert spec.get("config", {}).get("axisY", {}).get("gridColor") != "#dd0000"


def test_config_axisXY_absent():
    """config.axisX and config.axisY absent — moved to encoding."""
    spec = _spec()
    assert "axisX" not in spec.get("config", {})
    assert "axisY" not in spec.get("config", {})


def test_config_axis_global_absent():
    """config.axis (global) absent — values moved to both encoding.x.axis and encoding.y.axis."""
    spec = _spec()
    assert "axis" not in spec.get("config", {}), (
        "config.axis must be absent after full unification"
    )
    # Global axis values must appear in encoding channels
    x_axis = spec.get("encoding", {}).get("x", {}).get("axis", {})
    y_axis = spec.get("encoding", {}).get("y", {}).get("axis", {})
    assert "gridColor" in x_axis, (
        f"Global axis.gridColor must reach encoding.x.axis; got {x_axis}"
    )
    assert "gridColor" in y_axis, (
        f"Global axis.gridColor must reach encoding.y.axis; got {y_axis}"
    )


def test_config_axisQuantitative_absent():
    """config.axisQuantitative absent — values flow to encoding where scale type is quantitative."""
    spec = _spec()
    assert "axisQuantitative" not in spec.get("config", {})
    # quantitative axis values reach y encoding (revenue is quantitative)
    y_axis = spec.get("encoding", {}).get("y", {}).get("axis", {})
    assert len(y_axis) > 0, "axisQuantitative values must reach encoding.y.axis"


def test_chart_local_axis_y_offset_at_encoding():
    """axis_y.ticks.offset at encoding.y.axis.offset (ignored at config level by vl-convert)."""
    patch = BarChartStylePatch(
        orientation="vertical",
        axis_y=AxisYStylePatch(ticks=AxisTicksStylePatch(offset=42)),
    )
    spec = _spec(style=patch)
    assert spec.get("encoding", {}).get("y", {}).get("axis", {}).get("offset") == 42


def test_axis_to_vl_mapper_emits_offset():
    """axis_to_vl() canonical mapper emits offset from resolved axis.ticks.offset."""
    reset_config()
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

    base = resolve_chart_style_context(get_theme_style())
    axis_y = resolved_axis_style(
        base, "axis_y", "quantitative", chart_type="", label_authored=False
    )
    new_ticks = dataclasses.replace(axis_y.ticks, offset=99.0)
    new_axis = dataclasses.replace(axis_y, ticks=new_ticks)
    result = axis_to_vl(new_axis)
    assert result.get("offset") == 99.0


# ── Precedence-pin tests ───────────────────────────────────────────────────────


def test_chart_local_axis_quantitative_at_encoding_not_config():
    """chart.style.axis_quantitative routes to encoding, not config.axisQuantitative."""
    from dbt_charts.core.compile.models.style.authored import BaseAxisGridStylePatch

    patch = BarChartStylePatch(
        orientation="vertical",
        axis_quantitative=QuantitativeAxisStylePatch(
            grid=BaseAxisGridStylePatch(color="#quant")
        ),
    )
    spec = _spec(style=patch)
    # Must NOT appear in config
    assert (
        spec.get("config", {}).get("axisQuantitative", {}).get("gridColor") != "#quant"
    )
    # Must appear in encoding.y (rev is quantitative)
    y_axis = spec.get("encoding", {}).get("y", {}).get("axis", {})
    assert y_axis.get("gridColor") == "#quant", (
        f"expected gridColor in y.axis; got {y_axis}"
    )


def test_chart_local_axis_band_at_encoding_not_config():
    """chart.style.axis_band routes to encoding, not config.axisBand."""
    from dbt_charts.core.compile.models.style.authored import BaseAxisGridStylePatch

    patch = BarChartStylePatch(
        orientation="vertical",
        axis_band=BandAxisStylePatch(grid=BaseAxisGridStylePatch(color="#band")),
    )
    spec = _spec(style=patch)
    assert spec.get("config", {}).get("axisBand", {}).get("gridColor") != "#band"
    # Categorical x axis (month is ordinal/nominal) should get axis_band
    x_axis = spec.get("encoding", {}).get("x", {}).get("axis", {})
    assert x_axis.get("gridColor") == "#band", (
        f"expected gridColor in x.axis; got {x_axis}"
    )


def test_chart_local_axis_y_wins_over_axis_quantitative():
    """chart.style.axis_y (channel-specific) overrides axis_quantitative for y."""
    from dbt_charts.core.compile.models.style.authored import BaseAxisGridStylePatch

    patch = BarChartStylePatch(
        orientation="vertical",
        axis_quantitative=QuantitativeAxisStylePatch(
            grid=BaseAxisGridStylePatch(color="#quant")
        ),
        axis_y=AxisYStylePatch(grid=BaseAxisGridStylePatch(color="#channel")),
    )
    spec = _spec(style=patch)
    y_axis = spec.get("encoding", {}).get("y", {}).get("axis", {})
    assert y_axis.get("gridColor") == "#channel", (
        f"axis_y must win over axis_quantitative; got {y_axis}"
    )


def test_chart_local_axis_quantitative_wins_over_chart_local_global():
    """chart.style.axis_quantitative wins when both axis_quantitative and other axes are set."""
    from dbt_charts.core.compile.models.style.authored import BaseAxisGridStylePatch

    patch = BarChartStylePatch(
        orientation="vertical",
        axis_x=AxisXStylePatch(grid=BaseAxisGridStylePatch(color="#x")),
        axis_quantitative=QuantitativeAxisStylePatch(
            grid=BaseAxisGridStylePatch(color="#quant")
        ),
    )
    spec = _spec(style=patch)
    y_axis = spec.get("encoding", {}).get("y", {}).get("axis", {})
    assert y_axis.get("gridColor") == "#quant", (
        f"axis_quantitative must take precedence for quantitative y axis; got {y_axis}"
    )


# ── Legend ────────────────────────────────────────────────────────────────────


def test_config_legend_absent():
    """config.legend absent — values at encoding.color.legend.*."""
    spec = _spec()
    assert "legend" not in spec.get("config", {})


def test_theme_legend_at_encoding_color():
    """Theme legend.orient at encoding.color.legend.orient."""
    spec = _spec()
    color_legend = spec.get("encoding", {}).get("color", {}).get("legend", {})
    assert "orient" in color_legend, (
        f"legend.orient must be at encoding.color.legend; got {color_legend}"
    )


def test_chart_local_legend_disable():
    """chart.style.legend.visible=False produces encoding.color.legend=null.

    Explicit stack="zero" + endpoint_labels off: the grouped-bar default
    (unset stack) forces its own top/horizontal legend on regardless of an
    author's legend patch (see _base_kwargs's top_legend), and a stacked bar
    with endpoint labels on suppresses the legend independently of this
    chart's own patch — either would make this assertion pass for the wrong
    reason. Stacked-without-endpoint-labels isolates the one suppression
    path this test means to pin.
    """
    patch = BarChartStylePatch(
        stack="zero",
        legend=LegendStylePatch(visible=False),
        endpoint_labels=EndpointLabelsConfigPatch(visible=False),
    )
    spec = _spec(style=patch)
    color_enc = spec.get("encoding", {}).get("color", {})
    assert color_enc.get("legend") is None, (
        f"Suppressed legend must be null; got {color_enc}"
    )


def test_clarity_scatter_legend_position_reaches_color_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scatter inherits the family legend opt-in and honors authored placement."""
    monkeypatch.setenv("DCT_DEFAULT_THEME", "clarity")
    reset_config()
    style = ScatterChartStylePatch(
        legend=LegendStylePatch(position={"edge": "top"}, direction="horizontal")
    )
    chart = ScatterChart(
        id="t",
        type="scatter",
        x="flight_number",
        y="mass_kg",
        color="orbit",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=style,
    )
    data = [
        {"flight_number": 1, "mass_kg": 1000, "orbit": "LEO"},
        {"flight_number": 2, "mass_kg": 2000, "orbit": "GTO"},
    ]

    spec = generate_vega_lite_spec(chart, data, width=400)

    color_legend = spec.get("encoding", {}).get("color", {}).get("legend")
    assert color_legend is not None, "scatter color legend must not be suppressed"
    assert color_legend["orient"] == "top"
    assert color_legend["direction"] == "horizontal"


# ── Mark ──────────────────────────────────────────────────────────────────────


def test_config_bar_corner_radius_absent():
    """config.bar.cornerRadius* absent — emitted at spec.mark instead.

    Bars use ``cornerRadiusEnd`` so only the value-end corners (top for
    vertical, right for horizontal) round, leaving the baseline edge square.
    """
    spec = _spec()
    cfg_bar = spec.get("config", {}).get("bar", {})
    assert "cornerRadius" not in cfg_bar
    assert "cornerRadiusEnd" not in cfg_bar
    mark = spec.get("mark", spec.get("layer", [{}])[0].get("mark", {}))
    if isinstance(mark, dict):
        assert "cornerRadiusEnd" in mark, (
            f"bar cornerRadiusEnd must be at spec.mark; got {mark}"
        )


def test_chart_local_bar_style_at_mark():
    """chart.style.bar.marks.bar.band_width → spec.mark.{width|height}.band.

    After ADR-015 marks namespace, bar mark geometry lives under the bar
    family overlay: style.bar.marks.bar.band_width. band_width sizes a
    single-series bar against the category band; grouped bars are sized by
    style.bar.overlap instead, so this pins the single-series (no color) case.
    """
    patch = BarChartStylePatch.model_validate({"marks": {"bar": {"band_width": 0.42}}})
    chart = BarChart(
        id="t",
        type="bar",
        x="month",
        y="rev",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=patch,
    )
    spec = generate_vega_lite_spec(
        chart,
        _DATA,
        width=400,
    )
    mark = spec.get("mark", spec.get("layer", [{}])[0].get("mark", {}))
    if isinstance(mark, dict):
        band_dim = mark.get("width") or mark.get("height")
        assert band_dim == {"band": 0.42}, (
            f"bar.marks.bar.band_width must reach spec.mark.{{width|height}}={{band: N}}; got {mark}"
        )


def test_config_line_stroke_width_absent():
    """config.line.strokeWidth absent — in spec.mark.strokeWidth for line charts."""
    reset_config()
    chart = LineChart(
        id="t",
        type="line",
        x="month",
        y="rev",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
    )
    spec = generate_vega_lite_spec(chart, _DATA, width=400)
    assert "strokeWidth" not in spec.get("config", {}).get("line", {})
    mark = spec.get("mark", spec.get("layer", [{}])[0].get("mark", {}))
    if isinstance(mark, dict):
        assert "strokeWidth" in mark, (
            f"line strokeWidth must be at spec.mark; got {mark}"
        )


# ── Legitimately stays at config.* ───────────────────────────────────────────


def test_config_view_stays():
    """config.view stays — only way to control plot border."""
    spec = _spec()
    assert "view" in spec.get("config", {})


def test_config_title_stays():
    """config.title stays — board-level title styling."""
    spec = _spec()
    assert "title" in spec.get("config", {})


def test_config_range_stays():
    """config.range stays — palette routing."""
    spec = _spec()
    assert "range" in spec.get("config", {})
