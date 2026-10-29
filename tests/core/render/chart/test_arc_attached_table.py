"""Trigger logic + series extraction for donut-attached tables.

Three-mode decision rule (wheel-dominance; locked with RJ — see decision D-03
of the wheel-first-pie-donut-labeling initiative):

- Empty shares: "direct" (placeholder path handled elsewhere).
- No slice clears ``WEDGE_LABEL_MIN_SHARE`` (8%): "full_table"
  (nothing would render a direct label anyway).
- Dominance ratio below ``WHEEL_DOMINANCE_MIN_RATIO`` (0.6):
  "full_table" (labels would starve the wheel).
- Otherwise (dominant): "hybrid" when any slice is below
  ``INVISIBLE_SLICE_SHARE`` (2%) — the wheel keeps its direct
  labels and a table lists every unlabeled (<= 8%) slice; else "direct" — wheel
  labels only, no table.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import PieChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
from dbt_charts.core.compile.models.primitives import (
    FormatConfig,
    StaticGradientColorStyle,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart.pie_attachment import (
    choose_table_placement,
    resolve_hybrid_heading_font,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style_and_context,
)
from dbt_charts.core.constants.pie import (
    ATTACHED_TABLE_GAP_PX,
    INVISIBLE_SLICE_SHARE,
    RIGHT_PLACEMENT_MAX_WHEEL_PX,
    RIGHT_PLACEMENT_MIN_WIDTH_FRACTION,
    RIGHT_PLACEMENT_MIN_WIDTH_PX,
    WEDGE_LABEL_MIN_SHARE,
)
from dbt_charts.core.render.chart.vega_lite import render_chart, render_resolved_chart
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.pie_layout import sized_pie_mode
from dbt_charts.core.render.svg_utils import escape_attr

from ..._engine_render import render_pie_svg

_SVG_NS = "{http://www.w3.org/2000/svg}"
_PIE_GAP = ATTACHED_TABLE_GAP_PX


def _donut_and_table_texts(svg: str) -> tuple[str, str]:
    """Parse a composed donut+table SVG and return (donut_text, table_text) —
    the concatenated text content of each of the two top-level groups, in
    document order (donut first, table second). Lets a test assert exactly
    which labels/title live on the wheel vs. in the attached table."""
    root = ET.fromstring(svg)
    donut_group, table_group = root.findall(f"{_SVG_NS}g")

    def _joined(group: ET.Element) -> str:
        return "".join(
            "".join(t.itertext()) for t in group.findall(f".//{_SVG_NS}text")
        )

    return _joined(donut_group), _joined(table_group)


def _shares(*pcts: float) -> list[float]:
    """Build a shares list summing to 1.0 from percentage inputs."""
    total = sum(pcts)
    return [p / total for p in pcts]


def _modes(pcts: list[float], names: list[str], width: float) -> tuple[str, str]:
    """``(labeled_mode, sized mode)`` of a pie of these slices in a card
    ``width`` wide: what the data allows and what the box decides."""
    rows = [{"n": n, "v": p} for n, p in zip(names, pcts, strict=True)]
    board_style, context = resolve_style_and_context(get_theme_style())
    resolved = resolve(
        PieChart(id="p", type="pie", theta="v", color="n"),
        rows,
        chart_style_context=context,
        width=width,
    )
    assert isinstance(resolved, ResolvedPieChart)
    return resolved.labeled_mode, sized_pie_mode(resolved, rows, board_style)


_EIGHT_NAMES = [
    "Enterprise",
    "Mid-Market",
    "SMB",
    "Self-serve",
    "Channel",
    "Partners",
    "Education",
    "Healthcare",
]


def test_slice_labels_that_clear_the_label_floor_are_offered_before_any_box_is_known():
    assert _modes([35, 25, 20, 12, 8], list("ABCDE"), 600.0)[0] == "direct"


def test_all_slices_below_label_floor_offers_no_labels():
    """Every slice at or under the 8% floor: nothing would render a direct
    label, so only the full table is on offer."""
    labeled, mode = _modes([1.0] * 13, [f"S{i}" for i in range(13)], 600.0)
    assert (labeled, mode) == ("none", "full_table")


def test_wheel_dominant_five_slice_is_direct():
    """A clean 5-slice pie at the 600px default keeps direct labels: the
    solved wheel keeps well over the dominance ratio of the label-free one."""
    names = ["North America", "Europe", "Asia Pacific", "Latin America", "Other"]
    assert _modes([35, 25, 20, 12, 8], names, 600.0) == ("direct", "direct")


def test_starved_narrow_width_is_full_table():
    """8 equal long-named slices at 300px starve the wheel below T=0.6."""
    assert _modes([1.0] * 8, _EIGHT_NAMES, 300.0) == ("direct", "full_table")


def test_wider_width_same_data_is_direct():
    """The same 8 equal long-named slices clear T=0.6 once the card is wide
    enough that the labels stop starving the wheel. The flip sits between 440px
    and 500px, where each side's own widest label leaves the wheel its share."""
    assert _modes([1.0] * 8, _EIGHT_NAMES, 440.0)[1] == "full_table"
    assert _modes([1.0] * 8, _EIGHT_NAMES, 500.0) == ("direct", "direct")


