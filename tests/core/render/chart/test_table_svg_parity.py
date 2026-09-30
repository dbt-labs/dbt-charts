"""Table rendering tests — smoke and behavioral contract for render_table_svg.

Originally written as V1==V2 parity tests; the V1 oracle (render_table_svg) is
retired so each case now verifies the render path directly.
"""

from __future__ import annotations

import re
from typing import Any

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.normalized.table import TableChart
from dbt_charts.core.compile.models.style.authored import (
    TableChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.table import render_table_svg


def _board_style() -> Any:
    return resolve_style_and_context(get_theme_style(get_default_theme_name()))


def _chart(
    *,
    style: dict[str, Any] | None = None,
    conditional_formatting: dict[str, Any] | None = None,
    rows: list[str] | None = None,
    columns: list[str] | None = None,
    values: list[str] | None = None,
) -> TableChart:
    return TableChart(
        id="t1",
        type="table",
        style=TableChartStylePatch.model_validate(style) if style else None,
        conditional_formatting=conditional_formatting,
        rows=rows,
        columns=columns,
        values=values,
    )


def _render(
    *,
    data: list[dict[str, Any]],
    style: dict[str, Any] | None = None,
    conditional_formatting: dict[str, Any] | None = None,
    rows: list[str] | None = None,
    columns: list[str] | None = None,
    values: list[str] | None = None,
    width: float = 400,
) -> str:
    board_rs, board_ctx = _board_style()
    chart = _chart(
        style=style,
        conditional_formatting=conditional_formatting,
        rows=rows,
        columns=columns,
        values=values,
    )
    from dbt_charts.core.compile.models.chart.resolved.table import ResolvedTableChart

    resolved = resolve(chart, data, board_ctx)
    assert isinstance(resolved, ResolvedTableChart)
    return render_table_svg(resolved, data, width=width, board_style=board_rs)


def test_v2_table_svg_plain_columns() -> None:
    """Flat table with an explicit per-column display config (style.columns)."""
    data = [{"region": "US", "amount": 100}, {"region": "EU", "amount": 200}]
    style = {
        "columns": {"amount": {"format": ",.0f", "label": "Amount", "visible": True}}
    }
    svg = _render(style=style, data=data)
    assert "<svg" in svg


def test_v2_table_svg_pivoted() -> None:
    """Pivot cross-tab: rows/columns/values channels reshape long → wide."""
    data = [
        {"region": "US", "month": "Jan", "amount": 100},
        {"region": "US", "month": "Feb", "amount": 200},
        {"region": "EU", "month": "Jan", "amount": 150},
        {"region": "EU", "month": "Feb", "amount": 250},
    ]
    svg = _render(rows=["region"], columns=["month"], values=["amount"], data=data)
    assert "<svg" in svg


def test_v2_table_svg_conditional_formatting() -> None:
    """Chart-level conditional_formatting block drives per-cell styling."""
    data = [{"amount": -500}, {"amount": 200}]
    cf = {"amount": {"when": [{"lt": 0, "background": "#fee2e2"}]}}
    svg = _render(conditional_formatting=cf, data=data)
    assert "#fee2e2" in svg


def test_v2_table_svg_header_overflow() -> None:
    """Authored style.header_overflow drives header wrapping.

    Width is narrow enough that "wrap" actually changes the rendered header
    (more <tspan> lines) versus the default overflow mode.
    """
    data = [
        {"a_very_long_column_name_indeed": 1, "another_rather_long_column": 2},
    ]
    style = {"header_overflow": "wrap"}
    svg = _render(style=style, data=data, width=150)
    assert svg.count("<tspan") > 2


def test_v2_table_svg_pagination_override() -> None:
    """Chart-local style.pagination override drives visible-row slicing.

    The chart authors page_rows=1 over 5 rows (past the grow cap) — only the
    first row's value may appear on page one. Wrapped in the interactive-host contract (a
    single page rendered, onclick=updateVariable) so this stays a pure
    page_rows-slicing check; the static-export contract pre-renders every
    page into a toggled group and is covered separately in
    ``TestStaticMultiPagePagination`` (test_table_pagination_controls.py).
    """
    from dbt_charts.core.render.controls import interactive_controls

    data = [{"v": v} for v in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    style = {"pagination": {"enabled": True, "page_rows": 1}}
    with interactive_controls(True):
        svg = _render(style=style, data=data, width=200)
    assert "AAA" in svg
    assert "BBB" not in svg and "CCC" not in svg


def test_v2_table_svg_column_defaults() -> None:
    """style.column_defaults applies uniform presentation without an explicit columns list."""
    data = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    style = {"column_defaults": {"align": "center", "width": 60}}
    svg = _render(style=style, data=data)
    assert "<svg" in svg


def test_v2_table_svg_negative_numbers_never_use_ascii_hyphen() -> None:
    """table.py's numeric-cell path always calls format_kpi_parts with
    default_number=True, so format_spec is never empty and the bare-digit
    fallback (a Python f-string, which would emit ASCII "-") is
    unreachable — every negative cell goes through d3_format, which always
    emits the real minus sign (U+2212). Covers both the theme-default (no
    explicit format:) and an explicit-format column."""
    data = [{"delta": -1234.5, "revenue": -47300}]
    style = {"columns": {"revenue": {"format": "integer", "visible": True}}}
    svg = _render(style=style, data=data)
    assert "−" in svg
    assert not re.search(r">-\d", svg)


def test_v2_table_svg_native_formatter_columns_never_use_ascii_hyphen() -> None:
    """PREDEFINED_NATIVE columns (percent_number_delta, percentage_points_delta)
    bypass d3_format entirely — their Python f-string lambdas must emit the
    house minus glyph (U+2212) themselves, same as every d3-formatted column."""
    data = [{"rate_delta": -1.5, "pts_delta": -3.2}]
    style = {
        "columns": {
            "rate_delta": {"format": "percent_number_delta"},
            "pts_delta": {"format": "percentage_points_delta"},
        }
    }
    svg = _render(style=style, data=data)
    assert not re.search(r">-\d", svg)
    assert svg.count("−") >= 2
