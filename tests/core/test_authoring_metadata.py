"""Rendered SVG metadata used by Cloud's authoring interactions."""

from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree

import pytest

from dbt_charts.core.render.controls import controls_stylesheet

from ._svg_render import (
    authored_boxes,
    leaf_kind_subtrees,
    render_board_file,
    render_board_to_svg,
)

# A themed value deliberately distinct from any shipped default, so a test
# passing by coincidence (matching the real default) can't hide a bug.
_PROBE_CARD_PADDING = 24.0

# Boxes are emitted through px() rounding, so exact edge-sharing can land a
# fraction over. Touching is fine; only real overlap is the defect.
_OVERLAP_TOLERANCE = 1.0

# A nested board carrying BOTH a title and its own variables, with the band
# layout that puts them side by side — the only shape where a leaf kind can
# accidentally span two authored keys.
_NESTED_INLINE_BAND_BOARD = """
title: Board Title
variables:
  scope:
    input: text
    default: All
style:
  variables:
    position: title-inline
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - title: Section Heading
    variables:
      region:
        input: text
        default: East
    rows:
      - c
"""

# All three composers that draw a variables band, in one board. `scope` takes
# the root's inline band (a titled board, which `style.variables.position`
# defaults to) and `region` the root-level plain band's nested twin; `segment`
# takes the nested *inline* band, which is the ordinary shape for a titled
# section and the one branch none of the three assertions in this repo reached.
# Both nested boards are declared here, so all three paths are keys of this file
# — which is the whole distinction: the nested composers get their position from
# `source_path`, and only a nested titled section can tell a real path from a
# literal `"variables"`.
_ROOT_AND_NESTED_VARIABLES_BOARD = """
title: Board Title
variables:
  scope:
    input: text
    default: All
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - variables:
      region:
        input: text
        default: East
    rows:
      - c
  - title: Nested Section
    variables:
      segment:
        input: text
        default: SMB
    rows:
      - c
"""

_SELECTION_BOUNDARY_BOARD = f"""
title: Box Probe
text: Body copy.
style:
  frame:
    card_padding: {_PROBE_CARD_PADDING}
queries:
  q:
    type: values
    rows:
      - month: Jan
        revenue: 100
      - month: Feb
        revenue: 150
  q1:
    type: values
    rows:
      - revenue: 250
charts:
  bars:
    query: q
    type: bar
    x: month
    y: revenue
  tbl:
    query: q
    type: table
  kpi:
    query: q1
    type: kpi
    value: revenue
rows:
  - cols: [bars, tbl, kpi]
"""


def _first_ink_offset_x(svg: str, authored_path: str) -> float:
    """Local x of the first painted element inside the tagged authoring group.

    Walks past the group's own opening tag and its two non-painting boundary
    rects, then returns the x of the first ``translate(x, ...)`` or explicit
    ``x="..."`` on a paint-bearing element — whichever comes first in document
    order. This is a local offset relative to the tagged group's own coordinate
    frame, which is exactly what "does the tagged box start outside its own ink
    by the padding it was handed" needs: it doesn't matter whether that ink sits
    behind one themed wrapper translate or is emitted directly.
    """
    tag = re.search(rf'data-authored-path="{re.escape(authored_path)}"[^>]*>', svg)
    assert tag, f"no tagged group for authored path {authored_path!r}"
    rest = svg[tag.end() :]
    while True:
        boundary_rect = re.match(r'<rect class="dbt-box-(?:outer|inner)"[^>]*/>', rest)
        if not boundary_rect:
            break
        rest = rest[boundary_rect.end() :]
    ink = re.search(
        r'<(?:rect|text|tspan|g)\b[^>]*?\btransform="translate\(([-\d.]+)'
        r'|<(?:rect|text|tspan)\b[^>]*?\bx="([-\d.]+)"',
        rest,
    )
    assert ink, f"no paintable content found for {authored_path!r}"
    return float(ink.group(1) or ink.group(2))


@pytest.mark.parametrize(
    "authored_path",
    ["charts.bars", "charts.tbl", "charts.kpi", "title", "text"],
)
def test_tagged_box_starts_outside_its_own_ink_by_the_padding_it_was_handed(
    authored_path: str,
) -> None:
    """A tagged box always contains the block's padding, never sits inside it.

    This always held for Vega families (bar), which bake padding into the VL
    spec. It did not hold for table, KPI, or prose: their tag sat on the
    padding-excluded inner box while the padding lived in an enclosing
    transform the tag never saw, which is what put a host's selection mark on
    a table's own first and last rows.
    """
    svg = render_board_to_svg(_SELECTION_BOUNDARY_BOARD)

    ink_x = _first_ink_offset_x(svg, authored_path)

    assert ink_x >= _PROBE_CARD_PADDING, (
        f"{authored_path}: tagged box's ink starts at local x={ink_x}, "
        f"less than the {_PROBE_CARD_PADDING}px padding it was handed — "
        "the tag is on the padding-excluded inner box"
    )


