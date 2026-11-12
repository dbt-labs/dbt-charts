"""A column's conditional rules live at ``style.columns.<col>.conditional``.

Each rule is one predicate plus the column style it applies, restricted to the
fields of ``TableColumnConfig`` marked ``Conditionable()``, and ``tone``. Rules evaluate in order:
every matching rule applies its keys, the later match wins per key, and
``default`` applies only when nothing else matched.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.migrations import migrate_yaml_text
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.style.authored import TableColumnRule
from dbt_charts.core.compile.parse.parser import load_yaml_mapping
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
    resolve_style_and_context,
)

from ._engine_render import render_table_svg

_BOARD_CTX = resolve_style_and_context(get_theme_style())[1]
_RENDER_STYLE = resolve_style(get_theme_style())


def _columns_style(
    conditional: dict[str, Any], style: dict[str, Any] | None = None
) -> dict[str, Any]:
    columns = dict((style or {}).get("columns", {}))
    for column, rules in conditional.items():
        columns[column] = {**columns.get(column, {}), "conditional": rules}
    return {**(style or {}), "columns": columns}


def _resolve(conditional: dict[str, Any], data: list[dict[str, Any]], **kwargs: Any):
    style = kwargs.pop("style", {})
    chart = TableChart(
        id="t",
        type="table",
        style=_columns_style(conditional, style),
        **kwargs,
    )
    return resolve(chart, data, chart_style_context=_BOARD_CTX)


def _render(conditional: dict[str, Any], data: list[dict[str, Any]], **kw: Any) -> str:
    resolved = _resolve(conditional, data, **kw)
    return render_table_svg(resolved, data, width=600, board_style=_RENDER_STYLE)


def test_rule_paints_background_and_font() -> None:
    svg = _render(
        {
            "status": [
                {
                    "eq": "active",
                    "background": "#e7f2ec",
                    "font": {"color": "#256b4a", "weight": "bold"},
                },
                {"default": True, "background": "#f7eee2"},
            ]
        },
        [{"status": "active"}, {"status": "idle"}],
    )
    assert "#e7f2ec" in svg
    assert "#256b4a" in svg
    assert 'font-weight="bold"' in svg
    assert "#f7eee2" in svg


def test_default_does_not_fire_beside_a_match() -> None:
    svg = _render(
        {
            "status": [
                {"eq": "active", "background": "#e7f2ec"},
                {"default": True, "background": "#f7eee2"},
            ]
        },
        [{"status": "active"}],
    )
    assert "#e7f2ec" in svg
    assert "#f7eee2" not in svg


def test_overlapping_rules_apply_together_and_the_later_wins_per_key() -> None:
    svg = _render(
        {
            "share": [
                {
                    "gte": 0.9,
                    "background": "#aaaaaa",
                    "font": {"weight": "bold"},
                },
                {"gte": 0.98, "background": "#bbbbbb"},
            ]
        },
        [{"share": 0.99}],
    )
    assert "#bbbbbb" in svg
    assert "#aaaaaa" not in svg
    assert 'font-weight="bold"' in svg


def test_rule_glyph_and_tone_paint() -> None:
    svg = _render(
        {"delta": [{"gt": 0, "glyph": "▲", "tone": "positive"}]},
        [{"delta": 5}],
    )
    assert "▲" in svg


def test_tone_without_a_glyph_anywhere_is_refused() -> None:
    with pytest.raises(ValueError, match=r"style\.columns\.delta\.conditional.*glyph"):
        _resolve({"delta": [{"gt": 0, "tone": "positive"}]}, [{"delta": 5}])


def test_tone_takes_the_glyph_from_the_column() -> None:
    resolved = _resolve(
        {"delta": [{"gt": 0, "tone": "positive"}]},
        [{"delta": 5}],
        style={"columns": {"delta": {"glyph": "▲"}}},
    )
    assert resolved.columns["delta"].conditional is not None


def test_unknown_column_is_refused() -> None:
    with pytest.raises(ValueError, match="not in the query result"):
        _resolve({"nope": [{"eq": 1, "background": "#fff"}]}, [{"a": 1}])


def test_pivot_rules_skip_the_column_check() -> None:
    data = [
        {"region": "N", "quarter": "Q1", "sales": 1},
        {"region": "S", "quarter": "Q1", "sales": 2},
    ]
    resolved = _resolve(
        {"Q1": [{"gt": 1, "background": "#112233"}]},
        data,
        rows=["region"],
        columns=["quarter"],
        values=["sales"],
    )
    svg = render_table_svg(resolved, data, width=600, board_style=_RENDER_STYLE)
    assert "#112233" in svg


def test_color_tokens_in_rules_resolve() -> None:
    resolved = _resolve({"a": [{"eq": 1, "background": "category[1]"}]}, [{"a": 1}])
    rule = resolved.columns["a"].conditional[0]
    assert rule.background.startswith("#")


@pytest.mark.parametrize(
    "paint",
    [{"align": "right"}, {"font": {"size": 12}}],
    ids=["align", "font-size"],
)
def test_a_style_that_cannot_vary_per_row_is_refused(paint: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match=r"columns\.status\.conditional\.0"):
        TableChart(
            id="t",
            type="table",
            style=_columns_style({"status": [{"eq": 1, **paint}]}),
        )


def test_pagination_cannot_be_set_in_a_rule() -> None:
    with pytest.raises(
        ValidationError, match=r"columns\.status\.conditional\.0\.pagination"
    ):
        TableChart(
            id="t",
            type="table",
            style=_columns_style({"status": [{"eq": 1, "pagination": 5}]}),
        )


def test_two_predicates_in_one_rule_are_refused() -> None:
    with pytest.raises(ValidationError, match="exactly one condition operator"):
        TableChart(
            id="t",
            type="table",
            style=_columns_style({"a": [{"eq": 1, "gt": 2, "background": "#fff"}]}),
        )


def test_a_rule_with_no_predicate_is_refused() -> None:
    with pytest.raises(ValidationError, match="exactly one condition operator"):
        TableChart(
            id="t",
            type="table",
            style=_columns_style({"a": [{"background": "#fff"}]}),
        )


def test_default_must_be_last() -> None:
    with pytest.raises(ValidationError, match="must be the last entry"):
        TableChart(
            id="t",
            type="table",
            style=_columns_style(
                {
                    "a": [
                        {"default": True, "background": "#fff"},
                        {"eq": 1, "background": "#000"},
                    ]
                }
            ),
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
_CONDITIONAL = """columns:
  a:
    conditional:
      - eq: 1
        background: "#fafafa"
