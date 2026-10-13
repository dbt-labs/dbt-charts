"""End-to-end: table subtitle chrome matches the shared chart-title chrome.

Every chart family except table draws its title/subtitle through Vega-Lite's own
title component, styled from ``style.title`` (``ResolvedChartsStyle.title``).
Table draws its title block by hand in the table engine and, until this
fix, read a second, table-only copy of the subtitle font size/color instead of
the same ``style.title.subtitle`` the chart families use.

These tests run the full ``compile()`` → ``render()`` pipeline and assert on the
emitted SVG — a hand-built ``ResolvedStyle`` patch would not exercise the
Vega-Lite title layout the table has to match. ``test_subtitle_spec_bundle.py``
covers the narrower cascade-plumbing contract (does an override reach the SVG at
all); these cover the actual cross-family agreement.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

SVG_NS = "{http://www.w3.org/2000/svg}"
_TRANSLATE_Y = re.compile(r"translate\([^,]+,\s*([-\d.]+)\)")

_VALUES_QUERY = """
    query:
      type: values
      rows:
        - {month: Jan, revenue: 6000}
        - {month: Feb, revenue: 15000}
        - {month: Mar, revenue: 22000}
"""


def _render_svg(yaml_body: str, local_project: Callable[..., FilesystemProject]) -> str:
    result = compile_board(yaml_body)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format="svg").output
    assert isinstance(output, str)
    return output


def _chart_subtree(svg: str, chart_id: str) -> ET.Element:
    """The ``<g class="dbt-chart" data-chart-id="...">`` subtree for one chart."""
    root = ET.fromstring(svg)
    for el in root.iter():
        if el.get("data-chart-id") == chart_id:
            return el
    raise AssertionError(f"no chart with id {chart_id!r} in rendered SVG")


def _translate_y(transform: str | None) -> float:
    match = _TRANSLATE_Y.search(transform or "")
    assert match, f"no translate() y in {transform!r}"
    return float(match.group(1))


def _vl_title_groups(
    chart_el: ET.Element,
) -> tuple[ET.Element | None, ET.Element | None]:
    """The raw ``role-title-text`` / ``role-title-subtitle`` <text> elements,
    or None for whichever is absent (a title-only chart emits no subtitle
    group at all)."""
    title_text = subtitle_text = None
    for el in chart_el.iter(f"{SVG_NS}g"):
        classes = (el.get("class") or "").split()
        if "role-title-text" in classes:
            title_text = el.find(f"{SVG_NS}text")
        elif "role-title-subtitle" in classes:
            subtitle_text = el.find(f"{SVG_NS}text")
    return title_text, subtitle_text


def _vl_title_only(chart_el: ET.Element) -> tuple[float, float]:
    """(title_font_size, title_y) for a Vega-Lite-rendered chart's title,
    independent of whether it also carries a subtitle."""
    title_text, _ = _vl_title_groups(chart_el)
    assert title_text is not None, "chart has no role-title-text"
    return (
        float((title_text.get("font-size") or "0").removesuffix("px")),
        _translate_y(title_text.get("transform")),
    )


def _vl_title_subtitle(
    chart_el: ET.Element,
) -> tuple[float, float, float, float, str]:
    """(title_font_size, title_y, subtitle_font_size, subtitle_y, subtitle_fill)
    for a Vega-Lite-rendered chart, read off the emitted
    ``role-title-text`` / ``role-title-subtitle`` groups."""
    title_text, subtitle_text = _vl_title_groups(chart_el)
    assert title_text is not None, "chart has no role-title-text"
    assert subtitle_text is not None, "chart has no role-title-subtitle"
    return (
        float((title_text.get("font-size") or "0").removesuffix("px")),
        _translate_y(title_text.get("transform")),
        float((subtitle_text.get("font-size") or "0").removesuffix("px")),
        _translate_y(subtitle_text.get("transform")),
        subtitle_text.get("fill", ""),
    )


def _table_title_subtitle(
    chart_el: ET.Element,
) -> tuple[float, float, float, float, str]:
    """Same tuple shape as ``_vl_title_subtitle``, read off the table's hand-drawn
    ``<text>``/``<tspan>`` pair. Table emits title then subtitle, both before any
    header/row text, so they are always the first two ``<text>`` elements."""
    texts = list(chart_el.iter(f"{SVG_NS}text"))
    title_text, subtitle_text = texts[0], texts[1]
    title_tspan = title_text.find(f"{SVG_NS}tspan")
    subtitle_tspan = subtitle_text.find(f"{SVG_NS}tspan")
    assert title_tspan is not None
    assert subtitle_tspan is not None
    return (
        float(title_text.get("font-size", "0")),
        float(title_tspan.get("y", title_text.get("y", "0"))),
        float(subtitle_text.get("font-size", "0")),
        float(subtitle_tspan.get("y", "0")),
        subtitle_text.get("fill", ""),
    )


def _table_title_only(chart_el: ET.Element) -> float:
    """Title baseline y when a table has no subtitle (single <text>)."""
    texts = list(chart_el.iter(f"{SVG_NS}text"))
    title_tspan = texts[0].find(f"{SVG_NS}tspan")
    assert title_tspan is not None
    return float(title_tspan.get("y", texts[0].get("y", "0")))


_THREE_COL_ROW = """
title: Tile chrome probe
extends: {theme}
style:
  frame:
    width: {width}
