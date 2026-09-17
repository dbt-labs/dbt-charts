"""Recognition must not treat a *newer* construct as a reason to skip migration.

Recognition answers one question: which transition chain does this document
enter at? A document carrying a key that postdates every retained grammar is
still an old document — the newer key is not evidence of a different grammar.
Scoring it as a mismatch silently disabled migration and surfaced the stale
syntax as unrelated validation errors, with no signal that a migration had been
skipped.
"""

from __future__ import annotations

import warnings
from copy import deepcopy
from typing import cast

import pytest

from dbt_charts.core.compile.migrations import (
    IncompleteMigrationError,
    MigrationRegistry,
    Move,
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
    prepare_board_mapping,
)
from dbt_charts.core.compile.migrations.migrations import (
    _board_migration_context,
    _recognize,
    _schema_path_exists,
    move_source_locations,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    YamlSchemaCatalog,
    YamlSchemaEntry,
)

from ._migration_catalogs import flat_schema, released, synthetic_catalog
from ._migration_declarations import schema_has_tail, schema_has_tail_for_chart_type

V1 = "0.1.0"
V2 = "0.2.0"


def _at(document: JsonObject, *keys: str) -> JsonObject:
    node = document
    for key in keys:
        node = cast(JsonObject, node[key])
    return node


def _retired_board(**root: object) -> JsonObject:
    """A board whose only defect is a construct the real catalog can migrate.

    ``style.axis_y.format`` moved under ``labels:`` in a retained transition;
    the tests below assert it actually migrated, so this fixture fails loudly
    rather than passing vacuously if that transition ages out of the window.
    """
    return cast(
        JsonObject,
        {
            "source": "s",
            "queries": {"q": {"sql": "select 1 as a, 2 as b"}},
            "charts": {
                "c": {
                    "query": "q",
                    "type": "line",
                    "x": "a",
                    "y": "b",
                    "style": {"axis_y": {"format": ".2%"}},
                }
            },
            # `style.charts.animation_duration` is a declared *Deletion*, and a
            # root-anchored one. Without it this fixture carries only a Move,
            # whose applier matches absolute paths and never enters the
            # positionally gated walk — so the test would pass while `theme:`
            # stayed broken for every Deletion-carrying board.
            "style": {"charts": {"animation_duration": 100}},
            "rows": [{"cols": ["c"]}],
            **root,
        },
    )


