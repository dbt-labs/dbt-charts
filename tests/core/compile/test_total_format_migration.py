"""Migration test for the 0.6.0 -> 0.7.0 pie ``total.format`` -> ``style.total.value.format``
move.

Pie/donut ``total.format`` moved from chart-root copy to style paint -- it
never belonged beside ``total.label`` (author intent, chart-local copy) when
every other format field in the schema lives in style. Declared via
``suffix_rename_moves`` in
``compile/migrations/versions/v0_7_0.py``'s ``TOTAL_FORMAT_RENAMES``, same
mechanism as ``THRESHOLD_RENAMES``
(``tests/core/compile/test_threshold_migration.py``) despite the differing
tail depth -- ``suffix_rename_moves`` preserves the shared prefix regardless.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

from ._engine_migrations import migrate_mapping


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw)


def _board_with_chart_root_total_format() -> dict[str, Any]:
    return {
        "charts": {
            "donut": {
                "type": "pie",
                "query": "q",
                "theta": "val",
                "color": "cat",
                "total": {"label": "Sessions", "format": "$,.0f"},
            }
        },
        "rows": ["donut"],
    }


def test_total_format_migrates_to_style_total_value_format(
    catalog: YamlSchemaCatalog,
) -> None:
    """A chart-root ``total.format`` migrates to ``style.total.value.format``
    and still compiles against the current schema; ``total.label`` (copy)
    is untouched, staying at chart root."""
    migrated = _migrate(_board_with_chart_root_total_format(), catalog)

    chart = migrated["charts"]["donut"]
    assert chart["total"] == {"label": "Sessions"}
    assert chart["style"]["total"]["value"]["format"] == "$,.0f"
    AuthoredBoard.model_validate(migrated)


#: Every ``total.format`` position ``suffix_rename_moves`` resolves at the
#: 0.6.0 boundary. Written out rather than re-derived so a bad anchor
#: silently resolving to zero moves (or picking up an unintended position)
#: shows up here as a diff. No ``style.charts.pie.total.format`` position:
#: chart-root ``total`` was never theme-cascaded in the old grammar, so
#: that candidate structurally doesn't exist in the source schema and
#: suffix_rename_moves drops it.
EXPECTED_TOTAL_FORMAT_MOVES = (
    "charts.*.total.format",
    "cols.*.*.total.format",
    "cols.*.total.format",
    "grid.items.*.item.*.total.format",
    "grid.items.*.item.total.format",
    "rows.*.*.total.format",
    "rows.*.total.format",
    "tabs.items.*.cols.*.*.total.format",
    "tabs.items.*.cols.*.total.format",
    "tabs.items.*.grid.items.*.item.*.total.format",
    "tabs.items.*.grid.items.*.item.total.format",
    "tabs.items.*.rows.*.*.total.format",
    "tabs.items.*.rows.*.total.format",
)


def test_total_format_compiles_clean_end_to_end() -> None:
    """compile()-level: an old-grammar board authoring a chart-root
    ``total.format`` compiles clean through the automatic
    ``prepare_board_mapping`` path -- the only path a Cloud user's board
    takes; nothing runs ``dct migrate`` for one."""
    yaml_content = """title: T
queries:
  q: {sql: "select 1 as val, 'A' as cat", source: test}
charts:
  donut:
    type: pie
    query: q
    theta: val
    color: cat
    total: {label: "Sessions", format: "$,.0f"}
rows: [donut]
"""
    result = compile(yaml_content)
    assert any(
        "migrated this YAML" in d.message
        for d in result.warnings
        if d.code == "WARN-SCHEMA-MIGRATED"
    ), result.warnings

    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]
    assert result.board is not None
    chart = result.board.charts["donut"]
    assert chart.total is not None
    assert chart.total.label == "Sessions"
    assert chart.style is not None
    assert chart.style.total.value.format == "$,.0f"
