"""Migration test for the 0.5.0 -> 0.6.0 description -> notes rename.

``description:`` renamed to ``notes:`` on every object carrying non-rendering
prose — declared as a ``Move`` via ``suffix_rename_moves`` in
``compile/migrations/versions/v0_6_0.py``'s ``NOTES_RENAMES``.

The open-map cases are the point of this file. ``queries:``/``charts:``/
``variables:`` are open maps, so an author may legitimately *name* a query
``description`` — a bare tail rename would rewrite that identifier and leave
its ``query: description`` reference dangling. ``suffix_rename_moves``
resolves through the field tree instead, so every position carries a ``*``
segment at the open-map key level (``queries.*.description``): the key
position is never a rename target. ``test_query_named_description_*`` pin
that.
"""

from __future__ import annotations

import json
import warnings
from typing import Any

import pytest

from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


def _board(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Accounts",
        "queries": {"q": {"sql": "SELECT 1 AS month, 2 AS revenue"}},
        "charts": {
            "revenue_bar": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
            }
        },
        "rows": ["revenue_bar"],
    }
    base.update(extra)
    return base


def test_board_level_description_migrates_to_notes(catalog: YamlSchemaCatalog) -> None:
    """description -> notes at the board root."""
    migrated = _migrate(_board(description="Won revenue by account."), catalog)

    assert migrated["notes"] == "Won revenue by account."
    assert "description" not in migrated
    AuthoredBoard.model_validate(migrated)


def test_chart_level_description_migrates_to_notes(catalog: YamlSchemaCatalog) -> None:
    """charts.<id>.description -> charts.<id>.notes."""
    raw = _board()
    raw["charts"]["revenue_bar"]["description"] = "chart prose"

    migrated = _migrate(raw, catalog)

    chart = migrated["charts"]["revenue_bar"]
    assert chart["notes"] == "chart prose"
    assert "description" not in chart
    AuthoredBoard.model_validate(migrated)


def test_query_level_description_migrates_to_notes(catalog: YamlSchemaCatalog) -> None:
    """queries.<id>.description -> queries.<id>.notes."""
    raw = _board()
    raw["queries"]["q"]["description"] = "query prose"

    migrated = _migrate(raw, catalog)

    query = migrated["queries"]["q"]
    assert query["notes"] == "query prose"
    assert "description" not in query
    AuthoredBoard.model_validate(migrated)


def test_variable_level_description_migrates_to_notes(
    catalog: YamlSchemaCatalog,
) -> None:
    """variables.<id>.description -> variables.<id>.notes."""
    raw = _board(
        variables={
            "industry": {
                "input": "multiselect",
                "description": "variable prose",
                "options": {"static": ["Finance", "Retail"]},
            }
        }
    )

    migrated = _migrate(raw, catalog)

    variable = migrated["variables"]["industry"]
    assert variable["notes"] == "variable prose"
    assert "description" not in variable
    AuthoredBoard.model_validate(migrated)


def test_query_named_description_keeps_its_identifier(
    catalog: YamlSchemaCatalog,
) -> None:
    """A query *named* ``description`` is a key at the ``*`` position, never renamed.

    The rename resolves to ``queries.*.description``; the ``*`` consumes the
    query name, so the applier only ever rewrites a ``description`` key one
    level below it.
    """
    raw = _board(
        queries={
            "description": {
                "sql": "SELECT 1 AS month, 2 AS revenue",
                "description": "prose on the query named 'description'",
            }
        },
    )
    raw["charts"]["revenue_bar"]["query"] = "description"

    migrated = _migrate(raw, catalog)

    assert "description" in migrated["queries"], (
        "the query identifier 'description' must survive the rename"
    )
    assert migrated["charts"]["revenue_bar"]["query"] == "description", (
        "the chart's query reference must still resolve"
    )
    assert migrated["queries"]["description"]["notes"] == (
        "prose on the query named 'description'"
    )
    assert "description" not in migrated["queries"]["description"]
    AuthoredBoard.model_validate(migrated)


