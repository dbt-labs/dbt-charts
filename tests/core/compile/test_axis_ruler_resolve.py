"""The ruler's composition decision is baked at resolve, not render.

``build_resolved_axis`` folds three resolve-time facts into one
``ResolvedAxisStyle.ruler`` field: does the baked tick ladder compact
(``shared_scale_for_ladder``, ``dbt_charts.core.text.numeral_scale``), is the
axis's format SI-shaped (a plain format is a deliberate opt-out), and is
``label.expr`` unauthored (the documented full opt-out). ``None`` means
"paint exactly as the resolved format/labelExpr already specify — no
magnitude producer runs, in paint, in the gutter measurement, or in the
mirror ghost". It also enforces the tabular guarantee: a column-forming axis
that will actually ship the suffix-field reservation must resolve to a
tabular-guaranteed label font, else the engine refuses at resolve rather
than shipping a misaligned column. The reservation itself is composed from
measured Unicode space characters to match the suffix's advance
(``font_measure.compose_suffix_reservation``) — the tabular guarantee holds
the column together because every digit shares one advance, not because the
padding is figure-spaces. See "ruler" in
``ai_notes/jul26-02-numeral-system-design.md``.
"""

from __future__ import annotations

import pytest

from d3_format import format as d3_format_apply
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import AreaChart, BarChart
from dbt_charts.core.compile.models.style.authored import (
    AxisLabelStylePatch,
    AxisYStylePatch,
    BarChartStylePatch,
)
from dbt_charts.core.compile.models.style.resolved import ResolvedTickLabel
from dbt_charts.core.compile.models.style.theme.axis import (
    AxisMirrorStyle,
    BaseScaleStyle,
    ScaleContinuousStyle,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    build_resolved_axis,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_AXIS_COLUMN_REQUIRES_TABULAR_FONT,
)
from dbt_charts.core.font_measure import RESERVATION_GUARD, compose_suffix_reservation
from dbt_charts.core.fonts import (
    DBT_SERIF_OLDSTYLE_TABULAR_FONT_FAMILY,
    SOURCE_SERIF_4_FONT_FAMILY,
)
from dbt_charts.core.numeric import nice_tick_values
from dbt_charts.core.text.format_d3 import is_d3_si_spec
from dbt_charts.core.text.numeral_scale import (
    SuffixMode,
    shared_scale_for_ladder,
    sub_unit_digit_format,
    sub_unit_scientific_format,
)

# A ladder that compacts in ANCHOR mode (mirrors the 0-450k worked example in
# the numeral design doc): step 100,000 divides the thousands tier evenly and
# the extreme tick (500,000) reaches six written-out digits.
_ANCHOR_TICKS = (0.0, 100_000.0, 200_000.0, 300_000.0, 400_000.0, 500_000.0)

# A short ladder that never compacts (step carries no SI tier at all).
_PLAIN_TICKS = (0.0, 20.0, 40.0, 60.0)


def _merged_axis_y():
    """The merged (pre-build) axis_y AxisStyle from the default (stark) theme.

    stark's axis_quantitative.labels.font.family is 'dbt Sans Tabular' —
    tabular — so tests override family explicitly to exercise the guard.
    Its format resolves (via the alias cascade already run by
    ``_bake_cartesian_axes``) to a real SI-shaped d3 spec (``.3~s``).
    """
    chart_style_context = resolve_chart_style_context(get_theme_style("stark"))
    chart = BarChart.model_validate(
        {"id": "t", "type": "bar", "x": "cat", "y": "value"}
    )
    (
        _ax_merged,
        ay_merged,
        _ax_band_position,
        _ay_band_position,
        _ay_format_authored,
        _ay_format_is_alias,
    ) = _bake_cartesian_axes(
        chart_style_context,
        chart,
        "bar",
        "nominal",
        "quantitative",
        AxisOverrides(),
    )
    return ay_merged


def _with_family(ay_merged, family: str):
    return ay_merged.model_copy(
        update={
            "labels": ay_merged.labels.model_copy(
                update={
                    "font": ay_merged.labels.font.model_copy(update={"family": family})
                }
            )
        }
    )