def test_each_side_reserves_only_its_own_widest_label():
    """The widest label sits on the left; the right side reserves only for its
    own, narrower one, so the wheel keeps its direct labels (Farmers' markets
    at 563px)."""
    names = ["Wholesale", "Grocers", "Juice and cider", "Farmers' markets", "Rest"]
    assert _modes([24587, 16801, 9115, 8702, 3880], names, 563.0) == (
        "direct",
        "direct",
    )


def test_a_starved_side_still_goes_to_the_table():
    """A single side whose labels consume the card still sends the chart to the
    table."""
    names = ["Wholesale", "Very long channel name " * 4]
    assert _modes([50, 50], names, 563.0)[1] == "full_table"


def test_a_hybrid_wheel_squeezed_beside_its_table_still_goes_to_the_table():
    """The dominance floor reads the bare wheel in the whole card, so a hybrid
    wheel that only matches its own narrow wheel box does not stand (the
    legend-position-pie hybrid / narrow card)."""
    names = ["Enterprise", "Mid-Market", "SMB", "Self-serve", "Partner", "Other"]
    assert _modes([40, 30, 20, 8, 1.5, 0.5], names, 342.67) == (
        "hybrid",
        "full_table",
    )


def test_wedge_threshold_config_is_eight_percent() -> None:
    """Pin the config default so accidental edits to the threshold show up
    here. The number is a design call, not a theme value."""
    assert WEDGE_LABEL_MIN_SHARE == 0.08


def test_invisible_slice_threshold_config_is_two_percent() -> None:
    """Pin the physical-visibility fallback separately from label suppression."""
    assert INVISIBLE_SLICE_SHARE == 0.02


def test_threshold_boundary_is_strictly_greater():
    """Slices at exactly 8% are suppressed (count as NOT visible). The
    rule is "share > 8%", not "share >= 8%"."""
    # Only the 68% wedge qualifies as visible (>8%); the rest sit at exactly
    # 8% and are suppressed. A single dominant label leaves the wheel intact.
    assert _modes([68, 8, 8, 8, 8], ["Big", "A", "B", "C", "D"], 700.0) == (
        "direct",
        "direct",
    )
    assert _modes([8.0] * 12 + [4.0], [f"S{i}" for i in range(13)], 700.0)[0] == "none"


# --------------------------------------------------------------------------
# The three worked cases (RJ), locked in the task spec as regression tests
# --------------------------------------------------------------------------


def test_worked_case_single_six_percent_slice_is_direct():
    """One 6% slice, no sub-2% tail: stays direct — the 6% wedge renders
    unlabeled on the wheel (hover only), no table at all."""
    assert _modes([70, 24, 6], ["Big", "Rest", "Small"], 600.0) == (
        "direct",
        "direct",
    )


def test_worked_case_single_one_percent_slice_is_hybrid():
    """One 1% slice: hybrid — the >8% wedges keep their direct labels, the
    1% is listed in the attached table."""
    assert _modes([70, 29, 1], ["Big", "Rest", "Tiny"], 600.0) == (
        "hybrid",
        "hybrid",
    )


def test_worked_case_six_and_one_percent_slices_is_hybrid():
    """A 6% AND a 1% slice: hybrid — both are <= 8% so both land in the
    table; the >8% wedges stay labeled on the wheel."""
    assert _modes([63, 30, 6, 1], ["Big", "Rest", "Small", "Tiny"], 600.0) == (
        "hybrid",
        "hybrid",
    )


# --------------------------------------------------------------------------
# Wedge-suppression: per-wedge label filter (share > 8%)
# --------------------------------------------------------------------------


def test_arc_resolution_suppresses_label_rows_below_threshold(make_chart):
    chart = make_chart(
        "pie",
        x="series",
        y="value",
        style={"marks": {"slice": {"labels": {"template": "{{ series }}"}}}},
    )
    data = [
        {"series": "Big", "value": 50},
        {"series": "Med", "value": 30},
        {"series": "Small1", "value": 12},
        {"series": "Small2", "value": 5},
        {"series": "Small3", "value": 3},
    ]
    rs, ctx = resolve_style_and_context(get_theme_style())
    resolved = resolve(chart, data, chart_style_context=ctx, width=415.0)
    assert resolved.slice_label_indices == (0, 1, 2)
    artifact = render_resolved_chart(resolved, data, rs, width=415.0, height=415.0)
    assert isinstance(artifact.payload, str)
    painted = re.findall(
        r'aria-roledescription="text mark"><tspan[^>]*>([^<]*)</tspan>',
        artifact.payload,
    )
    assert painted == ["Big", "Med", "Small1"]


# --------------------------------------------------------------------------
# Composed render order: swatch table rows must match donut wedge order
# --------------------------------------------------------------------------


