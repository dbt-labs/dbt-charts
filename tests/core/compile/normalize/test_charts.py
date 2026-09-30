"""TDD tests for normalize_chart — B3 Phase 2.

normalize_chart maps an authored chart definition → the corresponding
discriminated normalized family model from compile/models/chart/normalized/.

Phase 2 parallel stack: exists alongside old normalize_chart; no production wiring.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    GeoshapeChart,
    KpiChart,
    LineChart,
    PieChart,
    PointMapChart,
    TableChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery


def _registry(**kwargs: SqlQuery) -> dict[str, SqlQuery]:
    """Build a minimal query registry for tests."""
    return dict(kwargs)


def _sql(sql: str = "SELECT 1") -> SqlQuery:
    return SqlQuery(sql=sql, source="t")


# ---------------------------------------------------------------------------
# B3: normalize_chart basic dispatch
# ---------------------------------------------------------------------------


def test_bar_authored_to_compiled() -> None:
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql("SELECT month, revenue FROM t"))
    compiled = normalize_chart(
        "chart1",
        {"type": "bar", "x": "month", "y": "revenue", "query": "q"},
        qr,
        sources={},
    )
    assert isinstance(compiled, BarChart)
    assert compiled.id == "chart1"
    assert compiled.type == "bar"
    assert compiled.x == "month"
    assert compiled.y == "revenue"
    assert compiled.query is not None


def test_axis_y_mirror_carried_through_canonical_normalizer() -> None:
    """style.axis_y.mirror survives the canonical V2 normalizer (normalize_chart) —
    the compile path real authored YAML takes."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql("SELECT month, revenue FROM t"))
    compiled = normalize_chart(
        "c",
        {
            "type": "area",
            "x": "month",
            "y": "revenue",
            "style": {"axis_y": {"mirror": True}},
            "query": "q",
        },
        qr,
        sources={},
    )
    assert compiled.style is not None
    assert compiled.style.axis_y is not None
    assert compiled.style.axis_y.mirror is True


def test_axis_y_mirror_format_override_carried_through_canonical_normalizer() -> None:
    """style.axis_y.mirror: {format: ...} (per-edge relabel) survives the
    canonical V2 normalizer alongside the plain-bool shorthand."""
    from dbt_charts.core.compile.models.style.theme.axis import AxisMirrorStyle
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql("SELECT month, revenue FROM t"))
    compiled = normalize_chart(
        "c",
        {
            "type": "area",
            "x": "month",
            "y": "revenue",
            "style": {"axis_y": {"mirror": {"format": ".0%"}}},
            "query": "q",
        },
        qr,
        sources={},
    )
    assert compiled.style is not None
    assert compiled.style.axis_y is not None
    assert isinstance(compiled.style.axis_y.mirror, AxisMirrorStyle)
    assert compiled.style.axis_y.mirror.format == ".0%"


def test_line_authored_to_compiled() -> None:
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "c",
        {"type": "line", "x": "date", "y": "val", "query": "q"},
        qr,
        sources={},
    )
    assert isinstance(compiled, LineChart)
    assert compiled.type == "line"


def test_kpi_authored_to_compiled() -> None:
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "kpi1",
        {"type": "kpi", "value": "revenue", "label": "Revenue", "query": "q"},
        qr,
        sources={},
    )
    assert isinstance(compiled, KpiChart)
    assert compiled.value == "revenue"
    assert compiled.label == "Revenue"


def test_table_authored_to_compiled() -> None:
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart("t1", {"type": "table", "query": "q"}, qr, sources={})
    assert isinstance(compiled, TableChart)
    assert compiled.type == "table"


def test_donut_normalizes_to_pie() -> None:
    """type: donut → PieChart compiled with inner_radius derived from style."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "d",
        {"type": "donut", "theta": "amount", "query": "q"},
        qr,
        sources={},
    )
    assert isinstance(compiled, PieChart)
    assert compiled.type == "pie"
    assert compiled.style is not None
    assert compiled.style.inner_radius == 0.6


def test_donut_preserves_explicit_zero_inner_radius() -> None:
    """An explicit zero is authored data, not an omitted donut radius."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "d",
        {
            "type": "donut",
            "theta": "amount",
            "query": "q",
            "style": {"inner_radius": 0},
        },
        qr,
        sources={},
    )

    assert isinstance(compiled, PieChart)
    assert compiled.style is not None
    assert compiled.style.inner_radius == 0