rows:
  - cols: [e_line, e_bar, e_table]
charts:
  e_line:
    type: line
    title: "Line tile"
    subtitle: "Subtitle sits here"
    x: month
    y: revenue
{query}
  e_bar:
    type: bar
    title: "Bar tile"
    subtitle: "Subtitle sits here"
    x: month
    y: revenue
{query}
  e_table:
    type: table
    title: "Table tile"
    subtitle: "Subtitle sits here"
{query}
"""

# Object-title font size is width-tiered (typography.py: tiny/narrow -> 11,
# medium -> 14, wide -> 18 for a level-2 object title). These board widths
# each land a 3-equal-column row of line/bar/table on one of the three
# reachable sizes — confirmed by the title_size equality assertion below,
# not assumed. Covering all three is the point: a single calibrated
# title_subtitle_gap constant could only ever match Vega-Lite's spacing at
# the one tier it was fitted against (see the task's Review Feedback).
_BOARD_WIDTH_BY_TITLE_TIER = {"tiny_11px": 900, "medium_14px": 1400, "wide_18px": 1900}


@pytest.mark.parametrize("theme", ["clarity", "stark"])
@pytest.mark.parametrize("tier", list(_BOARD_WIDTH_BY_TITLE_TIER))
def test_table_subtitle_chrome_matches_chart_family_chrome(
    tier: str, theme: str, local_project: Callable[..., FilesystemProject]
):
    """A table tile and its line/bar siblings, same row, same authored title +
    subtitle: subtitle font size, subtitle fill, and the title->subtitle
    baseline gap must all agree — serif (clarity) and sans (stark), at each
    of the three title-size tiers a chart/table object title can resolve to."""
    svg = _render_svg(
        _THREE_COL_ROW.format(
            theme=theme, width=_BOARD_WIDTH_BY_TITLE_TIER[tier], query=_VALUES_QUERY
        ),
        local_project,
    )
    line_title_size, line_title_y, line_sub_size, line_sub_y, line_sub_fill = (
        _vl_title_subtitle(_chart_subtree(svg, "e_line"))
    )
    bar_title_size, bar_title_y, bar_sub_size, bar_sub_y, bar_sub_fill = (
        _vl_title_subtitle(_chart_subtree(svg, "e_bar"))
    )
    tbl_title_size, tbl_title_y, tbl_sub_size, tbl_sub_y, tbl_sub_fill = (
        _table_title_subtitle(_chart_subtree(svg, "e_table"))
    )

    # Sanity: line and bar land on the same width-tier title size as each other,
    # and (per the "matches" row in the task's Problem table) as the table too —
    # otherwise the gap comparison below would be comparing different tiers.
    assert line_title_size == bar_title_size == tbl_title_size

    line_gap = line_sub_y - line_title_y
    bar_gap = bar_sub_y - bar_title_y
    tbl_gap = tbl_sub_y - tbl_title_y

    assert tbl_title_y == line_title_y == bar_title_y, (
        f"table title baseline {tbl_title_y} != chart family "
        f"{line_title_y}/{bar_title_y} at title size {tbl_title_size}"
    )
    assert tbl_sub_size == line_sub_size == bar_sub_size, (
        f"table subtitle font-size {tbl_sub_size} != chart family "
        f"{line_sub_size}/{bar_sub_size}"
    )
    assert tbl_sub_fill == line_sub_fill == bar_sub_fill, (
        f"table subtitle fill {tbl_sub_fill!r} != chart family "
        f"{line_sub_fill!r}/{bar_sub_fill!r}"
    )
    assert tbl_gap == line_gap == bar_gap, (
        f"table title->subtitle gap {tbl_gap} != chart family "
        f"{line_gap}/{bar_gap} at title size {tbl_title_size}"
    )


_SOLO_ROW = """
title: Tile chrome probe (solo)
extends: clarity
style:
  frame:
    width: 700
rows:
  - cols: [{chart_id}]
