"""Integration tests for the 0.4.0 -> 0.5.0 interactive_legend deletion.

``interactive_legend`` was removed from LegendStyle as part of the 0.5.0
release. The frozen module ``versions/v0_5_0.py`` registers a Deletion for
every position in the schema where the key appeared so that authored boards
from before the removal migrate cleanly rather than blowing up the
normalizer.

These tests use the real catalog and migration registry -- no synthetic data.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_yaml_text,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.parse.parser import load_yaml_mapping
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


def test_chart_local_interactive_legend_stripped(catalog: YamlSchemaCatalog) -> None:
    """interactive_legend under charts.<name>.style.legend is deleted."""
    raw: dict[str, Any] = {
        "charts": {
            "sales": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
                "style": {
                    "legend": {
                        "interactive_legend": True,
                        "visible": True,
                    }
                },
            }
        },
        "rows": ["sales"],
    }

    migrated = _migrate(raw, catalog)

    legend = migrated["charts"]["sales"]["style"]["legend"]
    assert "interactive_legend" not in legend
    assert legend["visible"] is True
    AuthoredBoard.model_validate(migrated)


def test_chart_local_interactive_legend_false_also_stripped(
    catalog: YamlSchemaCatalog,
) -> None:
    """The false variant (e.g. heatmap) is deleted just the same."""
    raw: dict[str, Any] = {
        "charts": {
            "heat": {
                "type": "heatmap",
                "query": "matrix",
                "x": "row",
                "y": "col",
                "color": "val",
                "style": {
                    "legend": {
                        "interactive_legend": False,
                        "visible": True,
                    }
                },
            }
        },
        "rows": ["heat"],
    }

    migrated = _migrate(raw, catalog)

    legend = migrated["charts"]["heat"]["style"]["legend"]
    assert "interactive_legend" not in legend


def test_global_chart_type_interactive_legend_stripped(
    catalog: YamlSchemaCatalog,
) -> None:
    """interactive_legend under style.charts.<type>.legend is deleted."""
    raw: dict[str, Any] = {
        "style": {
            "charts": {
                "bar": {
                    "legend": {
                        "interactive_legend": True,
                        "visible": True,
                    }
                }
            }
        },
        "charts": {
            "sales": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
            }
        },
        "rows": ["sales"],
    }

    migrated = _migrate(raw, catalog)

    legend = migrated["style"]["charts"]["bar"]["legend"]
    assert "interactive_legend" not in legend
    assert legend["visible"] is True
    AuthoredBoard.model_validate(migrated)


def test_board_without_interactive_legend_is_unchanged(
    catalog: YamlSchemaCatalog,
) -> None:
    """A board that never used the field passes through untouched."""
    raw: dict[str, Any] = {
        "charts": {
            "sales": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
                "style": {
                    "legend": {"visible": True},
                },
            }
        },
        "rows": ["sales"],
    }

    migrated = _migrate(raw, catalog)

    assert migrated == raw
    AuthoredBoard.model_validate(migrated)


def test_nested_board_interactive_legend_stripped(catalog: YamlSchemaCatalog) -> None:
    """interactive_legend inside a nested board (rows[].cols[]) is deleted.

    The dominant authoring shape: rows → cols → charts.  The deletion must
    follow the $ref: '#' references in the frozen schema so nested-board
    positions are not missed.
    """
    raw: dict[str, Any] = {
        "title": "t",
        "rows": [
            {
                "cols": [
                    {
                        "charts": {
                            "s": {
                                "type": "bar",
                                "query": "q",
                                "x": "a",
                                "y": "b",
                                "style": {"legend": {"interactive_legend": True}},
                            }
                        }
                    }
                ]
            }
        ],
    }

    migrated = _migrate(raw, catalog)

    chart: Any = migrated["rows"][0]["cols"][0]["charts"]["s"]
    # interactive_legend was the sole key in legend, so legend (and its
    # empty parent style) are removed by the orphaned-parent cleanup.
    assert "interactive_legend" not in str(chart)
    assert "style" not in chart


def test_yaml_text_strips_interactive_legend_key(catalog: YamlSchemaCatalog) -> None:
    """The file-preserving writer removes interactive_legend from the YAML source."""
    yaml_text = (
        "charts:\n"
        "  sales:\n"
        "    type: bar\n"
        "    query: revenue\n"
        "    x: month\n"
        "    y: amount\n"
        "    style:\n"
        "      legend:\n"
        "        # keep this comment\n"
        "        interactive_legend: true\n"
        "        visible: true\n"
        "rows: [sales]\n"
    )

    migrated = migrate_yaml_text(yaml_text)

    assert "interactive_legend" not in migrated
    assert "# keep this comment" in migrated
    doc = load_yaml_mapping(migrated)
    legend = doc["charts"]["sales"]["style"]["legend"]
    assert legend["visible"] is True


def test_yaml_text_strips_nested_board_interactive_legend(
    catalog: YamlSchemaCatalog,
) -> None:
    """The file writer removes interactive_legend from a rows-nested board.

    This is the dominant authoring shape.  Pre-enumeration computed numeric path
    segments (rows.0.cols.0...) that set_board_values cannot address; tail-matching
    walks the document directly instead.
    """
    yaml_text = (
        "title: t\n"
        "rows:\n"
        "  - cols:\n"
        "      - charts:\n"
        "          s:\n"
        "            type: bar\n"
        "            query: q\n"
        "            x: a\n"
        "            y: b\n"
        "            style:\n"
        "              legend:\n"
        "                interactive_legend: true\n"
        "                visible: true\n"
    )

    migrated = migrate_yaml_text(yaml_text)

    assert "interactive_legend" not in migrated
    assert "visible: true" in migrated
    assert "rows:" in migrated
