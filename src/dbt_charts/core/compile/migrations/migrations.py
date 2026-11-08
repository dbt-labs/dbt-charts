"""Board grammar migrations: one engine, in Rust.

The `migrate` crate (``libs/chart-svg/migrate``) recognizes which retained
grammar a document was authored against and rewrites it to the current one;
its declarations live in ``libs/chart-svg/migrate/src/v*.rs``. This module is
the Python glue: the in-memory call every board load goes through
(``prepare_board_mapping``), the notice plumbing, the retired-key report, and
``dct migrate``'s text writer, which replays the engine's applied edits on the
file through ``yaml_patch``'s line algorithm.
"""

from __future__ import annotations

import dataclasses
import re
import warnings
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from functools import cache
from typing import TYPE_CHECKING, ParamSpec, TypeAlias, TypeVar

import yaml

from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    JsonValue,
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
    parse_dotted_version,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.authoring.yaml_patch import ScalarLeaf

# Where a key sits in one document, list positions included (ints) -- unlike a
# dotted string, which would let `charts.a.style.color` also name a chart
# called `a.style.color`.
DocumentPath: TypeAlias = tuple[str | int, ...]

_YAML_BLANK_OR_COMMENT_RE = re.compile(r"^\s*(#.*)?$")
_STAMP_LINE_COMMENT = "  # written automatically by dct migrate"
_NOTICE_SINK: ContextVar[list[MigrationNotice] | None] = ContextVar(
    "migration_notice_sink", default=None
)
_NOTICE_FILE: ContextVar[str | None] = ContextVar("migration_notice_file", default=None)


P = ParamSpec("P")
T = TypeVar("T")


