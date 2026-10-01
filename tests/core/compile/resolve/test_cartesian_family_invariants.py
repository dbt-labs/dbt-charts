"""Family-invariant tests for the cartesian resolvers' shared policy.

Every cartesian family (bar, line, area, scatter, heatmap) hand-assembles its
own composition of the same shared helpers in
`compile/resolve/chart/_axes.py`, `_layers.py`, and `_domain.py`. A family
that skips a policy call renders wrong with no compile-time signal — nothing
polices that the composition is complete. This suite drives the real public
`resolve()` for every family and pins five invariants that must hold
identically across all of them, or be explicitly, reason-carrying skipped for
a family the invariant structurally does not apply to. A silently-missing
family is exactly the failure mode this file exists to catch.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored import (
    ChartSort,
    LayerAxisYStyle,
    LineLayer,
    MultiplesConfig,
)
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    HeatmapChart,
    LineChart,
    ScatterChart,
    SparkBarChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedChart,
    ResolvedLineChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    BarChartStylePatch,
    HeatmapChartStylePatch,
    LineChartStylePatch,
    ScatterChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.diagnostics import WARN_LEGEND_POSITION_WIDTH_FALLBACK
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.warnings import (
    WarningContext,
    legend_position_width_fallback,
)

from ..._board_utils import make_test_resolved_board

_FAMILIES = ("bar", "line", "area", "scatter", "heatmap", "histogram")

# _resolve_histogram passes multiples=None, y=None to plan_cartesian by design
# (histogram bins x and counts rows -- there is no per-row y series) and never
# calls cartesian_series_naming(): no color-series legend, no multiples-facet
# top legend, no endpoint-label rail. Every invariant below is downstream of
# either that call or a per-family guard histogram never runs; each skip below
# names the specific reason, not this shared root cause alone.
_HISTOGRAM_NO_SERIES_LEGEND = (
    "histogram never calls cartesian_series_naming() (plan_cartesian is called "
    "with multiples=None, y=None by design) -- there is no series legend for "
    "this invariant to check."
)

_TINY_WIDTH = 200.0


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_config()
    yield
    reset_config()


def _board() -> Any:
    return resolve_chart_style_context(get_theme_style())


def _sql() -> SqlQuery:
    return SqlQuery(sql="SELECT 1", source="t")


def _bar(**kwargs: Any) -> BarChart:
    defaults: dict[str, Any] = {
        "id": "c",
        "type": "bar",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
    }
    defaults.update(kwargs)
    return BarChart(**defaults)


def _line(**kwargs: Any) -> LineChart:
    defaults: dict[str, Any] = {
        "id": "c",
        "type": "line",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
    }
    defaults.update(kwargs)
    return LineChart(**defaults)


def _area(**kwargs: Any) -> AreaChart:
    defaults: dict[str, Any] = {
        "id": "c",
        "type": "area",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
    }
    defaults.update(kwargs)
    return AreaChart(**defaults)


def _scatter(**kwargs: Any) -> ScatterChart:
    defaults: dict[str, Any] = {
        "id": "c",
        "type": "scatter",
        "x": "month_index",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
    }
    defaults.update(kwargs)
    return ScatterChart(**defaults)


def _heatmap(**kwargs: Any) -> HeatmapChart:
    defaults: dict[str, Any] = {
        "id": "c",
        "type": "heatmap",
        "x": "month",
        "y": "category",
        "query": _sql(),
        "query_name": "q",
    }
    defaults.update(kwargs)
    return HeatmapChart(**defaults)


_BUILDERS: dict[str, Callable[..., Chart]] = {
    "bar": _bar,
    "line": _line,
    "area": _area,
    "scatter": _scatter,
    "heatmap": _heatmap,
}

_STYLE_PATCHES: dict[str, Any] = {
    "bar": BarChartStylePatch,
    "line": LineChartStylePatch,
    "area": AreaChartStylePatch,
    "scatter": ScatterChartStylePatch,
    "heatmap": HeatmapChartStylePatch,
}


def _family_params(exemptions: dict[str, str]) -> list[Any]:
    """Every family, with exempted ones wrapped as an explicit, reasoned skip."""
    return [
        pytest.param(family, marks=pytest.mark.skip(reason=exemptions[family]))
        if family in exemptions
        else family
        for family in _FAMILIES
    ]


# month/month_index: dimension, usable as either family's x. category: a second
# nominal field so heatmap's y differs from its x. series: the color channel
# (bare field name -> mode="series", never a gradient). facet: the multiples
# partition, kept distinct from every other role so no family double-purposes
# a column between its own axes and the panel split.
_SERIES_DATA: list[dict[str, Any]] = [
    {
        "month": "Jan",
        "month_index": 1,
        "category": "Alpha",
        "series": "S1",
        "facet": "P1",
        "revenue": 10.0,
    },
    {
        "month": "Feb",
        "month_index": 2,
        "category": "Alpha",
        "series": "S1",
        "facet": "P1",
        "revenue": 20.0,
    },
    {
        "month": "Jan",
        "month_index": 1,
        "category": "Beta",
        "series": "S2",
        "facet": "P2",
        "revenue": 15.0,
    },
    {
        "month": "Feb",
        "month_index": 2,
        "category": "Beta",
        "series": "S2",
        "facet": "P2",
        "revenue": 25.0,
    },
]


class TestMultiplesWithColorSeriesGetsTopLegend:
    """Invariant 1: multiples + a color series -> resolved legend is top and visible."""

    @pytest.mark.parametrize(
        "family", _family_params({"histogram": _HISTOGRAM_NO_SERIES_LEGEND})
    )
    def test_multiples_with_color_series_gets_top_legend(self, family: str) -> None:
        # stack="zero" for bar: at the theme default stack="none", bar's own
        # unconditional (non-stacked) top-legend trigger short-circuits the
        # "row" branch in _axes.py before _multiples_wants_top_legend is ever
        # evaluated, so the assertion would pass even with the multiples term
        # deleted. Stacked bar clears that short-circuit and actually
        # exercises it.
        extra_kwargs: dict[str, Any] = {"stack": "zero"} if family == "bar" else {}
        chart = _BUILDERS[family](
            color="series",
            multiples=MultiplesConfig(rows="facet"),
            **extra_kwargs,
        )
        resolved = resolve(chart, _SERIES_DATA, _board())
        assert resolved.legend.position == "top", (
            f"{family}: faceted color-series chart did not get a top legend "
            f"(position={resolved.legend.position!r})"
        )
        assert resolved.legend.visible is True, (
            f"{family}: faceted color-series chart's top legend is not visible"
        )


_LAYER_DATA: list[dict[str, Any]] = [
    {"month": "Jan", "month_index": 1, "revenue": 100.0, "target": 4000.0},
    {"month": "Feb", "month_index": 2, "revenue": 200.0, "target": 5000.0},
    {"month": "Mar", "month_index": 3, "revenue": 150.0, "target": 4500.0},
]


class TestLayersWithAuthoredYDomainRaises:
    """Invariant 2: layers + an authored axis_y.scale.domain -> _check_layers_y_domain fires."""

    @pytest.mark.parametrize(
        "family",
        _family_params(
            {
                "heatmap": (
                    "HeatmapChart carries no `layers` field at all (both its axes "
                    "are nominal, no measure axis for an overlay to split) — there "
                    "is no dual-axis shape for _check_layers_y_domain to police."
                ),
                "histogram": (
                    "_resolve_histogram never resolves `normalized.layers` -- "
                    "layers is a family decision plan_cartesian's docstring "
                    "documents histogram opting out of (multiples=None, y=None) "
                    "-- no layer ever reaches _check_layers_y_domain to raise "
                    "against."
                ),
            }
        ),
    )
    def test_layers_with_authored_y_domain_and_split_axis_raises(
        self, family: str
    ) -> None:
        style_patch_cls = _STYLE_PATCHES[family]
        chart = _BUILDERS[family](
            layers=[
                LineLayer(
                    type="line", y="target", axis_y=LayerAxisYStyle(position="right")
                )
            ],
            style=style_patch_cls.model_validate(
                {"axis_y": {"scale": {"continuous": {"domain": [0, 300]}}}}
            ),
        )
        with pytest.raises(CompilationError) as exc_info:
            resolve(chart, _LAYER_DATA, _board())
        assert exc_info.value.code is not None
        assert exc_info.value.code.code == "ERR-LAYERS-AMBIGUOUS-Y-DOMAIN", (
            f"{family}: layered chart with a right-pinned overlay and an authored "
            f"axis_y.scale.domain did not raise ERR-LAYERS-AMBIGUOUS-Y-DOMAIN"
        )


_LOG_ZERO_DATA_NOMINAL_X: list[dict[str, Any]] = [
    {"month": "Jan", "revenue": 0.0},
    {"month": "Feb", "revenue": 200.0},
]
_LOG_ZERO_DATA_NUMERIC_X: list[dict[str, Any]] = [
    {"month_index": 1, "revenue": 0.0},
    {"month_index": 2, "revenue": 200.0},
]


class TestLogMeasureAxisWithNonPositiveDataRaises:
    """Invariant 3: a log measure axis with non-positive data -> ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA."""

    @pytest.mark.parametrize(
        "family",
        _family_params(
            {
                "bar": (
                    "bar rejects axis_y.scale.type: log outright via "
                    "ERR_BAR_LOG_SCALE_NOT_SUPPORTED before the data is ever "
                    "inspected — a bar's length encodes magnitude from zero, "
                    "meaningless on a log scale."
                ),
                "heatmap": (
                    "heatmap bakes both axes as 'nominal' "
                    "(_resolve_heatmap -> _bake_cartesian_axes) — there is no "
                    "measure axis for a log scale to apply to."
                ),
                "histogram": (
                    "_resolve_histogram never calls "
                    "_reject_non_positive_log_scale_data. A histogram's y is a "
                    "bin count VL computes client-side, not an authored column "
                    "the resolver reads at compile time -- there is no y-column "
                    "extent for this guard to inspect."
                ),
            }
        ),
    )
    def test_log_measure_axis_with_non_positive_data_raises(self, family: str) -> None:
        style_patch_cls = _STYLE_PATCHES[family]
        data = (
            _LOG_ZERO_DATA_NUMERIC_X
            if family == "scatter"
            else _LOG_ZERO_DATA_NOMINAL_X
        )
        chart = _BUILDERS[family](
            style=style_patch_cls.model_validate(
                {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
            ),
        )
        with pytest.raises(CompilationError) as exc_info:
            resolve(chart, data, _board())
        assert exc_info.value.code is not None
        assert exc_info.value.code.code == "ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA", (
            f"{family}: log measure axis with a non-positive data value did not "
            f"raise ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA"
        )


def _assert_compact_top_legend(resolved: ResolvedChart, family: str) -> None:
    assert resolved.legend.position == "top", (
        f"{family}: tiny-width color-series chart did not fall back to a top "
        f"legend (position={resolved.legend.position!r})"
    )
    assert resolved.legend.columns != 0, (
        f"{family}: tiny-width top legend is not in compact-columns layout "
        f"(columns=0 means the uncompacted 'row' layout, not 'compact')"
    )
    # Only the three rail-bearing families discard the rail in favor of the
    # compact legend; scatter/heatmap have no endpoint_labels field to check —
    # "no rail" is their whole story, there is nothing to fall back from.
    if isinstance(resolved, ResolvedBarChart | ResolvedLineChart | ResolvedAreaChart):
        assert resolved.style.endpoint_labels.visible is False, (
            f"{family}: tiny-width color-series chart kept the endpoint-label "
            f"rail instead of falling back to the compact top legend"
        )


class TestTinyWidthFallsBackToCompactTopLegend:
    """Invariant 4: tiny width -> series naming falls back to the compact top legend."""

    @pytest.mark.parametrize(
        "family", _family_params({"histogram": _HISTOGRAM_NO_SERIES_LEGEND})
    )
    def test_tiny_width_falls_back_to_compact_top_legend(self, family: str) -> None:
        chart = _BUILDERS[family](color="series")
        resolved = resolve(chart, _SERIES_DATA, _board(), width=_TINY_WIDTH)
        _assert_compact_top_legend(resolved, family)


class TestAuthoredLegendPositionResolvesVisible:
    """Invariant 5: an authored ``legend.position`` resolves to a visible legend.

    Every family threads ``naming.force_legend_visible`` into ``_base_kwargs``.
    A family that drops the kwarg leaves the theme's root
    ``charts.legend.visible: false`` standing, so the author places a legend
    that never renders — and the omission is invisible in review, which is what
    this file exists to catch. Asserting visibility rather than position is
    deliberate: the ``author_placed_legend`` gate on ``top_legend`` already
    yields the right position without the kwarg, so a position-only assertion
    stays green when the threading is deleted.

    Deleting the kwarg reddens line and area here, and only those two: under all
    seven shipped themes bar, scatter and heatmap carry ``legend.visible: true``
    as a family default, so the kwarg is a no-op for them and no test can reach
    it without manufacturing a theme. They are parametrized anyway because the
    contract is the same authored key resolving visible in every family, not the
    mechanism that gets it there — a family that later inherits the root
    ``visible: false`` starts exercising the kwarg with nobody editing this file.
    """

    @pytest.mark.parametrize(
        "family", _family_params({"histogram": _HISTOGRAM_NO_SERIES_LEGEND})
    )
    def test_authored_position_resolves_to_a_visible_legend(self, family: str) -> None:
        chart = _BUILDERS[family](
            color="series",
            style=_STYLE_PATCHES[family].model_validate(
                {"legend": {"position": "bottom"}}
            ),
        )
        resolved = resolve(chart, _SERIES_DATA, _board())
        assert resolved.legend.visible is True, (
            f"{family}: authored legend.position resolved to an invisible legend "
            f"-- force_legend_visible is not threaded into _base_kwargs"
        )
        assert resolved.legend.position == "bottom", (
            f"{family}: authored legend.position was overridden "
            f"(position={resolved.legend.position!r})"
        )


class TestAuthoredLegendPositionSurvivesTopLegendRoutes:
    """Invariant 6: an authored ``legend.position`` beats every route that would
    otherwise force a top legend -- multiples, layered overlays, and bar's own
    unconditional (non-stacked) default (already exercised by invariant 5's
    plain-chart case, since bar's un-stacked default is that route) -- with one
    documented exception: the tiny-width tier.

    ``cartesian_series_naming()`` gates both its ``top_legend_series`` fit
    check and ``_multiples_wants_top_legend()`` behind a single
    ``not author_moved_legend_off_top``. Testing only one of those routes
    would leave the same invisible-skip risk this file exists to catch if the
    gate were narrowed to cover just one of them later. ``tiny_top_legend``
    sits outside that gate by design (see its docstring in ``_axes.py``) and is
    pinned separately below as the one route where the fallback still wins.
    """

    @pytest.mark.parametrize(
        "family", _family_params({"histogram": _HISTOGRAM_NO_SERIES_LEGEND})
    )
    def test_authored_position_survives_multiples_top_legend_route(
        self, family: str
    ) -> None:
        chart = _BUILDERS[family](
            color="series",
            multiples=MultiplesConfig(rows="facet"),
            style=_STYLE_PATCHES[family].model_validate(
                {"legend": {"position": "bottom"}}
            ),
        )
        resolved = resolve(chart, _SERIES_DATA, _board())
        assert resolved.legend.position == "bottom", (
            f"{family}: multiples + color series overrode the authored legend "
            f"position (position={resolved.legend.position!r})"
        )
        assert resolved.legend.visible is True

    @pytest.mark.parametrize(
        "family",
        _family_params(
            {
                "bar": (
                    "bar answers the same 'does the top strip carry the legend' "
                    "question unconditionally when not stacked, already covered "
                    "by invariant 5's plain-chart case."
                ),
                "scatter": (
                    "Scatter (and bubble) never routes to a top legend, layered "
                    "or not -- its legend swatch reads as one more data mark "
                    "(mark-similarity carve-out, RJ 2026-08-26), so it stays on "
                    "the right-hand vertical default cartesian_series_naming() "
                    "never overrides for this family."
                ),
                "heatmap": (
                    "HeatmapChart carries no `layers` field at all, and its "
                    "color channel is always a gradient legend -- a different "
                    "shape the top-legend fit rule does not apply to."
                ),
                "histogram": _HISTOGRAM_NO_SERIES_LEGEND,
            }
        ),
    )
    def test_authored_position_survives_layers_top_legend_route(
        self, family: str
    ) -> None:
        chart = _BUILDERS[family](
            layers=[LineLayer(type="line", y="target")],
            style=_STYLE_PATCHES[family].model_validate(
                {"legend": {"position": "bottom"}}
            ),
        )
        resolved = resolve(chart, _LAYER_DATA, _board())
        assert resolved.legend.position == "bottom", (
            f"{family}: a layered overlay overrode the authored legend position "
            f"(position={resolved.legend.position!r})"
        )

    @pytest.mark.parametrize(
        "family", _family_params({"histogram": _HISTOGRAM_NO_SERIES_LEGEND})
    )
    def test_tiny_width_still_forces_top_by_design(self, family: str) -> None:
        """Documented exception, not a defect: ``tiny_top_legend`` wins over an
        authored position the same way it already wins over the endpoint-label
        rail -- a card this narrow cannot physically hold a side legend. Pinned
        here so a future change to that policy is a deliberate edit to this
        test, not a silent behavior change. The fallback is no longer silent:
        the resolved chart carries the overridden position and a render-stage
        WARN-LEGEND-POSITION-WIDTH-FALLBACK diagnostic fires for it.
        """
        chart = _BUILDERS[family](
            color="series",
            style=_STYLE_PATCHES[family].model_validate(
                {"legend": {"position": "bottom"}}
            ),
        )
        resolved = resolve(chart, _SERIES_DATA, _board(), width=_TINY_WIDTH)
        assert resolved.legend.position == "top", (
            f"{family}: tiny-width chart with an authored legend.position no "
            f"longer falls back to the compact top legend "
            f"(position={resolved.legend.position!r}) -- if this is now correct "
            f"behavior, update this test and drop the width-tier caveat from "
            f"cartesian_series_naming()'s docstring"
        )
        assert resolved.legend.position_overridden_by_width == "bottom", (
            f"{family}: the tiny-width fallback overrode an authored "
            f"legend.position but left no resolved-only fact behind for the "
            f"render-stage detector to read "
            f"(position_overridden_by_width={resolved.legend.position_overridden_by_width!r})"
        )
        board = make_test_resolved_board(charts={resolved.id: resolved})
        ctx = WarningContext(
            board_spec=board,
            chart_results={resolved.id: _SERIES_DATA},
            vega_specs={},
            layout_charts={resolved.id: resolved},
        )
        warnings = legend_position_width_fallback.detect(ctx)
        assert len(warnings) == 1, (
            f"{family}: tiny-width legend fallback overrode an authored position "
            f"but WARN-LEGEND-POSITION-WIDTH-FALLBACK did not fire"
        )
        assert warnings[0].code == WARN_LEGEND_POSITION_WIDTH_FALLBACK.code
        assert "bottom" in warnings[0].message


# Two long, realistic entry names chosen so a single top-legend row measures
# short of a 450px card and clear of an 800px one (calibrated directly
# against legend_row_fits with the default theme's legend font -- see the
# task's Decision section for the "no fixed entry count" finding this proves:
# the same two entries fit or don't purely as a function of width). Both
# widths sit well above the 352.5px tiny-width tier (_TINY_MAX in
# compile/resolve/style/typography.py), which forces every family's legend to
# a compact top layout regardless of this policy -- testing under it would
# prove nothing about the fit rule.
_LONG_BASE_LABEL = "Quarterly Recurring Revenue"
_LONG_LAYER_LABEL = "Annual Contract Value Growth Rate By Region And Product Line"
_FIT_WIDTH = 800.0
_OVERFLOW_WIDTH = 450.0


def _layered_chart(family: str, **kwargs: Any) -> Chart:
    return _BUILDERS[family](
        y_label=_LONG_BASE_LABEL,
        layers=[LineLayer(type="line", y="target", label=_LONG_LAYER_LABEL)],
        **kwargs,
    )


class TestTopLegendFitRule:
    """The top-legend trigger this task replaces two per-family booleans with:
    a chart with layers to name gets a top legend while a single row naming
    them fits the card, and the right-hand vertical legend once it does not
    -- the same rule for every family that opts in, not a family-specific
    entry count.
    """

    @pytest.mark.parametrize("family", ["bar", "area", "line"])
    def test_wide_card_fits_and_gets_top_legend(self, family: str) -> None:
        """Invariant 1 (acceptance): two charts of the same shape (base +
        one overlay layer) place their legend identically regardless of base
        family, when the row fits.

        ``columns == 0`` pins this to rung 1 specifically -- ``position ==
        "top"`` alone is satisfied by rung 2 (wrapped, compact-columns) as
        well, so a chart that silently slid off rung 1 onto rung 2 for the
        same entries at the same width would pass this assertion unnoticed
        (`test_horizontal_bar_left_axis_reserve_wraps_a_row_that_would_otherwise_fit`
        distinguishes the two rungs the same way, bar-only)."""
        chart = _layered_chart(family)
        resolved = resolve(chart, _LAYER_DATA, _board(), width=_FIT_WIDTH)
        assert resolved.legend.position == "top", (
            f"{family}: a layered chart whose legend row fits the card did "
            f"not get a top legend (position={resolved.legend.position!r})"
        )
        assert resolved.legend.columns == 0, (
            f"{family}: a layered chart whose legend row fits the card "
            f"landed on rung 2 (wrapped, compact-columns) instead of rung 1 "
            f"(columns={resolved.legend.columns!r})"
        )

    @pytest.mark.parametrize("family", ["bar", "area", "line"])
    def test_narrow_card_wraps_to_compact_top_legend(self, family: str) -> None:
        """Rung 2 of the fallback ladder (RJ, 2026-08-27): the exact same
        chart, only narrower, no longer fits a single top row -- but its two
        entries wrap into a row budget the card can afford, so it stays at
        the top, wrapped, rather than jumping straight to the right-hand
        legend.

        No ``visible`` assertion here: ``_kwargs.py`` forces
        ``visible: True`` unconditionally whenever ``top_legend != "off"``,
        which is already true at this rung regardless of
        ``force_legend_visible`` -- asserting it here would pass whether or
        not that field's ``wants_top_legend_shape`` half exists at all. The
        meaningful assertion is at rung 3, in
        ``test_too_many_entries_to_wrap_falls_back_to_right``, the one rung
        where visibility is NOT otherwise forced."""
        chart = _layered_chart(family)
        resolved = resolve(chart, _LAYER_DATA, _board(), width=_OVERFLOW_WIDTH)
        assert resolved.legend.position == "top", (
            f"{family}: a layered chart whose legend row overflows but whose "
            f"wrapped legend fits the row budget did not stay at the top "
            f"(position={resolved.legend.position!r})"
        )
        assert resolved.legend.columns != 0, (
            f"{family}: a layered chart whose legend wrapped to the top is "
            f"not in compact-columns layout (columns=0 means the uncompacted "
            f"'row' layout, not 'compact')"
        )

    @pytest.mark.parametrize("family", ["bar", "area", "line"])
    def test_too_many_entries_to_wrap_falls_back_to_right(self, family: str) -> None:
        """Rung 3 of the fallback ladder (RJ, 2026-08-27): once even a wrapped
        legend would sink the plot below the shared plot-height floor
        (chart_rendering.plot_height_floor.ratio -- the same floor bar.py's
        own starved-plot warning checks), the chart gives up on top
        placement and falls back to the family's own right-hand legend,
        same as the old single-rung rule did for every overflow.

        Asserts ``visible`` too -- this is the one rung where it is not
        otherwise forced. ``_kwargs.py`` folds ``visible: True``
        unconditionally whenever ``top_legend != "off"`` (rungs 1 and 2);
        at rung 3, ``top_legend == "off"``, so ``force_legend_visible``'s
        ``wants_top_legend_shape`` half (``_axes.py``) is the ONLY thing
        that can still force it on. On the editorial theme default, line
        and area hide their legend unless something forces it back on --
        without this, a layered line or area whose row overflows loses its
        legend entirely, and the overlay it exists to name goes unlabeled.

        16 regions (17 entries with the layer): the corrected marginal-cost
        floor charge (``legend_wrap_marginal_height_px``, flat per row, no
        fixed chrome of its own) flips this chart's own boundary to 14
        entries -- the fixed count this test used to pin (12) only overflowed
        the floor because the pre-fix code double-charged fixed chrome
        already subtracted by ``estimate_plot_height``. Comfortably past the
        real boundary rather than pinned to it, since this test is about the
        fallback firing at all, not the exact entry count it fires at."""
        regions = [f"Territory Region {i:02d}" for i in range(16)]
        data = [
            {"month": "Jan", "revenue": 10.0, "target": 12.0, "region": region}
            for region in regions
        ]
        chart = _layered_chart(family, color="region")
        resolved = resolve(chart, data, _board(), width=_OVERFLOW_WIDTH)
        assert resolved.legend.position == "right", (
            f"{family}: a chart with more legend entries than the wrap "
            f"budget allows still got a top legend "
            f"(position={resolved.legend.position!r})"
        )
        assert resolved.legend.visible is True, (
            f"{family}: a chart whose legend fell back to the right-hand "
            f"position is not visible -- its layer/color series is now "
            f"named nowhere"
        )

    @pytest.mark.parametrize("family", ["bar", "area", "line"])
    def test_same_entry_count_flips_rungs_as_card_height_changes(
        self, family: str
    ) -> None:
        """The whole point of the floor-based rule over a fixed row cap
        (RJ, 2026-08-27): the identical chart -- same width, same 8-entry
        legend -- lands on a different rung purely because the card is
        taller or shorter. A row count alone cannot express this; only a
        height-clearing check against the chart's own plot-height floor
        can."""
        regions = [f"Territory Region {i:02d}" for i in range(7)]
        data = [
            {"month": "Jan", "revenue": 10.0, "target": 12.0, "region": region}
            for region in regions
        ]
        tall_chart = _layered_chart(family, color="region", height=400.0)
        short_chart = _layered_chart(family, color="region", height=200.0)
        tall = resolve(tall_chart, data, _board(), width=_OVERFLOW_WIDTH)
        short = resolve(short_chart, data, _board(), width=_OVERFLOW_WIDTH)
        assert tall.legend.position == "top", (
            f"{family}: a taller card whose wrapped legend fits its height "
            f"budget did not stay top (position={tall.legend.position!r})"
        )
        assert tall.legend.columns != 0, (
            f"{family}: a taller card's wrapped legend is not in compact-columns layout"
        )
        assert short.legend.position == "right", (
            f"{family}: a shorter card whose identical legend now exceeds "
            f"its height budget still got a top legend "
            f"(position={short.legend.position!r}) -- height, not just row "
            f"count, must be able to flip this rung"
        )

    def test_scatter_with_layers_stays_right_at_the_fit_width(self) -> None:
        """The mark-similarity carve-out: scatter (and bubble) never routes to
        a top legend through this mechanism, even at the exact width where
        bar/area/line above flip to top for the identical entries -- this is
        the original bug's 11C cell, now a documented exception rather than
        an unexplained default."""
        chart = _layered_chart("scatter")
        resolved = resolve(chart, _LAYER_DATA, _board(), width=_FIT_WIDTH)
        assert resolved.legend.position == "right", (
            f"scatter: a layered chart got a top legend "
            f"(position={resolved.legend.position!r}) -- scatter must never "
            f"route layers to a top legend"
        )

    def test_bare_scatter_is_unchanged(self) -> None:
        """RJ's standing constraint: a bare (unlayered) scatter with a
        categorical color legend keeps its right-hand vertical legend --
        this policy must never touch that case."""
        chart = _scatter(color="series")
        resolved = resolve(chart, _SERIES_DATA, _board(), width=_FIT_WIDTH)
        assert resolved.legend.position == "right"
        assert resolved.legend.direction == "vertical"

    def test_bar_grouped_by_a_real_color_field_also_respects_fit(self) -> None:
        """Bar's own long-standing unconditional (non-stacked) trigger now
        goes through the same fit check as the layered case, using the
        chart's real, executed color-domain values (not a guess) --
        previously this route ignored width entirely. At the narrow width,
        the 5-entry row overflows but wraps within the row budget (rung 2 of
        the fallback ladder, RJ 2026-08-27), so it stays top rather than
        jumping to the right-hand legend."""
        regions = [
            "Northeast Territory Region",
            "Southwest Territory Region",
            "North Central Region",
            "Pacific Northwest Region",
            "Mid Atlantic Region",
        ]
        long_domain_data = [
            {"month": "Jan", "revenue": 10.0, "region": region} for region in regions
        ]
        chart = _bar(color="region")
        wide = resolve(chart, long_domain_data, _board(), width=900.0)
        assert wide.legend.position == "top", (
            f"a grouped bar whose real color-domain legend row fits the "
            f"card did not get a top legend (position={wide.legend.position!r})"
        )
        assert wide.legend.columns == 0, (
            "a grouped bar whose legend row fits the card landed on rung 2 "
            f"(wrapped, compact-columns) instead of rung 1 "
            f"(columns={wide.legend.columns!r})"
        )
        narrow = resolve(chart, long_domain_data, _board(), width=_OVERFLOW_WIDTH)
        assert narrow.legend.position == "top", (
            f"a grouped bar whose real color-domain legend row overflows "
            f"but whose wrapped legend fits the row budget did not stay top "
            f"(position={narrow.legend.position!r})"
        )
        assert narrow.legend.columns != 0, (
            "a grouped bar whose legend wrapped to the top is not in "
            "compact-columns layout"
        )

    @pytest.mark.parametrize("family", ["area", "line"])
    def test_layered_chart_with_real_color_domain_also_respects_fit(
        self, family: str
    ) -> None:
        """Line and area used to hardcode color_domain_values=() even when
        the chart's own `color:` channel was set -- `color:` + scalar `y:` +
        `layers:` is a legal shape for both, and the task's Decision names
        this exact combination ("bar and area") as in scope. 900px is chosen
        so the old, domain-blind measurement (base + layer label only) says
        the row fits, while the real 5-region domain the base actually
        colors by does not -- the width where the bug silently overflowed a
        top legend it should have fallen back from. The 6-entry wrapped
        legend still fits the row budget (rung 2, RJ 2026-08-27), so it
        stays top rather than falling all the way to the right."""
        regions = [
            "Northeast Territory Region",
            "Southwest Territory Region",
            "North Central Region",
            "Pacific Northwest Region",
            "Mid Atlantic Region",
        ]
        color_layer_data = [
            {"month": "Jan", "revenue": 100.0, "target": 4000.0, "region": region}
            for region in regions
        ]
        chart = _layered_chart(family, color="region")
        wide = resolve(chart, color_layer_data, _board(), width=1200.0)
        assert wide.legend.position == "top", (
            f"{family}: a layered, colored chart whose real color-domain "
            f"legend row fits the card did not get a top legend "
            f"(position={wide.legend.position!r})"
        )
        assert wide.legend.columns == 0, (
            f"{family}: a layered, colored chart whose legend row fits the "
            f"card landed on rung 2 (wrapped, compact-columns) instead of "
            f"rung 1 (columns={wide.legend.columns!r})"
        )
        narrow = resolve(chart, color_layer_data, _board(), width=900.0)
        assert narrow.legend.position == "top", (
            f"{family}: a layered, colored chart whose real color-domain "
            f"legend row overflows but whose wrapped legend fits the row "
            f"budget did not stay top (position={narrow.legend.position!r}) "
            f"-- the fit rule is inert for colored line/area charts"
        )
        assert narrow.legend.columns != 0, (
            f"{family}: a layered, colored chart whose legend wrapped to "
            f"the top is not in compact-columns layout"
        )

    def test_stacked_bar_with_layers_stays_right(self) -> None:
        """Bar's own comment (bar.py, just above the top_legend_series gate)
        says a stacked bar's segments read better against a side legend --
        stacking is the trigger, not layer presence. A stacked bar with an
        overlay layer (the ordinary "stacked bar + target line" shape) must
        stay off this mechanism the same as an unlayered stacked bar does,
        even at a width wide enough that a top row would easily fit."""
        chart = _layered_chart("bar", stack="zero")
        resolved = resolve(chart, _LAYER_DATA, _board(), width=_FIT_WIDTH)
        assert resolved.legend.position == "right", (
            f"a stacked bar with an overlay layer got a top legend "
            f"(position={resolved.legend.position!r}) -- stacking must keep "
            f"it off the top-legend fit rule regardless of layers"
        )

    def test_horizontal_bar_left_axis_reserve_wraps_a_row_that_would_otherwise_fit(
        self,
    ) -> None:
        """Regression, resolve-level: a horizontal bar's dimension field
        (its 12 month labels) sits on Vega-Lite's y-channel, on the left --
        real content a top legend's row loses width to (RJ review round,
        2026-08-27). At this exact width the row's own text measures as
        fitting the RAW card width, but not once the real left-axis reserve
        is subtracted -- proven by the vertical companion below, whose
        measure axis defaults right and gets no such reserve, landing rung 1
        (uncompacted, ``columns == 0``) for the identical entries at the
        identical width. ``columns != 0`` is rung 2's own signature --
        ``position == "top"`` alone is satisfied by both rungs and would not
        catch a chart sliding between them."""
        regions = (
            "North Central Region",
            "Northeast Territory Region",
            "Pacific Northwest Region",
            "Southwest Territory Region",
        )
        months = (
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
        data = [
            {"month": m, "region": r, "revenue": 100.0} for m in months for r in regions
        ]
        width = 672.0
        horizontal = _bar(
            color="region", style=BarChartStylePatch(orientation="horizontal")
        )
        resolved_horizontal = resolve(horizontal, data, _board(), width=width)
        assert resolved_horizontal.legend.position == "top", (
            f"a horizontal bar's colored legend row did not land top at all "
            f"(position={resolved_horizontal.legend.position!r})"
        )
        assert resolved_horizontal.legend.columns != 0, (
            "a horizontal bar's legend row, which only fits the card's raw "
            "width and not the width net of its own left-side dimension "
            "axis, was declared an uncompacted rung-1 fit -- the left-axis "
            "reserve is not being subtracted"
        )

        vertical = _bar(
            color="region", style=BarChartStylePatch(orientation="vertical")
        )
        resolved_vertical = resolve(vertical, data, _board(), width=width)
        assert resolved_vertical.legend.position == "top", (
            f"a vertical bar's colored legend row did not land top at all "
            f"(position={resolved_vertical.legend.position!r})"
        )
        assert resolved_vertical.legend.columns == 0, (
            "a vertical bar's colored legend row -- whose measure axis "
            "defaults to the right, reserving nothing real on the left -- "
            "was wrongly wrapped at the identical width and entries as the "
            "horizontal case; the reserve must be gated on orientation, not "
            "applied unconditionally"
        )

    def test_horizontal_bar_left_axis_reserve_accounts_for_an_authored_label_expr(
        self,
    ) -> None:
        """An authored ``axis_x.labels.expr`` paints text the reserve can't
        measure, so the reserve takes the axis's label limit instead of the
        raw one-character values. The same chart without the expr fits the
        legend row at rung 1, which keeps this test from passing on metric
        drift alone."""
        regions = (
            "North Central Region",
            "Northeast Territory Region",
            "Pacific Northwest Region",
            "Southwest Territory Region",
        )
        cats = ("A", "B", "C", "D")
        data = [
            {"month": m, "region": r, "revenue": 100.0} for m in cats for r in regions
        ]

        def resolve_with(labels: dict[str, object], width: float) -> ResolvedChart:
            style = BarChartStylePatch.model_validate(
                {"orientation": "horizontal", "axis_x": {"labels": labels}}
            )
            return resolve(
                _bar(color="region", style=style), data, _board(), width=width
            )

        long_expr = {"expr": "'Very Long Constant Category Label Text'"}
        assert resolve_with({}, 700.0).legend.columns == 0
        with_expr = resolve_with(long_expr, 700.0)
        assert with_expr.legend.position == "top"
        assert with_expr.legend.columns != 0
        assert resolve_with({**long_expr, "visible": False}, 700.0).legend.columns == 0
        assert resolve_with({"expr": "'x'", "max_width": 20}, 760.0).legend.columns == 0
        assert resolve_with({"expr": "'x'"}, 760.0).legend.columns != 0

    @pytest.mark.parametrize("family", ["bar", "line", "area"])
    def test_gradient_colored_chart_never_routes_to_top(self, family: str) -> None:
        """A gradient color channel (chart.style.color.gradient, authorable
        on bar/line/area alike via the shared ``ColorStyle``) is a continuous
        ramp, not a row of discrete series -- it must never drive this
        ladder, the same way heatmap.py's own always-gradient color channel
        never does. Scoped to widths above the tiny tier: below _TINY_MAX
        (352.5px) ``tiny_top_legend`` short-circuits ahead of both rungs and
        a gradient chart does still land top, which is deliberate
        family-agnostic policy predating this rule, not a gap in it. Checked once, in
        ``cartesian_series_naming`` itself, not per family: line and area
        only ever build ``top_legend_series`` when layered, so
        ``_layered_chart`` reproduces the shape each family's own trigger
        actually needs to reach the bug.

        Width-invariance is the actual proof: before this fix, a gradient
        chart's real, distinct (numeric-string) domain values were fed to
        the fit rule as though they were legend entries, so placement varied
        with width purely by accident of how many of those values a row
        happened to fit -- not because a gradient legend has any row concept
        at all. A width-varying position for the identical chart is itself
        the defect.

        Also asserts ``visible`` at every width -- the earlier gradient fix
        reassigned ``top_legend_series`` to ``None`` to keep it off the
        ladder, which also killed ``wants_top_legend_shape``'s "this shape
        has entries to name" signal, silencing a gradient-colored line/area
        legend entirely on editorial (which hides line/area legends by
        default). The fix must exclude the gradient from the two rungs that
        measure rows without touching that signal, and this is the
        assertion that catches a regression back to reassigning it."""
        chart = _layered_chart(
            family,
            color="intensity",
            style=_STYLE_PATCHES[family](
                color={"gradient": {"palette": ["#fff", "#000"]}}
            ),
        )
        data = [
            {
                "month": "Jan",
                "revenue": 10.0,
                "target": 12.0,
                "intensity": float(i),
            }
            for i in range(12)
        ]
        widths = (450.0, 900.0, 2000.0)
        resolved_by_width = {
            width: resolve(chart, data, _board(), width=width) for width in widths
        }
        positions = {
            width: resolved.legend.position
            for width, resolved in resolved_by_width.items()
        }
        assert set(positions.values()) == {"right"}, (
            f"{family}: a gradient-colored chart's legend placement must "
            f"not vary across the widths above the tiny tier -- got "
            f"{positions!r}. Below _TINY_MAX (352.5px) `tiny_top_legend` "
            f"short-circuits ahead of both rungs and a gradient chart still "
            f"lands top; that route is family-agnostic policy predating this "
            f"rule, so these widths deliberately start above it."
        )
        for width, resolved in resolved_by_width.items():
            assert resolved.legend.visible is True, (
                f"{family}: a gradient-colored, layered chart's legend is "
                f"not visible at width={width} -- its layer/color series is "
                f"now named nowhere"
            )


class TestSortFieldMissingRaises:
    """Invariant: a ``sort.by`` column absent from a row raises, never sorts naturally."""

    @pytest.mark.parametrize("family", _FAMILIES)
    def test_missing_sort_field_raises(self, family: str) -> None:
        build = _BUILDERS.get(family) or (lambda **kw: _bar(type="histogram", **kw))
        chart = build(sort=ChartSort(by="nonexistent_col", order="desc"))
        with pytest.raises(ChartDataError, match="nonexistent_col") as exc:
            resolve(chart, _SERIES_DATA, _board())
        assert exc.value.code.code == "ERR-SORT-FIELD-NOT-FOUND"

    def test_spark_bar_missing_sort_field_raises(self) -> None:
        chart = SparkBarChart(
            id="c",
            type="spark_bar",
            x="revenue",
            y="month",
            query=_sql(),
            query_name="q",
            sort=ChartSort(by="nonexistent_col", order="desc"),
        )
        with pytest.raises(
            ChartDataError, match="ERR-SORT-FIELD-NOT-FOUND|nonexistent"
        ):
            resolve(chart, _SERIES_DATA, _board())
