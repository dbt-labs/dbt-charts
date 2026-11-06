"""Migration tests for tooltip/legend/sizing keys removed from non-painting chart families.

These keys existed in the 0.4.0 authored schema and are stripped automatically
by dct migrate at the pending (latest->current) boundary:

- (family, tooltip) for all 11 chart families at the theme level
- (kpi, legend) and (table, legend) at the theme level
- (style, tooltip) at the chart level

Chart-level style.legend on kpi/table is not auto-stripped (the tail is still
valid for painting families); that path raises an error with an actionable hint
instead (tested in test_legend_style_kpi_table_hint.py).

All tests use the real catalog and migration registry -- no synthetic schema data.
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


def test_theme_level_bar_tooltip_stripped(catalog: YamlSchemaCatalog) -> None:
    """style.charts.bar.tooltip at the theme level is stripped and result validates."""
    raw: dict[str, Any] = {
        "charts": {
            "c": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
            }
        },
        "style": {"charts": {"bar": {"tooltip": {"gap": 4}}}},
        "rows": ["c"],
    }

    migrated = _migrate(raw, catalog)

    bar_style = migrated.get("style", {}).get("charts", {}).get("bar", {})
    assert "tooltip" not in bar_style
    AuthoredBoard.model_validate(migrated)


def test_theme_level_kpi_legend_stripped(catalog: YamlSchemaCatalog) -> None:
    """style.charts.kpi.legend at the theme level is stripped and result validates."""
    raw: dict[str, Any] = {
        "charts": {
            "k": {
                "type": "kpi",
                "query": "q",
                "value": "revenue",
            }
        },
        "style": {"charts": {"kpi": {"legend": {"position": "bottom"}}}},
        "rows": ["k"],
    }

    migrated = _migrate(raw, catalog)

    kpi_style = migrated.get("style", {}).get("charts", {}).get("kpi", {})
    assert "legend" not in kpi_style
    AuthoredBoard.model_validate(migrated)


def test_theme_level_table_legend_stripped(catalog: YamlSchemaCatalog) -> None:
    """style.charts.table.legend at the theme level is stripped and result validates."""
    raw: dict[str, Any] = {
        "charts": {
            "t": {
                "type": "table",
                "query": "q",
                "columns": ["a", "b"],
            }
        },
        "style": {"charts": {"table": {"legend": {"position": "top"}}}},
        "rows": ["t"],
    }

    migrated = _migrate(raw, catalog)

    table_style = migrated.get("style", {}).get("charts", {}).get("table", {})
    assert "legend" not in table_style
    AuthoredBoard.model_validate(migrated)


def test_chart_level_style_tooltip_stripped(catalog: YamlSchemaCatalog) -> None:
    """chart-level style.tooltip is stripped and result validates."""
    raw: dict[str, Any] = {
        "charts": {
            "b": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
                "style": {"tooltip": {"gap": 8}},
            }
        },
        "rows": ["b"],
    }

    migrated = _migrate(raw, catalog)

    chart_style = migrated["charts"]["b"].get("style", {})
    assert not chart_style or "tooltip" not in chart_style
    AuthoredBoard.model_validate(migrated)


def test_multiple_deleted_keys_stripped_together(catalog: YamlSchemaCatalog) -> None:
    """A board using several now-deleted keys migrates cleanly in one pass."""
    raw: dict[str, Any] = {
        "charts": {
            "bar_chart": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
                "style": {"tooltip": {"gap": 4}},
            },
            "kpi_chart": {
                "type": "kpi",
                "query": "q",
                "value": "revenue",
            },
            "table_chart": {
                "type": "table",
                "query": "q",
                "columns": ["a"],
            },
        },
        "style": {
            "charts": {
                "bar": {"tooltip": {"gap": 4}},
                "kpi": {"legend": {"position": "bottom"}, "tooltip": {}},
                "table": {"legend": {"position": "top"}},
            }
        },
        "rows": ["bar_chart", "kpi_chart", "table_chart"],
    }

    migrated = _migrate(raw, catalog)

    charts_theme = migrated.get("style", {}).get("charts", {})
    # Chart-level tooltip gone
    bar_chart_style = migrated["charts"]["bar_chart"].get("style", {})
    assert not bar_chart_style or "tooltip" not in bar_chart_style
    # Theme-level bar tooltip gone
    assert "tooltip" not in charts_theme.get("bar", {})
    # Theme-level kpi legend and tooltip gone
    kpi_style = charts_theme.get("kpi", {})
    assert "legend" not in kpi_style
    assert "tooltip" not in kpi_style
    # Theme-level table legend gone
    assert "legend" not in charts_theme.get("table", {})
    AuthoredBoard.model_validate(migrated)


def test_kpi_tooltip_stripped_at_chart_level(catalog: YamlSchemaCatalog) -> None:
    """style.tooltip on a kpi chart is also stripped (same style.tooltip tail)."""
    raw: dict[str, Any] = {
        "charts": {
            "k": {
                "type": "kpi",
                "query": "q",
                "value": "revenue",
                "style": {"tooltip": {}},
            }
        },
        "rows": ["k"],
    }

    migrated = _migrate(raw, catalog)

    kpi_style = migrated["charts"]["k"].get("style", {})
    assert not kpi_style or "tooltip" not in kpi_style
    AuthoredBoard.model_validate(migrated)


# ---------------------------------------------------------------------------
# Text-rewrite path (migrate_yaml_text / dct migrate)
# The tests above only exercise the in-memory path (migrate_mapping).
# The following tests verify that dct migrate rewrites the YAML source file
# correctly, which requires the whole nested block under a mapping-valued key
# to go, not just the single key line.
# ---------------------------------------------------------------------------


def test_yaml_text_strips_kpi_theme_legend_block(catalog: YamlSchemaCatalog) -> None:
    """The file writer removes the kpi.legend block from the YAML source.

    preferred_width is a sibling key at the same indent level; it must survive
    to confirm that only the legend block (key line + nested children) is removed
    and not adjacent content.
    """
    yaml_text = (
        "charts:\n"
        "  k:\n"
        "    type: kpi\n"
        "    query: q\n"
        "    value: revenue\n"
        "style:\n"
        "  charts:\n"
        "    kpi:\n"
        "      legend:\n"
        "        position: bottom\n"
        "      preferred_width: 300\n"
        "rows: [k]\n"
    )

    migrated = migrate_yaml_text(yaml_text)

    assert "legend" not in migrated
    assert "preferred_width: 300" in migrated
    doc = load_yaml_mapping(migrated)
    kpi = doc["style"]["charts"]["kpi"]
    assert "legend" not in kpi
    assert kpi["preferred_width"] == 300


def test_yaml_text_strips_bar_tooltip_block(catalog: YamlSchemaCatalog) -> None:
    """The file writer removes the bar.tooltip block from the YAML source.

    preferred_width is a sibling key; it must survive.
    """
    yaml_text = (
        "charts:\n"
        "  b:\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "style:\n"
        "  charts:\n"
        "    bar:\n"
        "      preferred_width: 200\n"
        "      tooltip:\n"
        "        gap: 4\n"
        "rows: [b]\n"
    )

    migrated = migrate_yaml_text(yaml_text)

    assert "tooltip" not in migrated
    assert "preferred_width: 200" in migrated
    doc = load_yaml_mapping(migrated)
    bar = doc["style"]["charts"]["bar"]
    assert "tooltip" not in bar
    assert bar["preferred_width"] == 200


def test_yaml_text_strips_chart_level_style_tooltip_block(
    catalog: YamlSchemaCatalog,
) -> None:
    """The file writer removes chart-level style.tooltip from the YAML source.

    preferred_width is a sibling key inside the chart's style block; it must survive.
    """
    yaml_text = (
        "charts:\n"
        "  b:\n"
        "    type: bar\n"
        "    query: q\n"
        "    x: month\n"
        "    y: revenue\n"
        "    style:\n"
        "      preferred_width: 400\n"
        "      tooltip:\n"
        "        gap: 8\n"
        "rows: [b]\n"
    )

    migrated = migrate_yaml_text(yaml_text)

    assert "tooltip" not in migrated
    assert "preferred_width: 400" in migrated
    doc = load_yaml_mapping(migrated)
    chart_style = doc["charts"]["b"]["style"]
    assert "tooltip" not in chart_style
    assert chart_style["preferred_width"] == 400