def test_style_unwrapped_per_family() -> None:
    """Family-specific style patch is correctly set on the compiled model."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "b",
        {
            "type": "bar",
            "x": "x",
            "y": "y",
            "query": "q",
            "style": {"stack_order": "alphabetical"},
        },
        qr,
        sources={},
    )
    assert isinstance(compiled, BarChart)
    assert compiled.style is not None
    assert compiled.style.stack_order == "alphabetical"


def test_kpi_style_unwrapped() -> None:
    """KpiChartStylePatch is correctly unwrapped onto KpiChart.style."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "kpi",
        {
            "type": "kpi",
            "value": "v",
            "query": "q",
            "style": {"glyph": {"character": "▲"}},
        },
        qr,
        sources={},
    )
    assert isinstance(compiled, KpiChart)
    assert compiled.style is not None
    assert compiled.style.glyph is not None
    assert compiled.style.glyph.character == "▲"  # type: ignore[union-attr]


def test_query_resolved_on_compiled() -> None:
    """Compiled chart has resolved AnyQuery, not a string."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(myq=_sql("SELECT * FROM sales"))
    compiled = normalize_chart(
        "c",
        {"type": "bar", "x": "x", "y": "y", "query": "myq"},
        qr,
        sources={},
    )
    assert compiled.query is not None
    assert compiled.query_name == "myq"


def test_variable_deps_computed() -> None:
    """Variable dependencies from title are propagated to compiled model."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "c",
        {"type": "bar", "x": "x", "y": "y", "title": "{{ my_var }}", "query": "q"},
        qr,
        sources={},
    )
    assert "my_var" in compiled.variable_dependencies


def test_unknown_type_raises() -> None:
    """Unknown chart type raises CompilationError."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    with pytest.raises((CompilationError, ValueError)):
        normalize_chart(
            "c", {"type": "barchart_nonexistent", "query": "q"}, qr, sources={}
        )


def test_chart_type_enum_member_without_authored_patch_raises() -> None:
    """`ChartType` carries VL-mark entries (boxplot, tick, ...) with no authored
    family patch class. Authoring one must fail at the stage boundary, not slip
    through as a 'valid' compiled type."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    with pytest.raises((CompilationError, ValueError)):
        normalize_chart("c", {"type": "boxplot", "query": "q"}, qr, sources={})


def test_compiled_chart_returns_family_model() -> None:
    """normalize_chart returns a per-family Chart model (BarChart, etc.)."""
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "c",
        {"type": "bar", "x": "x", "y": "y", "query": "q"},
        qr,
        sources={},
    )
    assert isinstance(compiled, BarChart), f"expected BarChart, got {type(compiled)}"


# ---------------------------------------------------------------------------
# HIGH regression: missing required channels must error, not silently coerce
# ---------------------------------------------------------------------------


def test_pie_missing_theta_raises() -> None:
    """Pie chart with no theta field raises CompilationError, not silent empty string."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    with pytest.raises(CompilationError, match="theta"):
        normalize_chart("c", {"type": "pie", "query": "q"}, qr, sources={})


def test_kpi_missing_value_raises() -> None:
    """KPI chart with no value field raises CompilationError, not silent empty string."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    with pytest.raises(CompilationError, match="value"):
        normalize_chart("c", {"type": "kpi", "query": "q"}, qr, sources={})


