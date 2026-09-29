"""Comment-preserving YAML path setter for scalar board edits.

Round-tripping a board through `yaml.safe_load` -> `yaml.dump` silently
destroys comments and formatting on every edit. This module instead performs
surgical line-level edits: it locates (or creates) the line holding a dot-path
leaf and replaces/inserts only that line, leaving every other line
byte-identical.

Scoped small on purpose -- scalar leaves in block-style mappings are the
entire contract. Flow-style parent mappings, YAML anchors/aliases, duplicate
keys along the path, and a non-mapping value at an intermediate path all
raise `ValueError` rather than guessing or falling back to a whole-document
dump.
"""

import re
from dataclasses import dataclass

import yaml

from dbt_charts.core.utils import YAML_LOADER

# Leaf value types the setter writes; None deletes the key. A list of strings
# is a leaf here because it is written as one: `y: ["revenue", "cost"]` is a
# single line, so it splices exactly like a scalar does. Nothing else does —
# a mapping value would have to be laid out over lines this setter never
# rewrites.
ScalarLeaf = str | int | float | bool | list[str]

_BLANK_RE = re.compile(r"^\s*$")
_COMMENT_RE = re.compile(r"^\s*#")
_KEY_RE = re.compile(r"^( *)([A-Za-z0-9_.\-]+):(?:[ \t]+(\S.*?))?[ \t]*$")
# A block-style sequence item, and the same line when it also carries the item's
# first key (`- title: Monthly Revenue`). The key then sits at `indent + 2`.
_ITEM_RE = re.compile(r"^( *)- (.*)$")
_ITEM_KEY_RE = re.compile(r"^( *)- ([A-Za-z0-9_.\-]+):(?:[ \t]+(\S.*?))?[ \t]*$")
# A block-scalar header as `_KEY_RE` captures it: `|`/`>` with optional
# indentation and chomping indicators, optionally trailed by a comment. The
# body under one is a scalar spelled over lines, not a nested mapping.
_BLOCK_SCALAR_RE = re.compile(r"^[|>][0-9+-]{0,2}(?:[ \t]+#.*)?$")
_DOC_MARKER = "---"
_NEW_BLOCK_INDENT = 2
# Columns a `- ` prefix occupies; an item's keys are indented by exactly this
# much relative to the dash.
_ITEM_KEY_OFFSET = 2
# A block-sequence item that is a plain scalar -- quoted either way, or bare
# -- with an optional trailing comment. Bare excludes `{`/`[`, which read as
# flow-style structure, not a scalar this setter can rewrite in place.
_SEQUENCE_SCALAR_ITEM_RE = re.compile(
    r"^( *)- "
    r'(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\'|'
    # A `#` mid-value (`abc#def`) is a literal character, not a comment
    # marker -- YAML only starts a comment at whitespace-then-`#`. The
    # lookahead lets `bare` consume such a `#`, stopping only where
    # `trail`'s own `[ \t]+` could actually begin one.
    r"(?P<bare>[^\s#{\[](?:(?!\s+#).)*?))"
    r"(?P<trail>(?:[ \t]+(?:#.*)?)?)$"
)


@dataclass
class _KeyMatch:
    line_index: int
    indent: int
    inline_value: str | None
    # True when this key sits on its sequence item's `- ` line
    # (`- title: X`). The key is at `indent`, but the line starts two
    # columns earlier and the dash must survive any rewrite of it.
    is_item_head: bool = False


def _is_skippable(line: str) -> bool:
    """Blank and comment lines carry no key."""
    return bool(_BLANK_RE.match(line) or _COMMENT_RE.match(line))


def _block_extent(lines: list[str], start: int, end: int, indent: int) -> int:
    """Index (within [start, end)) where a block opened at `indent` closes.

    A block closes at the first line whose indentation is <= `indent` (a
    dedent), ignoring blank/comment lines along the way. If none is found,
    the block runs to `end`.
    """
    i = start
    while i < end:
        line = lines[i]
        if _BLANK_RE.match(line) or _COMMENT_RE.match(line):
            i += 1
            continue
        if len(line) - len(line.lstrip(" ")) <= indent:
            return i
        i += 1
    return end


def _child_indent(lines: list[str], start: int, end: int, parent_indent: int) -> int:
    """Indentation of an existing block's children, or a fresh 2-space step."""
    i = start
    while i < end:
        line = lines[i]
        if _BLANK_RE.match(line) or _COMMENT_RE.match(line):
            i += 1
            continue
        return len(line) - len(line.lstrip(" "))
    return parent_indent + _NEW_BLOCK_INDENT


