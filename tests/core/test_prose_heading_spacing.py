"""Where a prose block's first line sits inside the box reserved for it.

A dashboard row is usually prose beside charts. The prose is one markdown blob
opening with a heading; the chart carries its own title. Both should start at
the same height, and two mechanisms used to stop them:

- A heading opening a prose blob drew its own ``margin_top`` -- whitespace whose
  job is to separate it from text above it, of which there was none. mdsvg now
  collapses that margin against the top of the box it opens (the same rule
  ``BlockMetrics.leading_margin`` already let the column packer apply, which is
  why the packed multi-column path was flush and the single-column path was
  not: identical markdown, and slot width alone decided its leading space).
- A prose band then claimed no vertical inset at all, while a chart's ink sits
  ``card_padding`` inside its own card on all four sides. The band now claims
  the same inset, once, before its first element -- ``BoardContentBox.content_top``.

Still true and not this module's subject: heading margins do not scale with
heading level (``test_heading_margin_is_the_same_for_every_heading_level``).
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.theme import TextColumnStyle
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.board_resolve import build_resolved_board
from dbt_charts.core.render.boards import render_board_svg
from dbt_charts.core.render.prose import render_prose_svg
from dbt_charts.core.render.sizing import get_compact_style

from ._prose_plan import full_width_plan

# Recovering "space above the heading" means undoing the baseline's own offset
# inside its line box, which is ``half_leading + ascent`` (the CSS model
# ``mdsvg`` implements). Asking the renderer that drew it keeps this helper from
# becoming yet another hand-copy of that model -- a stale copy is what made these
# tests read ``baseline - font_size``, which held only while mdsvg assumed every
# face had an ascent of exactly one em.
# Distances recovered from the rendered `y`, which is emitted at 2dp.
_ROUNDING = 0.01

_HEADING_RE = re.compile(
    r'(?:<g transform="translate\(0, ([\-\d.]+)\)">)?\s*'
    r'<text[^>]*y="([\-\d.]+)"[^>]*font-size="(\d+)"[^>]*class="md-[0-9a-f]{8}-heading"'
)


def _heading_top(svg: str) -> float:
    """Distance from the block's own top to the top of the heading's line box."""
    from dbt_charts.core.font_measure import markdown_font_faces
    from mdsvg.renderer import SVGRenderer

    m = _HEADING_RE.search(svg)
    assert m, svg
    wrap = float(m.group(1)) if m.group(1) is not None else 0.0
    baseline, font_size = float(m.group(2)), float(m.group(3))
    style = get_compact_style(resolve_style(get_theme_style("clarity")))
    renderer = SVGRenderer(
        style=style, fonts=markdown_font_faces(style.font_family, style)
    )
    multiplier = (
        style.heading_line_height
        if style.heading_line_height is not None
        else style.line_height
    )
    offset = renderer._baseline_offset(font_size, multiplier)
    # `y` is emitted at 2dp, so a recovered distance is good to ~0.01px. The old
    # `baseline - font_size` form hid that: both terms were whole pixels.
    return wrap + baseline - offset


def _prose_column_svg(svg: str) -> str:
    """Slice a full board render down to just its prose column's own content.

    A board's own ``title:`` is *also* an ``md-heading`` and renders before any
    row content, so searching the whole board SVG for the heading class finds
    the board title, not the prose column's heading. Bound the search to
    between the prose block's own marker and the next chart's.
    """
    start = svg.index('data-authored-kind="text"')
    end = svg.index('data-authored-kind="chart"', start)
    return svg[start:end]


def _render(markdown: str, width: float, n_cols: int | None) -> str:
    resolved = resolve_style(get_theme_style("clarity"))
    col = (
        TextColumnStyle(max_number=n_cols) if n_cols is not None else TextColumnStyle()
    )
    text_style = resolved.text.model_copy(update={"column": col})
    svg, _ = render_prose_svg(
        markdown, width, text_style, resolved, plan=full_width_plan(resolved, width)
    )
    return svg


ISOLATED = "## Heading text\n\nBody paragraph text that follows the heading."
ONE_PARAGRAPH = "First paragraph of body text that runs on for a bit."
MID_BLOB = f"{ONE_PARAGRAPH}\n\n## Heading text\n\nSecond paragraph after the heading."

# A ~550px slot is what a prose column gets beside a chart in a two-up row on a
# 1128-unit board, and it is narrow enough that mdsvg's column planner picks a
# single column outright -- the path that used to keep the full margin. 1128px
# is wide enough that the planner's initial pick is more than one column, which
# takes the packed path. The two must now agree.
_NARROW_SLOT = 550.0
_WIDE_SLOT = 1128.0


def test_a_heading_opening_a_prose_blob_gets_no_leading_space() -> None:
    """Its margin separates it from text above it, and there is none."""
    assert _heading_top(_render(ISOLATED, _NARROW_SLOT, n_cols=None)) == pytest.approx(
        0.0, abs=_ROUNDING
    )


def test_slot_width_does_not_change_a_headings_leading_space() -> None:
    """The same markdown, narrow slot and wide, opens at the same height.

    Which of the two internal paths a blob takes is decided by slot width, and
    an author neither asks for it nor can see it. It must not be visible in the
    output either.

    Sub-pixel rather than exact: the packed path positions each block with
    ``px()``, which snaps to whole pixels so a structural mark stays crisp, and
    the margin it lifts off a column-opening block is not a whole number.
    """
    narrow = _heading_top(_render(ISOLATED, _NARROW_SLOT, n_cols=None))
    wide = _heading_top(_render(ISOLATED, _WIDE_SLOT, n_cols=None))

    assert narrow == pytest.approx(wide, abs=0.5)


def test_a_mid_blob_heading_keeps_its_full_margin() -> None:
    """Only the opener collapses: the rhythm between blocks is untouched.

    ``paragraph_height`` is the paragraph's full rendered footprint (it has no
    margin of its own), so a mid-blob heading's leading space is that plus
    exactly the heading's own margin_top -- no more (which would be double
    counting) and no less (which would flatten the rhythm).
    """
    resolved = resolve_style(get_theme_style("clarity"))
    style = get_compact_style(resolved)
    text_style = resolved.text.model_copy(
        update={"column": TextColumnStyle(max_number=1)}
    )
    _, paragraph_height = render_prose_svg(
        ONE_PARAGRAPH,
        _WIDE_SLOT,
        text_style,
        resolved,
        plan=full_width_plan(resolved, _WIDE_SLOT),
    )

    mid_blob_top = _heading_top(_render(MID_BLOB, _WIDE_SLOT, n_cols=1))

    assert mid_blob_top == pytest.approx(
        paragraph_height + style.heading_margin_top_px, abs=_ROUNDING
    )


def test_heading_margin_is_the_same_for_every_heading_level() -> None:
    """h1 and h3 get the same leading space -- margins don't scale with level.

    ``compact_style_kwargs`` derives ``heading_margin_top_px`` from the body
    font's line box, not from ``get_heading_size(level)``, and mdsvg's
    px-override path always prefers it over the level-scaled em fallback. A
    24px h1 and a 14px h3 (the same size as body text) should visually earn
    different leading space; today they don't. Characterization, unchanged by
    this fix -- an opening heading now collapses whichever margin it was given,
    so the two are equal at zero as well as mid-blob.
    """
    h1_top = _heading_top(_render(f"{ONE_PARAGRAPH}\n\n# H1 heading", _WIDE_SLOT, 1))
    h3_top = _heading_top(_render(f"{ONE_PARAGRAPH}\n\n### H3 heading", _WIDE_SLOT, 1))

    assert h1_top == pytest.approx(h3_top, abs=_ROUNDING)


def _render_board(yaml_text: str) -> tuple[str, float]:
    """Returns (svg, card_padding) -- the padding a chart's title band sits inside."""
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_board(yaml_text)
        assert result.success, result.errors
        assert result.board is not None
        executor = Executor(
            result.board,
            build_adapter_registry(FilesystemProject(Path(tmp))),
            query_registry=result.query_registry,
        )
        variables: dict[str, object] = {}
        resolved, render_cache = build_resolved_board(result.board, executor, variables)
        background = resolved.style.background
        svg = render_board_svg(
            resolved,
            executor,
            variables,
            background=None if background == "transparent" else background,
            render_cache=render_cache,
        )
        assert resolved.card_padding is not None
        return svg, resolved.card_padding


