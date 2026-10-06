"""Migration test for the 0.5.0 -> 0.6.0 KpiTonesStyle Move.

``style.charts.kpi.tones`` moved to board-level ``style.tones`` (a sibling of
``style.palettes``) — declared as a ``Move`` in
``compile/migrations/versions/v0_6_0.py``. An authored board (or theme
patch) using the pre-move path migrates transparently.

The Move resolves to the root-anchored ``style.charts.kpi.tones`` and the
per-tab ``tabs.items.*.style.charts.kpi.tones`` (a tab's own ``style:``
override); ``Move.sub_board_prefixes`` repeats both inside every board nested
in a board (``rows``/``cols``/``grid.items.*.item``).

``migrate_mapping`` (in-memory, used when compiling/loading an old board)
relocates the whole ``tones:`` mapping regardless of its shape — ``Move``
pops and re-places the raw value found at ``old_path``. ``migrate_yaml_text``
(the ``dct migrate`` text-preserving rewriter) is narrower by design — see
its own docstring — and refuses a non-scalar move to a different parent
rather than reformat the file; a board authoring the old ``tones:`` shape
must be moved by hand when run through ``dct migrate``.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile.migrations import (
    MigrationError,
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
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
    """dct migrate's text-preserving rewriter cannot perform this relocation.

    Unlike migrate_mapping, migrate_yaml_text only rewrites scalar
    block-mapping leaves or same-position key renames (see its own
    docstring) — a whole nested mapping moving to a different parent is out
    of its scope by design. This must fail loud with a MigrationError
    naming the field, not silently leave it or corrupt the file; the author
    is told to migrate this field manually.
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

    with pytest.raises(MigrationError, match="style.charts.kpi.tones"):
        migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)


def _nested(depth: int) -> dict[str, Any]:
    """A board holding *depth* boards, the innermost authoring retired tones."""
    inner: dict[str, Any] = {
        "rows": ["c"],
        "style": {"charts": {"kpi": {"tones": {"positive": "#0a7"}}}},
    }
    for _ in range(depth - 1):
        inner = {"rows": [inner]}
    return {"rows": [inner]}


@pytest.mark.parametrize("depth", [1, 2])
def test_nested_sub_board_style_charts_kpi_tones_migrates(
    depth: int, catalog: YamlSchemaCatalog
) -> None:
    """A board nested in a board (``rows.*``, at any depth) migrates like the root."""
    migrated = _migrate(_nested(depth), catalog)

    innermost = migrated
    for _ in range(depth):
        innermost = innermost["rows"][0]
    assert innermost["style"]["tones"] == {"positive": "#0a7"}
    assert "tones" not in innermost["style"]["charts"]["kpi"]
    AuthoredBoard.model_validate(migrated)


def test_root_and_nested_sub_board_tones_migrate_together(
    catalog: YamlSchemaCatalog,
) -> None:
    """Both scopes move, so the residual re-check has nothing left to refuse."""
    raw = _nested(1)
    raw["text"] = "x"
    raw["style"] = {"charts": {"kpi": {"tones": {"positive": "#0a7"}}}}

    migrated = _migrate(raw, catalog)

    assert migrated["style"]["tones"] == {"positive": "#0a7"}
    assert migrated["rows"][0]["style"]["tones"] == {"positive": "#0a7"}
