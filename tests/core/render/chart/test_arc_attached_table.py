"""Trigger logic + series extraction for donut-attached tables.

Three-mode decision rule (wheel-dominance; locked with RJ — see decision D-03
of the wheel-first-pie-donut-labeling initiative):

- Empty shares: "direct" (placeholder path handled elsewhere).
- No slice clears ``chart_rendering.pie.wedge_label_min_share`` (8%): "full_table"
  (nothing would render a direct label anyway).
- Dominance ratio below ``chart_rendering.pie.wheel_dominance_min_ratio`` (0.6):
  "full_table" (labels would starve the wheel).
- Otherwise (dominant): "hybrid" when any slice is below
  ``chart_rendering.pie.invisible_slice_share`` (2%) — the wheel keeps its direct
  labels and a table lists every unlabeled (<= 8%) slice; else "direct" — wheel
  labels only, no table.
"""

from __future__ import annotations

import importlib
import re
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from dbt_charts.core.compile.config import get_chart_rendering, get_theme_style
from dbt_charts.core.compile.models.primitives import (
    FormatConfig,
    StaticGradientColorStyle,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart.pie_attachment import (
    choose_table_placement,
    classify_arc_render_mode,
    resolve_hybrid_heading_font,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.arc_attached_table import (
    compose_attached_table_svg,
)
from dbt_charts.core.render.chart.vega_lite import render_chart, render_resolved_chart
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.svg_utils import escape_attr

_SVG_NS = "{http://www.w3.org/2000/svg}"
_PIE_GAP = get_chart_rendering().pie.attached_table_gap_px


def _donut_and_table_texts(svg: str) -> tuple[str, str]:
    """Parse a composed donut+table SVG and return (donut_text, table_text) —
    the concatenated text content of each of the two top-level groups, in
    document order (donut first, table second). Lets a test assert exactly
    which labels/title live on the wheel vs. in the attached table."""
    root = ET.fromstring(svg)
    donut_group, table_group = root.findall(f"{_SVG_NS}g")

    def _joined(group: ET.Element) -> str:
        inner = group.find(f"{_SVG_NS}svg")
        assert inner is not None
        return "".join(
            "".join(t.itertext()) for t in inner.findall(f".//{_SVG_NS}text")
        )

    return _joined(donut_group), _joined(table_group)


def _shares(*pcts: float) -> list[float]:
    """Build a shares list summing to 1.0 from percentage inputs."""
    total = sum(pcts)
    return [p / total for p in pcts]


def _default_label_style() -> tuple[str, float, float]:
    """Return (font_family, font_size, label_offset) from the default theme's
    pie slice-label style — the same values the real render dispatch reads."""
    labels = resolve_chart_style_context(get_theme_style()).pie.marks.slice.labels
    assert labels.font.family is not None
    assert labels.font.size is not None
    return labels.font.family, labels.font.size, labels.offset


def _labeled_lines(shares: list[float], names: list[str]) -> list[str]:
    """Build one label line per visible (share above wedge_label_min_share) slice,
    mirroring the "{pct}% {name}" default template shape."""
    threshold = get_chart_rendering().pie.wedge_label_min_share
    return [
        f"{round(s * 100)}% {n}"
        for s, n in zip(shares, names, strict=True)
        if s > threshold
    ]


def test_empty_shares_returns_direct():
    """Empty shares list (zero-total data) returns "direct" so the dispatch
    misses and the standard renderer handles the placeholder case."""
    font_family, font_size, offset = _default_label_style()
    assert (
        classify_arc_render_mode([], [], font_family, font_size, offset, 600.0)
        == "direct"
    )


def test_all_slices_below_label_floor_is_full_table():
    """Degenerate case: every slice is at or under the 8% label floor, so no
    direct label would render at all. Falls back to the full table (nothing
    to keep on the wheel)."""
    font_family, font_size, offset = _default_label_style()
    shares = _shares(*([7.7] * 13))
    assert (
        classify_arc_render_mode(shares, [], font_family, font_size, offset, 600.0)
        == "full_table"
    )


def test_wheel_dominant_five_slice_is_direct():
    """A clean 5-slice pie at the 600px default keeps direct labels — the
    dominance ratio stays comfortably above threshold.

    Committed calibration point (the ceiling-based trigger this replaces
    used to table this exact shape at 600px; the wheel-dominance measure
    correctly keeps it labeled).
    """
    font_family, font_size, offset = _default_label_style()
    names = ["North America", "Europe", "Asia Pacific", "Latin America", "Other"]
    shares = _shares(35, 25, 20, 12, 8)
    label_lines = _labeled_lines(shares, names)
    assert (
        classify_arc_render_mode(
            shares, label_lines, font_family, font_size, offset, 600.0
        )
        == "direct"
    )


def test_starved_narrow_width_is_full_table():
    """8 equal long-named slices at 300px starve the wheel below T=0.6.

    Committed calibration point locked with RJ.
    """
    font_family, font_size, offset = _default_label_style()
    names = [
        "Enterprise",
        "Mid-Market",
        "SMB",
        "Self-serve",
        "Channel",
        "Partners",
        "Education",
        "Healthcare",
    ]
    shares = _shares(*([1.0] * 8))
    label_lines = _labeled_lines(shares, names)
    assert (
        classify_arc_render_mode(
            shares, label_lines, font_family, font_size, offset, 300.0
        )
        == "full_table"
    )


def test_wider_width_same_data_is_direct():
    """The same 8 equal long-named slices at 440px clear T=0.6 — the extra
    100px is enough for labels to stop starving the wheel.

    Committed calibration point locked with RJ.
    """
    font_family, font_size, offset = _default_label_style()
    names = [
        "Enterprise",
        "Mid-Market",
        "SMB",
        "Self-serve",
        "Channel",
        "Partners",
        "Education",
        "Healthcare",
    ]
    shares = _shares(*([1.0] * 8))
    label_lines = _labeled_lines(shares, names)
    assert (
        classify_arc_render_mode(
            shares, label_lines, font_family, font_size, offset, 440.0
        )
        == "direct"
    )


def test_wedge_threshold_config_is_eight_percent() -> None:
    """Pin the config default so accidental edits to the threshold show up
    here. The number is a design call, not a theme value."""
    assert get_chart_rendering().pie.wedge_label_min_share == 0.08


def test_invisible_slice_threshold_config_is_two_percent() -> None:
    """Pin the physical-visibility fallback separately from label suppression."""
    assert get_chart_rendering().pie.invisible_slice_share == 0.02


def test_wheel_dominance_min_ratio_config_is_point_six() -> None:
    """Pin T=0.6, locked with RJ against the calibration sweep."""
    assert get_chart_rendering().pie.wheel_dominance_min_ratio == 0.6


def test_threshold_boundary_is_strictly_greater():
    """Slices at exactly 8% are suppressed (count as NOT visible). The
    rule is "share > 8%", not "share >= 8%"."""
    font_family, font_size, offset = _default_label_style()
    # Only the 68% wedge qualifies as visible (>8%); the rest sit at exactly
    # 8% and are suppressed. A single dominant label leaves the wheel intact.
    shares = [0.68, 0.08, 0.08, 0.08, 0.08]
    label_lines = ["68% Big"]
    assert (
        classify_arc_render_mode(
            shares, label_lines, font_family, font_size, offset, 700.0
        )
        == "direct"
    )


# --------------------------------------------------------------------------
# The three worked cases (RJ), locked in the task spec as regression tests
# --------------------------------------------------------------------------


def test_worked_case_single_six_percent_slice_is_direct():
    """One 6% slice, no sub-2% tail: stays direct — the 6% wedge renders
    unlabeled on the wheel (hover only), no table at all."""
    font_family, font_size, offset = _default_label_style()
    names = ["Big", "Rest", "Small"]
    shares = _shares(70, 24, 6)
    label_lines = _labeled_lines(shares, names)
    assert (
        classify_arc_render_mode(
            shares, label_lines, font_family, font_size, offset, 600.0
        )
        == "direct"
    )


def test_worked_case_single_one_percent_slice_is_hybrid():
    """One 1% slice: hybrid — the >8% wedges keep their direct labels, the
    1% is listed in the attached table."""
    font_family, font_size, offset = _default_label_style()
    names = ["Big", "Rest", "Tiny"]
    shares = _shares(70, 29, 1)
    label_lines = _labeled_lines(shares, names)
    assert (
        classify_arc_render_mode(
            shares, label_lines, font_family, font_size, offset, 600.0
        )
        == "hybrid"
    )


def test_worked_case_six_and_one_percent_slices_is_hybrid():
    """A 6% AND a 1% slice: hybrid — both are <= 8% so both land in the
    table; the >8% wedges stay labeled on the wheel."""
    font_family, font_size, offset = _default_label_style()
    names = ["Big", "Rest", "Small", "Tiny"]
    shares = _shares(63, 30, 6, 1)
    label_lines = _labeled_lines(shares, names)
    assert (
        classify_arc_render_mode(
            shares, label_lines, font_family, font_size, offset, 600.0
        )
        == "hybrid"
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
    assert artifact.kind == "vega_spec"
    spec = artifact.payload

    # Label layer is the one with mark.type == 'text'. There may be
    # multiple text layers (center total, etc.); find the per-wedge one
    # whose text encoding reads the per-row label field.
    label_layers = [
        layer
        for layer in spec.get("layer", [])
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") == "__dbt_label"
    ]
    assert label_layers, "Expected a per-wedge label layer with text field __dbt_label"
    transforms = label_layers[0].get("transform", [])
    filters = [t.get("filter") for t in transforms if "filter" in t]
    assert filters == ["datum.__dbt_label != null"]


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
    arc_block_match = re.search(
        r'<g[^>]*\bclass="mark-arc role-mark[^"]*"[^>]*>(.*?)</g>', svg, re.DOTALL
    )
    assert arc_block_match, "Expected an arc mark-group in the donut SVG"
    arc_block = arc_block_match.group(1)
    wedge_fills_in_draw_order = [
        m.group(1).lower()
        for m in re.finditer(r'<path[^>]*\bfill="(#[a-fA-F0-9]+)"', arc_block)
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


def test_compose_attached_table_svg_heading_draws_text_and_grows_table_height():
    """An optional heading (hybrid mode) draws a single text line above the
    table using the caller-supplied style — and grows the effective table
    height so both placements' layout math accounts for it."""
    composed, _outer_w, outer_h = compose_attached_table_svg(
        donut_svg="<svg>DONUT</svg>",
        table_svg="<svg>TABLE</svg>",
        donut_width=400.0,
        donut_height=400.0,
        table_width=300.0,
        table_height=100.0,
        card_width=400.0,
        gap=_PIE_GAP,
        heading_gap=get_chart_rendering().pie.hybrid_heading_gap_px,
        heading="Too small to label",
        heading_font_family="Sans",
        heading_font_size=11.0,
        heading_font_weight="600",
        heading_color="#111111",
    )
    assert "Too small to label" in composed
    assert 'font-family="Sans"' in composed
    assert 'font-weight="600"' in composed
    assert 'fill="#111111"' in composed
    assert outer_h > 400.0 + _PIE_GAP + 100.0, (
        "outer height must grow to make room for the heading line"
    )


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

    # Positive control. This fixture reaches full_table by clearing a MEASURED
    # label-width threshold, so a font-metric refresh could flip it to `direct`
    # -- no table built, no rects emitted, and the stripe assertion below would
    # pass green while checking nothing. Assert on the resolved model, which is
    # the brittleness-free half: the opt-out itself, not its rendered absence.
    resolved = resolve(chart, data, chart_style_context=ctx)
    assert resolved.attached_table is not None, (
        "Fixture no longer reaches full_table mode -- the stripe assertion "
        "below would be vacuous. Re-tune the fixture, don't delete this."
    )
    assert resolved.attached_table.style.table.row.stripe.color is None

    svg = render_chart(chart, rs, ctx, data, format="svg", width=600.0)

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
    the theme's stripe entirely, or gates it inside table.py, would leave the
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
    assert artifact.kind == "vega_spec"
    spec = artifact.payload
    arc_layers = [
        layer
        for layer in spec.get("layer", [])
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "arc"
    ]
    assert arc_layers, "Expected at least one arc mark layer"
    transforms = arc_layers[0].get("transform", []) or []
    filters = [t.get("filter") for t in transforms if "filter" in t]
    for f in filters:
        assert not (isinstance(f, str) and "__dbt_pct" in f and "0.08" in f), (
            "Arc layer must not be filtered by wedge-label threshold. "
            f"Got filter: {f!r}"
        )


def test_resolved_pie_uses_stored_width_when_render_width_is_omitted(make_chart):
    chart = make_chart("pie", x="series", y="value")
    data = [{"series": "Only", "value": 1}]
    rs, ctx = resolve_style_and_context(get_theme_style())
    resolved = resolve(chart, data, chart_style_context=ctx, width=415.0)

    artifact = render_resolved_chart(resolved, data, rs)

    assert artifact.kind == "vega_spec"


def test_resolved_pie_rejects_conflicting_render_width(make_chart):
    chart = make_chart("pie", x="series", y="value")
    data = [{"series": "Only", "value": 1}]
    rs, ctx = resolve_style_and_context(get_theme_style())
    resolved = resolve(chart, data, chart_style_context=ctx, width=415.0)

    with pytest.raises(RenderError, match="finalized at width 415.0, not 500.0"):
        render_resolved_chart(resolved, data, rs, width=500.0)


def test_resolved_pie_render_does_not_reread_geometry_config(make_chart, monkeypatch):
    chart = make_chart("pie", x="series", y="value")
    data = [
        {"series": f"S{i}", "value": value}
        for i, value in enumerate(_HYBRID_TAIL_VALUES)
    ]
    rs, ctx = resolve_style_and_context(get_theme_style())
    resolved = resolve(chart, data, chart_style_context=ctx, width=1200.0)
    before = render_resolved_chart(resolved, data, rs)

    changed_config = SimpleNamespace(
        pie=SimpleNamespace(
            outer_fraction=0.1,
            attached_table_gap_px=200.0,
            hybrid_heading_gap_px=100.0,
        )
    )
    pie_emitter = importlib.import_module("dbt_charts.core.render.chart.emitters.pie")
    attachment = importlib.import_module(
        "dbt_charts.core.render.chart.arc_attached_table"
    )
    monkeypatch.setattr(
        pie_emitter, "get_chart_rendering", lambda: changed_config, raising=False
    )
    monkeypatch.setattr(
        attachment, "get_chart_rendering", lambda: changed_config, raising=False
    )

    after = render_resolved_chart(resolved, data, rs)

    assert after == before


# --------------------------------------------------------------------------
# Placement: table RIGHT of the wheel (wide cards) vs BELOW (narrow cards)
# --------------------------------------------------------------------------


def test_choose_table_placement_right_when_wheel_region_stays_dominant():
    """A wide card with a modest table leaves a comfortably large wheel
    region — placement should be "right", decided purely from measured
    widths, not a fixed aspect-ratio literal."""
    assert choose_table_placement(width=900.0, table_width=200.0) == "right"


def test_choose_table_placement_below_when_wheel_region_would_starve():
    """A narrow card can't spare half its width plus the absolute floor for
    the wheel once the table is reserved — placement falls back to
    "below"."""
    assert choose_table_placement(width=340.0, table_width=200.0) == "below"


def test_choose_table_placement_boundary_uses_both_thresholds() -> None:
    """The fraction and absolute-pixel floors are independent ANDed
    conditions. width=1000, table_width=700 leaves a 288px region: it
    clears the absolute floor (288 >= 280) but not the fraction floor
    (288 < 0.5*1000=500) — placement must still fall back to "below"."""
    pie_cfg = get_chart_rendering().pie
    assert pie_cfg.right_placement_min_width_fraction == 0.5
    assert pie_cfg.right_placement_min_width_px == 280.0
    assert choose_table_placement(width=1000.0, table_width=700.0) == "below"


def test_compose_attached_table_svg_right_placement_left_anchored_in_card_width():
    """placement="right" with a card_width wider than the tight group produces
    an outer SVG spanning the full card_width, with the [donut | gap | table]
    content group LEFT-ANCHORED to the card's left edge (donut_x = 0) — content
    flows from the left like every other dbt charts card, with trailing whitespace
    on the right, rather than floating as a centered island in the slot."""
    donut_w, table_w, gap = 400.0, 200.0, _PIE_GAP
    card_width = 800.0

    composed, outer_w, outer_h = compose_attached_table_svg(
        donut_svg="<svg>DONUT</svg>",
        table_svg="<svg>TABLE</svg>",
        donut_width=donut_w,
        donut_height=400.0,
        table_width=table_w,
        table_height=200.0,
        card_width=card_width,
        gap=gap,
        heading_gap=get_chart_rendering().pie.hybrid_heading_gap_px,
        placement="right",
    )
    assert outer_w == card_width, (
        "outer SVG must span the full card width so the composed element "
        "fills the board slot rather than shrinking to content"
    )
    offsets = _group_offsets(composed)
    assert len(offsets) == 2
    (donut_x, _), (table_x, _) = offsets
    assert donut_x == 0.0, (
        f"donut must be left-anchored to the card edge; expected 0.0, got {donut_x}"
    )
    assert table_x == donut_w + gap, (
        f"table must sit at donut + gap from the left edge; expected "
        f"{donut_w + gap}, got {table_x}"
    )


def test_compose_attached_table_svg_right_placement_is_side_by_side():
    """placement="right" puts the table beside the donut (table x-offset at
    donut_width + gap from the left edge). The donut pins to (0, 0); the table
    TOP-ALIGNS with the WHEEL'S DISK, not the card — its y-offset is the disk's
    top within the donut SVG, (donut_height - disk_diameter) / 2 — so a short
    table sits beside the disk (not floating above it) and a tall one grows
    downward from the disk's top. Outer width expands to card_width; content is
    left-anchored."""
    donut_w, table_w, gap = 500.0, 200.0, _PIE_GAP
    card_width = 800.0
    # donut SVG carries a disk of diameter 400 (arc radius 200), so the disk
    # top sits (500 - 400) / 2 = 50px below the SVG top.
    donut_svg = '<g class="mark-arc role-mark"><path d="M0,0 A200,200 0 0 1 1,1"/></g>'
    composed, outer_w, outer_h = compose_attached_table_svg(
        donut_svg=donut_svg,
        table_svg="<svg>TABLE</svg>",
        donut_width=donut_w,
        donut_height=500.0,
        table_width=table_w,
        table_height=120.0,
        card_width=card_width,
        gap=gap,
        heading_gap=get_chart_rendering().pie.hybrid_heading_gap_px,
        placement="right",
    )
    assert outer_w == card_width
    assert outer_h == 500.0  # max(donut_height, disk_top + table_height)
    offsets = _group_offsets(composed)
    assert offsets == [
        (0.0, 0.0),  # donut left-anchored, pinned to the top
        (donut_w + gap, 50.0),  # table beside the donut, top-aligned to the disk top
    ]


def test_compose_attached_table_svg_below_placement_is_default_and_centered():
    """placement defaults to "below" and keeps the stacked geometry (outer
    height is donut + gap + table); both the donut and the table are
    horizontally CENTERED within card_width — balanced left/right margins,
    matching the plain direct-label pie's layout in the board slot."""
    card_width = 500.0
    composed, outer_w, outer_h = compose_attached_table_svg(
        donut_svg="<svg>DONUT</svg>",
        table_svg="<svg>TABLE</svg>",
        donut_width=400.0,
        donut_height=400.0,
        table_width=300.0,
        table_height=100.0,
        card_width=card_width,
        gap=_PIE_GAP,
        heading_gap=get_chart_rendering().pie.hybrid_heading_gap_px,
    )
    assert outer_w == card_width  # outer SVG spans the full card slot
    assert outer_h == 400.0 + _PIE_GAP + 100.0
    offsets = _group_offsets(composed)
    # donut centered: (500 - 400) / 2 = 50; table centered: (500 - 300) / 2 = 100
    assert offsets == [
        ((card_width - 400.0) / 2, 0.0),
        ((card_width - 300.0) / 2, 400.0 + _PIE_GAP),
    ]


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
    assert (
        table_x
        <= donut_x + get_chart_rendering().pie.right_placement_max_wheel_px + _PIE_GAP
    ), (
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