charts:
  {chart_id}:
    type: {chart_type}
    title: "{chart_id} tile"
    subtitle: "Subtitle sits here"
{extra}
{query}
"""


def test_table_alone_in_row_matches_chart_family_chrome(
    local_project: Callable[..., FilesystemProject],
):
    """The same chrome holds when the table is the only tile in its row — not
    an artifact of sitting next to a Vega-Lite sibling. Both tiles render at
    the same board width (700px, landing both on the "medium/wide" title-size
    tier) so the comparison isn't confounded by a different width tier picking
    a different title font size for one side."""
    line_svg = _render_svg(
        _SOLO_ROW.format(
            chart_id="e_line",
            chart_type="line",
            extra="    x: month\n    y: revenue",
            query=_VALUES_QUERY,
        ),
        local_project,
    )
    table_svg = _render_svg(
        _SOLO_ROW.format(
            chart_id="e_table", chart_type="table", extra="", query=_VALUES_QUERY
        ),
        local_project,
    )
    title_size, line_title_y, line_sub_size, line_sub_y, line_sub_fill = (
        _vl_title_subtitle(_chart_subtree(line_svg, "e_line"))
    )
    tbl_title_size, tbl_title_y, tbl_sub_size, tbl_sub_y, tbl_sub_fill = (
        _table_title_subtitle(_chart_subtree(table_svg, "e_table"))
    )
    assert title_size == tbl_title_size, "solo renders landed on different width tiers"
    assert tbl_sub_size == line_sub_size
    assert tbl_sub_fill == line_sub_fill
    assert (tbl_sub_y - tbl_title_y) == (line_sub_y - line_title_y)


@pytest.mark.parametrize("tier", list(_BOARD_WIDTH_BY_TITLE_TIER))
def test_table_without_subtitle_title_baseline_unchanged(
    tier: str, local_project: Callable[..., FilesystemProject]
):
    """A title-only table's title baseline must not move because of this fix,
    and must still agree with its chart-family sibling's title baseline — the
    subtitle-chrome divergence has no bearing on a table with no subtitle, at
    any of the three title-size tiers."""
    width = _BOARD_WIDTH_BY_TITLE_TIER[tier]
    title_only = f"""
title: Table title only
extends: clarity
style:
  frame:
    width: {width}
rows:
  - cols: [e_line, e_table]
charts:
  e_line:
    type: line
    title: "Line tile"
    x: month
    y: revenue
{_VALUES_QUERY}
  e_table:
    type: table
    title: "Table tile"
{_VALUES_QUERY}
"""
    with_subtitle = f"""
title: Table title and subtitle
extends: clarity
style:
  frame:
    width: {width}
rows:
  - cols: [e_line, e_table]
charts:
  e_line:
    type: line
    title: "Line tile"
    x: month
    y: revenue
{_VALUES_QUERY}
  e_table:
    type: table
    title: "Table tile"
    subtitle: "Subtitle sits here"
{_VALUES_QUERY}
"""
    title_only_svg = _render_svg(title_only, local_project)
    title_only_y = _table_title_only(_chart_subtree(title_only_svg, "e_table"))
    # Line has no subtitle authored either way, so its own title-only baseline
    # is the independent reference — a regression that moved both the table's
    # formula and its "no subtitle" behavior together would still be caught
    # here, unlike comparing the table only against itself.
    _, line_title_y = _vl_title_only(_chart_subtree(title_only_svg, "e_line"))
    assert title_only_y == line_title_y

    with_subtitle_svg = _render_svg(with_subtitle, local_project)
    _, with_subtitle_title_y, _, _, _ = _table_title_subtitle(
        _chart_subtree(with_subtitle_svg, "e_table")
    )
    assert title_only_y == with_subtitle_title_y


def test_table_subtitle_wraps_without_disturbing_first_line_gap(
    local_project: Callable[..., FilesystemProject],
):
    """A subtitle long enough to wrap (theme floor is wrap-two) still opens its
    first line at the same title->subtitle gap as a short, unwrapped one."""
    long_subtitle = (
        "This subtitle is deliberately long enough that it must wrap onto a "
        "second line even at this wider card width, which keeps both tiles on "
        "the same title-size tier as the rest of this test module"
    )
    narrow_board = f"""
title: Table subtitle wrap probe
extends: clarity
style:
  frame:
    width: 1500
rows:
  - cols: [e_line, e_table]
