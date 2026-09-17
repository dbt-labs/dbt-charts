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
    migrate_mapping,
)
from dbt_charts.core.compile.migrations.migrations import (
    _board_migration_context,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

from ._migration_declarations import schema_has_tail

OTHER_CARTESIAN_FAMILIES = ("bar", "line", "area", "scatter", "histogram")


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


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


def test_registry_declares_the_theme_level_deletion() -> None:
    """The removal was declared at the 0.5.0 -> 0.6.0 boundary, now frozen --
    not at ``catalog.latest_released.version``, which is 0.6.0 itself post-freeze."""
    _, registry = _board_migration_context()
    deletions = registry.deletions_from("0.5.0")

    declared = {d.path for d in deletions}
    assert ("charts", "heatmap", "axis_quantitative") in declared


def test_tail_was_in_the_released_grammar_and_is_gone_from_the_live_one(
    catalog: YamlSchemaCatalog,
) -> None:
    """Both halves of what makes a Deletion legal, asserted directly.

    Source-present is what makes the key worth migrating; target-absent is
    what stops the tail stripping a slot that still works. Source is pinned
    to 0.5.0, the grammar the tail actually shipped in -- not
    ``catalog.latest_released.version``, which is 0.6.0 post-freeze and never had it.
    """
    tail = ("charts", "heatmap", "axis_quantitative")
    assert schema_has_tail(catalog.schema_for("0.5.0"), tail)
    assert not schema_has_tail(catalog.current_schema, tail)


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


def test_chart_local_position_migrates_and_bar_keeps_its_own(
    catalog: YamlSchemaCatalog,
) -> None:
    """The chart-local half, and the collision that makes it interesting.

    The bare tail is live on five other cartesian families, so an over-fire
    here is silent data loss on a working key -- both sides asserted in one
    board.
    """
    assert schema_has_tail(catalog.current_schema, ("style", "axis_quantitative"))
    raw = {
        "charts": {
            "grid": {
                "type": "heatmap",
                "query": "counts",
                "x": "month",
                "y": "category",
                "style": {"axis_quantitative": {"scale": {"round": True}}},
            },
            "bars": {
                "type": "bar",
                "query": "counts",
                "x": "month",
                "y": "n",
                "style": {"axis_quantitative": {"scale": {"round": True}}},
            },
        },
        "rows": ["grid", "bars"],
    }

    migrated = _migrate(raw, catalog)

    assert "style" not in migrated["charts"]["grid"]
    assert migrated["charts"]["bars"]["style"]["axis_quantitative"] == {
        "scale": {"round": True}
    }


def test_theme_level_strip_tells_the_author_what_to_use(
    catalog: YamlSchemaCatalog,
) -> None:
    """The board-level slot, same obligation as the chart-local one below."""
    _, registry = _board_migration_context()
    raw = _board(
        {"charts": {"heatmap": {"axis_quantitative": {"scale": {"round": True}}}}}
    )

    with pytest.warns(SchemaMigrationWarning, match="style.charts.heatmap.axis_band"):
        migrate_mapping(raw, catalog=catalog, registry=registry)


def test_chart_local_strip_tells_the_author_what_to_use(
    catalog: YamlSchemaCatalog,
) -> None:
    """The key styled nothing, but the successor is real -- say so."""
    _, registry = _board_migration_context()
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
        migrate_mapping(raw, catalog=catalog, registry=registry)
