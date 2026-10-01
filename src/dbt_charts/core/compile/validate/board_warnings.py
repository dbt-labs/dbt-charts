"""Authoring warnings derived from the compiled Board.

The counterpart to ``authoring_warnings.py``: that module reads the raw authored
YAML text (redundant keys, flat-cols shape), this one reads the normalized tree —
so it sees the post-cascade title, every chart-reference spelling resolved, and
markdown boards after their translation to YAML.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.board.normalized import Board, LayoutItem
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    LineChart,
    ScatterChart,
    _SharedChartFields,
)
from dbt_charts.core.compile.normalize.chart_focus import (
    PRE_FOCUS_PLACED_CHARTS_META_KEY,
)
from dbt_charts.core.diagnostics import (
    WARN_AXIS_ALIGN_DISCARDED,
    WARN_DOUBLE_HEADER,
    WARN_H1_BODY_NO_TITLE,
    WARN_SINGLE_CHART_REDUNDANT_TITLE,
    WARN_UNREFERENCED_CHART,
    Diagnostic,
    RelatedLocation,
)
from dbt_charts.core.fonts import font_is_tabular
from dbt_charts.core.text.predefined_formats import PREDEFINED_NUMBER_NAMES


def detect_board_warnings(board: Board) -> list[Diagnostic]:
    """Return non-fatal warnings read off the compiled board tree."""
    warnings: list[Diagnostic] = []
    _detect_double_headers(board, warnings)
    _detect_single_chart_redundant_title(board, warnings)
    _detect_orphan_charts(board, warnings)
    _detect_axis_align_discarded(board, warnings)
    return warnings


# ─────────────────────────────── Double header ────────────────────────────────


def _detect_double_headers(board: Board, warnings: list[Diagnostic]) -> None:
    """Warn when the board's body opens with a heading that should be its title.

    Two shapes, split on whether `title:` is set: a title re-stated by a body
    heading (WARN-DOUBLE-HEADER), or no title at all with a body heading that
    should have been one (WARN-H1-BODY-NO-TITLE). Root-only, like the
    single-chart check below. A nested board is a section, and a section card
    that opens its body with a heading — titled or not — is a deliberate
    pattern often enough (a styled hero inside a card) that flagging it would
    spend the warning's credibility on authored intent.
    """
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
    if not board.title:
        # No title to compare a lower-level heading against — only a literal
        # level 1 stands in for a missing document title.
        if level != 1:
            return
        warnings.append(
            Diagnostic.from_code(
                WARN_H1_BODY_NO_TITLE,
                path="text",
                fields=needle,
                message=WARN_H1_BODY_NO_TITLE.message_template.format(heading=text),
                fix=WARN_H1_BODY_NO_TITLE.fix_template.format(heading=text),
            )
        )
        return
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


def _body_opening_heading(board: Board) -> tuple[int, str, str] | None:
    """Return (level, text, heading_line) for a body that opens with a markdown
    heading.

    The body is either the board's own ``text`` (text-only board) or, for a board
    with a layout, a bare text block in the first slot — the two ways prose lands
    directly under the board header.

    ``heading_line`` is the body's first non-blank line — the heading exactly as
    authored, whatever its spacing or style — so stamping can narrow a ``text:``
    block range down to it.
    """
    first = next(iter(board.layout.items), None)
    own_text = board.text
    body = own_text or (_bare_text_block_body(first) if first is not None else None)
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
            path=f"charts.{chart.id}.title",
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


# ──────────────────────────── Axis align discarded ────────────────────────────


def _detect_axis_align_discarded(board: Board, warnings: list[Diagnostic]) -> None:
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
    # predefined name -- see ResolvedAxisStyle.format_raw) -- a bare axis
    # inheriting this default still gets forced right-aligned, so the
    # diagnostic must check it too, not just a chart-local override.
    default_format = ctx.axis_quantitative.labels.format
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
        style = chart.style
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
        if fmt is None:
            fmt = default_format
        if fmt is None or fmt not in PREDEFINED_NUMBER_NAMES:
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