def test_no_two_authored_blocks_claim_the_same_space() -> None:
    """A block's selection box may never reach into a neighbor's.

    The x-axis check above passes on a box whose vertical padding was invented
    rather than allocated, which is exactly what shipped: prose synthesized
    ``card_padding`` on all four sides while the layout stacks title and text
    with a deliberate 0 gap, so the title's mark ran 16px past where the text's
    glyphs start and the two boxes overlapped by twice the padding. Overlap is
    the axis-complete statement of the invariant — a mark that covers a
    neighbor's ink is pointing at the wrong thing, whichever side it grew on.
    """
    boxes = authored_boxes(
        render_board_to_svg(_SELECTION_BOUNDARY_BOARD), "dbt-box-outer"
    )
    assert len(boxes) >= 3, f"expected several authored blocks, got {sorted(boxes)}"

    overlaps = []
    for a, b in itertools.combinations(sorted(boxes), 2):
        ax, ay, aw, ah = boxes[a]
        bx, by, bw, bh = boxes[b]
        wide = min(ax + aw, bx + bw) - max(ax, bx)
        tall = min(ay + ah, by + bh) - max(ay, by)
        if wide > _OVERLAP_TOLERANCE and tall > _OVERLAP_TOLERANCE:
            overlaps.append(f"{a} and {b} share {wide:.1f}x{tall:.1f}px")
    assert not overlaps, "selection boxes overlap: " + "; ".join(overlaps)


def test_root_title_text_and_charts_carry_authored_paths() -> None:
    svg = render_board_to_svg(
        """
title: Authoring Handles
text: Body copy.
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - revenue
"""
    )

    assert 'data-authored-path="title"' in svg
    assert 'data-authored-kind="title"' in svg
    assert 'data-authored-path="text"' in svg
    assert 'data-authored-kind="text"' in svg
    assert 'data-authored-path="charts.revenue"' in svg
    assert 'data-authored-kind="chart"' in svg
    # Identity lives on the authored group; the boundary rects are geometry
    # only, so a host resolves both from one `closest('[data-authored-path]')`.
    boundary_rects = re.findall(r'<rect class="dbt-box-(?:outer|inner)"[^>]*>', svg)
    assert boundary_rects
    assert not any("data-authored" in rect for rect in boundary_rects)


def test_axis_titles_carry_editable_label_kinds() -> None:
    """The first X- and Y-axis title runs are tagged as `x_label`/`y_label`
    leaves — the same double-click-to-edit vocabulary as `title`/`subtitle`.
    `pointer-events` stays exactly as vl_convert painted it (Cloud re-enables
    it in CSS), so stamped output normalizes identically for the goldens."""
    svg = render_board_to_svg(
        """
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    x_label: Month
    y_label: Revenue
rows:
  - revenue
"""
    )

    stamped = re.findall(
        r'<g class="mark-text role-axis-title" pointer-events="none"'
        r' data-authored-kind="([xy]_label)">',
        svg,
    )
    assert sorted(stamped) == ["x_label", "y_label"]


def _stamp_follows_axis(svg: str, kind: str) -> str:
    """The aria axis name of the group the `kind` stamp sits under."""
    at = svg.index(f'data-authored-kind="{kind}"')
    x_at = svg.rfind('aria-label="X-axis', 0, at)
    y_at = svg.rfind('aria-label="Y-axis', 0, at)
    return "X" if x_at > y_at else "Y"


def test_a_horizontal_chart_stamps_the_authored_key_not_the_layout_channel() -> None:
    """A horizontal bar draws the authored `x` on the visual Y axis —
    vl_convert's aria text names the layout channel, so trusting it wrote
    `y_label` onto the authored `x_label` title: silent YAML corruption on
    the exact double-click this exists to enable."""
    svg = render_board_to_svg(
        """
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  revenue:
    query: q
    type: bar
    style:
      orientation: horizontal
    x: month
    y: revenue
    x_label: Month
    y_label: Revenue
rows:
  - revenue
"""
    )

    assert _stamp_follows_axis(svg, "x_label") == "Y"
    assert _stamp_follows_axis(svg, "y_label") == "X"


def test_faceted_charts_stamp_no_axis_labels() -> None:
    """Faceting rearranges which axis carries what — with no confident
    mapping, nothing is stamped: an axis title that is not editable beats
    one that edits the wrong key."""
    svg = render_board_to_svg(
        """
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100, region: East}
      - {month: Jan, revenue: 90, region: West}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    color: region
    x_label: Month
    y_label: Revenue
    multiples:
      columns: region
rows:
  - revenue
"""
    )

    assert 'data-authored-kind="x_label"' not in svg
    assert 'data-authored-kind="y_label"' not in svg


