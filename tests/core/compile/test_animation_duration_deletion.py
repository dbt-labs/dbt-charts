"""Regression tests for the animation_duration deletion (DEV boundary).

``animation_duration`` was removed from all style models in the current
(unreleased) cycle.  ``versions/v0_7_0.py`` must register Deletions for every
position where the key appeared in the frozen 0.4.0 schema so that authored
boards from before the removal migrate cleanly.

Positions covered by the migration:
- style.charts.animation_duration  (ChartsStyle/ChartsStylePatch)
- style.charts.<family>.animation_duration  (per-family theme patches, 11 families)
- charts.<name>.style.animation_duration  (chart-level style, all families)
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


def test_charts_level_animation_duration_stripped(catalog: YamlSchemaCatalog) -> None:
    """style.charts.animation_duration at the theme level is stripped and result validates."""
    raw: dict[str, Any] = {
        "charts": {
            "c": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
            }
        },
        # aspect_ratio is a sibling that must survive; its presence pins the
        # assertion to real structure, not an empty dict after empty-parent pruning.
        "style": {"charts": {"animation_duration": 500, "aspect_ratio": 1.5}},
        "rows": ["c"],
    }

    migrated = _migrate(raw, catalog)

    charts_style = migrated.get("style", {}).get("charts", {})
    assert "animation_duration" not in charts_style
    assert charts_style.get("aspect_ratio") == 1.5
    AuthoredBoard.model_validate(migrated)


def test_per_family_animation_duration_stripped(catalog: YamlSchemaCatalog) -> None:
    """style.charts.<family>.animation_duration for each family is stripped."""
    families = [
        "area",
        "bar",
        "geoshape",
        "heatmap",
        "histogram",
        "kpi",
        "line",
        "pie",
        "point_map",
        "scatter",
        "table",
    ]
    for family in families:
        raw: dict[str, Any] = {
            "charts": {
                "c": {
                    "type": "bar",
                    "query": "q",
                    "x": "month",
                    "y": "revenue",
                }
            },
            # preferred_width survives migration; its presence pins the assertion to
            # real structure (not an empty dict after empty-parent pruning).
            # preferred_width is used instead of aspect_ratio because kpi/table patches
            # never declared aspect_ratio in the 0.4.0 frozen schema.
            "style": {
                "charts": {family: {"animation_duration": 300, "preferred_width": 4}}
            },
            "rows": ["c"],
        }

        migrated = _migrate(raw, catalog)

        family_style = migrated.get("style", {}).get("charts", {}).get(family, {})
        assert "animation_duration" not in family_style, (
            f"animation_duration not stripped from style.charts.{family}"
        )
        assert family_style.get("preferred_width") == 4, (
            f"preferred_width incorrectly removed from style.charts.{family}"
        )


def test_chart_level_style_animation_duration_stripped(
    catalog: YamlSchemaCatalog,
) -> None:
    """animation_duration in a chart's own style block is stripped and result validates."""
    raw: dict[str, Any] = {
        "charts": {
            "sales": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
                "style": {"animation_duration": 400},
            }
        },
        "rows": ["sales"],
    }

    migrated = _migrate(raw, catalog)

    chart_style = migrated["charts"]["sales"].get("style", {})
    assert "animation_duration" not in chart_style
    AuthoredBoard.model_validate(migrated)


def test_animation_duration_and_theme_family_stripped_together(
    catalog: YamlSchemaCatalog,
) -> None:
    """Board authoring both charts-level and per-family animation_duration migrates cleanly."""
    raw: dict[str, Any] = {
        "charts": {
            "sales": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
                "style": {"animation_duration": 400},
            }
        },
        "style": {
            "charts": {
                "animation_duration": 500,
                "aspect_ratio": 1.5,  # sibling that must survive at charts level
                "bar": {"animation_duration": 300, "preferred_width": 4},
                "line": {"animation_duration": 200, "preferred_width": 3},
            }
        },
        "rows": ["sales"],
    }

    migrated = _migrate(raw, catalog)

    charts_style = migrated.get("style", {}).get("charts", {})
    assert "animation_duration" not in charts_style
    assert charts_style.get("aspect_ratio") == 1.5
    assert "animation_duration" not in charts_style.get("bar", {})
    assert charts_style.get("bar", {}).get("preferred_width") == 4
    assert "animation_duration" not in charts_style.get("line", {})
    assert charts_style.get("line", {}).get("preferred_width") == 3
    assert "animation_duration" not in migrated["charts"]["sales"].get("style", {})
    AuthoredBoard.model_validate(migrated)


def test_chart_named_animation_duration_not_deleted_during_migration(
    catalog: YamlSchemaCatalog,
) -> None:
    """A chart keyed 'animation_duration' must not be deleted by the migration.

    Regression: ("charts", "animation_duration") as a 2-segment tail matched the
    board-root charts dict by chart name, silently deleting the entire chart entry
    (and the whole charts block when it became empty).
    """
    raw: dict[str, Any] = {
        "charts": {
            "animation_duration": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
            }
        },
        # style.charts.animation_duration forces the migration path to execute.
        "style": {"charts": {"animation_duration": 500, "aspect_ratio": 1.5}},
        "rows": ["animation_duration"],
    }

    migrated = _migrate(raw, catalog)

    assert "animation_duration" in migrated["charts"], (
        "chart named 'animation_duration' was incorrectly deleted during migration"
    )
    # style.charts.animation_duration must be stripped; the chart must survive.
    assert "animation_duration" not in migrated.get("style", {}).get("charts", {})
    AuthoredBoard.model_validate(migrated)


def test_board_without_animation_duration_is_unchanged(
    catalog: YamlSchemaCatalog,
) -> None:
    """Pins "a current-compatible board is not rewritten at all" — not animation_duration-specific.

    _recognize() returns the DEV version for this board, so migrate_mapping returns a
    deep copy without running any Deletion. This is a no-op-migration contract
    test, not a check that animation_duration is stripped.
    """
    raw: dict[str, Any] = {
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

    assert migrated == raw
    AuthoredBoard.model_validate(migrated)
