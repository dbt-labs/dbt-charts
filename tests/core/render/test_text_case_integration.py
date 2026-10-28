"""Integration test: the KPI label's ``font.case`` reaches the rendered SVG.

The ``apply_case`` dispatcher is unit-tested; this pins the wiring from
``style.charts.kpi.label.font.case`` through to the emitted text. Axis labels
are covered by ``chart-renderer/tests/it/axis_label_case.rs``.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import (
    KpiChart,
)
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context


def test_kpi_label_upper_case_reaches_rendered_svg() -> None:
    """style.charts.kpi.label.font.case: upper transforms KPI label text in SVG output."""
    from .._engine_render import render_kpi_svg

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "kpi": base.charts.kpi.model_copy(
                        update={
                            "label": base.charts.kpi.label.model_copy(
                                update={"font": FontStyle(case="upper")}
                            )
                        }
                    )
                }
            )
        }
    )
    board_rs, board_ctx = resolve_style_and_context(seed)

    chart = KpiChart(
        id="test_kpi_upper",
        type="kpi",
        value="revenue",
        label="net revenue retention",
    )
    data = [{"revenue": 1_500_000}]
    resolved = resolve(chart, data, chart_style_context=board_ctx)
    svg = render_kpi_svg(resolved, data, width=300, height=180, board_style=board_rs)
    assert "NET REVENUE RETENTION" in svg, (
        f"Expected upper-case KPI label in SVG. SVG snippet: {svg[:500]!r}"
    )