def test_selection_boxes_cover_table_and_kpi_charts() -> None:
    svg = render_board_to_svg(
        """
title: Authoring Handles
queries:
  q:
    type: values
    rows:
      - {name: Alpha, value: 10}
charts:
  metric:
    query: q
    type: kpi
    value: value
  table:
    query: q
    type: table
rows:
  - cols: [metric, table]
"""
    )

    # Title plus both charts — a KPI and a table get the same selection
    # boundary as a Vega chart does: one outer (mark) rect and one inner
    # (pointer-target) rect per authored block.
    assert svg.count('class="dbt-box-outer"') == 3
    assert svg.count('class="dbt-box-inner"') == 3
    assert 'data-authored-path="charts.metric"' in svg
    assert 'data-authored-path="charts.table"' in svg
    # The inner box catches the pointer through the host's rule on its class;
    # the board carries no pointer-events attribute of its own.
    assert 'pointer-events="all"' not in svg
    assert ".dbt-chart .dbt-box-inner" in controls_stylesheet()


def test_nested_title_and_text_share_one_handle_on_their_board() -> None:
    """``title:`` and ``text:`` are keys of the same board, so they select as one.

    A chart's title is a key inside the chart and selects with it; splitting a
    board's own header into two handles made prose behave unlike every chart beside
    it. The handle is the header rather than the whole board because a board can
    hold nested charts, and offering its whole subtree as the target is not a
    thing anyone is trying to point at.
    """
    svg = render_board_to_svg(
        """
title: Authoring Handles
rows:
  - title: Section Heading
    text: Section body.
"""
    )

    assert 'data-authored-path="rows.0"' in svg
    assert 'data-authored-kind="header"' in svg
    assert 'data-authored-path="rows.0.title"' not in svg
    assert 'data-authored-path="rows.0.text"' not in svg


def test_nested_inline_chart_uses_layout_authored_path() -> None:
    svg = render_board_to_svg(
        """
title: Inline Chart Handles
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
rows:
  - rows:
      - query: q
        type: bar
        x: month
        y: revenue
"""
    )

    assert 'data-authored-path="rows.0.rows.0"' in svg
    assert 'data-authored-path="charts.row0"' not in svg


def test_nested_callout_carries_its_authoring_path() -> None:
    """The callout family declares `source_path` itself and must populate it.

    `ResolvedCalloutChart` inherits `BaseModel` directly rather than
    `_BaseResolvedChartFields`, so the field added to the shared base did not
    reach it — every other family carried a path and this one relied on a
    `getattr` default. A nested declaration is what distinguishes the two: it
    is the case where the prefixed path and the bare `charts.<id>` fallback
    give different answers.
    """
    svg = render_board_to_svg(
        """
title: Callout Handles
rows:
  - charts:
      note:
        type: callout
        message: Heads up.
    rows:
      - note
"""
    )

    assert 'data-authored-path="rows.0.charts.note"' in svg
    assert 'data-authored-path="charts.note"' not in svg


def test_nested_named_chart_ref_uses_nested_layout_authored_path() -> None:
    svg = render_board_to_svg(
        """
title: Nested Named Chart Handles
rows:
  - charts:
      revenue:
        query:
          type: values
          rows:
            - {month: Jan, revenue: 100}
        type: bar
        x: month
        y: revenue
    rows:
      - revenue
"""
    )

    assert 'data-authored-path="rows.0.charts.revenue"' in svg
    assert 'data-authored-path="charts.revenue"' not in svg


def test_tab_inline_chart_uses_tab_layout_authored_path() -> None:
    svg = render_board_to_svg(
        """
title: Tab Chart Handles
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
tabs:
  items:
    - title: Overview
      rows:
        - query: q
          type: bar
          x: month
          y: revenue
"""
    )

    assert 'data-authored-path="tabs.items.0.rows.0"' in svg
    assert 'data-authored-path="rows.0"' not in svg


def test_title_and_text_groups_carry_selection_boxes() -> None:
    """Text groups only receive pointer events over their glyphs.

    Cloud's hover indicator measures the inner rect, so a title or text block
    without one is hoverable across its words but dead across the rest of its
    box — and reports a tight glyph bbox rather than the authored block.
    """
    svg = render_board_to_svg(
        """
title: Authoring Handles
text: Body copy.
rows:
  - title: Section Heading
    text: Section body.
"""
    )

    # The root board's own two keys, plus one over the nested board's header band.
    assert svg.count('class="dbt-box-outer"') == 3
    assert svg.count('class="dbt-box-inner"') == 3


