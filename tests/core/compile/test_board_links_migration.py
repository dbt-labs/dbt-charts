"""0.9.0 -> 0.10.0: board ``auto_link`` becomes ``links.auto``
(dbt_charts.core.compile.migrations.versions.v0_10_0).
"""

from __future__ import annotations

import warnings

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
    prepare_board_mapping,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.parse.parser import load_yaml_mapping
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore::dbt_charts.core.compile.migrations.SchemaMigrationWarning"
)


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: JsonObject, catalog: YamlSchemaCatalog) -> JsonObject:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


@pytest.mark.parametrize("value", [True, False])
def test_auto_link_becomes_links_auto(value: bool, catalog: YamlSchemaCatalog) -> None:
    migrated = _migrate({"title": "t", "auto_link": value, "rows": []}, catalog)
    assert migrated["links"] == {"auto": value}
    assert "auto_link" not in migrated


def test_a_nested_board_auto_link_migrates_to_a_nested_links_block(
    catalog: YamlSchemaCatalog,
) -> None:
    raw: JsonObject = {
        "title": "t",
        "rows": [{"title": "inner", "auto_link": True, "rows": []}],
    }
    migrated = _migrate(raw, catalog)
    assert migrated["rows"][0]["links"] == {"auto": True}
    assert "auto_link" not in migrated["rows"][0]


def test_a_chart_named_auto_link_in_a_rows_map_keeps_its_name(
    catalog: YamlSchemaCatalog,
) -> None:
    chart: JsonObject = {"type": "bar", "query": "q", "x": "a", "y": "b"}
    raw: JsonObject = {
        "title": "t",
        "auto_link": True,
        "queries": {"q": {"sql": "select 1 as a, 2 as b"}},
        "rows": [{"auto_link": chart}],
    }
    migrated = _migrate(raw, catalog)
    assert migrated["links"] == {"auto": True}
    assert migrated["rows"] == [{"auto_link": chart}]


def test_a_nested_auto_link_fails_naming_the_fix() -> None:
    result = compile(
        "title: t\nrows:\n  - title: Inner\n    auto_link: true\n    text: x\n"
    )
    assert not result.success
    assert [error.code for error in result.errors] == ["ERR-LINKS-NESTED"]
    assert "move `links:` to the root board" in result.errors[0].message


def test_a_current_board_is_left_alone() -> None:
    raw: JsonObject = {"title": "t", "links": {"auto": True, "open": "modal"}}
    assert prepare_board_mapping(raw) == raw


def test_an_old_board_compiles_as_links_auto() -> None:
    old = compile("title: t\nauto_link: true\nrows: []\n")
    new = compile("title: t\nlinks:\n  auto: true\nrows: []\n")
    assert old.success and new.success, [old.errors, new.errors]
    assert old.board is not None and new.board is not None
    assert old.board.links.auto is True
    assert old.board.links == new.board.links


def test_text_rewrite_moves_the_key(catalog: YamlSchemaCatalog) -> None:
    _, registry = _board_migration_context()
    text = "title: t\nauto_link: true\nrows:\n  - c\n"
    rewritten = migrate_yaml_text(text, catalog=catalog, registry=registry)
    mapping = load_yaml_mapping(rewritten)
    assert mapping["links"] == {"auto": True}
    assert "auto_link" not in mapping
    assert "rows:\n  - c\n" in rewritten


def test_a_board_with_both_spellings_is_rejected_at_compile() -> None:
    result = compile("title: t\nauto_link: true\nlinks:\n  auto: false\nrows: []\n")
    assert not result.success
    assert [error.code for error in result.errors] == ["ERR-EXTRA-FIELD"]
    assert "auto_link" in result.errors[0].message