def test_callout_missing_message_raises() -> None:
    """Callout chart with no message raises CompilationError, not silent empty string."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    with pytest.raises(CompilationError, match="message"):
        normalize_chart("c", {"type": "callout", "query": "q"}, qr, sources={})


def test_multiples_self_crossed_raises() -> None:
    """multiples.rows and multiples.columns naming the same field raises at
    compile time — before any query ever runs, not merely a degraded render.
    """
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql("SELECT month, region, revenue FROM t"))
    with pytest.raises(CompilationError, match="multiples.rows and multiples.columns"):
        normalize_chart(
            "c",
            {
                "type": "bar",
                "x": "month",
                "y": "revenue",
                "query": "q",
                "multiples": {"rows": "region", "columns": "region"},
            },
            qr,
            sources={},
        )


def test_multiples_self_crossed_raises_on_every_cartesian_family() -> None:
    """Refused regardless of family — every family multiples supports (bar,
    histogram, line, area, scatter, heatmap) rejects the same shape.
    """
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql("SELECT month, region, revenue FROM t"))
    for chart_type in ("bar", "histogram", "line", "area", "scatter", "heatmap"):
        chart_def = {
            "type": chart_type,
            "x": "month",
            "y": "revenue",
            "query": "q",
            "multiples": {"rows": "region", "columns": "region"},
        }
        with pytest.raises(
            CompilationError, match="multiples.rows and multiples.columns"
        ):
            normalize_chart(f"c_{chart_type}", chart_def, qr, sources={})


def test_multiples_different_rows_and_columns_does_not_raise() -> None:
    """rows and columns naming different fields is a legitimate grid — must
    not be swept up by the self-crossed check.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql("SELECT month, region, product, revenue FROM t"))
    compiled = normalize_chart(
        "c",
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": "q",
            "multiples": {"rows": "region", "columns": "product"},
        },
        qr,
        sources={},
    )
    assert compiled.multiples is not None
    assert compiled.multiples.rows == "region"
    assert compiled.multiples.columns == "product"


def test_kpi_background_passes_through() -> None:
    """KpiChart.background must be wired from flat Chart through the v2 compiler.

    Regression for the merge-time fix: compiled/kpi.py was missing the field and
    normalize.charts wasn't passing it, causing channel.py to crash on
    AttributeError when resolving a KPI chart with a background channel.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "kpi1",
        {"type": "kpi", "value": "revenue", "background": "revenue", "query": "q"},
        qr,
        sources={},
    )
    assert isinstance(compiled, KpiChart)
    assert compiled.background == "revenue"


def test_authored_table_rejects_x_y_color_sort() -> None:
    """Authored TableChart must reject x, y, color, sort — those are vestigial channels."""
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.chart.authored import (
        TableChart as AuthoredTableChart,
    )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AuthoredTableChart(type="table", x="month")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AuthoredTableChart(type="table", y="revenue")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AuthoredTableChart(type="table", color="segment")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AuthoredTableChart(type="table", sort={"field": "month"})


def test_normalized_table_has_rows_columns_values() -> None:
    """Normalized TableChart carries rows/columns/values; no x/y/color/sort."""
    from dbt_charts.core.compile.models.chart.normalized.table import (
        TableChart as NormTableChart,
    )

    chart = NormTableChart(id="t", type="table")
    assert hasattr(chart, "rows")
    assert hasattr(chart, "columns")
    assert hasattr(chart, "values")
    assert not hasattr(chart, "x")
    assert not hasattr(chart, "y")
    assert not hasattr(chart, "color")
    assert not hasattr(chart, "sort")


def test_resolved_table_has_no_x_y_color_sort() -> None:
    """ResolvedTableChart must not carry x/y/color/sort; it carries rows/pivot_columns/values."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.resolved.table import ResolvedTableChart
    from dbt_charts.core.compile.models.style.resolved import ResolvedTableStyle
    from dbt_charts.core.compile.models.style.theme import PaddingStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.resolve.style.typography import resolve_title_font

    board_charts = resolve_chart_style_context(
        get_theme_style(get_default_theme_name())
    )
    legend = board_charts.legend
    chart = ResolvedTableChart(
        id="t1",
        chart_type="table",
        style=ResolvedTableStyle(
            table=board_charts.table,
            title=board_charts.title,
            formats=board_charts.formats,
            title_font=resolve_title_font(board_charts, 600.0),
            pagination=board_charts.pagination,
        ),
        variable_dependencies=frozenset(),
        palette=(),
        resolved_channels={},
        legend=legend,
        background=board_charts.background,
        canvas=board_charts.ink_canvas,
        title_style=board_charts.title,
        layout_padding=PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0),
        rows=["region"],
        pivot_columns=["product"],
        values=["revenue"],
    )
    assert not hasattr(chart, "x")
    assert not hasattr(chart, "y")
    assert not hasattr(chart, "color")
    assert not hasattr(chart, "sort")
    assert chart.rows == ["region"]
    assert chart.pivot_columns == ["product"]
    assert chart.values == ["revenue"]