def test_chart_named_description_keeps_its_identifier(
    catalog: YamlSchemaCatalog,
) -> None:
    """Same guarantee for the ``charts:`` open map and its layout reference."""
    raw = _board(
        charts={
            "description": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
                "description": "prose on the chart named 'description'",
            }
        },
        rows=["description"],
    )

    migrated = _migrate(raw, catalog)

    assert "description" in migrated["charts"]
    assert migrated["rows"] == ["description"]
    assert migrated["charts"]["description"]["notes"] == (
        "prose on the chart named 'description'"
    )
    AuthoredBoard.model_validate(migrated)


def test_inline_chart_description_migrates_to_notes(
    catalog: YamlSchemaCatalog,
) -> None:
    """rows.*.description -> rows.*.notes for a chart authored inline."""
    raw: dict[str, Any] = {
        "title": "Accounts",
        "queries": {"q": {"sql": "SELECT 1 AS month, 2 AS revenue"}},
        "rows": [
            {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
                "description": "inline chart prose",
            }
        ],
    }

    migrated = _migrate(raw, catalog)

    assert migrated["rows"][0]["notes"] == "inline chart prose"
    assert "description" not in migrated["rows"][0]
    AuthoredBoard.model_validate(migrated)


def test_yaml_text_migration_preserves_comments_and_open_map_keys(
    catalog: YamlSchemaCatalog,
) -> None:
    """The on-disk text-rewrite path rewrites the field, not the identifier.

    ``migrate_yaml_text`` is a distinct code path from ``migrate_mapping`` -- it
    edits the document text so comments and key order survive -- so the
    open-map guarantee is pinned here separately.

    Calls ``migrate_yaml_text`` directly (uncapped) against the real
    NOTES_RENAMES declaration, not ``migrate_board_yaml_text`` (which caps
    ``dct migrate`` at the latest *frozen* version -- this rename is still
    pending, not frozen, so the capped CLI path would leave it untouched).
    This still exercises the real declaration and the real text-rewrite
    mechanics; it is not testing the CLI entry point specifically.
    """
    from dbt_charts.core.compile.migrations import migrate_yaml_text

    source = """title: Accounts
description: Won revenue by account.

# Industry cells filter the list.
queries:
  description:
    sql: SELECT 1 AS month, 2 AS revenue
    description: prose on the query named 'description'
charts:
  directory:
    type: bar
    query: description
    x: month
    y: revenue
rows: [directory]
"""

    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_yaml_text(source, catalog=catalog, registry=registry)

    assert "# Industry cells filter the list." in migrated
    assert "notes: Won revenue by account." in migrated
    assert "notes: prose on the query named 'description'" in migrated
    assert "  description:\n" in migrated, "the query identifier must survive"
    assert "query: description" in migrated, "the reference must still resolve"
    assert "description: " not in migrated


def test_sub_board_query_named_description_keeps_its_identifier(
    catalog: YamlSchemaCatalog,
) -> None:
    """The open-map guarantee must hold at two-wildcard positions too.

    ``rows: list[... | dict[str, AuthoredChart]]`` resolves ``rows.*.*.notes``,
    whose final segment can land on an open-map key one level down — a query
    named ``description`` inside a sub-board's own ``queries:`` map. Application
    is arm-blind, so only a schema gate keeps the identifier intact.
    """
    raw: dict[str, Any] = {
        "title": "T",
        "description": "board prose",
        "rows": [
            {
                "queries": {
                    "description": {"sql": "SELECT 1 AS a, 2 AS b"},
                },
                "charts": {
                    "c1": {"type": "bar", "query": "description", "x": "a", "y": "b"}
                },
                "rows": ["c1"],
            }
        ],
    }

    migrated = _migrate(raw, catalog)

    sub = migrated["rows"][0]
    assert "description" in sub["queries"], (
        "the sub-board's query identifier must survive the rename"
    )
    assert "notes" not in sub["queries"]
    assert sub["charts"]["c1"]["query"] == "description"
    assert migrated["notes"] == "board prose"
    AuthoredBoard.model_validate(migrated)