"""


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "".join(f"{pad}{line}\n" for line in text.splitlines())


def test_theme_or_board_scope_refuses_conditional() -> None:
    board_style = "style:\n  charts:\n    table:\n" + _indent(_CONDITIONAL, 6)
    doc = yaml.safe_load(_BOARD.format(board_style=board_style, chart_style=""))
    with pytest.raises(ValidationError, match=r"chart-local.*style\.charts\.table"):
        AuthoredBoard.model_validate(doc)


def test_chart_scope_accepts_conditional() -> None:
    chart_style = "    style:\n" + _indent(_CONDITIONAL, 6)
    doc = yaml.safe_load(_BOARD.format(board_style="", chart_style=chart_style))
    AuthoredBoard.model_validate(doc)


def test_a_table_refuses_style_conditional() -> None:
    with pytest.raises(ValidationError, match="conditional"):
        TableChart(
            id="t",
            type="table",
            style={"conditional": {"a": [{"eq": 1, "background": "#fff"}]}},
        )


def test_column_defaults_refuses_conditional() -> None:
    with pytest.raises(ValidationError, match="conditional"):
        TableChart(
            id="t",
            type="table",
            style={
                "column_defaults": {
                    "conditional": [{"eq": 1, "background": "#fff"}],
                }
            },
        )


def test_rules_on_a_hidden_column_are_refused() -> None:
    with pytest.raises(ValidationError, match="visible"):
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


def test_conditional_body_keeps_only_conditionable_fields() -> None:
    body = TableColumnRule
    assert set(body.model_fields) >= {"background", "glyph", "glyph_color", "tone"}
    assert not {"align", "width", "format", "scale", "link"} & set(body.model_fields)
    font = body.model_fields["font"].annotation.__args__[0]
    # `extends` lets a rule font name a style.fonts entry, as static styling does.
    assert set(font.model_fields) == {
        "color",
        "weight",
        "style",
        "decoration",
        "extends",
    }
    assert body.__name__ == "TableColumnConfigConditional"


_OLD = """    # keep this comment
    conditional_formatting:
      status:
        when:
          - eq: active
            background: "#e7f2ec"
            font:
              color: "#256b4a"
              weight: bold
          - default: true
            glyph: "●"
            tone: warning
"""
_NEW_RULES = {
    "status": [
        {
            "eq": "active",
            "background": "#e7f2ec",
            "font": {"color": "#256b4a", "weight": "bold"},
        },
        {"default": True, "glyph": "●", "tone": "warning"},
    ]
}


def _old_board() -> str:
    return _BOARD.format(board_style="", chart_style=_OLD).replace(
        "select 1 as a", "select 'active' as status"
    )


def test_text_rewrite_moves_the_rules_to_their_columns_and_keeps_comments() -> None:
    migrated = migrate_yaml_text(_old_board())
    assert "# keep this comment" in migrated
    chart = load_yaml_mapping(migrated)["charts"]["c1"]
    assert "conditional_formatting" not in chart
    assert chart["style"]["columns"]["status"]["conditional"] == _NEW_RULES["status"]
    AuthoredBoard.model_validate(load_yaml_mapping(migrated))


def test_old_spelling_compiles_in_memory() -> None:
    result = compile_board(_old_board())
    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]
    assert result.board is not None
    assert "#e7f2ec" in result.board.model_dump_json()


def test_a_kpi_conditional_formatting_block_migrates_too() -> None:
    text = """title: T
queries:
  q1: {sql: "select 1 as v", source: test}
charts:
  c1:
    type: kpi
    query: q1
    value: v
    conditional_formatting:
      v:
        when:
          - gt: 0
            font:
              color: "#256b4a"
rows: [c1]
"""
    migrated = migrate_yaml_text(text)
    chart = load_yaml_mapping(migrated)["charts"]["c1"]
    assert "conditional_formatting" not in chart
    assert chart["style"]["conditional"] == {
        "v": [{"gt": 0, "value": {"font": {"color": "#256b4a"}}}]
    }


def test_rules_merge_into_an_existing_column_entry() -> None:
    text = _old_board().replace(
        "    # keep this comment\n",
        "    style:\n      columns:\n        status:\n          label: State  # kept\n"
        "        other:\n          width: 40\n",
    )
    migrated = migrate_yaml_text(text)
    assert "# kept" in migrated
    columns = load_yaml_mapping(migrated)["charts"]["c1"]["style"]["columns"]
    assert columns["status"]["label"] == "State"
    assert columns["status"]["conditional"] == _NEW_RULES["status"]
    assert columns["other"] == {"width": 40}
    AuthoredBoard.model_validate(load_yaml_mapping(migrated))


def test_rules_merge_into_an_existing_column_entry_in_memory() -> None:
    text = _old_board().replace(
        "    # keep this comment\n",
        "    style:\n      columns:\n        status:\n          label: State\n",
    )
    result = compile_board(text)
    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]
    assert "#e7f2ec" in result.board.model_dump_json()