def test_normalize_table_passes_through_pivot_channels() -> None:
    """normalize_chart for table passes rows/columns/values from authored to normalized."""
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "t1",
        {
            "type": "table",
            "query": "q",
            "rows": ["region"],
            "columns": ["product"],
            "values": ["revenue"],
        },
        qr,
        sources={},
    )
    assert isinstance(compiled, TableChart)
    assert compiled.rows == ["region"]
    assert compiled.columns == ["product"]
    assert compiled.values == ["revenue"]


def test_table_chart_rejects_support_table() -> None:
    """Normalized TableChart must not accept support_table — it belongs on cartesian types."""
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.chart.normalized.table import TableChart

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TableChart(id="t", type="table", support_table={"entries": [{"source": "x"}]})


# ---------------------------------------------------------------------------
# Fix 1: Dead getattr fallbacks — regression tests confirming always-None values
# ---------------------------------------------------------------------------


def test_pie_chart_format_always_none_height_width_wired() -> None:
    """PieChart has no authored format; normalized format must be None.

    height/width ARE authored on PieChart (_RadialChartFields) and must reach
    the normalized chart — render/sizing.py already reads them generically off
    any PieChart/_GeoChartFields instance, so leaving them unwired silently
    drops an author's explicit box-geometry override.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "p",
        {"type": "pie", "theta": "amount", "query": "q", "height": 200, "width": 300},
        qr,
        sources={},
    )
    assert isinstance(compiled, PieChart)
    assert compiled.format is None
    assert compiled.height == 200
    assert compiled.width == 300


def test_pie_chart_width_rejects_non_positive() -> None:
    """Authored PieChart width follows the same gt=0 contract as cartesian width."""
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.chart.authored import (
        PieChart as AuthoredPieChart,
    )

    with pytest.raises(ValidationError):
        AuthoredPieChart(type="pie", theta="amount", width=0)


def test_table_chart_height_width_min_max_always_none() -> None:
    """TableChart has no authored height/width/min_height/max_height; all must be None.

    TableChart extends _SharedChartFields only; those geometry fields are on
    _CartesianChartFields. The getattr calls always returned None.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart("t", {"type": "table", "query": "q"}, qr, sources={})
    assert isinstance(compiled, TableChart)
    assert compiled.height is None
    assert compiled.width is None
    assert compiled.min_height is None
    assert compiled.max_height is None


def test_cartesian_format_always_none() -> None:
    """Cartesian charts have no authored top-level format; normalized format must be None.

    _CartesianChartFields authored has no format field (format lives in style).
    The getattr in _build_cartesian always returned None.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "b",
        {"type": "bar", "x": "month", "y": "revenue", "query": "q"},
        qr,
        sources={},
    )
    assert isinstance(compiled, BarChart)
    assert compiled.format is None


def test_geoshape_basemap_always_none_height_width_wired() -> None:
    """GeoshapeChart has no authored basemap; normalized basemap must be None.

    height/width ARE authored on GeoshapeChart (_GeoChartFields) and must reach
    the normalized chart — render/sizing.py already reads them generically off
    any _GeoChartFields instance. basemap remains geoshape-inapplicable (it is
    only meaningful — and only declared — on PointMapChart).
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "g",
        {
            "type": "geoshape",
            "geo": "world-countries",
            "query": "q",
            "height": 250,
            "width": 400,
        },
        qr,
        sources={},
    )
    assert isinstance(compiled, GeoshapeChart)
    assert compiled.basemap is None
    assert compiled.height == 250
    assert compiled.width == 400


def test_point_map_height_width_wired() -> None:
    """PointMapChart authors height/width (_GeoChartFields) and they must reach
    the normalized chart, same as geoshape.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "pm",
        {
            "type": "point_map",
            "latitude": "lat",
            "longitude": "lon",
            "query": "q",
            "height": 220,
            "width": 350,
        },
        qr,
        sources={},
    )
    assert isinstance(compiled, PointMapChart)
    assert compiled.height == 220
    assert compiled.width == 350


def test_point_map_aspect_ratio_wired_from_style() -> None:
    """PointMapChart normalize wires style.aspect_ratio through (was always None).

    Mirrors the geoshape aspect_ratio wiring already present before this PR;
    point_map's was a bonus fix in the same normalize_chart branch.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "pm",
        {
            "type": "point_map",
            "latitude": "lat",
            "longitude": "lon",
            "query": "q",
            "style": {"aspect_ratio": 2.5},
        },
        qr,
        sources={},
    )
    assert isinstance(compiled, PointMapChart)
    assert compiled.aspect_ratio == 2.5