def _engine_call(call: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run an engine call, raising its refusal as the coded ``ParseError``."""
    from dbt_charts import _engine
    from dbt_charts.core.compile.errors import ParseError
    from dbt_charts.core.diagnostics import ERR_INTERNAL
    from dbt_charts.core.diagnostics.codes_render import ERR_ENGINE_REFUSED

    try:
        return call(*args, **kwargs)
    except _engine.RenderDiagnosticError as refusal:
        code = {c.code: c for c in (ERR_INTERNAL, ERR_ENGINE_REFUSED)}[refusal.code]
        raise ParseError.from_code(code, **refusal.params) from refusal


@cache
def package_schemas() -> YamlSchemaCatalog:
    """The package's frozen schemas and the live one; package data, built once."""
    return load_yaml_schema_catalog()


class MigrationError(ValueError):
    """A raw YAML migration cannot be safely applied."""


class MigrationConflictError(MigrationError):
    """A move would overwrite an authored destination value."""


class UnsupportedSchemaError(MigrationError):
    """The mapping matches none of the retained grammars."""


class IncompleteMigrationError(MigrationError):
    """A recognized grammar's transitions ran but did not reach the current one.

    A sibling of ``UnsupportedSchemaError`` rather than a subclass: to a caller
    willing to fall back on the parser the two mean opposite things. An
    unsupported mapping was never old and has no skipped migration to report;
    this one demonstrably was old and is being handed on unmodernized.
    """


class SchemaMigrationWarning(UserWarning):
    """A board was migrated in memory and should be rewritten with ``dct migrate``."""


@dataclasses.dataclass(frozen=True)
class MigrationNotice:
    message: str
    file: str | None


# ContextVars, not `warnings.catch_warnings`: the latter swaps process-global
# state and races across Cloud's request threads.


@contextmanager
def collect_migration_notices() -> Generator[list[MigrationNotice]]:
    """Route migration notices raised in this context into the yielded list.

    Outside a collector a notice is a ``SchemaMigrationWarning``.
    """
    sink: list[MigrationNotice] = []
    token = _NOTICE_SINK.set(sink)
    try:
        yield sink
    finally:
        _NOTICE_SINK.reset(token)


@contextmanager
def migration_notice_file(file: str | None) -> Generator[None]:
    """Attribute notices raised in this context to *file*, the YAML being migrated."""
    token = _NOTICE_FILE.set(file)
    try:
        yield
    finally:
        _NOTICE_FILE.reset(token)


def warn_migration(message: str, *, stacklevel: int) -> None:
    sink = _NOTICE_SINK.get()
    if sink is None:
        warnings.warn(message, SchemaMigrationWarning, stacklevel=stacklevel + 1)
    else:
        sink.append(MigrationNotice(message=message, file=_NOTICE_FILE.get()))


class Dropped:
    """The edit of a move whose value rewrite dropped it: the source is struck and nothing lands."""


DROP = Dropped()


@dataclasses.dataclass(frozen=True)
class RetiredKey:
    """A key the grammar retired, as found in one document.

    ``successor`` is the concrete document path the declaration moves it to, or
    ``None`` for a removal; ``retired_in`` is the grammar version that dropped it.
    """

    path: tuple[str, ...]
    retired_in: str
    successor: tuple[str, ...] | None
    reason: str | None


def retired_keys(mapping: Mapping[str, JsonValue]) -> dict[tuple[str, ...], RetiredKey]:
    """Every key in *mapping* a declaration would fire on, by concrete document path.

    "Retired" means exactly "the migration would act here": the engine's own
    recognition gates. It reads only, so it works on files migration declined
    to touch (mixed old/new keys, a grammar past the support window).
    """
    from dbt_charts import _engine

    found = _engine_call(_engine.retired_keys, mapping, "retired_keys")
    return {
        tuple(path): RetiredKey(
            tuple(path),
            retired_in,
            None if successor is None else tuple(successor),
            reason,
        )
        for path, retired_in, successor, reason in found
    }


def retired_theme_renames() -> dict[str, str]:
    """Retired theme names and the theme each now means; they keep resolving forever."""
    from dbt_charts import _engine

    return dict(_engine.retired_theme_renames())


def prepare_board_mapping(
    mapping: Mapping[str, JsonValue], *, today: date | None = None
) -> JsonObject:
    """Recognize and migrate a raw board-shaped mapping before model validation.

    One engine call: the Rust ``migrate`` crate reads the document's shape,
    applies every declared move and deletion, and returns the migrated mapping.
    A document it finds current comes back as the same object. Migrates **in
    memory**, never touches a file. A document the engine cannot finish
    migrating comes back unchanged with a notice, for the parser to report
    against the current grammar. ``today`` is the clock the six-month
    transparent-migration window reads; it defaults to the process date.
    """
    import sys

    from dbt_charts import _engine

    frame = sys._getframe(1)  # noqa: SLF001 — names the caller in the engine's ERR-INTERNAL
    caller = f"{frame.f_globals['__name__']}.{frame.f_code.co_name}"
    clock = (
        today if today is not None else date.today()
    )  # type-state: silent_fallback — documented clock seam, the process date
    prepared, notices, _outcome = _engine_call(
        _engine.migrate_mapping, mapping, clock.isoformat(), caller
    )
    for message in notices:
        warn_migration(message, stacklevel=2)
    return prepared


def migrate_board_yaml_text(
    yaml_text: str, *, locked_schema_version: str | None = None
) -> str:
    """Rewrite one retained board grammar to the project's target grammar.

    The target is the project's ``locked_schema_version``, else the latest
    frozen (released) grammar -- never the live schema: this is the on-disk
    rewrite path (``dct migrate``), so it must never write syntax no released
    dbt charts recognizes yet. A board already newer than the target is left
    unchanged. See ``migrate_yaml_text``'s ``stop_target`` docstring.
    """
    target = (
        package_schemas().latest_released.version
        if locked_schema_version is None
        else locked_schema_version
    )
    return migrate_yaml_text(yaml_text, stop_target=target)


def migrate_yaml_text(yaml_text: str, *, stop_target: str | None = None) -> str:
    """Rewrite declared moves while preserving unrelated YAML text.

    The Rust engine plans the migration (``plan_text_migration``: recognition,
    the declared moves, deletions and map-key deletions up to ``stop_target``,
    never a ``ConditionalMove`` the writer cannot express); this writer replays
    the engine's applied edits on the text through ``yaml_patch``'s line
    algorithm. It moves scalar block-mapping leaves, relocates a mapping of
    scalars by striking it and writing its leaves at the new parent, and
    renames a key in place when the move is a pure rename (old and new path
    share the same parent). A rename never relocates content, so it carries
    no restriction on the value's shape. A relocated value holding anything
    but scalars and mappings is caught by the final check against the
    engine's result instead of reformatting a board through a YAML dump/load
    round trip.

    ``stop_target``, when given, stops the walk at that frozen version instead
    of the current one, and returns a document already recognized as newer
    than it unchanged. This is how ``dct migrate`` avoids writing syntax no
    released dbt charts recognizes yet. It also gates the ``_schema_version``
    stamp: a file is only ever stamped with a frozen, real version number, and
    only alongside a real structural change, never as the sole reason to
    rewrite an otherwise-untouched file. A written or corrected stamp lands on
    the file's first line with a trailing comment naming ``dct migrate``.

    Reports a migration notice per ``Deletion.reason`` whose tail fired.
    """
    from dbt_charts import _engine
    from dbt_charts.core.compile.authoring.yaml_patch import (
        rename_key_at_path,
        set_board_values,
    )
    from dbt_charts.core.compile.parse.parser import load_yaml_mapping

    raw = load_yaml_mapping(yaml_text)
    plan = _engine_call(
        _engine.plan_text_migration, raw, stop_target, "migrate_yaml_text"
    )
    if plan[0] == "current":
        return yaml_text
    if plan[0] != "planned":
        kind, message = plan
        raise {
            "unsupported": UnsupportedSchemaError,
            "incomplete": IncompleteMigrationError,
            "conflict": MigrationConflictError,
            "migration": MigrationError,
        }[kind](message)
    _, staged, edits, notices = plan

    updates: dict[str, ScalarLeaf | None] = {}
    removals: set[str] = set()
    for edit in edits:
        if edit["kind"] == "delete":
            yaml_text = _delete_paths_in_yaml_text(yaml_text, [tuple(edit["path"])])
            continue
        if edit["kind"] == "write":
            yaml_text = set_board_values(
                yaml_text, {".".join(edit["path"]): edit["value"]}
            )
            continue
        source, destination = edit["from"], edit["to"]
        if edit["expansion"]:
            yaml_text = set_board_values(yaml_text, {".".join(source): None})
            yaml_text = set_board_values(
                yaml_text,
                {
                    ".".join((*source, leaf)): leaf_value
                    for leaf, leaf_value in edit["after"].items()
                    if isinstance(leaf_value, (str, int, float, bool))
                },
            )
            continue
        if edit["item"] and source != destination:
            # A list-item destination only ever arises from an identity-path
            # Move's trailing wildcard -- no declared Move relocates *into* a
            # list index, only respells the value already sitting at one. A
            # genuine relocation ending inside a list has no representation
            # set_board_values can address positionally.
            raise MigrationError(
                f"Cannot rewrite {edit['declared']!r}: list-item paths require "
                "manual migration."
            )
        value = edit["before"]
        if edit["rewrite"]:
            yaml_text = _replay_rewrite(yaml_text, edit, value, source, destination)
            continue
        # A value_map changes the value itself (html_policy's bool -> string),
        # so it needs the scalar setter, not a rename.
        if source[:-1] == destination[:-1] and not edit["value_map"]:
            yaml_text = rename_key_at_path(yaml_text, ".".join(source), destination[-1])
            continue
        if isinstance(value, dict) and not edit["value_map"]:
            yaml_text = _delete_paths_in_yaml_text(yaml_text, [tuple(source)])
            updates.update(_destination_leaves(edit, destination, value))
            continue
        if value is None or not isinstance(value, (str, int, float, bool)):
            raise MigrationError(
                f"Cannot rewrite {edit['declared']!r}: only scalars, mappings of "
                "scalars, and same-position key renames can be written to the "
                "file; migrate this field manually."
            )
        if edit["value_map"]:
            value = edit["after"]
        updates[".".join(destination)] = value
        # An identity-path Move (source == destination) rewrites the key's
        # value in place -- it must not also be queued as a removal, or the
        # removals/updates dicts below would collide on the same key and the
        # merge would delete the rewritten value instead of setting it.
        if source != destination:
            removals.add(".".join(source))

    # staged != raw: only stamp a file this call actually changed something
    # in; a structurally-current file must never be rewritten for the stamp
    # alone, and a file is never touched solely to add a key the frozen
    # grammar it names does not itself declare.
    if (
        stop_target is not None
        and staged != raw
        and _should_stamp_schema_version(staged.get("_schema_version"), stop_target)
    ):
        staged["_schema_version"] = stop_target
        updates["_schema_version"] = stop_target

    final_text = set_board_values(yaml_text, {**updates, **dict.fromkeys(removals)})
    if "_schema_version" in updates:
        final_text = _mark_stamp_line_auto_written(final_text)
    # Verify the text-level rewrite produced the same result as the engine's.
    # The text editor cannot handle flow-style mappings (the regex only matches
    # block-mapping key lines) or block-scalar content (which can contain lines
    # that look like YAML keys). Either case silently no-ops or silently
    # corrupts content without the equality check.
    reparsed = load_yaml_mapping(final_text)
    if dict(reparsed) != staged:
        raise MigrationError(
            "YAML text rewrite diverged from in-memory migration; "
            "the file may use YAML constructs the text editor cannot handle "
            "(flow-style mappings, block scalars, YAML anchors, quoted keys), "
            "or a deleted key may sit where the text editor matches its name "
            "but the schema does not declare it — migrate this file manually."
        )
    # Held until here: the file is only actually written by this text once the
    # caller sees a clean return, so a reason attached to a rewrite that raised
    # above would describe damage that was never committed to disk.
    for msg in notices:
        warn_migration(msg, stacklevel=2)
    return final_text


def _replay_rewrite(
    yaml_text: str,
    edit: Mapping[str, JsonValue],
    value: JsonValue,
    source: Sequence[str],
    destination: Sequence[str],
) -> str:
    """Write what the move's rewrite made of *value* into the text as leaf edits.

    A mapping kept under its parent is renamed in place and then edited leaf
    by leaf, so comments beside untouched leaves survive. A scalar rewritten in
    place is set where it stands. Anything else is struck and its successor
    written leaf by leaf at the destination. A value the rewrite drops is only
    struck.
    """
    from dbt_charts.core.compile.authoring.yaml_patch import (
        rename_key_at_path,
        set_board_values,
    )
    from dbt_charts.core.utils import YAML_LOADER

    rewritten = DROP if edit.get("dropped") else edit["after"]
    if source == destination and isinstance(rewritten, (str, int, float, bool)):
        if rewritten == value:
            return yaml_text
        return set_board_values(yaml_text, {".".join(destination): rewritten})
    if source[:-1] != destination[:-1]:
        if not isinstance(rewritten, Dropped):
            # A `null` where the successor's mapping goes gives way to it.
            written = yaml.load(yaml_text, Loader=YAML_LOADER)
            for depth in range(1, len(destination)):
                ancestor = destination[:depth]
                if _null_at(written, ancestor):
                    yaml_text = set_board_values(yaml_text, {".".join(ancestor): None})
                    break
            yaml_text = set_board_values(
                yaml_text, dict(_destination_leaves(edit, destination, rewritten))
            )
        # A scalar leaves as a key removal, which strikes the mappings it empties
        # (the engine does the same after the successor lands); a mapping is
        # struck as it stands.
        if value is None or isinstance(value, (str, int, float, bool)):
            return set_board_values(yaml_text, {".".join(source): None})
        return _delete_paths_in_yaml_text(yaml_text, [tuple(source)])
    if isinstance(value, dict):
        if source != destination:
            yaml_text = rename_key_at_path(yaml_text, ".".join(source), destination[-1])
        old_leaves = _scalar_leaves(value)
    else:
        yaml_text = set_board_values(yaml_text, {".".join(source): None})
        old_leaves = {}
    if isinstance(rewritten, Dropped):
        return (
            _delete_paths_in_yaml_text(yaml_text, [tuple(destination)])
            if isinstance(value, dict)
            else yaml_text
        )
    if not isinstance(rewritten, dict):
        raise MigrationError(
            f"Cannot rewrite {edit['declared']!r}: only mappings can "
            "be written to the file; migrate this field manually."
        )
    new_leaves = _scalar_leaves(rewritten)
    edits: dict[str, ScalarLeaf | None] = {}
    for leaf, leaf_value in new_leaves.items():
        if leaf not in old_leaves or old_leaves[leaf] != leaf_value:
            edits[".".join((*destination, *leaf))] = leaf_value
    # A `null` leaf the rewrite struck goes too; `_scalar_leaves` skips it.
    for leaf in _leaf_paths(value if isinstance(value, dict) else {}) - _leaf_paths(
        rewritten
    ):
        edits[".".join((*destination, *leaf))] = None
    return set_board_values(yaml_text, edits) if edits else yaml_text


def _leaf_paths(
    value: Mapping[str, JsonValue], prefix: tuple[str, ...] = ()
) -> set[tuple[str, ...]]:
    paths: set[tuple[str, ...]] = set()
    for key, leaf in value.items():
        if isinstance(leaf, dict):
            paths |= _leaf_paths(leaf, (*prefix, key))
        else:
            paths.add((*prefix, key))
    return paths


def _null_at(document: JsonValue, path: Sequence[str]) -> bool:
    """Whether *path* is present in *document* and holds ``null``."""
    node = document
    for part in path:
        if isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    return node is None


def _destination_leaves(
    edit: Mapping[str, JsonValue],
    destination: Sequence[str],
    value: JsonValue | Dropped,
) -> dict[str, ScalarLeaf]:
    if isinstance(value, (str, int, float, bool)):
        return {".".join(destination): value}
    if not isinstance(value, dict):
        raise MigrationError(
            f"Cannot rewrite {edit['declared']!r}: only scalars and "
            "mappings can be written to the file; migrate this field manually."
        )
    return {
        ".".join((*destination, *leaf)): leaf_value
        for leaf, leaf_value in _scalar_leaves(value).items()
    }


def _scalar_leaves(
    value: Mapping[str, JsonValue], prefix: tuple[str, ...] = ()
) -> dict[tuple[str, ...], ScalarLeaf]:
    """The scalar leaves of nested mappings, keyed by path. Other values are
    left to the text as they stand; the caller's equality check against the
    in-memory result catches a rewrite that changed one."""
    leaves: dict[tuple[str, ...], ScalarLeaf] = {}
    for key, leaf in value.items():
        if isinstance(leaf, dict):
            leaves.update(_scalar_leaves(leaf, (*prefix, key)))
        elif isinstance(leaf, (str, int, float, bool)):
            leaves[(*prefix, key)] = leaf
    return leaves


def _should_stamp_schema_version(current: JsonValue | None, target: str) -> bool:
    """Whether ``dct migrate`` should (re)write ``_schema_version`` to *target*.

    ``False`` (no write needed) only when the existing value already equals
    *target*, or is a well-formed string (``parse_dotted_version``, shared
    with the diagnostic hint's own comparison so the two agree on what
    "malformed" means) and genuinely newer than *target* -- overwriting a
    newer value would erase the one signal
    ``yaml_error_formatter._newer_schema_version_hint`` reads. Any other
    string -- absent, older, or malformed -- gets stamped. (A non-string
    value, e.g. an unquoted YAML float, never reaches here in practice:
    ``migrate_yaml_text``'s completeness check, which runs earlier in the
    same call, already rejects a document carrying one -- no frozen or live
    schema declares ``_schema_version`` as anything but a string.)
    """
    if not isinstance(current, str) or current == target:
        return current != target
    current_parts = parse_dotted_version(current)
    target_parts = parse_dotted_version(target)
    if current_parts is None or target_parts is None:
        return True
    return current_parts <= target_parts


def _mark_stamp_line_auto_written(yaml_text: str) -> str:
    """Append an explanatory comment to the freshly (re)written ``_schema_version:`` line.

    Only called right after ``set_board_values`` wrote or corrected the stamp
    this call, so the top-level, unindented ``_schema_version:`` line is
    always present and unique -- a board never declares the field twice, and
    no other top-level key can share its name. A trailing YAML comment is
    invisible to the parser, so this never changes the value the completeness
    check already verified.
    """
    lines = yaml_text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("_schema_version:"):
            lines[i] = line + _STAMP_LINE_COMMENT
            break
    return "\n".join(lines)


def _delete_paths_in_yaml_text(yaml_text: str, paths: Sequence[DocumentPath]) -> str:
    """Remove the block-mapping key at each of *paths*, and its nested block.

    *paths* are the concrete document paths the in-memory walk actually struck
    (``_apply_deletions``' second return). Replaying them is what makes
    ``dct migrate`` and the in-memory migration one rule: this rewriter has no
    schema of its own, and every attempt to re-derive the decision from key
    names alone over-fires on a leaf that is retired at one position and live
    at another (``style.color``: dead on a board, working on eight chart
    families). It is also why there is no ancestor pruning here — the walk
    reports the emptied parent it removed as a path of its own.

    ``set_board_values`` is not the tool despite addressing the same path
    grammar: it refuses to delete a key holding a nested mapping, which is most
    of what a retired grammar leaves behind (``style.page``,
    ``conditional_formatting``).

    A path whose key does not resolve to a block-mapping line of its own is
    skipped rather than guessed at (``_yaml_key_lines``). The file is not
    written on a skip: ``migrate_yaml_text``'s equality check against
    ``staged`` sees the divergence and refuses it.
    """
    if not paths:
        return yaml_text
    lines = yaml_text.split("\n")
    key_lines = _yaml_key_lines(yaml_text)
    to_delete: set[int] = set()
    promotions: list[tuple[int, int]] = []
    for path in paths:
        located = key_lines.get(path)
        if located is None:
            continue
        index, column = located
        to_delete.add(index)
        # The key's whole nested block: every line down to the last one
        # indented past it (blank and comment lines inside that span
        # included). A scalar leaf has none, and the range stays empty.
        block_end = index
        cursor = index + 1
        while cursor < len(lines):
            line = lines[cursor]
            if not _YAML_BLANK_OR_COMMENT_RE.match(line):
                if len(line) - len(line.lstrip(" ")) <= column:
                    break
                block_end = cursor
            cursor += 1
        to_delete.update(range(index + 1, block_end + 1))
        if lines[index].lstrip(" ").startswith("- "):
            promotions.append((block_end, column))
    for block_end, column in promotions:
        _promote_list_item_dash(lines, block_end, column, to_delete)
    return "\n".join(line for i, line in enumerate(lines) if i not in to_delete)


def _promote_list_item_dash(
    lines: list[str], block_end: int, column: int, to_delete: set[int]
) -> None:
    """Move a deleted item-leading key's ``-`` onto the item's next key.

    A sequence item's first key shares its line with the dash (``- style:``),
    so striking that key takes the marker with it and orphans the rest of the
    item at an indentation that no longer parses. Rewriting the next surviving
    key at the same column to carry the dash keeps the item whole. An item that
    held nothing else has no next key to promote and the scan finds none.
    """
    for index in range(block_end + 1, len(lines)):
        line = lines[index]
        if _YAML_BLANK_OR_COMMENT_RE.match(line):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < column:
            return
        if indent == column and index not in to_delete:
            dash = column - 2
            lines[index] = " " * dash + "-" + " " * (column - dash - 1) + line[column:]
            return


def _yaml_key_lines(yaml_text: str) -> dict[DocumentPath, tuple[int, int]]:
    """Document path -> the (line, column) its block-mapping key sits at.

    Composed from the YAML node tree rather than scanned, so sequence indices
    and nesting come from the parser instead of an indentation heuristic.

    A key the rewriter cannot act on is left out, and the caller skips it: a
    flow-style key (``style: {color: x}``) shares its parent's line, so
    deleting the line would take the parent too, and a key inside a block
    scalar is prose, not structure. Both are recognized the same way —
    everything left of the key on its own line must be indentation, optionally
    with the sequence dashes that legitimately precede an item's first key.
    """
    from dbt_charts.core.compile.parse.parser import compose_yaml

    lines = yaml_text.split("\n")
    out: dict[DocumentPath, tuple[int, int]] = {}

    def walk(node: yaml.Node, prefix: DocumentPath) -> None:
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                if not isinstance(key_node, yaml.ScalarNode):
                    continue
                key = str(key_node.value)
                path = (*prefix, key)
                line = key_node.start_mark.line
                column = key_node.start_mark.column
                if line < len(lines) and _is_block_key_line(lines[line], column, key):
                    out[path] = (line, column)
                walk(value_node, path)
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                walk(item, (*prefix, index))

    walk(compose_yaml(yaml_text), ())
    return out


def _is_block_key_line(line: str, column: int, key: str) -> bool:
    """Whether *key* opens a block mapping entry at *column* on *line*."""
    return line[column:].startswith(f"{key}:") and not line[:column].strip(" -")