def test_composed_render_wedge_paint_matches_swatch_paint(make_chart):
    """The donut wedges paint in palette[idx] order — same source the
    attached table reads — so a swatch labeled "Bravo" maps to the
    same color as the Bravo wedge.

    Inspects the donut SVG's actual <path> fill colors (not just the
    table's swatch rects). VL would otherwise order palette assignment
    alphabetically by color domain when no ``order`` encoding is set —
    the regression case the data_override + arc ``order`` hoist exists
    to prevent. Uses non-alphabetical series names so an alphabetical
    reorder would visibly diverge from the palette-index expectation.

    Fixture carries a sub-2% tail slice so the attached-table path fires
    deterministically (independent of width/font metrics) — this test's
    concern is wedge/swatch color order, not the dominance trigger itself.

    The chart deliberately omits ``total:`` to exercise the
    ``total is None`` early-return path in _map_slice — without the hoist
    sitting *above* that early-return, the order encoding would never be set
    in attached-table mode and the wedges would render in
    palette[alphabetical-of-color-domain] order.
    """
    chart = make_chart("pie", x="series", y="value")
    # No total — exercises the no-total early-return path in _map_slice.
    chart.total = None
    data = [
        {"series": "Bravo", "value": 96.0},
        {"series": "Alpha", "value": 1.0},
        {"series": "Charlie", "value": 1.0},
        {"series": "Delta", "value": 1.0},
        {"series": "Echo", "value": 1.0},
    ]
    rs, ctx = resolve_style_and_context(get_theme_style())
    svg = render_chart(chart, rs, ctx, data, format="svg", width=415.0)
    palette = list(ctx.palette)

    # Donut wedges are <path> elements with class="mark-arc" (the arc
    # mark layer). Extract their fill colors in document order — VL
    # emits arcs in the spec's data order when the ``order`` encoding
    # pins it. Each arc layer's first <path> fill is wedge 0, next is
    # wedge 1, etc.
    wedge_fills_in_draw_order = [
        fill.lower()
        for fill in re.findall(
            r'<path [^>]*fill="(#[a-fA-F0-9]+)"[^>]*aria-roledescription="arc mark"',
            svg,
        )
    ]
    assert len(wedge_fills_in_draw_order) == 5, (
        f"expected 5 wedge paths, got {len(wedge_fills_in_draw_order)}: "
        f"{wedge_fills_in_draw_order}"
    )
    expected_palette = [c.lower() for c in palette[:5]]
    assert wedge_fills_in_draw_order == expected_palette, (
        "Donut wedge fills must match palette[:n] in data-insertion order. "
        f"Got {wedge_fills_in_draw_order}, expected {expected_palette}. "
        "If alphabetical (Alpha→Bravo→Charlie→...), the arc order encoding "
        "wasn't applied — check the hoist position above the no-total/"
        "no-labels early return in the slice-mapping code."
    )


# --------------------------------------------------------------------------
# Composed render: donut SVG + attached table SVG in one chart slot
# --------------------------------------------------------------------------

# Shared hybrid fixture for the render-level cluster below: the wheel stays
# dominant (short names, plenty of width) but a sub-2% tail slice is present,
# so this exercises hybrid mode — direct labels on the >8% wedges (30, 20,
# 15, 12, 10) plus a table of the 3 unlabeled ones (7, 5, 1) — deterministically
# across the widths these tests use, so they can focus on the composed-render
# mechanics rather than re-proving the dominance calibration.
_HYBRID_TAIL_VALUES = [30.0, 20.0, 15.0, 12.0, 10.0, 7.0, 5.0, 1.0]

# Genuine-starvation fixture: 7 near-equal long-named slices (all > 8%, so
# nothing would naturally get tabled) plus one 1.5% tail, at a width narrow
# enough (300px) to starve the wheel below T=0.6 regardless of the tail slice.
# Exercises full_table's "starvation wins even with a sub-2% slice present"
# corner — the table must list every slice, not just the tail.
_GENUINE_STARVATION_NAMES = [
    "Enterprise",
    "Mid-Market",
    "SMB",
    "Self-serve",
    "Channel",
    "Partners",
    "Education",
    "Tail",
]
_GENUINE_STARVATION_VALUES = [14.0, 14.0, 14.0, 14.0, 14.0, 14.0, 14.5, 1.5]


def test_hybrid_render_emits_donut_and_table(make_chart):
    """When hybrid mode fires (here: a sub-2% tail slice on an otherwise
    dominant wheel), render_chart returns a composed SVG containing both the
    donut (Vega marks) and the swatch-keyed table (with <rect rx=3> swatches)
    for just the 3 unlabeled (<= 8%) rows.
    """
    chart = make_chart("pie", x="series", y="value")
    data = [{"series": f"S{i}", "value": v} for i, v in enumerate(_HYBRID_TAIL_VALUES)]
    rs, ctx = resolve_style_and_context(get_theme_style())

    svg = render_chart(chart, rs, ctx, data, format="svg", width=1200.0)

    # Donut from Vega-Lite — class="marks" wrapper.
    assert 'class="marks"' in svg, "Composed output must include the VL donut SVG"
    # Attached table — swatch rects with rx=3 (the rounded-square swatch).
    swatch_rects = re.findall(
        r'<rect[^>]*\brx="3"[^>]*\bfill="#[a-fA-F0-9]+"[^>]*/?>',
        svg,
    )
    assert len(swatch_rects) == 3, (
        f"Hybrid table must list only the 3 unlabeled (<= 8%) wedges (7, 5, "
        f"1). Got {len(swatch_rects)} swatch rects."
    )


