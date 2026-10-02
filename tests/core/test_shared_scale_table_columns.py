"""Regression tests: table columns wired onto the shared-scale ruler machinery.

An SI/compact-formatted table column (`number`, or any inline `~s`
spec) picks a single shared magnitude across its rows -- like an axis
ruler's tick ladder -- instead of each row picking its own suffix
independently. See
tasks/workstreams/graph-library/tasks/wire-table-columns-onto-the-shared-scale-ruler-machinery-for-si-compact-formats.md.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from d3_format import format as _d3_format, parse as _d3_parse
from dbt_charts.core.compile.models.style.authored.table import TableColumnConfig
from dbt_charts.core.compile.models.style.resolved.table import (
    ResolvedColumnSharedScale,
)
from dbt_charts.core.compile.resolve.chart._table import _with_resolved_scale_stops
from dbt_charts.core.fonts import DBT_SANS_TABULAR_FONT_FAMILY
from dbt_charts.core.render.format_utils import format_kpi_parts
from dbt_charts.core.text.numeral_scale import (
    SuffixMode,
    column_digit_format,
    decimal_pad_for,
    tier_distance,
)


class TestColumnDigitFormat:
    """column_digit_format picks decimal precision from a single given scaled
    value. The caller (_with_resolved_scale_stops) is responsible for
    passing the column's *finest* (smallest non-zero, fewest integer digits)
    scaled value -- that is the row that needs the most decimals to hit the
    format's significant-figure count, so pinning precision to it guarantees
    no row in the column undershoots its own sig figs. Passing the column's
    largest value instead (an earlier version of this bake did) silently
    truncates every smaller row -- see TestResolveSharedScaleForTableColumn
    for the caller-side regression pin.
    """

    def test_low_integer_digit_value_keeps_more_decimals(self) -> None:
        # 3,456,789 at tier M (1e6): scaled value is 3.456789, 1 integer
        # digit. 3 sig figs (".3~s") - 1 integer digit = 2 decimals.
        symbol, digit_spec = column_digit_format(".3~s", 3_456_789 / 1e6)
        assert symbol == ""
        assert _d3_parse(digit_spec).precision == 2
        assert _d3_format(digit_spec)(3_456_789 / 1e6) == "3.46"

    def test_high_integer_digit_value_keeps_fewer_decimals(self) -> None:
        # 999,000,000 at tier M: scaled value is 999.0, 3 integer digits.
        # 3 sig figs - 3 integer digits = 0 decimals.
        symbol, digit_spec = column_digit_format(".3~s", 999_000_000 / 1e6)
        assert symbol == ""
        assert _d3_parse(digit_spec).precision == 0
        assert _d3_format(digit_spec)(999_000_000 / 1e6) == "999"

    def test_default_precision_is_six_sig_figs_when_unspecified(self) -> None:
        # No explicit precision on the SI spec -- d3's own default (6 sig figs).
        _, digit_spec = column_digit_format("~s", 1_234_567 / 1e6)
        assert _d3_parse(digit_spec).precision == 5  # 6 sig figs - 1 int digit

    def test_digit_spec_never_types_as_si(self) -> None:
        _, digit_spec = column_digit_format(".3~s", 3_456_789 / 1e6)
        assert _d3_parse(digit_spec).type == "f"


class TestResolveSharedScaleForTableColumn:
    """_with_resolved_scale_stops bakes a ResolvedColumnSharedScale onto a
    column whose rows share a majority SI tier, mirroring the axis-side
    _build_ruler bake.
    """

    def _resolve(
        self, rows: list[dict[str, Any]], fmt: str = "number"
    ) -> ResolvedColumnSharedScale | None:
        columns = {"amount": TableColumnConfig(format=fmt)}
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        return resolved["amount"].shared_scale

    def test_majority_tier_column_resolves_a_shared_scale(self) -> None:
        scale = self._resolve(
            [{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000)]
        )
        assert scale is not None
        assert scale.exponent == 6
        assert scale.mode is SuffixMode.ANCHOR

    def test_precision_comes_from_the_smallest_value_not_the_largest(self) -> None:
        # Regression for the inverted-precision bug: 3,500,000 (smallest,
        # scaled 3.5, 1 integer digit) needs 2 decimals for 3 sig figs;
        # 12,000,000 (largest, scaled 12.0, 2 integer digits) needs only 1.
        # Picking precision from the largest would truncate 3.5 -> "4".
        scale = self._resolve(
            [{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000)]
        )
        assert scale is not None
        magnitude = 10.0**scale.exponent
        assert _d3_format(scale.digit_spec)(3_500_000 / magnitude) == "3.5"
        assert _d3_format(scale.digit_spec)(4_200_000 / magnitude) == "4.2"
        assert _d3_format(scale.digit_spec)(12_000_000 / magnitude) == "12"

    def test_no_majority_tier_column_resolves_no_shared_scale(self) -> None:
        # 500 (no tier -- None) and 1_000_000 (tier 6) tie 1-1; None wins any
        # tie it's part of.
        scale = self._resolve([{"amount": 500}, {"amount": 1_000_000}])
        assert scale is None

    def test_below_compaction_threshold_resolves_no_shared_scale(self) -> None:
        scale = self._resolve([{"amount": 1500}, {"amount": 3000}])
        assert scale is None

    def test_unformatted_column_falls_back_to_number_for_the_gate(self) -> None:
        # No format: authored at all (None, not the "number" string)
        # -- render's own format_kpi_parts(default_number=True) still treats
        # this as SI at paint time, so the resolve-time gate must match or a
        # plain unformatted column would never earn a shared_scale bake.
        columns = {"amount": TableColumnConfig()}
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=[{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000)],
        )
        assert resolved is not None
        assert resolved["amount"].shared_scale is not None
        assert resolved["amount"].shared_scale.exponent == 6

    def test_decimal_rows_resolve_the_same_as_float_rows(self) -> None:
        float_scale = self._resolve(
            [{"amount": v} for v in (3_500_000.0, 4_200_000.0, 12_000_000.0)]
        )
        decimal_scale = self._resolve(
            [
                {"amount": Decimal("3500000")},
                {"amount": Decimal("4200000")},
                {"amount": Decimal("12000000")},
            ]
        )
        assert float_scale is not None
        assert decimal_scale is not None
        assert decimal_scale.exponent == float_scale.exponent
        assert decimal_scale.mode is float_scale.mode

    def test_non_finite_rows_do_not_crash_and_are_excluded(self) -> None:
        # nan/inf must not reach max()/min() in the bake -- coerce_numeric_cell
        # already filters them to None for every other table numeric path;
        # the shared-scale bake must agree, order-independently.
        first = self._resolve(
            [{"amount": v} for v in (float("nan"), 3_500_000, 4_200_000, 12_000_000)]
        )
        second = self._resolve(
            [{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000, float("inf"))]
        )
        assert first is not None
        assert second is not None
        assert first.exponent == second.exponent == 6

    def test_non_si_format_never_resolves_a_shared_scale(self) -> None:
        # A fixed-point format is never SI-shaped -- shared_scale only ever
        # applies to a `~s`-typed spec.
        scale = self._resolve(
            [{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000)],
            fmt=",.0f",
        )
        assert scale is None

    def test_shared_scale_digit_spec_always_types_fixed_point(self) -> None:
        scale = self._resolve(
            [{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000)]
        )
        assert scale is not None
        assert _d3_parse(scale.digit_spec).type == "f"

    def test_repeat_mode_when_column_has_outgrown_its_tier(self) -> None:
        # Extreme scaled value >= 4 integer digits at its own tier triggers
        # REPEAT (mirrors _mode_for_scaled_extreme's ladder contract).
        scale = self._resolve(
            [{"amount": v} for v in (3_500_000, 4_200_000, 1_234_000_000)]
        )
        assert scale is not None
        assert scale.mode is SuffixMode.REPEAT

    def test_value_far_below_the_shared_tier_refuses_the_whole_bake(self) -> None:
        # 500 has no SI tier of its own at all (below even thousands) --
        # more than one tier below the M tier the other two rows share.
        # Deriving precision from a value this far below the majority would
        # force every OTHER row in the column to inherit far more decimal
        # places than the format's own significant-figure count asks for
        # (three sig figs requested, seven delivered, because of one
        # far-below-tier straggler) -- see TestSubTierPrecisionAndRefusal
        # for the case genuinely close enough (one tier below) to accommodate
        # with a few extra decimals instead of refusing.
        scale = self._resolve(
            [{"amount": 500}, {"amount": 3_500_000}, {"amount": 4_200_000}]
        )
        assert scale is None


class TestFormatKpiPartsSharedScale:
    """format_kpi_parts formats through a resolved shared_scale instead of
    re-deriving an independent per-row SI suffix, mirroring
    quantitative_tick_labels's ruler-aware branch. Uses the real resolver
    output (via _resolved_scale) rather than a hand-built ResolvedColumnSharedScale,
    so a wrong digit_spec computation would show up here too.
    """

    def _resolved_scale(
        self, rows: list[dict[str, Any]], fmt: str = "number"
    ) -> ResolvedColumnSharedScale:
        columns = {"amount": TableColumnConfig(format=fmt)}
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        scale = resolved["amount"].shared_scale
        assert scale is not None
        return scale

    def test_anchor_mode_shows_suffix_only_on_anchor_row(self) -> None:
        rows = [{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000)]
        scale = self._resolved_scale(rows)
        assert scale.mode is SuffixMode.ANCHOR
        _, anchor_num, anchor_suffix = format_kpi_parts(
            3_500_000, ".3~s", shared_scale=scale, is_anchor=True
        )
        _, other_num, other_suffix = format_kpi_parts(
            4_200_000, ".3~s", shared_scale=scale, is_anchor=False
        )
        assert anchor_suffix.strip() == "M"
        assert other_suffix == ""
        assert anchor_num == "3.5"
        assert other_num == "4.2"

    def test_repeat_mode_shows_suffix_on_every_nonzero_row(self) -> None:
        rows = [{"amount": v} for v in (3_500_000, 4_200_000, 1_234_000_000)]
        scale = self._resolved_scale(rows)
        assert scale.mode is SuffixMode.REPEAT
        _, anchor_num, anchor_suffix = format_kpi_parts(
            3_500_000, ".3~s", shared_scale=scale, is_anchor=True
        )
        _, other_num, other_suffix = format_kpi_parts(
            4_200_000, ".3~s", shared_scale=scale, is_anchor=False
        )
        # REPEAT speaks the narrative register ("mn"), not analytic ("M").
        assert anchor_suffix.strip() == "mn"
        assert other_suffix.strip() == "mn"
        # Precision comes from the column's smallest value (3.5M, 1 integer
        # digit -> 2 decimals for 3 sig figs), not the largest (1234M, which
        # would truncate every other row to 0 decimals).
        assert anchor_num == "3.5"
        assert other_num == "4.2"

    def test_authored_notation_overrides_the_mode_derived_register(self) -> None:
        from dbt_charts.core.compile.models.primitives import FormatConfig

        rows = [{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000)]
        scale = self._resolved_scale(rows)  # ANCHOR mode -> analytic by default
        _, _, suffix = format_kpi_parts(
            3_500_000,
            FormatConfig(spec=".3~s", notation="narrative"),
            shared_scale=scale,
            is_anchor=True,
        )
        assert suffix.strip() == "mn"

    def test_zero_never_carries_a_suffix(self) -> None:
        rows = [{"amount": v} for v in (3_500_000, 4_200_000, 1_234_000_000)]
        scale = self._resolved_scale(rows)  # REPEAT mode
        _, number_str, suffix = format_kpi_parts(
            0, ".3~s", shared_scale=scale, is_anchor=False
        )
        assert suffix == ""
        assert number_str == "0"

    def test_scaled_value_never_reformats_through_the_original_si_spec(self) -> None:
        # A REPEAT-mode value >= 1000 at its own tier: re-entering it through
        # the column's original `~s` spec would pick a *second* SI suffix
        # ("1k") on top of the shared one -- the double-suffix bug
        # ruler_digit_format's own docstring warns about. digit_spec is
        # always fixed-point, so this must render plain grouped digits.
        rows = [{"amount": v} for v in (3_500_000, 4_200_000, 1_234_000_000)]
        scale = self._resolved_scale(rows)  # REPEAT mode, exponent 6
        _, number_str, _ = format_kpi_parts(
            1_234_000_000, ".3~s", shared_scale=scale, is_anchor=False
        )
        assert number_str == "1,234"
        assert "k" not in number_str.lower() and "m" not in number_str.lower()

    def test_no_shared_scale_matches_todays_pinned_output(self) -> None:
        # Pinned against an independent expectation (not a second call with
        # the same defaults) -- today's per-row SI split for 3,500,000 at
        # ".3~s" is "3.5" + "M".
        prefix, number_str, suffix = format_kpi_parts(
            3_500_000, ".3~s", default_number=True
        )
        assert (prefix, number_str, suffix) == ("", "3.5", "M")
        # shared_scale=None (the default) is byte-identical to omitting it.
        assert format_kpi_parts(3_500_000, ".3~s", default_number=True) == (
            format_kpi_parts(
                3_500_000,
                ".3~s",
                default_number=True,
                shared_scale=None,
                is_anchor=False,
            )
        )


class TestSharedScaleAnchorsRender:
    """Full render-path regression: table.py's row-paint loop strips the
    magnitude suffix on non-anchor rows for an ANCHOR-mode shared_scale
    column under `symbol_mode: anchors`, and keeps it on every row for a
    REPEAT-mode one -- the acceptance criterion for wiring shared_scale into
    the existing anchors-mode exclusion.
    """

    def test_anchor_mode_shared_scale_strips_suffix_on_middle_rows(
        self, make_chart
    ) -> None:
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.table import render_table_svg

        from .test_table_variants import _BOARD_STYLE, _style_with

        chart = make_chart("table", x=None, y=None)
        # Three million-range rows sharing tier M (ANCHOR mode: extreme
        # 12,000,000 / 1e6 = 12.0, only 2 integer digits).
        data = [{"revenue": 3_500_000}, {"revenue": 4_200_000}, {"revenue": 12_000_000}]
        custom_ctx = _style_with(symbol_mode="anchors")
        chart = resolve(chart, data, chart_style_context=custom_ctx)
        svg = render_table_svg(chart, data, width=400, board_style=_BOARD_STYLE)

        # Exactly one row (the anchor) carries the magnitude suffix; the
        # other two show bare, digit-aligned numbers at the same scale.
        assert svg.count(">M</tspan>") == 1, (
            "Only the anchor row should keep its magnitude suffix under "
            f"anchors + a resolved ANCHOR-mode shared_scale; got: {svg}"
        )
        # The numbers themselves must be the correctly-scaled digits, not
        # truncated by the precision-inversion bug (3.5 would render "4").
        # Substring, not an exact tspan boundary: decimal_pad_table appends
        # invisible figure-space padding after the visible digits so mixed
        # decimal depths still align.
        assert "3.5" in svg
        assert "4.2" in svg
        assert ">12" in svg

    def test_repeat_mode_shared_scale_keeps_suffix_on_every_row(
        self, make_chart
    ) -> None:
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.table import render_table_svg

        from .test_table_variants import _BOARD_STYLE, _style_with

        chart = make_chart("table", x=None, y=None)
        # Extreme scaled value >= 4 integer digits at its own tier -> REPEAT.
        data = [
            {"revenue": 3_500_000},
            {"revenue": 4_200_000},
            {"revenue": 1_234_000_000},
        ]
        custom_ctx = _style_with(symbol_mode="anchors")
        chart = resolve(chart, data, chart_style_context=custom_ctx)
        svg = render_table_svg(chart, data, width=400, board_style=_BOARD_STYLE)

        # REPEAT mode speaks the narrative register ("mn"), not analytic
        # ("M") -- SharedScale.register follows mode: a suffix stated on
        # every row is value-attached and reads narrative.
        assert svg.count(">mn</tspan>") == 3, (
            "Every non-zero row should keep its magnitude suffix under a "
            f"REPEAT-mode shared_scale; got: {svg}"
        )

    def test_symbol_mode_all_keeps_suffix_on_every_row_even_in_anchor_mode(
        self, make_chart
    ) -> None:
        """`symbol_mode: all` predates shared_scale and means "show the full
        formatted value on every row" -- an ANCHOR-mode shared_scale must not
        suppress the magnitude suffix under `all` the way it does under
        `anchors`, or `all` would silently stop meaning "every row" for a
        column that happens to share a magnitude.
        """
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.table import render_table_svg

        from .test_table_variants import _BOARD_STYLE, _style_with

        chart = make_chart("table", x=None, y=None)
        data = [{"revenue": 3_500_000}, {"revenue": 4_200_000}, {"revenue": 12_000_000}]
        custom_ctx = _style_with(symbol_mode="all")
        chart = resolve(chart, data, chart_style_context=custom_ctx)
        svg = render_table_svg(chart, data, width=400, board_style=_BOARD_STYLE)

        assert svg.count(">M</tspan>") == 3, (
            "Every row should keep its magnitude suffix under symbol_mode: "
            f"all, even for an ANCHOR-mode shared_scale; got: {svg}"
        )

    def test_zero_first_row_does_not_swallow_the_column_magnitude(
        self, make_chart
    ) -> None:
        """A zero (or non-numeric) first row structurally cannot carry a
        magnitude suffix (format_kpi_parts never puts one on a zero value).
        Anchoring on row 0 unconditionally would leave an ANCHOR-mode
        column's magnitude declared nowhere -- the anchor must fall to the
        first row that can actually carry it.
        """
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.table import render_table_svg

        from .test_table_variants import _BOARD_STYLE, _style_with

        chart = make_chart("table", x=None, y=None)
        data = [
            {"revenue": 0},
            {"revenue": 3_500_000},
            {"revenue": 4_200_000},
            {"revenue": 12_000_000},
        ]
        custom_ctx = _style_with(symbol_mode="anchors")
        chart = resolve(chart, data, chart_style_context=custom_ctx)
        svg = render_table_svg(chart, data, width=400, board_style=_BOARD_STYLE)

        assert svg.count(">M</tspan>") == 1, (
            "The magnitude suffix must land on the first row that can "
            f"actually carry it, even when row 0 is zero; got: {svg}"
        )


class TestSubTierPrecisionAndRefusal:
    """Round-2 review regressions: precision derivation and the zero-refusal
    guard must hold for values genuinely below the column's shared tier, not
    just for the "renders as literal 0" case round 1 caught.
    """

    def _resolve(self, rows: list[dict[str, Any]], fmt: str = "number"):
        columns = {"amount": TableColumnConfig(format=fmt)}
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        return resolved["amount"].shared_scale

    def test_distinct_sub_tier_values_never_collapse_to_the_same_string(self) -> None:
        # Two rows both below the M tier but at different magnitudes must
        # not paint identically -- _integer_digit_count floors at 1 for any
        # value < 1, so a naive precision derivation gives both rows the
        # same (too-shallow) decimal depth regardless of how far below the
        # tier they sit.
        rows = [
            {"amount": v}
            for v in (10_000, 14_900, 10_001, 2_000_000, 3_000_000, 4_000_000)
        ]
        scale = self._resolve(rows)
        assert scale is not None
        magnitude = 10.0**scale.exponent
        formatted_10000 = _d3_format(scale.digit_spec)(10_000 / magnitude)
        formatted_14900 = _d3_format(scale.digit_spec)(14_900 / magnitude)
        assert formatted_10000 != formatted_14900, (
            f"10,000 and 14,900 must render distinguishably, both got "
            f"{formatted_10000!r}"
        )

    def test_extremely_low_sig_figs_spec_refuses_when_precision_cannot_help(
        self,
    ) -> None:
        # An author-authored 0-significant-figure spec (`.0~s`) leaves no
        # decimal precision to lean on: the finest value's own decimal
        # exponent can still land the computed precision at exactly the
        # rounding boundary, where a sub-tier row renders with no nonzero
        # digit at all -- distinguishable from every other refusal case
        # covered above, which precision alone resolves.
        scale = self._resolve(
            [{"amount": v} for v in (300_000, 3_500_000, 4_200_000)], fmt=".0~s"
        )
        assert scale is None

    def test_far_outlier_refuses_rather_than_inflate_precision_for_everyone(
        self,
    ) -> None:
        # Round-3 regression: an unbounded precision derivation let one
        # far-below-tier straggler force every OTHER row in the column to
        # inherit far more decimal places than the format's own
        # significant-figure count ever asked for (three sig figs
        # requested, seven delivered). Refusing the whole bake when the
        # finest value sits more than one tier below the majority protects
        # the normal rows' own precision contract.
        scale = self._resolve([{"amount": v} for v in (500, 3_456_789, 4_234_567)])
        assert scale is None

    def test_far_outlier_refuses_even_when_it_is_not_the_smallest_by_much(
        self,
    ) -> None:
        scale = self._resolve(
            [{"amount": v} for v in (5_000_000, 6_000_000, 7_000_000, 0.001)]
        )
        assert scale is None

    def test_one_tier_below_still_gets_a_bounded_precision(self) -> None:
        # The boundary this task must not over-correct past: a value ONE
        # tier below the majority (thousands inside a millions column)
        # legitimately needs a few extra decimals and must still resolve,
        # not refuse -- distinguishing it from the far-outlier cases above.
        scale = self._resolve([{"amount": v} for v in (10_000, 3_500_000, 4_200_000)])
        assert scale is not None
        assert _d3_parse(scale.digit_spec).precision <= 5

    def test_signed_low_sig_figs_spec_still_refuses(self) -> None:
        # A `+`-signed digit_spec formats a rounded-to-zero value as "+0",
        # not "0" -- the refusal check must key on digit content, not a
        # fixed glyph set, or a signed spec silently bypasses the guard.
        scale = self._resolve(
            [{"amount": v} for v in (300_000, 3_500_000, 4_200_000)], fmt="+.0~s"
        )
        assert scale is None


class TestTierDistance:
    """tier_distance: the pure predicate the sub-tier refusal gate is built on."""

    def test_same_tier_is_zero(self) -> None:
        assert tier_distance(3_500_000, 6) == 0

    def test_one_tier_below_is_one(self) -> None:
        assert tier_distance(10_000, 6) == 1

    def test_three_tiers_below_is_three(self) -> None:
        # k(3) -> M(6) -> B(9) -> T(12): three steps apart.
        assert tier_distance(10_000, 12) == 3

    def test_below_every_tier_is_none(self) -> None:
        assert tier_distance(500, 6) is None

    def test_zero_has_no_tier(self) -> None:
        assert tier_distance(0, 6) is None


class TestPadTableCappedAtObservedDepth:
    """A shared-scale column with no explicit format precision (a bare `~s`)
    falls back to 6 significant figures -- correct for the format's own
    contract, but a column whose finest value has few integer digits can
    then bake a digit_spec several decimals deep that no row's *trimmed*
    output ever actually reaches. decimal_pad_table must be sized to what
    the column really needs, not the theoretical worst case.
    """

    def test_pad_table_does_not_exceed_the_columns_real_fractional_depth(self) -> None:
        columns = {"amount": TableColumnConfig(format="~s")}
        rows = [{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000)]
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        col = resolved["amount"]
        assert col.shared_scale is not None
        magnitude = 10.0**col.shared_scale.exponent
        actual_max_frac = max(
            len(
                _d3_format(col.shared_scale.digit_spec)(v / magnitude).partition(".")[2]
            )
            for v in (3_500_000, 4_200_000, 12_000_000)
        )
        # pad_table length is precision + 2; a table sized to the column's
        # own uncapped digit_spec precision (6 sig figs - 1 int digit = 5)
        # would be far longer than the real observed depth requires.
        assert len(col.decimal_pad_table) <= actual_max_frac + 2

    def test_cap_never_applies_to_a_non_shared_scale_column(self) -> None:
        # The cap is scoped to shared_scale columns only. A plain fixed-point
        # column's pad table stays at its declared precision -- the bake has no
        # basis for treating the observed depths as a bound the way the
        # shared-scale path does. Pin the observable contract
        # instead of internals: a plain ",.5~f" column's pad table stays at
        # its full declared precision (7 entries) even though every row's
        # own trimmed depth is far shallower.
        columns = {"amount": TableColumnConfig(format=",.5~f")}
        rows = [{"amount": v} for v in (1.5, 2.0, 3.25)]
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        col = resolved["amount"]
        assert col.shared_scale is None
        assert len(col.decimal_pad_table) == 7, (
            "a non-shared-scale column's pad table must stay at its full "
            f"declared precision (5 + 2 = 7 entries); got {col.decimal_pad_table!r}"
        )


class TestSizingFunctionsRegression:
    """Round-1 CRITICAL #3 (numeric-string cells) and the is_anchor=True
    sizing rationale, pinned directly against the sizing functions rather
    than only through a full render (a mutation to either was previously
    invisible to every test in this file).
    """

    def _resolved_column(self, rows: list[dict[str, Any]]):
        columns = {"amount": TableColumnConfig(format="number")}
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        return resolved["amount"]

    def test_measure_column_demands_handles_numeric_string_cells(self) -> None:
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table_support import (
            measure_column_demands,
        )

        values = (3_456_789, 8_765_432, 87_654_321)
        rows = [{"amount": v} for v in values]
        col = self._resolved_column(rows)
        assert col.shared_scale is not None
        measurer = get_font_measurer(DBT_SANS_TABULAR_FONT_FAMILY, numeric=True)

        def _demand(data: list[dict[str, Any]]) -> float:
            cell_demands, _ = measure_column_demands(
                columns=["amount"],
                column_configs={"amount": col},
                data=data,
                measurer=measurer,
                font_size=11,
                header_font_size=11,
                cell_pad=8,
                column_when_rules={},
            )
            return cell_demands["amount"]

        # CSV/warehouse adapters can deliver numbers as strings -- must
        # measure the same real (scaled) content int cells do, not silently
        # fall through to the unscaled per-row SI text (round-1 CRITICAL #3:
        # that fallback also caused an IndexError from a pad-depth mismatch
        # against the scaled digit_spec's pad table). Mutation-sensitive:
        # reverting the string coercion sends string cells through
        # format_table_cell_value's independent "3.46 M" / "8.77 M" /
        # "87.7 M" path instead of the shared digit_spec's "3.46" / "8.77" /
        # "87.65" -- a measurably different (narrower) demand.
        int_demand = _demand(rows)
        string_demand = _demand([{"amount": str(v)} for v in values])
        assert string_demand == int_demand, (
            f"string cells must measure identically to int cells under a "
            f"shared_scale column; got {string_demand} vs {int_demand}"
        )

    def test_compute_lane_positions_reserves_room_for_the_anchor_suffix(self) -> None:
        # Mutation-sensitive: if is_anchor=True were dropped (or wrongly
        # False) when measuring an ANCHOR-mode shared_scale column, the
        # suffix lane would size to an empty string and content_right would
        # collapse onto the number lane -- no width reserved for "M" at all.
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.render.chart.table import _compute_lane_positions

        rows = [{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000)]
        col = self._resolved_column(rows)
        assert col.shared_scale is not None
        positions = _compute_lane_positions(
            rows=rows,
            columns=["amount"],
            column_configs={"amount": col},
            col_widths={"amount": 300.0},
            col_x_offsets=[0.0],
            padding_x=16,
            cell_pad=8,
            cell_font=FontStyle(family=DBT_SANS_TABULAR_FONT_FAMILY, size=12.0),
            column_when_rules={},
        )
        _, number_x, suffix_x, _, content_right = positions["amount"]
        assert suffix_x > number_x, (
            "suffix lane must sit to the right of the number lane"
        )
        assert content_right > number_x, (
            "content_right must reserve room for the anchor row's real "
            "suffix, not collapse onto the number lane"
        )


class TestAllowSharedScaleGate:
    """``allow_shared_scale=False`` disables the shared-magnitude bake.

    A pie/donut chart's attached legend "table" shares this column-resolution
    machinery with real authored tables, but a legend entry is read beside
    its own slice, not scanned down a column -- ANCHOR mode declaring the
    magnitude once on the top entry and stripping it from the rest would
    read as data loss there, not alignment. pie.py passes
    ``allow_shared_scale=False`` at its attached-table call site.
    """

    def _resolve(
        self, rows: list[dict[str, Any]], allow_shared_scale: bool
    ) -> ResolvedColumnSharedScale | None:
        columns = {"amount": TableColumnConfig(format="number")}
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
            allow_shared_scale=allow_shared_scale,
        )
        assert resolved is not None
        return resolved["amount"].shared_scale

    def test_false_disables_the_shared_scale_bake(self) -> None:
        rows = [{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000)]
        assert self._resolve(rows, allow_shared_scale=False) is None

    def test_true_keeps_the_default_authored_table_behavior(self) -> None:
        rows = [{"amount": v} for v in (3_500_000, 4_200_000, 12_000_000)]
        scale = self._resolve(rows, allow_shared_scale=True)
        assert scale is not None
        assert scale.exponent == 6


class TestDecimalPadFallbackWhenNoSharedScaleTier:
    """A column whose values all sit below the smallest SI tier (no majority
    magnitude to anchor on -- shared_scale is None) still needs a
    decimal_pad_table: decimal_pad_table_for refuses because the effective
    format is an SI spec (type "s"), not a fixed-point trim spec (type "f"),
    so without a fallback nothing gets baked and whole numbers in the column
    fail to align on the decimal point with fractional ones.
    """

    def test_mixed_whole_and_fractional_below_every_tier_bakes_a_pad_table(
        self,
    ) -> None:
        columns = {"amount": TableColumnConfig(format="number")}
        rows = [{"amount": v} for v in (84.3, 88, 128.4, 42)]
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        col = resolved["amount"]
        # Confirms the gap this fallback targets: no shared SI tier at all.
        assert col.shared_scale is None
        assert col.decimal_pad_table != ()

        whole_pad = decimal_pad_for(col.decimal_pad_table, "88")
        assert whole_pad != ""
        # Measured placeholder unit, never a visible zero -- repo rule: no
        # padded zeros on data values.
        assert "0" not in whole_pad

        deepest_pad = decimal_pad_for(col.decimal_pad_table, "84.3")
        assert deepest_pad == ""

    def test_sub_dollar_currency_cell_bakes_deep_enough_for_its_own_render(
        self,
    ) -> None:
        # Regression: table.py's format_kpi_parts prints a sub-$1 currency
        # cell through a different, deeper spec than the rest of the column
        # (si_sub_unit_floor's per-row swap). A bake that measures every row
        # with the bare SI spec is too shallow for that swapped row.
        columns = {"amount": TableColumnConfig(format="currency")}
        values = (0.25, 84.3, 88, 42)
        rows = [{"amount": v} for v in values]
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        col = resolved["amount"]
        assert col.shared_scale is None
        # 0.25 -> "0.25" (2 fractional digits, the swapped fallback spec);
        # 84.3 -> "84.3" (1); 88/42 -> 0. Precision must be the deepest: 2.
        assert len(col.decimal_pad_table) - 2 == 2

        for v in values:
            _, number_str, _ = format_kpi_parts(v, "currency", default_number=True)
            decimal_pad_for(col.decimal_pad_table, number_str)  # must not raise
        assert decimal_pad_for(col.decimal_pad_table, "0.25") == ""
        assert decimal_pad_for(col.decimal_pad_table, "84.3") != ""
        assert decimal_pad_for(col.decimal_pad_table, "88") != ""

    def test_allow_shared_scale_false_still_pads_a_homogeneous_real_tier(
        self,
    ) -> None:
        """``allow_shared_scale=False`` (pie/donut's attached legend table)
        never attempts the shared-scale bake at all, so ``shared_scale`` is
        None for EVERY column of that caller -- including a homogeneous
        single-real-tier one (every value in thousands, e.g. 15000/5000/
        3500). That column still has one shared decimal position to align
        on and must still pad -- only a genuine tier mix should refuse
        (``column_shares_one_printed_unit``).
        """
        columns = {"amount": TableColumnConfig(format="number")}
        rows = [{"amount": v} for v in (15_000, 5_000, 3_500)]
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
            allow_shared_scale=False,
        )
        assert resolved is not None
        col = resolved["amount"]
        assert col.shared_scale is None
        assert col.decimal_pad_table != ()
        assert decimal_pad_for(col.decimal_pad_table, "15") != ""
        assert decimal_pad_for(col.decimal_pad_table, "3.5") == ""

    def test_genuine_tier_mix_bakes_no_pad_at_all(self) -> None:
        """A real mix -- some values print with a "k" suffix, some bare -- is
        not the same "everyone's too small" case the fallback above targets.
        shared_scale_for_column refuses this column too (a real tier can tie
        with "no tier"), but a bare "900" and a "12.1 k" (=12,100) have no
        shared decimal position to align on, so no pad table should be baked
        at all.
        """
        columns = {"amount": TableColumnConfig(format="number")}
        rows = [{"amount": v} for v in (12_100, 900)]
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        col = resolved["amount"]
        assert col.shared_scale is None
        assert col.decimal_pad_table == ()

    def test_sub_unit_value_mixed_with_a_bare_value_bakes_no_pad(self) -> None:
        """0.671 prints "671m" (a milli-prefixed SI suffix); 84.3 prints
        bare. Regression: a raw-value tier classifier (``_natural_tier``)
        only recognizes tiers at or above thousands, so it reads 0.671 as
        having no tier at all -- the same bucket as 84.3 -- and would
        wrongly treat this as the all-below-tier case above. The two cells
        print in different units and must not be padded together.
        """
        columns = {"amount": TableColumnConfig(format="number")}
        rows = [{"amount": v} for v in (0.671, 84.3)]
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        col = resolved["amount"]
        assert col.shared_scale is None
        assert col.decimal_pad_table == ()

    def test_currency_spec_via_format_config_measures_the_sub_dollar_swap(
        self,
    ) -> None:
        """``raw_format_name`` is derived from ``col.format.spec`` when the
        column authors a ``FormatConfig`` (not a bare string) -- must not
        skip the sub-$1 swap that measurement depends on. 0.25 falls in the
        sub-$1 band and is measured through the deeper two-decimal fallback
        spec; 84.3/88 are measured through the bare SI spec. Must not raise.
        """
        from dbt_charts.core.compile.models.primitives import FormatConfig

        columns = {"amount": TableColumnConfig(format=FormatConfig(spec="currency"))}
        rows = [{"amount": v} for v in (0.25, 84.3, 88)]
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        col = resolved["amount"]
        assert col.shared_scale is None
        # pad_table length is precision + 2 (see build_decimal_pad_table);
        # 0.25 -> "0.25" (2 fractional digits, the swapped fallback spec) is
        # the deepest cell, so precision is 2.
        assert len(col.decimal_pad_table) - 2 == 2
        assert decimal_pad_for(col.decimal_pad_table, "0.25") == ""
        assert decimal_pad_for(col.decimal_pad_table, "84.3") != ""
        assert decimal_pad_for(col.decimal_pad_table, "88") != ""

    def test_explicit_suffix_money_code_measures_the_sub_dollar_swap(self) -> None:
        """``explicit_suffix`` (from ``get_format_prefix_suffix``) feeds
        ``si_sub_unit_floor``'s money detection even when the ``spec`` itself
        carries no currency symbol -- a `` USD`` suffix must still route 0.25
        through the deeper two-decimal fallback spec, not the bare SI one.
        Must not raise.
        """
        from dbt_charts.core.compile.models.primitives import FormatConfig

        columns = {
            "amount": TableColumnConfig(
                format=FormatConfig(spec="number", suffix=" USD")
            )
        }
        rows = [{"amount": v} for v in (0.25, 84.3, 88)]
        resolved = _with_resolved_scale_stops(
            columns,
            text_color=None,
            formats=None,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        col = resolved["amount"]
        assert col.shared_scale is None
        # pad_table length is precision + 2 (see build_decimal_pad_table);
        # 0.25 -> "0.25" (2 fractional digits, the swapped fallback spec) is
        # the deepest cell, so precision is 2.
        assert len(col.decimal_pad_table) - 2 == 2
        assert decimal_pad_for(col.decimal_pad_table, "0.25") == ""
        assert decimal_pad_for(col.decimal_pad_table, "84.3") != ""
        assert decimal_pad_for(col.decimal_pad_table, "88") != ""


def test_column_alias_to_a_preset_keeps_its_sub_unit_pad() -> None:
    """An alias naming a preset pads sub-$1 rows like the preset itself."""
    rows: list[dict[str, Any]] = [{"amount": v} for v in (0.67, 1.5, 2.25)]

    def pad(fmt: str, formats: dict[str, str] | None) -> tuple[str, ...]:
        resolved = _with_resolved_scale_stops(
            {"amount": TableColumnConfig(format=fmt)},
            text_color=None,
            formats=formats,
            font_family=DBT_SANS_TABULAR_FONT_FAMILY,
            rows=rows,
        )
        assert resolved is not None
        return resolved["amount"].decimal_pad_table

    direct = pad("currency", None)
    assert direct
    assert pad("mine", {"mine": "currency"}) == direct
