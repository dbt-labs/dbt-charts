"""Integration tests: chart channel syntax → Vega-Lite spec emission.

TDD: these tests were written before the implementation.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    Chart,
    KpiChart,
    TableChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CONTEXT = resolve_style_and_context(get_theme_style())

_DUMMY_QUERY = SqlQuery(sql="SELECT 1", source="test")


_BAR_DATA = [
    {"category": "A", "arr": 2_000_000},
    {"category": "B", "arr": 500_000},
]


def _bar_chart(**kwargs: Any) -> Chart:
    return BarChart(
        id="test_chart",
        query=_DUMMY_QUERY,
        query_name="q",
        type="bar",
        x="category",
        y="arr",
        **kwargs,
    )


# ============================================================================
# color gradient on bar chart
# ============================================================================


# ============================================================================
# literal chart.style.color (already works, regression guard)
# ============================================================================


def test_literal_style_color_passes_through():
    """chart.style.color (literal) still works as before — no resolved channel emitted."""
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    chart = _bar_chart(
        style=BarChartStylePatch.model_validate({"color": {"static": "#ff0000"}})
    )
    resolved = resolve(chart, _BAR_DATA, chart_style_context=_BOARD_CONTEXT)
    # chart.style.color is the literal cascade — no channel encoding
    assert resolved.resolved_channels == {}


def test_heatmap_chart_local_palette_overrides_theme_for_colorless_rect_fill():
    """``style.palette`` on a colorless heatmap reaches `the rect fill`.

    Regression for removing the assemble-time re-patch (translate.py's
    path/rect/symbol/shape/trail loop): the heatmap emitter now bakes
    `the rect fill` directly from its own already-resolved effective
    palette instead of relying on a post-merge correction.
    """
    from dbt_charts.core.compile.models.chart.normalized import HeatmapChart
    from dbt_charts.core.compile.models.style.authored import HeatmapChartStylePatch
    from dbt_charts.core.compile.resolve.style.palette import palette as resolve_palette

    chart = HeatmapChart(
        id="heatmap_test",
        query=_DUMMY_QUERY,
        query_name="q",
        type="heatmap",
        x="col",
        y="row",
        style=HeatmapChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": "dbt-seq-rust"}}}
        ),
    )
    data = [
        {"col": "A", "row": "1"},
        {"col": "B", "row": "2"},
    ]
    resolved = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    svg = render_resolved_chart(resolved, data, _BOARD_STYLE).payload
    expected_stop_0 = resolve_palette("dbt-seq-rust")[0]
    assert isinstance(svg, str)
    assert svg.count(f'fill="{expected_stop_0}" opacity="1"') == len(data)


# ============================================================================
# Partial: KPI chart with color.when evaluates against data row
# ============================================================================


def test_kpi_color_when():
    """KPI chart with conditional_formatting evaluates font.color rule against data row."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    from ..._engine_render import render_kpi_svg

    _kpi_data = [{"revenue": 2_500_000}]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "kpi1",
            "query": _DUMMY_QUERY,
            "query_name": "q",
            "type": "kpi",
            "value": "revenue",
            "conditional_formatting": {
                "revenue": {
                    "when": [
                        {"gt": 2_000_000, "font": {"color": "#00ff00"}},
                        {"lte": 2_000_000, "font": {"color": "#ff0000"}},
                    ]
                }
            },
        }
    )
    resolved = resolve(chart, _kpi_data, chart_style_context=_BOARD_CONTEXT)
    svg = render_kpi_svg(
        resolved,
        _kpi_data,
        board_style=resolve_style(get_theme_style()),
    )
    # Revenue is 2.5M > 2M → should use #00ff00
    assert "#00ff00" in svg


# ============================================================================
# Tables read conditional_formatting directly — no lowering into style.columns
# ============================================================================


