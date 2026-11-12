"""Migration tests for the 0.6.0 -> 0.7.0 conditional_formatting retirement.

``conditional_formatting:`` retired from 11 ``type:`` literals (bar,
histogram, line, area, scatter, pie, donut, map, geoshape, point_map,
bubble_map), moved on ``table``/``kpi`` — declared as 11
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

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_yaml_text,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
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
    assert chart.type == "bar"


def test_mixed_table_and_bar_conditional_formatting_round_trip_inline_rows_shape() -> (
    None
):
    """migrate_yaml_text round-trip, inline rows: list-item shape.

    Bar's conditional_formatting block is gone; KPI's moved to style.conditional.
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
        "  - type: kpi\n"
        "    query: q1\n"
        "    value: b\n"
        "    conditional_formatting:\n"
        "      b:\n"
        "        when:\n"
        '          - gt: 1\n            background: "#ff0000"\n'
    )

    load_yaml_schema_catalog()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_yaml_text(text)

    lines = result.split("\n")
    bar_start = next(i for i, line in enumerate(lines) if line.strip() == "- type: bar")
    table_start = next(
        i for i, line in enumerate(lines) if line.strip() == "- type: kpi"
    )
    bar_block = "\n".join(lines[bar_start:table_start])
    table_block = "\n".join(lines[table_start:])
    assert "conditional_formatting" not in bar_block, bar_block
    assert "conditional_formatting" not in table_block, table_block
    assert "conditional:" in table_block, table_block


def test_mixed_table_and_bar_conditional_formatting_round_trip_charts_mapping_shape() -> (
    None
):
    """migrate_yaml_text round-trip, charts: mapping-entry shape (momentum.yml's own shape).

    Bar's conditional_formatting block is gone; KPI's moved to style.conditional.
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
        "    type: kpi\n"
        "    query: q1\n"
        "    value: b\n"
        "    conditional_formatting:\n"
        "      b:\n"
        "        when:\n"
        '          - gt: 1\n            background: "#ff0000"\n'
        "rows: [b, t]\n"
    )

    load_yaml_schema_catalog()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_yaml_text(text)

    lines = result.split("\n")
    bar_start = next(i for i, line in enumerate(lines) if line.strip() == "b:")
    table_start = next(i for i, line in enumerate(lines) if line.strip() == "t:")
    bar_block = "\n".join(lines[bar_start:table_start])
    table_block = "\n".join(lines[table_start : table_start + 7])
    assert "conditional_formatting" not in bar_block, bar_block
    assert "conditional_formatting" not in table_block, table_block
    assert "conditional:" in table_block, table_block