charts:
  e_line:
    type: line
    title: "Line tile"
    subtitle: "Subtitle sits here"
    x: month
    y: revenue
{_VALUES_QUERY}
  e_table:
    type: table
    title: "Table tile"
    subtitle: "{long_subtitle}"
{_VALUES_QUERY}
"""
    svg = _render_svg(narrow_board, local_project)
    table_el = _chart_subtree(svg, "e_table")
    texts = list(table_el.iter(f"{SVG_NS}text"))
    subtitle_tspans = texts[1].findall(f"{SVG_NS}tspan")
    assert len(subtitle_tspans) >= 2, "subtitle did not wrap at this width"

    _, line_title_y, line_sub_size, line_sub_y, _ = _vl_title_subtitle(
        _chart_subtree(svg, "e_line")
    )
    _, tbl_title_y, tbl_sub_size, tbl_sub_y, _ = _table_title_subtitle(table_el)
    assert tbl_sub_size == line_sub_size
    assert (tbl_sub_y - tbl_title_y) == (line_sub_y - line_title_y)


# Every test above varies the BOARD width against a fixed layout, so each one
# renders a board whose tiles are all equal width. Title size is tiered off the
# *tile's* width, so any sizing that happens to agree with the tile whenever
# tiles are uniform stays green across all of them — in particular deriving a
# tile width as board / columns and ignoring the item's span. (A regression
# reading the raw board width is already caught: the tier-equality assertion in
# the three-column test trips at most of those board widths.)
#
# `grid:` spans give differently-sized tiles in a single board, which separates
# the two. The spans below span more than one title tier — asserted at the end
# of the test, not assumed, so a future change to the tier thresholds cannot
# silently collapse this to a single-tier fixture that tests nothing.
_MIXED_WIDTH_GRID = """
title: Tile chrome probe (mixed tile widths, one board)
extends: {theme}
style:
  frame:
    width: 1900
rows:
  - grid:
      columns: 12
      items:
        - item: wide_line
          width: 6
        - item: wide_table
          width: 6
  - grid:
      columns: 12
      items:
        - item: mid_line
          width: 3
        - item: mid_table
          width: 3
  - grid:
      columns: 12
      items:
        - item: narrow_line
          width: 2
        - item: narrow_table
          width: 2
charts:
  wide_line: &line_tile
    type: line
    title: "Line tile"
    subtitle: "Subtitle sits here"
    x: month
    y: revenue
{query}
  mid_line: *line_tile
  narrow_line: *line_tile
  wide_table: &table_tile
    type: table
    title: "Table tile"
    subtitle: "Subtitle sits here"
{query}
  mid_table: *table_tile
  narrow_table: *table_tile
"""


@pytest.mark.parametrize("theme", ["clarity", "stark"])
def test_chrome_matches_per_tile_width_not_board_width(
    theme: str, local_project: Callable[..., FilesystemProject]
):
    """Tiles of different widths on ONE board: each table must match the line
    beside it at that tile's own width.

    This is the card-grid case — a wide tile and a narrow tile on the same
    board resolve to different title sizes, and each table has to follow its
    own neighbor rather than a board-level constant.
    """
    svg = _render_svg(
        _MIXED_WIDTH_GRID.format(theme=theme, query=_VALUES_QUERY), local_project
    )

    title_sizes = []
    for size_name in ("wide", "mid", "narrow"):
        line_title_size, line_title_y, line_sub_size, line_sub_y, line_sub_fill = (
            _vl_title_subtitle(_chart_subtree(svg, f"{size_name}_line"))
        )
        tbl_title_size, tbl_title_y, tbl_sub_size, tbl_sub_y, tbl_sub_fill = (
            _table_title_subtitle(_chart_subtree(svg, f"{size_name}_table"))
        )
        title_sizes.append(line_title_size)

        assert tbl_title_size == line_title_size, (
            f"{size_name} tile: table title size {tbl_title_size} != line "
            f"{line_title_size} — table is not tracking its own tile's width"
        )
        assert tbl_title_y == line_title_y, (
            f"{size_name} tile: table title baseline {tbl_title_y} != line "
            f"{line_title_y} at title size {line_title_size}"
        )
        assert tbl_sub_size == line_sub_size, (
            f"{size_name} tile: table subtitle size {tbl_sub_size} != line {line_sub_size}"
        )
        assert tbl_sub_fill == line_sub_fill, (
            f"{size_name} tile: table subtitle fill {tbl_sub_fill!r} != line {line_sub_fill!r}"
        )
        assert (tbl_sub_y - tbl_title_y) == (line_sub_y - line_title_y), (
            f"{size_name} tile: table title->subtitle gap "
            f"{tbl_sub_y - tbl_title_y} != line {line_sub_y - line_title_y}"
        )

    # The point of the fixture: without more than one tier on this single
    # board, the assertions above would pass just as well against a
    # board-width-derived baseline.
    assert len(set(title_sizes)) > 1, (
        f"fixture no longer spans multiple title tiers (sizes: {title_sizes}) — "
        "it cannot distinguish per-tile sizing from per-board sizing"
    )
