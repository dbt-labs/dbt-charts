"""Unit tests for the tick-label text generation this module's own gutter
measurement (now ``measured_label_padding``/``cap_padding_to_label_limit``
in ``core/utils.py`` — see ``test_utils.py`` for their tests) measures.

The own-orient-side gating (align == the axis's own side, baked tick content
present) lives inline at each call site (vl_field_maps.py, mirror_axis.py,
bar.py) rather than as a shared function, so it's covered by the integration
tests there (test_vega_lite_axes.py, test_render_mirror_axis.py,
test_bar_chart_style.py) instead of duplicated here.
"""

from __future__ import annotations

from d3_format import format as d3_format
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedRulerAxis,
    ResolvedTickLabel,
)
from dbt_charts.core.font_measure import RESERVATION_GUARD, compose_suffix_reservation
from dbt_charts.core.fonts import DBT_SANS_TABULAR_FONT_FAMILY
from dbt_charts.core.render.chart.emitters._measured_label_padding import (
    estimated_quantitative_tick_labels,
    numeric_values,
    quantitative_tick_labels,
)
from dbt_charts.core.text.numeral_scale import (
    SuffixMode,
    sub_unit_digit_format,
    sub_unit_scientific_format,
)
from dbt_charts.core.utils import measured_label_padding


class TestQuantitativeTickLabels:
    def test_formats_each_tick_through_the_same_d3_spec_vl_uses(self) -> None:
        assert quantitative_tick_labels((0.0, 5000.0, 15000.0), "~s", ruler=None) == [
            "0",
            "5k",
            "15k",
        ]

    def test_empty_tick_values_yields_empty_labels(self) -> None:
        assert quantitative_tick_labels((), "~s", ruler=None) == []

    def test_no_ruler_is_the_plain_d3_render(self) -> None:
        """ruler=None behavior is unchanged -- a ladder that doesn't compact
        still renders straight from the literal spec.
        """
        assert quantitative_tick_labels((0.0, 20.0, 40.0), ".3~s", ruler=None) == [
            "0",
            "20",
            "40",
        ]


class TestQuantitativeTickLabelsEndAnchoredPrefix:
    """Non-compacting axis with a currency prefix, end-anchored (no padding).
    The anchor tick's label must include the prefix for gutter measurement;
    others must not.
    """

    def test_anchor_gets_prefix_others_do_not(self) -> None:
        """Anchor (last tick of ascending ladder) carries the prefix; the rest
        carry plain digits. tick_label.counts is None -- no RESERVATION_GUARD.
        """
        ticks = (0.0, 2_000.0, 4_000.0, 6_000.0, 8_000.0)
        format_spec = ",.0f"
        prefix = "$"

        labels = quantitative_tick_labels(
            ticks,
            format_spec,
            ruler=None,
            tick_label=ResolvedTickLabel(
                format=format_spec, prefix=prefix, anchor_at_start=False
            ),
        )

        # Anchor is the last tick (anchor_at_start=False).
        assert labels[-1] == prefix + "8,000"
        for label in labels[:-1]:
            assert not label.startswith(prefix), (
                f"non-anchor label should not have prefix: {label!r}"
            )

    def test_anchor_at_start_none_repeats_prefix_on_every_nonzero_tick(self) -> None:
        """Non-column-forming companion (``anchor_at_start=None``): every
        non-zero tick carries the prefix, mirroring
        ``inject_axis_numeral_expr``'s labelExpr -- reached in production via
        ``features/mirror_axis.py``, which passes a horizontal measure axis's
        resolved ``tick_label`` through with no reachability gate.
        """
        ticks = (0.0, 2_000.0, 4_000.0, 6_000.0, 8_000.0)
        format_spec = ",.0f"
        prefix = "$"

        labels = quantitative_tick_labels(
            ticks,
            format_spec,
            ruler=None,
            tick_label=ResolvedTickLabel(
                format=format_spec, prefix=prefix, anchor_at_start=None
            ),
        )

        assert labels == ["0", "$2,000", "$4,000", "$6,000", "$8,000"]

    def test_anchor_label_is_wider_than_non_anchor(self) -> None:
        """Anchor with prefix must produce a wider gutter measurement than
        the bare non-anchor labels -- the gutter is sized for the widest label.
        """
        ticks = (0.0, 2_000.0, 4_000.0, 6_000.0, 8_000.0)
        format_spec = ",.0f"
        prefix = "$"

        labels = quantitative_tick_labels(
            ticks,
            format_spec,
            ruler=None,
            tick_label=ResolvedTickLabel(
                format=format_spec, prefix=prefix, anchor_at_start=False
            ),
        )

        prefixed_gutter = measured_label_padding(labels, "Inter", 12.0)
        bare_labels = [d3_format(format_spec, v) for v in ticks]
        bare_gutter = measured_label_padding(bare_labels, "Inter", 12.0)
        assert prefixed_gutter > bare_gutter, (
            f"gutter must be wider when anchor carries prefix: "
            f"prefixed={prefixed_gutter:.1f}px, bare={bare_gutter:.1f}px"
        )

    def test_no_reservation_guard_in_end_anchored_labels(self) -> None:
        """End-anchored labels must NOT contain RESERVATION_GUARD -- that's
        the start-anchored padding device, not needed here.
        """
        ticks = (0.0, 2_000.0, 4_000.0, 6_000.0, 8_000.0)
        format_spec = ",.0f"
        prefix = "$"

        labels = quantitative_tick_labels(
            ticks,
            format_spec,
            ruler=None,
            tick_label=ResolvedTickLabel(
                format=format_spec, prefix=prefix, anchor_at_start=False
            ),
        )
        for label in labels:
            assert RESERVATION_GUARD not in label, (
                f"end-anchored label {label!r} must not contain RESERVATION_GUARD"
            )

    def test_negative_anchor_renders_sign_before_symbol(self) -> None:
        """Regression: negative anchor tick must produce '-$500' not '$-500'.

        The bug was hand-concatenating the prefix before the d3-formatted digit
        string, which already carries a minus sign for negative values. The fix
        passes the symbol into the d3 spec so d3-format's sign-ordering applies.
        """
        ticks = (-500.0, 0.0)
        format_spec = ",.0f"
        prefix = "$"

        labels = quantitative_tick_labels(
            ticks,
            format_spec,
            ruler=None,
            tick_label=ResolvedTickLabel(
                format=format_spec, prefix=prefix, anchor_at_start=True
            ),
        )
        # d3 emits U+2212 MINUS SIGN, not ASCII hyphen.
        assert labels[0] == "−$500", f"expected sign before symbol, got {labels[0]!r}"
        assert labels[1] == "0"


