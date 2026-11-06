"""Migration coverage for the heatmap ``axis_quantitative`` removal.

Heatmap's axes are both nominal — it has no quantitative axis for
``axis_quantitative`` to style. The board-level slot
(``style.charts.heatmap.axis_quantitative``) is a plain tail anchored at
``charts``. The chart-local position (``charts.<id>.style.axis_quantitative``)
carries no family segment of its own, so its only available tail is still live
on the other five cartesian families: it is scoped to ``chart_type="heatmap"``
and confined by ``_live_declares_tail``. Both halves are pinned here, each
against the sibling family that keeps its own.
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

OTHER_CARTESIAN_FAMILIES = ("bar", "line", "area", "scatter", "histogram")


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw)


def _board(style: dict[str, Any]) -> dict[str, Any]:
    return {
        "charts": {
            "grid": {
                "type": "heatmap",
                "query": "counts",
                "x": "month",
                "y": "category",
            }
        },
        "style": style,
        "rows": ["grid"],
    }


def test_board_level_axis_quantitative_key_stripped(catalog: YamlSchemaCatalog) -> None:
    raw = _board(
        {
            "charts": {
                "heatmap": {
                    "axis_quantitative": {"scale": {"continuous": {"zero": False}}},
                    "cell_padding": 4,
                }
            }
        }
    )

    migrated = _migrate(raw, catalog)

    assert "axis_quantitative" not in migrated["style"]["charts"]["heatmap"]
    # The sibling key on the same slot survives -- the tail strips one field,
    # not the whole family block.
    assert migrated["style"]["charts"]["heatmap"]["cell_padding"] == 4
    AuthoredBoard.model_validate(migrated)


@pytest.mark.parametrize("family", OTHER_CARTESIAN_FAMILIES)
def test_live_sibling_family_axis_quantitative_survives_migration(
    family: str, catalog: YamlSchemaCatalog
) -> None:
    """The heatmap-anchored tail must not strip a live sibling family's slot.

    A bare ("axis_quantitative",) tail would have matched every one of these;
    the ``charts``/``heatmap`` anchor is what prevents that.
    """
    raw = _board(
        {"charts": {family: {"axis_quantitative": {"scale": {"round": True}}}}}
    )

    migrated = _migrate(raw, catalog)

    assert migrated["style"]["charts"][family]["axis_quantitative"] == {
        "scale": {"round": True}
    }
    AuthoredBoard.model_validate(migrated)


def test_theme_level_strip_tells_the_author_what_to_use(
    catalog: YamlSchemaCatalog,
) -> None:
    """The board-level slot, same obligation as the chart-local one below."""
    raw = _board(
        {"charts": {"heatmap": {"axis_quantitative": {"scale": {"round": True}}}}}
    )

    with pytest.warns(SchemaMigrationWarning, match="style.charts.heatmap.axis_band"):
        migrate_mapping(raw)


def test_chart_local_strip_tells_the_author_what_to_use(
    catalog: YamlSchemaCatalog,
) -> None:
    """The key styled nothing, but the successor is real -- say so."""
    raw = {
        "charts": {
            "grid": {
                "type": "heatmap",
                "query": "counts",
                "x": "month",
                "y": "category",
                "style": {"axis_quantitative": {"scale": {"round": True}}},
            }
        },
        "rows": ["grid"],
    }

    with pytest.warns(SchemaMigrationWarning, match="style.axis_band"):
        migrate_mapping(raw)