def test_hybrid_render_keeps_wheel_labels_and_titles_the_table(make_chart):
    """Hybrid mode keeps direct labels on the >8% wedges (visible on the
    wheel) and lists only the <=8% wedges in a table titled "Too small to
    label" — the clean partition + contextual title from decision D-03."""
    chart = make_chart("pie", x="series", y="value")
    data = [
        {"series": "Big", "value": 63},
        {"series": "Rest", "value": 30},
        {"series": "Small", "value": 6},
        {"series": "Tiny", "value": 1},
    ]
    rs, ctx = resolve_style_and_context(get_theme_style())

    svg = render_chart(chart, rs, ctx, data, format="svg", width=600.0)
    donut_text, table_text = _donut_and_table_texts(svg)

    assert "Big" in donut_text and "Rest" in donut_text, (
        f"Wheel must keep direct labels for the >8% wedges. Got: {donut_text!r}"
    )
    assert "Small" not in donut_text and "Tiny" not in donut_text, (
        f"Wheel must NOT label the <=8% wedges. Got: {donut_text!r}"
    )
    assert "Small" in table_text and "Tiny" in table_text, (
        f"Table must list both <=8% wedges. Got: {table_text!r}"
    )
    assert "too small to label" in table_text.lower(), (
        f'Hybrid table must carry the "Too small to label" title. Got: {table_text!r}'
    )


def test_hybrid_heading_uses_table_body_style_not_chart_title(make_chart):
    """The hybrid heading is a subtle legend line, not a chart title: exact
    sentence case (never title-cased), the table's own BODY font family/size
    (never the serif chart-title font), and a weight heavier than plain rows
    (the header's weight — the theme-consistent "heavier than body" step)."""
    chart = make_chart("pie", x="series", y="value")
    data = [
        {"series": "Big", "value": 63},
        {"series": "Rest", "value": 30},
        {"series": "Small", "value": 6},
        {"series": "Tiny", "value": 1},
    ]
    rs, ctx = resolve_style_and_context(get_theme_style())
    table_style = ctx.table

    svg = render_chart(chart, rs, ctx, data, format="svg", width=600.0)

    m = re.search(r"<text[^>]*>Too small to label</text>", svg)
    assert m, f"Expected an exact 'Too small to label' text element. svg={svg!r}"
    assert "Too Small To Label" not in svg, (
        "Heading must never be title-cased — sentence case only."
    )
    heading_tag = m.group(0)

    assert f'font-family="{escape_attr(table_style.font.family)}"' in heading_tag, (
        "Heading must use the table's own body font family (never the chart "
        f"title font). Got: {heading_tag!r}"
    )
    assert f'font-size="{int(table_style.font.size)}"' in heading_tag, (
        f"Heading must match the table body font size. Got: {heading_tag!r}"
    )
    assert f'font-weight="{table_style.header.font.weight}"' in heading_tag, (
        "Heading weight must be the header's weight — heavier than plain "
        f"body rows. Got: {heading_tag!r}"
    )


def test_resolve_hybrid_heading_style_reads_table_body_and_header_weight():
    """Unit-level pin of the style-source contract: family/size come from
    the table's own body font, weight from the header's (heavier) weight —
    never a hardcoded literal, always read off the resolved style passed in."""
    rs, ctx = resolve_style_and_context(get_theme_style())
    table_style = ctx.table

    heading_font = resolve_hybrid_heading_font(table_style)

    assert heading_font.family == table_style.font.family
    assert heading_font.size == table_style.font.size
    assert heading_font.weight == table_style.header.font.weight


def test_resolve_hybrid_heading_style_applies_static_color_override():
    """A table's style.color.static override must reach the hybrid heading
    color, not just the table body — regression for the migration that
    changed TableChartStyle.color from a bare str to StaticGradientColorStyle,
    which silently broke the old `isinstance(tc.color, str)` extraction."""
    rs, ctx = resolve_style_and_context(get_theme_style())
    table_style = ctx.table.model_copy(
        update={"color": StaticGradientColorStyle(static="#ff0000", gradient=None)}
    )

    heading_font = resolve_hybrid_heading_font(table_style)

    assert heading_font.color == "#ff0000"


def test_full_table_render_has_no_labels_no_title_lists_every_slice(make_chart):
    """Genuine starvation (ratio < T, here from 8 long-named near-equal
    slices) drops every wheel label and lists ALL slices in the table with
    NO title — even though one slice is also below the 2% invisible-slice
    floor. Starvation, not the sub-2% guard, decides this mode."""
    chart = make_chart("pie", x="series", y="value")
    data = [
        {"series": n, "value": v}
        for n, v in zip(
            _GENUINE_STARVATION_NAMES, _GENUINE_STARVATION_VALUES, strict=True
        )
    ]
    rs, ctx = resolve_style_and_context(get_theme_style())

    svg = render_chart(chart, rs, ctx, data, format="svg", width=300.0)
    donut_text, table_text = _donut_and_table_texts(svg)

    assert donut_text == "", (
        f"Full-table wheel must carry no labels. Got: {donut_text!r}"
    )
    assert "too small to label" not in table_text.lower(), (
        f"Full-table has no title — it IS the whole legend. Got: {table_text!r}"
    )
    for name in _GENUINE_STARVATION_NAMES:
        assert name in table_text, f"Full table must list every slice. Missing {name!r}"


