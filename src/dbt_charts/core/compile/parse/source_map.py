"""YAML source map: dotted authoring path -> SourceRange.

Built once per compile from PyYAML's composed node tree (``yaml.compose()``),
which carries a ``start_mark``/``end_mark`` per node — the first real
(non-regex) source of positions in this codebase, and the first producer of
columns (every range before this shipped with ``columns=None`` because
nothing could fill one).

Compile owns the authored buffer, so the map builder lives here. The
stamping pass (``stamp_diagnostics``) is deliberately separate and takes an
already-built map as a plain argument — it does no parsing and cannot raise,
so callers above compile (e.g. the render/compile result-assembly seam in
``board.py``) can stamp render-side diagnostics against a compile-built
map without re-running any compile behavior.
"""

from __future__ import annotations

from typing import NamedTuple

import yaml

from dbt_charts.core.compile.authoring.paths import join_path, split_path
from dbt_charts.core.compile.models.chart.authored import SUPPORTED_AUTHORED_CHART_TYPES
from dbt_charts.core.diagnostics.diagnostic import (
    ColumnSpan,
    Diagnostic,
    RelatedLocation,
    SourceRange,
)
from dbt_charts.core.utils import YAML_LOADER


class LiteralBlock(NamedTuple):
    """One literal (``|``) block scalar, as it sits in the board file.

    ``indent`` is the column count YAML strips from each content line — what
    ``stamp_diagnostics`` needs to offset a SQL-local column back into a
    board-file column. ``lines`` are those same content lines already
    dedented, so they can be compared character-for-character against the SQL
    a diagnostic's position was measured against.
    """

    indent: int
    lines: tuple[str, ...]


class SourceIndex(NamedTuple):
    """Everything compile reads out of one board's composed node tree.

    ``source_map`` is the dotted-path -> SourceRange map; ``literal_blocks``
    holds the literal (``|``) block scalars; ``container_paths`` names the
    paths whose node is a mapping or sequence. All three are walks over the
    same tree, so they are built together from one ``yaml.compose()``: parsing
    a board is expensive enough that nothing here may parse it twice.
    """

    source_map: dict[str, SourceRange]
    literal_blocks: dict[str, LiteralBlock]
    container_paths: frozenset[str]


def build_source_index(yaml_content: str, file: str) -> SourceIndex:
    """Compose ``yaml_content`` once and build all three source products from it.

    Returns an empty index when ``yaml_content`` is empty or does not parse —
    positions for invalid YAML cannot exist. Diagnostics simply get
    ``range=None`` downstream, exactly like any other unresolvable path; this
    is not a fallback, it is the same "no position known" outcome by another
    door.
    """
    if not yaml_content:
        return SourceIndex({}, {}, frozenset())
    try:
        root = yaml.compose(yaml_content, Loader=YAML_LOADER)
    except yaml.YAMLError:
        return SourceIndex({}, {}, frozenset())
    if root is None:
        return SourceIndex({}, {}, frozenset())
    return build_source_index_from_node(root, yaml_content, file)


def build_source_index_from_node(
    root: yaml.Node, yaml_content: str, file: str
) -> SourceIndex:
    """The three source products off a tree a caller has already composed.

    ``yaml_content`` is the text ``root`` was composed from — the marks index
    into its lines.
    """
    lines = yaml_content.splitlines()
    source_map: dict[str, SourceRange] = {}
    _walk_node(root, [], file, lines, source_map)
    literal_blocks: dict[str, LiteralBlock] = {}
    _walk_literal_blocks(root, [], lines, literal_blocks)
    container_paths: set[str] = set()
    _walk_containers(root, [], container_paths)
    return SourceIndex(source_map, literal_blocks, frozenset(container_paths))


def _walk_node(
    node: yaml.Node,
    path_parts: list[str],
    file: str,
    lines: list[str],
    out: dict[str, SourceRange],
) -> None:
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                continue
            child_path = [*path_parts, str(key_node.value)]
            out[join_path(child_path)] = _node_range(
                file, lines, key_node.start_mark, value_node.end_mark
            )
            _walk_node(value_node, child_path, file, lines, out)
    elif isinstance(node, yaml.SequenceNode):
        for idx, item_node in enumerate(node.value):
            child_path = [*path_parts, str(idx)]
            out[join_path(child_path)] = _node_range(
                file, lines, item_node.start_mark, item_node.end_mark
            )
            _walk_node(item_node, child_path, file, lines, out)