def test_ruler_is_none_when_ladder_does_not_compact():
    ay_merged = _merged_axis_y()
    ay = build_resolved_axis(
        ay_merged,
        tick_values=_PLAIN_TICKS,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is None


def test_ruler_baked_from_tick_ladder_matches_the_pure_resolver():
    ay_merged = _merged_axis_y()
    ay = build_resolved_axis(
        ay_merged,
        tick_values=_ANCHOR_TICKS,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    expected = shared_scale_for_ladder(list(_ANCHOR_TICKS))
    assert expected is not None
    assert ay.ruler is not None
    assert ay.ruler.exponent == expected.exponent
    assert ay.ruler.mode is expected.mode is SuffixMode.ANCHOR
    assert ay.ruler.anchor_at_start is False


def test_reservation_is_composed_against_the_axis_own_resolved_font():
    """The baked ``reservation`` must be measured against THIS axis's own
    resolved label font, not a fixed default -- proven by authoring a
    different vendored tabular family (dbt Serif Oldstyle Tabular) and
    checking the bake follows it, rather than by re-calling
    ``compose_suffix_reservation`` with the same arguments the bake itself
    used (which would pass even if ``build_resolved_axis`` ignored the
    resolved font entirely, as it effectively did before
    ``compose_suffix_reservation`` started resolving its measurer from the
    family argument instead of the always-Sans-Tabular numeric stand-in).
    """
    ay_merged = _with_family(_merged_axis_y(), DBT_SERIF_OLDSTYLE_TABULAR_FONT_FAMILY)
    ay = build_resolved_axis(
        ay_merged,
        tick_values=_ANCHOR_TICKS,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is not None
    assert ay.labels.font.family == DBT_SERIF_OLDSTYLE_TABULAR_FONT_FAMILY
    expected = compose_suffix_reservation(
        ay.ruler.suffix_string, DBT_SERIF_OLDSTYLE_TABULAR_FONT_FAMILY
    )
    assert ay.ruler.reservation == expected
    assert ay.ruler.reservation.endswith(RESERVATION_GUARD)

    # And it must differ from what the default (Sans Tabular) axis bakes --
    # otherwise this could still pass with the family silently ignored.
    default_ay = build_resolved_axis(
        _merged_axis_y(),
        tick_values=_ANCHOR_TICKS,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    assert default_ay.ruler is not None
    assert ay.ruler.reservation != default_ay.ruler.reservation


def test_non_column_forming_axis_bakes_no_reservation():
    ay_merged = _merged_axis_y()
    _REPEAT_TICKS = (0.0, 200_000.0, 400_000.0, 600_000.0, 800_000.0, 1_000_000.0)
    ay = build_resolved_axis(
        ay_merged,
        tick_values=_REPEAT_TICKS,
        column_forming=False,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is not None
    assert ay.ruler.reservation == ""


def test_anchor_at_start_is_true_for_an_all_negative_zero_topped_ladder():
    """Regression: an all-negative, zero-topped ladder (routine for a
    zero-anchored measure axis over all-negative data — see
    ``_resolve_cartesian_ticks``) anchors on its most negative tick (the
    FIRST tick in the ascending ladder), not on the trailing 0 (the last).
    Positional gating fixed at ``len(tick_values) - 1`` would pick 0, which
    never carries a magnitude suffix, so the axis would silently never
    declare its magnitude at all.
    """
    ay_merged = _merged_axis_y()
    negative_ticks = (-600_000.0, -400_000.0, -200_000.0, 0.0)
    raw = shared_scale_for_ladder(list(negative_ticks))
    assert raw is not None  # sanity: this ladder does compact
    ay = build_resolved_axis(
        ay_merged,
        tick_values=negative_ticks,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is not None
    assert ay.ruler.anchor_at_start is True


def test_no_tick_values_never_compacts():
    """Empty tick_values (a chart family that never bakes a ladder) yields
    None regardless of the font — there is nothing to raise about either.
    """
    ay_merged = _with_family(_merged_axis_y(), "Comic Sans MS")
    ay = build_resolved_axis(
        ay_merged,
        tick_values=(),
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is None


def test_non_si_format_never_bakes_ruler_even_when_the_ladder_compacts():
    """An authored plain (non-SI) format is a deliberate choice to show full
    digits — the engine must not force a magnitude suffix onto it, so
    ``ruler`` stays None even though the same ladder would otherwise compact.
    Without this gate, the gutter measurement (which reads ``ruler`` too)
    would size itself for a labelExpr the axis never paints.
    """
    ay_merged = _merged_axis_y()
    ay_merged = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"format": ",.0f"})}
    )
    ay = build_resolved_axis(
        ay_merged,
        tick_values=_ANCHOR_TICKS,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is None


def test_authored_label_expr_never_bakes_ruler():
    """``label.expr`` is the documented full opt-out — the engine's
    enrichment stands down entirely, so ``ruler`` stays None even on a
    compacting ladder with an SI-shaped format.
    """
    ay_merged = _merged_axis_y()
    ay_expr = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"expr": "'x'"})}
    )
    ay = build_resolved_axis(
        ay_expr,
        tick_values=_ANCHOR_TICKS,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is None


def test_non_column_forming_axis_bakes_repeat_mode_regardless_of_ladder():
    """Register follows mode: a suffix stated once (anchor) must be spaced
    and capital because it's a set declaration; a suffix on every member
    (repeat) must be unspaced and lowercase because it's attached to its
    value. Narrative-plus-anchor is one of the two mismatched combinations
    the design contract rejects — a horizontal (non-column-forming) axis is
    always narrative (no internal space may fragment a token in a stream of
    space-separated things), so it must also always bake repeat mode,
    regardless of what the ladder's own mode would otherwise select for a
    vertical ruler with the same ticks.
    """
    ay_merged = _merged_axis_y()
    _REPEAT_TICKS = (0.0, 200_000.0, 400_000.0, 600_000.0, 800_000.0, 1_000_000.0)
    for ladder_mode, ticks in (
        (SuffixMode.ANCHOR, _ANCHOR_TICKS),
        (SuffixMode.REPEAT, _REPEAT_TICKS),
    ):
        raw = shared_scale_for_ladder(list(ticks))
        assert raw is not None and raw.mode is ladder_mode
        ay = build_resolved_axis(
            ay_merged,
            tick_values=ticks,
            column_forming=False,
            chart_id="test",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.ruler is not None
        assert ay.ruler.mode is SuffixMode.REPEAT
        assert ay.ruler.reserve is False


def test_column_forming_compacting_axis_on_non_tabular_font_raises():
    ay_merged = _with_family(_merged_axis_y(), "Comic Sans MS")
    with pytest.raises(CompilationError) as exc_info:
        build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
    assert exc_info.value.code is ERR_AXIS_COLUMN_REQUIRES_TABULAR_FONT


def test_column_forming_compacting_axis_on_vendored_non_tabular_font_raises():
    """Regression: the tabular guard must run BEFORE
    compose_suffix_reservation, not after. A vendored-but-proportional
    family (Source Serif 4, tabular=False in the registry) reaches real
    measurement -- unlike "Comic Sans MS" above, which is unvendored and
    degrades to the Sans Tabular stand-in, composes fine, and would let
    this guard fire correctly even mis-ordered. Source Serif 4 does not:
    checked after composing, its real board misses the sub-pixel tolerance
    and compose_suffix_reservation's own internal-invariant RuntimeError
    fires first, asserting a font-registry defect for what is really an
    ordinary authoring mistake with an actionable fix. The resolve-layer
    guard must win this race every time.
    """
    ay_merged = _with_family(_merged_axis_y(), SOURCE_SERIF_4_FONT_FAMILY)
    with pytest.raises(CompilationError) as exc_info:
        build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
    assert exc_info.value.code is ERR_AXIS_COLUMN_REQUIRES_TABULAR_FONT


def test_column_forming_compacting_axis_on_tabular_font_does_not_raise():
    ay_merged = _merged_axis_y()  # stark's default: dbt Sans Tabular
    ay = build_resolved_axis(
        ay_merged,
        tick_values=_ANCHOR_TICKS,
        column_forming=True,
        chart_id="fixture",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is not None


def test_non_column_forming_axis_on_non_tabular_font_does_not_raise():
    """A horizontal ruler forms no column, so the tabular guarantee doesn't
    apply even though its ladder compacts on the same non-tabular font.
    """
    ay_merged = _with_family(_merged_axis_y(), "Comic Sans MS")
    ay = build_resolved_axis(
        ay_merged,
        tick_values=_ANCHOR_TICKS,
        column_forming=False,
        chart_id="fixture",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is not None


def _with_align(ay_merged, align):
    return ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"align": align})}
    )


def _with_case(ay_merged, case):
    return ay_merged.model_copy(
        update={
            "labels": ay_merged.labels.model_copy(
                update={"font": ay_merged.labels.font.model_copy(update={"case": case})}
            )
        }
    )


