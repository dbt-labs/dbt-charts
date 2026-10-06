"""Unit tests for the top-legend fit predicate and its entry list.

``legend_row_fits`` is the single source of truth for "does this legend fit
one row" -- both the compile-time top/right trigger
(``cartesian_series_naming``) and the render-time entry-omission fix (a
separate, already-filed defect) must call it, so it is tested in isolation
here, independent of any one chart family.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import (
    get_chart_rendering,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.authored import LineLayer
from dbt_charts.core.compile.models.style.resolved import ResolvedLegendStyle
from dbt_charts.core.compile.resolve.chart._axes import (
    cartesian_top_legend_entries,
    estimate_left_axis_reserve_px,
    legend_row_fits,
    legend_wrap_fits_height_budget,
    legend_wrap_required_height_px,
    legend_wrapped_row_count,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_config()
    yield
    reset_config()


def _legend() -> ResolvedLegendStyle:
    return resolve_chart_style_context(get_theme_style()).legend


class TestLegendRowFits:
    def test_no_entries_trivially_fits(self) -> None:
        assert legend_row_fits((), 1.0, _legend()) is True

    def test_short_entries_fit_a_normal_card(self) -> None:
        assert legend_row_fits(("Alpha", "Beta"), 400.0, _legend()) is True

    def test_long_entries_overflow_a_narrow_card(self) -> None:
        entries = (
            "Quarterly Recurring Revenue",
            "Annual Contract Value Growth Rate By Region And Product Line",
        )
        assert legend_row_fits(entries, 450.0, _legend()) is False

    def test_same_entries_fit_once_the_card_is_wide_enough(self) -> None:
        """No fixed entry count: the same entries flip from overflow to fit
        purely as a function of width -- proves the predicate measures, it
        does not count."""
        entries = (
            "Quarterly Recurring Revenue",
            "Annual Contract Value Growth Rate By Region And Product Line",
        )
        assert legend_row_fits(entries, 450.0, _legend()) is False
        assert legend_row_fits(entries, 800.0, _legend()) is True

    def test_longer_label_text_narrows_the_capacity(self) -> None:
        """Label length moves the threshold, not just entry count: doubling
        one entry's text can flip the same width from fit to overflow."""
        short = ("Revenue", "Target")
        long = ("Revenue", "Target " * 20)
        width = 300.0
        assert legend_row_fits(short, width, _legend()) is True
        assert legend_row_fits(long, width, _legend()) is False


class TestLegendWrappedRowCount:
    """Row-count arithmetic shared between the fallback ladder's rung-2 wrap
    check (``legend_wrap_fits_height_budget``) and bar.py's own
    ``_stack_legend_should_yield`` -- the two ask different questions of the
    same row count, so the count itself is tested once here."""

    def test_divides_entries_across_columns(self) -> None:
        assert legend_wrapped_row_count(8, None, 2) == 4
        assert legend_wrapped_row_count(9, None, 2) == 5

    def test_caps_at_symbol_limit_before_dividing(self) -> None:
        """A chart with far more series than symbol_limit still shows only
        symbol_limit entries (plus Vega-Lite's own synthetic "...N entries"
        row) -- the row count must reflect what actually renders, not the
        raw column cardinality."""
        assert legend_wrapped_row_count(100, 6, 2) == legend_wrapped_row_count(6, 6, 2)

    def test_entry_count_exactly_at_symbol_limit_matches_one_more(self) -> None:
        """One more entry than symbol_limit must not add a row: both are
        capped to the same rendered count."""
        assert legend_wrapped_row_count(20, 20, 2) == legend_wrapped_row_count(
            21, 20, 2
        )

    def test_no_symbol_limit_uses_the_raw_count(self) -> None:
        assert legend_wrapped_row_count(9, None, 2) == 5


class TestLegendWrapRequiredHeightPx:
    """Pixel height a wrapped, multi-column legend needs at collapse -- the
    legend's *total* footprint, fixed chrome included. bar.py's own
    ``_stack_legend_should_yield`` is the one caller: it compares this
    directly against an absolute plot-height requirement, nothing else
    subtracted alongside it. The fallback ladder's rung-2 check
    (``legend_wrap_fits_height_budget``) and the plot-height floor's own
    compact-legend charge want ``legend_wrap_marginal_height_px`` instead --
    a flat per-row cost with no fixed chrome of its own, exercised below via
    ``TestLegendWrapFitsHeightBudget``."""

    def test_scales_with_row_count(self) -> None:
        cfg = get_chart_rendering().legend
        one_row = legend_wrap_required_height_px(2, None, 2)
        two_rows = legend_wrap_required_height_px(4, None, 2)
        assert one_row == cfg.row_height_px + cfg.chrome_height_px
        assert two_rows == 2 * cfg.row_height_px + cfg.chrome_height_px