def test_full_table_two_row_donut_has_no_stripe(make_chart):
    """A 2-row full_table breakdown (two near-equal slices with long,
    two-line labels — the plan-mix-donut shape) must not zebra-stripe.

    The attached table is a wedge legend, not a dense data table: striping
    a 2-row key reads as if the second entry were selected or somehow
    different in kind from the first, not as row-tracking texture."""
    chart = make_chart("pie", x="series", y="value")
    data = [
        {"series": "Enterprise Annual", "value": 52},
        {"series": "Mid-Market Annual", "value": 48},
    ]
    rs, ctx = resolve_style_and_context(get_theme_style())

    # Positive control. This fixture reaches full_table by the solved wheel
    # falling under the dominance ratio at this width, so a font-metric refresh
    # could flip it to `direct` -- no table drawn, no rects emitted, and the
    # stripe assertion below would pass green while checking nothing.
    resolved = resolve(chart, data, chart_style_context=ctx, width=500.0)
    assert isinstance(resolved, ResolvedPieChart)
    assert sized_pie_mode(resolved, data, rs) == "full_table", (
        "Fixture no longer reaches full_table mode -- the stripe assertion "
        "below would be vacuous. Re-tune the fixture, don't delete this."
    )
    assert resolved.full_attachment is not None
    assert resolved.full_attachment.table.style.table.row.stripe.color is None

    svg = render_chart(chart, rs, ctx, data, format="svg", width=500.0)

    row_h = get_theme_style().charts.table.row.height
    stripe_like = [
        (x, y, w, h, fill)
        for x, y, w, h, fill in re.findall(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" '
            r'height="([\d.]+)" fill="(#[0-9a-fA-F]{6,8})"',
            svg,
        )
        if abs(float(h) - row_h) < 1
    ]
    assert not stripe_like, (
        f"Donut attached table must not render row stripes; found {stripe_like}"
    )


def test_standalone_table_still_stripes(make_chart):
    """The opt-out must be scoped to the attached table, not to striping.

    This is the contrast that proves the fix landed in the right place: the
    donut's wedge key stops striping while an ordinary `type: table` on the
    same theme keeps it. Without this, a future "simplification" that drops
    the theme's stripe entirely, or gates it inside the table engine, would leave the
    donut test green and silently flatten every real data table.
    """
    stripe = get_theme_style().charts.table.row.stripe
    assert stripe is not None and stripe.color is not None, (
        "The default theme must still declare a table row stripe -- if this "
        "fails, striping was removed globally rather than opted out of."
    )


def test_render_attached_table_mode_keeps_standard_arc_height(make_chart):
    """Composed SVG never balloons toward a huge square disk on a wide card.

    A full-width pie's label-free arc renders much shorter than its width.
    At 1200px the wheel region comfortably clears the right-placement
    thresholds, so the table sits beside the donut (not stacked below it) —
    the mechanism that keeps this composed card short in the first place.
    """
    chart = make_chart("pie", x="series", y="value")
    data = [{"series": f"S{i}", "value": v} for i, v in enumerate(_HYBRID_TAIL_VALUES)]
    rs, ctx = resolve_style_and_context(get_theme_style())

    svg = render_chart(chart, rs, ctx, data, format="svg", width=1200.0)
    m_w = re.search(r'<svg[^>]+\bwidth="([0-9.]+)"', svg)
    m_h = re.search(r'<svg[^>]+\bheight="([0-9.]+)"', svg)
    assert m_w and m_h, "SVG root must have width and height attributes"
    w, h = float(m_w.group(1)), float(m_h.group(1))
    assert h < w, (
        f"Composed height ({h}) should never balloon to a square disk "
        f"against the composed width ({w})."
    )


def test_direct_label_mode_omits_attached_table(make_chart):
    """When trigger says direct labels (e.g. 3 wedges at narrow), the
    composed render path doesn't fire — output is a plain Vega donut SVG
    with NO attached-table rects.
    """
    chart = make_chart("pie", x="series", y="value")
    data = [
        {"series": "Big", "value": 60},
        {"series": "Med", "value": 25},
        {"series": "Sml", "value": 15},
    ]
    rs, ctx = resolve_style_and_context(get_theme_style())

    svg = render_chart(chart, rs, ctx, data, format="svg", width=415.0)
    # No swatch rects from the attached table.
    swatch_rects = re.findall(
        r'<rect[^>]*\brx="3"[^>]*\bfill="#[a-fA-F0-9]+"[^>]*/?>',
        svg,
    )
    assert len(swatch_rects) == 0, (
        f"Direct-label mode must not emit attached-table swatch rects. "
        f"Got {len(swatch_rects)}: {swatch_rects[:2]}..."
    )


def test_high_cardinality_dominant_wheel_keeps_direct_labels(make_chart):
    """8 equal short-named slices at 700px used to force a table under the
    old callout-count-ceiling rule (visible count 8 > medium max 4). Under
    wheel-dominance, short names don't starve the wheel — direct labels win.
    """
    chart = make_chart("pie", x="series", y="value")
    data = [{"series": f"S{i}", "value": 100} for i in range(8)]
    rs, ctx = resolve_style_and_context(get_theme_style())

    svg = render_chart(chart, rs, ctx, data, format="svg", width=700.0)

    swatch_rects = re.findall(
        r'<rect[^>]*\brx="3"[^>]*\bfill="#[a-fA-F0-9]+"[^>]*/?>',
        svg,
    )
    assert len(swatch_rects) == 0, (
        f"High cardinality alone must not force the table when the wheel "
        f"stays dominant. Got {len(swatch_rects)} swatch rects."
    )


