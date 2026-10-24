"""Integration: a narrow bar card's starved plot is measured, not mutated.

Real ``resolve()`` calls on a ``BarChart`` — see
``tests/core/compile/resolve/chart/test_plot_height_floor.py`` for the pure
``estimate_plot_height`` unit tests this wires
up, ``tests/render/warnings/test_plot_height_below_minimum.py`` for the
``WARN-PLOT-HEIGHT-BELOW-MINIMUM`` detector that reads the fields asserted
here, and ``constants/estimates.py`` for the constants.

No authored chrome is ever removed: this only asserts the resolved
``style.plot_height_below_floor`` fact (and its supporting
``estimated_plot_height_px``/``estimated_card_height_px``), never a change
in what renders.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved.bar import ResolvedBarStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._axes import legend_wrap_marginal_height_px
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
)
from dbt_charts.core.constants.estimates import ROW_LEGEND_TOTAL_PX

_CTX = resolve_chart_style_context(get_theme_style())


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _grouped_data(series: int = 5) -> list[dict[str, str | float]]:
    names = ("North America", "EMEA", "APAC", "LATAM", "Other")
    regions = [names[i] if i < len(names) else f"Region {i:02d}" for i in range(series)]
    return [
        {"quarter": q, "region": r, "revenue": 100.0 + i}
        for i, q in enumerate(["Q1", "Q2", "Q3", "Q4"])
        for r in regions
    ]


def _resolved(
    width: float,
    *,
    title: str = "Quarterly revenue by region",
    subtitle: str = "Grouped by region across four fiscal quarters",
    x_label: str | None = "Fiscal quarter",
    y_label: str | None = "Revenue (USD)",
    color: str | None = "region",
    height: float | None = None,
    hide_axis_title: str | None = None,
    orientation: str | None = None,
    series: int = 5,
    hide_legend: bool = False,
    patch_extra: dict[str, object] | None = None,
    ctx: ChartStyleContext = _CTX,
) -> ResolvedBarChart:
    patch: dict[str, object] = dict(patch_extra or {})
    if hide_legend:
        patch["legend"] = {"visible": False}
    if hide_axis_title:
        patch[hide_axis_title] = {"title": {"visible": False}}
    if orientation:
        patch["orientation"] = orientation
    style = BarChartStylePatch.model_validate(patch) if patch else None
    chart = BarChart(
        id="probe",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        title=title,
        subtitle=subtitle,
        x="quarter",
        y="revenue",
        color=color,
        x_label=x_label,
        y_label=y_label,
        height=height,
        style=style,
    )
    resolved = resolve(chart, _grouped_data(series), ctx, width=width)
    assert isinstance(resolved, ResolvedBarChart)
    return resolved


def test_wide_card_is_never_flagged_and_nothing_is_touched() -> None:
    """640px is nowhere near the tiny tier — the plot is never flagged, and
    every piece of authored chrome is still present, unchanged."""
    resolved = _resolved(640.0)
    assert resolved.style.plot_height_below_floor is False
    assert resolved.subtitle == "Grouped by region across four fiscal quarters"
    assert resolved.style.axis_x.title.visible is not False
    assert resolved.style.axis_y.title.visible is not False
    assert resolved.legend.visible is not False


def test_300px_card_is_flagged_but_nothing_is_removed() -> None:
    """The calibration case: at 300px the plot is genuinely starved, so the
    fact is flagged — but title, subtitle, axis titles, and the legend all
    still render exactly as authored."""
    resolved = _resolved(300.0)
    assert resolved.style.plot_height_below_floor is True
    assert (
        resolved.style.estimated_plot_height_px
        < resolved.style.estimated_card_height_px * 0.30
    )
    assert resolved.style.axis_x.title.visible is not False
    assert resolved.style.axis_y.title.visible is not False
    assert resolved.subtitle == "Grouped by region across four fiscal quarters"
    assert resolved.legend.visible is not False


def test_severely_squeezed_card_is_flagged_with_a_negative_estimate() -> None:
    """A very short explicit height pushes the estimate negative -- still
    just a flag, and the chart still renders every authored piece."""
    resolved = _resolved(300.0, height=90.0)
    assert resolved.style.plot_height_below_floor is True
    assert resolved.style.estimated_plot_height_px < 0
    assert resolved.style.axis_x.title.visible is not False
    assert resolved.subtitle == "Grouped by region across four fiscal quarters"


def test_dropping_the_subtitle_is_not_enough_at_300px() -> None:
    """Recovering the subtitle's height does not save a 300px card.

    Rendered, this card is 300x232px with a 58px plot — 0.250 of its own
    height, still under the floor. This test previously asserted the
    opposite; it was reading a card height short by 2 * card_padding, which
    put the floor 9.6px lower than the card being drawn.
    """
    resolved = _resolved(300.0, subtitle="")
    assert resolved.style.plot_height_below_floor is True
    assert resolved.subtitle == ""


def test_a_slightly_wider_card_clears_the_floor_without_a_subtitle() -> None:
    """320px without a subtitle renders a 320x245px card with a 77px plot —
    0.315 of its height, over the floor, and correctly not flagged."""
    assert _resolved(320.0, subtitle="").style.plot_height_below_floor is False


def test_single_series_narrow_card_has_no_legend_to_be_squeezed_by() -> None:
    """No color field -> no legend at all, so nothing competes for the
    plot's height at 300px and the floor is already cleared -- the boring,
    correct case where nothing is flagged."""
    resolved = _resolved(300.0, color=None)
    assert resolved.style.plot_height_below_floor is False


def test_a_roomy_card_clears_the_floor_on_its_own_merits() -> None:
    """353px is not flagged because its plot genuinely clears the floor —
    rendered, a 353x267px card with a 147px plot, 0.551 of its height."""
    assert _resolved(353.0).style.plot_height_below_floor is False


def test_a_wide_card_can_still_be_flagged() -> None:
    """Width does not exempt a card. At 640px — far above the 352.5px tier
    boundary a retired gate used to exempt — an authored height of 90px
    starves the plot just as thoroughly, and is reported.

    The floor is a fraction of the card's own height, so a short wide card
    is starved in exactly the way a narrow one is. Without this, nothing in
    the suite would notice a width gate coming back at the call site.
    """
    assert _resolved(640.0, height=90.0).style.plot_height_below_floor is True


@pytest.mark.parametrize(
    ("orientation", "costs_height", "costs_width"),
    [("vertical", "axis_x", "axis_y"), ("horizontal", "axis_y", "axis_x")],
)
def test_only_the_bottom_axis_title_is_charged_against_height(
    orientation: str, costs_height: str, costs_width: str
) -> None:
    """Only the axis title on the bottom rail costs the plot height. The
    other one is rotated and costs width, so hiding it frees no height.

    Which channel lands on the bottom depends on orientation — a horizontal
    bar puts the measure axis there, a vertical one the category axis.
    Measured on a vertical bar at a 300px card: hiding the x title took the
    plot from 44px to 65px tall at unchanged width, while hiding the y title
    took it from 216px to 238px wide at unchanged height. Charging whichever
    of the two happens to be visible bills 21px of height for chrome that
    never took any.
    """
    baseline = _resolved(300.0, orientation=orientation).style
    freed = _resolved(
        300.0, orientation=orientation, hide_axis_title=costs_height
    ).style
    unchanged = _resolved(
        300.0, orientation=orientation, hide_axis_title=costs_width
    ).style
    assert unchanged.estimated_plot_height_px == baseline.estimated_plot_height_px
    assert freed.estimated_plot_height_px > baseline.estimated_plot_height_px


def test_the_card_height_measured_against_is_the_one_the_renderer_builds() -> None:
    """The floor's denominator is the card height ``render/sizing.py``
    actually produces for this chart, card padding included.

    ``_estimate_bar_plot_height`` mirrors ``get_chart_content_height``'s
    aspect-ratio arithmetic but stopped short of the ``+ 2 * card_padding``
    the renderer's ``get_item_content_height`` adds on top, so the floor was
    a fraction of a card 32px shorter than the real one at the default
    theme. Pinned against the renderer's own entry point so the two cannot
    drift apart again.
    """
    from dbt_charts.core.compile.models.board.normalized import LayoutItem
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.sizing import get_item_content_height

    width = 340.0
    chart = BarChart(
        id="probe",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="quarter",
        y="revenue",
        color="region",
    )
    renderer_card_height = get_item_content_height(
        LayoutItem(type="chart", chart=chart),
        card_gap=0.0,
        gap=0.0,
        width=width,
        resolved_style=resolve_style(get_theme_style()),
    )
    resolved = resolve(chart, _grouped_data(), _CTX, width=width)
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.style.estimated_card_height_px == pytest.approx(
        renderer_card_height
    )


def test_a_starved_plot_is_flagged_at_340px() -> None:
    """340px: rendered, this card is 340x259px with a 77px plot — 0.296 of
    its own height, under the 0.30 floor — and it went unreported.

    Measured from the emitted SVG, not from the estimate. What hid it was
    the card height the floor was taken as a fraction of, short by
    ``2 * card_padding``. (340px sits inside the "tiny" tier, so the
    retired width gate was never what suppressed this one —
    ``test_a_wide_card_can_still_be_flagged`` covers that.)
    """
    assert _resolved(340.0).style.plot_height_below_floor is True


def test_a_legend_is_charged_only_the_rows_vega_will_draw() -> None:
    """A legend past ``symbol_limit`` costs the rows it renders, not one per
    series.

    Vega draws at most ``symbol_limit`` symbols (20 by default), so a
    60-series legend is 20 symbols tall, not 60. Charging a row per series
    bills height that is never drawn and accuses a healthy plot: unclamped,
    this card estimates -117px against a 189.6px floor and fires. Clamped it
    estimates 303px and stays quiet. Remove the clamp and this fails.
    """
    roomy = _resolved(300.0, height=600.0, series=60).style
    assert roomy.plot_height_below_floor is False
    assert roomy.estimated_plot_height_px > roomy.estimated_card_height_px * 0.30
    # Below the cap the clamp is inert, so the same card with fewer series
    # must land on exactly the same estimate.
    assert (
        _resolved(300.0, height=600.0, series=20).style.estimated_plot_height_px
        == roomy.estimated_plot_height_px
    )


def test_a_single_row_legend_is_charged_at_a_wide_card() -> None:
    """A wide grouped bar's legend flows as one horizontal row above the
    plot, and that row costs the plot height.

    Bar's default with a ``color:`` channel is grouped, and a grouped bar
    emits this legend at every width — not just in the tiny tier where the
    multi-column ``compact`` layout takes over. Measured legend-on vs
    legend-off at a 400px card: the plot went 146 -> 177px tall at unchanged
    width, and the same ~31px at 640px and at 2, 5, 16 and 25 series. Charge
    only ``compact`` and every default grouped bar above the tiny tier bills
    0px for a strip that really takes ~31px.
    """
    with_legend = _resolved(640.0).style.estimated_plot_height_px
    without = _resolved(640.0, hide_legend=True).style.estimated_plot_height_px
    assert without - with_legend == pytest.approx(ROW_LEGEND_TOTAL_PX)


def test_the_row_legends_charge_does_not_track_series_count() -> None:
    """That legend is one flowing row, so its height is flat — unlike the
    tiny tier's ``compact`` layout, whose height tracks its row count.

    Both series counts stay on the ``row`` rung at this width: past 6
    series the region-name generator's longer "Region NN" labels overflow
    the single row and the fit-rule ladder wraps the legend into the
    row-count-tracking ``compact`` layout instead, which is exactly the
    behavior this test is distinguishing itself from.
    """
    assert (
        _resolved(640.0, series=6).style.estimated_plot_height_px
        == _resolved(640.0, series=2).style.estimated_plot_height_px
    )


def test_a_chart_with_nothing_to_name_is_charged_no_legend() -> None:
    """A bar with no ``color:`` channel has no legend, so it is charged
    nothing for one — even though the layout policy still reads ``row``.

    ``top_legend`` describes where a legend *would* go, not whether one
    exists. Charging the flat row-layout height off that alone bills every
    single-series bar 31px for a strip Vega never emits.
    """
    named = _resolved(640.0).style.estimated_plot_height_px
    unnamed = _resolved(640.0, color=None, series=1).style.estimated_plot_height_px
    assert unnamed - named == pytest.approx(ROW_LEGEND_TOTAL_PX)


def test_a_layered_bar_is_charged_for_the_legend_its_layers_earn() -> None:
    """A bar with ``layers:`` draws a top legend naming the base series and
    each overlay, even with no ``color:`` and a plain string ``y``.

    Layers are a third source of legend entries alongside a color channel
    and a wide (list) ``y``. Count only the first two and this shape reads
    as zero entries, so the no-entries guard bills it nothing for a strip
    Vega really draws — measured on the spec as two entries, oriented top
    and horizontal.
    """
    layered = BarChart(
        id="probe",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        title="Quarterly revenue against target",
        subtitle="With a target overlay",
        x="quarter",
        y="revenue",
        layers=[LineLayer(type="line", y="target")],
    )
    rows: list[dict[str, str | float]] = [
        {"quarter": q, "revenue": 100.0 + i, "target": 90.0 + i}
        for i, q in enumerate(["Q1", "Q2", "Q3", "Q4"])
    ]
    resolved = resolve(layered, rows, _CTX, width=640.0)
    assert isinstance(resolved, ResolvedBarChart)

    unlegended = layered.model_copy(
        update={
            "style": BarChartStylePatch.model_validate({"legend": {"visible": False}})
        }
    )
    without = resolve(unlegended, rows, _CTX, width=640.0)
    assert isinstance(without, ResolvedBarChart)
    assert (
        without.style.estimated_plot_height_px - resolved.style.estimated_plot_height_px
    ) == pytest.approx(ROW_LEGEND_TOTAL_PX)


def test_measuring_a_layered_bar_never_suppresses_its_legend() -> None:
    """Counting a layered bar's legend entries must not reach the renderer.

    The floor keeps its own entry count precisely because
    ``legend_entry_count`` also feeds the stacked-legend yield classifier,
    whose verdict becomes ``suppress_legend`` on the resolved chart. Feeding
    the layer term into that shared variable un-short-circuits the
    classifier on a shape its stacked-segment collapse model was never
    calibrated for: measured at a 300px card, a stacked layered bar lost its
    legend at authored heights of 50px and 60px. On this shape the legend is
    the only thing telling base from overlay — endpoint labels are off — so
    that is a series left unnamed by a diagnostic that promises to change
    nothing.
    """
    rows: list[dict[str, str | float]] = [
        {"quarter": q, "revenue": 100.0 + i, "target": 90.0 + i}
        for i, q in enumerate(["Q1", "Q2", "Q3", "Q4"])
    ]
    for height in (50.0, 60.0, 80.0, 120.0):
        chart = BarChart(
            id="probe",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            title="Quarterly revenue against target",
            subtitle="With a target overlay",
            x="quarter",
            y="revenue",
            height=height,
            layers=[LineLayer(type="line", y="target")],
            style=BarChartStylePatch.model_validate({"stack": "zero"}),
        )
        resolved = resolve(chart, rows, _CTX, width=300.0)
        assert isinstance(resolved, ResolvedBarChart)
        assert resolved.legend.visible is not False, (
            f"legend suppressed at authored height {height}"
        )


def test_a_suppressed_legend_is_charged_nothing() -> None:
    """When the stacked-legend classifier drops the legend, the floor must
    not bill for it.

    At 19 series and a 300px card the classifier yields and the legend is
    not drawn. Charging it anyway costs 210px of estimate, drives the plot
    to -97px against a 69.6px floor, and tells the author to trim a legend
    they cannot see — the same false-positive class as an unclamped
    ``symbol_limit``.
    """
    fields = [f"m{i:02d}" for i in range(19)]
    rows: list[dict[str, str | float]] = [
        {"x_time": month, **{f: 10.0 + i for i, f in enumerate(fields)}}
        for month in ("2024-01-01", "2024-02-01", "2024-03-01")
    ]

    def build(hide_legend: bool) -> ResolvedBarChart:
        patch: dict[str, object] = {"stack": "zero"}
        if hide_legend:
            patch["legend"] = {"visible": False}
        chart = BarChart(
            id="probe",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            title="Monthly revenue by measure",
            subtitle="Nineteen stacked measures",
            x="x_time",
            y=fields,
            style=BarChartStylePatch.model_validate(patch),
        )
        resolved = resolve(chart, rows, _CTX, width=300.0)
        assert isinstance(resolved, ResolvedBarChart)
        return resolved

    starved = build(hide_legend=False)
    assert starved.legend.visible is False
    assert (
        starved.style.estimated_plot_height_px
        == build(hide_legend=True).style.estimated_plot_height_px
    )


def _colored_overlay(
    rows: list[dict[str, str | float]],
    *,
    layer_color: str | None,
    with_layer: bool,
) -> ResolvedBarStyle:
    """A 4-region bar at a 300px card, optionally carrying a target overlay."""
    chart = BarChart(
        id="probe",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        title="Quarterly revenue by region",
        subtitle="Against target",
        x="quarter",
        y="revenue",
        color="region",
        layers=(
            [LineLayer(type="line", y="target", color=layer_color)]
            if with_layer
            else []
        ),
    )
    resolved = resolve(chart, rows, _CTX, width=300.0)
    assert isinstance(resolved, ResolvedBarChart)
    return resolved.style


def test_an_overlay_adds_an_entry_to_a_color_legend() -> None:
    """An overlay joins the color legend rather than starting its own, so a
    colored bar with a layer is charged for one more entry than its color
    cardinality.

    Measured on the emitted spec: 3 regions plus one line layer produce the
    domain ``["APAC", "EMEA", "NA", "target"]`` under one shared top legend.
    Counting only the color cardinality charges ``ceil(3/2) = 2`` rows where
    the legend draws ``ceil(4/2) = 2`` — harmless until the cardinality is
    even, where 4-vs-5 entries is a whole row of height and the difference
    between reporting a starved plot and staying quiet. The expected delta is
    one row's worth of the shared ``legend_wrap_marginal_height_px`` height
    (the floor's own marginal charge, flat per row with no fixed chrome term
    of its own — not ``legend_wrap_required_height_px``, that one's fixed
    chrome would double-count against ``estimate_plot_height``'s own).
    """
    rows: list[dict[str, str | float]] = [
        {"quarter": q, "region": r, "revenue": 100.0 + i, "target": 90.0 + i}
        for i, q in enumerate(["Q1", "Q2", "Q3", "Q4"])
        for r in ("North America", "EMEA", "APAC", "LATAM")
    ]

    # Four regions fill two rows; the overlay's entry starts a third.
    plain = _colored_overlay(rows, layer_color=None, with_layer=False)
    layered = _colored_overlay(rows, layer_color=None, with_layer=True)
    legend = resolve_chart_style_context(get_theme_style()).legend
    four_entries = legend_wrap_marginal_height_px(4, None, legend.compact_columns)
    five_entries = legend_wrap_marginal_height_px(5, None, legend.compact_columns)
    assert (
        plain.estimated_plot_height_px - layered.estimated_plot_height_px
    ) == pytest.approx(five_entries - four_entries)


def test_a_layer_with_its_own_color_adds_no_entry_of_its_own() -> None:
    """A layer authoring `color:` contributes only values not already in the
    base's color domain — for a per-region target line, none of them.

    The emitter appends a color-less overlay's label unconditionally, but
    skips a colored layer's values that the scale already holds
    (``_overlay.py``: ``if value in scale_domain: continue``). Charging such
    a layer +1 regardless bills a compact row the legend never draws:
    measured at 4 regions on a 300px card, that took the estimate from 71.0
    to 50.0, under the 69.6px floor, and accused a chart drawing exactly the
    two rows its author expects.
    """
    rows: list[dict[str, str | float]] = [
        {"quarter": q, "region": r, "revenue": 100.0 + i, "target": 90.0 + i}
        for i, q in enumerate(["Q1", "Q2", "Q3", "Q4"])
        for r in ("North America", "EMEA", "APAC", "LATAM")
    ]
    plain = _colored_overlay(rows, layer_color=None, with_layer=False)
    same_domain = _colored_overlay(rows, layer_color="region", with_layer=True)
    assert same_domain.estimated_plot_height_px == plain.estimated_plot_height_px
    assert same_domain.plot_height_below_floor is False


def test_chrome_costs_derive_from_the_theme_fonts() -> None:
    """A subtitle costs its font size plus Vega's subtitle gap (11 + 3) and the
    horizontal rail's axis title its font size plus padding (11 + 10)."""
    baseline = _resolved(300.0, orientation="vertical").style
    no_subtitle = _resolved(300.0, orientation="vertical", subtitle="").style
    no_axis_title = _resolved(
        300.0, orientation="vertical", hide_axis_title="axis_x"
    ).style
    assert no_subtitle.estimated_plot_height_px - baseline.estimated_plot_height_px == (
        pytest.approx(14.0)
    )
    assert (
        no_axis_title.estimated_plot_height_px - baseline.estimated_plot_height_px
        == (pytest.approx(21.0))
    )


def test_an_authored_axis_title_font_size_changes_the_estimate() -> None:
    baseline = _resolved(300.0, orientation="vertical").style
    larger = _resolved(
        300.0,
        orientation="vertical",
        patch_extra={"axis_x": {"title": {"font": {"size": 20}}}},
    ).style
    assert baseline.estimated_plot_height_px - larger.estimated_plot_height_px == (
        pytest.approx(9.0)
    )


def test_a_larger_subtitle_font_size_changes_the_estimate() -> None:
    subtitle = _CTX.title.subtitle
    larger_font = subtitle.font.model_copy(update={"size": 20})
    title = _CTX.title.model_copy(
        update={"subtitle": subtitle.model_copy(update={"font": larger_font})}
    )
    ctx = dataclasses.replace(_CTX, title=title)
    baseline = _resolved(300.0, orientation="vertical").style
    larger = _resolved(300.0, orientation="vertical", ctx=ctx).style
    assert baseline.estimated_plot_height_px - larger.estimated_plot_height_px == (
        pytest.approx(9.0)
    )


def test_a_null_axis_title_padding_charges_the_vega_lite_default() -> None:
    baseline = _resolved(300.0, orientation="vertical").style
    unset = _resolved(
        300.0,
        orientation="vertical",
        patch_extra={"axis_x": {"title": {"padding": None}}},
    ).style
    assert unset.estimated_plot_height_px - baseline.estimated_plot_height_px == (
        pytest.approx(6.0)
    )


def test_a_layer_declining_its_legend_entry_is_not_charged_for_the_legend() -> None:
    """Six overlays wrap a top legend at 300px; declining them leaves the base alone."""
    rows: list[dict[str, str | float]] = [
        {"quarter": q, **{f"target_{n}": 90.0 + n for n in range(6)}, "revenue": 100.0}
        for q in ["Q1", "Q2", "Q3", "Q4"]
    ]

    def estimate(legend: bool) -> float:
        chart = BarChart(
            id="probe",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            title="Revenue against targets",
            x="quarter",
            y="revenue",
            layers=[
                LineLayer(
                    type="line",
                    y=f"target_{n}",
                    label=f"A long target label {n}",
                    legend=legend,
                )
                for n in range(6)
            ],
        )
        resolved = resolve(chart, rows, _CTX, width=300.0)
        assert isinstance(resolved, ResolvedBarChart)
        return resolved.style.estimated_plot_height_px

    assert estimate(legend=False) > estimate(legend=True)
