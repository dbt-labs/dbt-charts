"""Migration tests for the 0.6.0 -> 0.7.0 conditional_formatting retirement.

``conditional_formatting:`` retired from 11 ``type:`` literals (bar,
histogram, line, area, scatter, pie, donut, map, geoshape, point_map,
bubble_map), kept unchanged on ``table``/``kpi`` — declared as 11
chart_type-scoped ``Deletion`` entries in the DEV boundary module
``compile/migrations/versions/v0_7_0.py`` (``CONDITIONAL_FORMATTING_RETIRED_CHART_TYPES``).
See ``Deletion.chart_type``'s docstring in ``migrations.py`` for why a bare
per-path deletion cannot express "retired on this family, kept on that one".

This file proves the declaration on the real production catalog, at all
three levels ``compile/migrations/AGENTS.md``'s "prove it with a test, both
ways" rule asks for, for one representative family (bar):

1. mapping-level (``migrate_mapping`` / ``prepare_board_mapping``) — fast,
   mechanical.
2. ``compile()``-level — an old-grammar board authoring
   ``conditional_formatting`` on a bar chart compiles clean end to end.
3. ``dct migrate``'s underlying rewriter (``migrate_yaml_text``, called
   directly with no ``stop_target`` so the walk reaches the DEV version — see
   the round-trip tests' own docstrings for why ``migrate_board_yaml_text``
   itself, capped at the latest *released* schema, cannot reach a
   deletion declared on the still-pending boundary) round-trip, using a
   single file with both a ``table`` chart and a ``bar`` chart authoring
   ``conditional_formatting`` — asserting bar's block is gone and table's
   survives, in both authoring shapes a real board uses: inline ``rows:``
   list items and ``charts:`` mapping entries (``momentum.yml``'s own
   shape). A bar-only fixture, or a fixture covering only one shape, would
   pass even if the scope were dropped entirely.
"""

from __future__ import annotations

import warnings

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.migrations.versions.v0_7_0 import (
    CONDITIONAL_FORMATTING_RETIRED_CHART_TYPES,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    load_yaml_schema_catalog,
)

# The schema-level validation `migrate_mapping`'s recognizer runs (an
# anyOf discriminated-union match, not a query column check) requires each
# family's own required fields -- a bare x/y shape only satisfies bar/
# histogram/line/area/scatter. One minimal, schema-valid field set per
# retired chart type, mirroring dbt-charts/tests/core/render/chart/fixtures/
# parity/*.yml.
_EXTRA_FIELDS_BY_CHART_TYPE: dict[str, dict[str, str]] = {
    "bar": {"x": "a", "y": "b"},
    "histogram": {"x": "a", "color": "b"},
    "line": {"x": "a", "y": "b"},
    "area": {"x": "a", "y": "b"},
    "scatter": {"x": "a", "y": "b"},
    "pie": {"theta": "a", "color": "b"},
    "donut": {"theta": "a", "color": "b"},
    "map": {"geo_source": "us-states", "lookup": "a", "value": "b"},
    "geoshape": {"geo_source": "us-states", "lookup": "a", "color": "b"},
    "point_map": {"latitude": "a", "longitude": "b"},
    "bubble_map": {"latitude": "a", "longitude": "b", "color": "a", "size": "b"},
}


@pytest.mark.parametrize("chart_type", CONDITIONAL_FORMATTING_RETIRED_CHART_TYPES)
def test_conditional_formatting_migrates_via_mapping_for_every_retired_chart_type(
    chart_type: str,
) -> None:
    """mapping-level: migrate_mapping strips conditional_formatting for each of
    the 11 retired chart types -- not just bar. A typo in
    CONDITIONAL_FORMATTING_RETIRED_CHART_TYPES fails here; a *dropped*
    literal does not, since parametrizing over the constant under test drops
    the Deletion and its test case together -- see
    test_retired_chart_types_exactly_match_the_frozen_minus_live_schema_diff
    below for that half.
    """
    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()
    raw = {
        "title": "T",
        "queries": {"q1": {"sql": "select 1 as a, 2 as b", "source": "test"}},
        "charts": {
            "c1": {
                "type": chart_type,
                "query": "q1",
                **_EXTRA_FIELDS_BY_CHART_TYPE[chart_type],
                "conditional_formatting": {
                    "b": {"when": [{"gt": 1, "background": "#ff0000"}]}
                },
            }
        },
        "rows": ["c1"],
    }

    with pytest.warns(SchemaMigrationWarning, match="conditional_formatting"):
        result = migrate_mapping(raw, catalog=catalog, registry=registry)

    assert "conditional_formatting" not in result["charts"]["c1"]