def test_right_edge_axis_with_no_authored_align_bakes_no_ruler():
    """A right-edge axis with no authored align takes VL's own smart
    default (away from the plot = text-anchor:start on the right edge) --
    start-anchored. The reservation only ever suits an end-anchored label
    (it pads the wrong side of a start-anchored one), and the digit field
    that used to align a start-anchored label is gone, so this axis now
    bakes no ruler at all.
    """
    ay_merged = _merged_axis_y()
    ay = build_resolved_axis(
        ay_merged,
        tick_values=_ANCHOR_TICKS,
        edge="right",
        column_forming=True,
        chart_id="fixture",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is None


def test_left_edge_axis_still_bakes_reservation():
    ay_merged = _merged_axis_y()
    ay = build_resolved_axis(
        ay_merged,
        tick_values=_ANCHOR_TICKS,
        edge="left",
        column_forming=True,
        chart_id="fixture",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is not None
    assert ay.ruler.reserve is True
    assert ay.ruler.reservation != ""


def test_right_edge_axis_on_non_tabular_font_does_not_raise():
    """The tabular-font guard protects the trailing reservation only -- a
    start-anchored axis now bakes no ruler at all, so it never even reaches
    the guard.
    """
    ay_merged = _with_family(_merged_axis_y(), "Comic Sans MS")
    ay = build_resolved_axis(
        ay_merged,
        tick_values=_ANCHOR_TICKS,
        edge="right",
        column_forming=True,
        chart_id="fixture",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is None


class TestMechanismFollowsResolvedAnchoringNotEdge:
    """The mechanism (digit field vs trailing reservation) is chosen from
    the resolved text anchoring (label.align, defaulting per VL's own
    per-orient smart default), never from the physical edge alone.
    """

    def test_right_edge_with_inward_align_is_end_anchored_takes_reservation(self):
        """axis_y.labels.align: inward on a right-edge axis resolves to
        label.align == "right" (inward == edge itself) -- text-anchor:end,
        growing toward the plot. Its digits already share a right edge on
        their own; the reservation aligns the suffix field, and the
        tabular guard must still protect it. Before this fix, the
        mechanism was chosen from edge alone and this configuration wrongly
        took the digit field, skipping the guard and reintroducing the
        original bug (an ANCHOR-mode suffix tick shifting its digits left
        of its siblings').
        """
        ay_merged = _with_align(_merged_axis_y(), "inward")
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="right",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.labels.align == "right"
        assert ay.ruler is not None
        assert ay.ruler.reserve is True
        assert ay.ruler.reservation != ""

    def test_right_edge_with_inward_align_on_non_tabular_font_raises(self):
        ay_merged = _with_align(
            _with_family(_merged_axis_y(), "Comic Sans MS"), "inward"
        )
        with pytest.raises(CompilationError) as exc_info:
            build_resolved_axis(
                ay_merged,
                tick_values=_ANCHOR_TICKS,
                edge="right",
                column_forming=True,
                chart_id="fixture",
                format_authored=True,
                format_is_alias=False,
            )
        assert exc_info.value.code is ERR_AXIS_COLUMN_REQUIRES_TABULAR_FONT

    def test_left_edge_with_inward_align_is_start_anchored_bakes_no_ruler(self):
        """axis_y.labels.align: inward on a LEFT-edge axis resolves to
        label.align == "left" (inward == edge itself, and edge is "left")
        -- text-anchor:start, growing toward the plot. This is the mirror
        image of the right-edge default case: a start-anchored axis now
        bakes no ruler at all -- the digit field that used to align it is
        gone, and the reservation only ever suits an end-anchored label.
        """
        ay_merged = _with_align(_merged_axis_y(), "inward")
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="left",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.labels.align == "left"
        assert ay.ruler is None

    def test_center_align_on_right_edge_bakes_the_smart_default_of_no_ruler(self):
        """ "center" is neither start- nor end-anchored, and no single-sided
        padding aligns a centered label -- ``measure_axis_to_vl``'s
        ``_fallback_reversed_label_align`` ALWAYS deletes a "center"
        ``labelAlign`` at render, regardless of measurability, falling back
        to VL's own per-orient smart default. On a right-edge axis that
        default is start-anchored ("left"), and a start-anchored axis now
        bakes no ruler at all -- the reservation a naive `align == "left"`
        check on the pre-fallback "center" value would pick only ever
        suits an end-anchored label, so baking it here would disagree with
        what render actually does with this axis.
        """
        ay_merged = _with_align(_merged_axis_y(), "center")
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="right",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.ruler is None

    def test_center_align_on_left_edge_bakes_the_smart_default_reservation(self):
        """The mirror image: a left-edge axis's smart default is
        end-anchored ("right"), so a "center"-authored axis there actually
        renders with the reservation, not the digit field.
        """
        ay_merged = _with_align(_merged_axis_y(), "center")
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="left",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.ruler is not None
        assert ay.ruler.reserve is True

    def test_own_side_align_with_upper_case_bakes_the_render_fallback_mechanism(self):
        """``measure_axis_to_vl``'s own-side/case safety net
        (``_fallback_reversed_label_align``) deletes an own-side
        ``labelAlign`` at render when ``label.font.case`` is upper/lower --
        VL then prefers the injected, un-measurable ``labelExpr`` over
        ``format``, so dbt charts can't safely compute a ``labelPadding`` and
        falls back to VL's own smart default instead of shipping an
        unmeasured (and possibly wrong) gutter. A left-edge axis with
        ``align: left`` (own-side, start-anchored) and ``case: upper`` must
        therefore bake the mechanism its actual (post-fallback) end-anchored
        default renders with -- the reservation -- not the digit field the
        pre-fallback ``align: left`` alone would pick.
        """
        ay_merged = _with_case(_with_align(_merged_axis_y(), "left"), "upper")
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="left",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.labels.align == "left"
        assert ay.ruler is not None
        assert ay.ruler.reserve is True

    def test_away_side_align_with_upper_case_is_unaffected(self):
        """An away-side align never invades the plot, so
        ``_fallback_reversed_label_align`` never touches it regardless of
        case -- this axis keeps the mechanism its (unmodified) align picks.
        """
        ay_merged = _with_case(_with_align(_merged_axis_y(), "right"), "upper")
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="left",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.labels.align == "right"
        assert ay.ruler is not None
        assert ay.ruler.reserve is True


def _with_mirror(ay_merged, mirror):
    return ay_merged.model_copy(update={"mirror": mirror})


class TestMirrorRulerBakedAtResolve:
    """``style.axis_y.mirror`` draws the y-scale on both edges. The ghost's
    own anchoring is typically (not always -- an authored ``labels.align``
    can make either edge either anchoring) the opposite of the primary's,
    so it needs its own mechanism decision -- baked here, at resolve,
    through the same tabular guard the primary's goes through, rather than
    re-derived in render where the guard has already run and cannot reach
    it.
    """

    def test_right_edge_mirror_bakes_no_ruler_on_either_edge(self):
        """Primary (right edge, unset align) is start-anchored -- no ruler
        at all (the digit-field device that used to align it is gone). The
        gate that builds the ghost (``ruler is not None``) is therefore
        never reached either, so the ghost bakes no ruler regardless of its
        own (end-anchored) anchoring.
        """
        ay_merged = _with_mirror(_merged_axis_y(), True)
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="right",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.ruler is None
        assert ay.mirror_ruler is None

    def test_left_edge_mirror_bakes_no_ruler_for_the_start_anchored_ghost(self):
        """Primary (left edge, unset align) is end-anchored -- reservation,
        unaffected by this task. The ghost (right edge, unset align) is
        start-anchored -- it now bakes no ruler at all (the digit field it
        used to need is gone), even though the primary still has one.
        """
        ay_merged = _with_mirror(_merged_axis_y(), True)
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="left",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.ruler is not None
        assert ay.ruler.reserve is True
        assert ay.mirror_ruler is None

    def test_no_mirror_bakes_no_ghost_ruler(self):
        ay_merged = _merged_axis_y()
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="right",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.mirror_ruler is None

    def test_non_compacting_ladder_bakes_no_ghost_ruler(self):
        ay_merged = _with_mirror(_merged_axis_y(), True)
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_PLAIN_TICKS,
            edge="right",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.ruler is None
        assert ay.mirror_ruler is None

    def test_mirror_expr_override_bakes_no_ghost_ruler(self):
        """mirror.expr replaces the ghost's paint with an authored,
        unmeasurable expression -- the engine's own ruler composition
        (baked or not) is never consulted, so baking one would be dead
        weight and, worse, could spuriously trip the tabular guard for a
        font the user never asked this device to measure."""
        ay_merged = _with_mirror(_merged_axis_y(), AxisMirrorStyle(expr="'x'"))
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="right",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.mirror_ruler is None

    def test_mirror_format_override_bakes_no_ghost_ruler(self):
        ay_merged = _with_mirror(_merged_axis_y(), AxisMirrorStyle(format=".0%"))
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="right",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.mirror_ruler is None

    def test_right_edge_mirror_on_non_tabular_font_does_not_raise(self):
        """A start-anchored primary now bakes no ruler at all, so it never
        reaches the tabular guard -- and the gate that would build the
        ghost (``ruler is not None``) is skipped too, so the ghost's own
        guard never runs either. A non-tabular font is a non-event here.
        """
        ay_merged = _with_mirror(
            _with_family(_merged_axis_y(), SOURCE_SERIF_4_FONT_FAMILY), True
        )
        ay = build_resolved_axis(
            ay_merged,
            tick_values=_ANCHOR_TICKS,
            edge="right",
            column_forming=True,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        )
        assert ay.ruler is None
        assert ay.mirror_ruler is None

    def test_left_edge_mirror_on_non_tabular_font_does_not_raise(self):
        """The mirror image of the crash above: primary (left, end-
        anchored) needs the reservation and DOES need the guard -- but
        that's the primary's own existing guard, unrelated to mirroring.
        Included to confirm the ghost-guard fix doesn't flip this case.
        """
        ay_merged = _with_mirror(
            _with_family(_merged_axis_y(), SOURCE_SERIF_4_FONT_FAMILY), True
        )
        with pytest.raises(CompilationError) as exc_info:
            build_resolved_axis(
                ay_merged,
                tick_values=_ANCHOR_TICKS,
                edge="left",
                column_forming=True,
                chart_id="fixture",
                format_authored=True,
                format_is_alias=False,
            )
        assert exc_info.value.code is ERR_AXIS_COLUMN_REQUIRES_TABULAR_FONT


# ── Non-compacting branch: tick_label, the ruler's non-compacting sibling ──
#
# A ladder that does not compact (``raw_scale is None``) still writes its
# ticks out in full when the axis's SI-shaped format is unauthored:
# ``build_resolved_axis`` bakes the plain fixed-point d3 spec onto
# ``ResolvedAxisStyle.tick_label.format``. ``label.format`` and ``label.expr``
# are never touched, so value labels, tooltips, the currency-warning
# detector, and any other consumer that inherits the resolved measure format
# keep the theme's bounded SI default. ``ruler`` and ``tick_label`` are
# mutually exclusive — one ladder decision, two possible outcomes.


def test_non_compacting_ladder_bakes_tick_label_when_unauthored():
    ay_merged = _merged_axis_y()
    ticks = (0.0, 20_000.0, 40_000.0, 60_000.0)
    assert shared_scale_for_ladder(list(ticks)) is None  # sanity: does not compact
    ay = build_resolved_axis(
        ay_merged,
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.ruler is None
    # label.format is untouched -- everything that inherits it (value labels,
    # tooltips, the currency-warning detector) keeps the theme's SI default.
    assert ay.labels.format == ".3~s"
    assert ay.labels.expr is None
    assert ay.tick_label.format == ",.0~f"
    assert d3_format_apply(ay.tick_label.format, 20_000.0) == "20,000"
    assert d3_format_apply(ay.tick_label.format, 60_000.0) == "60,000"


def test_non_compacting_ladder_leaves_an_authored_format_untouched():
    """``format_authored`` is a required keyword, no default — this call
    passes True explicitly for an author who wrote the SI format
    themselves, keeping today's behavior.
    """
    ay_merged = _merged_axis_y()
    ticks = (0.0, 20_000.0, 40_000.0, 60_000.0)
    ay = build_resolved_axis(
        ay_merged,
        tick_values=ticks,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is None
    assert ay.labels.format == ".3~s"
    assert ay.tick_label is None


def test_non_compacting_ladder_bakes_tick_label_when_format_is_an_authored_alias():
    """An author who reaches for a named format alias (``currency``,
    ``"$~s"``) to get the ``$`` prefix still gets the ruler's own settled
    non-compacting rule -- ``format_is_alias`` relaxes the ``format_authored``
    gate so an alias-sourced SI format bakes plain digits below the
    compaction threshold exactly like the unauthored theme default does.
    Reproduces the bug report's ``hero_bookings`` case: an 8,000-topping
    ladder authored via ``currency`` must write ``0, $2,000, ...``,
    not stay permanently SI-compacted.
    """
    ay_merged = _merged_axis_y()
    ay_currency = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"format": "$~s"})}
    )
    ticks = (0.0, 2_000.0, 4_000.0, 6_000.0, 8_000.0)
    # edge="right" -> start_anchored=True -> prefix-split path.
    ay = build_resolved_axis(
        ay_currency,
        tick_values=ticks,
        format_authored=True,
        format_is_alias=True,
        edge="right",
        chart_id="test",
    )
    assert ay.ruler is None
    # build_resolved_axis does not alter labels.format (it was set to "$~s").
    assert ay.labels.format == "$~s"
    # prefix is split out; tick_label.format is the digit spec only.
    assert ay.tick_label.format == ",.0~f"
    # Bare symbol -- no configured gap; "$1,000" touches directly.
    assert ay.tick_label.prefix == "$"
    # Concatenating prefix + format produces the full labeled form.
    assert d3_format_apply(ay.tick_label.format, 8_000.0) == "8,000"
    assert ay.tick_label.prefix + d3_format_apply(ay.tick_label.format, 8_000.0) == (
        "$8,000"
    )


def test_non_compacting_ladder_bakes_tick_label_repeat_when_not_column_forming():
    """A horizontal bar's measure axis (``column_forming=False``) has no
    vertical digit column for an anchor-only prefix to disambiguate against
    -- ``anchor_at_start_plain`` stays unset (``None``) even though the
    format carries a currency prefix, mirroring ``_build_ruler``'s
    ``effective_mode`` override (``scale.py``) for the same orientation.
    """
    ay_merged = _merged_axis_y()
    ay_currency = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"format": "$~s"})}
    )
    ticks = (0.0, 2_000.0, 4_000.0, 6_000.0, 8_000.0)
    ay = build_resolved_axis(
        ay_currency,
        tick_values=ticks,
        format_authored=True,
        format_is_alias=True,
        column_forming=False,
        chart_id="test",
    )
    assert ay.ruler is None
    assert ay.tick_label.format == ",.0~f"
    assert ay.tick_label.prefix == "$"
    assert ay.tick_label.anchor_at_start is None