def _has_content(lines: list[str], start: int, end: int) -> bool:
    """True if [start, end) holds any non-blank, non-comment line."""
    return any(
        not (_BLANK_RE.match(line) or _COMMENT_RE.match(line))
        for line in lines[start:end]
    )


def _insertion_tail(lines: list[str], start: int, boundary: int) -> int:
    """Position right after the last real line in [start, boundary).

    Trailing blank/comment lines (including the empty string `split("\\n")`
    leaves for a file ending in a newline) don't count as block content --
    without this, appending a new sibling at the end of a block would land
    after them, opening a stray blank line and losing the file's trailing
    newline.
    """
    for j in range(boundary - 1, start - 1, -1):
        if not (_BLANK_RE.match(lines[j]) or _COMMENT_RE.match(lines[j])):
            return j + 1
    return start


def _is_sequence_block(lines: list[str], start: int, end: int, indent: int) -> bool:
    """Whether the block at `indent` holds `- ` items rather than `key:` lines."""
    for i in range(start, end):
        if _is_skippable(lines[i]):
            continue
        match = _ITEM_RE.match(lines[i])
        return bool(match and len(match.group(1)) == indent)
    return False


def _sequence_item_span(
    lines: list[str], start: int, end: int, indent: int, index: int, path: str
) -> tuple[int, int]:
    """Line span of the `index`-th `- ` item of the sequence at `indent`.

    Raises rather than clamping: an out-of-range index means the caller is
    addressing an item that is not there, and writing to a neighbor would be a
    silent wrong edit.
    """
    starts = [
        i
        for i in range(start, end)
        if not _is_skippable(lines[i])
        and (match := _ITEM_RE.match(lines[i])) is not None
        and len(match.group(1)) == indent
    ]
    if index >= len(starts):
        raise ValueError(
            f"Cannot set {path!r}: index {index} is out of range — the sequence "
            f"has {len(starts)} item{'' if len(starts) == 1 else 's'}."
        )
    item_start = starts[index]
    if index + 1 < len(starts):
        return item_start, starts[index + 1]
    return item_start, _block_extent(lines, item_start + 1, end, indent)


def _sequence_span(
    lines: list[str], start: int, limit: int, parent_indent: int, *, immediate: bool
) -> tuple[int, int] | None:
    """`(end, item_indent)` for a block sequence that is a key's value.

    A sequence may be written at its parent key's own column — which is what
    `yaml.dump` emits, and what a hand-authored `rows:` at column 0 looks like.
    The parent's ordinary block extent stops at the first line indented back to
    the parent, so it excludes every such item; addressing one through that
    extent used to miss the sequence entirely. Returns None when the key's
    value is not a block sequence.

    `immediate` is what the two call sites disagree about, and they are right to.
    A **leaf** write replaces the whole span, so it may only claim a sequence
    whose first item is on the very next line: skipping blanks and comments to
    reach one reads an unfenced markdown board's prose body as the value of the
    empty front-matter key above it — the `- ` found there is a bullet, and the
    lines between are a heading. A **descent** only reads through the span to
    address an item inside it, and a comment or blank line between `rows:` and
    its first item is legal YAML that boards in this repo ship; refusing it
    there costs every edit on the board, including edits to other keys entirely.
    """
    for i in range(start, limit):
        if not immediate and _is_skippable(lines[i]):
            continue
        start = i
        break
    if start >= limit:
        return None
    match = _ITEM_RE.match(lines[start])
    if match is None or len(match.group(1)) < parent_indent:
        return None
    item_indent = len(match.group(1))
    end = limit
    for j in range(start, limit):
        if _is_skippable(lines[j]):
            continue
        line_indent = len(lines[j]) - len(lines[j].lstrip(" "))
        is_item = _ITEM_RE.match(lines[j]) is not None
        if line_indent > item_indent or (line_indent == item_indent and is_item):
            continue
        end = j
        break
    return end, item_indent


