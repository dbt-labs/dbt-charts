"""Migration test for the 0.5.0 -> 0.6.0 KpiTonesStyle Move.

``style.charts.kpi.tones`` moved to board-level ``style.tones`` (a sibling of
``style.palettes``) — declared as a ``Move`` in
``compile/migrations/versions/v0_6_0.py``. An authored board (or theme
patch) using the pre-move path migrates transparently.

The Move covers exactly two positions: the root-anchored
``style.charts.kpi.tones`` and the per-tab
``tabs.items.*.style.charts.kpi.tones`` (a tab's own ``style:`` override).
Any ``rows``/``cols``/``grid.items.*.item`` self-nesting below the root is
uncovered — ``_relative_field_paths`` checks its ``seen`` ancestor set on
entry, so a self-referential model's fields are never walked a second time
along the same path (see ``v0_6_0.py``'s module docstring for the full
rationale and what remains uncovered).

``migrate_mapping`` (in-memory, used when compiling/loading an old board)
relocates the whole ``tones:`` mapping regardless of its shape — ``Move``
pops and re-places the raw value found at ``old_path``. ``migrate_yaml_text``
(the ``dct migrate`` text-preserving rewriter) is narrower by design — see
its own docstring — and refuses a non-scalar move to a different parent
rather than reformat the file; a board authoring the old ``tones:`` shape
must be moved by hand when run through ``dct migrate``.
"""

from __future__ import annotations

import copy
import warnings
from typing import Any

import pytest

from dbt_charts.core.compile.migrations import (
    MigrationError,
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
    prepare_board_mapping,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
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


def test_style_charts_kpi_tones_migrates_to_style_tones(
    catalog: YamlSchemaCatalog,
) -> None:
    """The whole tones mapping moves in one shot from charts.kpi.tones to the
    board-level style.tones — a full-object Move, not a per-leaf one."""
    raw: dict[str, Any] = {
        "charts": {
            "revenue_kpi": {
                "type": "kpi",
                "query": "q1",
                "value": "revenue",
            }
        },
        "style": {
            "charts": {
                "kpi": {
                    "tones": {
                        "positive": "#00ff00",
                        "negative": "#ff0000",
                    }
                }
            }
        },
        "rows": ["revenue_kpi"],
    }

    migrated = _migrate(raw, catalog)

    assert migrated["style"]["tones"] == {
        "positive": "#00ff00",
        "negative": "#ff0000",
    }
    # tones is popped out; the emptied kpi container is not orphan-cleaned by
    # a plain Move (unlike the ConditionalMove path) but is harmless — an
    # all-Optional KpiChartStylePatch validates fine against {}.
    assert "tones" not in migrated["style"].get("charts", {}).get("kpi", {})
    AuthoredBoard.model_validate(migrated)


def test_style_charts_kpi_tones_migrates_alongside_other_kpi_style(
    catalog: YamlSchemaCatalog,
) -> None:
    """Sibling charts.kpi.* keys survive the tones relocation untouched."""
    raw: dict[str, Any] = {
        "charts": {
            "revenue_kpi": {
                "type": "kpi",
                "query": "q1",
                "value": "revenue",
            }
        },
        "style": {
            "charts": {
                "kpi": {
                    "min_card_width": 120,
                    "tones": {"warning": "#ffaa00"},
                }
            }
        },
        "rows": ["revenue_kpi"],
    }

    migrated = _migrate(raw, catalog)

    assert migrated["style"]["tones"] == {"warning": "#ffaa00"}
    assert migrated["style"]["charts"]["kpi"]["min_card_width"] == 120
    assert "tones" not in migrated["style"]["charts"]["kpi"]
    AuthoredBoard.model_validate(migrated)


def test_tabs_items_style_charts_kpi_tones_migrates_to_tab_style_tones(
    catalog: YamlSchemaCatalog,
) -> None:
    """A tab's own style: override authors the same old shape and must move
    the same way as the root-level one — tabs.items.*.style.charts.kpi.tones
    -> tabs.items.*.style.tones."""
    raw: dict[str, Any] = {
        "charts": {
            "revenue_kpi": {
                "type": "kpi",
                "query": "q1",
                "value": "revenue",
            }
        },
        "tabs": {
            "items": [
                {
                    "title": "Tab A",
                    "style": {
                        "charts": {
                            "kpi": {
                                "tones": {"positive": "#00ff00"},
                            }
                        }
                    },
                    "rows": ["revenue_kpi"],
                }
            ]
        },
    }

    migrated = _migrate(raw, catalog)

    tab = migrated["tabs"]["items"][0]
    assert tab["style"]["tones"] == {"positive": "#00ff00"}
    assert "tones" not in tab["style"].get("charts", {}).get("kpi", {})
    AuthoredBoard.model_validate(migrated)


def test_migrate_yaml_text_raises_for_style_charts_kpi_tones(
    catalog: YamlSchemaCatalog,
) -> None:
    """dct migrate refuses rather than corrupting the file.

    The in-memory migration leaves an empty ``style.charts.kpi`` mapping,
    which the text writer cannot express, so its final check fails loud.
    """
    _, registry = _board_migration_context()
    yaml_text = (
        "charts:\n"
        "  revenue_kpi:\n"
        "    type: kpi\n"
        "    query: q1\n"
        "    value: revenue\n"
        "style:\n"
        "  charts:\n"
        "    kpi:\n"
        "      tones:\n"
        "        positive: '#00ff00'\n"
        "rows: [revenue_kpi]\n"
    )

    with pytest.raises(MigrationError, match="diverged"):
        migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)


