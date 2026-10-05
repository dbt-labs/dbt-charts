"""``retired_keys`` reports what the migration declarations would fire on."""

from __future__ import annotations

import copy
from typing import cast

from dbt_charts.core.compile.migrations import (
    ConditionalMove,
    Deletion,
    MapKeyDeletion,
    MigrationRegistry,
    Move,
)
from dbt_charts.core.compile.migrations.migrations import RetiredKey, retired_keys
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import JsonObject

from ._migration_catalogs import synthetic_catalog

V1 = "0.1.0"
V2 = "0.2.0"


def _board(chart: dict[str, object]) -> JsonObject:
    return cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "charts": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["bar", "table"]},
                            **chart,
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    )


_STR = {"type": "string"}
_OBJ = {"type": "object", "additionalProperties": False}
CATALOG = synthetic_catalog(
    {
        V1: _board(
            {
                "old_name": _STR,
                "dead": _STR,
                "scoped": _STR,
                "style": {**_OBJ, "properties": {"tone": _STR}},
            }
        ),
        V2: _board(
            {"new_name": _STR, "support": {**_OBJ, "properties": {"tone": _STR}}}
        ),
    }
)
MOVE = Move(V1, V2, ("charts", "*", "old_name"), ("charts", "*", "new_name"))
DOC: dict[str, object] = {
    "charts": {
        "b": {
            "type": "bar",
            "old_name": "x",
            "dead": "y",
            "scoped": "z",
            "style": {"tone": "t"},
        },
        "t": {"type": "table", "scoped": "z"},
    }
}


def _report(
    document: dict[str, object],
    *,
    moves: tuple[Move, ...] = (),
    deletions: tuple[Deletion, ...] = (),
    conditional_moves: tuple[ConditionalMove, ...] = (),
    map_deletions: tuple[MapKeyDeletion, ...] = (),
) -> dict[tuple[str, ...], RetiredKey]:
    registry = MigrationRegistry(
        moves, deletions, conditional_moves, map_deletions, catalog=CATALOG
    )
    return retired_keys(cast(JsonObject, document), CATALOG, registry)


def test_move_binds_wildcards_and_names_successor_and_version() -> None:
    assert _report(DOC, moves=(MOVE,)) == {
        ("charts", "b", "old_name"): RetiredKey(
            ("charts", "b", "old_name"), V2, ("charts", "b", "new_name"), None
        )
    }


def test_prefix_changing_move_names_the_full_successor_path() -> None:
    move = Move(V1, V2, ("charts", "*", "old_name"), ("charts", "*", "support", "n"))

    assert _report(DOC, moves=(move,))[("charts", "b", "old_name")].successor == (
        "charts",
        "b",
        "support",
        "n",
    )


def test_deletion_carries_its_reason_or_none() -> None:
    report = _report(
        DOC,
        deletions=(
            Deletion(V1, V2, ("dead",), reason="never read"),
            Deletion(V1, V2, ("old_name",)),
        ),
    )

    assert report[("charts", "b", "dead")] == RetiredKey(
        ("charts", "b", "dead"), V2, None, "never read"
    )
    assert report[("charts", "b", "old_name")].reason is None


def test_chart_type_scoped_deletion_reports_only_that_family() -> None:
    report = _report(DOC, deletions=(Deletion(V1, V2, ("scoped",), chart_type="bar"),))

    assert list(report) == [("charts", "b", "scoped")]


def test_conditional_move_reports_the_bound_destination() -> None:
    rule = ConditionalMove(
        V1,
        V2,
        "bar",
        ("style", "tone"),
        ("support", "tone"),
        ("support",),
        "dropped {chart}",
    )

    assert _report(DOC, conditional_moves=(rule,)) == {
        ("charts", "b", "style", "tone"): RetiredKey(
            ("charts", "b", "style", "tone"),
            V2,
            ("charts", "b", "support", "tone"),
            None,
        )
    }


def test_identity_move_and_map_key_deletion_are_not_retired_keys() -> None:
    identity = Move(
        V1, V2, ("charts", "*", "type"), ("charts", "*", "type"), {"bar": "table"}
    )
    map_keys = MapKeyDeletion(V1, V2, ("charts",), {"b": "gone"})

    assert _report(DOC, moves=(identity,), map_deletions=(map_keys,)) == {}


def test_report_does_not_mutate_the_document() -> None:
    before = copy.deepcopy(DOC)

    _report(DOC, moves=(MOVE,), deletions=(Deletion(V1, V2, ("dead",)),))

    assert before == DOC


def test_a_new_declaration_is_reported_with_no_other_change() -> None:
    other = Move(V1, V2, ("charts", "*", "dead"), ("charts", "*", "alive"))

    assert len(_report(DOC, moves=(MOVE,))) == 1
    assert len(_report(DOC, moves=(MOVE, other))) == 2