def _is_scalar_sequence(
    lines: list[str], start: int, end: int, item_indent: int
) -> bool:
    """Whether `[start, end)` is an unbroken run of scalar sequence items.

    A leaf write replaces everything the key owns with the single flow line
    `_format_scalar` renders, so it may only claim a span that line can stand in
    for. Two shapes are not that, and each one this predicate rejects here is one
    the splice would otherwise have deleted while reporting success:

    - an item that is **not a plain scalar** — `- type: line`, `- {type: line}`,
      `- [a, b]` — carries structure the flow line drops, so a layer stack was
      being replaced by a string and a layout tree by nothing at all;
    - an **interleaved comment**, which this function promises to preserve.

    The item text is parsed rather than pattern-matched. A key may hold a space
    and a mapping may be written flow-style, and neither spelling matches the
    `key:` pattern that used to be the whole test — so both read as scalars and
    were destroyed, where the same content in block style raised.
    """
    for i in range(start, end):
        item = _ITEM_RE.match(lines[i])
        if item is None or len(item.group(1)) != item_indent:
            return False
        try:
            parsed = yaml.load(item.group(2), Loader=YAML_LOADER)
        except yaml.YAMLError:
            return False
        if isinstance(parsed, dict | list):
            return False
    return True


def _rewrite_item_head(line: str, indent: int, text: str) -> str:
    """Rewrite an item-head line's key, keeping its `- ` prefix in place."""
    return f"{' ' * (indent - _ITEM_KEY_OFFSET)}- {text}"


def _promote_next_line_to_item_head(
    lines: list[str], removed_at: int, item_end: int, indent: int
) -> list[str]:
    """Move the `- ` onto the next key after the item head was deleted.

    Deleting `- title: X` from an item that still has keys would otherwise leave
    those keys orphaned at the item's indent with no dash to open the item.
    """
    for i in range(removed_at, item_end - 1):
        if _is_skippable(lines[i]):
            continue
        lines = list(lines)
        lines[i] = _rewrite_item_head(lines[i], indent, lines[i].lstrip(" "))
        return lines
    return lines


def _check_for_anchor_or_alias(value: str, path: str) -> None:
    if value.startswith("&"):
        raise ValueError(
            f"Cannot set {path!r}: an existing YAML anchor is in the way "
            f"({value!r}) -- anchors/aliases aren't supported by this setter."
        )
    if value.startswith("*"):
        raise ValueError(
            f"Cannot set {path!r}: an existing YAML alias is in the way "
            f"({value!r}) -- anchors/aliases aren't supported by this setter."
        )


def _find_key(
    lines: list[str], start: int, end: int, indent: int, key: str, path: str
) -> tuple[_KeyMatch | None, int]:
    """Scan [start, end) at `indent` for `key`.

    Returns `(match, tail_index)`. `tail_index` is where a new sibling key
    would be inserted if `key` isn't found: the block's dedent boundary, or
    -- only at the top level (`indent == 0`), to tolerate trailing non-YAML
    content such as a Cloud markdown board's prose body -- the first
    unrecognized line. Raises `ValueError` on a duplicate `key`, or on an
    unrecognized line inside a nested (`indent > 0`) block, since neither
    construct is safe for this setter to edit around.
    """
    match: _KeyMatch | None = None
    i = start
    while i < end:
        line = lines[i]
        if _is_skippable(line):
            i += 1
            continue

        if indent == 0 and line.strip() == _DOC_MARKER:
            if i == 0:  # opening front-matter marker
                i += 1
                continue
            # A later top-level `---` is a closing front-matter fence
            # (fenced markdown boards): it ends the recognized region.
            return match, _insertion_tail(lines, start, i)

        line_indent = len(line) - len(line.lstrip(" "))
        # An item's first key rides its `- ` line, so it is written two columns
        # to the left of its siblings while being the same depth as them.
        item_head = (
            _ITEM_KEY_RE.match(line)
            if line_indent == indent - _ITEM_KEY_OFFSET
            else None
        )
        if item_head is None and line_indent < indent:
            return match, _insertion_tail(lines, start, i)

        key_match = item_head or (
            _KEY_RE.match(line) if line_indent == indent else None
        )
        if key_match is None:
            if indent == 0:
                return match, _insertion_tail(lines, start, i)
            raise ValueError(
                f"Cannot set {path!r}: unrecognized YAML at line {i + 1} "
                f"({line!r}) -- only block-style mappings are supported."
            )

        found_key = key_match.group(2)
        inline_value = key_match.group(3)
        if found_key == key:
            if inline_value is not None:
                _check_for_anchor_or_alias(inline_value, path)
            if match is not None:
                raise ValueError(
                    f"Cannot set {path!r}: duplicate key {key!r} at line "
                    f"{i + 1} (first seen at line {match.line_index + 1})."
                )
            match = _KeyMatch(
                i, indent, inline_value, is_item_head=item_head is not None
            )
        # Step over this key's value. A sequence value may be written at the
        # key's own column, where the ordinary block extent stops immediately
        # and would leave the scan standing on the first `- ` item — a line
        # this mapping scanner cannot read.
        value_span = _sequence_span(lines, i + 1, end, indent, immediate=False)
        i = value_span[0] if value_span else _block_extent(lines, i + 1, end, indent)
    return match, _insertion_tail(lines, start, end)