def test_point_map_collapse_authors_through_normalize() -> None:
    """Authored `collapse` must survive normalize_chart onto the normalized chart.

    Without this, deleting the `collapse=` line in normalize.charts leaves the
    whole suite green while every `collapse: true` a user authors silently does
    nothing: the field parses on the authored model, is dropped on the way to
    the normalized one, and the emitter never sees it. Asserts both states so a
    hardcoded default cannot satisfy it either.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    base = {
        "type": "point_map",
        "latitude": "lat",
        "longitude": "lon",
        "query": "q",
    }

    collapsed = normalize_chart("pm", {**base, "collapse": True}, qr, sources={})
    assert isinstance(collapsed, PointMapChart)
    assert collapsed.collapse is True

    plain = normalize_chart("pm", base, qr, sources={})
    assert isinstance(plain, PointMapChart)
    assert plain.collapse is False


def test_point_map_basemap_authors_through_normalize() -> None:
    """Authored basemap: must survive normalize_chart as a typed BasemapConfig.

    Regression: normalize_chart parsed `chart_def` into an authored PointMapChart
    (basemap: BasemapConfig), then passed that object straight into the
    normalized PointMapChart's basemap field. While that field stayed typed
    dict[str, Any], pydantic rejected the BasemapConfig instance with
    'Input should be a valid dictionary' — basemap could never compile.
    """
    from dbt_charts.core.compile.models.chart.authored import BasemapConfig
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "pm",
        {
            "type": "point_map",
            "latitude": "lat",
            "longitude": "lon",
            "query": "q",
            "basemap": {"source": "us-states", "fill": "#f5f5f5", "stroke": "#ccc"},
        },
        qr,
        sources={},
    )
    assert isinstance(compiled, PointMapChart)
    assert isinstance(compiled.basemap, BasemapConfig)
    assert compiled.basemap.source == "us-states"
    assert compiled.basemap.fill == "#f5f5f5"
    assert compiled.basemap.stroke == "#ccc"


# ---------------------------------------------------------------------------
# Fix 2: GeoshapeChart silently drops authored latitude/longitude
# ---------------------------------------------------------------------------


def test_geoshape_rejects_latitude_longitude() -> None:
    """Geoshape charts must reject latitude/longitude at parse time.

    latitude/longitude are declared on PointMapChart only (not the shared
    _GeoChartFields base), so extra="forbid" structurally rejects them on
    GeoshapeChart — the same mechanism that rejects `size` on a LineChart.
    Silently accepting then dropping them would violate 'validate and error fast'.
    """
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.chart.authored.geoshape import GeoshapeChart

    with pytest.raises(ValidationError, match="latitude|longitude"):
        GeoshapeChart.model_validate(
            {"type": "map", "latitude": "lat_col", "query": "q"}
        )

    with pytest.raises(ValidationError, match="latitude|longitude"):
        GeoshapeChart.model_validate(
            {"type": "geoshape", "longitude": "lon_col", "query": "q"}
        )


def test_support_table_entry_inherits_measure_format_from_style_axis_y() -> None:
    """v2 must match v1: a support_table entry reading chart.y with format=None

    inherits the chart's measure format (style.axis_y.format here), same as
    normalize_chart() does for v1. Regression: normalize_chart passed
    authored.support_table straight through with no inheritance, so v2 strip
    cells rendered unformatted.
    """
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    qr = _registry(q=_sql())
    compiled = normalize_chart(
        "b",
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": "q",
            "style": {"axis_y": {"labels": {"format": "$,.0f"}}},
            "support_table": [{"source": "revenue"}, {"source": "count"}],
        },
        qr,
        sources={},
    )
    assert isinstance(compiled, BarChart)
    assert compiled.support_table is not None
    entries = compiled.support_table.entries
    assert entries[0].format == "$,.0f", (
        "measure entry must inherit style.axis_y.format, matching v1"
    )
    assert entries[1].format is None, "non-measure entry must not inherit"
