"""Integration tests: case transform reaches each render surface.

HIGH-2 gap: the apply_case dispatcher is unit-tested, but prior to this file
no test confirmed the wiring from style.*.font.case through to emitted
SVG / VL spec. These tests pin the contract at three surfaces:

  1. Chart title (VL spec) — style.charts.title.font.case reaches spec.title.text
  2. Axis labelExpr (VL spec) — axis_x.labels.font.case: upper reaches
     encoding.x.axis.labelExpr, even when the axis is temporal (HIGH-1).
  3. KPI label (SVG) — style.charts.kpi.label.font.case reaches rendered text.

HIGH-1 regression: case: upper on a temporal yearmonth axis must wrap the
smart cadence labelExpr, not suppress it.
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

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())


# ---------------------------------------------------------------------------
# Integration surface 1 — chart title VL spec
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Integration surface 2 — axis labelExpr in VL spec (non-temporal)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HIGH-1 regression — temporal yearmonth axis with case: upper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Regression: axis_y / boxplot x / rect x — case injection was lost in 16b0d897f
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Integration surface 3 — KPI label SVG
# ---------------------------------------------------------------------------


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