class TestQuantitativeTickLabelsWithRuler:
    """These pin the same worked examples
    ``test_axis_numeral_expr.py::TestRealVegaRendering`` verifies through
    real Vega -- this module must mirror what the axis actually paints, not
    the literal d3 spec (that assumption is exactly what this task
    falsifies; see the module docstring). ``reservation`` is built via
    ``compose_suffix_reservation`` -- the same helper ``build_resolved_axis``
    bakes it with -- rather than a hand-typed literal, so these tests can't
    drift from what the real composition actually produces.
    """

    def test_anchor_mode_450k_ladder_matches_the_real_render(self) -> None:
        ruler = ResolvedRulerAxis(
            exponent=3,
            mode=SuffixMode.ANCHOR,
            reserve=True,
            prefix_repeats=False,
            prefix="$",
            digit_spec=",.1~f",
            anchor_at_start=False,
            reservation=compose_suffix_reservation(" K", DBT_SANS_TABULAR_FONT_FAMILY),
        )
        ticks = (0.0, 100_000.0, 200_000.0, 300_000.0, 400_000.0, 500_000.0)
        labels = quantitative_tick_labels(ticks, "$.3~s", ruler=ruler)
        pad = ruler.reservation
        assert labels == [
            "0" + pad,
            "100" + pad,
            "200" + pad,
            "300" + pad,
            "400" + pad,
            "$500 K",
        ]

    def test_repeat_mode_900k_ladder_matches_the_real_render(self) -> None:
        """A column-forming axis (``reserve=True``): REPEAT is the ladder's
        own magnitude-driven suffix register, independent of the prefix,
        which still anchors on one tick (``prefix_repeats=False``).
        """
        ruler = ResolvedRulerAxis(
            exponent=3,
            mode=SuffixMode.REPEAT,
            reserve=True,
            prefix_repeats=False,
            prefix="$",
            digit_spec=",.1~f",
            anchor_at_start=False,
            reservation=compose_suffix_reservation("k", DBT_SANS_TABULAR_FONT_FAMILY),
        )
        ticks = (0.0, 200_000.0, 400_000.0, 600_000.0, 800_000.0, 1_000_000.0)
        labels = quantitative_tick_labels(ticks, "$.3~s", ruler=ruler)
        pad = ruler.reservation
        assert labels == [
            "0" + pad,
            "200k",
            "400k",
            "600k",
            "800k",
            "$1,000k",
        ]

    def test_repeat_mode_900k_ladder_non_column_forming_repeats_the_prefix(
        self,
    ) -> None:
        """The non-column-forming companion: same ladder, ``reserve=False``
        and ``prefix_repeats=True`` -- the prefix now repeats on every
        non-zero tick.
        """
        ruler = ResolvedRulerAxis(
            exponent=3,
            mode=SuffixMode.REPEAT,
            reserve=False,
            prefix_repeats=True,
            prefix="$",
            digit_spec=",.1~f",
            anchor_at_start=False,
            reservation="",
        )
        ticks = (0.0, 200_000.0, 400_000.0, 600_000.0, 800_000.0, 1_000_000.0)
        labels = quantitative_tick_labels(ticks, "$.3~s", ruler=ruler)
        assert labels == [
            "0",
            "$200k",
            "$400k",
            "$600k",
            "$800k",
            "$1,000k",
        ]

    def test_zero_reserves_even_when_it_is_the_only_other_tick(self) -> None:
        ruler = ResolvedRulerAxis(
            exponent=3,
            mode=SuffixMode.ANCHOR,
            reserve=True,
            prefix_repeats=False,
            prefix="",
            digit_spec=",.1~f",
            anchor_at_start=False,
            reservation=compose_suffix_reservation(" K", DBT_SANS_TABULAR_FONT_FAMILY),
        )
        labels = quantitative_tick_labels((0.0, 500_000.0), ".3~s", ruler=ruler)
        assert labels[0] == "0" + ruler.reservation
        assert labels[1] == "500 K"

    def test_non_column_forming_never_reserves(self) -> None:
        """A horizontal ruler's ruler.reserve is False -- even though the
        ladder compacts, no tick pads, matching inject_axis_numeral_expr's
        own horizontal branch.
        """
        ruler = ResolvedRulerAxis(
            exponent=3,
            mode=SuffixMode.REPEAT,
            reserve=False,
            prefix_repeats=True,
            prefix="$",
            digit_spec=",.1~f",
            anchor_at_start=False,
            reservation="",
        )
        ticks = (0.0, 200_000.0, 400_000.0, 600_000.0, 800_000.0, 1_000_000.0)
        labels = quantitative_tick_labels(ticks, "$.3~s", ruler=ruler)
        assert labels == ["0", "$200k", "$400k", "$600k", "$800k", "$1,000k"]

    def test_ruler_negative_anchor_renders_sign_before_symbol(self) -> None:
        """Regression: compacting (ruler) path with a currency prefix and a
        negative anchor tick must produce '-$500 K' not '$-500 K'.
        """
        ruler = ResolvedRulerAxis(
            exponent=3,
            mode=SuffixMode.ANCHOR,
            reserve=True,
            prefix_repeats=False,
            prefix="$",
            digit_spec=",.1~f",
            anchor_at_start=True,
            reservation=compose_suffix_reservation(" K", DBT_SANS_TABULAR_FONT_FAMILY),
        )
        ticks = (-500_000.0, -300_000.0, -100_000.0, 0.0)
        labels = quantitative_tick_labels(ticks, "$.3~s", ruler=ruler)
        pad = ruler.reservation
        # d3 emits U+2212 MINUS SIGN, not ASCII hyphen.
        assert labels[0] == "−$500 K", f"expected sign before symbol, got {labels[0]!r}"
        assert labels[1] == "−300" + pad
        assert labels[2] == "−100" + pad
        assert labels[3] == "0" + pad

    def test_negative_zero_topped_ladder_anchors_on_the_extreme_not_the_last_tick(
        self,
    ) -> None:
        """Regression: a zero-anchored measure axis over all-negative data
        bakes a ladder that tops out at 0 (``_resolve_cartesian_ticks``
        anchors zero-anchored ticks at 0 regardless of data sign). The
        anchor is the extreme-by-magnitude tick (-600,000), NOT the
        last-drawn one (0) -- gating on position instead of
        ``ruler.anchor_at_start`` silently drops the magnitude suffix
        entirely, since 0 never carries one.
        """
        ruler = ResolvedRulerAxis(
            exponent=3,
            mode=SuffixMode.ANCHOR,
            reserve=True,
            prefix_repeats=False,
            prefix="",
            digit_spec=",.1~f",
            anchor_at_start=True,
            reservation=compose_suffix_reservation(" K", DBT_SANS_TABULAR_FONT_FAMILY),
        )
        ticks = (-600_000.0, -400_000.0, -200_000.0, 0.0)
        labels = quantitative_tick_labels(ticks, ".3~s", ruler=ruler)
        pad = ruler.reservation
        # d3 emits U+2212 MINUS SIGN, not ASCII hyphen, for a negative value.
        assert labels == [
            "\u2212600 K",
            "\u2212400" + pad,
            "\u2212200" + pad,
            "0" + pad,
        ]