def test_non_compacting_ladder_leaves_a_literal_authored_format_untouched():
    """The mirror negative case: the same ladder with a hand-typed literal SI
    spec (not resolved through the alias table) must NOT bake plain digits --
    proving the alias/literal distinction is real, not "always bake".
    """
    ay_merged = _merged_axis_y()
    ay_currency = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"format": "$~s"})}
    )
    ticks = (0.0, 2_000.0, 4_000.0, 6_000.0, 8_000.0)
    ay = build_resolved_axis(
        ay_currency,
        tick_values=ticks,
        format_authored=True,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.ruler is None
    # build_resolved_axis does not alter labels.format (it was set to "$~s").
    assert ay.labels.format == "$~s"
    assert ay.tick_label is None


# The two tests above call ``build_resolved_axis`` directly with a hand-set
# ``format_is_alias``, which only proves the gate itself is wired correctly --
# it never exercises how ``format_is_alias`` gets COMPUTED and threaded from a
# real authored patch through ``resolve()``'s production seam
# (BarChart -> resolve_bar_chart -> _bake_cartesian_axes -> build_resolved_axis,
# via _extract_axis_overrides's non-Optional AxisOverrides -- the only path
# production ever takes). A bug in that wiring (e.g. a swapped argument in one
# of _bake_cartesian_axes's 6 return values, or a mixed-up variable in one of
# _merge_axis_cascade's four v2-path provenance blocks) would leave the tests
# above green while shipping the bug report's original behavior unfixed.
_NON_COMPACTING_BAR_DATA = [
    {"month": "Jan", "revenue": 2000},
    {"month": "Feb", "revenue": 4000},
    {"month": "Mar", "revenue": 8000},
]


def test_end_to_end_authored_alias_bakes_plain_digits_through_real_resolve():
    """Reproduces the bug report exactly: a real BarChart authoring
    ``style.axis_y.labels.format: currency`` on data topping out at
    8,000 must bake plain-digit tick_label through the actual
    production resolve() pipeline, not just the isolated
    ``build_resolved_axis`` call above.

    A string x column ("month") makes this bar resolve to horizontal
    orientation (``_bar_orientation``), so its measure axis renders on VL's
    x channel and is non-column-forming -- the prefix repeats on every tick
    (``anchor_at_start is None``) rather than baking an anchor-only bool.
    See ``test_end_to_end_authored_alias_right_edge_splits_prefix`` for the
    column-forming companion (a LineChart, whose y-axis is always vertical).
    """

    board = resolve_chart_style_context(get_theme_style())
    patch = BarChartStylePatch(
        axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format="currency"))
    )
    chart = BarChart(id="t", type="bar", x="month", y="revenue", style=patch)
    resolved = resolve(chart, _NON_COMPACTING_BAR_DATA, chart_style_context=board)
    ay = resolved.style.axis_y
    assert ay.labels.format == "$.3~s"
    assert ay.ruler is None
    assert ay.tick_label.format == ",.0~f"
    assert ay.tick_label.prefix == "$"
    # Non-column-forming: repeat mode, not an anchor-only bool.
    assert ay.tick_label.anchor_at_start is None
    assert d3_format_apply(ay.tick_label.format, 8_000.0) == "8,000"


