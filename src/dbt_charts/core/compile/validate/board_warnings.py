"""Authoring warnings derived from the compiled Board.

The counterpart to ``authoring_warnings.py``: that module reads the raw authored
YAML text (redundant keys, flat-cols shape), this one reads the normalized tree —
so it sees the post-cascade title, every chart-reference spelling resolved, and
markdown boards after their translation to YAML.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterator, Mapping

from pydantic import BaseModel

from dbt_charts.core.compile.format import resolve_format_parts
from dbt_charts.core.compile.models.board.authored import LayoutType
from dbt_charts.core.compile.models.board.normalized import Board, LayoutItem
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    LineChart,
    ScatterChart,
    _SharedChartFields,
)
from dbt_charts.core.compile.normalize.chart_focus import (
    PRE_FOCUS_PLACED_CHARTS_META_KEY,
    PRE_FOCUS_REFERENCED_QUERIES_META_KEY,
)
from dbt_charts.core.compile.normalize.query_references import (
    boards_in,
    defined_query_names,
    referenced_query_names,
)
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AUTO_AXIS_FORMAT,
    decided_axis_format,
)
from dbt_charts.core.compile.validate.formats import format_names_used
from dbt_charts.core.diagnostics import (
    WARN_ADJACENT_TEXT_ROWS,
    WARN_AXIS_ALIGN_DISCARDED,
    WARN_BODY_HEADING_NO_TITLE,
    WARN_DOUBLE_HEADER,
    WARN_FORMAT_UNUSED_NAME,
    WARN_RETIRED_IGNORE_CODE,
    WARN_SINGLE_CHART_REDUNDANT_TITLE,
    WARN_UNREFERENCED_CHART,
    WARN_UNREFERENCED_QUERY,
    Diagnostic,
    RelatedLocation,
)
from dbt_charts.core.diagnostics.codes_retired import RETIRED_WARNING_CODES
from dbt_charts.core.fonts import font_is_tabular
from dbt_charts.core.text.predefined_formats import ALL_PREDEFINED_NAMES


def detect_board_warnings(
    board: Board,
    own_format_names: frozenset[str],
    chart_styles: Mapping[
        str, BaseModel | None
    ],  # type-state: optional — None when the chart authors no style
) -> list[Diagnostic]:
    """Return non-fatal warnings read off the compiled board tree.

    ``chart_styles`` maps chart id to the style patch the chart itself authored.
    """
    warnings: list[Diagnostic] = []
    _detect_double_headers(board, warnings)
    _detect_untitled_sections(board, warnings)
    _detect_single_chart_redundant_title(board, warnings)
    _detect_adjacent_text_rows(board, warnings)
    _detect_orphan_charts(board, warnings)
    _detect_unreferenced_queries(board, warnings)
    _detect_retired_ignore_codes(board, warnings)
    _detect_axis_align_discarded(board, chart_styles, warnings)
    _detect_unused_format_names(board, own_format_names, chart_styles, warnings)
    return warnings


# ─────────────────────────────── Double header ────────────────────────────────


def _detect_double_headers(board: Board, warnings: list[Diagnostic]) -> None:
    """Warn when a board `title:` is re-stated by the heading opening its body.

    Root-only, like the single-chart check below. A nested board is a section,
    and a titled section card pairing its title with a styled body heading is a
    deliberate pattern often enough that flagging it would spend the warning's
    credibility on authored intent.
    """
    if not board.title:
        return
    heading = _body_opening_heading(board)
    if heading is None:
        return
    level, text, heading_line = heading
    # Prose reaching here from a layout slot came through a normalized nested
    # board, which no longer carries the authored path it was written at — only
    # the board's own `text:` can be marked inside.
    body_is_own_text = bool(board.text)
    # The heading line, verbatim, lets stamping narrow a `text:` block range
    # down to just it. Reconstructing it from `level` + `text` would guess wrong
    # on `#   Sales` and on setext headings.
    needle = {"source_needle": heading_line}
    # Level 1 is the document-title level — a second one under the board title is
    # a double header whatever it says. Below that, only a heading that repeats
    # the title is; `## Overview` under `title: Sales` is section structure.
    if level != 1 and _comparable(text) != _comparable(board.title):
        return
    # The heading is the line to delete, so it gets the mark; `title:` is the
    # other half of the pair and rides along as a related location. When the
    # prose lives in a layout slot rather than the board's own `text:`, the body
    # path is not recoverable from the normalized tree, so the two swap roles
    # and `title:` carries the mark — still one line, never the whole board.
    related: tuple[RelatedLocation, ...]
    if body_is_own_text:
        path = "text"
        related = (
            RelatedLocation(
                path="title",
                message=f"the board title {board.title!r} already heads the board",
            ),
        )
    else:
        path = "title"
        related = ()
    warnings.append(
        Diagnostic.from_code(
            WARN_DOUBLE_HEADER,
            path=path,
            related=related,
            fields=needle,
            message=WARN_DOUBLE_HEADER.message_template.format(
                title=board.title, level=level, heading=text
            ),
            fix=WARN_DOUBLE_HEADER.fix_template,
        )
    )


def _detect_untitled_sections(board: Board, warnings: list[Diagnostic]) -> None:
    """Warn when text opens with a heading its board has no `title:` for.

    A prose row is a section, and a section's title is the board's `title:`.
    Runs after `_detect_double_headers`, whose first-slot heading is already the
    one line that warning tells the author to delete.
    """
    rows = list(_rows_in(board))
    if not board.text and any(w.code == WARN_DOUBLE_HEADER.code for w in warnings):
        rows = rows[1:]
    sections = [
        (board, "text"),
        *((row, f"{path}.text" if path else None) for row, path in rows),
    ]
    for section, path in sections:
        heading = None if section.title else _opening_heading(section.text)
        if heading is None:
            continue
        _, text, heading_line = heading
        warnings.append(
            Diagnostic.from_code(
                WARN_BODY_HEADING_NO_TITLE,
                path=path,
                fields={"source_needle": heading_line},
                message=WARN_BODY_HEADING_NO_TITLE.message_template.format(
                    heading=text
                ),
                fix=WARN_BODY_HEADING_NO_TITLE.fix_template.format(heading=text),
            )
        )


def _rows_in(board: Board) -> Iterator[tuple[Board, str]]:
    """Yield each `rows:` item that is a board, with its path, tab panes' included.

    Stops at `cols:` and `grid:`: a cell is a card, whose opening heading titles
    it level with its neighbors.
    """
    if board.layout.type in (LayoutType.COLS, LayoutType.GRID):
        return
    for item in board.layout.items:
        if item.board is None:
            continue
        if board.layout.type == LayoutType.ROWS:
            yield item.board, item.source_path
        yield from _rows_in(item.board)


def _body_opening_heading(board: Board) -> tuple[int, str, str] | None:
    """Return `_opening_heading` of a board's body.

    The body is either the board's own ``text`` (text-only board) or, for a board
    with a layout, a bare text block in the first slot — the two ways prose lands
    directly under the board header.
    """
    # A tab's pane opens under its own tab label, not under the board header.
    first = (
        None
        if board.layout.type == LayoutType.TABS
        else next(iter(board.layout.items), None)
    )
    return _opening_heading(
        board.text or (_bare_text_block_body(first) if first is not None else None)
    )


def _opening_heading(body: str | None) -> tuple[int, str, str] | None:
    """Return (level, text, heading_line) when ``body`` opens with a heading.

    ``heading_line`` is the body's first non-blank line — the heading exactly as
    authored, whatever its spacing or style — so stamping can narrow a ``text:``
    block range down to it.
    """
    if not body:
        return None
    heading_line = next(
        (line for line in body.splitlines() if line.strip()),
        "",
    )

    # mdsvg is the parser that renders board text, so asking it what a heading is
    # keeps the warning from firing on something that renders as prose (a fenced
    # code block opening with a `#` comment). Lazy-imported to match render.
    from mdsvg import Heading, parse

    blocks = parse(body)
    first_block = next(iter(blocks), None)
    if not isinstance(first_block, Heading):
        return None
    text = "".join(span.text for span in first_block.spans)
    return first_block.level, text, heading_line


def _bare_text_block_body(item: LayoutItem) -> str | None:
    """Return a layout slot's text when the slot is prose and nothing else.

    A `{text: ...}` row normalizes to a nested board carrying only that text. One
    that also has a title or a layout of its own is a section, and its heading
    belongs to that section rather than to the parent's header.
    """
    nested = item.board
    if nested is None or nested.title or nested.layout.items:
        return None
    return nested.text or None


def _comparable(title: str) -> str:
    """Reduce a title to the letters and digits that distinguish it."""
    return "".join(ch for ch in title.lower() if ch.isalnum())


# ───────────────────────── Single-chart redundant title ───────────────────────


def _detect_single_chart_redundant_title(
    board: Board, warnings: list[Diagnostic]
) -> None:
    """Warn when a board's whole content is one chart that titles itself too.

    Root-only: nested boards are sections, and a section title above its one chart
    is legitimate framing rather than the board naming itself twice. A titled
    section anywhere in the tree also disqualifies the board — the message claims
    two headers over one chart, and a section title in between makes three.
    """
    if not board.title:
        return
    charts = board.placed_charts()
    if len(charts) != 1 or _has_body_text(board) or _has_titled_section(board):
        return
    # Only the families carrying the title/subtitle display envelope can head
    # themselves. KpiChart labels itself with `label:` and CalloutChart's title
    # is a prose lead-in — neither reads as a chart header stacked under the
    # board's, so neither is a double title.
    chart = charts[0]
    if not isinstance(chart, _SharedChartFields) or not chart.title:
        return
    warnings.append(
        Diagnostic.from_code(
            WARN_SINGLE_CHART_REDUNDANT_TITLE,
            path="title",
            chart=chart.id,
            message=WARN_SINGLE_CHART_REDUNDANT_TITLE.message_template.format(
                title=board.title, chart_title=chart.title
            ),
            fix=WARN_SINGLE_CHART_REDUNDANT_TITLE.fix_template,
        )
    )


def _has_body_text(board: Board) -> bool:
    """Whether the tree renders any prose — it separates the two titles."""
    if board.text:
        return True
    return any(
        item.board is not None and _has_body_text(item.board)
        for item in board.layout.items
    )


def _has_titled_section(board: Board) -> bool:
    """Whether any nested board heads its own slot, adding a third header."""
    return any(
        item.board is not None
        and (bool(item.board.title) or _has_titled_section(item.board))
        for item in board.layout.items
    )


# ─────────────────────────────── Adjacent text rows ───────────────────────────


# Characters a prose block needs before it *can* reach a second column, so
# before stacking it against another can misalign anything. The packer's
# `columns_earned` is `line_count // MIN_LINES_PER_COLUMN`, so five lines buy
# the first column's worth and ten buy a second; a grid span holds at least
# `MIN_CHARS` characters per line, so the shortest block that can take two
# columns is 2 x 5 x 45. Deliberately approximate: compile has no font metrics
# and no slot width, and the two constants live in render, which compile cannot
# import (the arithmetic is pinned against them from the test suite, which
# can). Its only job is to tell a passage from a caption: below this a block is
# one column wide at any board width, and two single-column blocks of the same
# measure cannot misalign. They read as ordinary consecutive paragraphs.
_MIN_FRAGMENTING_CHARS = 450


def _detect_adjacent_text_rows(board: Board, warnings: list[Diagnostic]) -> None:
    """Warn on runs of consecutive prose-only rows anywhere in the tree.

    Each prose block is measured and flowed alone — the column count comes from
    that block's line count and the balance from its own lines, with no flow
    between blocks. Stacked, they render as unrelated grids whose column edges
    do not line up, and whichever block half-fills its last column leaves a gap
    mid-page. The author wanted one continuous passage and cannot see why they
    did not get one.

    `cols:` is excluded: prose set side by side is an authored spread, not a
    fragmented flow. Grid and tab items are positioned rather than stacked.
    """
    if board.layout.type == "rows":
        run: list[LayoutItem] = []
        for item in board.layout.items:
            if _is_fragmenting_prose_slot(item, board):
                run.append(item)
                continue
            _warn_fragmented_run(run, warnings)
            run = []
        _warn_fragmented_run(run, warnings)
    for item in board.layout.items:
        if item.board is not None:
            _detect_adjacent_text_rows(item.board, warnings)


def _warn_fragmented_run(run: list[LayoutItem], warnings: list[Diagnostic]) -> None:
    """Emit one warning for a run of prose rows — a run is one decision.

    The mark sits on the second row, the first one that should have been merged
    upward, with the block it should join alongside. Firing once per adjacent
    pair would report a four-row run three times over.

    A row with no authored coordinates in this file — an import or a foreach
    expansion — is not reported at all. `source_path` is empty there, so the
    warning would carry no location while telling the author to merge two blocks
    that live in another file, or that a loop generated. Unfollowable advice with
    nowhere to point is worse than silence.
    """
    if len(run) < 2 or not run[1].source_path:
        return
    warnings.append(
        Diagnostic.from_code(
            WARN_ADJACENT_TEXT_ROWS,
            path=run[1].source_path or None,
            related=(
                RelatedLocation(
                    path=run[0].source_path,
                    message="the block this prose should join",
                ),
            )
            if run[0].source_path
            else (),
            message=WARN_ADJACENT_TEXT_ROWS.message_template.format(count=len(run)),
            fix=WARN_ADJACENT_TEXT_ROWS.fix_template,
        )
    )


def _is_fragmenting_prose_slot(item: LayoutItem, parent: Board) -> bool:
    """Whether a slot is a passage of prose big enough to fragment the page.

    A `title:` on the slot does not disqualify it — a titled prose row is the
    ordinary way to write a section, and two of them fragment exactly the way
    two untitled ones do. Merging keeps the sections: the first title heads the
    merged block and the rest become headings inside its prose.

    A block under ``_MIN_FRAGMENTING_CHARS`` is excluded, and breaks a run: it
    is a caption or a lead-in, one column wide whatever the board does, so
    stacking it misaligns nothing.

    A block holding no paragraph — a markdown table or a code block on its own —
    is excluded. It does not flow, and merging one into a prose column squeezes
    it to the measure, so recommending the merge there would be wrong advice
    rather than merely noisy.

    A slot carrying anything of its own that only a slot can carry — `style:`,
    `visible:`, a `details:` disclosure, an authored height, an `extends:` that
    changes its theme — is excluded, for one reason: a merged block has one of
    each. Merging two rows that style themselves differently has to discard a
    style; merging a conditionally visible row into an unconditional one
    publishes prose the author hid. The fix the warning names is not available
    on those rows, so it must not name it.
    """
    nested = item.board
    if nested is None or nested.layout.items or not nested.text:
        return False
    if (
        nested.authored_style is not None
        or item.visible is not None
        or item.details_variable is not None
        or item.layout_height is not None
        or nested.theme != parent.theme
    ):
        return False
    if len(nested.text) < _MIN_FRAGMENTING_CHARS:
        return False

    # Same parser that renders the text, for the same reason as the heading
    # check above. Lazy-imported to match render.
    from mdsvg import Paragraph, parse

    return any(isinstance(block, Paragraph) for block in parse(nested.text))


# ─────────────────────────────── Orphan charts ────────────────────────────────


def _detect_orphan_charts(board: Board, warnings: list[Diagnostic]) -> None:
    """Warn for charts defined somewhere in the tree but never placed.

    Warn rather than error — a content-only board still renders, and the warning
    surfaces lazy authoring. Render-side has the harder check for the specific
    case of "board has charts but layout is empty", which would produce a silently
    empty dashboard. Chart references resolve globally, so a parent-defined chart
    referenced from a nested board is not an orphan.

    A focused board is judged against its layout as written, which
    ``focus_on_chart`` records in ``board.meta``.
    """
    defined: set[str] = set()

    def walk(f: Board) -> None:
        if f.charts:
            defined.update(f.charts.keys())
        for item in f.layout.items:
            if item.board is not None:
                walk(item.board)

    walk(board)
    pre_focus_placed = board.meta.get(PRE_FOCUS_PLACED_CHARTS_META_KEY)
    referenced = (
        set(pre_focus_placed)
        if pre_focus_placed is not None
        else {chart.id for chart in board.placed_charts() if chart.id}
    )

    for chart_id in sorted(defined - referenced):
        warnings.append(
            Diagnostic.from_code(
                WARN_UNREFERENCED_CHART,
                chart=chart_id,
                message=WARN_UNREFERENCED_CHART.message_template.format(
                    chart_id=chart_id
                ),
                fix=WARN_UNREFERENCED_CHART.fix_template,
            )
        )


def _detect_unreferenced_queries(board: Board, warnings: list[Diagnostic]) -> None:
    """Warn for queries defined somewhere in the tree that nothing reads.

    A focused board is judged by the references it had before focus pruned its
    variables and title, which ``focus_on_chart`` records in ``board.meta``.
    """
    pre_focus = board.meta.get(PRE_FOCUS_REFERENCED_QUERIES_META_KEY)
    referenced = (
        set(pre_focus) if pre_focus is not None else referenced_query_names(board)
    )
    for name in sorted(defined_query_names(board) - referenced):
        warnings.append(
            Diagnostic.from_code(
                WARN_UNREFERENCED_QUERY,
                path=f"queries.{name}",
                message=WARN_UNREFERENCED_QUERY.message_template.format(query=name),
                fix=WARN_UNREFERENCED_QUERY.fix_template,
            )
        )


def _detect_retired_ignore_codes(board: Board, warnings: list[Diagnostic]) -> None:
    """Warn for each retired code a chart's `warnings_ignore` or a query's `ignore`
    names: it can no longer fire, so the entry suppresses nothing."""
    # Keyed by name: nested groups carry the parent's definitions, so the same
    # chart or query is visited once per scope.
    charts = {c.id: c for scope in boards_in(board) for c in scope.charts.values()}
    queries = {n: q for scope in boards_in(board) for n, q in scope.queries.items()}
    entries = [
        (cid, f"charts.{cid}.warnings_ignore", c.warnings_ignore)
        for cid, c in charts.items()
    ] + [(None, f"queries.{n}.ignore", q.ignore) for n, q in queries.items()]
    for chart_id, path, codes in entries:
        if codes is None:
            continue
        for code in sorted(set(codes) & RETIRED_WARNING_CODES):
            warnings.append(
                Diagnostic.from_code(
                    WARN_RETIRED_IGNORE_CODE,
                    chart=chart_id,
                    path=path,
                    message=WARN_RETIRED_IGNORE_CODE.message_template.format(
                        source=path, code=code
                    ),
                    fix=WARN_RETIRED_IGNORE_CODE.fix_template,
                )
            )


# ──────────────────────────── Axis align discarded ────────────────────────────


def _detect_axis_align_discarded(
    board: Board,
    chart_styles: Mapping[
        str, BaseModel | None
    ],  # type-state: optional — None when the chart authors no style
    warnings: list[Diagnostic],
) -> None:
    """Warn when an authored axis_y.labels.align is discarded by a house-format alias.

    House-rule format aliases force label.align = 'right' on right-edge quantitative
    axes. An authored align is silently discarded. This fires only when:
    - The chart is LineChart, AreaChart, or ScatterChart — BarChart is excluded because
      its orientation is data-dependent (horizontal vs. vertical can only be determined
      at resolve time from the x column type, not at compile time). HeatmapChart is also
      excluded: its resolve path never threads a real format into build_resolved_axis
      (see heatmap.py's build_cartesian_axes call), so the force never applies.
    - axis_y.position is explicitly "right" (auto/unset may resolve to left, where
      the force never fires and the authored align is honored).
    - axis_y.labels.align is authored.
    - axis_y.labels.format (chart-local, falling back to the theme's own
      axis_quantitative default when the chart authors no format at all) is
      an engine-predefined name.
    - The effective axis label font has tabular figures. _force_right requires
      tabular_figures=True when column_forming=True. If the chart-local font override
      is non-tabular, the force never fires and the authored align is honored.

    model_dump() is used instead of direct attribute access because the TYPE_CHECKING
    stub for patch models inherits from the non-patch base (all fields non-None), which
    makes pyright flag `ay.labels is None` as unreachable at static analysis time.
    """
    # Proxy the effective quantitative-axis label font from the board's cascade:
    # axis_quantitative.labels.font > axis_y.labels.font > axis.labels.font > root font.
    # _force_right requires tabular figures, so a non-tabular board font means no axis
    # can get the forced align — no warning is a true positive.
    ctx = board.chart_style_context
    # Theme baseline: what axis_quantitative.labels.format resolves to when a
    # chart authors no per-chart format at all (e.g. "number", itself a
    # predefined name -- see ResolvedFormat.is_house) -- a bare axis
    # inheriting this default still gets forced right-aligned, so the
    # diagnostic must check it too, not just a chart-local override.
    default_format = ctx.axis_quantitative.labels.format or ctx.axis.labels.format
    if default_format == AUTO_AXIS_FORMAT:
        default_format = decided_axis_format("quantitative")
    board_quant_font = (
        ctx.axis_quantitative.labels.font.family
        or ctx.axis_y.labels.font.family
        or ctx.axis.labels.font.family
        or ctx.font_family
    )
    board_font_tabular = font_is_tabular(board_quant_font)
    for chart_id, chart in board.charts.items():
        if not isinstance(chart, (LineChart, AreaChart, ScatterChart)):
            continue
        style = chart_styles[chart_id]
        if style is None:
            continue
        style_dict = style.model_dump()
        ay_dict = style_dict.get("axis_y")
        if not isinstance(ay_dict, dict):
            continue
        # Only fire when position is explicitly "right": the force is scoped to
        # right-edge axes. An auto/unset position may resolve to the left side.
        if ay_dict.get("position") != "right":
            continue
        labels = ay_dict.get("labels")
        if not isinstance(labels, dict) or labels.get("align") is None:
            continue
        # Check the effective font's tabular status. A chart-local axis font
        # override takes priority; otherwise fall back to the board-level check.
        font_dict = labels.get("font")
        chart_font_family = (
            font_dict.get("family") if isinstance(font_dict, dict) else None
        )
        effective_tabular = (
            font_is_tabular(chart_font_family)
            if chart_font_family is not None
            else board_font_tabular
        )
        if not effective_tabular:
            continue
        fmt = labels.get("format")
        # A FormatConfig dumps to a dict (model_dump() recurses): judge its spec.
        if isinstance(fmt, dict):
            fmt = fmt.get("spec")
        if fmt is None:
            fmt = default_format
        if fmt is None or not resolve_format_parts(fmt, ctx.formats).is_house:
            continue
        warnings.append(
            Diagnostic.from_code(
                WARN_AXIS_ALIGN_DISCARDED,
                chart=chart_id,
                field="axis_y.labels.align",
                path=f"charts.{chart_id}.style.axis_y.labels.align",
                message=WARN_AXIS_ALIGN_DISCARDED.message_template.format(
                    chart_id=chart_id,
                    authored_align=labels["align"],
                    format_alias=fmt,
                ),
                fix=WARN_AXIS_ALIGN_DISCARDED.fix_template,
            )
        )


def _engine_name_hint(name: str) -> str:
    engines = sorted(ALL_PREDEFINED_NAMES)
    related = [engine for engine in engines if engine.startswith(name)]
    related = related or difflib.get_close_matches(name, engines, n=3, cutoff=0.7)
    if not related:
        return ""
    return " Did you mean " + " or ".join(f"`{engine}`" for engine in related) + "?"


def _detect_unused_format_names(
    board: Board,
    own_format_names: frozenset[str],
    chart_styles: Mapping[
        str, BaseModel | None
    ],  # type-state: optional — None when the chart authors no style
    warnings: list[Diagnostic],
) -> None:
    """Warn on a name this board file's ``style.formats`` defines and nothing reads.

    Only names from the board's own file are judged: one inherited from
    ``meta.yml`` may be used by a sibling board this compile cannot see.
    """
    unjudged = own_format_names - ALL_PREDEFINED_NAMES
    if not unjudged:
        return
    used = format_names_used(board, chart_styles)
    for name in sorted(unjudged - used):
        warnings.append(
            Diagnostic.from_code(
                WARN_FORMAT_UNUSED_NAME,
                path=f"style.formats.{name}",
                message=WARN_FORMAT_UNUSED_NAME.message_template.format(
                    name=name, did_you_mean=_engine_name_hint(name)
                ),
                fix=WARN_FORMAT_UNUSED_NAME.fix_template,
            )
        )
