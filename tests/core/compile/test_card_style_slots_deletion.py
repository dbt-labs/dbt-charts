"""Migration coverage for the font/border removal on the card-less families.

Board-level ``style.charts.<family>.font`` / ``.border`` were accepted by the
0.5.0 grammar and are auto-stripped by ``dct migrate`` (Deletion declared at
the 0.5.0 -> 0.6.0 boundary in ``versions/v0_6_0.py``). The chart-local
position (``charts.<id>.style.font`` / ``.border``) is declarable but
unconverted -- its only available tails are live on the families that keep
theirs, so it needs per-family ``chart_type`` scoping -- and fails loud today.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile import compile
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

CARD_LESS_FAMILIES = (
    "bar",
    "line",
    "area",
    "scatter",
    "histogram",
    "heatmap",
    "pie",
)


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
            "sales": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
            }
        },
        "style": style,
        "rows": ["sales"],
    }


def test_registry_declares_the_board_level_card_style_deletions() -> None:
    """Declared at the 0.5.0 -> 0.6.0 boundary, now frozen -- not at
    ``catalog.latest_released.version``, which is 0.6.0 itself post-freeze."""
    _, registry = _board_migration_context()
    deletions = registry.deletions_from("0.5.0")

    # Scoped to the card-style fields this test is about. `charts`-anchored
    # deletions are not exclusively card-style -- heatmap's inert
    # `axis_quantitative` slot is one too -- so an unfiltered set comparison
    # here fails on every unrelated family/field removal that follows.
    declared = {
        d.path
        for d in deletions
        if d.path[0:1] == ("charts",) and d.path[2:3] in (("font",), ("border",))
    }
    assert declared == {
        ("charts", fam, field)
        for fam in CARD_LESS_FAMILIES
        for field in ("font", "border")
    }


@pytest.mark.parametrize("family", CARD_LESS_FAMILIES)
@pytest.mark.parametrize("field", ["font", "border"])
def test_tail_was_in_the_released_grammar_and_is_gone_from_the_live_one(
    family: str, field: str, catalog: YamlSchemaCatalog
) -> None:
    """Both halves of what makes a Deletion legal, asserted directly.

    Source-present is what makes the key worth migrating; target-absent is what
    stops the tail stripping a slot that still works. Source is pinned to
    0.5.0, the grammar the tail actually shipped in -- not
    ``catalog.latest_released.version``, which is 0.6.0 post-freeze and never had it.
    """
    tail = ("charts", family, field)
    assert schema_has_tail(catalog.schema_for("0.5.0"), tail)
    assert not schema_has_tail(catalog.current_schema, tail)


@pytest.mark.parametrize("family", CARD_LESS_FAMILIES)
@pytest.mark.parametrize("field", ["font", "border"])
def test_board_level_card_style_key_stripped(
    family: str, field: str, catalog: YamlSchemaCatalog
) -> None:
    value = {"size": 40} if field == "font" else {"width": 12}
    raw = _board({"charts": {family: {field: value, "preferred_width": 300}}})

    migrated = _migrate(raw, catalog)

    assert field not in migrated["style"]["charts"][family]
    # The sibling key on the same slot survives -- the tail strips one field,
    # not the whole family block.
    assert migrated["style"]["charts"][family]["preferred_width"] == 300
    AuthoredBoard.model_validate(migrated)


def test_live_board_frame_border_survives_migration(
    catalog: YamlSchemaCatalog,
) -> None:
    """The anchored tails must not strip the board frame's own border.

    This is the key a bare ("border", ...) tail would have taken out.
    """
    raw = _board({"border": {"width": 2, "color": "#aaa", "radius": 4}})

    migrated = _migrate(raw, catalog)

    assert migrated["style"]["border"] == {"width": 2, "color": "#aaa", "radius": 4}
    AuthoredBoard.model_validate(migrated)


def test_live_marks_bar_border_survives_migration(
    catalog: YamlSchemaCatalog,
) -> None:
    """charts.marks.bar.border keeps a full BorderStyle and must be untouched.

    A bare ("bar", "border") tail would have matched it; the charts anchor is
    what prevents that.
    """
    raw = _board({"charts": {"marks": {"bar": {"border": {"width": 1}}}}})

    migrated = _migrate(raw, catalog)

    assert migrated["style"]["charts"]["marks"]["bar"]["border"] == {"width": 1}
    AuthoredBoard.model_validate(migrated)


@pytest.mark.parametrize("field", ["font", "border"])
def test_chart_local_position_is_not_migrated(field: str) -> None:
    """The chart-local half ships no Deletion, so the key still fails loud.

    Pinned at ``compile()``, where an author meets it: asserting the tail is
    live would say nothing, since a scoped declaration is legal on a live tail.
    The hint's wording is covered by test_card_style_hint_font_border.py.
    """
    result = compile(
        f"""title: Test
queries:
  q: {{type: values, rows: [{{n: 1}}]}}
charts:
  c:
    type: bar
    query: q
    x: n
    y: n
    style:
      {field}: {{}}
rows:
- c
"""
    )

    assert not result.success
    unknown = {e.fields.get("unknown_field") for e in result.errors if e.fields}
    assert field in unknown
