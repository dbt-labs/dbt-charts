"""Migration test for the 0.5.0 -> 0.6.0 data_table -> support_table rename.

``data_table:`` renamed to ``support_table:`` — declared as a ``Move`` via
``suffix_rename_moves`` in ``compile/migrations/versions/v0_6_0.py``'s
``SUPPORT_TABLE_RENAMES`` (see that module's docstring for the full position
set and the self-nesting caveat it shares with ``TONES_RENAMES``). An
authored board using any pre-rename position migrates transparently.

Positions pinned here:
- charts.<id>.data_table              (chart-level attachment, via ``charts:``)
- rows.*.data_table                   (chart-level attachment, inline in ``rows:``)
- charts.<id>.style.data_table        (chart-local style override)
- style.charts.data_table             (board/theme-level style, tier 1)
- style.charts.bar.data_table         (per-family style override, tier 2)
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

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


def test_chart_level_data_table_migrates_to_support_table(
    catalog: YamlSchemaCatalog,
) -> None:
    """charts.<id>.data_table -> charts.<id>.support_table, chart referenced via charts:."""
    raw: dict[str, Any] = {
        "charts": {
            "revenue_bar": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
                "data_table": ["revenue"],
            }
        },
        "rows": ["revenue_bar"],
    }

    migrated = _migrate(raw, catalog)

    chart = migrated["charts"]["revenue_bar"]
    assert chart["support_table"] == ["revenue"]
    assert "data_table" not in chart
    AuthoredBoard.model_validate(migrated)


def test_inline_row_chart_data_table_migrates_to_support_table(
    catalog: YamlSchemaCatalog,
) -> None:
    """rows.*.data_table -> rows.*.support_table for a chart authored inline
    (not through the charts: map)."""
    raw: dict[str, Any] = {
        "rows": [
            {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
                "data_table": ["revenue"],
            }
        ],
    }

    migrated = _migrate(raw, catalog)

    inline_chart = migrated["rows"][0]
    assert inline_chart["support_table"] == ["revenue"]
    assert "data_table" not in inline_chart
    AuthoredBoard.model_validate(migrated)


def test_chart_local_style_data_table_migrates_to_support_table(
    catalog: YamlSchemaCatalog,
) -> None:
    """charts.<id>.style.data_table -> charts.<id>.style.support_table."""
    raw: dict[str, Any] = {
        "charts": {
            "revenue_bar": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
                "data_table": ["revenue"],
                "style": {"data_table": {"padding_top": 4.0}},
            }
        },
        "rows": ["revenue_bar"],
    }

    migrated = _migrate(raw, catalog)

    chart_style = migrated["charts"]["revenue_bar"]["style"]
    assert chart_style["support_table"] == {"padding_top": 4.0}
    assert "data_table" not in chart_style
    AuthoredBoard.model_validate(migrated)


def test_theme_level_style_charts_data_table_migrates_to_support_table(
    catalog: YamlSchemaCatalog,
) -> None:
    """style.charts.data_table (tier 1, universal override) -> style.charts.support_table."""
    raw: dict[str, Any] = {
        "charts": {
            "revenue_bar": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
            }
        },
        "style": {"charts": {"data_table": {"padding_top": 4.0}}},
        "rows": ["revenue_bar"],
    }

    migrated = _migrate(raw, catalog)

    charts_style = migrated["style"]["charts"]
    assert charts_style["support_table"] == {"padding_top": 4.0}
    assert "data_table" not in charts_style
    AuthoredBoard.model_validate(migrated)


def test_per_family_style_charts_bar_data_table_migrates_to_support_table(
    catalog: YamlSchemaCatalog,
) -> None:
    """style.charts.bar.data_table (tier 2, per-family override) -> style.charts.bar.support_table."""
    raw: dict[str, Any] = {
        "charts": {
            "revenue_bar": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
            }
        },
        "style": {
            "charts": {
                "bar": {
                    "data_table": {"padding_top": 4.0},
                    "aspect_ratio": 1.5,
                }
            }
        },
        "rows": ["revenue_bar"],
    }

    migrated = _migrate(raw, catalog)

    bar_style = migrated["style"]["charts"]["bar"]
    assert bar_style["support_table"] == {"padding_top": 4.0}
    assert "data_table" not in bar_style
    # sibling key survives the rename untouched
    assert bar_style["aspect_ratio"] == 1.5
    AuthoredBoard.model_validate(migrated)


#: Every ``data_table`` position ``suffix_rename_moves`` resolves at the 0.5.0
#: boundary. Written out rather than re-derived so a change to the resolver's
#: traversal shows up here as a diff instead of passing silently — the example
#: boards above each exercise one position and cannot see the other 31.
#:
#: ``rows.*.charts.*.data_table`` is deliberately absent: a sub-board nested
#: under ``rows``/``cols``/``grid`` that declares its own ``charts:`` map is not
#: reached, because ``_relative_field_paths`` returns ``()`` on re-entry into
#: ``AuthoredBoard``. That is the pre-existing self-nesting limitation
#: ``TONES_RENAMES`` already ships with, documented in ``v0_6_0.py``.
EXPECTED_DATA_TABLE_MOVES = (
    "charts.*.data_table",
    "charts.*.style.data_table",
    "cols.*.*.data_table",
    "cols.*.*.style.data_table",
    "cols.*.data_table",
    "cols.*.style.data_table",
    "grid.items.*.item.data_table",
    "grid.items.*.item.style.data_table",
    "rows.*.*.data_table",
    "rows.*.*.style.data_table",
    "rows.*.data_table",
    "rows.*.style.data_table",
    "style.charts.area.data_table",
    "style.charts.bar.data_table",
    "style.charts.data_table",
    "style.charts.heatmap.data_table",
    "style.charts.histogram.data_table",
    "style.charts.line.data_table",
    "style.charts.scatter.data_table",
    "tabs.items.*.cols.*.*.data_table",
    "tabs.items.*.cols.*.*.style.data_table",
    "tabs.items.*.cols.*.data_table",
    "tabs.items.*.cols.*.style.data_table",
    "tabs.items.*.grid.items.*.item.data_table",
    "tabs.items.*.grid.items.*.item.style.data_table",
    "tabs.items.*.rows.*.*.data_table",
    "tabs.items.*.rows.*.*.style.data_table",
    "tabs.items.*.rows.*.data_table",
    "tabs.items.*.rows.*.style.data_table",
    "tabs.items.*.style.charts.area.data_table",
    "tabs.items.*.style.charts.bar.data_table",
    "tabs.items.*.style.charts.data_table",
    "tabs.items.*.style.charts.heatmap.data_table",
    "tabs.items.*.style.charts.histogram.data_table",
    "tabs.items.*.style.charts.line.data_table",
    "tabs.items.*.style.charts.scatter.data_table",
)