def _format_scalar(value: ScalarLeaf) -> str:
    """Render `value` as a single-line YAML scalar that round-trips exactly.

    Strings are always double-quoted -- simpler and safer than judging
    whether a given string is "ambiguous" (`"1"`, `"yes"`, `"null"` all
    parse as their typed counterparts unquoted), at the cost of quoting a
    few strings that didn't strictly need it.
    """
    # Asked of the value, not of the rendering: `default_style='"'` escapes a
    # newline away on the string arm, so a bare multi-line string would render
    # single-line and pass a check on the output while an item carrying the same
    # newline inside a list still raised. One question, one answer.
    # A tuple, not a list: the two arms carry different element types, and only
    # a covariant container lets them join without an annotation mypy and
    # ruff's SIM108 cannot both be satisfied by.
    parts = tuple(value) if isinstance(value, list) else (value,)
    if any(isinstance(part, str) and "\n" in part for part in parts):
        raise ValueError(
            f"Cannot set a multi-line value ({value!r}) -- this setter only "
            "supports single-line scalar leaves."
        )
    # width=inf on both arms: PyYAML's emitter wraps at 80 columns by default,
    # and a flow sequence of column names passes 80 easily — either wrap puts
    # half the value on the line where the next key belongs.
    if isinstance(value, str):
        rendered = yaml.dump(
            value, default_flow_style=True, default_style='"', width=float("inf")
        ).rstrip("\n")
    else:
        dumped = yaml.dump(value, default_flow_style=True, width=float("inf"))
        rendered = dumped.removesuffix("...\n").rstrip("\n")
    return rendered


def _rewrite_scalar_sequence_item(line: str, value: str) -> str | None:
    """Rewrite a block-sequence item's plain scalar value in place.

    Keeps `line`'s indent, quote style (or its bareness), and trailing
    comment; only the value between them changes. Returns `None` when
    `line` isn't a single-line plain scalar item -- a nested mapping/list
    item, or a shape this narrow rewriter doesn't parse -- so the caller
    falls back to its existing refusal rather than guessing.
    """
    if _ITEM_KEY_RE.match(line) is not None:
        # `- key: value` -- a mapping item's first key riding the dash
        # line, not a scalar. The bare arm below can't tell a colon-bearing
        # key from a colon-bearing scalar value, so this is checked first.
        return None
    match = _SEQUENCE_SCALAR_ITEM_RE.match(line)
    if match is None:
        return None
    indent, trail = match.group(1), match.group("trail")
    if match.group("dq") is not None:
        new_value = f'"{value}"'
    elif match.group("sq") is not None:
        new_value = f"'{value}'"
    else:
        new_value = value
    return f"{indent}- {new_value}{trail}"


def _prune_emptied_parents(
    lines: list[str], parents: list[tuple[str, _KeyMatch]], path: str
) -> list[str]:
    """Drop each ancestor mapping the delete just left holding no keys.

    A key with nothing under it loads back as `None`, not as an absent key,
    and a field typed as a required sub-model rejects that -- so clearing the
    last leaf of a nested block would break the board it was cleared from. The
    self-check cannot catch it: it walks to `None` and reads that as deleted.
    """
    for key, parent in reversed(parents):
        body = parent.line_index + 1
        # Same question the descent asks: a sequence value may sit at its key's
        # own column, where the ordinary block extent stops before the first
        # item and reads a full board as an emptied parent.
        span = _sequence_span(lines, body, len(lines), parent.indent, immediate=False)
        extent = (
            span[0]
            if span is not None
            else _block_extent(lines, body, len(lines), parent.indent)
        )
        if _has_content(lines, body, extent):
            return lines
        if parent.is_item_head:
            item_end = _block_extent(
                lines, body, len(lines), parent.indent - _ITEM_KEY_OFFSET
            )
            if not _has_content(lines, body, item_end):
                raise ValueError(
                    f"Cannot delete {path!r}: it empties {key!r}, the only key "
                    "of its sequence item, and removing the item would renumber "
                    "every item after it."
                )
            lines = lines[: parent.line_index] + lines[parent.line_index + 1 :]
            lines = _promote_next_line_to_item_head(
                lines, parent.line_index, item_end, parent.indent
            )
            continue
        lines = lines[: parent.line_index] + lines[parent.line_index + 1 :]
    return lines