def test_title_inner_box_spans_the_rendered_block_width() -> None:
    """The pointer target is only useful if it matches the block it fronts.

    ``content_width`` exists solely to make that true, and it is threaded
    through several call sites — so pin it against the width the title block
    actually declares rather than against a recomputed layout formula.
    """
    svg = render_board_to_svg(
        """
title: Authoring Handles
text: Body copy.
"""
    )

    # The inner rect is the un-padded pointer target: `<rect
    # class="dbt-box-inner" … width="…"/>`, then — through the padding
    # translate every authored group now wraps its content in — the block
    # it fronts.
    match = re.search(
        r'<rect class="dbt-box-inner"[^>]*width="(\d+)"[^>]*/>'
        r'<g transform="translate\([^)]*\)">\s*<svg[^>]*width="([\d.]+)"',
        svg,
    )
    assert match, "Expected an inner box followed by its block"
    inner_width, block_width = int(match.group(1)), float(match.group(2))
    assert inner_width == round(block_width)


def test_inline_title_band_title_carries_selection_boxes() -> None:
    """The title-inline band builds its own title group, not the shared one."""
    svg = render_board_to_svg(
        """
title: Banded
variables:
  region:
    input: text
    default: East
style:
  variables:
    position: title-inline
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
    )

    assert 'data-authored-kind="title"' in svg
    # The band title plus the chart background.
    assert svg.count('class="dbt-box-outer"') == 2
    assert svg.count('class="dbt-box-inner"') == 2


def test_nested_inline_title_band_does_not_carry_the_root_title_path() -> None:
    """A section's own heading is not a handle on the board's ``title:`` key.

    The band renderer hard-coded the root ``title`` path, which was harmless while
    the band was the root's own. Wrapping it in the section's header handle left two
    nested handles over one heading, and ``closest('[data-authored-path]')`` resolves
    the inner one — so clicking a section heading selected the whole board's title
    and sent the editor to the top of the document.
    """
    svg = render_board_to_svg(_NESTED_INLINE_BAND_BOARD)

    assert 'data-authored-path="rows.0"' in svg
    assert 'data-authored-kind="header"' in svg
    assert svg.count('data-authored-path="title"') == 1


def test_every_variable_authored_in_this_file_carries_its_own_path() -> None:
    """The wiring, not the strip's own contract, which is pinned elsewhere.

    ``render_variables_strip_svg`` takes ``variables_path`` and honors it —
    ``test_variables_chrome.py`` pins both answers. What that cannot see is
    which path each composer passes, and there are four of them.

    A boolean here was the wrong shape twice over. It re-derived a fact
    ``LayoutItem.source_path`` already carries, against the ``compile``
    invariant that position is composed once at the call site that knows it;
    and having only two answers, it could only say *root* or *nothing* — so an
    inline nested board, whose variables are keys of this very file at
    ``rows.0.variables.region``, got no handle at all and its controls were
    unclickable. Passing the path instead makes the third answer sayable, and
    the two the boolean did get right fall out of it: the root is
    ``"variables"``, and a board whose ``source_path`` is empty — an imported
    file, whose keys are not in this document — stays empty and stamps nothing.

    ``segment`` is the case the first version of this test missed. The inline
    composer derives its argument from the title attributes it was handed, so
    simplifying that expression to a literal — the natural tidy-up — leaves the
    root answer unchanged and silently hands every nested titled section a
    board-level path. A click there selects board settings, which is the
    resolve-up failure this argument exists to prevent.
    """
    svg = render_board_to_svg(_ROOT_AND_NESTED_VARIABLES_BOARD)

    handles = {
        group.get("data-dbt-variable"): group.get("data-authored-path")
        for group in ElementTree.fromstring(svg).iter()
        if group.get("data-dbt-variable")
    }
    assert handles == {
        "scope": "variables.scope",
        "region": "rows.0.variables.region",
        "segment": "rows.1.variables.segment",
    }

    # The fourth composer, and the last one unpinned: drop the board title and
    # the root's own variables move to the standalone band, which passes a
    # literal `"variables"`. Emptying that costs the root strip its handle and
    # nothing else in the repo notices.
    untitled = render_board_to_svg(
        _ROOT_AND_NESTED_VARIABLES_BOARD.replace("title: Board Title\n", "")
    )
    assert 'data-authored-path="variables.scope"' in untitled


def test_imported_board_chart_carries_no_handle(tmp_path: Path) -> None:
    """A chart from an imported board file gets no authoring handle at all.

    Its coordinates address the imported file, not the one being viewed, so a
    handle here would send click-to-source to whatever the importing file
    happens to have at those coordinates. No handle beats a wrong one — and
    A `source_path` of "" is only meaningful if the emission site actually
    honors it.
    """
    (tmp_path / "partial.yml").write_text(
        """
title: Imported Board
rows:
  - query:
      type: values
      rows:
        - month: Jan
          revenue: 100
    type: bar
    x: month
    y: revenue
"""
    )
    importing = tmp_path / "importing.yml"
    importing.write_text(
        """
title: Importing Board
rows:
  - partial.yml
"""
    )

    svg = render_board_file(importing)
    assert isinstance(svg, str)

    # The importing board's own title still has one; the imported subtree does not.
    assert 'data-authored-path="title"' in svg
    assert 'data-authored-kind="chart"' not in svg
    assert 'data-authored-path="rows.0"' not in svg


def test_imported_board_variables_carry_no_handle(tmp_path: Path) -> None:
    """The variables half of the rule above, and the one the path now decides.

    An inline nested board and an imported one are the same object to the
    renderer and differ only in ``source_path`` — so the composer that stamps
    ``rows.0.variables.region`` on the first must stamp nothing on the second,
    whose ``variables:`` keys are in a file this document does not contain.
    While the argument was a boolean this was covered by never stamping a
    nested board at all; now that inline nested boards do get a handle, an empty
    ``source_path`` is the only thing keeping the imported one from getting a
    wrong one, and that is worth its own failure.
    """
    (tmp_path / "partial.yml").write_text(
        """
title: Imported Board
variables:
  region:
    input: text
    default: East
rows:
  - query:
      type: values
      rows:
        - month: Jan
          revenue: 100
    type: bar
    x: month
    y: revenue
"""
    )
    importing = tmp_path / "importing.yml"
    importing.write_text(
        """
title: Importing Board
variables:
  scope:
    input: text
    default: All
rows:
  - partial.yml
"""
    )

    svg = render_board_file(importing)
    assert isinstance(svg, str)

    handles = {
        group.get("data-dbt-variable"): group.get("data-authored-path")
        for group in ElementTree.fromstring(svg).iter()
        if group.get("data-dbt-variable")
    }
    assert handles == {"scope": "variables.scope", "region": None}

    # No path, no keys: the whole control is value surface. Tagging the label
    # run anyway would offer an edit with nowhere to write, and would split a
    # control's pointer behavior on which file it came from.
    labels = {
        group.get("data-dbt-variable"): [
            run.get("data-authored-kind")
            for run in group
            if run.get("data-authored-kind")
        ]
        for group in ElementTree.fromstring(svg).iter()
        if group.get("data-dbt-variable")
    }
    assert labels == {"scope": ["label"], "region": []}


# A board that names two of another file's items by reference. Both keys are
# written in *this* file, which is what makes them the interesting case: the
# coordinates exist here, and only the definitions they point at are elsewhere.
_CROSS_FILE_SHARED = """
title: Shared
variables:
  region:
    input: select
    options:
      static: [East, West]
    default: East
charts:
  bar:
    query:
      type: values
      rows:
        - {month: Jan, revenue: 100}
    type: bar
    x: month
    y: revenue
"""

_CROSS_FILE_IMPORTING = """
title: Importing Board
variables:
  local:
    input: text
    default: All
  shared: shared.yml.variables.region
charts:
  borrowed: shared.yml.charts.bar
  own:
    query:
      type: values
      rows:
        - {month: Jan, revenue: 100}
    type: bar
    x: month
    y: revenue
rows:
  - borrowed
  - own
"""


def _write_cross_file_pair(tmp_path: Path) -> Path:
    (tmp_path / "shared.yml").write_text(_CROSS_FILE_SHARED)
    importing = tmp_path / "importing.yml"
    importing.write_text(_CROSS_FILE_IMPORTING)
    return importing


def test_a_cross_file_variable_reference_carries_no_handle(tmp_path: Path) -> None:
    """``shared: other.yml.variables.region`` is a name here, not a definition.

    The importing file really does have a ``variables.shared`` key, so the
    coordinates are honest — but what sits at them is a reference string, and
    the design walk refuses those and hands back the nearest editable ancestor
    instead. That ancestor is the board, so a stamped handle turns a click on a
    filter chip into the board's own controls, and the save writes board YAML.
    Withholding is the same answer the imported chart wrapper already gives.
    """
    svg = render_board_file(_write_cross_file_pair(tmp_path))
    assert isinstance(svg, str)

    handles = {
        group.get("data-dbt-variable"): group.get("data-authored-path")
        for group in ElementTree.fromstring(svg).iter()
        if group.get("data-dbt-variable")
    }
    assert handles == {"local": "variables.local", "shared": None}


def test_a_cross_file_chart_reference_carries_no_handle(tmp_path: Path) -> None:
    """The chart half of the same rule, and the reason ``local_chart_ids`` exists.

    A ``charts:`` key holding ``other.yml.charts.bar`` is in this file's chart
    map, so it counted as local and got a handle — pointing at a line the panel
    cannot edit, which lands the click on the board.
    """
    svg = render_board_file(_write_cross_file_pair(tmp_path))
    assert isinstance(svg, str)

    assert 'data-authored-path="charts.own"' in svg
    assert 'data-authored-path="charts.borrowed"' not in svg


def test_chart_title_and_subtitle_carry_their_own_leaf_kind() -> None:
    """Vega's own ``role-title-text``/``role-title-subtitle`` classes are a
    1:1 signal for the chart's ``title:``/``subtitle:`` keys — transcribed
    into our vocabulary as a bare leaf kind, not read by Cloud directly.
    Selectors.md's dbt-* carve-out covers classes we emit; these are
    vl_convert's, so Cloud selects on the attribute instead."""
    svg = render_board_to_svg(
        """
title: Authoring Handles
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue
    subtitle: by month
rows:
  - revenue
"""
    )

    title_leaf = re.search(
        r'class="mark-text role-title-text"[^>]*data-authored-kind="title"', svg
    )
    subtitle_leaf = re.search(
        r'class="mark-text role-title-subtitle"[^>]*data-authored-kind="subtitle"',
        svg,
    )
    assert title_leaf, "no leaf-kind tag on the chart's title text"
    assert subtitle_leaf, "no leaf-kind tag on the chart's subtitle text"
    assert "data-authored-path" not in title_leaf.group(0)
    assert "data-authored-path" not in subtitle_leaf.group(0)


@pytest.mark.parametrize("variant", ["stacked", "inline", "compact"])
def test_kpi_label_carries_its_own_leaf_kind(variant: str) -> None:
    """A KPI's authored key is ``label:``, not ``title:`` — no signal existed
    at all for its text run before this, so it gets a bare leaf kind.

    Parametrized over every ``variant:`` because the three values dispatch to
    three different emitters that all render the same authored ``label:`` key.
    A fixture that omits ``variant:`` exercises only ``stacked`` and makes the
    other two read as covered — which is exactly how the gap shipped once.
    """
    svg = render_board_to_svg(
        f"""
title: Authoring Handles
queries:
  q:
    type: values
    rows:
      - {{name: Alpha, value: 10}}
charts:
  metric:
    query: q
    type: kpi
    value: value
    label: Alpha Metric
    variant: {variant}
"""
    )

    label_leaf = re.search(
        r"<(?:text|tspan)[^>]*data-authored-kind=\"label\"[^>]*>", svg
    )
    assert label_leaf, f"no leaf-kind tag on the KPI label for variant={variant!r}"
    assert "data-authored-path" not in label_leaf.group(0)


def test_nested_header_pieces_carry_their_own_leaf_kind() -> None:
    """Inside the combined ``header`` handle, each piece names its own key.

    The block-level handle says "this is a header"; only the leaf inside it
    says which of the two keys a double-click landed on. The leaf carries a
    bare kind — no path, since the path already lives on the enclosing block.
    """
    svg = render_board_to_svg(
        """
title: Authoring Handles
rows:
  - title: Section Heading
    text: Section body.
"""
    )

    title_leaf = re.search(r'<g transform="[^"]*" data-authored-kind="title">', svg)
    text_leaf = re.search(r'<g transform="[^"]*" data-authored-kind="text">', svg)
    assert title_leaf, "no leaf-kind tag on the header's title piece"
    assert text_leaf, "no leaf-kind tag on the header's text piece"
    assert "data-authored-path" not in title_leaf.group(0)
    assert "data-authored-path" not in text_leaf.group(0)


def test_nested_inline_band_tags_the_title_not_the_variables_beside_it() -> None:
    """A leaf kind names one authored key, so it may not span two.

    ``style.variables.position: title-inline`` puts the board's title and its
    variable controls in one band, and ``should_use_title_inline_band`` only
    fires when there *are* visible variables — so the band always holds both.
    Tagging the band wrapper ``kind="title"`` makes the nearest-kind lookup
    resolve a variable control to ``title:``, which would point an inline
    editor at the heading when the user clicked a variable pill. The root board
    already tags only its title sub-group; this pins the nested branch to the
    same shape.
    """
    svg = render_board_to_svg(_NESTED_INLINE_BAND_BOARD)

    tagged = leaf_kind_subtrees(svg, "title")
    assert tagged, "no title leaf kind in the nested inline band"
    offenders = [sub for sub in tagged if "dbt-variable" in sub]
    assert not offenders, (
        "a title-kinded group encloses the variables strip — a variable "
        "control would resolve to the title: key"
    )


# A title carrying both an ascender-and-cap top edge and a real descender, so
# the ink it measures is the tallest and deepest a heading gets.
_TITLE_INK_BOARD = """
title: Big Title Gypsy
rows:
  - text: Body copy.
"""

_INLINE_BAND_INK_BOARD = """
title: Big Title Gypsy
variables:
  region:
    input: text
    default: East
style:
  variables:
    position: title-inline
rows:
  - text: Body copy.
"""


class _InkAndBox(NamedTuple):
    box_top: float
    box_bottom: float
    ink_top: float
    ink_bottom: float
    baseline: float
    font_size: float


def _title_ink_and_box(svg: str, kind: str = "title") -> _InkAndBox:
    """``(box_top, box_bottom, ink_top, ink_bottom, font_size)`` for the title.

    All five are in the boxed group's own frame. The heading's ``y`` is in the
    content's frame, which the block's own top padding offsets from that one, so
    the wrappers between the boundary rects and the text are walked and their
    ``dy`` added — the same conversion ``selection_boxes`` does to the mark.

    Ink is measured from the font's own glyph outlines rather than from the
    formula that places the box — a test that recomputed the production
    arithmetic would agree with a wrong answer.
    """
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.ttLib import TTFont

    from dbt_charts.core.font_measure import markdown_font_faces
    from dbt_charts.core.render.sizing import (
        get_compact_style,
        get_theme_style,
        resolve_style,
        title_font_family,
    )

    group = leaf_kind_subtrees(svg, kind)
    assert len(group) == 1, f"expected exactly one {kind} group, got {len(group)}"
    subtree = group[0]

    box = re.search(
        r'<rect class="dbt-box-outer"[^>]*y="([-\d.]+)"[^>]*height="([\d.]+)"', subtree
    )
    assert box, "no outer selection box on the title group"
    box_top = float(box.group(1))
    box_bottom = box_top + float(box.group(2))

    heading = re.search(r'<text[^>]*y="([\d.]+)"[^>]*font-size="([\d.]+)"', subtree)
    assert heading, "no heading text in the title group"
    baseline, font_size = float(heading.group(1)), float(heading.group(2))
    between = subtree[box.end() : heading.start()]
    baseline += sum(
        float(dy) for dy in re.findall(r"translate\([-\d.]+,\s*([-\d.]+)\)", between)
    )

    resolved = resolve_style(get_theme_style())
    family = title_font_family(resolved)
    faces = markdown_font_faces(family, get_compact_style(resolved))
    # The title's own ink always paints through the heading face now (mdsvg
    # measures/paints any heading, board title included, against
    # Style.heading_font_family) — .heading is what glyph outlines here must
    # match, not .regular. markdown_font_faces always populates it.
    assert faces.heading is not None
    face = faces.heading
    font = TTFont(face.path, fontNumber=face.font_number)
    units = font["head"].unitsPerEm
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    tops, bottoms = [], []
    for char in re.search(r"<tspan[^>]*>([^<]*)</tspan>", subtree).group(1):
        name = cmap.get(ord(char))
        if name is None:
            continue
        pen = BoundsPen(glyphs)
        glyphs[name].draw(pen)
        if pen.bounds:
            bottoms.append(pen.bounds[1])
            tops.append(pen.bounds[3])
    assert tops, "measured no glyph outlines for the title"
    return _InkAndBox(
        box_top,
        box_bottom,
        baseline - max(tops) / units * font_size,
        baseline - min(bottoms) / units * font_size,
        baseline,
        font_size,
    )


@pytest.mark.parametrize(
    "board", [_TITLE_INK_BOARD, _INLINE_BAND_INK_BOARD], ids=["plain", "inline-band"]
)
def test_title_selection_box_sits_evenly_around_its_glyphs(board: str) -> None:
    """The mark a host traces is centered on the words, not on the band.

    A heading band is not centered on its own text — mdsvg reserves
    ``heading_margin_top`` above the line box and a smaller
    ``heading_margin_bottom`` below it — so a box drawn on the band extent
    rides high above the glyphs by roughly the difference. Bounding the
    asymmetry is what "looks seated on the title" reduces to, and it is the
    axis-complete statement: growing either edge to fix the eye-line would
    fail it just as growing the other did.
    """
    m = _title_ink_and_box(render_board_to_svg(board))

    above, below = m.ink_top - m.box_top, m.box_bottom - m.ink_bottom
    assert abs(above - below) <= 0.25 * m.font_size, (
        f"selection box slack is lopsided: {above:.1f}px above the ink, "
        f"{below:.1f}px below it (font size {m.font_size:g})"
    )


@pytest.mark.parametrize(
    "board", [_TITLE_INK_BOARD, _INLINE_BAND_INK_BOARD], ids=["plain", "inline-band"]
)
def test_title_selection_box_never_clips_above_the_baseline(board: str) -> None:
    """The mark is the line box, so descenders may hang out of it — nothing else may.

    A line box is the space a line is allotted, not a bounding box of its ink: at
    a heading line height tighter than the face's ascent-to-descender, the tails
    of a ``g`` or ``y`` fall below it, exactly as they fall out of a browser's
    selection highlight. Everything from the baseline up is the body of the word
    and has to be inside, or the mark is seated too low — which is the failure
    mode that trades one lopsided box for its mirror image.
    """
    m = _title_ink_and_box(render_board_to_svg(board))

    assert m.box_top <= m.ink_top, (
        f"box top {m.box_top:.1f} clips the tops of the glyphs at {m.ink_top:.1f}"
    )
    assert m.box_bottom >= m.baseline, (
        f"box bottom {m.box_bottom:.1f} is above the baseline {m.baseline:.1f} — "
        "the mark is cutting through the words, not just their descenders"
    )
    assert m.ink_bottom - m.box_bottom <= 0.1 * m.font_size, (
        f"descenders hang {m.ink_bottom - m.box_bottom:.1f}px below the mark "
        f"(font size {m.font_size:g}) — more overhang than a line box explains"
    )


# A nested board's ``title:`` and ``text:`` select as one handle over the header
# band, so the box that has to sit on the words is the header's, not a title's.
_NESTED_HEADER_INK_BOARD = """
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - title: Section Heading Gypsy
    text: Section body.
    rows:
      - c
"""

_NESTED_TITLE_ONLY_INK_BOARD = """
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - title: Section Heading Gypsy
    rows:
      - c
"""


@pytest.mark.parametrize(
    "board",
    [_NESTED_HEADER_INK_BOARD, _NESTED_TITLE_ONLY_INK_BOARD],
    ids=["with-text", "title-only"],
)
def test_nested_header_handle_is_seated_on_its_heading(board: str) -> None:
    """The combined handle opens on a heading, so it gets seated like one.

    Its bottom edge is a different question from the standalone title's: with
    text under the heading the handle has to reach the end of that text, and the
    heading's trailing margin is then the rhythm between the two rather than
    slack. The top edge has no such excuse — a section heading and a board title
    are the same heading geometry, so the two boxes must open the same distance
    above their glyphs. Pinning them to each other says that without naming a
    number that a theme is free to retune.
    """
    header = _title_ink_and_box(render_board_to_svg(board), "header")
    title = _title_ink_and_box(render_board_to_svg(_TITLE_INK_BOARD))
    opens, title_opens = (
        header.ink_top - header.box_top,
        title.ink_top - title.box_top,
    )

    assert opens == pytest.approx(title_opens, abs=1.0), (
        f"header handle opens {opens:.1f}px above its first glyphs but a board "
        f"title opens {title_opens:.1f}px above its own — the two are the same "
        "heading and must be seated alike"
    )
    # An absolute ceiling as well as the pin: seating both handles on the band
    # would satisfy the equality above, since the defect is common to both.
    # The bound is the face's own ascent overshoot — the ink of a cap sits below
    # the ascender line — and nothing like the 27.5px margin that used to be in.
    assert opens <= 0.35 * header.font_size, (
        f"header handle opens {opens:.1f}px above its first glyphs "
        f"(font size {header.font_size:g}) — that is the reserved band, not the ink"
    )


def test_seating_the_mark_does_not_shrink_what_a_pointer_can_hit() -> None:
    """The two rects are a mark and a hit target, and only the mark moved.

    Cloud traces ``.dbt-box-outer`` for the hover indicator and hit-tests
    ``.dbt-box-inner`` (``init.js`` — outer is ``pointer-events: none``). Seating
    both on the line box would look right and quietly make the heading's trailing
    margin unclickable, dropping the pointer through to whatever is behind it.
    The mark is the only thing the ink span is allowed to move.

    The two share a top edge: a heading opening its block collapses its leading
    margin, so there is no reserved space above the ink for the target to keep.
    All the slack is below, which is what makes the mark the shorter rect.
    """
    svg = render_board_to_svg(_TITLE_INK_BOARD)

    marks = authored_boxes(svg, "dbt-box-outer")
    targets = authored_boxes(svg, "dbt-box-inner")
    _, mark_y, _, mark_h = marks["title"]
    _, target_y, _, target_h = targets["title"]

    assert mark_h < target_h, (
        "the title's mark should be seated on its ink and so be shorter than "
        f"the block: mark {mark_h:.1f}px, hit target {target_h:.1f}px"
    )
    assert target_y <= mark_y and target_y + target_h > mark_y + mark_h, (
        f"hit target ({target_y:.1f}..{target_y + target_h:.1f}) no longer spans "
        f"the mark ({mark_y:.1f}..{mark_y + mark_h:.1f}) — it followed the ink"
    )


def test_a_variable_label_is_the_leaf_that_names_its_label_key() -> None:
    """The control is the block; its label run is the one key inside it.

    A variable control is the only authored element whose primary gesture is
    *using* it, so the block tag alone cannot carry the edit: an author aiming
    at the value surface must get the filter, not the code panel. The label run
    is the part of a control that is not a value, which is what makes it both
    the handle and the editable key — ``variables.<name>.label``, composed by
    ``resolveAuthored`` from these two tags exactly as a chart title is.
    """
    svg = render_board_to_svg(_ROOT_AND_NESTED_VARIABLES_BOARD)

    labels = {
        group.get("data-dbt-variable"): [
            run.get("data-authored-kind")
            for run in group
            if run.get("data-authored-kind")
        ]
        for group in ElementTree.fromstring(svg).iter()
        if group.get("data-dbt-variable")
    }
    assert labels == {"scope": ["label"], "region": ["label"], "segment": ["label"]}
