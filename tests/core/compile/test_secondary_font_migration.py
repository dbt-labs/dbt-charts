"""0.10.0 -> 0.11.0: style.muted moves to style.fonts.secondary.color."""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    load_yaml_schema_catalog,
)

_CHART = {"type": "bar", "query": "q1", "x": "a", "y": "b"}
_QUERIES = {"q1": {"sql": "select 1 as a, 2 as b", "source": "test"}}


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(
            raw, catalog=load_yaml_schema_catalog(), registry=registry
        )


def test_root_muted_moves_to_the_secondary_font() -> None:
    migrated = _migrate(
        {
            "title": "T",
            "queries": _QUERIES,
            "charts": {"c1": _CHART},
            "style": {"muted": "#777777"},
            "rows": ["c1"],
        }
    )
    assert migrated["style"] == {"fonts": {"secondary": {"color": "#777777"}}}
    AuthoredBoard.model_validate(migrated)


def test_nested_board_muted_moves() -> None:
    migrated = _migrate(
        {
            "title": "T",
            "queries": _QUERIES,
            "charts": {"c1": _CHART},
            "rows": [{"style": {"muted": "#777777"}, "rows": ["c1"]}],
        }
    )
    assert migrated["rows"][0]["style"] == {
        "fonts": {"secondary": {"color": "#777777"}}
    }
    AuthoredBoard.model_validate(migrated)


_SECONDARY = {"fonts": {"secondary": {"color": "#777777"}}}


def _tab(**fields: Any) -> dict[str, Any]:
    return {"title": "A", **fields}


@pytest.mark.parametrize(
    ("board", "styled_at"),
    [
        pytest.param(
            {"tabs": {"items": [_tab(style={"muted": "#777777"}, rows=["c1"])]}},
            ("tabs", "items", 0),
            id="tab",
        ),
        pytest.param(
            {
                "tabs": {
                    "items": [
                        _tab(
                            tabs={
                                "items": [_tab(style={"muted": "#777777"}, rows=["c1"])]
                            }
                        )
                    ]
                }
            },
            ("tabs", "items", 0, "tabs", "items", 0),
            id="nested-tab",
        ),
        pytest.param(
            {
                "rows": [
                    {"tabs": {"items": [_tab(style={"muted": "#777777"}, rows=["c1"])]}}
                ]
            },
            ("rows", 0, "tabs", "items", 0),
            id="tab-in-nested-board",
        ),
        pytest.param(
            {
                "tabs": {
                    "items": [
                        _tab(rows=[{"style": {"muted": "#777777"}, "rows": ["c1"]}])
                    ]
                }
            },
            ("tabs", "items", 0, "rows", 0),
            id="nested-board-in-tab",
        ),
    ],
)
def test_tab_scope_muted_moves(
    board: dict[str, Any], styled_at: tuple[str | int, ...]
) -> None:
    migrated = _migrate(
        {"title": "T", "queries": _QUERIES, "charts": {"c1": _CHART}, **board}
    )
    node: Any = migrated
    for key in styled_at:
        node = node[key]
    assert node["style"] == _SECONDARY
    AuthoredBoard.model_validate(migrated)


def test_theme_fragment_muted_moves() -> None:
    migrated = _migrate({"style": {"muted": "#777777", "accent": "#112233"}})
    assert migrated["style"] == {
        "accent": "#112233",
        "fonts": {"secondary": {"color": "#777777"}},
    }


def test_muted_beside_an_authored_secondary_font_keeps_its_siblings() -> None:
    migrated = _migrate(
        {"style": {"muted": "#777777", "fonts": {"secondary": {"size": 10}}}}
    )
    assert migrated["style"]["fonts"] == {"secondary": {"size": 10, "color": "#777777"}}


def test_a_retired_variant_token_in_muted_is_respelled() -> None:
    migrated = _migrate({"style": {"muted": "category_dark[2]"}})
    assert migrated["style"]["fonts"]["secondary"]["color"] == "category[2].dark"


def test_a_tab_scope_muted_compiles() -> None:
    result = compile(
        """title: T
queries:
  q1:
    sql: select 1 as a, 2 as b
    source: test
charts:
  c1:
    type: bar
    query: q1
    x: a
    y: b
tabs:
  items:
    - title: A
      style:
        muted: "#777777"
      rows: [c1]
"""
    )
    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]


@pytest.mark.parametrize(
    ("authored", "respelled"),
    [
        ("dbt-grays.muted", "dbt-grays.8"),
        ("dbt-creams.subtitle", "dbt-creams.7"),
        ("chrome.heading", "chrome[10]"),
        ("chrome.placeholder", "chrome[8]"),
    ],
)
def test_a_retired_scaffold_step_in_muted_is_respelled(
    authored: str, respelled: str
) -> None:
    migrated = _migrate({"style": {"muted": authored}})
    assert migrated["style"] == {"fonts": {"secondary": {"color": respelled}}}


def test_a_retired_scaffold_step_in_nested_muted_compiles() -> None:
    result = compile(
        """title: T
queries:
  q1:
    sql: select 1 as a, 2 as b
    source: test
charts:
  c1:
    type: bar
    query: q1
    x: a
    y: b
rows:
  - style:
      muted: dbt-grays.muted
    rows: [c1]
"""
    )
    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]


def test_muted_yaml_text_migrates() -> None:
    _, registry = _board_migration_context()
    text = "title: T\nstyle:\n  muted: '#777777'\nrows: []\n"
    migrated = migrate_yaml_text(
        text, catalog=load_yaml_schema_catalog(), registry=registry
    )
    assert "muted" not in str(migrated)
    assert "secondary" in str(migrated)


def test_old_muted_compiles_to_the_secondary_font_color() -> None:
    result = compile(
        """title: T
queries:
  q1: {sql: "select 1 as a, 2 as b", source: test}
charts:
  c1:
    type: bar
    query: q1
    x: a
    y: b
style:
  muted: "#123456"
rows: [c1]
"""
    )
    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]
    assert result.board is not None
    assert result.board.resolved_style.secondary_color == "#123456"


def test_muted_is_not_a_style_field_any_more() -> None:
    with pytest.raises(ValidationError, match="muted"):
        AuthoredBoard.model_validate({"title": "T", "style": {"muted": "#777777"}})