class TestNumericValues:
    def test_extracts_finite_numeric_values(self) -> None:
        data = [{"revenue": 5}, {"revenue": 1_500_000}, {"revenue": None}]
        assert numeric_values(data, ("revenue",)) == [5.0, 1_500_000.0]

    def test_skips_bools_and_missing_and_non_numeric(self) -> None:
        data = [
            {"revenue": True},
            {"other": 10},
            {"revenue": "not a number"},
            {"revenue": 42},
        ]
        assert numeric_values(data, ("revenue",)) == [42.0]

    def test_combines_multiple_fields_for_multi_metric_charts(self) -> None:
        data = [{"a": 1, "b": 200}, {"a": 2, "b": 300}]
        assert numeric_values(data, ("a", "b")) == [1.0, 2.0, 200.0, 300.0]

    def test_no_fields_yields_empty(self) -> None:
        assert numeric_values([{"revenue": 5}], ()) == []


class TestEstimatedQuantitativeTickLabels:
    """Upper-bound gutter estimate for an axis whose real Vega-Lite ticks
    aren't known (dbt charts hasn't baked ``tick_values`` — e.g. a theme that
    leaves ``axis.ticks.count`` unset). Must never underestimate: a plain
    (non-SI) format where Vega-Lite's own domain-nicing rounds the max up
    across a digit boundary must still be covered.
    """

    def test_empty_values_yields_empty_labels(self) -> None:
        assert estimated_quantitative_tick_labels([], "~s") == []

    def test_covers_the_raw_data_extent(self) -> None:
        # domain_min is clamped to include 0 (common zero-anchored scale);
        # the max side must still be covered by an M-magnitude candidate.
        labels = estimated_quantitative_tick_labels([5.0, 1_500_000.0], "~s")
        assert "0" in labels
        assert any(label.endswith("M") for label in labels)

    def test_covers_a_nice_rounded_up_domain_max_for_plain_format(self) -> None:
        """The exact failure mode this exists to close: a plain thousands
        format where Vega-Lite's `nice: true` domain rounds 999,950 up to
        1,000,000 — one digit-group wider than the raw max.
        """
        labels = estimated_quantitative_tick_labels([0.0, 999_950.0], ",.0f")
        assert "1,000,000" in labels

    def test_wider_domain_yields_a_wider_max_label(self) -> None:
        narrow = estimated_quantitative_tick_labels([0.0, 5.0], ",.0f")
        wide = estimated_quantitative_tick_labels([0.0, 1_500_000.0], ",.0f")
        narrow_max_len = max(len(label) for label in narrow)
        wide_max_len = max(len(label) for label in wide)
        assert wide_max_len > narrow_max_len

    def test_si_format_measures_what_the_ladder_less_guard_actually_paints(
        self,
    ) -> None:
        """The paint/measure invariant this module's own docstring states:
        a ladder-less axis (``ResolvedTickLabel.si_format`` set) doesn't
        paint every candidate from ``format_spec`` alone -- a value at or
        above 1 paints through ``si_format`` instead
        (``inject_axis_numeral_expr``'s per-tick guard). Passing ``format_spec``
        unconditionally here, as the pre-existing signature did, measures
        "1500000" for a candidate Vega-Lite actually paints "1.5M" -- reserving
        gutter for a string the axis never draws.
        """
        plain_only = estimated_quantitative_tick_labels([0.0, 1_500_000.0], ".3~r")
        guarded = estimated_quantitative_tick_labels(
            [0.0, 1_500_000.0], ".3~r", si_format=".3~s"
        )
        assert any(len(label) > 6 for label in plain_only)
        assert all(len(label) <= 6 for label in guarded)
        assert any(label.endswith("M") for label in guarded)

    def test_si_format_still_uses_plain_format_below_one(self) -> None:
        labels = estimated_quantitative_tick_labels(
            [0.0, 0.5], ".3~r", si_format=".3~s"
        )
        assert "0.5" in labels

    def test_scientific_format_covers_a_deep_sub_unit_candidate(self) -> None:
        labels = estimated_quantitative_tick_labels(
            [0.0, 1e-11],
            sub_unit_digit_format(".3~s"),
            si_format=".3~s",
            scientific_format=sub_unit_scientific_format(".3~s"),
        )
        assert "1e-11" in labels
        assert not any("0.0000000000" in label for label in labels)

    def test_zero_never_measured_as_scientific(self) -> None:
        labels = estimated_quantitative_tick_labels(
            [0.0, 1e-11],
            sub_unit_digit_format(".3~s"),
            si_format=".3~s",
            scientific_format=sub_unit_scientific_format(".3~s"),
        )
        assert "0" in labels
