"""TDD tests: MarkStyle fields that must flow from model → VL mark output.

Tests cover the unit-level mappers and end-to-end spec generation for:
- line_mark_to_vl: stroke.color → mark.stroke, stroke.dasharray → mark.strokeDash
- area_mark_to_vl: stroke.color → mark.stroke, stroke.dasharray → mark.strokeDash
- _map_line halo path: stroke.color reaches the foreground line layer
- _map_line halo path: point.color/shape reach the foreground point layer
- _map_area halo path: stroke.color reaches the foreground area.line sub-object
- layered chart line sub-layer: point.color emitted as separate layer with value encoding
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import (
    get_chart_rendering,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.style.theme import PointMarkStyle
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec
from dbt_charts.core.render.chart.vl_field_maps import (
    area_mark_to_vl,
    bar_mark_to_vl,
    line_mark_to_vl,
    scatter_mark_to_vl,
)


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


SAMPLE_DATA = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
MULTI_SERIES_DATA = [
    {"month": "Jan", "revenue": 100, "segment": "a"},
    {"month": "Feb", "revenue": 200, "segment": "a"},
    {"month": "Jan", "revenue": 50, "segment": "b"},
    {"month": "Feb", "revenue": 90, "segment": "b"},
]


def _base_line_mark():
    """Return the compiled global LineMarkStyle from the default theme."""
    return get_theme_style("clarity").charts.marks.line


def _base_area_mark():
    """Return the compiled global AreaMarkStyle from the default theme."""
    return get_theme_style("clarity").charts.marks.area


def _base_bar_mark():
    """Return the compiled global BarMarkStyle from the default theme."""
    return get_theme_style("clarity").charts.marks.bar


# ---------------------------------------------------------------------------
# Unit tests — vl_field_maps mappers
# ---------------------------------------------------------------------------


class TestLineMark:
    def test_stroke_color_emitted_as_stroke(self):
        mark = _base_line_mark().model_copy(
            update={
                "stroke": _base_line_mark().stroke.model_copy(
                    update={"color": "#ff0000"}
                )
            }
        )
        result = line_mark_to_vl(mark)
        assert result["stroke"] == "#ff0000"

    def test_stroke_color_absent_not_emitted(self):
        mark = _base_line_mark()
        # Default theme line mark has no stroke.color — must not appear.
        result = line_mark_to_vl(mark)
        assert "stroke" not in result

    def test_stroke_dasharray_emitted_as_strokeDash(self):
        mark = _base_line_mark().model_copy(
            update={
                "stroke": _base_line_mark().stroke.model_copy(
                    update={"dasharray": "4 2"}
                )
            }
        )
        result = line_mark_to_vl(mark)
        assert result["strokeDash"] == [4.0, 2.0]

    def test_stroke_dasharray_absent_not_emitted(self):
        mark = _base_line_mark()
        result = line_mark_to_vl(mark)
        assert "strokeDash" not in result


class TestAreaMark:
    def test_stroke_color_emitted_as_stroke(self):
        from dbt_charts.core.compile.models.primitives import StrokeStyle

        mark = _base_area_mark().model_copy(
            update={"stroke": StrokeStyle(color="#00ff00", width=2.0)}
        )
        result = area_mark_to_vl(mark)
        assert result["stroke"] == "#00ff00"

    def test_stroke_color_absent_not_emitted(self):
        mark = _base_area_mark()
        result = area_mark_to_vl(mark)
        assert "stroke" not in result

    def test_stroke_dasharray_emitted_as_strokeDash(self):
        from dbt_charts.core.compile.models.primitives import StrokeStyle

        mark = _base_area_mark().model_copy(
            update={"stroke": StrokeStyle(color=None, width=2.0, dasharray="8 4")}
        )
        result = area_mark_to_vl(mark)
        assert result["strokeDash"] == [8.0, 4.0]

    def test_stroke_dasharray_absent_not_emitted(self):
        mark = _base_area_mark()
        result = area_mark_to_vl(mark)
        assert "strokeDash" not in result


class TestBarMark:
    def test_opacity_emits_fill_opacity_not_whole_mark_opacity(self):
        # BarMarkStyle.opacity is documented as fill opacity. VL's top-level
        # `opacity` multiplies fill AND stroke together, which would hide an
        # authored `border` right along with the fill on an opacity:0 bar
        # (an outline-only bar needs the two isolated) -- `fillOpacity` is the
        # native VL property that does that, leaving stroke at its own opacity.
        mark = _base_bar_mark().model_copy(update={"opacity": 0.85})
        result = bar_mark_to_vl(mark, "vertical", True)
        assert result["fillOpacity"] == 0.85
        assert "opacity" not in result

    def test_opacity_absent_not_emitted(self):
        # Default theme bar mark has no opacity override — must not appear.
        # This is the untested-by-default path this field previously had no
        # coverage for at all (there was no opacity field to test).
        mark = _base_bar_mark()
        result = bar_mark_to_vl(mark, "vertical", True)
        assert "fillOpacity" not in result
        assert "opacity" not in result


# ---------------------------------------------------------------------------
# Helpers to build board overrides (mirrors pattern from test_per_chart_style_promotions)
# ---------------------------------------------------------------------------


def _board_with_global_line_stroke(**stroke_overrides):
    compiled = get_theme_style("clarity")
    line_mark = compiled.charts.marks.line
    new_stroke = line_mark.stroke.model_copy(update=stroke_overrides)
    new_line_mark = line_mark.model_copy(update={"stroke": new_stroke})
    new_marks = compiled.charts.marks.model_copy(update={"line": new_line_mark})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _board_with_global_area_stroke(**stroke_overrides):
    """Override the top-edge stroke a multi-series (overlap-recipe) area
    chart shares with the line family.

    A colorless/layerless area now takes the stacked recipe wholesale (see
    ``_board_with_stacked_area_stroke``), so this override only reaches a
    chart's top edge when the chart is genuinely multi-series (color/wide
    y/layers) and stays on the overlap recipe. Area's top-edge stroke there
    is a genuine separate line mark (Vega-Lite itself compiles it that
    way) — its geometry lives on the global marks.line tier, not marks.area
    (fill-only: opacity/curve).
    """
    compiled = get_theme_style("clarity")
    line_mark = compiled.charts.marks.line
    new_stroke = line_mark.stroke.model_copy(update=stroke_overrides)
    new_line_mark = line_mark.model_copy(update={"stroke": new_stroke})
    new_marks = compiled.charts.marks.model_copy(update={"line": new_line_mark})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _board_no_halo_line(**stroke_overrides):
    """Line board with halo_multiplier=0 so spec uses a single mark dict."""
    compiled = get_theme_style("clarity")
    line_mark = compiled.charts.marks.line
    new_stroke = line_mark.stroke.model_copy(update=stroke_overrides)
    new_line_mark = line_mark.model_copy(
        update={"stroke": new_stroke, "halo_multiplier": 0.0}
    )
    new_marks = compiled.charts.marks.model_copy(update={"line": new_line_mark})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _board_with_stacked_area_stroke(**stroke_overrides):
    """Override the top-edge separator stroke a colorless/layerless area
    chart takes via the stacked recipe.

    A single-series area is now treated like a stack: it
    routes its edge stroke through marks.area.stacked.stroke with
    halo_multiplier already 0.0 by default, not the global marks.line tier
    the overlap recipe uses. The spec emits a single line mark for the
    edge (there is no halo layer to switch off).
    """
    compiled = get_theme_style("clarity")
    stacked = compiled.charts.marks.area.stacked
    assert stacked is not None
    assert stacked.stroke is not None
    new_stroke = stacked.stroke.model_copy(update=stroke_overrides)
    new_stacked = stacked.model_copy(update={"stroke": new_stroke})
    new_area_mark = compiled.charts.marks.area.model_copy(
        update={"stacked": new_stacked}
    )
    new_marks = compiled.charts.marks.model_copy(update={"area": new_area_mark})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


# ---------------------------------------------------------------------------
# End-to-end: no-halo path (halo_multiplier=0)
# ---------------------------------------------------------------------------


class TestLineNoHaloPath:
    """stroke.color / stroke.dasharray reach the single mark dict when halo is off."""

    def test_stroke_color_in_single_mark(self, make_chart):
        board = _board_no_halo_line(color="#ab1234")
        chart = make_chart("line", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board[0], chart_style_context=board[1]
        )
        # No halo → single-mark or first layer is the line.
        mark = spec.get("mark") or spec["layer"][0]["mark"]
        assert mark["stroke"] == "#ab1234", f"Expected stroke '#ab1234', got {mark!r}"

    def test_stroke_dasharray_in_single_mark(self, make_chart):
        board = _board_no_halo_line(dasharray="6 3")
        chart = make_chart("line", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board[0], chart_style_context=board[1]
        )
        mark = spec.get("mark") or spec["layer"][0]["mark"]
        assert mark["strokeDash"] == [
            6.0,
            3.0,
        ], f"Expected strokeDash [6,3], got {mark!r}"


class TestAreaNoHaloPath:
    """stroke.color / stroke.dasharray reach the fg line mark when halo is off.

    A colorless/layerless area now always takes the stacked recipe, whose
    halo_multiplier defaults to 0.0 -- there is no separate halo path to
    force off for this shape. It still emits a separate line mark
    (type: "line") for the top-edge separator stroke; the area fill layer
    suppresses its own border stroke via strokeOpacity=0.
    """

    def test_stroke_color_in_single_mark(self, make_chart):
        board = _board_with_stacked_area_stroke(color="#cd5678")
        chart = make_chart("area", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board[0], chart_style_context=board[1]
        )
        mark = _fg_line_mark(spec)
        assert mark.get("stroke") == "#cd5678", (
            f"Expected stroke '#cd5678', got {mark!r}"
        )

    def test_stroke_dasharray_in_single_mark(self, make_chart):
        board = _board_with_stacked_area_stroke(dasharray="5 5")
        chart = make_chart("area", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board[0], chart_style_context=board[1]
        )
        mark = _fg_line_mark(spec)
        assert mark.get("strokeDash") == [
            5.0,
            5.0,
        ], f"Expected strokeDash [5,5], got {mark!r}"


# ---------------------------------------------------------------------------
# End-to-end: halo path (default halo_multiplier > 0)
# ---------------------------------------------------------------------------


def _fg_line_mark(spec: dict[str, Any]) -> dict[str, Any]:
    """Return the foreground (last line-type) mark dict from a layered spec."""
    for layer in reversed(spec.get("layer", [])):
        m = layer.get("mark", {})
        if isinstance(m, dict) and m.get("type") == "line":
            return m
    return spec.get("mark", {})


def _fg_area_line(spec: dict[str, Any]) -> dict[str, Any]:
    """Return the foreground stroke-line mark dict from a layered area spec.

    After the fill/stroke split, area chart stroke is a separate line mark
    (type: "line") rather than an embedded area.line sub-dict.
    """
    return _fg_line_mark(spec)


class TestLineHaloPath:
    """stroke.color / stroke.dasharray reach the foreground line layer when halo is on."""

    def test_stroke_color_on_foreground_line(self, make_chart):
        board = _board_with_global_line_stroke(color="#ff4400")
        chart = make_chart("line", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board[0], chart_style_context=board[1]
        )
        fg = _fg_line_mark(spec)
        assert fg.get("stroke") == "#ff4400", (
            f"Foreground line mark must have stroke='#ff4400', got {fg!r}"
        )

    def test_stroke_dasharray_on_foreground_line(self, make_chart):
        board = _board_with_global_line_stroke(dasharray="4 2")
        chart = make_chart("line", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board[0], chart_style_context=board[1]
        )
        fg = _fg_line_mark(spec)
        assert fg.get("strokeDash") == [
            4.0,
            2.0,
        ], f"Foreground line mark must have strokeDash=[4.0,2.0], got {fg!r}"


class TestAreaHaloPath:
    """stroke.color / stroke.dasharray reach the fg line mark when halo is on.

    Only a genuinely multi-series area (color/wide y/layers) still uses the
    overlap recipe's halo path -- a colorless/layerless area takes the
    stacked recipe instead (see TestAreaNoHaloPath), so these charts author
    ``color`` to stay on the recipe under test.
    """

    def test_stroke_color_on_foreground_area_line(self, make_chart):
        board = _board_with_global_area_stroke(color="#0044ff", width=3.0)
        chart = make_chart(
            "area",
            x="month",
            y="revenue",
            color="segment",
            style={"endpoint_labels": {"visible": False}},
        )
        spec = generate_vega_lite_spec(
            chart,
            MULTI_SERIES_DATA,
            board_style=board[0],
            chart_style_context=board[1],
        )
        fg_line = _fg_area_line(spec)
        assert fg_line.get("stroke") == "#0044ff", (
            f"Foreground area mark.line must have stroke='#0044ff', got {fg_line!r}"
        )

    def test_stroke_dasharray_on_foreground_area_line(self, make_chart):
        board = _board_with_global_area_stroke(dasharray="3 3", width=2.0)
        chart = make_chart(
            "area",
            x="month",
            y="revenue",
            color="segment",
            style={"endpoint_labels": {"visible": False}},
        )
        spec = generate_vega_lite_spec(
            chart,
            MULTI_SERIES_DATA,
            board_style=board[0],
            chart_style_context=board[1],
        )
        fg_line = _fg_area_line(spec)
        assert fg_line.get("strokeDash") == [
            3.0,
            3.0,
        ], f"Foreground area mark.line must have strokeDash=[3.0,3.0], got {fg_line!r}"


# ---------------------------------------------------------------------------
# point.color and point.shape on line chart halo path
# ---------------------------------------------------------------------------


def _board_with_point_override(**point_overrides):
    """Board with halo enabled and point.size > 0 so the point layers are emitted."""
    compiled = get_theme_style("clarity")
    # Enable points at the family tier (line.marks.point.size = 0 in theme → override).
    line_style = compiled.charts.line
    family_point = line_style.marks.point or compiled.charts.marks.point
    new_point = family_point.model_copy(update={"size": 40.0, **point_overrides})
    updated_marks = line_style.marks.model_copy(update={"point": new_point})
    updated_line = line_style.model_copy(update={"marks": updated_marks})
    charts = compiled.charts.model_copy(update={"line": updated_line})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _fg_point_mark(spec: dict[str, Any]) -> dict[str, Any]:
    """Return the foreground data-point mark (opacity > 0, tooltip=True) from a layered spec."""
    for layer in reversed(spec.get("layer", [])):
        m = layer.get("mark", {})
        if (
            isinstance(m, dict)
            and m.get("type") == "point"
            and m.get("opacity", 0) > 0
            and m.get("tooltip") is True
        ):
            return m
    return {}


class TestPointMarkMapper:
    """Unit tests for scatter_mark_to_vl (covers _emit_point_mark)."""

    def test_fill_emitted_when_filled_false(self):
        point = PointMarkStyle(fill="#ff0000", filled=False)
        result = scatter_mark_to_vl(point)
        assert result["fill"] == "#ff0000"

    def test_fill_ignored_when_filled_true(self):
        # filled=true means encoding.color owns the fill; explicit fill is irrelevant.
        point = PointMarkStyle(fill="#ff0000", filled=True)
        result = scatter_mark_to_vl(point)
        assert "fill" not in result

    def test_fill_ignored_when_filled_unset(self):
        # filled=None means theme default (true) applies; fill is not emitted.
        point = PointMarkStyle(fill="#ff0000")
        result = scatter_mark_to_vl(point)
        assert "fill" not in result

    def test_fill_absent_not_emitted(self):
        point = PointMarkStyle()
        result = scatter_mark_to_vl(point)
        assert "fill" not in result

    def test_filled_without_fill_respected(self):
        point = PointMarkStyle(filled=True)
        result = scatter_mark_to_vl(point)
        assert result["filled"] is True

    def test_theme_default_fill_emitted_when_filled_false(self):
        # theme sets fill: theme.background on the global point; overriding
        # filled=false must expose that default fill in the VL output.
        theme_point = get_theme_style("clarity").charts.marks.point
        assert theme_point.fill is not None, "theme must supply a default fill"
        hollow = theme_point.model_copy(update={"filled": False})
        result = scatter_mark_to_vl(hollow)
        assert result["fill"] == theme_point.fill

    def test_stroke_width_emitted_as_strokeWidth(self):
        # Authored stroke_width on a point mark must flow through to VL's
        # ``strokeWidth`` so hollow ring outlines pick up the configured weight
        # instead of VL's hardcoded 2 px default.
        point = PointMarkStyle(filled=False, stroke_width=5)
        result = scatter_mark_to_vl(point)
        assert result["strokeWidth"] == 5

    def test_stroke_width_absent_not_emitted(self):
        # No stroke_width → no strokeWidth key (VL default applies).
        point = PointMarkStyle()
        result = scatter_mark_to_vl(point)
        assert "strokeWidth" not in result

    @pytest.mark.parametrize("family", ["scatter", "point_map", "area"])
    def test_family_default_stroke_width_matches_line_stroke(self, family):
        # Families whose point ring is not density-baked supply their own
        # default so hollow rings read at the line family's stroke weight
        # rather than falling back to VL's hardcoded 2 px. This is a
        # separate theme literal pinned equal to
        # chart_rendering.stroke.fallback_width for visual coherence -- not
        # an inherited reference, so a project overriding fallback_width in
        # dbt_charts.yml would desync the two.
        editorial = get_theme_style("clarity")
        theme_point = getattr(editorial.charts, family).marks.point
        fallback_width = get_chart_rendering().stroke.fallback_width
        assert theme_point.stroke_width == fallback_width, (
            f"{family}.marks.point.stroke_width ({theme_point.stroke_width}) "
            f"should match chart_rendering.stroke.fallback_width "
            f"({fallback_width}) so hollow rings read with the same weight "
            f"as the line itself"
        )

    def test_global_stroke_width_stays_unset_for_the_bake(self):
        # The global slot is deliberately empty (like marks.point.size):
        # bake_point_companions reads "no tier authored a ring" off the
        # cascaded value, so a literal here would shadow a board-tier pin.
        assert get_theme_style("clarity").charts.marks.point.stroke_width is None


class TestLineHaloPointPath:
    """point.color, point.shape, and point.fill reach the foreground point layer when halo is on."""

    def test_point_color_on_foreground_point(self, make_chart):
        board = _board_with_point_override(color="#aa2200")
        chart = make_chart("line", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board[0], chart_style_context=board[1]
        )
        fg_pt = _fg_point_mark(spec)
        assert fg_pt.get("color") == "#aa2200", (
            f"Foreground point mark must have color='#aa2200', got {fg_pt!r}"
        )

    def test_point_shape_on_foreground_point(self, make_chart):
        board = _board_with_point_override(shape="square")
        chart = make_chart("line", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board[0], chart_style_context=board[1]
        )
        fg_pt = _fg_point_mark(spec)
        assert fg_pt.get("shape") == "square", (
            f"Foreground point mark must have shape='square', got {fg_pt!r}"
        )

    def test_point_fill_on_foreground_point(self, make_chart):
        board = _board_with_point_override(fill="#00bbff", filled=False)
        chart = make_chart("line", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board[0], chart_style_context=board[1]
        )
        fg_pt = _fg_point_mark(spec)
        assert fg_pt.get("fill") == "#00bbff", (
            f"Foreground point mark must have fill='#00bbff', got {fg_pt!r}"
        )


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Global mark → bar family propagation
# apply_inherit propagates tier-1 globals into family.marks at resolve time,
# so the render layer can read effective.bar.marks.bar directly without
# calling resolve_mark.  These tests guard that invariant.
# ---------------------------------------------------------------------------


def _board_with_global_bar_size(size: float):
    """Board with charts.marks.bar.size set at the global tier (tier 1)."""
    compiled = get_theme_style("clarity")
    new_bar_mark = compiled.charts.marks.bar.model_copy(update={"size": size})
    new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar_mark})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _board_with_global_bar_corner_radius(radius: float):
    """Board with charts.marks.bar.border.radius at the global tier (tier 1)."""
    compiled = get_theme_style("clarity")
    assert compiled.charts.marks.bar.border is not None
    new_border = compiled.charts.marks.bar.border.model_copy(update={"radius": radius})
    new_bar_mark = compiled.charts.marks.bar.model_copy(update={"border": new_border})
    new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar_mark})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


class TestBarGlobalMarkPropagation:
    """Global mark defaults (tier 1) propagate to bar renders.

    apply_inherit propagates global marks into family.marks at resolve time.
    The render layer must not need resolve_mark to produce correct output —
    reading effective.bar.marks.bar directly is enough.
    """

    def test_global_bar_size_reaches_spec(self, make_chart):
        board = _board_with_global_bar_size(55.0)
        chart = make_chart("bar", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board[0], chart_style_context=board[1]
        )
        mark = spec.get("mark") or (spec.get("layer") or [{}])[0].get("mark", {})
        # A fixed-width bar.size is a literal mark.width (vertical) or
        # mark.height (horizontal) — it overrides band_width on any scale.
        assert mark.get("width") == 55.0 or mark.get("height") == 55.0, (
            f"Global bar.size must reach mark.width/height, got {mark!r}"
        )

    def test_global_bar_corner_radius_reaches_spec(self, make_chart):
        board = _board_with_global_bar_corner_radius(6.0)
        chart = make_chart("bar", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board[0], chart_style_context=board[1]
        )
        mark = spec.get("mark") or (spec.get("layer") or [{}])[0].get("mark", {})
        assert mark.get("cornerRadiusEnd") == 6.0, (
            f"Global bar.border.radius must reach mark.cornerRadiusEnd, got {mark!r}"
        )