def _prepared(board: JsonObject) -> JsonObject:
    """Migrate through the real catalog, with the support window taken out.

    ``allow_expired=True`` because these tests are about *recognition*, not the
    six-month window — otherwise they inherit the release date of whichever
    retired construct ``_retired_board`` uses and acquire an expiry of their own.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return prepare_board_mapping(board, allow_expired=True)


def test_theme_key_migrates_identically_to_extends() -> None:
    """The reported case: `theme: X` and `extends: X` are the same document."""
    via_extends = _prepared(_retired_board(extends="vivid"))
    via_theme = _prepared(_retired_board(theme="vivid"))

    assert "format" not in _at(via_extends, "charts", "c", "style", "axis_y"), (
        "fixture no longer exercises a retired construct — pick a live one"
    )
    assert {key: value for key, value in via_theme.items() if key != "theme"} == {
        key: value for key, value in via_extends.items() if key != "extends"
    }


def test_retired_root_key_migrates_alongside_a_newer_root_key() -> None:
    """`allow_html:` (retired) and `theme:` (newer) both sit at the root.

    The live instance of the case that rules out deciding recognition by where
    the two grammars disagree: an unexpected root key is reported *at the root*,
    so the retired key and the newer key are indistinguishable by location.
    """
    prepared = _prepared(_retired_board(allow_html=True, theme="vivid"))

    assert "allow_html" not in prepared
    assert "html_policy" in prepared


def _post_freeze_context() -> tuple[YamlSchemaCatalog, MigrationRegistry]:
    """A catalog whose live grammar knows a key no frozen grammar has seen.

    Every field added between two schema freezes has this shape, which is why
    this is pinned synthetically rather than against whichever keys happen to
    postdate the newest freeze today.
    """
    catalog = synthetic_catalog(
        {V1: flat_schema("old", "keep"), V2: flat_schema("new", "keep")},
        flat_schema("new", "keep", "fresh"),
    )
    registry = MigrationRegistry(
        [Move(V1, V2, ("old",), ("new",))],
        catalog=catalog,
    )
    return catalog, registry


def test_key_newer_than_every_frozen_grammar_does_not_disable_migration() -> None:
    catalog, registry = _post_freeze_context()

    with pytest.warns(SchemaMigrationWarning):
        result = migrate_mapping(
            cast(JsonObject, {"old": "value", "fresh": "f"}),
            catalog=catalog,
            registry=registry,
        )

    assert result == {"new": "value", "fresh": "f"}


def test_current_document_with_a_newer_key_is_left_alone() -> None:
    catalog, registry = _post_freeze_context()
    raw = cast(JsonObject, {"new": "value", "fresh": "f"})

    assert migrate_mapping(raw, catalog=catalog, registry=registry) == raw


def test_unfinished_migration_is_announced() -> None:
    """A migration that runs but cannot finish must say so.

    ``cache: "1h"`` is documented scalar shorthand for ``CachePatch`` — expanded
    by a `model_validator(mode="before")` the generated JSON Schema cannot
    express (the schema only knows the block form: ``{$ref: CachePatch}``, no
    string/bool arm) — so the migrated document still fails the
    current-grammar gate and is handed to the parser unmigrated. That is
    survivable; doing it silently is what made the reported bug undiagnosable
    from the error text.

    (``queries: {q: "<sql>"}`` used to be this test's trigger for the same
    reason, before introspection learned to read a `BeforeValidator`'s
    `json_schema_input_type` — that shorthand migrates cleanly now, see
    test_authoring_shorthand_migration.py.)
    """
    board = _retired_board()
    board["cache"] = "1h"

    # The location is pinned, not just the fact of a warning: "migration was
    # skipped" without "and here is where" is most of the way back to the
    # original bug, where the author was told nothing about which construct
    # stopped it. Draft7's own text for a composite failure dumps the offending
    # node and names no path at all.
    with pytest.warns(
        SchemaMigrationWarning,
        match=r"could not finish migrating.*at cache: ",
    ):
        prepared = prepare_board_mapping(board, allow_expired=True)

    assert prepared == board


def test_a_board_that_was_never_old_is_not_announced_as_a_skipped_migration() -> None:
    """The other half of the split, and the reason it exists.

    A board that fails recognition because it is simply invalid had no migration
    to skip. Announcing one sends the author looking for retired syntax that is
    not there. Widen the warning to the parent error class and this catches it.
    """
    invalid = _retired_board()
    _at(invalid, "charts", "c")["type"] = "not_a_chart_type"
    del _at(invalid, "charts", "c", "style")["axis_y"]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        prepare_board_mapping(invalid)

    assert [w for w in caught if issubclass(w.category, SchemaMigrationWarning)] == []


def test_a_migration_that_cannot_be_applied_reaches_the_parser_not_the_user() -> None:
    """``prepare_board_mapping`` returns a mapping or defers to the parser.

    It never raises — a board carrying both spellings of a renamed key is an
    author mid-migration, exactly the person this machinery exists to serve, and
    a traceback out of the loader is the worst possible answer for them. The
    conflict is real and must still be reported; the parser reports it.
    """
    # `allow_html` → `html_policy` is window-exempt only while 0.4.0 is the
    # newest frozen grammar; `allow_expired=True` so the next freeze cannot turn
    # this into a support-window assertion.
    board = cast(
        JsonObject,
        {
            "title": "Mid-migration",
            "text": "x",
            "allow_html": True,
            "html_policy": "none",
        },
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        prepared = prepare_board_mapping(board, allow_expired=True)

    assert prepared == board
    assert [
        w
        for w in caught
        if issubclass(w.category, SchemaMigrationWarning)
        and "both fields exist" in str(w.message)
    ], "a migration that could not be applied must not be silent"


def test_conflicting_board_compiles_to_an_error_rather_than_raising() -> None:
    """The same case at the seam that actually failed.

    Pinned through ``compile()`` because every existing test drove these
    exceptions at ``migrate_mapping``/``_apply_move`` directly, which is how a
    crash on the real loading path stayed invisible to a green suite.
    """
    from dbt_charts.core.compile.compiler import compile as compile_board

    result = compile_board(
        "title: Mid-migration\n"
        "allow_html: true\n"
        "html_policy: none\n"
        "text: both spellings present\n"
    )

    assert not result.success
    assert any("allow_html" in error.message for error in result.errors)


@pytest.mark.parametrize("chart_id", ["zed", "bar", "line", "kpi", "style"])
@pytest.mark.parametrize("position", ["charts", "rows", "cols"])
@pytest.mark.parametrize(
    "post_freeze", [False, True], ids=["frozen-only", "post-freeze"]
)
def test_an_authoring_error_is_reported_whatever_the_chart_is_named(
    chart_id: str, position: str, post_freeze: bool
) -> None:
    """A retired *theme* tail must not match a user's chart id, anywhere.

    Ten declared deletion tails lead with a chart-family name — `("bar",
    "tooltip")` means the `bar` block under `style.charts` — and one leads with
    `style`. `charts:` and inline layout slots are both keyed by the author, so
    a chart named `bar`, or `style`, presents the same key chain. Matching it
    deleted their content and let the board compile green: whether an authoring
    mistake got reported depended on what the chart was called and where it sat.

    Crossed three ways on purpose. Each direction was guarded alone before, at
    one position each, and a fix satisfying one re-broke another twice. The
    `post_freeze` axis carries `collapse:`, without which every fixture here
    short-circuits before the forgiveness arm and cannot see it loosen: replace
    that arm's re-validation with `return list(positions)` and only these cases
    go red.
    """
    from dbt_charts.core.compile.compiler import compile as compile_board

    body = (
        "    type: point_map\n    query: q1\n    latitude: lat\n    longitude: lon\n"
        "    tooltip: {enabled: true}\n"
    )
    if post_freeze:
        body += "    collapse: true\n"
    if position == "charts":
        layout = f"charts:\n  {chart_id}:\n{body}rows: [{chart_id}]\n"
    else:
        layout = f"{position}:\n  - {chart_id}:\n{body.replace('    ', '      ')}"

    preamble = (
        "title: T\n"
        "queries:\n"
        "  q1:\n"
        '    sql: "select 1.0 as lat, 2.0 as lon"\n'
        "    source: test\n"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_board(preamble + layout)

    # Inline slots report the whole item as unrecognized rather than naming the
    # key, so pin the chart, not the wording: the point is that the mistake
    # surfaces at all, wherever the chart sits and whatever it is called.
    assert not result.success
    assert any(chart_id in error.path for error in result.errors)


_CHART_BODY = (
    "      type: bar\n"
    "      query: q1\n"
    "      x: a\n"
    "      y: b\n"
    "      style:\n"
    '        tooltip: {format: ".2f"}\n'
)
_PREAMBLE = (
    'title: T\nqueries:\n  q1:\n    sql: "select 1 as a, 2 as b"\n    source: test\n'
)


@pytest.mark.parametrize(
    ("label", "layout"),
    [
        (
            "charts block",
            "charts:\n  c:\n" + _CHART_BODY.replace("      ", "    ") + "rows: [c]\n",
        ),
        ("inline in rows", "rows:\n  - c:\n" + _CHART_BODY),
        ("inline in cols", "cols:\n  - c:\n" + _CHART_BODY),
        (
            "nested board",
            "rows:\n  - cols:\n      - charts:\n          c:\n"
            + _CHART_BODY.replace("      ", "            ")
            + "        rows: [c]\n",
        ),
    ],
)
def test_a_retired_key_is_stripped_wherever_a_chart_may_be_authored(
    label: str, layout: str
) -> None:
    """The positional gate must not shrink to the shapes it was tested on.

    `style.tooltip` is retired and must be stripped from a chart in every
    authoring position. The discriminated chart union is emitted as `allOf` of
    `if`/`then`, which a walker that understands only `anyOf` reads as "this
    position declares nothing" — so an inline chart kept its retired key while
    the same chart under `charts:` lost it.
    """
    from dbt_charts.core.compile.compiler import compile as compile_board

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_board(_PREAMBLE + layout)

    assert result.success, f"{label}: {[(e.path, e.message) for e in result.errors]}"


def test_a_post_freeze_key_does_not_disable_a_deletion() -> None:
    """The headline bug, one level down, where the positional gate put it back.

    `collapse:` postdates every frozen grammar. Requiring a node to be *wholly*
    valid under the frozen source grammar before a tail counts as declared there
    is whole-document validation again, scoped to a subtree — so a newer key
    anywhere inside the chart stopped its retired `style.tooltip` from migrating,
    and did it on the silent arm.
    """
    from dbt_charts.core.compile.compiler import compile as compile_board

    board = (
        "title: T\n"
        'queries: {q1: {sql: "select 1.0 as lat, 2.0 as lon", source: test}}\n'
        "charts:\n"
        "  c:\n"
        "    type: point_map\n"
        "    query: q1\n"
        "    latitude: lat\n"
        "    longitude: lon\n"
        "    collapse: true\n"
        "    style:\n"
        '      tooltip: {format: ".2f"}\n'
        "rows: [c]\n"
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_board(board)

    assert result.success, [(e.path, e.message) for e in result.errors]


def test_a_current_document_is_never_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live-schema gate runs before the probe, not after it.

    Pinned by making the probe fatal rather than by finding a document the
    appliers would corrupt: the appliers are positionally gated now, so such a
    document may not exist today — and a guard that depends on one being found
    stops guarding the moment they get safer. What must stay true is that a
    current document is answered without consulting them at all.

    Demote the gate below the loop — the shape that let an already-current board
    be rewritten — and this fails.
    """
    from dbt_charts.core.compile.migrations import migrations as _impl

    def _fatal(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("probed a document the live grammar already accepts")

    monkeypatch.setattr(_impl, "_transition_applies", _fatal)
    catalog, registry = _board_migration_context()
    board = cast(
        JsonObject,
        {
            "title": "Current",
            "queries": {"q": {"sql": "select 1 as a, 2 as b"}},
            "charts": {
                "bar": {"query": "q", "type": "bar", "x": "a", "y": "b"},
                "legend": {"query": "q", "type": "line", "x": "a", "y": "b"},
            },
            "rows": [{"cols": ["bar", "legend"]}],
        },
    )

    assert _recognize(board, catalog, registry) == catalog.dev.version


def test_an_expired_grammar_is_announced_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling out of the support window must not crash the loader either.

    ``SchemaVersionTooOldError`` is a sibling of the other migration errors and
    rode the same uncaught route out of ``prepare_board_mapping``; the only test
    covering it drove ``migrate_mapping`` directly, where raising is correct.
    It must also say that no migration was *attempted*, not that one could not
    finish — nothing ran.
    """
    from dbt_charts.core.compile.migrations import migrations as _impl

    dev = "0.3.0"
    schemas = {V1: flat_schema("old", "keep"), V2: flat_schema("new", "keep")}
    entries = (
        YamlSchemaEntry(
            dev,
            status="DEV",
            released_at=None,
            filename=None,
            sha256=None,
            predecessor=V2,
        ),
        YamlSchemaEntry(
            V2,
            status="RELEASED",
            released_at=released(1),
            filename=f"{V2}.json",
            sha256="test",
            predecessor=V1,
        ),
        YamlSchemaEntry(
            V1,
            status="RELEASED",
            released_at=released(400),
            filename=f"{V1}.json",
            sha256="test",
            predecessor=None,
        ),
    )
    catalog = YamlSchemaCatalog(entries, {**schemas, dev: schemas[V2]}, schemas[V2])
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)
    monkeypatch.setattr(_impl, "_board_has_historical_schemas", lambda: True)
    monkeypatch.setattr(_impl, "_board_migration_context", lambda: (catalog, registry))

    raw = cast(JsonObject, {"old": "value"})
    with pytest.warns(SchemaMigrationWarning, match="did not migrate") as caught:
        prepared = prepare_board_mapping(raw)

    assert prepared == raw
    assert all("could not finish" not in str(w.message) for w in caught)


def test_a_discarded_migration_does_not_claim_it_dropped_anything() -> None:
    """A drop warning is a data-loss notice, so it must describe reality.

    A conditional move that cannot relocate a value drops it and says so. But
    when the migrated result then fails the current-grammar gate, the caller
    keeps the *original* mapping — the value is still in the file, and telling
    the author it was destroyed sends them looking for damage that never
    happened. So the warnings are held until the gate passes.

    Both halves asserted together: move the emission back inside the transition
    loop and the second case starts warning again.
    """
    dropped = {
        "title": "T",
        "queries": {"q": {"sql": "select 1 as revenue"}},
        "charts": {
            "k": {
                "type": "kpi",
                "query": "q",
                "value": "revenue",
                "style": {"tone": "positive"},
            }
        },
        "rows": ["k"],
    }

    def drop_warnings_for(board: JsonObject) -> list[str]:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            prepare_board_mapping(deepcopy(board), allow_expired=True)
        return [str(w.message) for w in caught if "dropped" in str(w.message)]

    assert drop_warnings_for(cast(JsonObject, dropped)), (
        "fixture no longer drops anything — pick a live conditional move"
    )

    # `cache: "1h"` is CachePatch's scalar shorthand — the schema only knows
    # the block form (`{$ref: CachePatch}`), so the migrated result is
    # discarded and the original — tone included — is kept.
    survived = deepcopy(dropped)
    survived["cache"] = "1h"
    assert drop_warnings_for(cast(JsonObject, survived)) == []


def test_no_retired_path_survives_into_the_current_grammar() -> None:
    """The precondition recognition rests on.

    A declared retired path is treated as proof that a document predates the
    current grammar. That inference is only sound while no retired path is
    also a live one -- checked structurally here for an ordinary rename
    (``validate_declarations``'s precondition: old_path must be absent
    from the target grammar). A ``chart_type``-scoped ``Deletion`` narrows
    that precondition to its own family's branch (``schema_has_tail_for_chart_type``,
    mirroring ``validate_declarations``'s own scoped check) -- a path can legitimately
    still exist globally (e.g. ``conditional_formatting`` on ``table``/``kpi``)
    as long as it is gone from the scoped family's own branch.

    An unscoped ``Deletion`` narrows it a third way, to the document root's own
    branch: ``style.color`` is gone from a board's style block while every chart
    family keeps its own, so the tail survives globally and the anchored path
    does not. ``_live_declares_tail`` is what confines the firing to the
    positions that lost it.

    An identity-path Move (``old_path == new_path``, e.g. dbt charts'
    `theme:` sugar) cannot satisfy that precondition at all -- its whole
    point is a key that survives every transition unrenamed. Key presence is
    therefore never evidence such a document is old; only the *value* found
    there is (`_identity_value_would_change`). The second half below pins
    that: for every value an identity-path Move's `value_map` sends to
    itself (a currently-valid value), `move_source_locations` must find
    nothing to migrate. This is what the structural check above stands in
    for on a shape that check cannot reach -- collapsing it to "any
    old_path != new_path" would make identity-path Moves untested rather
    than sound.
    """
    catalog, registry = _board_migration_context()

    assert registry.moves and registry.deletions, (
        "no declared moves or deletions — this would pass vacuously"
    )
    survivors = [
        move.old_path
        for move in registry.moves
        if move.old_path != move.new_path
        and _schema_path_exists(catalog.current_schema, move.old_path)
    ] + [
        deletion.path
        for deletion in registry.deletions
        if (
            schema_has_tail_for_chart_type(
                catalog.current_schema, deletion.path, deletion.chart_type
            )
            if deletion.chart_type is not None
            else schema_has_tail(catalog.current_schema, deletion.path)
        )
        and _schema_path_exists(catalog.current_schema, deletion.path)
    ]

    assert survivors == []

    identity_moves = [move for move in registry.moves if move.old_path == move.new_path]
    assert identity_moves, "no identity-path Move declared — this would pass vacuously"
    for move in identity_moves:
        assert move.value_map is not None, (
            f"identity-path Move for {move.old_path!r} has no value_map -- "
            "validate_declarations should have already rejected this"
        )
        current_values = [
            value for value, mapped in move.value_map.items() if mapped == value
        ]
        assert current_values, (
            f"identity-path Move for {move.old_path!r} has no current-value "
            "entries in its value_map to probe against"
        )
        for value in current_values:
            document = cast(JsonObject, _nest(move.old_path, value))
            assert not any(move_source_locations(document, move, catalog)), (
                f"identity-path Move for {move.old_path!r} fired on the "
                f"currently-valid value {value!r} -- it must gate on the "
                "value a document holds, not on the key merely being present"
            )


def _nest(path: tuple[str, ...], value: object) -> JsonObject:
    """Build a minimal document holding *value* at *path* (dict keys only)."""
    node: JsonObject = cast(JsonObject, value)
    for key in reversed(path):
        node = {key: node}
    return node


def test_the_file_writer_will_not_strip_a_chart_named_after_a_deletion_tail() -> None:
    """The disk half of the collision, which the in-memory tests cannot reach.

    ``dct migrate`` rewrites files. Before the positional gate it stripped a
    ``tooltip:`` block from a chart the author had named ``bar`` and wrote the
    result back — the collision, made permanent. The in-memory guard cannot
    catch that on its own: with both appliers ungated they agree, so the
    text-vs-memory divergence check passes and the file is written.

    Pinned on the real writer rather than through ``compile()``, because the two
    appliers are separate code paths and only one of them is gated.
    """
    from dbt_charts.core.compile.migrations import (
        IncompleteMigrationError,
        migrate_board_yaml_text,
    )

    text = (
        "title: T\n"
        "queries:\n"
        "  q1:\n"
        '    sql: "select 1 as a, 2 as b"\n'
        "    source: test\n"
        "charts:\n"
        "  bar:\n"  # the author's chart id collides with the `bar` family tail
        "    type: bar\n    query: q1\n    x: a\n    y: b\n"
        "    tooltip: {enabled: true}\n"
        "  other:\n"  # a genuinely retired tail, so the boundary is recognized
        "    type: line\n    query: q1\n    x: a\n    y: b\n"
        "    style:\n"
        '      tooltip: {format: ".2f"}\n'
        "rows: [bar, other]\n"
    )

    with pytest.raises(IncompleteMigrationError):
        migrate_board_yaml_text(text)


def test_a_post_freeze_key_nested_below_the_firing_node_is_still_forgiven() -> None:
    """The strip has to reach all the way down, not just the node it starts at.

    `style.charts.animation_duration` is a root-anchored deletion tail, so the
    tail fires at the document root — while `style.frame:` sits two levels
    below. Make the strip shallow and this board stops migrating, silently.

    Not a synthetic depth: `style.frame:` is the spelling every board picks up
    from the rebrand rename, so "newer key nested under the firing node" is the
    ordinary shape rather than the exotic one.
    """
    from dbt_charts.core.compile.compiler import compile as compile_board

    board = (
        "title: T\n"
        "text: hello\n"
        "style:\n"
        "  charts:\n"
        "    animation_duration: 100\n"
        "  frame:\n"
        "    width: 900\n"
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile_board(board)

    assert result.success, [(e.path, e.message) for e in result.errors]


def _pending_boundary_context() -> tuple[YamlSchemaCatalog, MigrationRegistry]:
    """A catalog with a pending ``latest_released -> DEV`` boundary.

    Models the real DEV-entry shape ``migrate_yaml_text``'s ``stop_target``
    exists to cap: ``V2`` (the frozen "latest released") is the source of a
    Move that only lands once the next, unreleased version ships. The live
    schema also declares ``_schema_version`` -- the field the capped
    on-disk rewrite stamps -- mirroring the real catalog: the live/current
    schema knows the field, older frozen schemas do not.
    """
    catalog = synthetic_catalog(
        {V1: flat_schema("keep"), V2: flat_schema("old", "keep")},
        flat_schema("new", "keep", "_schema_version"),
    )
    registry = MigrationRegistry(
        [Move(V2, catalog.dev.version, ("old",), ("new",))],
        catalog=catalog,
    )
    return catalog, registry


def test_migrate_yaml_text_stop_target_does_not_apply_the_pending_boundary() -> None:
    """dct migrate's on-disk rewrite must never write not-yet-frozen syntax.

    This board's only "retired" construct is the pending boundary itself
    (nothing frozen fires, 0 loop iterations), so nothing about the file
    actually changes -- including no stamp: the stamp is written only
    alongside a real structural change, never as the sole edit.
    """
    catalog, registry = _pending_boundary_context()
    yaml_text = "old: value\nkeep: k\n"

    capped = migrate_yaml_text(
        yaml_text, catalog=catalog, registry=registry, stop_target=V2
    )

    assert capped == yaml_text, "nothing changed, so the file must be untouched"


def test_migrate_yaml_text_without_stop_target_still_reaches_current() -> None:
    """The uncapped call (today's default, used directly by other tests) is unaffected."""
    catalog, registry = _pending_boundary_context()
    yaml_text = "old: value\nkeep: k\n"

    uncapped = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "new: value" in uncapped
    assert "old:" not in uncapped
    assert "_schema_version" not in uncapped, "uncapped calls never stamp"


def test_migrate_yaml_text_stop_target_never_stamps_an_already_current_file() -> None:
    """A file needing no structural migration must be returned byte-identical.

    A field the tool imposes on every file it touches is a much bigger
    change than "migrated files also get stamped" -- it would make dct
    migrate rewrite every already-current board in a project, and stamp a
    value the frozen version it names may not itself declare, purely for the
    stamp. The stamp is written only alongside a real structural change.
    Exercises the early ``identifier == catalog.dev.version`` return specifically.
    """
    catalog, registry = _pending_boundary_context()
    yaml_text = "keep: k\n"

    capped = migrate_yaml_text(
        yaml_text, catalog=catalog, registry=registry, stop_target=V2
    )

    assert capped == yaml_text


def test_migrate_yaml_text_stop_target_never_crashes_on_a_flow_style_root() -> None:
    """A flow-style-root board with nothing to migrate is valid YAML the text
    writer never attempts to edit -- it is byte-identical input to the early
    ``identifier == catalog.dev.version`` return, which touches nothing regardless of
    shape. Pins that recognition itself handles flow-style syntax cleanly
    (no crash), not a stamp-degradation mechanism -- there is none once
    nothing is written.
    """
    catalog, registry = _pending_boundary_context()
    flow_style = "{keep: k}\n"

    capped = migrate_yaml_text(
        flow_style, catalog=catalog, registry=registry, stop_target=V2
    )

    assert capped == flow_style, "an already-current file is never touched"


def test_migrate_yaml_text_stop_target_is_idempotent() -> None:
    """Running dct migrate twice must not rewrite an already-migrated,
    already-stamped file -- proven alongside a real frozen Move (not the
    pending-boundary-only fixture, which makes no change on the first run
    either, so two no-op runs would prove nothing about idempotency).
    """
    catalog = synthetic_catalog(
        {
            V1: flat_schema("old", "keep"),
            V2: flat_schema("new", "keep", "_schema_version"),
        }
    )
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)
    yaml_text = "old: value\nkeep: k\n"

    once = migrate_yaml_text(
        yaml_text, catalog=catalog, registry=registry, stop_target=V2
    )
    twice = migrate_yaml_text(once, catalog=catalog, registry=registry, stop_target=V2)

    assert once != yaml_text, "the real Move must have fired on the first run"
    assert twice == once
    assert f'_schema_version: "{V2}"' in once


def test_migrate_yaml_text_stop_target_stamp_line_explains_itself() -> None:
    """The written _schema_version: line is the file's first line and carries
    a trailing comment naming dct migrate as the author, so a reader scanning
    the file top-to-bottom never wonders where the line came from."""
    catalog = synthetic_catalog(
        {
            V1: flat_schema("old", "keep"),
            V2: flat_schema("new", "keep", "_schema_version"),
        }
    )
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)
    yaml_text = "old: value\nkeep: k\n"

    capped = migrate_yaml_text(
        yaml_text, catalog=catalog, registry=registry, stop_target=V2
    )

    assert (
        capped.splitlines()[0]
        == f'_schema_version: "{V2}"  # written automatically by dct migrate'
    )