def test_table_conditional_formatting_preserved_on_source_chart():
    """Table CF is preserved on the chart-level block for direct render access.

    Post ``delete-tablecolumnconfig-when``, the table renderer reads ``when``
    rules straight from ``chart.conditional_formatting``. Resolve must not
    synthesize or mutate ``style.columns[*].when`` to carry CF.
    """
    _table_data = [
        {"region": "US", "arr": 1_200_000},
        {"region": "EU", "arr": 400_000},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "table1",
            "query": _DUMMY_QUERY,
            "query_name": "q",
            "type": "table",
            "conditional_formatting": {
                "arr": {
                    "when": [
                        {"gt": 1_000_000, "font": {"color": "#00ff00"}},
                        {"lte": 1_000_000, "font": {"color": "#ff0000"}},
                    ]
                }
            },
        }
    )
    resolved = resolve(chart, _table_data, chart_style_context=_BOARD_CONTEXT)
    assert resolved is not None
    # CF block survives on the resolved chart, where the table renderer reads it.
    assert resolved.conditional_formatting is not None
    assert "arr" in resolved.conditional_formatting


# ============================================================================
# Regression: dict-form color must not crash validation.py (TypeError on hash)
# ============================================================================


# ============================================================================
# Series grouping: detail encoding for line/area/scatter
# ============================================================================


# ============================================================================
# Gradient domain: only set when both min and max are authored
# ============================================================================


# ============================================================================

# ============================================================================
# Table lowering: color.when lowers to style.columns
# ============================================================================


def test_table_conditional_formatting_flows_through_render_to_svg():
    """Table CF rules from the chart-level block apply at render time."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    from ..._engine_render import render_table_svg

    _table_data = [
        {"region": "US", "arr": 1_200_000},
        {"region": "EU", "arr": 400_000},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "table1",
            "query": _DUMMY_QUERY,
            "query_name": "q",
            "type": "table",
            "conditional_formatting": {
                "arr": {
                    "when": [
                        {"gt": 1_000_000, "font": {"color": "#00ff00"}},
                        {"lte": 1_000_000, "font": {"color": "#ff0000"}},
                    ]
                }
            },
        }
    )
    resolved = resolve(chart, _table_data, chart_style_context=_BOARD_CONTEXT)
    svg = render_table_svg(
        resolved,
        _table_data,
        width=400,
        height=200,
        board_style=resolve_style(get_theme_style()),
    )
    # Both branches of the rule set must appear in the rendered SVG.
    assert "#00ff00" in svg
    assert "#ff0000" in svg


def test_table_link_does_not_override_conditional_font_color():
    """Conditional font color is explicit data styling; link affordance adds underline."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    from ..._engine_render import render_table_svg

    table_data = [{"region": "US", "arr": 1_200_000}]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "table1",
            "query": _DUMMY_QUERY,
            "query_name": "q",
            "type": "table",
            "link": "/detail/{{ region }}",
            "conditional_formatting": {
                "region": {
                    "when": [
                        {"eq": "US", "font": {"color": "#ff0000"}},
                    ]
                }
            },
        }
    )

    resolved = resolve(chart, table_data, chart_style_context=_BOARD_CONTEXT)
    svg = render_table_svg(
        resolved,
        table_data,
        width=400,
        height=200,
        board_style=resolve_style(get_theme_style()),
    )

    assert "#ff0000" in svg


# ============================================================================
# KPI cascade precedence: resolved channel overrides style literal
# ============================================================================


def test_kpi_channel_overrides_style_font_color():
    """resolved_channels color (projected from conditional_formatting) takes priority
    over chart.style.font.color — the whole-card fallback lever now that
    style.color is gone."""
    from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    from ..._engine_render import render_kpi_svg

    _kpi_data = [{"revenue": 3_000_000}]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "kpi_cascade",
            "query": _DUMMY_QUERY,
            "query_name": "q",
            "type": "kpi",
            "value": "revenue",
            "style": KpiChartStylePatch.model_validate(
                {"font": {"color": "#aaaaaa"}}
            ),  # would be used as fallback
            "conditional_formatting": {
                "revenue": {
                    "when": [
                        {"gt": 2_000_000, "font": {"color": "#00ff00"}},  # should win
                    ]
                }
            },
        }
    )
    resolved = resolve(chart, _kpi_data, chart_style_context=_BOARD_CONTEXT)
    svg = render_kpi_svg(
        resolved,
        _kpi_data,
        board_style=resolve_style(get_theme_style()),
    )
    assert "#00ff00" in svg
    assert "#aaaaaa" not in svg