def test_attached_table_uses_chart_format_not_hardcoded_currency(make_chart):
    """Value column reads its format from the chart, not a hardcoded ``$,.0f``.

    Regression: an earlier draft of the dispatcher always rendered values
    as ``$N`` — a donut over non-currency data (sessions, page views,
    ratings count) would silently paint a $ prefix. The format must come
    from the chart's declared format hint.
    """
    chart = make_chart("pie", x="series", y="value")
    chart.format = None  # author declared no format → expect plain integers
    data = [{"series": f"S{i}", "value": v} for i, v in enumerate(_HYBRID_TAIL_VALUES)]
    rs, ctx = resolve_style_and_context(get_theme_style())

    svg = render_chart(chart, rs, ctx, data, format="svg", width=1200.0)
    # No $ sign should appear in the value column when chart has no format.
    dollar_texts = re.findall(r">[\s]*\$[\s]*<", svg)
    assert not dollar_texts, (
        "Attached table forced a $ prefix on a chart with no currency "
        f"format. Found {len(dollar_texts)} dollar text node(s)."
    )


def test_attached_table_preserves_format_config_prefix_suffix(make_chart):
    """A FormatConfig with prefix/suffix flows through to the value
    column unchanged — same regression class as the hardcoded-``$`` bug,
    just one authoring shape deeper.

    Chart declares ``format: {spec: ",.0f", prefix: "$", suffix: " USD"}``;
    the rendered SVG must contain "USD" suffix text.
    """
    chart = make_chart("pie", x="series", y="value")
    chart.format = FormatConfig(spec=",.0f", prefix="$", suffix=" USD")
    data = [{"series": f"S{i}", "value": v} for i, v in enumerate(_HYBRID_TAIL_VALUES)]
    rs, ctx = resolve_style_and_context(get_theme_style())

    svg = render_chart(chart, rs, ctx, data, format="svg", width=1200.0)
    # The suffix should appear in the rendered value column.
    usd_count = len(re.findall(r"USD", svg))
    assert usd_count >= 1, (
        f"FormatConfig.suffix was silently dropped — expected USD text in "
        f"the value column. Found {usd_count} occurrences."
    )


def test_arc_layer_not_filtered_by_wedge_threshold(make_chart):
    """The arc (slice) layer itself must NOT carry the share-based label
    filter — every wedge renders, only the labels are suppressed.
    """
    chart = make_chart("pie", x="series", y="value")
    data = [
        {"series": "Big", "value": 50},
        {"series": "Small", "value": 3},
    ]
    rs, ctx = resolve_style_and_context(get_theme_style())
    resolved = resolve(chart, data, chart_style_context=ctx, width=415.0)
    artifact = render_resolved_chart(resolved, data, rs, width=415.0, height=415.0)
    assert isinstance(artifact.payload, str)
    # Small (~5.7%) is under the 8% label floor but still paints its wedge.
    assert artifact.payload.count('aria-roledescription="arc mark"') == 2


def test_resolved_pie_uses_stored_width_when_render_width_is_omitted(make_chart):
    chart = make_chart("pie", x="series", y="value")
    data = [{"series": "Only", "value": 1}]
    rs, ctx = resolve_style_and_context(get_theme_style())
    resolved = resolve(chart, data, chart_style_context=ctx, width=415.0)

    artifact = render_resolved_chart(resolved, data, rs)

    assert artifact.kind == "svg"
    assert isinstance(artifact.payload, str)
    assert re.match(r'<svg [^>]*width="415(\.0)?"', artifact.payload)


def test_resolved_pie_rejects_conflicting_render_width(make_chart):
    chart = make_chart("pie", x="series", y="value")
    data = [{"series": "Only", "value": 1}]
    rs, ctx = resolve_style_and_context(get_theme_style())
    resolved = resolve(chart, data, chart_style_context=ctx, width=415.0)

    with pytest.raises(RenderError, match="finalized at width 415.0, not 500.0"):
        render_resolved_chart(resolved, data, rs, width=500.0)


# --------------------------------------------------------------------------
# Placement: table RIGHT of the wheel (wide cards) vs BELOW (narrow cards)
# --------------------------------------------------------------------------


def test_choose_table_placement_right_when_wheel_region_stays_dominant():
    """A wide card with a modest table leaves a comfortably large wheel
    region — placement should be "right", decided purely from measured
    widths, not a fixed aspect-ratio literal."""
    assert choose_table_placement(900.0, 200.0, None) == ("right", None)


def test_choose_table_placement_bottom_when_wheel_region_would_starve():
    """A narrow card can't spare half its width plus the absolute floor for
    the wheel once the table is reserved — placement falls back to
    "bottom"."""
    assert choose_table_placement(340.0, 200.0, None) == ("bottom", None)


def test_choose_table_placement_boundary_uses_both_thresholds() -> None:
    """The fraction and absolute-pixel floors are independent ANDed
    conditions. width=1000, table_width=700 leaves a 288px region: it
    clears the absolute floor (288 >= 280) but not the fraction floor
    (288 < 0.5*1000=500) — placement must still fall back to "bottom"."""
    assert RIGHT_PLACEMENT_MIN_WIDTH_FRACTION == 0.5
    assert RIGHT_PLACEMENT_MIN_WIDTH_PX == 280.0
    assert choose_table_placement(1000.0, 700.0, None) == ("bottom", None)