def test_migrate_yaml_text_stop_target_never_downgrades_a_newer_stamp() -> None:
    """A well-formed, genuinely newer existing _schema_version must survive a
    real structural change untouched.

    This is the entire reason ``_should_stamp_schema_version`` exists rather
    than an unconditional overwrite: downgrading it would silently erase the
    signal the newer-schema-version diagnostic hint depends on. Proven
    alongside a real frozen Move, not the already-current early return --
    a document with no retired construct never reaches
    ``_should_stamp_schema_version`` at all (see the "never stamps an
    already-current file" test above), so that input can't pin this branch.
    """
    catalog = synthetic_catalog(
        {
            V1: flat_schema("old", "keep"),
            V2: flat_schema("new", "keep", "_schema_version"),
        }
    )
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)

    capped = migrate_yaml_text(
        '_schema_version: "99.0.0"\nold: value\nkeep: k\n',
        catalog=catalog,
        registry=registry,
        stop_target=V2,
    )

    assert '_schema_version: "99.0.0"' in capped
    assert "new: value" in capped


def test_migrate_yaml_text_stop_target_corrects_an_older_well_formed_stamp() -> None:
    """A well-formed but older existing _schema_version must be corrected to
    stop_target, not treated as "genuinely newer, leave alone" -- this is
    the mainline path after every release freeze, and had no fixture
    anywhere alongside its "newer" and "malformed" siblings.
    """
    catalog = synthetic_catalog(
        {
            V1: flat_schema("old", "keep"),
            V2: flat_schema("new", "keep", "_schema_version"),
        }
    )
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)

    capped = migrate_yaml_text(
        '_schema_version: "0.1.0"\nold: value\nkeep: k\n',
        catalog=catalog,
        registry=registry,
        stop_target=V2,
    )

    assert f'_schema_version: "{V2}"' in capped
    assert '"0.1.0"' not in capped
    assert "new: value" in capped