def test_sub_board_query_identifier_survives_on_disk(
    catalog: YamlSchemaCatalog,
) -> None:
    """Proof A, on the on-disk text-rewrite path: the identifier must not be
    rewritten. Uncapped ``migrate_yaml_text`` against the real (still-pending)
    NOTES_RENAMES declaration -- see the sibling test's docstring for why not
    ``migrate_board_yaml_text``."""
    from dbt_charts.core.compile.migrations import migrate_yaml_text

    source = """title: T
description: board prose
rows:
  - queries:
      description:
        sql: SELECT 1 AS a, 2 AS b
    charts:
      c1: {type: bar, query: description, x: a, y: b}
    rows: [c1]
"""

    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_yaml_text(source, catalog=catalog, registry=registry)

    assert "      description:\n" in migrated, "the query identifier must survive"
    assert "      notes:\n" not in migrated
    assert "notes: board prose" in migrated


def test_callout_description_aborts_the_whole_board_migration() -> None:
    """An undeclared position strands the *whole* board, not just its own chart.

    ``CalloutChart`` declares no ``notes`` — and declared no ``description`` in
    0.5.0 either, so no valid board reaches this. Authoring it anyway leaves a
    key no move can rewrite; the post-migration whole-document check fails,
    ``prepare_board_mapping`` returns the *original* mapping, and the sibling
    bar chart's ``description:`` is reported unmigrated too.

    Pinned because the blast radius is the surprise, not the callout: the
    diagnostic names a chart the author did not touch.
    """
    from dbt_charts.core.compile import compile

    yaml_content = """title: T
queries:
  q1:
    sql: SELECT 1 AS a, 2 AS b
    source: duckdb
charts:
  c1:
    type: callout
    message: hello
    description: callout prose
  c2:
    type: bar
    query: q1
    x: a
    y: b
    description: chart prose
rows: [c1, c2]
"""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile(yaml_content)

    paths = {
        e.message.split("Full path: ")[-1].rstrip(".")
        for e in result.errors
        if e.code == "ERR-EXTRA-FIELD"
    }
    assert any("c1" in path for path in paths), paths
    assert any("c2" in path for path in paths), (
        f"the sibling bar chart is stranded too — that is the point: {paths}"
    )


def test_reported_bug_compiles_end_to_end() -> None:
    """The reported failure was a `compile()` error, so pin it at that layer."""
    from dbt_charts.core.compile import compile

    yaml_content = """title: Accounts
description: Every customer account, ranked by won revenue.
queries:
  q1: {sql: "SELECT 1 AS month, 2 AS revenue", source: duckdb}
charts:
  directory: {type: bar, query: q1, x: month, y: revenue, description: chart prose}
rows: [directory]
"""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile(yaml_content)

    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]


def test_row_item_chart_named_description_keeps_its_identifier(
    catalog: YamlSchemaCatalog,
) -> None:
    """`rows.*` is a *list index*, so the author's key sits one level below it.

    A row item authored as ``dict[str, AuthoredChart]`` puts the chart id
    exactly where the resolved path's final segment lands. Only
    ``_open_map_claims`` separates that from a sub-board setting its own
    ``description`` field — the value decides, nothing else can.
    """
    raw: dict[str, Any] = {
        "title": "T",
        "description": "real board prose",
        "queries": {"q": {"sql": "SELECT 1 AS a, 2 AS b"}},
        "rows": [{"description": {"type": "bar", "query": "q", "x": "a", "y": "b"}}],
    }

    migrated = _migrate(raw, catalog)

    assert "description" in migrated["rows"][0], "the chart identifier must survive"
    assert "notes" not in migrated["rows"][0]
    assert migrated["notes"] == "real board prose", (
        "a genuine board-level description in the same document must still migrate"
    )
    AuthoredBoard.model_validate(migrated)


def test_col_item_chart_identifier_survives_on_disk(
    catalog: YamlSchemaCatalog,
) -> None:
    """The same guarantee for `cols.*`, on the on-disk text-rewrite path."""
    from dbt_charts.core.compile.migrations import migrate_yaml_text

    source = """title: T
description: real board prose
queries:
  q:
    sql: SELECT 1 AS a, 2 AS b
cols:
  - description:
      type: bar
      query: q
      x: a
      y: b
"""

    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_yaml_text(source, catalog=catalog, registry=registry)

    assert "  - description:\n" in migrated, "the chart identifier must survive"
    assert "notes: real board prose" in migrated


