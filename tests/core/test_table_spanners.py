"""Authored ``spanners:`` on flat tables: a label centered over adjacent columns.

Spanners share the pivot group-header painter, so a pivot table and an authored
spanner look identical. Errors never fix: unknown, non-adjacent, or
double-claimed columns raise rather than reorder or drop.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored import TableChart as AuthoredTable
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError

from ._engine_render import render_table_svg

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())
_BOARD_RENDER_STYLE = resolve_style(get_theme_style())

_DATA = [
    {"region": "West", "q1_rev": 10, "q2_rev": 12, "q1_heads": 3, "q2_heads": 4},
    {"region": "East", "q1_rev": 20, "q2_rev": 22, "q1_heads": 5, "q2_heads": 6},
]
_REVENUE = {"label": "Revenue", "columns": ["q1_rev", "q2_rev"]}
_HEADS = {"label": "Headcount", "columns": ["q1_heads", "q2_heads"]}


def _render(spanners: list[dict], style: dict | None = None) -> str:
    chart = TableChart(
        id="t", type="table", spanners=spanners or None, style=style or {}
    )
    resolved = resolve(chart, _DATA, chart_style_context=_BOARD_CTX)
    return render_table_svg(resolved, _DATA, width=700, board_style=_BOARD_RENDER_STYLE)


def _text_x(svg: str, text: str) -> float:
    m = re.search(rf'<text x="([\d.]+)"[^>]*>{text}</text>', svg)
    assert m, f"{text!r} not rendered"
    return float(m.group(1))


def _text_y(svg: str, text: str) -> float:
    m = re.search(rf'<text [^>]*? y="([\d.]+)"[^>]*>{text}</text>', svg)
    assert m, f"{text!r} not rendered"
    return float(m.group(1))


def _svg_height(svg: str) -> float:
    m = re.search(r'<svg[^>]* height="([\d.]+)"', svg)
    assert m
    return float(m[1])


def _rules(svg: str) -> list[tuple[float, float]]:
    """(x, width) of every spanner-sized 1px rule (the full-width header rule excluded)."""
    return [
        (float(x), float(w))
        for x, w in re.findall(
            r'<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)" height="1(?:\.0)?" '
            r'fill="[^"]+" shape-rendering="crispEdges"/>',
            svg,
        )
        if float(w) < 600
    ]


class TestModel:
    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden|not permitted"):
            AuthoredTable(
                type="table",
                spanners=[{"label": "A", "columns": ["a"], "id": "x"}],
            )

    def test_empty_columns_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1"):
            AuthoredTable(type="table", spanners=[{"label": "A", "columns": []}])

    def test_duplicate_column_in_one_spanner_rejected(self) -> None:
        with pytest.raises(ValidationError, match="lists column .q1. twice"):
            AuthoredTable(
                type="table", spanners=[{"label": "A", "columns": ["q1", "q1"]}]
            )

    def test_column_in_two_spanners_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at most one spanner"):
            AuthoredTable(
                type="table",
                spanners=[
                    {"label": "A", "columns": ["q1", "q2"]},
                    {"label": "B", "columns": ["q2", "q3"]},
                ],
            )

    def test_pivot_channels_rejected(self) -> None:
        with pytest.raises(ValidationError, match="spanners.*pivot"):
            AuthoredTable(
                type="table",
                rows=["region"],
                spanners=[{"label": "A", "columns": ["a"]}],
            )


class TestRender:
    def test_label_centered_over_span_with_rule(self) -> None:
        svg = _render([_REVENUE])
        (x, w) = _rules(svg)[0]
        assert _text_x(svg, "Revenue") == pytest.approx(x + w / 2)

    def test_spanner_row_sits_above_leaf_headers_and_grows_height(self) -> None:
        plain = _render([])
        spanned = _render([_REVENUE])
        assert _text_y(spanned, "Revenue") < _text_y(spanned, "Q1 Rev")
        assert _svg_height(spanned) > _svg_height(plain)

    def test_adjacent_spanners_have_separate_rules(self) -> None:
        rules = _rules(_render([_REVENUE, _HEADS]))
        assert len(rules) == 2
        (x1, w1), (x2, _) = rules
        assert x1 + w1 < x2, "rules must not touch: the gap reads as a group break"

    def test_unspanned_column_gets_no_rule(self) -> None:
        # region (stub) is outside every spanner: only the two spans are ruled.
        (x, _), _ = _rules(_render([_REVENUE, _HEADS]))
        assert x > _text_x(_render([]), "Region") + 20

    def test_row_numbers_do_not_shift_the_span(self) -> None:
        svg = _render([_REVENUE], {"row_numbers": {"visible": True}})
        x, w = _rules(svg)[0]
        for header in ("Q1 Rev", "Q2 Rev"):
            assert x <= _text_x(svg, header) <= x + w, header
        assert _text_x(svg, "Region") < x

    def test_hidden_spanned_column_narrows_span(self) -> None:
        full = _rules(_render([_REVENUE]))[0]
        narrowed = _rules(
            _render([_REVENUE], {"columns": {"q2_rev": {"visible": False}}})
        )[0]
        assert narrowed[1] < full[1]

    def test_all_hidden_spanner_is_dropped(self) -> None:
        svg = _render(
            [_REVENUE, _HEADS],
            {
                "columns": {
                    "q1_rev": {"visible": False},
                    "q2_rev": {"visible": False},
                }
            },
        )
        assert "Revenue" not in svg
        assert "Headcount" in svg

    def test_single_all_hidden_spanner_reserves_no_band(self) -> None:
        hide = {
            "columns": {
                "q1_rev": {"visible": False},
                "q2_rev": {"visible": False},
            }
        }
        assert _svg_height(_render([_REVENUE], hide)) == _svg_height(_render([], hide))

    def test_spanners_on_empty_result_render_without_raising(self) -> None:
        chart = TableChart(id="t", type="table", spanners=[_REVENUE])
        resolved = resolve(chart, [], chart_style_context=_BOARD_CTX)
        render_table_svg(resolved, [], width=700, board_style=_BOARD_RENDER_STYLE)

    def test_contiguity_is_checked_on_visible_columns(self) -> None:
        # q1_rev and q1_heads are adjacent only once q2_rev is hidden.
        svg = _render(
            [{"label": "Span", "columns": ["q1_rev", "q1_heads"]}],
            {
                "columns": {
                    "q2_rev": {"visible": False},
                }
            },
        )
        assert "Span" in svg


class TestErrors:
    def test_unknown_column(self) -> None:
        with pytest.raises(ChartDataError, match="nope"):
            _render([{"label": "X", "columns": ["q1_rev", "nope"]}])

    def test_non_contiguous_columns(self) -> None:
        with pytest.raises(ChartDataError, match="q1_rev.*q1_heads|adjacent"):
            _render([{"label": "X", "columns": ["q1_rev", "q1_heads"]}])


class TestThroughCompile:
    def test_spanners_survive_normalize_chart(self) -> None:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.normalize.charts import normalize_chart

        chart = normalize_chart(
            "c",
            {"type": "table", "query": "q", "spanners": [_REVENUE]},
            {"q": SqlQuery(sql="SELECT 1", source="test")},
            sources={},
        )
        assert [s.label for s in chart.spanners] == ["Revenue"]
