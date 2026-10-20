"""Migration coverage for the retired scaffold aliases
(dbt_charts.core.compile.migrations.versions.v0_11_0): `separator`,
`fill-muted`, `spark-empty`, `placeholder` and `accent` on `dbt-grays` and
`dbt-creams` respell to a ladder step or `info.solid-hover`.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.migrations import prepare_board_mapping
from dbt_charts.core.compile.migrations.migrations import (
    _board_migration_context,
    migrate_yaml_text,
)

_CASES = [
    ("dbt-grays.separator", "dbt-grays.border"),
    ("dbt-grays.fill-muted", "dbt-grays.grid"),
    ("dbt-grays.spark-empty", "dbt-grays.grid-emphasis"),
    ("dbt-grays.placeholder", "dbt-grays.muted"),
    ("dbt-grays.accent", "info.solid-hover"),
    ("dbt-creams.separator", "dbt-creams.border"),
    ("dbt-creams.fill-muted", "dbt-creams.grid"),
    ("dbt-creams.spark-empty", "dbt-creams.grid-emphasis"),
    ("dbt-creams.placeholder", "dbt-creams.muted"),
    ("dbt-creams.accent", "info.solid-hover"),
]

_QUERIES = {"q": {"columns": ["a", "b"], "values": [[1, 2]]}}


def _chart(color: str) -> dict[str, Any]:
    return {
        "type": "bar",
        "query": "q",
        "x": "a",
        "y": "b",
        "style": {"color": {"static": color}},
    }


def _board(color: str) -> dict[str, Any]:
    return {
        "title": "t",
        "style": {
            "charts": {"category_colors": {"region": {"values": {"A": color}}}},
        },
        "charts": {"c": _chart(color)},
        "queries": _QUERIES,
        "rows": ["c"],
    }


@pytest.mark.parametrize(("old", "new"), _CASES)
def test_a_retired_alias_respells_wherever_a_color_is_authored(
    old: str, new: str
) -> None:
    result = prepare_board_mapping(_board(old))

    assert result["charts"]["c"]["style"]["color"]["static"] == new
    assert result["style"]["charts"]["category_colors"]["region"]["values"]["A"] == new


@pytest.mark.parametrize(("old", "new"), _CASES)
def test_a_retired_alias_in_a_nested_boards_chart_respells(old: str, new: str) -> None:
    mapping = _board("category[1]")
    mapping["rows"] = [{"charts": {"n": _chart(old)}, "rows": ["n"]}, "c"]

    result = prepare_board_mapping(mapping)

    assert result["rows"][0]["charts"]["n"]["style"]["color"]["static"] == new


def test_a_ladder_step_is_left_alone() -> None:
    mapping = _board("dbt-grays.border")

    assert prepare_board_mapping(mapping) == mapping


@pytest.mark.parametrize(("old", "new"), _CASES)
def test_a_board_authoring_a_retired_alias_compiles_to_its_successor(
    old: str, new: str
) -> None:
    def source(color: str) -> str:
        return (
            "extends: clarity\n"
            "style:\n  charts:\n    table:\n      columns:\n"
            f"        rev:\n          background: {color}\n"
            "rows:\n  - text: hello\n"
        )

    migrated = compile_board(source(old))
    expected = compile_board(source(new))

    assert migrated.success, migrated.errors
    assert (
        migrated.board.chart_style_context.table.columns["rev"].background
        == expected.board.chart_style_context.table.columns["rev"].background
    )


def test_migrate_yaml_text_rewrites_the_authored_text_to_the_dev_boundary() -> None:
    catalog, registry = _board_migration_context()
    text = (
        "title: t\n"
        "style:\n  charts:\n    category_colors:\n      region:\n"
        "        values:\n          A: dbt-grays.separator\n"
        "rows:\n  - text: hello\n"
    )

    migrated = migrate_yaml_text(
        text, catalog=catalog, registry=registry, stop_target=catalog.dev.version
    )

    assert "dbt-grays.border" in migrated
    assert "separator" not in migrated


def _compiles(style: str) -> None:
    result = compile_board(f"extends: clarity\nstyle:\n{style}rows:\n  - text: hello\n")
    assert result.success, result.errors


@pytest.mark.parametrize(
    "style",
    [
        "  charts:\n    table:\n      row:\n        divider:\n"
        "          color: dbt-grays.separator\n",
        "  text:\n    divider:\n      color: dbt-grays.separator\n",
        "  fonts:\n    heading:\n      color: dbt-grays.placeholder\n",
    ],
    ids=["table-row-divider", "text-divider", "heading-font"],
)
def test_a_color_field_the_010_grammar_added_compiles_with_a_retired_alias(
    style: str,
) -> None:
    _compiles(style)


def test_a_090_board_renames_a_rule_color_then_respells_it() -> None:
    """`rule.color` becomes `divider.color` at 0.10.0; 0.11.0 respells the value."""
    _compiles(
        "  charts:\n    table:\n      row:\n        rule:\n"
        "          color: dbt-grays.separator\n"
    )


def test_a_support_table_neutral_color_compiles_with_a_retired_alias() -> None:
    _compiles(
        "  charts:\n    support_table:\n      neutral_color: dbt-grays.placeholder\n"
    )


def test_every_color_field_of_the_board_model_has_a_respell_move() -> None:
    from dbt_charts.core.compile.migrations.migrations import (
        _board_migration_context,
        _closure,
    )
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.models.markers import Color

    color_fields = {
        name
        for model in _closure(AuthoredBoard)
        for name, field in model.model_fields.items()
        if any(isinstance(marker, Color) for marker in field.metadata)
    }
    _, registry = _board_migration_context()
    reached = {
        move.old_path[-1]
        for move in registry.moves
        if move.value_map is not None
        and move.value_map.get("dbt-grays.separator") == "dbt-grays.border"
    }

    assert color_fields - reached == set()
