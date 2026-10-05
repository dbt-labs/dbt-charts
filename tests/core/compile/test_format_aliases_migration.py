"""Migration coverage for `style.formats` names moving under `style.formats.aliases`.

`style.formats` is a closed block (`aliases`, and later `defaults`), so an
author's names cannot collide with a key the engine reads. The declaration is a
`Move` per position in `compile/migrations/versions/v0_10_0.py`: a board in the
0.9.0 grammar compiles to the same alias table, and `dct migrate` rewrites it.
"""

from __future__ import annotations

import warnings

import pytest
import yaml

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.compiler import CompileResult
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    load_yaml_schema_catalog,
)

_QUERY_AND_CHART = """queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      number_format: money
"""

CURRENT = (
    """title: T
style:
  formats:
    aliases:
      money: "$,.0f"
      defaults: ",.1f"
"""
    + _QUERY_AND_CHART
    + """rows:
  - revenue
"""
)

FLAT = (
    """title: T
style:
  formats:
    money: "$,.0f"
    defaults: ",.1f"
"""
    + _QUERY_AND_CHART
    + """rows:
  - title: Nested
    style:
      formats:
        nested_money: "$,.2f"
    rows:
      - revenue
"""
)


def _errors(result: CompileResult) -> list[str]:
    return [f"{e.code}: {e.message}" for e in result.errors]


def test_aliases_are_the_names_a_chart_resolves() -> None:
    result = compile(CURRENT)

    assert result.success, _errors(result)
    assert result.board.chart_style_context.formats == {
        "money": "$,.0f",
        "defaults": ",.1f",
    }
    assert not [d for d in result.warnings if d.code == "WARN-SCHEMA-MIGRATED"]


def test_a_flat_formats_map_compiles_to_the_same_alias_table() -> None:
    result = compile(FLAT)

    assert result.success, _errors(result)
    assert result.board.chart_style_context.formats == {
        "money": "$,.0f",
        "defaults": ",.1f",
    }
    nested = next(
        item.board for item in result.board.layout.items if item.type == "board"
    )
    assert nested is not None
    assert nested.chart_style_context.formats == {
        "money": "$,.0f",
        "defaults": ",.1f",
        "nested_money": "$,.2f",
    }


def test_the_mapping_nests_every_flat_map_under_aliases() -> None:
    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_mapping(
            yaml.safe_load(FLAT), catalog=catalog, registry=registry
        )

    assert result["style"]["formats"] == {
        "aliases": {"money": "$,.0f", "defaults": ",.1f"}
    }
    assert result["rows"][0]["style"]["formats"] == {
        "aliases": {"nested_money": "$,.2f"}
    }


def test_dct_migrate_rewrites_the_file() -> None:
    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_yaml_text(FLAT, catalog=catalog, registry=registry)

    migrated = yaml.safe_load(result)
    assert migrated["style"]["formats"] == {
        "aliases": {"money": "$,.0f", "defaults": ",.1f"}
    }
    assert migrated["rows"][0]["style"]["formats"] == {
        "aliases": {"nested_money": "$,.2f"}
    }


def test_a_name_directly_under_formats_is_rejected_in_the_current_grammar() -> None:
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.style.theme.style import FormatsStyle

    with pytest.raises(ValidationError):
        FormatsStyle.model_validate({"money": "$,.0f"})


def test_aliases_null_clears_inherited_names() -> None:
    board = (
        """title: T
style:
  formats:
    aliases:
      money: "$,.0f"
"""
        + _QUERY_AND_CHART.replace("number_format: money", "number_format: integer")
        + """rows:
  - title: Nested
    style:
      formats:
        aliases: null
    rows:
      - revenue
"""
    )
    result = compile(board)

    assert result.success, _errors(result)
    nested = next(
        item.board for item in result.board.layout.items if item.type == "board"
    )
    assert nested is not None
    assert not nested.chart_style_context.formats


def test_a_bare_object_branch_accepts_a_wildcard() -> None:
    """A `dict[str, str]` field renders as `{"type": "object"}` with neither
    `properties` nor `additionalProperties`, which JSON Schema reads as "any
    key". A Move into or out of one must be provable."""
    from dbt_charts.core.compile.migrations.migrations import _schema_path_exists

    schema = {
        "type": "object",
        "properties": {"names": {"anyOf": [{"type": "object"}, {"type": "null"}]}},
    }
    assert _schema_path_exists(schema, ("names", "*"))
    assert not _schema_path_exists(schema, ("names", "*", "nested"))


def test_a_meta_or_extends_fragment_is_migrated() -> None:
    from dbt_charts.core.compile.migrations import prepare_board_mapping
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = prepare_board_mapping(
            {"style": {"formats": {"money": "$,.0f"}}}, model=BoardPatch
        )

    assert result["style"]["formats"] == {"aliases": {"money": "$,.0f"}}


def test_a_flat_name_beside_aliases_moves_in_and_aliases_stays_put() -> None:
    """A trailing `*` never takes a key the live grammar declares: `aliases`
    is current grammar, not a name, so only the flat name moves."""
    board = CURRENT.replace(
        '      defaults: ",.1f"\n', '      defaults: ",.1f"\n    stray: "$,.2f"\n'
    )
    result = compile(board)

    assert result.success, _errors(result)
    assert result.board.chart_style_context.formats == {
        "money": "$,.0f",
        "defaults": ",.1f",
        "stray": "$,.2f",
    }