def _block_body(lines: list[str], start_idx: int, indent: int) -> list[str]:
    """The block scalar's content lines from ``start_idx``, dedented.

    Bounded by YAML's own rule rather than the node's end mark: the scalar
    owns every line indented at least ``indent``, plus blank lines, and ends
    at the first line that dedents past it. PyYAML's ``end_mark`` sits on the
    *next* token's line, so slicing to it drags a fragment of the following
    key in ("source: db" arriving as "urce: db").
    """
    body: list[str] = []
    for line in lines[start_idx:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return body


def _walk_literal_blocks(
    node: yaml.Node,
    path_parts: list[str],
    lines: list[str],
    out: dict[str, LiteralBlock],
) -> None:
    """Collect one entry per literal (``|``) block-scalar node.

    A path lands here iff its node is a literal block scalar; folded (``>``)
    and plain/quoted scalars are never added — see ``_narrow_to_sql_token``'s
    docstring for why folding in particular can't be translated safely.
    """
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                continue
            child_path = [*path_parts, str(key_node.value)]
            if isinstance(value_node, yaml.ScalarNode) and value_node.style == "|":
                # The indicator ("sql: |") is on the key's own line, so the
                # scalar's first content line is always the very next one.
                content_line_idx = value_node.start_mark.line + 1
                if content_line_idx < len(lines):
                    first = lines[content_line_idx]
                    indent = len(first) - len(first.lstrip())
                    out[join_path(child_path)] = LiteralBlock(
                        indent=indent,
                        lines=tuple(_block_body(lines, content_line_idx, indent)),
                    )
            _walk_literal_blocks(value_node, child_path, lines, out)
    elif isinstance(node, yaml.SequenceNode):
        for idx, item_node in enumerate(node.value):
            _walk_literal_blocks(item_node, [*path_parts, str(idx)], lines, out)


def _walk_containers(node: yaml.Node, path_parts: list[str], out: set[str]) -> None:
    """Collect dotted paths whose node is a mapping or sequence, not a scalar.

    A container's range covers its whole body, so a diagnostic about the
    container itself wants the key line naming it rather than every line
    inside. Node kind is the only signal that separates that case from a block
    scalar, whose multi-line, column-less range is *also* what the source map
    produces and whose whole-block mark is correct (a SQL error should
    underline the SQL).
    """
    if isinstance(node, yaml.MappingNode):
        if path_parts:
            out.add(join_path(path_parts))
        for key_node, value_node in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                continue
            _walk_containers(value_node, [*path_parts, str(key_node.value)], out)
    elif isinstance(node, yaml.SequenceNode):
        if path_parts:
            out.add(join_path(path_parts))
        for idx, item_node in enumerate(node.value):
            _walk_containers(item_node, [*path_parts, str(idx)], out)


def _last_content_line(lines: list[str], start: yaml.Mark, end: yaml.Mark) -> int:
    """0-based index of the last line the value actually occupies.

    PyYAML's ``end_mark`` is exclusive — for a block value it lands on the
    *next* token, or past EOF. Consumers treat the end line as inclusive, so
    it has to be walked back to real content: the mark ends the value on its
    own line only when something precedes it there, otherwise the value
    stopped at the last non-blank line above.
    """
    if end.line < len(lines) and lines[end.line][: end.column].strip():
        return end.line
    idx = end.line - 1
    while idx > start.line and _is_gap(lines[idx], start.column):
        idx -= 1
    return max(idx, start.line)


def _is_gap(line: str, key_column: int) -> bool:
    """Whether a line trails the value rather than belonging to it.

    Blank lines always do. Comments only when they sit at or left of the key's
    own column: a comment above the next key is indented like that key and
    documents *it*, so absorbing it squiggles someone else's annotation. A
    ``#`` line inside a block scalar is indented past the key and is the
    scalar's own content — the end mark lands beyond it, so this walk does see
    it, and trimming it would cut the range short.
    """
    stripped = line.strip()
    if not stripped:
        return True
    return stripped.startswith("#") and (len(line) - len(line.lstrip())) <= key_column


def _node_range(
    file: str, lines: list[str], start: yaml.Mark, end: yaml.Mark
) -> SourceRange:
    """1-based SourceRange from a pair of 0-based PyYAML marks.

    Columns are populated only when start and end share a line — a value
    spanning multiple lines (a nested mapping, a block scalar) covers whole
    lines instead.
    """
    start_line = start.line + 1
    end_line = _last_content_line(lines, start, end) + 1
    # Gate on the RAW marks, not the adjusted end line. When the walk-back
    # collapses a multi-line value down onto its start line, `end.column`
    # belongs to a different physical line — pairing it with `start.column`
    # would describe a span the marks never covered, and could invert
    # (end_col < start_col), which both consumers pass through unguarded.
    columns = (
        ColumnSpan(start_col=start.column + 1, end_col=end.column + 1)
        if start.line == end.line
        else None
    )
    return SourceRange(
        file=file, start_line=start_line, end_line=end_line, columns=columns
    )


def diagnostic_path_to_source_map_key(path: str) -> str:
    """Normalize a ``Diagnostic.path`` into a source-map lookup key.

    ``Diagnostic.path`` grammar is richer than YAML's: Pydantic discriminated
    unions insert the chart-family tag ("bar", "line", …) at index 2 of
    "charts.<id>.<type>.…" paths, and that tag is never a real YAML key.
    Strip only that one segment, and only at that position, so a chart
    legitimately named e.g. "line" is never mistaken for the discriminator.
    Numeric list indices are NOT stripped — unlike the old regex-based line
    finder, the source map resolves sequence positions natively.
    """
    parts = split_path(path)
    if (
        len(parts) > 2
        and parts[0] == "charts"
        and parts[2] in SUPPORTED_AUTHORED_CHART_TYPES
    ):
        del parts[2]
    return join_path(parts)


# Shared, never-mutated default for stamp_diagnostics' literal_blocks param —
# a plain `= {}` default would be a mutable-default hazard; a named
# module-level empty dict, only ever read via `.get`, is not.
_NO_LITERAL_BLOCKS: dict[str, LiteralBlock] = {}


def stamp_diagnostics(
    diagnostics: list[Diagnostic],
    source_map: dict[str, SourceRange],
    literal_blocks: dict[str, LiteralBlock] = _NO_LITERAL_BLOCKS,
    container_paths: frozenset[str] = frozenset(),
) -> None:
    """Resolve ``.range`` in place for every diagnostic with a ``.path`` and
    no range yet.

    The one central stamping pass: a new diagnostic code needs no position
    wiring of its own — as long as its `path` is authored, this fills the
    range whenever the source map has it. Never overwrites an already-set
    range (e.g. a YAML syntax error's mark-derived range from ``parser.py``),
    and never fabricates one when the path doesn't resolve — `range` stays
    `None`.

    ``literal_blocks`` (from ``build_source_index``, keyed the same
    dotted-path way as ``source_map``) is the second half of the SQL-position
    translation: when the key that resolved ``d.range`` is present in it, and
    ``d.fields`` carries sqlglot's SQL-local position
    (``UnparseableSqlError``'s Diagnostic), the just-stamped whole-block range
    is narrowed to the offending token. Omitted (the default, an empty map)
    reproduces the un-narrowed behavior exactly — every existing caller is
    unaffected.

    ``container_paths`` (from the same index) does the same job for
    the other narrowing: a range that resolved to a mapping or sequence covers
    the node's whole body, so it collapses to the key line naming it. Omitted
    (the default, an empty set) likewise reproduces the un-collapsed behavior.
    """
    if not source_map:
        return
    for d in diagnostics:
        _stamp_one(d, source_map, literal_blocks, container_paths)
        for rel in d.related:
            _stamp_one(rel, source_map, literal_blocks, container_paths)


def _stamp_one(
    target: Diagnostic | RelatedLocation,
    source_map: dict[str, SourceRange],
    literal_blocks: dict[str, LiteralBlock],
    container_paths: frozenset[str],
) -> None:
    """Resolve one positionable thing's ``.range``, if it has none yet.

    Shared by the diagnostic itself and each of its related locations so a
    secondary mark goes through identical resolution — same candidate walk,
    same collapse, same refusal to fabricate.
    """
    if target.range is not None:
        return
    for key in _candidate_keys(target):
        if key not in source_map:
            continue
        target.range = source_map[key]
        if key in container_paths:
            _collapse_to_key_line(target)
        elif isinstance(target, Diagnostic):
            _narrow_within_block(target, literal_blocks, key)
        return


def _narrow_within_block(
    d: Diagnostic, literal_blocks: dict[str, LiteralBlock], key: str
) -> None:
    """Narrow a whole-block-scalar range to the part of it that is at fault.

    Two ways an emitter can say which part: a measured position (sqlglot's
    line/column, from a SQL parse failure) or the authored line itself
    (``source_needle`` — for a finding whose subject is one line of prose, like
    the markdown heading a board title duplicates). Both share the same refusal
    rule: narrow only on an exact textual match against the authored line, so a
    rewritten or reflowed body keeps the coarse mark rather than gaining a
    confident one in the wrong place.
    """
    _narrow_to_sql_token(d, literal_blocks, key)
    _narrow_to_needle(d, literal_blocks, key)


def _narrow_to_needle(
    d: Diagnostic, literal_blocks: dict[str, LiteralBlock], key: str
) -> None:
    """Narrow ``d.range`` to the block-scalar line equal to ``source_needle``.

    The needle is an authored line, carried verbatim by the emitter, so the
    match is equality on the dedented line — no parsing, no reconstruction from
    a parsed form (``"#" * level`` would guess wrong on ``#   Sales`` and on
    setext headings). Absent needle, no matching line, or a non-literal block
    all leave the range as it was.
    """
    needle = d.fields.get("source_needle")
    if needle is None or d.range is None or d.range.columns is not None:
        return
    block = literal_blocks.get(key)
    if block is None:
        return
    for offset, line in enumerate(block.lines, start=1):
        if line != needle:
            continue
        board_line = d.range.start_line + offset
        d.range = SourceRange(
            file=d.range.file,
            start_line=board_line,
            end_line=board_line,
            columns=ColumnSpan(
                start_col=block.indent + 1,
                end_col=block.indent + len(line) + 1,
            ),
        )
        return


def _collapse_to_key_line(target: Diagnostic | RelatedLocation) -> None:
    """Shrink a container's whole-body range to the line its key sits on.

    ``columns`` is carried through untouched rather than synthesized: the map
    sets it to ``None`` for every multi-line node (pairing a start column with
    an end column from a different line can invert), and this function has no
    access to the source lines to build one honestly. ``None`` means "the whole
    line", so the mark lands on ``  arr_breakdown:`` — the key plus its
    indentation, one line instead of thirty. A single-line container
    (``style: {}``) already carries a real span and is returned unchanged by
    the guard above.
    """
    r = target.range
    if r is None or r.end_line == r.start_line:
        return
    target.range = SourceRange(
        file=r.file,
        start_line=r.start_line,
        end_line=r.start_line,
        columns=r.columns,
    )


def _narrow_to_sql_token(
    d: Diagnostic, literal_blocks: dict[str, LiteralBlock], key: str
) -> None:
    """Narrow ``d.range`` (just stamped to a whole ``sql:``/query block) down
    to the single token sqlglot's ParseError pointed at, when it's safe to.

    Two coordinate systems have to meet: sqlglot's ``sql_line``/``sql_*_col``
    (``d.fields``) are relative to the *SQL string* it parsed; ``d.range`` is
    relative to the *board file*. ``d.range.start_line`` is the ``sql:`` key's
    own line (YAML requires the ``|`` indicator there), so the scalar's first
    content line is always ``start_line + 1``.

    The two are only the same text when nothing rewrote the SQL on its way to
    the parser — and plenty does: ``render_parameterized`` swaps every
    ``{{ variable }}`` for a bind placeholder, and the dbt path wraps a
    ``SELECT * FROM (`` prefix around line 1 to push ``limit`` down. Both
    shift columns, so the honest test is textual, not a list of known
    rewrites: narrow only when the authored line the range would land on is
    character-for-character the line sqlglot measured (``sql_line_text``).
    Anything else keeps the whole-block range — a coarse mark beats a
    confident mark on the wrong token.

    Also bails, for the same never-guess reason, when:
    - ``key not in literal_blocks``: the resolved node isn't a literal block
      scalar — folded (``>``) scalars re-flow physical lines into fewer
      logical ones, so a sqlglot line/col can't map back to one physical
      source line without reimplementing YAML's folding algorithm;
      plain/quoted scalars are absent for the same reason.
    - ``d.range`` already has columns, or no ``sql_*`` fields are present
      (e.g. ``ERR-WAREHOUSE-RUNTIME``, which has no position at all) —
      nothing to narrow.
    """
    block = literal_blocks.get(key)
    if block is None:
        return
    if d.range is None or d.range.columns is not None:
        return
    sql_line = d.fields.get("sql_line")
    start_col = d.fields.get("sql_start_col")
    end_col = d.fields.get("sql_end_col")
    line_text = d.fields.get("sql_line_text")
    if sql_line is None or start_col is None or end_col is None or line_text is None:
        return
    if not 1 <= sql_line <= len(block.lines) or block.lines[sql_line - 1] != line_text:
        return
    board_line = d.range.start_line + sql_line
    d.range = SourceRange(
        file=d.range.file,
        start_line=board_line,
        end_line=board_line,
        columns=ColumnSpan(
            start_col=block.indent + start_col, end_col=block.indent + end_col
        ),
    )


def _candidate_keys(target: Diagnostic | RelatedLocation) -> list[str]:
    """Source-map keys to try for ``target``, best position first. Empty when
    it carries no path — there is nothing to look up.

    Two things widen a single path into a list.

    **Walking up an unauthored anchor.** An emitter names the authored key its
    complaint is about (``charts.rev.style.axis_y.format``), but plenty of
    warnings are about a key the author never wrote — a missing axis format is
    exactly a missing ``format:`` line. Rather than lose the position, each
    trailing segment is dropped in turn until something resolves, so the mark
    lands on the nearest authored ancestor and, at worst, on the chart. This is
    what lets an emitter name the most specific thing it means without first
    checking whether it exists: an over-specific anchor degrades, it never
    misfires.

    **A query failure's SQL.** It names the chart it broke, so its path is the
    whole ``charts.<id>`` block — but the SQL is what's wrong and the chart
    definition it marks is usually fine. When the path says no more than "this
    chart", try the named query's body first. Deliberately narrow: a path that
    already reaches inside the chart (``charts.c1.x``) knows a line the query
    doesn't, so it stays first.

    ``path`` is never rewritten — a chart's error callout must still reveal its
    chart even when the squiggle sits on the query. The two fields answer
    different questions.
    """
    if target.path is None:
        return []
    key = diagnostic_path_to_source_map_key(target.path)
    query = target.query if isinstance(target, Diagnostic) else None
    if query is None or key.count(".") != 1 or not key.startswith("charts."):
        return _ancestors(key)
    # `query` set is what makes this a query failure. Render warnings carry
    # `path = "charts.<id>"` and no query — they are about the chart, so they
    # must never be pulled onto its `query:` node.
    #
    # An inline `query:` still names itself (`_inline_query_<chart>`), so it
    # reaches this branch; its SQL lives under the chart rather than under
    # `queries.`, hence both spellings.
    return [
        f"queries.{query}.sql",
        f"queries.{query}",
        f"{key}.query.sql",
        f"{key}.query",
        key,
    ]


def _ancestors(key: str) -> list[str]:
    """``key`` then each of its ancestors, longest first, stopping before the
    bare top-level section.

    ``charts.c1.style.axis_y.format`` -> ``[..., "charts.c1.style", "charts.c1"]``,
    never ``"charts"``. That last step is excluded because it is the one
    candidate that stops being *about the thing named*: ``charts``' range is
    every chart in the file, so a diagnostic about one chart that no longer
    exists would squiggle all of them — the exact whole-block mark this walk
    exists to remove. A single-segment key (``title``, ``text``) is its own only
    candidate: it names a top-level key directly, so resolving to itself is
    right.

    Nothing here consults the map. The caller stops at the first candidate that
    resolves, and when none does, ``range`` stays ``None`` rather than being
    fabricated.
    """
    parts = split_path(key)
    if len(parts) == 1:
        return [key]
    return [join_path(parts[: i + 1]) for i in reversed(range(1, len(parts)))]