def test_retired_chart_types_exactly_match_the_frozen_minus_live_schema_diff() -> None:
    """CONDITIONAL_FORMATTING_RETIRED_CHART_TYPES must equal
    {frozen 0.6.0 declares conditional_formatting} - {live schema declares it}.

    The parametrized test above derives its cases from the constant under
    test, so a *dropped* literal (not a typo) removes the Deletion and its
    test case together and the suite stays green. This derives the expected
    set independently, straight from each concrete *Chart schema's own
    `type:` enum and whether it carries `conditional_formatting`, so a
    dropped literal shows up as a set mismatch even though nothing
    parametrizes over it.
    """
    catalog = load_yaml_schema_catalog()

    def chart_types_declaring_cf(schema: JsonObject) -> set[str]:
        declaring: set[str] = set()
        for name, node in schema["$defs"].items():
            if not name.endswith("Chart") or name == "AuthoredChart":
                continue
            props = node.get("properties", {})
            type_schema = props.get("type", {})
            literals = type_schema.get("enum") or (
                [type_schema["const"]] if "const" in type_schema else []
            )
            if "conditional_formatting" in props:
                declaring.update(literals)
        return declaring

    frozen_cf = chart_types_declaring_cf(catalog.schema_for("0.6.0"))
    live_cf = chart_types_declaring_cf(catalog.current_schema)

    assert frozen_cf - live_cf == set(CONDITIONAL_FORMATTING_RETIRED_CHART_TYPES)


def test_bar_conditional_formatting_compiles_clean_end_to_end() -> None:
    """compile()-level: an old-grammar board with bar+CF compiles, field gone,
    warning surfaced -- not rejected outright."""
    yaml_content = """title: T
queries:
  q1: {sql: "select 1 as a, 2 as b", source: test}
charts:
  bar1: {type: bar, query: q1, x: a, y: b, conditional_formatting: {b: {when: [{gt: 1, background: "#ff0000"}]}}}
rows: [bar1]
"""
    result = compile(yaml_content)
    assert any(
        "conditional_formatting" in d.message
        for d in result.warnings
        if d.code == "WARN-SCHEMA-MIGRATED"
    ), result.warnings

    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]
    assert result.board is not None
    chart = result.board.charts["bar1"]
    # conditional_formatting stays a universal field on the normalized model
    # (deliberate keep — compile/normalize/charts.py:231's getattr pattern);
    # what proves the migration actually stripped bar's authored block is
    # this field reading None, not the field being absent.
    assert chart.conditional_formatting is None


def test_mixed_table_and_bar_conditional_formatting_round_trip_inline_rows_shape() -> (
    None
):
    """migrate_yaml_text round-trip, inline rows: list-item shape.

    Bar's conditional_formatting block is gone; table's survives untouched.
    Calls migrate_yaml_text directly (not migrate_board_yaml_text / dct
    migrate) with no stop_target, reaching the DEV version: this Deletion is
    declared on the pending 0.6.0 -> 0.7.0 boundary in versions/v0_7_0.py,
    which dct migrate's on-disk writer deliberately caps short of (it never
    writes syntax no released dbt charts recognizes yet) -- unrelated to the
    chart_type gate under test here. The full walk to the DEV version is what
    the framework fix actually needs to prove: that the Deletion.chart_type
    gate applies correctly when the text writer *does* reach it.
    """
    text = (
        "title: T\n"
        "queries:\n"
        "  q1:\n"
        '    sql: "select 1 as a, 2 as b"\n'
        "    source: test\n"
        "rows:\n"
        "  - type: bar\n"
        "    query: q1\n"
        "    x: a\n"
        "    y: b\n"
        "    conditional_formatting:\n"
        "      b:\n"
        "        when:\n"
        '          - gt: 1\n            background: "#ff0000"\n'
        "  - type: table\n"
        "    query: q1\n"
        "    conditional_formatting:\n"
        "      b:\n"
        "        when:\n"
        '          - gt: 1\n            background: "#ff0000"\n'
    )

    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_yaml_text(text, catalog=catalog, registry=registry)

    lines = result.split("\n")
    bar_start = next(i for i, line in enumerate(lines) if line.strip() == "- type: bar")
    table_start = next(
        i for i, line in enumerate(lines) if line.strip() == "- type: table"
    )
    bar_block = "\n".join(lines[bar_start:table_start])
    table_block = "\n".join(lines[table_start:])
    assert "conditional_formatting" not in bar_block, bar_block
    assert "conditional_formatting" in table_block, table_block


def test_mixed_table_and_bar_conditional_formatting_round_trip_charts_mapping_shape() -> (
    None
):
    """migrate_yaml_text round-trip, charts: mapping-entry shape (momentum.yml's own shape).

    Bar's conditional_formatting block is gone; table's survives untouched.
    Same no-stop_target rationale as the inline rows: shape test above.
    """
    text = (
        "title: T\n"
        "queries:\n"
        "  q1:\n"
        '    sql: "select 1 as a, 2 as b"\n'
        "    source: test\n"
        "charts:\n"
        "  b:\n"
        "    type: bar\n"
        "    query: q1\n"
        "    x: a\n"
        "    y: b\n"
        "    conditional_formatting:\n"
        "      b:\n"
        "        when:\n"
        '          - gt: 1\n            background: "#ff0000"\n'
        "  t:\n"
        "    type: table\n"
        "    query: q1\n"
        "    conditional_formatting:\n"
        "      b:\n"
        "        when:\n"
        '          - gt: 1\n            background: "#ff0000"\n'
        "rows: [b, t]\n"
    )

    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_yaml_text(text, catalog=catalog, registry=registry)

    lines = result.split("\n")
    bar_start = next(i for i, line in enumerate(lines) if line.strip() == "b:")
    table_start = next(i for i, line in enumerate(lines) if line.strip() == "t:")
    bar_block = "\n".join(lines[bar_start:table_start])
    table_block = "\n".join(lines[table_start : table_start + 6])
    assert "conditional_formatting" not in bar_block, bar_block
    assert "conditional_formatting" in table_block, table_block