@pytest.mark.parametrize("edge", ["top", "bottom"])
def test_authored_vertical_edge_ignores_the_width_heuristic(edge) -> None:
    assert choose_table_placement(900.0, 200.0, edge) == (edge, None)
    assert choose_table_placement(340.0, 200.0, edge) == (edge, None)


@pytest.mark.parametrize("edge", ["left", "right"])
def test_authored_side_edge_holds_while_the_wheel_region_fits(edge) -> None:
    assert choose_table_placement(900.0, 200.0, edge) == (edge, None)


@pytest.mark.parametrize("edge", ["left", "right"])
def test_authored_side_edge_falls_to_bottom_and_names_the_edge_when_too_narrow(
    edge,
) -> None:
    assert choose_table_placement(340.0, 200.0, edge) == ("bottom", edge)


def _group_offsets(svg: str) -> list[tuple[float, float]]:
    """Extract the (x, y) translate offsets of compose_attached_table_svg's
    two direct child <g> wrappers, in document order: donut group first,
    table group second. Only DIRECT children of the root <svg> — a hybrid
    heading nests its own <g translate(...)> inside the table group, which
    must not be mistaken for a third top-level group."""
    root = ET.fromstring(svg)
    offsets = []
    for g in root.findall(f"{_SVG_NS}g"):
        m = re.match(r"translate\(([-\d.]+), ([-\d.]+)\)", g.get("transform", ""))
        assert m, f"expected a translate(...) transform, got {g.get('transform')!r}"
        offsets.append((float(m.group(1)), float(m.group(2))))
    return offsets


def test_wide_tabled_pie_places_table_to_the_right(make_chart):
    """A wide card (900px) with a table-triggering distribution places the
    table beside the wheel rather than stacking beneath it — the composed
    card stays short instead of ballooning tall. The outer SVG spans the
    full card width (900px); the [donut | gap | table] group is LEFT-ANCHORED
    to the card edge (donut_x = 0), with the table beside the wheel
    (table_x > 0) and trailing whitespace filling the rest of the wide card.
    """
    chart = make_chart("pie", x="series", y="value")
    data = [{"series": f"S{i}", "value": v} for i, v in enumerate(_HYBRID_TAIL_VALUES)]
    rs, ctx = resolve_style_and_context(get_theme_style())

    svg = render_chart(chart, rs, ctx, data, format="svg", width=900.0)
    m_w = re.search(r'^<svg[^>]+\bwidth="([0-9.]+)"', svg)
    m_h = re.search(r'^<svg[^>]+\bheight="([0-9.]+)"', svg)
    assert m_w and m_h
    w, h = float(m_w.group(1)), float(m_h.group(1))
    assert w == 900.0, (
        "outer SVG must span the full card width; composed SVG = card_width"
    )

    offsets = _group_offsets(svg)
    assert len(offsets) == 2, f"expected donut + table groups, got {offsets}"
    (donut_x, _donut_y), (table_x, _table_y) = offsets
    assert donut_x == 0.0, (
        "in 'right' placement the content group is left-anchored to the card "
        f"edge; expected donut_x=0.0, got {donut_x}"
    )
    assert table_x > donut_x, (
        f"table must sit to the right of the wheel; got table_x={table_x}, "
        f"donut_x={donut_x}"
    )
    assert table_x > 0.5 * w, (
        f"the table should sit past the card's own midpoint; table_x={table_x} w={w}"
    )
    assert table_x <= donut_x + RIGHT_PLACEMENT_MAX_WHEEL_PX + _PIE_GAP, (
        f"the wheel region before the table must not exceed the cap; "
        f"table_x={table_x} donut_x={donut_x}"
    )
    # Side-by-side keeps the card short — well under a stacked donut+table
    # height, and nowhere near square.
    assert h < 0.6 * w, f"expected a short, wide card; got h={h} at w={w}"


def test_narrow_tabled_pie_places_table_below(make_chart):
    """The same table-triggering distribution at a narrow card (340px)
    keeps the table below the wheel, horizontally CENTERED beneath it
    (balanced margins, matching the plain direct-label pie) — there's no
    room for the wheel to survive a side-by-side split.
    """
    chart = make_chart("pie", x="series", y="value")
    data = [{"series": f"S{i}", "value": v} for i, v in enumerate(_HYBRID_TAIL_VALUES)]
    rs, ctx = resolve_style_and_context(get_theme_style())

    svg = render_chart(chart, rs, ctx, data, format="svg", width=340.0)
    m_w = re.search(r'^<svg[^>]+\bwidth="([0-9.]+)"', svg)
    assert m_w
    w = float(m_w.group(1))
    assert w == 340.0

    offsets = _group_offsets(svg)
    assert len(offsets) == 2, f"expected donut + table groups, got {offsets}"
    (donut_x, _donut_y), (table_x, table_y) = offsets
    assert donut_x == 0.0, (
        "the donut is the widest element, so it collapses to x=0 under the "
        f"max(donut_width, table_width) centering rule; donut_x={donut_x}"
    )
    assert table_x > 0.0, (
        "the narrower table must be centered (a positive left margin) "
        f"beneath the donut, not flushed left at x=0; table_x={table_x} w={w}"
    )
    assert table_y > 0, "table must sit beneath the donut, not overlapping it"