def _apply_update(lines: list[str], path: str, value: ScalarLeaf | None) -> list[str]:
    segments = path.split(".")
    if not path or any(not segment for segment in segments):
        raise ValueError(f"Invalid dot-path: {path!r}")

    parents: list[tuple[str, _KeyMatch]] = []
    start, end, indent = 0, len(lines), 0
    for depth, segment in enumerate(segments):
        is_leaf = depth == len(segments) - 1

        # A numeric segment indexes a sequence — but only where one actually
        # sits. A mapping whose key happens to be digits (`counts.0`) stays a
        # mapping lookup, so the block's own shape decides, not the spelling.
        if segment.isdigit() and _is_sequence_block(lines, start, end, indent):
            item_start, item_end = _sequence_item_span(
                lines, start, end, indent, int(segment), path
            )
            if is_leaf:
                refusal = ValueError(
                    f"Cannot set {path!r}: {segment!r} addresses a whole "
                    "sequence item, not a scalar leaf."
                )
                # A block-style scalar item's own value is this leaf -- the
                # one shape a numeric segment can end on and still rewrite
                # in place, rather than refuse. Delete and non-string values
                # keep the refusal: deleting a list item renumbers every
                # later index (the same reason a mapping-key delete of an
                # item's only key refuses above), and a non-string value
                # has no single scalar-item spelling to substitute into.
                if (
                    value is None
                    or not isinstance(value, str)
                    or _has_content(lines, item_start + 1, item_end)
                ):
                    raise refusal
                rewritten = _rewrite_scalar_sequence_item(lines[item_start], value)
                if rewritten is None:
                    raise refusal
                return lines[:item_start] + [rewritten] + lines[item_start + 1 :]
            start, end = item_start, item_end
            indent += _ITEM_KEY_OFFSET
            continue

        match, tail = _find_key(lines, start, end, indent, segment, path)

        if match is None:
            if value is None:
                return lines  # absent key on a delete path is a no-op
            if segment.isdigit():
                # No sequence was found here, so the only thing left to do
                # would be to invent a mapping key named after the index --
                # which turns a list into a mapping, or writes a second
                # structure beside a sequence the walk failed to recognize.
                # Both parse as something else entirely; refuse instead.
                raise ValueError(
                    f"Cannot set {path!r}: {segment!r} addresses a sequence "
                    "item, but no block sequence was found at that position."
                )
            insert_at = 0 if indent == 0 else tail
            if indent == 0 and lines[:1] == [_DOC_MARKER]:
                insert_at = 1
            prefix = " " * indent
            if is_leaf:
                new_line = f"{prefix}{segment}: {_format_scalar(value)}"
                return lines[:insert_at] + [new_line] + lines[insert_at:]
            new_line = f"{prefix}{segment}:"
            lines = lines[:insert_at] + [new_line] + lines[insert_at:]
            start = end = insert_at + 1
            indent += _NEW_BLOCK_INDENT
            continue

        if is_leaf:
            # A block sequence is this key's *value*, and a list is a leaf this
            # setter writes — but the extent check cannot tell those item lines
            # from a nested mapping's keys, and at the key's own column it does
            # not see them at all. Either way the whole span goes, replaced by
            # the one flow line `_format_scalar` renders.
            # A key that already carries an inline value owns nothing below it.
            sequence = (
                None
                if match.inline_value is not None
                else _sequence_span(
                    lines, match.line_index + 1, end, indent, immediate=True
                )
            )
            # Everything this key owns, which the rewrite replaces. Without a
            # sequence that is the key's line alone: the block extent runs on
            # through blank lines and comments the edit must leave where they
            # are, so only `_has_content` may read it. A sequence's span ends at
            # the next key and carries those same trailing lines, which is why
            # it is walked back to the last item.
            verb = "delete" if value is None else "set"
            refusal = ValueError(
                f"Cannot {verb} {path!r}: {segment!r} already holds a "
                "nested mapping, not a scalar."
            )
            child_end = match.line_index + 1
            if match.inline_value is not None and _BLOCK_SCALAR_RE.match(
                match.inline_value
            ):
                # A block scalar's body is the value, so the key owns it and the
                # rewrite replaces it — walked back past trailing *blank* lines
                # only, which separate this key from the next rather than
                # belonging to it. A `#`-leading line inside the body is
                # content, not a comment: walking past one would orphan it
                # into the file as a stray comment line.
                child_end = _block_extent(lines, match.line_index + 1, end, indent)
                # Walk back past trailing blanks, and past comments shallower
                # than the block's BODY indent — the first body line fixes the
                # scalar's indentation, so a `#` line below it (even one deeper
                # than the key) is a comment YAML ends the scalar at, while one
                # at or past it is content that must go with the value.
                body_indent = next(
                    (
                        len(line) - len(line.lstrip(" "))
                        for line in lines[match.line_index + 1 : child_end]
                        if not _BLANK_RE.match(line)
                    ),
                    indent + 1,
                )
                while child_end > match.line_index + 1 and (
                    _BLANK_RE.match(lines[child_end - 1])
                    or (
                        _COMMENT_RE.match(lines[child_end - 1])
                        and len(lines[child_end - 1])
                        - len(lines[child_end - 1].lstrip(" "))
                        < body_indent
                    )
                ):
                    child_end -= 1
            elif sequence is not None:
                child_end = sequence[0]
                while child_end > match.line_index + 1 and _is_skippable(
                    lines[child_end - 1]
                ):
                    child_end -= 1
                # Raised here rather than deferred to the extent check below:
                # that check cannot see a sequence at its parent key's own
                # column — every item line is indented back to the parent, so
                # the extent is empty and the delete went through, taking the
                # layout with it.
                if not _is_scalar_sequence(
                    lines, match.line_index + 1, child_end, sequence[1]
                ):
                    raise refusal
            elif _has_content(
                lines,
                match.line_index + 1,
                _block_extent(lines, match.line_index + 1, end, indent),
            ):
                raise refusal
            if value is None:
                if match.is_item_head and not _has_content(lines, child_end, end):
                    # Removing the last key would remove the item with it, and
                    # every later index shifts down. Authoring paths derive from
                    # tree position, so that silently re-points every subsequent
                    # save at a different item. This setter clears scalar leaves;
                    # deleting a list item is a different operation.
                    raise ValueError(
                        f"Cannot delete {path!r}: {segment!r} is the only key of "
                        "its sequence item, and removing the item would renumber "
                        "every item after it."
                    )
                lines = lines[: match.line_index] + lines[child_end:]
                if match.is_item_head:
                    lines = _promote_next_line_to_item_head(
                        lines, match.line_index, end, indent
                    )
                return _prune_emptied_parents(lines, parents, path)
            body = f"{segment}: {_format_scalar(value)}"
            head = (
                _rewrite_item_head(lines[match.line_index], indent, body)
                if match.is_item_head
                else f"{' ' * indent}{body}"
            )
            return lines[: match.line_index] + [head] + lines[child_end:]

        if match.inline_value:
            if match.inline_value.startswith("{"):
                raise ValueError(
                    f"Cannot set {path!r}: {segment!r} is a flow-style "
                    f"mapping ({match.inline_value!r}) -- only block-style "
                    "mappings are supported."
                )
            raise ValueError(
                f"Cannot set {path!r}: {segment!r} is not a mapping (has "
                f"scalar value {match.inline_value!r})."
            )
        parents.append((segment, match))
        start = match.line_index + 1
        # A sequence value may sit at the key's own column, outside the block
        # extent a mapping value would occupy — look for that shape first.
        span = _sequence_span(lines, start, len(lines), match.indent, immediate=False)
        if span is not None:
            end, indent = span
        else:
            end = _block_extent(lines, start, len(lines), match.indent)
            indent = _child_indent(lines, start, end, match.indent)

    return lines