@pytest.mark.parametrize(
    "layout",
    [
        {"rows": [{"IDENT": None}]},
        {"cols": [{"IDENT": None}]},
        {"grid": {"items": [{"item": {"IDENT": None}}]}},
        {"tabs": {"items": [{"title": "T1", "rows": [{"IDENT": None}]}]}},
        {"tabs": {"items": [{"title": "T1", "cols": [{"IDENT": None}]}]}},
        {
            "tabs": {
                "items": [
                    {"title": "T1", "grid": {"items": [{"item": {"IDENT": None}}]}}
                ]
            }
        },
    ],
    ids=["rows", "cols", "grid", "tabs-rows", "tabs-cols", "tabs-grid"],
)
@pytest.mark.parametrize("identifier", ["description", "data_table"])
def test_no_declared_move_renames_an_author_chosen_chart_id(
    layout: dict[str, Any], identifier: str, catalog: YamlSchemaCatalog
) -> None:
    """Every position where an open map sits beside the declared field.

    A schema sweep over all declared moves finds twelve — six layout shapes for
    ``description`` and for the pre-existing ``data_table`` alike. At each one
    the resolved path's final segment can land on a chart id the author chose.

    The sweep reads ``additionalProperties`` from the **live** grammar as well
    as the frozen one, which is what surfaces the ``grid.items.*.item`` pair:
    ``GridItem.item`` gained its ``dict[str, AuthoredChart]`` arm after the
    last freeze, so a frozen-only sweep is blind exactly where the gate was.

    Parametrized rather than hand-written so a future rename landing on the
    same shape is covered the day it is declared.
    """
    chart = {"type": "bar", "query": "q", "x": "a", "y": "b"}
    rendered = json.loads(
        json.dumps(layout).replace('"IDENT": null', f'"IDENT": {json.dumps(chart)}')
    )
    raw: dict[str, Any] = {
        "title": "T",
        "description": "board prose",
        "queries": {"q": {"sql": "SELECT 1 AS a, 2 AS b"}},
        **json.loads(json.dumps(rendered).replace("IDENT", identifier)),
    }

    migrated = _migrate(raw, catalog)

    assert identifier in _all_keys(migrated), (
        f"the author's chart id {identifier!r} was renamed away: {migrated}"
    )
    assert migrated["notes"] == "board prose", (
        "the board's own description field must still migrate in the same document"
    )


def _all_keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _all_keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _all_keys(v)}
    return set()


def test_sub_board_charts_map_strands_the_whole_board() -> None:
    """The one shape where this rename does not repair what it should.

    ``_relative_field_paths``' ``seen`` guard makes a self-nested
    ``AuthoredBoard`` opaque, so ``rows.*.charts.*.description`` is not among
    the resolved positions. A board carrying one was valid 0.5.0, and its
    *top-level* ``description:`` is stranded along with the sub-board's — the
    whole-document check fails and the original mapping is returned unchanged.

    Pinned so the limitation is a recorded cost rather than a surprise; the
    `extra="forbid"` hint naming `notes:` is what the author gets instead.
    """
    from dbt_charts.core.compile import compile

    yaml_content = """title: T
description: top board prose
queries:
  q:
    sql: SELECT 1 AS a, 2 AS b
    source: duckdb
rows:
  - charts:
      c1:
        type: bar
        query: q
        x: a
        y: b
        description: sub chart prose
    rows: [c1]
"""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile(yaml_content)

    extra = [e for e in result.errors if e.code == "ERR-EXTRA-FIELD"]
    assert any(e.message.endswith("Full path: description.") for e in extra), (
        f"the top-level description is stranded too: {[e.message for e in extra]}"
    )
    assert all(
        "`notes`" in (e.hint or "") or "notes:" in (e.hint or "") for e in extra
    ), "every stranded position must still name the replacement"