def _plot_height_at_old_share_boundary(required: float, share: float = 0.30) -> float:
    """The plot height at which ``required`` is exactly ``share`` of the old
    BODY definition (``required + plot_height``) -- solving
    ``share = required / (required + plot_height)`` for ``plot_height``.

    ``share`` defaults to 0.30, the retired ``max_wrap_height_share``
    constant -- kept here as a literal, not read from config, purely to
    reconstruct the superseded formula for the disagreement test below. It
    no longer exists as a config field.
    """
    return required * (1.0 - share) / share


def _budget(
    entries: tuple[str, ...], legend: ResolvedLegendStyle, plot_height: float
) -> bool:
    """Call the floor-based check with an otherwise-typical chart's floor
    inputs -- no title/subtitle chrome, a representative card padding."""
    return legend_wrap_fits_height_budget(
        entries,
        legend,
        plot_height,
        card_padding_px=16.0,
        subtitle_present=False,
        axis_title_costs_height=False,
    )


class TestLegendWrapFitsHeightBudget:
    """Rung 2 of the fallback ladder: a wrapped legend stays top only while
    putting it there still leaves the plot clearing
    ``chart_rendering.plot_height_floor.ratio`` of the card's own height --
    the same floor ``bar.py``'s own starved-plot warning checks, not a
    second, independently-calibrated share (RJ, 2026-08-27's rejection of a
    fixed row cap, then again of the ``max_wrap_height_share`` share-of-body
    check this replaced once the floor's own estimate landed on main)."""

    def test_tall_plot_admits_the_wrapped_legend(self) -> None:
        legend = _legend()
        entries = ("S1", "S2")
        assert _budget(entries, legend, 600.0) is True

    def test_short_plot_falls_back(self) -> None:
        legend = _legend()
        entries = ("S1", "S2")
        assert _budget(entries, legend, 10.0) is False

    def test_same_entries_flip_rungs_as_plot_height_changes(self) -> None:
        """The whole point of the change: no row count is fixed here, only
        whether whatever plot height this chart actually has clears the
        floor once the wrapped legend sits on top of it."""
        legend = _legend()
        entries = tuple(f"Territory Region {i:02d}" for i in range(8))
        assert _budget(entries, legend, 600.0) is True
        assert _budget(entries, legend, 10.0) is False

    def test_disagrees_with_the_retired_share_check_at_its_own_boundary(self) -> None:
        """Proves the floor-based rewire is not a same-outcome rename: a
        plot height picked just *below* the retired ``max_wrap_height_share``
        (0.30 of legend+plot BODY) boundary -- a height the old mechanism
        would still have rejected, one px short of admitting the legend --
        the floor-based check already admits.

        The floor's own marginal legend charge
        (``legend_wrap_marginal_height_px``, flat per row, no fixed chrome
        of its own -- correct because ``estimate_plot_height`` already
        subtracts fixed chrome separately) is smaller than the old share
        check's ``legend_wrap_required_height_px`` total (fixed chrome
        folded into every row) at this low an entry count -- enough smaller
        that it outweighs the fixed ``plot_height_floor.irreducible_height_px``
        the old share check never counted at all, so the floor-based
        boundary sits well below the retired one rather than reproducing it.
        This is the exact collision two mechanisms reasoning about the same
        "how much can a legend take before the plot suffers" question,
        independently calibrated, are able to fall into: disagreeing on the
        same chart.
        """
        legend = _legend()
        entries = ("S1", "S2")
        required = legend_wrap_required_height_px(
            len(entries), legend.symbol_limit, legend.compact_columns
        )
        old_boundary_plot_height = _plot_height_at_old_share_boundary(required) - 1.0
        assert _budget(entries, legend, old_boundary_plot_height) is True