def set_board_values(yaml_text: str, updates: dict[str, ScalarLeaf | None]) -> str:
    """Set or delete scalar leaves in board YAML, preserving everything else.

    This function edits only the *leading top-level mapping region* of the
    file -- the contiguous block of recognizable `key: value` lines (plus
    blank/comment lines and a `---` marker) that starts at line 0. The first
    line that cannot be parsed as a mapping key, blank, comment, or `---`
    marker ends the recognized region. Everything after that line is passed
    through byte-identical and is never inspected or modified. This lets
    Cloud markdown boards (which store freeform prose after the YAML front
    matter) be edited safely: `yaml.safe_load("title: X\\n\\nProse.")` would
    fail, but scoping the load to the front-matter region avoids it.

    The post-edit self-check re-parses the front-matter region with the
    same `YAML_LOADER`, and only that region. Malformed YAML *within* that region (e.g. a bad inline
    value) surfaces as `ValueError`, like every other unsupported input.

    Args:
        yaml_text: Original board YAML content. Must use LF line endings --
            CRLF input is rejected rather than silently mixed with the
            LF-only lines this setter emits.
        updates: Dot-path -> value. Paths address top-level keys (`"title"`),
            nested leaves (`"style.frame.width"`), and leaves inside sequence
            items, where a numeric segment indexes the sequence
            (`"rows.0.cols.1.title"`). A `None` value deletes a scalar leaf (a
            no-op if it's already absent; deleting a key that holds a nested
            mapping raises), and takes any ancestor it leaves holding no keys
            with it -- an empty `font:` loads back as `None`, which a field
            typed as a required sub-model rejects. Intermediate block mappings are
            created (2-space indent) when a nested path's parents don't yet
            exist.

    Returns:
        The edited YAML text. Every line outside the edited/inserted spans
        is byte-identical to the input. Known limitation: replacing a leaf
        rewrites its whole line, so an inline comment on that exact line
        (`width: 800  # sidebar`) is dropped -- comments on all other lines
        survive.

    Raises:
        ValueError: The path touches a flow-style mapping, a YAML anchor or
            alias, a duplicate key, a non-mapping intermediate value, a
            nested mapping on a set/delete leaf, a sequence index that is out
            of range, a delete that would empty a sequence item (removing it
            renumbers every item after it, silently re-pointing every path
            derived from tree position), a multi-line string inside a list
            value -- a bare string's break is escaped into the double-quoted
            scalar `_format_scalar` renders, so only the list arm can reach a
            real second line -- CRLF input, or malformed front-matter YAML.
            This setter never falls back to re-dumping the whole document.
            (Value *types* are
            enforced statically by the `ScalarLeaf | None` signature.)
    """
    if "\r" in yaml_text:
        raise ValueError(
            "CRLF line endings are not supported: edited lines are emitted "
            "LF-only, which would silently mix line endings."
        )
    lines = yaml_text.split("\n")
    for path, value in updates.items():
        lines = _apply_update(lines, path, value)
    result = "\n".join(lines)
    _verify_written_values(result, updates)
    return result


