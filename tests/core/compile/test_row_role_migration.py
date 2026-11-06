"""``style.row.role`` leaves the style grammar; a table's role column is root ``row_role:``.

The 0.11.0 boundary moves a table chart's ``style.row.role`` to the chart's
root ``row_role:`` and strikes the board- or theme-level
``style.charts.table.row.role``, which names a column for every table and has
no chart root to move to. Claims are pinned at ``compile()``, where the
migration, the live schema and the resolver meet.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_yaml_text,
    prepare_board_mapping,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.parse.parser import load_yaml_mapping

_BOARD = """
queries:
  q:
    type: values
    rows:
      - {k: a, v: 1, kind: value}
      - {k: Total, v: 1, kind: total}
charts:
  t:
    type: table
    query: q
%s
rows:
  - t
"""


def _quiet_prepare(raw: dict[str, Any]) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return prepare_board_mapping(raw)


def test_chart_local_row_role_becomes_root_row_role() -> None:
    migrated = _quiet_prepare(
        {
            "charts": {
                "t": {
                    "type": "table",
                    "query": "q",
                    "style": {"row": {"role": "kind", "height": 20}},
                }
            },
            "rows": ["t"],
        }
    )
    chart = migrated["charts"]["t"]
    assert chart["row_role"] == "kind"
    assert chart["style"] == {"row": {"height": 20}}
    AuthoredBoard.model_validate(migrated)


def test_inline_chart_row_role_migrates() -> None:
    migrated = _quiet_prepare(
        {
            "rows": [
                {
                    "type": "table",
                    "query": "q",
                    "style": {"row": {"role": "kind"}},
                }
            ]
        }
    )
    assert migrated["rows"][0] == {"type": "table", "query": "q", "row_role": "kind"}


def test_old_spelling_compiles_like_the_new_one() -> None:
    old = compile_board(_BOARD % "    style:\n      row:\n        role: kind")
    new = compile_board(_BOARD % "    row_role: kind")
    assert old.success, old.errors
    assert new.success, new.errors


def test_board_level_row_role_is_struck_not_moved() -> None:
    raw = {
        "style": {"charts": {"table": {"row": {"role": "kind", "height": 20}}}},
        "text": "hello",
    }
    migrated = _quiet_prepare(raw)
    assert migrated["style"] == {"charts": {"table": {"row": {"height": 20}}}}
    AuthoredBoard.model_validate(migrated)


def test_style_row_role_is_not_a_live_field() -> None:
    with pytest.raises(ValidationError):
        AuthoredBoard.model_validate(
            {
                "charts": {
                    "t": {
                        "type": "table",
                        "query": "q",
                        "style": {"row": {"role": "kind"}},
                    }
                },
                "rows": ["t"],
            }
        )


def test_theme_cannot_set_row_role() -> None:
    with pytest.raises(ValidationError):
        AuthoredBoard.model_validate(
            {"style": {"charts": {"table": {"row": {"role": "kind"}}}}, "text": "x"}
        )


def test_text_rewrite_moves_the_chart_key() -> None:
    text = _BOARD % "    style:\n      row:\n        role: kind\n        height: 20"
    migrated = migrate_yaml_text(text)
    doc = load_yaml_mapping(migrated)
    assert doc["charts"]["t"]["row_role"] == "kind"
    assert doc["charts"]["t"]["style"] == {"row": {"height": 20}}
    AuthoredBoard.model_validate(doc)
