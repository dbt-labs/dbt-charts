"""Migration coverage for kpi's ``style.color``, retired at 0.5.0 -> 0.6.0.

kpi's was the family's sole bare-string ``color`` — every other family types
``style.color`` as an object — and it is the leaf's other retired position
beside the board-level one (``test_style_root_color_deletion.py``). Both are
declared on the same bare tail; ``_live_declares_tail`` is what confines each
to the positions that lost it. The theme-level slot,
``style.charts.kpi.color``, is a different field retired in the same release
and covered here too.
"""

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
from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch
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


def test_registry_scopes_the_deletion_to_kpi() -> None:
    """Declared with `chart_type`, not bare: the tail is live on eight other
    families, and the scope is what proves the declaration legal at
    registry-construction time."""
    _, registry = _board_migration_context()

    scoped = [
        d
        for d in registry.deletions_from("0.5.0")
        if d.path == ("style", "color") and d.chart_type == "kpi"
    ]

    assert len(scoped) == 1


def test_kpi_chart_style_patch_still_rejects_color() -> None:
    """The model is not what migrates — the key is gone from the grammar."""
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        KpiChartStylePatch.model_validate({"color": "#bf8700"})


def test_kpi_style_color_migrates_and_other_families_keep_theirs(
    catalog: YamlSchemaCatalog,
) -> None:
    raw = {
        "title": "Test",
        "queries": {"q": {"type": "values", "rows": [{"n": 1}]}},
        "charts": {
            "k": {
                "type": "kpi",
                "query": "q",
                "value": "n",
                "style": {"color": "#bf8700"},
            },
            "l": {
                "type": "line",
                "query": "q",
                "x": "n",
                "y": "n",
                "style": {"color": {"static": "#123456"}},
            },
        },
        "rows": ["k", "l"],
    }

    migrated = _migrate(raw, catalog)

    assert "style" not in migrated["charts"]["k"]
    assert migrated["charts"]["l"]["style"]["color"] == {"static": "#123456"}
    AuthoredBoard.model_validate(migrated)


def test_theme_level_kpi_color_migrates_and_says_so(
    catalog: YamlSchemaCatalog,
) -> None:
    """Not an inert key: it painted the value text, so the strip changes how
    the board looks and the author has to hear about it."""
    _, registry = _board_migration_context()
    raw = {
        "title": "Test",
        "queries": {"q": {"type": "values", "rows": [{"n": 1}]}},
        "style": {"charts": {"kpi": {"color": "#bf8700"}}},
        "charts": {"t": {"query": "q", "type": "table"}},
        "rows": ["t"],
    }

    with pytest.warns(
        SchemaMigrationWarning, match="style.charts.kpi.value.font.color"
    ):
        migrated = migrate_mapping(raw, catalog=catalog, registry=registry)

    assert "style" not in migrated
    AuthoredBoard.model_validate(migrated)


def test_a_chart_id_containing_a_dot_does_not_capture_a_sibling(
    catalog: YamlSchemaCatalog,
) -> None:
    """A chart id is author-chosen and may legally contain a dot.

    Struck paths are segment tuples for this: joined on dots, the retired
    `charts.a.style.color` would also name the chart called `a.style.color`,
    and the rewriter resolved it to that sibling's key line instead.
    """
    _, registry = _board_migration_context()

    migrated = migrate_yaml_text(
        """charts:
  a:
    type: kpi
    query: q
    value: n
    style:
      color: '#bf8700'
      align: center
  a.style.color:
    type: table
    query: q
rows:
- a
- a.style.color
queries:
  q: {type: values, rows: [{n: 1}]}
""",
        catalog=catalog,
        registry=registry,
    )

    assert "'#bf8700'" not in migrated
    assert "align: center" in migrated
    assert "  a.style.color:\n    type: table" in migrated


def test_compile_migrates_kpi_style_color() -> None:
    """`migrate_mapping()` skips the currency check, the fallbacks, and the
    whole-document gate that `compile()` applies — pin it at `compile()` too,
    and pin that the author is told what replaced the key."""
    result = compile(
        """title: Test
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  k:
    type: kpi
    query: q
    value: n
    style:
      color: '#bf8700'
rows:
- k
"""
    )
    assert any(
        "style.value.font.color" in d.message
        for d in result.warnings
        if d.code == "WARN-SCHEMA-MIGRATED"
    ), result.warnings

    assert result.success, [e.message for e in result.errors]
