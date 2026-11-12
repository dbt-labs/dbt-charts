"""``style.row.conditional``: a table rule keyed by any query column paints the row.

The key is the tested column (hidden ones included); the body paints every visible
cell of the matching row (``background``, ``font``) or named cells
(``columns.<col>``, which alone may take ``glyph``, ``glyph_color`` and ``tone``).
Evaluation is the column rules' (every match applies, the later wins per key,
``default`` only when nothing matched). A cell rule beats a row rule, and ``scale``
paints over a row rule on its own cell.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.normalized import BarChart, TableChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
    resolve_style_and_context,
)

from ._engine_render import render_table_svg

_BOARD_CTX = resolve_style_and_context(get_theme_style())[1]
_RENDER_STYLE = resolve_style(get_theme_style())

_ROWS = [
    {"name": "Acme", "status": "churned", "profit": -5},
    {"name": "Bolt", "status": "active", "profit": 7},
]


def _chart(
    conditional: dict[str, Any], style: dict[str, Any] | None = None, **kwargs: Any
) -> TableChart:
    return TableChart(
        id="t",
        type="table",
        style={**(style or {}), "row": {"conditional": conditional}},
        **kwargs,
    )


def _resolve(
    conditional: dict[str, Any],
    data: list[dict[str, Any]] = _ROWS,
    style: dict[str, Any] | None = None,
    **kwargs: Any,
):
    return resolve(
        _chart(conditional, style, **kwargs), data, chart_style_context=_BOARD_CTX
    )


def _render(
    conditional: dict[str, Any],
    data: list[dict[str, Any]] = _ROWS,
    style: dict[str, Any] | None = None,
    **kwargs: Any,
) -> str:
    resolved = _resolve(conditional, data, style, **kwargs)
    return render_table_svg(resolved, data, width=600, board_style=_RENDER_STYLE)


def _fills(svg: str, color: str) -> int:
    return len(re.findall(rf'fill="{color}"', svg))


def test_rule_resolves_onto_the_table_style() -> None:
    resolved = _resolve({"status": [{"eq": "churned", "background": "#aaaaaa"}]})
    rule = resolved.style.table.row.conditional["status"][0]
    assert rule.background == "#aaaaaa"


def test_color_tokens_in_rules_resolve() -> None:
    resolved = _resolve(
        {
            "status": [
                {
                    "eq": "churned",
                    "background": "category[1]",
                    "columns": {"name": {"font": {"color": "category[2]"}}},
                }
            ]
        }
    )
    rule = resolved.style.table.row.conditional["status"][0]
    assert rule.background.startswith("#")
    assert rule.columns["name"].font.color.startswith("#")


def test_a_row_rule_paints_every_visible_cell() -> None:
    svg = _render({"status": [{"eq": "churned", "background": "#abcdef"}]})
    assert _fills(svg, "#abcdef") == 3


def test_a_row_rule_paints_the_font_of_every_cell() -> None:
    svg = _render(
        {"status": [{"eq": "churned", "font": {"decoration": "line-through"}}]}
    )
    assert svg.count("line-through") == 3


def test_a_row_rule_on_a_hidden_flag_column_paints_the_row() -> None:
    data = [{"name": "Acme", "_flag": True}, {"name": "Bolt", "_flag": False}]
    svg = _render({"_flag": [{"eq": True, "background": "#abcdef"}]}, data)
    assert _fills(svg, "#abcdef") == 1


def test_a_row_rule_paints_a_named_cell_not_the_row() -> None:
    svg = _render(
        {
            "profit": [
                {"lt": 0, "columns": {"name": {"background": "#abcdef"}}},
            ]
        }
    )
    assert _fills(svg, "#abcdef") == 1


def test_a_cell_target_takes_glyph_and_tone() -> None:
    svg = _render(
        {
            "profit": [
                {"lt": 0, "columns": {"name": {"glyph": "▼", "tone": "negative"}}},
            ]
        }
    )
    assert svg.count("▼") == 1


def test_a_cell_rule_beats_a_row_rule_on_the_same_cell() -> None:
    svg = _render(
        {"status": [{"eq": "churned", "background": "#1a2b3c"}]},
        style={
            "columns": {"profit": {"conditional": [{"lt": 0, "background": "#2a3b4c"}]}}
        },
    )
    assert _fills(svg, "#1a2b3c") == 2
    assert _fills(svg, "#2a3b4c") == 1


def test_a_scale_paints_over_a_row_rule_on_its_own_cell() -> None:
    scale = {"background": {"palette": ["#fff7d6", "#92400e"]}}
    svg = _render(
        {"status": [{"eq": "churned", "background": "#1a2b3c"}]},
        style={"columns": {"profit": {"scale": scale}}},
    )
    assert _fills(svg, "#1a2b3c") == 2


def test_the_later_rule_wins_per_key_and_default_waits() -> None:
    svg = _render(
        {
            "profit": [
                {"lt": 10, "background": "#1a2b3c", "font": {"weight": "bold"}},
                {"lt": 0, "background": "#2a3b4c"},
                {"default": True, "background": "#3a4b5c"},
            ]
        }
    )
    assert _fills(svg, "#2a3b4c") == 3
    assert _fills(svg, "#1a2b3c") == 3
    assert _fills(svg, "#3a4b5c") == 0


def test_a_row_rule_applies_to_a_summary_row() -> None:
    data = [
        {"name": "Acme", "kind": "row", "profit": 1},
        {"name": "Total", "kind": "total", "profit": 1},
    ]
    svg = _render(
        {"profit": [{"eq": 1, "background": "#abcdef"}]},
        data,
        row_role="kind",
    )
    assert _fills(svg, "#abcdef") == 4


def test_unknown_tested_column_is_refused() -> None:
    with pytest.raises(ValueError, match=r"style\.row\.conditional.*'nope'.*Available"):
        _resolve({"nope": [{"eq": 1, "background": "#fff"}]})


def test_cell_target_that_is_not_a_query_column_is_refused() -> None:
    with pytest.raises(ValueError, match=r"columns\.nope.*visible query column"):
        _resolve({"status": [{"eq": "x", "columns": {"nope": {"background": "#fff"}}}]})


def test_cell_target_that_is_hidden_is_refused() -> None:
    with pytest.raises(ValueError, match=r"columns\.profit.*visible query column"):
        _resolve(
            {"status": [{"eq": "x", "columns": {"profit": {"background": "#fff"}}}]},
            style={"columns": {"profit": {"visible": False}}},
        )


def test_cell_target_that_is_underscore_hidden_is_refused() -> None:
    data = [{"name": "Acme", "_flag": 1}]
    with pytest.raises(ValueError, match=r"columns\._flag.*visible query column"):
        _resolve(
            {"name": [{"eq": "x", "columns": {"_flag": {"background": "#fff"}}}]},
            data,
        )


@pytest.mark.parametrize("key", ["tone", "glyph_color"])
def test_a_glyph_ink_without_a_glyph_is_refused(key: str) -> None:
    value = "positive" if key == "tone" else "#ff00aa"
    with pytest.raises(ValueError, match=rf"columns\.name.*{key}.*glyph"):
        _resolve({"status": [{"eq": "x", "columns": {"name": {key: value}}}]})


def test_a_glyph_color_paints_the_column_glyph() -> None:
    svg = _render(
        {
            "status": [
                {"eq": "churned", "columns": {"name": {"glyph_color": "#ff00aa"}}}
            ]
        },
        style={"columns": {"name": {"glyph": "●"}}},
    )
    assert _fills(svg, "#ff00aa") == 1


def test_a_tone_paints_the_column_glyph() -> None:
    plain = _render({}, style={"columns": {"name": {"glyph": "●"}}})
    toned = _render(
        {"status": [{"eq": "churned", "columns": {"name": {"tone": "negative"}}}]},
        style={"columns": {"name": {"glyph": "●"}}},
    )
    assert toned != plain


@pytest.mark.parametrize("key", ["glyph", "glyph_color", "tone"])
def test_a_row_level_glyph_key_is_unrepresentable(key: str) -> None:
    value = "negative" if key == "tone" else "#fff" if key == "glyph_color" else "▲"
    with pytest.raises(ValidationError, match=rf"row\.conditional.*{key}"):
        _chart({"status": [{"eq": "x", key: value}]})


def test_two_tested_columns_patching_one_property_are_refused() -> None:
    with pytest.raises(ValueError, match=r"'status' and 'profit'.*both patch"):
        _resolve(
            {
                "status": [{"eq": "churned", "background": "#1a2b3c"}],
                "profit": [{"lt": 0, "background": "#2a3b4c"}],
            }
        )


def test_two_tested_columns_patching_different_targets_are_accepted() -> None:
    _resolve(
        {
            "status": [{"eq": "churned", "background": "#1a2b3c"}],
            "profit": [{"lt": 0, "columns": {"name": {"background": "#2a3b4c"}}}],
        }
    )


def test_a_rule_with_no_style_is_refused() -> None:
    with pytest.raises(ValidationError, match="at least one style"):
        _chart({"status": [{"eq": "x"}]})


def test_default_must_be_last() -> None:
    with pytest.raises(ValidationError, match="must be the last entry"):
        _chart(
            {
                "status": [
                    {"default": True, "background": "#fff"},
                    {"eq": 1, "background": "#000"},
                ]
            }
        )


def test_pivot_tables_refuse_row_rules() -> None:
    data = [{"region": "N", "quarter": "Q1", "sales": 1}]
    with pytest.raises(ValueError, match=r"style\.row\.conditional.*pivot"):
        _resolve(
            {"region": [{"eq": "N", "background": "#fff"}]},
            data,
            rows=["region"],
            columns=["quarter"],
            values=["sales"],
        )


def test_transposed_tables_refuse_row_rules() -> None:
    with pytest.raises(ValueError, match=r"style\.row\.conditional.*transpose"):
        _resolve(
            {"status": [{"eq": "x", "background": "#fff"}]},
            style={"transpose": True},
        )


def test_a_hidden_column_cell_rule_hints_at_row_rules() -> None:
    with pytest.raises(ValidationError, match=r"style\.row\.conditional"):
        TableChart(
            id="t",
            type="table",
            style={
                "columns": {
                    "a": {
                        "visible": False,
                        "conditional": [{"eq": 1, "background": "#fff"}],
                    }
                }
            },
        )


def test_only_a_table_takes_row_rules() -> None:
    with pytest.raises(ValidationError, match="row"):
        BarChart(
            id="b",
            type="bar",
            style={"row": {"conditional": {"a": [{"eq": 1, "background": "#fff"}]}}},
        )


_BOARD = """title: T
queries:
  q1: {{sql: "select 1 as a", source: test}}
{board_style}charts:
  c1:
    type: table
    query: q1
{chart_style}rows: [c1]
"""
_ROW_RULES = """row:
  conditional:
    a:
      - eq: 1
        background: "#fafafa"
"""


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "".join(f"{pad}{line}\n" for line in text.splitlines())


def test_theme_or_board_scope_refuses_row_rules() -> None:
    board_style = "style:\n  charts:\n    table:\n" + _indent(_ROW_RULES, 6)
    doc = yaml.safe_load(_BOARD.format(board_style=board_style, chart_style=""))
    with pytest.raises(ValidationError, match=r"chart-local.*style\.charts\.table"):
        AuthoredBoard.model_validate(doc)


def test_chart_scope_accepts_row_rules() -> None:
    chart_style = "    style:\n" + _indent(_ROW_RULES, 6)
    doc = yaml.safe_load(_BOARD.format(board_style="", chart_style=chart_style))
    AuthoredBoard.model_validate(doc)
