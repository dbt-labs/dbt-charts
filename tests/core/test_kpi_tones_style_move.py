"""``KpiTonesStyle`` moved from ``charts.kpi.tones`` to board-level ``style.tones``.

Tones are a board-level semantic palette shared by two unrelated chart
families — the KPI support row and table conditional-formatting glyphs — not
a KPI concern that table used to reach across families for. These tests pin
the new authoring path (``style.tones``, a sibling of ``style.palettes``) and
that both consumers read the shared board-level slot.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

from ._engine_render import render_kpi_svg, render_table_svg

_OVERRIDE_POSITIVE = "#123456"


def test_style_tones_propagates_through_resolve_chart_style_context() -> None:
    """A board-authored style.tones.positive override reaches ChartStyleContext.tones."""
    patch = StylePatch.model_validate({"tones": {"positive": _OVERRIDE_POSITIVE}})
    ctx = resolve_chart_style_context(get_theme_style(), patch)
    assert ctx.tones.positive == _OVERRIDE_POSITIVE
    # Unauthored tones still resolve from the theme default (non-empty).
    assert ctx.tones.negative


def test_style_tones_propagates_through_resolve_style() -> None:
    """The same override reaches ResolvedStyle.tones and ResolvedChartDefaults.tones."""
    patch = StylePatch.model_validate({"tones": {"positive": _OVERRIDE_POSITIVE}})
    resolved = resolve_style(get_theme_style(), patch)
    assert resolved.tones.positive == _OVERRIDE_POSITIVE
    assert resolved.chart_defaults.tones.positive == _OVERRIDE_POSITIVE


def test_kpi_support_row_reads_board_level_tones_override() -> None:
    """A KPI support row with style.support.tone: positive picks up the board-level override."""
    patch = StylePatch.model_validate({"tones": {"positive": _OVERRIDE_POSITIVE}})
    board_style = resolve_style(get_theme_style(), patch)
    board_ctx = resolve_chart_style_context(get_theme_style(), patch)

    chart = KpiChart(
        id="kpi_tones",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="kpi",
        value="revenue",
        support={"value": "delta_pct", "label": "vs LQ", "glyph": "▲"},
        style={"support": {"tone": "positive"}},
    )
    data = [{"revenue": 1_500_000, "delta_pct": 0.124}]
    resolved = resolve(chart, data, chart_style_context=board_ctx)
    svg = render_kpi_svg(resolved, data, width=300, height=160, board_style=board_style)
    assert _OVERRIDE_POSITIVE in svg


def test_table_conditional_glyph_reads_board_level_tones_override(make_chart) -> None:
    """A table conditional-formatting `tone: positive` rule picks up the same
    board-level override — proof the two families share one slot rather than
    table reaching into KPI's own style tree."""
    patch = StylePatch.model_validate({"tones": {"positive": _OVERRIDE_POSITIVE}})
    board_style = resolve_style(get_theme_style(), patch)
    board_ctx = resolve_chart_style_context(get_theme_style(), patch)

    chart = make_chart(
        "table",
        title="Growth",
        conditional_formatting={
            "growth": {"when": [{"gt": 0, "glyph": "▲", "tone": "positive"}]}
        },
        style={"columns": {"company": {}, "growth": {"format": ".0%"}}},
    )
    data = [{"company": "Apex", "growth": 0.12}]
    resolved = resolve(chart, data, chart_style_context=board_ctx)
    svg = render_table_svg(resolved, data, width=600, board_style=board_style)
    assert _OVERRIDE_POSITIVE in svg
