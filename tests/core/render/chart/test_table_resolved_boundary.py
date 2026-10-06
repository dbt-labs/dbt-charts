"""Table rendering consumes only presentation decisions finalized by resolve."""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import TableChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.table import render_table_svg


def _board_style(*, title_case: str, boundary_format: str):
    theme = get_theme_style()
    title = theme.title.model_copy(
        update={"font": theme.title.font.model_copy(update={"case": title_case})}
    )
    formats = {**(theme.formats or {}), "boundary": boundary_format}
    return resolve_style_and_context(
        theme.model_copy(update={"title": title, "formats": formats})
    )


def test_transposed_table_renders_style_finalized_during_resolve() -> None:
    resolve_rs, resolve_ctx = _board_style(title_case="upper", boundary_format="$,.2f")
    render_rs, _render_ctx = _board_style(title_case="none", boundary_format=".1%")
    chart = TableChart(
        id="resolved_table_boundary",
        type="table",
        title="quarterly revenue",
        query=SqlQuery(sql="SELECT 1", source="test_db"),
        style=TableChartStylePatch.model_validate(
            {
                "transpose": True,
                "columns": {"revenue": {"format": "boundary"}},
            }
        ),
    )
    data = [{"revenue": 1234.5}]

    resolved = resolve(chart, data, chart_style_context=resolve_ctx)

    assert resolved.style.title.font.case == "upper"
    assert resolved.style.formats is not None
    assert resolved.style.formats["boundary"] == "$,.2f"

    svg = render_table_svg(
        resolved,
        data,
        width=720.0,
        board_style=render_rs,
    )

    assert "QUARTERLY REVENUE" in svg
    assert "$1,234.50" in svg
    assert "123450.0%" not in svg