def test_end_to_end_board_authored_alias_bakes_plain_digits():
    """Board-scope twin of the test above.

    The same ``currency`` alias, authored at board level
    (``style.charts.axis_y.labels.format``) instead of on the chart, must bake
    the same plain-digit tick_label. This pins the board tier's
    ``format_is_alias`` computation: without it the board layer reports
    format_authored=True / format_is_alias=False, which closes the compaction
    gate in build_resolved_axis, and the axis stays SI-compacted ($8k) while
    the identical chart-level authoring bakes plain digits — a board-vs-chart
    divergence for identical authoring intent.
    """
    from dbt_charts.core.compile.models.style.authored import StylePatch

    board_patch = StylePatch.model_validate(
        {"charts": {"axis_y": {"labels": {"format": "currency"}}}}
    )
    board = resolve_chart_style_context(get_theme_style(), board_patch)
    chart = BarChart(id="t", type="bar", x="month", y="revenue")
    resolved = resolve(chart, _NON_COMPACTING_BAR_DATA, chart_style_context=board)
    ay = resolved.style.axis_y
    assert ay.labels.format == "$.3~s"
    assert ay.tick_label.format == ",.0~f"
    assert ay.tick_label.prefix == "$"
    assert d3_format_apply(ay.tick_label.format, 8_000.0) == "8,000"


def test_end_to_end_authored_alias_right_edge_splits_prefix():
    """On a right-edge axis with currency, the house-rule forces
    label.align = 'right' (end-anchored), and the prefix is split so it
    appears on the anchor tick only -- same as a left-edge axis.

    Forcing end-anchoring is intentional: text-anchor=end gives automatic
    place-value alignment without any column-forming padding device.

    Uses LineChart -- a line chart's y-axis is always column-forming (vertical),
    unlike a BarChart with string x data (which resolves to horizontal orientation
    where the measure axis renders on VL's x channel and is non-column-forming).
    """
    from dbt_charts.core.compile.models.chart.normalized import LineChart
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch

    board = resolve_chart_style_context(get_theme_style())
    patch = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            position="right",
            labels=AxisLabelStylePatch(format="currency"),
        )
    )
    # Numeric x so the line chart resolves y as the measure axis.
    data = [
        {"m": 1, "revenue": 2000},
        {"m": 2, "revenue": 4000},
        {"m": 3, "revenue": 8000},
    ]
    chart = LineChart(id="t", type="line", x="m", y="revenue", style=patch)
    resolved = resolve(chart, data, chart_style_context=board)
    ay = resolved.style.axis_y

    assert ay.labels.format == "$.3~s"
    # Forced end-anchored: no column-forming device needed.
    assert ay.labels.align == "right"
    assert ay.ruler is None
    assert ay.tick_label.format == ",.0~f"
    assert ay.tick_label.prefix == "$"
    assert ay.tick_label.anchor_at_start is not None


def test_end_to_end_literal_authored_format_is_not_baked_through_real_resolve():
    """The literal mirror of the alias case above, through the same real
    resolve() seam: a hand-typed ``"$~s"`` (not through the alias table)
    must not get the plain-digit bake."""
    board = resolve_chart_style_context(get_theme_style())
    patch = BarChartStylePatch(
        axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format="$~s"))
    )
    chart = BarChart(id="t", type="bar", x="month", y="revenue", style=patch)
    resolved = resolve(chart, _NON_COMPACTING_BAR_DATA, chart_style_context=board)
    ay = resolved.style.axis_y
    # Inline d3 spec passes through unchanged (three-way contract: no trim for inline).
    assert ay.labels.format == "$~s"
    assert ay.ruler is None
    assert ay.tick_label is None


@pytest.mark.parametrize(
    ("theme_name", "multiples"),
    [
        pytest.param("stark", None, id="stark-ticks-count-unset"),
        pytest.param(
            "clarity",
            {"rows": "region", "scale": "independent"},
            id="clarity-independent-scale",
        ),
    ],
)
def test_end_to_end_no_ladder_root_causes_bake_the_sub_unit_guard(
    theme_name, multiples
):
    """Both no-ladder root causes, through the real resolve() seam --
    ``build_resolved_axis`` has no visibility into *why* ``tick_values`` came
    back empty, so both must reach the same bake:

    - ``stark`` leaves ``axis_quantitative.ticks.count`` unset -- no
      ``multiples:`` involved at all, the plain reported case.
    - ``clarity`` (whose ``ticks.count: 6`` would otherwise bake a real
      ladder under the default ``shared`` scale) with
      ``multiples.scale: independent``.

    Exercises the real ``plan_cartesian``/``_bake_cartesian_axes`` provenance
    plumbing, not a hand-passed ``format_authored``/``format_is_alias`` pair
    -- a caller elsewhere in that plumbing hardcoding the wrong pair (as
    histogram's does, deliberately, in ``bar.py``) would leave the isolated
    ``build_resolved_axis`` tests green while stark's real pipeline silently
    stopped reaching this branch.
    """
    board = resolve_chart_style_context(get_theme_style(theme_name))
    data = [
        {"month": m, "region": r, "revenue": 0.05 + 0.01 * m}
        for r in ("West", "East")
        for m in range(6)
    ]
    chart_fields: dict[str, object] = {
        "id": "t",
        "type": "area",
        "query_name": "q",
        "x": "month",
        "y": "revenue",
    }
    if multiples is not None:
        chart_fields["multiples"] = multiples
    chart = AreaChart.model_validate(chart_fields)
    resolved = resolve(chart, data, chart_style_context=board)
    ay = resolved.style.axis_y
    assert ay.tick_values == ()
    assert ay.ruler is None
    assert ay.tick_label is not None
    assert ay.tick_label.si_format == ay.labels.format
    assert is_d3_si_spec(ay.tick_label.si_format)
    assert d3_format_apply(ay.tick_label.format, 0.08) == "0.08"


def test_non_compacting_currency_bakes_anchor_at_start():
    """A non-compacting currency axis must carry tick_label.prefix and
    tick_label.anchor_at_start -- the fields that enable the anchor-only
    prefix. No column-forming padding device exists any more (text-anchor=end
    provides automatic place-value alignment, and a start-anchored axis now
    bakes no ruler/tick_label padding at all -- see build_resolved_axis).

    The ascending ladder [0, 2k, 4k, 6k, 8k] has its magnitude-extreme at
    the END, so anchor_at_start is False.
    """
    ay_merged = _merged_axis_y()
    ay_currency = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"format": "$~s"})}
    )
    ticks = (0.0, 2_000.0, 4_000.0, 6_000.0, 8_000.0)
    ay = build_resolved_axis(
        ay_currency,
        tick_values=ticks,
        format_authored=True,
        format_is_alias=True,
        column_forming=True,
        edge="right",
        chart_id="test",
    )

    assert ay.tick_label.format == ",.0~f"
    assert ay.tick_label.prefix == "$"
    # Magnitude-extreme (8,000) is the last tick of ascending ladder.
    assert ay.tick_label.anchor_at_start is False


def test_non_compacting_currency_mixed_sign_anchors_on_largest_positive():
    """A non-compacting currency axis whose ladder crosses zero with a larger
    negative magnitude than positive anchors '$' on the largest positive tick.

    (-1000, -500, 0, 500): the largest positive (500) is the last tick in the
    ascending ladder, so anchor_at_start is False.
    """
    ay_merged = _merged_axis_y()
    ay_currency = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"format": "$~s"})}
    )
    ticks = (-1000.0, -500.0, 0.0, 500.0)
    ay = build_resolved_axis(
        ay_currency,
        tick_values=ticks,
        format_authored=True,
        format_is_alias=True,
        column_forming=True,
        edge="right",
        chart_id="test",
    )

    assert ay.tick_label is not None
    assert ay.tick_label.prefix == "$"
    # 500 (the largest positive) is the last tick — anchor_at_start must be False.
    assert ay.tick_label.anchor_at_start is False


