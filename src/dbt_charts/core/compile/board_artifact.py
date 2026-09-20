"""Board-artifact codec — hoist repeated resolved styles into a shared table.

A serialized ``ResolvedBoard`` carries a full ``ResolvedStyle`` tree (~81 KB) on
the root *and* on every nested board, all byte-identical in practice. That makes
69-88% of an artifact redundant bytes, scaling with nesting depth rather than
with anything a reader cares about.

In memory the repetition costs nothing — every board shares one style object by
reference; only serialization expands it N times. So this is a serialization
concern only, and the fix lives here rather than in the models: ``hoist_styles``
replaces each occurrence with a ``{"$style_ref": <sha256>}`` marker and collects
the styles into a table keyed by the hash of their canonical JSON;
``inline_styles`` is the exact inverse.

Content-addressing, rather than assuming a single style, is what keeps this
correct when a nested board legitimately resolves a *different* style: N distinct
styles simply produce N table entries, with no special case.

``dump_resolved_board_artifact`` / ``load_resolved_board_artifact`` wrap the
codec in the publishable envelope: ``{$id, version, styles, board}``. ``$id``
names the schema this document conforms to
(``board-resolved.schema.json``) and ``version`` is the producing
``dbt-charts`` release — metadata a non-Python consumer needs to interpret the
``$style_ref`` markers correctly, since ``ResolvedBoard``/``ResolvedStyle``
themselves are unchanged and know nothing about hoisting.

``dump_compiled_board_artifact`` / ``load_compiled_board_artifact`` are the
same codec for the COMPILED (normalized) ``Board`` — the output of
``compile()``/``compile_file()``, one stage earlier than ``ResolvedBoard``.
That board carries two repeated trees per nested board, ``resolved_style``
and ``chart_style_context`` (the latter ~71 KB on its own); both hoist into
the *same* style table, since content-addressing means two different keys
sharing one table is free — a distinct table per key would only add
bookkeeping for no benefit. The envelope also carries ``diagnostics``
(errors then warnings), since a compiled artifact is the one that travels to
a consumer — like the planned Rust compile stage — that never runs the
Python compiler and has no other way to see them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from importlib.metadata import version as _installed_version
from typing import TYPE_CHECKING, Any, TypeAlias

from pydantic import TypeAdapter

from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.board.resolved import ResolvedBoard

if TYPE_CHECKING:
    from dbt_charts.core.compile.compiler import CompileResult

_STYLE_REF = "$style_ref"

# Matches the JSON Schema's own $id (schema/renderers/board_resolved.py) — an
# artifact declares the schema it conforms to using the same URI the schema
# publishes itself under.
SCHEMA_ID = "https://dbtcharts.com/schemas/board-resolved.schema.json"

# No JSON Schema renderer exists for the compiled artifact yet.
COMPILED_SCHEMA_ID = "https://dbtcharts.com/schemas/board-compiled.schema.json"

# One decoded JSON object. `Any` at the leaf is the accurate type — these
# functions move already-serialized documents around and never interpret the
# values — so it is named once here rather than repeated at every signature,
# matching `CacheRows` in execute/cache_backend.py.
JsonObject: TypeAlias = dict[str, Any]

StyleTable: TypeAlias = dict[str, JsonObject]

_BOARD_ADAPTER: TypeAdapter[ResolvedBoard] = TypeAdapter(ResolvedBoard)
_COMPILED_BOARD_ADAPTER: TypeAdapter[Board] = TypeAdapter(Board)


class DanglingStyleRefError(ValueError):
    """A ``$style_ref`` marker had no matching entry in the style table.

    Raised rather than substituting a default style: a board rendered against a
    silently-empty style would look plausible and be wrong, which this codebase
    treats as worse than a crash.

    Deliberately a plain ``ValueError`` rather than a ``DbtChartsError``: these
    functions are a pure codec with no user-facing entry point yet, so there is
    no raise site to stamp a registry ``ERR-*`` code on. When the follow-on task
    wires artifact loading to a ``dct`` verb, that loader is the boundary that
    should carry a registered code — an unstamped ``DbtChartsError`` here would
    surface as ``ERR-INTERNAL``, which signals a bug rather than a real tier.
    """


def _style_key(style: JsonObject) -> str:
    """Return the content address of a style document.

    Hashes canonical JSON (sorted keys, no whitespace) so the key depends only
    on content — dict insertion order cannot change it, and the same style
    produces the same key across processes and runs.
    """
    canonical = json.dumps(style, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _rewrite_boards(
    board: JsonObject,
    style_key: str,
    rewrite_style: Callable[[JsonObject], JsonObject],
) -> JsonObject:
    """Rebuild a board tree, applying ``rewrite_style`` to every board's
    ``style_key`` field (``"style"`` on a resolved board, ``"resolved_style"``
    or ``"chart_style_context"`` on a compiled one).

    Walks the root plus every nested board reachable through
    ``layout.items[].board``. Copies rather than mutating: the caller's JSON is
    left untouched, so a failed transform can never leave a half-rewritten tree.
    """
    out = dict(board)
    if style_key in out:
        out[style_key] = rewrite_style(out[style_key])

    # `layout` and its `items` are both required on their models, so a KeyError
    # here means a malformed artifact — which should fail loudly rather than
    # silently rewrite a board tree with no children.
    layout = out["layout"]
    new_layout = dict(layout)
    items = []
    for item in layout["items"]:
        new_item = dict(item)
        if new_item.get("board"):
            new_item["board"] = _rewrite_boards(
                new_item["board"], style_key, rewrite_style
            )
        items.append(new_item)
    new_layout["items"] = items
    out["layout"] = new_layout
    return out


def hoist_styles(board: JsonObject) -> tuple[JsonObject, StyleTable]:
    """Split a serialized board into a style-free board plus a style table.

    Args:
        board: A serialized ``ResolvedBoard`` (as produced by
            ``TypeAdapter(ResolvedBoard).dump_json``).

    Returns:
        ``(board, styles)`` where every board in ``board`` carries
        ``{"$style_ref": <sha256>}`` in place of its style, and ``styles`` maps
        each hash to the style document. Inverse of :func:`inline_styles`.
    """
    styles: StyleTable = {}

    def to_ref(style: JsonObject) -> dict[str, str]:
        key = _style_key(style)
        styles[key] = style
        return {_STYLE_REF: key}

    return _rewrite_boards(board, "style", to_ref), styles


def inline_styles(board: JsonObject, styles: StyleTable) -> JsonObject:
    """Splice styles back into a hoisted board, reversing :func:`hoist_styles`.

    Args:
        board: A board whose boards carry ``$style_ref`` markers.
        styles: The style table emitted alongside it.

    Returns:
        A serialized ``ResolvedBoard`` ready for
        ``TypeAdapter(ResolvedBoard).validate_python``.

    Raises:
        DanglingStyleRefError: If a marker has no entry in ``styles``.
    """

    def from_ref(style: JsonObject) -> JsonObject:
        key = style.get(_STYLE_REF)
        if key is None:
            # Already inline — an un-hoisted board round-trips unchanged.
            return style
        if key not in styles:
            raise DanglingStyleRefError(
                f"Style reference {key!r} is not in the style table "
                f"({len(styles)} entries). The artifact is incomplete: its "
                "board and style table came from different emits."
            )
        return styles[key]

    return _rewrite_boards(board, "style", from_ref)


def hoist_compiled_styles(board: JsonObject) -> tuple[JsonObject, StyleTable]:
    """Like :func:`hoist_styles`, for a compiled ``Board`` — hoists both
    ``resolved_style`` and ``chart_style_context`` into one shared table.

    Args:
        board: A serialized ``Board`` (as produced by
            ``TypeAdapter(Board).dump_python``).

    Returns:
        ``(board, styles)`` where every board in ``board`` carries a
        ``$style_ref`` marker in place of each of its two style fields, and
        ``styles`` maps each hash to the style document. Inverse of
        :func:`inline_compiled_styles`.
    """
    styles: StyleTable = {}

    def to_ref(style: JsonObject) -> dict[str, str]:
        key = _style_key(style)
        styles[key] = style
        return {_STYLE_REF: key}

    board = _rewrite_boards(board, "resolved_style", to_ref)
    board = _rewrite_boards(board, "chart_style_context", to_ref)
    return board, styles


def inline_compiled_styles(board: JsonObject, styles: StyleTable) -> JsonObject:
    """Reverse of :func:`hoist_compiled_styles`.

    Raises:
        DanglingStyleRefError: If a marker has no entry in ``styles``.
    """

    def from_ref(style: JsonObject) -> JsonObject:
        key = style.get(_STYLE_REF)
        if key is None:
            return style
        if key not in styles:
            raise DanglingStyleRefError(
                f"Style reference {key!r} is not in the style table "
                f"({len(styles)} entries). The artifact is incomplete: its "
                "board and style table came from different emits."
            )
        return styles[key]

    board = _rewrite_boards(board, "resolved_style", from_ref)
    return _rewrite_boards(board, "chart_style_context", from_ref)


def dump_resolved_board_artifact(board: ResolvedBoard) -> JsonObject:
    """Serialize a resolved board into the publishable, deduplicated artifact.

    Args:
        board: A fully resolved board (root or nested), as produced by
            ``build_resolved_board`` / ``build_resolved_board_static``.

    Returns:
        ``{"$id": SCHEMA_ID, "version": <dbt-charts release>, "styles": <table>,
        "board": <board tree with every style replaced by a $style_ref>}``.
    """
    board_json = _BOARD_ADAPTER.dump_python(board, mode="json", warnings="error")
    hoisted, styles = hoist_styles(board_json)
    return {
        "$id": SCHEMA_ID,
        "version": _installed_version("dbt-charts"),
        "styles": styles,
        "board": hoisted,
    }


def load_resolved_board_artifact(artifact: JsonObject) -> ResolvedBoard:
    """Reverse of :func:`dump_resolved_board_artifact`.

    Args:
        artifact: An envelope as produced by :func:`dump_resolved_board_artifact`.

    Returns:
        The reconstructed ``ResolvedBoard``, equal to the one originally dumped.

    Raises:
        DanglingStyleRefError: If the artifact's board and style table don't match.
    """
    board_json = inline_styles(artifact["board"], artifact["styles"])
    return _BOARD_ADAPTER.validate_python(board_json)


def dump_compiled_board_artifact(result: CompileResult) -> JsonObject:
    """Serialize a ``compile()``/``compile_file()`` result into the
    publishable, deduplicated compiled artifact.

    Args:
        result: A ``CompileResult`` with ``board`` set — i.e. ``result.success``.

    Returns:
        ``{"$id": COMPILED_SCHEMA_ID, "version": <dbt-charts release>,
        "styles": <table>, "board": <hoisted Board JSON>, "diagnostics":
        [<errors...>, <warnings...>]}``.

    Raises:
        ValueError: If ``result.board`` is ``None`` (compile failed) — there
            is no board to serialize.
    """
    if result.board is None:
        raise ValueError(
            "cannot build a board-compiled artifact from a failed compile "
            "(CompileResult.board is None); inspect result.errors instead"
        )
    board_json = _COMPILED_BOARD_ADAPTER.dump_python(
        result.board, mode="json", warnings="error"
    )
    hoisted, styles = hoist_compiled_styles(board_json)
    diagnostics = [
        diagnostic.model_dump(mode="json")
        for diagnostic in (*result.errors, *result.warnings)
    ]
    return {
        "$id": COMPILED_SCHEMA_ID,
        "version": _installed_version("dbt-charts"),
        "styles": styles,
        "board": hoisted,
        "diagnostics": diagnostics,
    }


def load_compiled_board_artifact(artifact: JsonObject) -> Board:
    """Reverse of :func:`dump_compiled_board_artifact`'s board half.

    Diagnostics are not reloaded — there is no ``CompileResult`` to rebuild
    them into, only the ``Board``.

    Args:
        artifact: An envelope as produced by :func:`dump_compiled_board_artifact`.

    Returns:
        The reconstructed ``Board``, equal to ``result.board`` originally dumped.

    Raises:
        DanglingStyleRefError: If the artifact's board and style table don't match.
    """
    board_json = inline_compiled_styles(artifact["board"], artifact["styles"])
    return _COMPILED_BOARD_ADAPTER.validate_python(board_json)