def test_attached_table_modes_suppress_builtin_color_legend(make_chart, model_copy_at):
    """Hybrid and full-table modes suppress the donut's built-in color legend —
    the attached table IS the legend, so on a theme that enables it
    (``legend.visible=True``) it must not double up (and, in hybrid, alongside
    the direct labels too). Direct-label pies keep the theme's legend untouched.

    No shipped theme defaults to a visible legend (every built-in suppresses
    it for direct labeling), so this forces the override on a real theme
    rather than depending on a theme's default posture.
    """
    themed_rs, themed_ctx = resolve_style_and_context(
        model_copy_at(get_theme_style("clarity"), "charts.legend.visible", True)
    )
    assert themed_ctx.legend.visible is True  # guards the premise below

    def render(pairs):
        chart = make_chart("pie", x="name", y="value")
        data = [{"name": n, "value": v} for n, v in pairs]
        return render_chart(
            chart, themed_rs, themed_ctx, data, format="svg", width=760.0
        )

    # Direct-label pie: dark keeps its built-in color legend (unchanged).
    assert "role-legend" in render([("A", 40), ("B", 35), ("C", 25)])
    # Hybrid (a sub-2% slice) and full-table (nothing clears the 8% floor):
    # the attached table replaces the legend, so no built-in legend is emitted.
    assert "role-legend" not in render(
        [
            ("Direct", 45),
            ("Organic", 28),
            ("Paid", 22),
            ("Referral", 3.5),
            ("Other", 1.5),
        ]
    )
    assert "role-legend" not in render([(f"C{i}", 6.67) for i in range(15)])


# --------------------------------------------------------------------------
# Composition: where the engine places the table against the wheel
# --------------------------------------------------------------------------

_ALIGN_ROWS = [
    {"segment": "Enterprise", "sessions": 40},
    {"segment": "Mid-Market", "sessions": 30},
    {"segment": "SMB", "sessions": 20},
    {"segment": "Self-serve", "sessions": 8},
    {"segment": "Partner", "sessions": 1.5},
    {"segment": "Other", "sessions": 0.5},
]
_MANY_ROWS = [{"segment": f"C{i}", "sessions": 6.67} for i in range(15)]


def _composed(
    edge: str, align: str, rows: list[dict[str, float | str]] = _ALIGN_ROWS
) -> tuple[str, list[tuple[float, float]]]:
    """An attached-table pie whose table sits on ``edge``, aligned ``align``,
    in a 1000px card: the SVG and its [wheel, table] group offsets."""
    chart = PieChart(
        id="t",
        type="pie",
        theta="sessions",
        color="segment",
        query_name="q",
        style={"legend": {"position": {"edge": edge, "align": align}}},
    )
    svg = render_pie_svg(chart, rows, width=1000.0)
    return svg, _group_offsets(svg)


def _wheel_center_y(svg: str) -> float:
    match = re.search(
        r'<g transform="translate\([\d.]+,([\d.]+)\)"><g class="mark-arc', svg
    )
    assert match is not None, svg
    return float(match.group(1))


def test_a_left_table_sits_before_the_wheel():
    _, [(wheel_x, _), (table_x, _)] = _composed("left", "start")
    assert table_x < wheel_x


def test_a_right_table_sits_after_the_left_anchored_wheel():
    svg, [(wheel_x, _), (table_x, _)] = _composed("right", "start")
    assert wheel_x == 0.0
    assert table_x > wheel_x
    assert re.match(r'<svg [^>]*width="1000(\.0)?"', svg)


def test_a_top_table_sits_above_the_wheel():
    svg, [_, (_, table_y)] = _composed("top", "center")
    assert table_y < _wheel_center_y(svg)


def test_a_bottom_table_sits_below_the_wheel():
    svg, [_, (_, table_y)] = _composed("bottom", "center")
    assert table_y > _wheel_center_y(svg)


@pytest.mark.parametrize("edge", ["top", "bottom"])
def test_a_vertical_table_aligns_across_the_card(edge: str) -> None:
    xs = [_composed(edge, align)[1][1][0] for align in ("start", "center", "end")]
    assert xs[0] < xs[1] < xs[2], xs


@pytest.mark.parametrize("edge", ["left", "right"])
def test_a_side_table_aligns_down_the_wheels_disk(edge: str) -> None:
    ys = [_composed(edge, align)[1][1][1] for align in ("start", "center", "end")]
    assert ys[0] < ys[1] < ys[2], ys


@pytest.mark.parametrize("align", ["center", "end"])
@pytest.mark.parametrize("edge", ["left", "right"])
def test_a_side_table_taller_than_the_disk_falls_back_to_the_disks_top(
    edge: str, align: str
) -> None:
    [(wheel_x, _), (table_x, start_y)] = _composed(edge, "start", _MANY_ROWS)[1]
    assert (table_x > wheel_x) == (edge == "right"), "the table must stay beside"
    assert _composed(edge, align, _MANY_ROWS)[1][1][1] == start_y


def test_a_hybrid_heading_heads_the_table():
    svg, _ = _composed("bottom", "center")
    _, table_text = _donut_and_table_texts(svg)
    assert table_text.startswith("Too small to label"), table_text