def test_non_compacting_currency_all_negative_anchors_on_most_negative():
    """A non-compacting currency axis with no positive ticks anchors '$' on the
    most-negative tick (the first in the ascending ladder, not on the 0 cap).

    (-800, -400, 0): no positive tick exists; the most-negative (-800) is the
    first tick, so anchor_at_start is True. Anchoring on 0 would hide the
    symbol on a tick that carries no magnitude.
    """
    ay_merged = _merged_axis_y()
    ay_currency = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"format": "$~s"})}
    )
    ticks = (-800.0, -400.0, 0.0)
    ay = build_resolved_axis(
        ay_currency,
        tick_values=ticks,
        format_authored=True,
        format_is_alias=True,
        column_forming=True,
        edge="right",
        chart_id="test",
    )

    assert ay.tick_label is not None
    assert ay.tick_label.prefix == "$"
    # -800 (the most-negative) is the first tick — anchor_at_start must be True.
    assert ay.tick_label.anchor_at_start is True


def test_non_compacting_ladder_no_stray_decimals_on_an_integer_step():
    ay_merged = _merged_axis_y()
    ticks = (
        0.0,
        500.0,
        1000.0,
        1500.0,
        2000.0,
        2500.0,
        3000.0,
        3500.0,
        4000.0,
        4500.0,
    )
    ay = build_resolved_axis(
        ay_merged,
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.labels.format == ".3~s"
    assert ay.tick_label.format == ",.0~f"
    assert d3_format_apply(ay.tick_label.format, 4500.0) == "4,500"


def test_non_compacting_sub_unit_ladder_writes_its_digits_out():
    """A milli-band ladder takes the plain-digit rewrite like any other
    non-compacting ladder: 0.001 renders "0.001", not d3's "1m".

    The precision comes from the step (0.001 -> three places), so the smallest
    rung keeps its own digits rather than rounding to a false "0".
    """
    ay_merged = _merged_axis_y()
    ticks = (0.001, 0.002, 0.003, 0.004, 0.005)
    ay = build_resolved_axis(
        ay_merged,
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.tick_label.format == ",.3~f"
    assert d3_format_apply(ay.tick_label.format, 0.001) == "0.001"
    assert d3_format_apply(ay.tick_label.format, 0.005) == "0.005"


def test_non_compacting_sub_one_ladder_never_paints_a_milli_suffix():
    """The reported bug: 0 - 0.5 under the theme's own SI default painted
    "100m"/"500m" -- d3's milli prefix, which a reader parses as million.

    Nothing chose SI for this ladder (``shared_scale_for_ladder`` declines
    below thousands); the suffix was the theme placeholder leaking through a
    hole in the dispatch. It writes its digits out instead.
    """
    ay_merged = _merged_axis_y()
    ticks = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5)
    ay = build_resolved_axis(
        ay_merged,
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.tick_label.format == ",.1~f"
    painted = [d3_format_apply(ay.tick_label.format, t) for t in ticks]
    assert painted == ["0", "0.1", "0.2", "0.3", "0.4", "0.5"]


def _non_compacting_ladders() -> list[tuple[float, ...]]:
    """One ladder per {1, 2, 5} * 10**k step from pico upward, each run from
    zero and again from a base big enough that the step is inexact there.

    The second base is the point: a ladder based at zero has a clean step, so
    it hides any decision that reads the step's float residue. Two shapes drop
    out -- a ladder that compacts (``shared_scale_for_ladder`` owns those, and
    the SI suffix is its answer, not a fall-through), and one whose base is
    coarse enough against its step to land two rungs on the same double --
    wholly (every rung identical) or in ulp-sized runs.
    """
    ladders = []
    for k in range(-12, 4):
        for mantissa in (1, 2, 5):
            for base in (0.0, 1e7):
                ticks = tuple(base + i * mantissa * 10.0**k for i in range(6))
                if len(set(ticks)) == len(ticks) and (
                    shared_scale_for_ladder(list(ticks)) is None
                ):
                    ladders.append(ticks)
    return ladders


@pytest.mark.parametrize("ticks", _non_compacting_ladders())
def test_every_non_compacting_ladder_leaves_the_cascade_with_a_chosen_spec(ticks):
    """The property the milli bug violated: when nothing chooses SI, something
    else must choose -- the theme's placeholder spec never reaches paint.

    Both halves matter. A ladder with no ``tick_label`` paints straight from
    ``labels.format`` (the theme's ``.3~s``), which below 1 spells d3's
    sub-unit prefixes. And a chosen spec still has to say something true about
    every tick: two ticks collapsing to one string is the "renders every tick
    as 0" failure, one order of magnitude at a time.
    """
    ay = build_resolved_axis(
        _merged_axis_y(),
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.tick_label is not None
    painted = [d3_format_apply(ay.tick_label.format, t) for t in ticks]
    assert len(set(painted)) == len(ticks)


def test_a_sub_cent_step_on_a_millions_ladder_keeps_its_ticks_distinct():
    """A near-constant metric in the millions, read at sub-cent resolution.

    The step is inexact there -- 0.01 apart on values near 1e7 is
    0.00999999977... as a double -- and the register split must not read that
    residue as "too fine for fixed point": the ladder's rungs genuinely differ,
    and one scientific spec at three significant figures paints all four "1e+7".

    Distinct is all this pins. The residue reaches the labels too
    (10,000,000.0099999998), which is what the axis painted before the register
    split existed; clamping precision to the ticks' own resolution is its own
    question.
    """
    ticks = (10_000_000.0, 10_000_000.01, 10_000_000.02, 10_000_000.03)
    ay = build_resolved_axis(
        _merged_axis_y(),
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.tick_label is not None
    painted = [d3_format_apply(ay.tick_label.format, t) for t in ticks]
    assert len(set(painted)) == len(ticks)


def test_a_ladder_below_fixed_point_reach_takes_scientific_not_a_false_zero():
    """A step finer than ten decimal places has no fixed-point spec at all --
    the precision derived from it would print every tick "0". That ladder gets
    an explicit register (scientific) rather than falling through to the theme
    placeholder.
    """
    ticks = (0.0, 1e-11, 2e-11, 3e-11)
    ay = build_resolved_axis(
        _merged_axis_y(),
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.tick_label.decimal_pad_table == ()
    painted = [d3_format_apply(ay.tick_label.format, t) for t in ticks]
    assert painted == ["0e+0", "1e-11", "2e-11", "3e-11"]


def test_a_ladder_less_axis_bakes_a_per_tick_guard_instead_of_a_fixed_spec():
    """An axis with no baked ladder (a theme leaving `ticks.count` unset, or
    `multiples.scale: independent`) lets Vega pick its own ticks, so no
    step-derived spec can be chosen for them the way a real ladder gets one.

    It still needs the milli-misread guard: `tick_label.si_format` carries
    the axis's own untouched SI spec (for a tick at or above 1 -- the
    house's k/M compaction stays exactly what it was), and `tick_label.format`
    carries `sub_unit_digit_format`'s significant-digit rewrite (for a tick
    below 1, where nothing chose SI to begin with). Choosing between the two
    per tick, not baking one fixed spec here, is `inject_axis_numeral_expr`'s
    job -- see its own tests.
    """
    ay = build_resolved_axis(
        _merged_axis_y(),
        tick_values=(),
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.ruler is None
    assert ay.tick_label is not None
    assert ay.tick_label.si_format == ay.labels.format
    assert is_d3_si_spec(ay.tick_label.si_format)
    assert ay.tick_label.format == sub_unit_digit_format(ay.labels.format)
    assert ay.tick_label.scientific_format == sub_unit_scientific_format(
        ay.labels.format
    )
    assert d3_format_apply(ay.tick_label.format, 0.3) == "0.3"


def test_a_log_scale_ladder_less_axis_bakes_no_tick_label():
    """A log-scale axis also reaches the ladder-less branch's entry condition
    (`_resolve_cartesian_ticks` returns empty `tick_values` for `type: log`
    unconditionally), but for an unrelated reason -- excluded here rather
    than folded in, because ANY `labelExpr` defeats Vega's own
    `labelOverlap` thinning of a log axis's dense minor-tick ladder,
    regardless of what that expression composes. See
    `test_render_small_multiples.py`-style real-render coverage for the
    render-level consequence this guards against.
    """
    ay_merged = _merged_axis_y()
    ay_log = ay_merged.model_copy(
        update={"scale": BaseScaleStyle(continuous=ScaleContinuousStyle(type="log"))}
    )
    ay = build_resolved_axis(
        ay_log,
        tick_values=(),
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.tick_label is None
    assert ay.ruler is None


def test_a_ladder_less_axis_with_an_authored_literal_format_keeps_it_untouched():
    """The same author opt-out the real-ladder branch honors: a literal
    (non-alias) SI format the author typed themselves is not a theme
    placeholder, so it is not up for rewrite even with no ladder to bake.
    """
    ay = build_resolved_axis(
        _merged_axis_y(),
        tick_values=(),
        format_authored=True,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.tick_label is None
    assert ay.ruler is None
    assert is_d3_si_spec(ay.labels.format)


def test_a_ladder_less_axis_with_an_authored_label_expr_is_not_touched():
    """`label.expr` is the documented full opt-out for the real-ladder branch
    too -- an author who wrote their own Vega expression does not get a
    second one composed on top of it.
    """
    ay_merged = _merged_axis_y()
    ay_expr = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"expr": "datum.label"})}
    )
    ay = build_resolved_axis(
        ay_expr,
        tick_values=(),
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.tick_label is None
    assert ay.ruler is None


def test_a_ladder_whose_rungs_round_to_each_other_derives_no_spec():
    """`nice_tick_values` rounds each rung to 10 places, so a near-degenerate
    span hands back equal adjacent ticks: two rungs, no step between them.

    Deriving a spec from a zero step would put an ordinary axis in the
    scientific register (1 painted as "1e+0"). The ladder carries no step, so
    it gets no rewrite at all -- the same exit a ladder too short to have one
    takes.
    """
    ticks = tuple(nice_tick_values(1.0, 1.0000000001, 6))
    assert ticks[0] == ticks[1]
    ay = build_resolved_axis(
        _merged_axis_y(),
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.tick_label is None


def test_non_compacting_half_step_ladder_keeps_its_half():
    """0, 0.5, 1, 1.5, 2: the 0.5 tier allowance is real -- a half-step must
    render its decimal (1.5), not round to a whole number.
    """
    ay_merged = _merged_axis_y()
    ticks = (0.0, 0.5, 1.0, 1.5, 2.0)
    ay = build_resolved_axis(
        ay_merged,
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.tick_label.format == ",.1~f"
    assert d3_format_apply(ay.tick_label.format, 1.5) == "1.5"
    assert d3_format_apply(ay.tick_label.format, 2.0) == "2"


def test_non_compacting_negative_ladder_keeps_full_magnitude():
    """A negative non-compacting ladder keeps its full magnitude -- -60,000
    must render as "-60,000", not truncated to "-60".
    """
    ay_merged = _merged_axis_y()
    ticks = (-60_000.0, -40_000.0, -20_000.0, 0.0)
    ay = build_resolved_axis(
        ay_merged,
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.tick_label.format == ",.0~f"
    assert d3_format_apply(ay.tick_label.format, -60_000.0) == "−60,000"


def test_non_compacting_bake_does_not_touch_an_authored_expr():
    """The documented full opt-out still wins: an author who wrote their own
    label.expr never has it overwritten by the plain-digit bake, even on a
    non-compacting ladder with an unauthored SI format.
    """
    ay_merged = _merged_axis_y()
    ay_expr = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"expr": "'x'"})}
    )
    ticks = (0.0, 20_000.0, 40_000.0, 60_000.0)
    ay = build_resolved_axis(
        ay_expr,
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.labels.expr == "'x'"
    assert ay.labels.format == ".3~s"
    assert ay.tick_label is None


def test_non_compacting_ladder_native_formatter_key_does_not_crash():
    """A ``PREDEFINED_NATIVE`` key (``percent_number``) is not d3-format
    grammar. Compile rejects it on authored axis format slots
    (``ERR-FORMAT-NATIVE-IN-VEGA-SLOT``); this test checks that
    ``build_resolved_axis`` also handles it gracefully when the resolved
    model carries it (e.g. a theme default written before the compile
    guard was added). The SI check that gates the plain-digit bake must
    recognize it as "not SI" without parsing it as d3, or the ruler
    crashes with a raw ``D3FormatError`` (surfacing as ``ERR-INTERNAL``).
    """
    ay_merged = _merged_axis_y()
    ay_native = ay_merged.model_copy(
        update={
            "labels": ay_merged.labels.model_copy(update={"format": "percent_number"})
        }
    )
    ticks = (0.0, 20.0, 40.0, 60.0)
    ay = build_resolved_axis(
        ay_native,
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.ruler is None
    assert ay.tick_label is None
    assert ay.labels.format == "percent_number"


def test_non_compacting_ladder_strftime_format_does_not_crash():
    """A strftime directive (``%b %Y``) shares the ``format:`` authoring
    surface with d3-format specs but is a different grammar entirely --
    ``validate.formats._validate_spec`` whitelists it via ``is_time_format``
    the same way it whitelists ``PREDEFINED_NATIVE`` keys. Same crash risk
    as the native-formatter case: the SI check must say "not SI" without
    parsing it as d3.
    """
    ay_merged = _merged_axis_y()
    ay_strftime = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"format": "%b %Y"})}
    )
    ticks = (0.0, 20.0, 40.0, 60.0)
    ay = build_resolved_axis(
        ay_strftime,
        tick_values=ticks,
        format_authored=False,
        format_is_alias=False,
        chart_id="test",
    )
    assert ay.ruler is None
    assert ay.tick_label is None
    assert ay.labels.format == "%b %Y"


def test_compacting_ladder_native_formatter_key_does_not_crash():
    """The pre-existing (main) SI check on the compacting branch has the same
    unguarded-parse hazard -- a compile-accepted native-formatter format on
    a ladder that would otherwise compact must not crash either.
    """
    ay_merged = _merged_axis_y()
    ay_native = ay_merged.model_copy(
        update={
            "labels": ay_merged.labels.model_copy(update={"format": "percent_number"})
        }
    )
    ay = build_resolved_axis(
        ay_native,
        tick_values=_ANCHOR_TICKS,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    assert ay.ruler is None
    assert ay.labels.format == "percent_number"


def test_non_compacting_native_formatter_axis_renders_end_to_end():
    """Full pipeline, not just ``build_resolved_axis`` in isolation: a real
    theme-cascade ``axis_quantitative.labels.format: percent_number`` on a
    chart whose y ladder does not compact must reach ``render_chart(...,
    format="svg")`` without raising -- ``ERR-INTERNAL`` (an uncaught
    ``D3FormatError``) is exactly the failure this CRITICAL closes.
    ``format="json"`` does not exercise ``build_resolved_axis``'s tick-ladder
    path at all (verified: it stayed green even reverting the fix), so this
    must use ``"svg"``, the format ``dct render`` actually uses.

    That still leaves an unrelated, pre-existing gap live past this test:
    Vega's own format grammar for axis ticks is d3-format only, so
    ``percent_number`` (a Python-only ``PREDEFINED_NATIVE`` callable, never
    valid Vega grammar) fails inside vl-convert's JS layer regardless of
    this fix, exactly as it would for any axis authoring a native-formatter
    key, ruler feature or not. Confirmed separately (``dct render
    --allow-chart-errors``) that this degrades gracefully to a per-chart
    ``ERR-CHART-PAINTED-NO-MARKS``, not a whole-render crash -- so
    ``render_chart`` below still returns a real SVG string rather than
    raising, which is what this test actually pins.
    """
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.render.chart.vega_lite import render_chart

    patch = StylePatch.model_validate(
        {"charts": {"axis_quantitative": {"labels": {"format": "percent_number"}}}}
    )
    resolved, chart_style_context = resolve_style_and_context(get_theme_style(), patch)
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "c",
            "type": "bar",
            "x": "cat",
            "y": "value",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
        }
    )
    data = [
        {"cat": "a", "value": 20.0},
        {"cat": "b", "value": 40.0},
        {"cat": "c", "value": 60.0},
    ]
    svg_output = render_chart(chart, resolved, chart_style_context, data, format="svg")
    assert svg_output


def test_ruler_and_tick_label_are_mutually_exclusive():
    """ResolvedAxisStyle's invariant: a ladder compacts or it doesn't -- the
    two baked outcomes can never both be set.
    """
    import dataclasses

    ay_merged = _merged_axis_y()
    compacting = build_resolved_axis(
        ay_merged,
        tick_values=_ANCHOR_TICKS,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    assert compacting.ruler is not None
    assert compacting.tick_label is None
    with pytest.raises(ValueError, match="mutually exclusive"):
        dataclasses.replace(compacting, tick_label=ResolvedTickLabel(format=",.0~f"))


def test_tick_label_si_format_and_scientific_format_are_set_together():
    """ResolvedTickLabel's own invariant, sibling to the one just above:
    ``si_format``/``scientific_format`` are the ladder-less per-tick guard's
    two arms -- one without the other is not a state anything ever needs to
    represent, and ``inject_axis_numeral_expr`` (the sole consumer) relies on
    this to build the guard unconditionally, with no defensive fallback.
    """
    with pytest.raises(ValueError, match="si_format and .scientific_format"):
        ResolvedTickLabel(format=".3~r", si_format=".3~s")
    with pytest.raises(ValueError, match="si_format and .scientific_format"):
        ResolvedTickLabel(format=".3~r", scientific_format=".3~e")
    # Both set, or both unset, are the only valid states -- no raise.
    ResolvedTickLabel(format=".3~r", si_format=".3~s", scientific_format=".3~e")
    ResolvedTickLabel(format=",.0~f")


def test_non_compacting_end_anchored_currency_splits_prefix():
    """End-anchored (left edge, text-anchor=end) non-compacting currency axis
    must set tick_label.prefix and tick_label.anchor_at_start.

    text-anchor=end provides automatic place-value alignment (the ones digit
    is always last, at the same anchor x) -- no padding device is needed.
    Only the prefix needs anchor-only gating to avoid "$8,000 / $6,000..."
    noise.
    """
    ay_merged = _merged_axis_y()
    ay_currency = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"format": "$~s"})}
    )
    ticks = (0.0, 2_000.0, 4_000.0, 6_000.0, 8_000.0)
    # edge="left" -> render_align="right" -> start_anchored=False (end-anchored)
    ay = build_resolved_axis(
        ay_currency,
        tick_values=ticks,
        format_authored=True,
        format_is_alias=True,
        column_forming=True,
        edge="left",
        chart_id="test",
    )
    assert ay.ruler is None
    assert ay.tick_label.format == ",.0~f"
    # Prefix is split out, not baked into tick_label.format.
    assert ay.tick_label.prefix != ""
    assert ay.tick_label.anchor_at_start is not None