def test_migrate_yaml_text_stop_target_corrects_a_malformed_existing_stamp_alongside_a_real_change() -> (
    None
):
    """A well-formed-string-but-not-a-version existing stamp must still be
    corrected, not treated as "genuinely newer, leave alone" -- but only
    proven here alongside a genuine structural change (a real, frozen Move),
    since the stamp is never written as the sole reason to touch a file (see
    the "never stamps an already-current file" test). A regression to "never
    restamp anything unparseable" would strand a file on a bad
    ``_schema_version`` forever, silently, even when it's otherwise legitimately
    being rewritten.
    """
    catalog = synthetic_catalog(
        {
            V1: flat_schema("old", "keep"),
            V2: flat_schema("new", "keep", "_schema_version"),
        }
    )
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)

    capped = migrate_yaml_text(
        '_schema_version: "abc"\nold: value\nkeep: k\n',
        catalog=catalog,
        registry=registry,
        stop_target=V2,
    )

    assert f'_schema_version: "{V2}"' in capped
    assert '"abc"' not in capped
    assert "new: value" in capped
    assert '"abc"' not in capped


def _incomplete_at_target_context() -> tuple[YamlSchemaCatalog, MigrationRegistry]:
    """A V1 -> V2 Move covers ``old`` but not ``stray``.

    V2's frozen schema doesn't declare ``stray`` -- a document carrying it
    after migrating is a genuinely incomplete migration once capped at V2, not
    evidence of an unrelated live-only field the frozen-schema completeness
    check should tolerate.
    """
    catalog = synthetic_catalog(
        {V1: flat_schema("old", "stray", "keep"), V2: flat_schema("new", "keep")},
        flat_schema("new", "keep"),
    )
    registry = MigrationRegistry(
        [Move(V1, V2, ("old",), ("new",))],
        catalog=catalog,
    )
    return catalog, registry


def test_migrate_yaml_text_stop_target_still_raises_for_a_genuinely_incomplete_migration() -> (
    None
):
    """The capped completeness check must still catch real incompleteness."""
    catalog, registry = _incomplete_at_target_context()
    yaml_text = "old: value\nstray: s\nkeep: k\n"

    with pytest.raises(IncompleteMigrationError):
        migrate_yaml_text(yaml_text, catalog=catalog, registry=registry, stop_target=V2)