def test_kpi_style_font_color_used_as_fallback():
    """chart.style.font.color flows through the cascade and lands as the KPI
    value fill — the whole-card lever every other family already uses.

    Renderer reads from resolved_style — never from a Patch. The chart-local
    font color is merged onto resolved_style.kpi.font.color by
    build_chart_style_context (consumed by the KPI card's value-color cascade).
    """
    from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    from ..._engine_render import render_kpi_svg

    _kpi_data = [{"revenue": 3_000_000}]
    chart = KpiChart(
        id="kpi_style_fallback",
        query=_DUMMY_QUERY,
        query_name="q",
        type="kpi",
        value="revenue",
        style=KpiChartStylePatch.model_validate({"font": {"color": "#cccccc"}}),
    )
    resolved = resolve(chart, _kpi_data, chart_style_context=_BOARD_CONTEXT)
    svg = render_kpi_svg(
        resolved,
        _kpi_data,
        board_style=resolve_style(get_theme_style()),
    )
    assert "#cccccc" in svg


def test_kpi_style_color_rejected():
    """style.color is not a KPI field — it was the family outlier (a bare
    string where every other family types an object) and had no channel of
    its own to configure. Authoring it now raises rather than silently
    resolving; style.font.color / style.value.font.color replace it."""
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

    with pytest.raises(ValidationError, match="Extra inputs"):
        KpiChartStylePatch.model_validate({"color": "#bf8700"})


# ============================================================================
# Table lowering: passthrough columns preserve original order
# ============================================================================


def test_table_cf_does_not_synthesize_style_columns_for_target_field():
    """CF on a column NOT in style.columns does not synthesize a column entry.

    After removing the CF → style.columns lowering, tables read rules straight
    from the chart-level block. ``style.columns`` stays authored-only.
    """
    from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
    from dbt_charts.core.compile.models.style.authored import TableChartStylePatch

    _table_data = [
        {"name": "Alice", "arr": 1_200_000, "region": "US"},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "table_order",
            "query": _DUMMY_QUERY,
            "query_name": "q",
            "type": "table",
            "style": TableChartStylePatch.model_validate(
                {
                    "columns": {
                        "name": TableColumnConfig(visible=True),
                        "region": TableColumnConfig(),
                    }
                }
            ),
            "conditional_formatting": {
                "arr": {"when": [{"gt": 1_000_000, "font": {"color": "#00ff00"}}]},
            },
        }
    )
    resolved = resolve(chart, _table_data, chart_style_context=_BOARD_CONTEXT)
    assert resolved.columns is not None
    cols = resolved.columns
    fields = list(cols.keys())
    # Every query column materializes (style.columns is styling-only), but the
    # CF rule on "arr" is not lowered into its column config.
    assert fields == ["name", "arr", "region"]
    assert cols["arr"].font is None


# ============================================================================
# Table chart rejects literal/series color channel
# ============================================================================


def test_table_literal_color_channel_raises():
    """color: {value: ...} is rejected at model creation time — use style.color.
    Chart.color is str | None; dict-form is rejected as type error.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="color"):
        TableChart(
            id="table_lit",
            query=_DUMMY_QUERY,
            query_name="q",
            type="table",
            color={"value": "#ff0000"},  # type: ignore[arg-type]
        )


# ============================================================================
# Regression: link template with color channel
# ============================================================================


def test_link_template_with_color_channel():
    """{{ color }} in link template resolves correctly when color uses channel grammar."""

    _data = [
        {"category": "A", "segment": "S1", "arr": 100},
        {"category": "B", "segment": "S2", "arr": 200},
    ]
    chart = BarChart(
        id="link_chart",
        query=_DUMMY_QUERY,
        query_name="q",
        type="bar",
        x="category",
        y="arr",
        color="segment",
        link="?filter={{ color }}",
    )
    resolved = resolve(chart, _data, chart_style_context=_BOARD_CONTEXT)
    # Must not raise — resolved_channels has color
    spec = render_resolved_chart(resolved, _data, _BOARD_STYLE).payload
    assert spec is not None


# ============================================================================
# 1B.6b: KPI gradient mode with CSV-string cell value
# ============================================================================


def test_kpi_gradient_color_csv_string_interpolates():
    """KPI gradient background must interpolate when the cell value is a numeric string.

    CSV-loaded data arrives as str; it must not short-circuit to the null color.
    """
    from ..._engine_render import kpi_gradient_fill

    fill = kpi_gradient_fill("50", ["#ffffff", "#0000ff"], 0.0, 100.0)
    assert fill not in ("#ffffff", "#0000ff", "none")