def test_currency_prefix_is_bare_no_configured_gap():
    """The prefix is the bare currency symbol -- no trailing FIGURE SPACE.

    A guaranteed gap was tried and reverted: it stacked with the digit-field
    alignment padding whenever the resolved tick ladder includes an entry
    Vega drops (nice_tick_values can overshoot the axis's actual domain),
    printing two figure-spaces after "$" instead of one. "$1,000" is meant
    to touch; the render/emitter side is responsible for putting any real
    alignment padding to the LEFT of the prefix instead, never between the
    prefix and the digits (see vl_field_maps.py's inject_axis_numeral_expr).
    """
    ay_merged = _merged_axis_y()
    ay_currency = ay_merged.model_copy(
        update={"labels": ay_merged.labels.model_copy(update={"format": "$~s"})}
    )
    ticks = (0.0, 2_000.0, 4_000.0, 6_000.0, 8_000.0)
    for edge in ("right", "left"):
        ay = build_resolved_axis(
            ay_currency,
            tick_values=ticks,
            format_authored=True,
            format_is_alias=True,
            column_forming=True,
            edge=edge,
            chart_id="test",
        )
        assert ay.tick_label.prefix == "$", (
            f"edge={edge!r}: prefix {ay.tick_label.prefix!r} must be the bare symbol"
        )