def test_formats_null_still_clears_inherited_names() -> None:
    """`style.formats: null` meant "no names here" in the flat grammar and
    still does, so a board that wrote it needs no migration."""
    board = (
        """title: T
style:
  formats:
    aliases:
      money: "$,.0f"
"""
        + _QUERY_AND_CHART.replace("number_format: money", "number_format: integer")
        + """rows:
  - title: Nested
    style:
      formats: null
    rows:
      - revenue
"""
    )
    result = compile(board)

    assert result.success, _errors(result)
    nested = next(
        item.board for item in result.board.layout.items if item.type == "board"
    )
    assert nested is not None
    assert not nested.chart_style_context.formats


def _nested(depth: int, formats_yaml: str) -> str:
    """A board whose innermost of `depth` nested boards sets its own formats."""
    inner = "rows:\n  - revenue\n"
    for level in range(depth, 0, -1):
        body = (
            f"title: Level {level}\nstyle:\n  formats:\n{formats_yaml}"
            if level == depth
            else f"title: Level {level}\n"
        ) + inner
        inner = "rows:\n" + "".join(
            ("  - " if i == 0 else "    ") + line + "\n"
            for i, line in enumerate(body.rstrip("\n").split("\n"))
        )
    return "title: T\n" + _QUERY_AND_CHART + inner


def _innermost(result: CompileResult, depth: int) -> object:
    board = result.board
    for _ in range(depth):
        board = next(i.board for i in board.layout.items if i.type == "board")
    return board


@pytest.mark.parametrize("depth", [3, 5])
def test_a_flat_map_on_a_deeply_nested_board_is_migrated(depth: int) -> None:
    result = compile(_nested(depth, '    money: "$,.0f"\n'))

    assert result.success, _errors(result)
    assert _innermost(result, depth).chart_style_context.formats == {"money": "$,.0f"}


@pytest.mark.parametrize(
    "flat",
    [
        '    aliases: "$,.2f"\n    money: "$,.0f"\n',
        '    money: "$,.0f"\n    aliases: "$,.2f"\n',
    ],
    ids=["aliases-first", "aliases-last"],
)
def test_a_flat_name_called_aliases_is_migrated_like_any_other(flat: str) -> None:
    board = (
        "title: T\nstyle:\n  formats:\n"
        + flat
        + _QUERY_AND_CHART
        + "rows:\n  - revenue\n"
    )
    result = compile(board)

    assert result.success, _errors(result)
    assert result.board.chart_style_context.formats == {
        "aliases": "$,.2f",
        "money": "$,.0f",
    }


@pytest.mark.parametrize(
    ("board", "path"),
    [
        (_nested(3, '    money: "$,.0f"\n'), ("rows", 0, "rows", 0, "rows", 0)),
        (
            "title: T\nstyle:\n  formats:\n"
            '    money: "$,.0f"\n    aliases: "$,.2f"\n'
            + _QUERY_AND_CHART
            + "rows:\n  - revenue\n",
            (),
        ),
    ],
    ids=["nested-three-deep", "name-called-aliases"],
)
def test_dct_migrate_rewrites_the_hard_shapes(board: str, path: tuple) -> None:
    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = yaml.safe_load(
            migrate_yaml_text(board, catalog=catalog, registry=registry)
        )

    node = migrated
    for key in path:
        node = node[key]
    flat = yaml.safe_load(board)
    for key in path:
        flat = flat[key]
    assert node["style"]["formats"] == {"aliases": flat["style"]["formats"]}


def test_dct_migrate_keeps_the_style_block_and_its_comments_in_place() -> None:
    board = (
        "# Revenue board\ntitle: T\n# Shared number formats\nstyle:\n"
        '  formats:\n    money: "$,.0f"\n' + _QUERY_AND_CHART + "rows:\n  - revenue\n"
    )
    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_yaml_text(board, catalog=catalog, registry=registry)

    assert result.startswith(
        "# Revenue board\ntitle: T\n# Shared number formats\nstyle:\n"
        '  formats:\n    aliases:\n      money: "$,.0f"\n'
    ), result


def test_a_flat_map_on_a_tab_is_migrated() -> None:
    board = (
        "title: T\n"
        + _QUERY_AND_CHART
        + "tabs:\n  items:\n    - title: A\n      style:\n        formats:\n"
        '          money: "$,.0f"\n      rows:\n        - revenue\n'
    )
    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_mapping(
            yaml.safe_load(board), catalog=catalog, registry=registry
        )

    assert result["tabs"]["items"][0]["style"]["formats"] == {
        "aliases": {"money": "$,.0f"}
    }


def test_a_free_form_value_carrying_style_formats_is_left_alone() -> None:
    """`**` tries `style.formats` only where the grammar declares `style`."""
    board = yaml.safe_load(FLAT)
    board["variables"] = {
        "v": {"input": "text", "default": {"style": {"formats": {"x": "y"}}}}
    }
    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_mapping(board, catalog=catalog, registry=registry)

    assert result["variables"]["v"]["default"] == {"style": {"formats": {"x": "y"}}}
    assert result["style"]["formats"] == {
        "aliases": {"money": "$,.0f", "defaults": ",.1f"}
    }


def test_retired_keys_reports_flat_names_at_their_board_path() -> None:
    """The guided hint for a board migration gave up on names the real path."""
    from dbt_charts.core.compile.migrations.migrations import retired_keys

    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()

    found = retired_keys(yaml.safe_load(FLAT), catalog, registry)

    assert found[("style", "formats", "money")].successor == (
        "style",
        "formats",
        "aliases",
        "money",
    )
    assert found[("rows", "0", "style", "formats", "nested_money")].successor == (
        "rows",
        "0",
        "style",
        "formats",
        "aliases",
        "nested_money",
    )