_CHART_TITLE_RE = re.compile(
    r'class="mark-text role-title-text"[^>]*>'
    r'<text[^>]*transform="translate\(([\d.]+),([\d.]+)\)"[^>]*font-size="(\d+)px"'
)

# The prose band's own inset, on the group that wraps the text block. The block
# itself renders at its own origin, so its ink's distance from the column top is
# this translate plus whatever the block puts above its first line.
_TEXT_BLOCK_RE = re.compile(
    r'<g transform="translate\(([\d.]+), ([\d.]+)\)">\s*'
    r'<g transform="translate\(0, 0\)" data-authored-kind="text"'
)

_BOARD_YAML = """
title: Chart title alignment probe
queries:
  q:
    columns: [month, revenue]
    values:
      - ["2026-01-01", 100]
      - ["2026-02-01", 150]
      - ["2026-03-01", 120]
charts:
  bar1:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue by month
rows:
  - cols:
      - text: "## Key takeaways\\n\\nNorth region is leading this quarter."
      - bar1
"""


def test_a_prose_heading_starts_level_with_a_sibling_chart_title() -> None:
    """The alignment target, through the real board pipeline.

    A chart column shares a row with a prose column, both the same width. The
    two cells start at the same y, so what has to agree is the distance from
    each cell's top to its first ink. A chart's is ``card_padding``: Vega's
    autosize insets the whole plot by that plus the title band's own reserved
    height, and the title role-group's transform is that reserved height
    negated, so the two cancel and the band's top is ``card_padding``. The
    prose column now claims the same inset.
    """
    svg, card_padding = _render_board(_BOARD_YAML)

    block = _TEXT_BLOCK_RE.search(svg)
    assert block, svg
    prose_ink_top = float(block.group(2)) + _heading_top(_prose_column_svg(svg))

    assert prose_ink_top == pytest.approx(card_padding, abs=_ROUNDING)

    # The chart side of the same statement, read off the render rather than
    # asserted from the algebra above: its title's glyphs land within one line
    # box of the heading's, which two blocks 25px apart could not.
    m = _CHART_TITLE_RE.search(svg)
    assert m, svg
    chart_title_glyph_top = card_padding + float(m.group(2)) - float(m.group(3))
    style = get_compact_style(resolve_style(get_theme_style("clarity")))
    line_box = style.base_font_size * style.line_height
    assert abs(chart_title_glyph_top - prose_ink_top) < line_box