class TestEstimateLeftAxisReservePx:
    """The compile-time estimate of how much horizontal space a real
    left-side axis costs a top legend's single row before it even starts
    (see the function's own docstring for the Vega-Lite autosize:fit
    mechanics this exists to model)."""

    def test_no_dimension_values_uses_the_fixed_floor(self) -> None:
        legend = _legend()
        cfg = get_chart_rendering().legend
        assert estimate_left_axis_reserve_px(None, legend) == cfg.min_reserve_px
        assert estimate_left_axis_reserve_px((), legend) == cfg.min_reserve_px

    def test_dimension_values_add_measured_chrome_on_top_of_the_floor(self) -> None:
        legend = _legend()
        cfg = get_chart_rendering().legend
        reserve = estimate_left_axis_reserve_px(("Jan", "Feb", "Mar"), legend)
        assert reserve > cfg.min_reserve_px

    def test_longer_dimension_labels_widen_the_reserve(self) -> None:
        """Measures real text, same as legend_row_fits -- a longer label
        must reserve more, not a fixed per-value count."""
        legend = _legend()
        short = estimate_left_axis_reserve_px(("Jan", "Feb"), legend)
        long = estimate_left_axis_reserve_px(
            ("Northeast Territory Region", "Southwest Territory Region"), legend
        )
        assert long > short


class TestLegendRowFitsAxisReserveRegression:
    """Regression for a real clip: a horizontal bar's single-row top legend
    measured as fitting the card's full width, then hard-clipped at render
    because Vega-Lite anchors a top legend to the plot's own local origin --
    shifted right by exactly this chart's left-side axis (its dimension
    field, drawn as Vega-Lite's y-channel on a horizontal bar). Pinned at
    the exact boundary a real render exhibited: legend_row_fits must still
    say "fits" against the raw card width (proving the bug's precondition
    holds), but the fit rule's actual, axis-reserve-adjusted width must
    correctly reject it."""

    def test_row_declared_fitting_at_raw_width_overflows_the_true_available_width(
        self,
    ) -> None:
        legend = _legend()
        card_width = 672.0
        entries = (
            "North Central Region",
            "Northeast Territory Region",
            "Pacific Northwest Region",
            "Southwest Territory Region",
        )
        dimension_values = (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        )
        assert legend_row_fits(entries, card_width, legend) is True, (
            "precondition: this row must measure as fitting the raw card "
            "width, or this test no longer reproduces the bug"
        )
        reserve = estimate_left_axis_reserve_px(dimension_values, legend)
        assert legend_row_fits(entries, card_width - reserve, legend) is False, (
            "the row-fit check must reject a row that only fits the card's "
            "raw width once the real left-side axis reserve is subtracted -- "
            "this is the exact case that hard-clipped at render"
        )


class TestCartesianTopLegendEntries:
    def test_no_layers_and_no_color_domain_is_empty(self) -> None:
        assert (
            cartesian_top_legend_entries(
                "revenue", None, [], (), has_color=False, field_labels={}
            )
            == ()
        )

    def test_layers_present_names_base_and_each_layer(self) -> None:
        entries = cartesian_top_legend_entries(
            "revenue",
            "Revenue",
            [LineLayer(type="line", y="target", label="Target")],
            (),
            has_color=False,
            field_labels={},
        )
        assert entries == ("Revenue", "Target")

    def test_layer_without_its_own_label_falls_back_to_its_field_title(
        self,
    ) -> None:
        entries = cartesian_top_legend_entries(
            "revenue",
            "Revenue",
            [LineLayer(type="line", y="order_target", label=None)],
            (),
            has_color=False,
            field_labels={},
        )
        assert entries == ("Revenue", "order target")

    def test_color_domain_values_are_appended(self) -> None:
        entries = cartesian_top_legend_entries(
            "revenue",
            "Revenue",
            [LineLayer(type="line", y="target", label="Target")],
            ("S1", "S2"),
            has_color=False,
            field_labels={},
        )
        assert entries == ("Revenue", "Target", "S1", "S2")

    def test_base_name_dropped_when_base_carries_a_color_field(self) -> None:
        """Render's own field_color_base fork (_overlay.py) never emits the
        base's y-title as a separate legend entry once the base has its own
        color field -- its color-domain values stand in for it instead.
        Charging the fit predicate for both would over-measure by one entry
        render never draws."""
        entries = cartesian_top_legend_entries(
            "revenue",
            "Revenue",
            [LineLayer(type="line", y="target", label="Target")],
            ("S1", "S2"),
            has_color=True,
            field_labels={},
        )
        assert entries == ("Target", "S1", "S2")

    def test_y_less_layer_is_skipped_not_faked_with_the_base_field(self) -> None:
        """A layer with no y is skipped entirely at render
        (_overlay.py: `if y_field is None: continue`) -- it must not be
        measured as though it inherited the base's y."""
        entries = cartesian_top_legend_entries(
            "revenue",
            "Revenue",
            [LineLayer(type="line", y=None, label="Ghost")],
            (),
            has_color=False,
            field_labels={},
        )
        assert entries == ("Revenue",)