def test_rows_nested_style_charts_kpi_tones_is_uncovered() -> None:
    """A single sub-board nesting (``rows.*``) is uncovered, standing alone.

    ``_relative_field_paths`` checks its ``seen`` ancestor set on entry, so
    ``AuthoredBoard`` reached a second time (through ``rows``) never yields
    its own fields again — ``tones:`` one ``rows``/``cols``/
    ``grid.items.*.item``/``tabs.items.*`` hop below the root is not a
    declared Move position. With no root-level ``tones:`` in the same
    document, ``_recognize`` matches no declared transition and
    ``prepare_board_mapping`` returns the original silently (same path as
    ``test_uncovered_only_board_is_returned_unmigrated_and_silent`` below).
    """
    raw = {
        "rows": [
            {
                "rows": ["revenue_kpi"],
                "style": {"charts": {"kpi": {"tones": {"positive": "#0a7"}}}},
            }
        ],
    }
    original = copy.deepcopy(raw)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = prepare_board_mapping(raw)

    assert result == original
    assert caught == []


def test_mixed_scope_board_aborts_rather_than_half_migrating() -> None:
    """The residual re-check stops a board being migrated only halfway.

    A board authoring ``tones`` at BOTH a covered scope (root) and an
    uncovered one is the dangerous shape: the root Move applies, and without
    the re-check the author would get a file with its root migrated and its
    uncovered scope silently left on retired syntax. ``migrate_mapping``
    raises ``IncompleteMigrationError`` on the residual instead, and
    ``prepare_board_mapping`` warns and hands back the original.

    This is the property the boundary docstring relies on to justify
    shipping partial coverage, so it is pinned here. A single
    ``rows``/``cols``/``grid`` self-nesting (``rows.*``) is already enough
    to be the uncovered scope, since self-nesting is uncovered at every
    depth.
    """
    raw = {
        "text": "x",
        "style": {"charts": {"kpi": {"tones": {"positive": "#0a7"}}}},
        "rows": [
            {
                "rows": ["c"],
                "style": {"charts": {"kpi": {"tones": {"positive": "#0a7"}}}},
            }
        ],
    }
    original = copy.deepcopy(raw)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = prepare_board_mapping(raw)

    assert result == original, "a mixed-scope board must not be half-migrated"
    assert any("could not finish migrating" in str(w.message) for w in caught), (
        "the incomplete migration must warn, not pass silently"
    )


def test_uncovered_only_board_is_returned_unmigrated_and_silent() -> None:
    """An uncovered-only board takes the other path — and takes it quietly.

    ``_recognize`` matches no declared transition, so ``UnsupportedSchemaError``
    is raised before any move runs and ``prepare_board_mapping`` returns the
    original with no warning. Pinned because the docstring calls this out as a
    known rough edge: the author sees ``extra_forbidden`` on retired syntax with
    no hint that a migration was skipped. If this ever starts warning, the
    docstring should stop apologizing for it.

    A second self-nesting (``rows.*.rows.*``) is used here (rather than the
    single nesting in the test above) just to exercise a deeper uncovered
    shape — both are equally uncovered.
    """
    raw = {
        "rows": [
            {
                "rows": [
                    {
                        "rows": ["c"],
                        "style": {"charts": {"kpi": {"tones": {"positive": "#0a7"}}}},
                    }
                ],
            }
        ]
    }
    original = copy.deepcopy(raw)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = prepare_board_mapping(raw)

    assert result == original
    assert not caught, "unrecognized-schema path is silent today — see the docstring"
