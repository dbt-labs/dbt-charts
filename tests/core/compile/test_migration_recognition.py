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
from datetime import date
from typing import cast

import pytest
import yaml

from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    UnsupportedSchemaError,
    migrate_board_yaml_text,
    prepare_board_mapping,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
)


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


_RECOGNITION_DAY = date(2000, 1, 1)


def _prepared(board: JsonObject) -> JsonObject:
    """Migrate through the real catalog, with the support window taken out.

    ``today=_RECOGNITION_DAY`` because these tests are about *recognition*, not the
    six-month window — otherwise they inherit the release date of whichever
    retired construct ``_retired_board`` uses and acquire an expiry of their own.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return prepare_board_mapping(board, today=_RECOGNITION_DAY)


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


def test_unfinished_migration_is_announced() -> None:
    """A migration that runs but cannot finish must say so.

    A document that still fails the current-grammar gate after its retired
    constructs migrate (here, a `rows:` that is not a list) is handed to the parser
    unmigrated. That is survivable; doing it silently is what made the reported
    bug undiagnosable from the error text.
    """
    board = _retired_board()
    board["rows"] = "not-a-list"

    # The location is pinned, not just the fact of a warning: "migration was
    # skipped" without "and here is where" is most of the way back to the
    # original bug, where the author was told nothing about which construct
    # stopped it. The validator's own text for a composite failure dumps the offending
    # node and names no path at all.
    with pytest.warns(
        SchemaMigrationWarning,
        match=r"could not finish migrating.*at rows: ",
    ):
        prepared = prepare_board_mapping(board, today=_RECOGNITION_DAY)

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
    # newest frozen grammar; `today=_RECOGNITION_DAY` so the next freeze cannot turn
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
        prepared = prepare_board_mapping(board, today=_RECOGNITION_DAY)

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


def test_an_expired_grammar_is_announced_not_raised() -> None:
    """Falling out of the support window must not crash the loader either.

    The board comes back unchanged with a notice saying no migration was
    attempted, not that one could not finish: nothing ran.
    """
    raw = _retired_board()
    with pytest.warns(SchemaMigrationWarning, match="did not migrate") as caught:
        prepared = prepare_board_mapping(deepcopy(raw), today=date(2100, 1, 1))

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
            prepare_board_mapping(deepcopy(board), today=_RECOGNITION_DAY)
        return [str(w.message) for w in caught if "dropped" in str(w.message)]

    assert drop_warnings_for(cast(JsonObject, dropped)), (
        "fixture no longer drops anything — pick a live conditional move"
    )

    # A `rows:` that is not a list keeps the migrated result failing the gate, so it
    # is discarded and the original — tone included — is kept.
    survived = deepcopy(dropped)
    survived["rows"] = "not-a-list"
    assert drop_warnings_for(cast(JsonObject, survived)) == []


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


_BAR_BOARD = (
    _PREAMBLE + "charts:\n  c:\n    type: bar\n    query: q1\n    x: a\n    y: b\n"
)


@pytest.mark.parametrize("position", ["rows", "cols"])
@pytest.mark.parametrize(
    ("chart_id", "bad_key"),
    [
        ("style", "page: {background: red}"),
        ("style", "variables: {padding: 4}"),
        ("grid", "row_height: 3"),
        ("grid", "default_width: 3"),
    ],
)
def test_a_bad_key_on_a_chart_named_like_a_board_field_is_not_stripped(
    chart_id: str, bad_key: str, position: str
) -> None:
    """A chart the author *named* `style` or `grid` is not a nested board's block.

    The layout slot accepts a nested board or a `dict[str, chart]` map; a retired
    board-level tail (`style.page`, `grid.row_height`) must not strip the chart's
    own bad key.
    """
    from dbt_charts.core.compile.compiler import compile as compile_board

    fields = ["type: bar", "query: q1", "x: a", "y: b", bad_key]
    body = "".join(f"      {field}\n" for field in fields)
    layout = f"{position}:\n  - {chart_id}:\n{body}"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = compile_board(_PREAMBLE + layout)

    assert not result.success
    assert any(chart_id in error.path for error in result.errors)
    assert [w for w in caught if "was removed" in str(w.message)] == []


@pytest.mark.parametrize(
    ("board_text", "error_path"),
    [
        (_BAR_BOARD + "rows:\n- c\nstyle:\n  color: true\n", "style"),
        (
            _BAR_BOARD + "    conditional_formatting:\n    - when: x\nrows:\n- c\n",
            "conditional_formatting",
        ),
    ],
    ids=["root-style-color", "bar-conditional-formatting"],
)
def test_a_retired_key_with_an_invalid_value_reaches_the_parser(
    board_text: str, error_path: str
) -> None:
    """Migration translates a retired construct only if its own grammar accepted it.

    Anything else is an authoring mistake, and deleting it would swap a compile
    error for a silent success.
    """
    from dbt_charts.core.compile.compiler import compile as compile_board

    board = yaml.safe_load(board_text)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        prepared = prepare_board_mapping(deepcopy(board), today=_RECOGNITION_DAY)
        result = compile_board(board_text)

    assert prepared == board
    assert [w for w in caught if "was removed" in str(w.message)] == []
    assert not result.success
    assert any(error_path in error.path for error in result.errors)
    with pytest.raises(UnsupportedSchemaError):
        migrate_board_yaml_text(board_text)