def _verify_written_values(result: str, updates: dict[str, ScalarLeaf | None]) -> None:
    """Self-check: reparse the edited front matter and confirm each write.

    Cloud markdown boards store prose after the YAML front matter, which
    doesn't parse as a document on its own -- so this loads only the
    recognized top-level mapping (the same block `_find_key` scans), not the
    literal whole file.
    """
    lines = result.split("\n")
    # No key can legally contain "\0", so this scans the whole top-level
    # mapping (skipping every key's nested block) without ever matching --
    # `tail` lands exactly on the boundary where recognized content ends.
    _, boundary = _find_key(lines, 0, len(lines), 0, "\0", "<self-check>")
    try:
        front_matter = (
            yaml.load("\n".join(lines[:boundary]), Loader=YAML_LOADER)
            or {}  # type-state: silent_fallback — empty front matter is a mapping with no keys
        )
    except yaml.YAMLError as e:
        raise ValueError(
            f"Front matter is not valid YAML after editing {', '.join(updates)}: {e}"
        ) from e
    for path, value in updates.items():
        node = front_matter
        missing = False
        for segment in path.split("."):
            # Mirrors the setter's own traversal: a numeric segment indexes a
            # list, and a digit-spelled mapping key parses back as an int. A
            # check that only walked string keys would report every successful
            # sequence write as a setter bug.
            if isinstance(node, list):
                if not segment.isdigit() or int(segment) >= len(node):
                    missing = True
                    break
                node = node[int(segment)]
                continue
            if not isinstance(node, dict):
                missing = True
                break
            if segment in node:
                node = node[segment]
            elif segment.isdigit() and int(segment) in node:
                node = node[int(segment)]
            else:
                missing = True
                break
        if value is None:
            if not missing:
                raise ValueError(
                    f"Setter bug: {path!r} was deleted but is still present."
                )
        elif missing:
            raise ValueError(
                f"Setter bug: {path!r} = {value!r} was requested but "
                "is missing after the edit."
            )
        elif node != value:
            raise ValueError(
                f"Setter bug: {path!r} should be {value!r}, found {node!r}."
            )