def test_ruler_carve_out_applies_to_inline_d3_si_spec() -> None:
    """ruler_digit_format (the ruler's fixed-point retyping) applies to an inline d3
    SI spec, not only to predefined format members.

    The ruler has already divided the value by the shared magnitude, so feeding
    the scaled result back through d3's own 's' type would pick a second suffix.
    That is a correctness issue (not house cosmetics), so the retyping applies
    regardless of whether the format came from an enum member or was written inline.

    Also verifies that an inline ".3s" spec does NOT receive the round-aware "~"
    injection -- that injection is house cosmetics, applied only for predefined
    format members, not for literal authored d3 specs.
    """
    from dbt_charts.core.compile.format import resolve_format

    # Inline d3 spec: should NOT get round-aware trim injected.
    resolved = resolve_format(".3s", formats=None)
    assert resolved == ".3s", (
        f"inline d3 spec must not receive round-aware ~ injection; got {resolved!r}"
    )

    # ruler_digit_format retypes the SI spec to fixed-point regardless of source.
    from dbt_charts.core.text.numeral_scale import ruler_digit_format

    _, digit_spec = ruler_digit_format(resolved)
    assert "f" in digit_spec, (
        f"ruler_digit_format must retype an inline SI spec to fixed-point; got {digit_spec!r}"
    )
    assert "s" not in digit_spec, (
        f"ruler_digit_format must remove the 's' type from the spec; got {digit_spec!r}"
    )


def test_end_to_end_inline_si_spec_builds_ruler_on_an_end_anchored_edge() -> None:
    """An inline d3 SI spec on axis_y must still produce a ruler through the full
    resolve() pipeline, on an edge where a ruler can exist at all.

    _build_ruler's ruler is gated on the SI-shape verdict (si_format), which
    the caller computes, not on format provenance
    (predefined vs. inline) -- an inline ".2s" is not an engine-predefined name,
    so it is never subject to build_resolved_axis's forced-right-align
    treatment (that's alias-only) and keeps whatever anchoring the axis
    resolves to on its own. Explicit position: left is naturally end-anchored
    by VL's own per-orient default, which is the only anchoring a ruler can
    still exist for at all -- a start-anchored axis (the unauthored default
    for this chart's right edge) now bakes no ruler regardless of format,
    since the digit-field device that used to align a start-anchored ladder
    is gone (see build_resolved_axis's own docstring).
    """
    from dbt_charts.core.compile.models.chart.normalized import LineChart
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch

    board = resolve_chart_style_context(get_theme_style())
    patch = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            position="left", labels=AxisLabelStylePatch(format=".2s")
        )
    )
    data = [
        {"m": 1, "revenue": 2_000_000},
        {"m": 2, "revenue": 4_000_000},
        {"m": 3, "revenue": 8_000_000},
    ]
    chart = LineChart(id="t", type="line", x="m", y="revenue", style=patch)
    resolved = resolve(chart, data, chart_style_context=board)
    ay = resolved.style.axis_y
    assert ay.ruler is not None, (
        "inline SI spec '.2s' on an end-anchored edge must build a ruler; "
        "the ruler is gated on the SI-shape verdict, not on format provenance"
    )