def rename_key_at_path(yaml_text: str, path: str, new_key: str) -> str:
    """Rename the final key segment at `path` to `new_key`, in place.

    Unlike `set_board_values`, this isn't restricted to scalar leaves: a
    rename edits only the key token on its own line, so a key that opens a
    nested block never needs its content relocated or reindented -- there
    is nothing for the scalar-only restriction to protect against.

    Args:
        yaml_text: Original board YAML content. LF line endings only, same
            contract as `set_board_values`.
        path: Dot-path to the key being renamed (`"axis_x.label"`). A numeric
            segment indexes a block sequence the same way `set_board_values`
            does (`"rows.0.notes"`), so a key inside a list item can be
            renamed too.
        new_key: The key's new name. Written verbatim, so it must already
            be a bare valid YAML key token (no quoting/escaping is applied).

    Returns:
        The edited YAML text. Every line other than the renamed key's own
        line is byte-identical to the input, including any trailing inline
        comment on that line.

    Raises:
        ValueError: `path` doesn't exist, a sibling already named `new_key`
            exists at that position, or CRLF input.
    """
    if "\r" in yaml_text:
        raise ValueError(
            "CRLF line endings are not supported: edited lines are emitted "
            "LF-only, which would silently mix line endings."
        )
    segments = path.split(".")
    if not path or any(not segment for segment in segments):
        raise ValueError(f"Invalid dot-path: {path!r}")

    lines = yaml_text.split("\n")
    start, end, indent = 0, len(lines), 0
    for depth, segment in enumerate(segments):
        is_leaf = depth == len(segments) - 1

        # A numeric segment indexes a sequence item on the way to the key
        # being renamed (rows.0.notes) -- same rule `_apply_update` uses: only
        # where a block sequence actually sits, so a mapping key that happens
        # to be digits stays a mapping lookup.
        if segment.isdigit() and _is_sequence_block(lines, start, end, indent):
            if is_leaf:
                raise ValueError(
                    f"Cannot rename {path!r}: {segment!r} addresses a whole "
                    "sequence item, not a key."
                )
            start, end = _sequence_item_span(
                lines, start, end, indent, int(segment), path
            )
            indent += _ITEM_KEY_OFFSET
            continue

        match, _ = _find_key(lines, start, end, indent, segment, path)
        if match is None:
            raise ValueError(f"Cannot rename {path!r}: {segment!r} not found.")
        if is_leaf:
            sibling, _ = _find_key(lines, start, end, indent, new_key, path)
            if sibling is not None:
                raise ValueError(
                    f"Cannot rename {path!r} to {new_key!r}: {new_key!r} "
                    f"already exists at line {sibling.line_index + 1}."
                )
            old_line = lines[match.line_index]
            # An item's first key rides its `- ` line (`- description: x`),
            # so it needs the item-head pattern to locate the key token past
            # the dash; every other key is a plain `key:` line.
            key_pattern = _ITEM_KEY_RE if match.is_item_head else _KEY_RE
            key_match = key_pattern.match(old_line)
            assert key_match is not None  # _find_key already matched this line
            new_line = (
                old_line[: key_match.start(2)] + new_key + old_line[key_match.end(2) :]
            )
            lines = list(lines)
            lines[match.line_index] = new_line
            return "\n".join(lines)
        start = match.line_index + 1
        # A sequence value may sit at the key's own column, outside the block
        # extent a mapping value would occupy — look for that shape first
        # (mirrors the setter path in `_apply_update` above).
        span = _sequence_span(lines, start, len(lines), match.indent, immediate=False)
        if span is not None:
            end, indent = span
        else:
            end = _block_extent(lines, start, len(lines), match.indent)
            indent = _child_indent(lines, start, end, match.indent)
    raise ValueError(f"Cannot rename {path!r}: not found.")  # pragma: no cover
