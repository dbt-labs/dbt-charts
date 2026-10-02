"""A stacked bar's legend yields to the marks once it would collapse them.

At a narrow card width, Vega-Lite's ``autosize:fit`` divides the card's total
height between the legend and the plot. Past some series count the legend's
demand leaves nothing for the stacked segments to draw into, and the chart
raises ERR-CHART-PAINTED-NO-MARKS instead of drawing a thin-but-honest stack.
The legend yields, the marks always survive.

In scope only for the one shape that actually collapses this way: a genuine
stack (``stack != "none"`` — a grouped bar subdivides width, not height),
rendered vertically (a horizontal stack's plot height comes from its category
count, not its series count), whose legend has actually moved to a top,
multi-column layout (a side legend costs width, not plot height).

A separate, broader measurement (``plot_height_below_floor``,
``plot_height_floor.py``) also watches narrow cartesian plots, but never
touches any chrome -- it only flags ``WARN-PLOT-HEIGHT-BELOW-MINIMUM`` when
the estimated plot height falls below a calibrated floor. It deliberately
never removes the legend either: for bar, the legend is typically a chart's
only series-naming mechanism, and hiding it with nothing to replace it would
violate the series-naming invariant (``tests/visual/
test_series_naming_invariant.py``). That measurement's own height accounting
still charges the legend's height against its floor; it just never acts on
the result. This file's assertions are unaffected by that measurement's
existence.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
    LegendStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart.bar import _stack_legend_should_yield
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.chart.mark_extents import all_marks_degenerate
from dbt_charts.core.render.chart.rendering import render_chart_item

_CTX = resolve_chart_style_context(get_theme_style())
_RESOLVED_STYLE = resolve_style(get_theme_style())

# Calibrated against dbt-charts/tests/visual's case-matrix corpus
# (12-series-x-width.yml) at its 300px "tiny" card width, whose default
# aspect-ratio-derived plot height is 200px (min_height/max_height/aspect_ratio
# defaults: 150/400/1.5). At the tiny tier the legend renders top/compact with
# a 2-column layout (theme default legend.compact_columns) — the boundary
# below is measured against the render with the classifier disabled: 18
# series (9 rows) draws fine, 19 (10 rows) collapses to ERR-CHART-PAINTED-NO-
# MARKS.
_NARROW_WIDTH = 300.0

# A second, narrower tiny-tier width where aspect_ratio_height's min_height
# clamp floors the estimated plot height at 150px (any width <= 225px lands
# here). A width-only calibration (fitting constants against _NARROW_WIDTH
# alone) previously left this floor band structurally unreachable — required()
# for 13-14 series computed to 149.5, just under the 150px floor, so the
# classifier could never fire below 15 series at any width, while the real
# render already collapses at 13. Measured with the classifier disabled: 12
# series (6 rows) draws fine, 13 (7 rows) collapses.
_FLOOR_WIDTH = 200.0


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _stacked_data(series_count: int) -> list[dict[str, str | float]]:
    return [
        {"x_time": month, "series": f"Series {s:02d}", "value": 10 + s}
        for month in ("2024-01-01", "2024-02-01", "2024-03-01")
        for s in range(1, series_count + 1)
    ]


def _wide_measure_fields(measure_count: int) -> list[str]:
    return [f"m{n:02d}" for n in range(1, measure_count + 1)]


def _wide_stacked_data(measure_count: int) -> list[dict[str, str | float]]:
    fields = _wide_measure_fields(measure_count)
    return [
        {"x_time": month, **{field: 10 + i for i, field in enumerate(fields)}}
        for month in ("2024-01-01", "2024-02-01", "2024-03-01")
    ]


def _resolved(
    series_count: int,
    width: float,
    stack: str = "zero",
    orientation: str | None = None,
    legend: LegendStylePatch | None = None,
    height: float | None = None,
    aspect_ratio: float | None = None,
    min_height: float | None = None,
) -> ResolvedBarChart:
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="x_time",
        y="value",
        color="series",
        height=height,
        aspect_ratio=aspect_ratio,
        min_height=min_height,
        style=BarChartStylePatch(stack=stack, orientation=orientation, legend=legend),
    )
    resolved = resolve(chart, _stacked_data(series_count), _CTX, width=width)
    assert isinstance(resolved, ResolvedBarChart)
    return resolved


def _resolved_wide(
    measure_count: int, width: float, stack: str = "zero"
) -> ResolvedBarChart:
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="x_time",
        y=_wide_measure_fields(measure_count),
        style=BarChartStylePatch(stack=stack),
    )
    resolved = resolve(chart, _wide_stacked_data(measure_count), _CTX, width=width)
    assert isinstance(resolved, ResolvedBarChart)
    return resolved


def _resolved_wide_by_dimension(
    dimension_count: int, measure_count: int, width: float
) -> ResolvedBarChart:
    fields = _wide_measure_fields(measure_count)
    data = [
        {"x_time": month, "series": f"Series {s:02d}", **dict.fromkeys(fields, 10 + s)}
        for month in ("2024-01-01", "2024-02-01", "2024-03-01")
        for s in range(1, dimension_count + 1)
    ]
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="x_time",
        y=fields,
        color="series",
        style=BarChartStylePatch(stack="zero"),
    )
    resolved = resolve(chart, data, _CTX, width=width)
    assert isinstance(resolved, ResolvedBarChart)
    return resolved


def test_wide_by_dimension_counts_every_composite_series() -> None:
    """`y: [a, b]` + `color:` renders dims x measures entries; the classifier
    must count those, not the dimension's cardinality alone (6 x 2 = 12
    keeps the legend at the floor width, 7 x 2 = 14 drops it)."""
    assert _resolved_wide_by_dimension(6, 2, _FLOOR_WIDTH).legend.visible is True
    assert _resolved_wide_by_dimension(7, 2, _FLOOR_WIDTH).legend.visible is False


def test_low_series_count_keeps_the_legend() -> None:
    assert _resolved(3, _NARROW_WIDTH).legend.visible is True


def test_eighteen_series_still_keeps_the_legend() -> None:
    """9 rows at 2 compact columns (16.0px/row + 47.0px chrome = 191px) fits 200px."""
    assert _resolved(18, _NARROW_WIDTH).legend.visible is True


def test_nineteen_series_drops_the_legend() -> None:
    """One series more tips the row count to 10 (207px), past the 200px plot height."""
    assert _resolved(19, _NARROW_WIDTH).legend.visible is False


def test_twenty_five_series_drops_the_legend() -> None:
    assert _resolved(25, _NARROW_WIDTH).legend.visible is False


def test_authored_legend_position_never_beats_the_yield() -> None:
    """An authored ``position:`` outranks the automatic legend policies, but not
    this guard. The tiny-tier ternary still resolves the legend to top/compact
    regardless of the authored position, so honoring ``visible`` here would
    hand the author a chart that raises ERR-CHART-PAINTED-NO-MARKS *and* a
    legend in a position they did not ask for. The marks always win.
    """
    for position in ("right", "bottom"):
        resolved = _resolved(
            25, _NARROW_WIDTH, legend=LegendStylePatch(position={"edge": position})
        )
        assert resolved.legend.visible is False, position


def test_authored_legend_visible_true_never_beats_the_yield() -> None:
    """The explicit signal must not lose where the weaker one wins."""
    assert (
        _resolved(
            25, _NARROW_WIDTH, legend=LegendStylePatch(visible=True)
        ).legend.visible
        is False
    )


def test_authored_height_escapes_the_yield_at_twenty_five_series() -> None:
    """``height`` bypasses the aspect-ratio clamp entirely and estimates the
    plot directly — an author who explicitly sizes a tall card is the only
    escape from the yield the classifier would otherwise apply here.
    """
    assert _resolved(25, _NARROW_WIDTH, height=600).legend.visible is True


def test_authored_aspect_ratio_escapes_the_yield_at_twenty_five_series() -> None:
    assert _resolved(25, _NARROW_WIDTH, aspect_ratio=0.5).legend.visible is True


def test_authored_min_height_escapes_the_yield_at_twenty_five_series() -> None:
    assert _resolved(25, _NARROW_WIDTH, min_height=400).legend.visible is True


def test_twelve_series_keeps_the_legend_at_floor_width() -> None:
    """6 rows (16.0px/row + 47.0px chrome = 143px) fits the 150px floor plot height."""
    assert _resolved(12, _FLOOR_WIDTH).legend.visible is True


def test_thirteen_series_drops_the_legend_at_floor_width() -> None:
    """7 rows (159px) exceeds the 150px floor — the band the old width-only
    calibration could never reach (required(13-14) computed to 149.5, just
    under the 150px floor, at every width).
    """
    assert _resolved(13, _FLOOR_WIDTH).legend.visible is False


def test_grouped_bar_keeps_its_legend_regardless_of_series_count() -> None:
    """A grouped bar subdivides width, not height — never in scope for this yield."""
    assert _resolved(25, _NARROW_WIDTH, stack="none").legend.visible is True


def test_horizontal_stack_keeps_its_legend_regardless_of_series_count() -> None:
    """A horizontal stack's plot height comes from its category count, not its
    series count — it never collapses the way a vertical stack does, so the
    classifier must never yield its legend.
    """
    resolved = _resolved(25, _NARROW_WIDTH, orientation="horizontal")
    assert resolved.orientation == "horizontal"
    assert resolved.legend.visible is True


def test_wide_card_never_reaches_the_yield_classifier() -> None:
    """At 450px — past the "tiny" width tier — endpoint labels already replace
    the legend by default, so the yield classifier never gets a legend to
    suppress. This is what makes the 60-series-at-450px case unchanged.
    """
    resolved = _resolved(60, 450.0)
    assert resolved.style.endpoint_labels.visible is True
    assert resolved.legend.visible is False


def test_author_narrowed_compact_columns_is_read_by_the_classifier() -> None:
    """The classifier must judge the legend that actually renders, not the
    board-level default. An authored ``style.legend.compact_columns: 1``
    lays the top legend out in a single column — 12 rows for 12 series, not
    the board default's 6 (2 columns) — so the plot collapses at a series
    count the board-default math alone would call safe. Charging the
    classifier the board-level legend (ignoring this override) would keep
    the legend visible while the renderer it actually builds still collapses
    the marks — the exact blank-chart bug this task exists to fix, reached
    through a different authoring path.
    """
    resolved = _resolved(12, _NARROW_WIDTH, legend=LegendStylePatch(compact_columns=1))
    assert resolved.legend.visible is False

    data = _stacked_data(12)
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = data
    svg, _height = render_chart_item(
        resolved,
        executor,
        variables={},
        available_width=_NARROW_WIDTH,
        available_height=200,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg
    assert not all_marks_degenerate(svg)


def test_yield_classifier_is_height_sensitive_not_just_width_tiered() -> None:
    """The same (symbol_limit-capped) 20-entry, 2-column legend fits a taller plot.

    Direct unit coverage of the pure classifier: entries, columns, and
    symbol_limit held fixed, only ``plot_height`` changes — this is the
    height-sensitivity established by measurement (plot height governs, not
    card width).
    """
    assert (
        _stack_legend_should_yield("zero", "vertical", 20, 20, 200.0, True, 2) is True
    )
    assert (
        _stack_legend_should_yield("zero", "vertical", 20, 20, 300.0, True, 2) is False
    )


def test_yield_classifier_divides_rows_by_legend_columns() -> None:
    """18 entries need 9 rows at 2 columns (fits 200px) but 18 rows at 1 column
    (does not) — the row count must come from the legend that actually
    renders, not one row per entry.
    """
    assert (
        _stack_legend_should_yield("zero", "vertical", 18, None, 200.0, True, 2)
        is False
    )
    assert (
        _stack_legend_should_yield("zero", "vertical", 18, None, 200.0, True, 1) is True
    )


def test_yield_classifier_ignores_grouped_bars() -> None:
    assert (
        _stack_legend_should_yield("none", "vertical", 25, 20, 50.0, True, 2) is False
    )


def test_yield_classifier_ignores_horizontal_orientation() -> None:
    assert (
        _stack_legend_should_yield("zero", "horizontal", 25, 20, 50.0, True, 2) is False
    )


def test_yield_classifier_clamps_entries_to_symbol_limit() -> None:
    """The renderer never draws more legend rows than symbol_limit lets
    through (excess entries collapse into a single "+N more" swatch) — the
    row math must clamp distinct_series to symbol_limit before dividing by
    columns, not charge every distinct series a row. 60 series clamped to
    symbol_limit=20 at 4 columns needs 5 rows (127px, fits 200px); the same
    60 series left unclamped needs 15 rows (287px, does not).
    """
    assert (
        _stack_legend_should_yield("zero", "vertical", 60, 20, 200.0, True, 4) is False
    )
    assert (
        _stack_legend_should_yield("zero", "vertical", 60, None, 200.0, True, 4) is True
    )


def test_yield_classifier_ignores_a_side_legend() -> None:
    """A legend that hasn't moved to a top/compact layout costs width, not
    plot height, and must never be charged against the plot's row budget —
    this is what keeps the classifier from over-firing on wider cards where
    the legend renders to the side.
    """
    assert (
        _stack_legend_should_yield("zero", "vertical", 25, 20, 50.0, False, 2) is False
    )


def test_twenty_five_series_at_narrow_card_draws_marks_with_real_height() -> None:
    """End-to-end: the actual render must not hit ERR-CHART-PAINTED-NO-MARKS."""
    data = _stacked_data(25)
    resolved = _resolved(25, _NARROW_WIDTH)
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = data

    svg, _height = render_chart_item(
        resolved,
        executor,
        variables={},
        available_width=_NARROW_WIDTH,
        available_height=200,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )

    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg
    assert not all_marks_degenerate(svg)


def test_wide_measure_stacked_bar_at_narrow_card_draws_marks_with_real_height() -> None:
    """A wide bar (``y: [m01..m25]``, no ``color:``) folds its measures into a
    color channel at render and gets a full legend — one entry per measure —
    via ``fold_wide_measures``. The classifier must charge that legend the
    same way it would a ``color:``-driven one: reading ``normalized.color``
    alone (``None`` here) and falling back to 0 entries reproduces the
    original blank-chart bug on this first-class authoring form.
    """
    measure_count = 25
    data = _wide_stacked_data(measure_count)
    resolved = _resolved_wide(measure_count, _NARROW_WIDTH)
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = data

    svg, _height = render_chart_item(
        resolved,
        executor,
        variables={},
        available_width=_NARROW_WIDTH,
        available_height=200,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )

    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg
    assert not all_marks_degenerate(svg)


def test_fourteen_series_at_floor_width_draws_marks_with_real_height() -> None:
    """End-to-end regression at the floor-width tier: with the old width-only
    calibration this was an unreachable band — the classifier never yielded
    below 15 series at any width, so this chart rendered
    ERR-CHART-PAINTED-NO-MARKS with the legend still (uselessly) attached.
    """
    data = _stacked_data(14)
    resolved = _resolved(14, _FLOOR_WIDTH)
    assert resolved.legend.visible is False
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = data

    svg, _height = render_chart_item(
        resolved,
        executor,
        variables={},
        available_width=_FLOOR_WIDTH,
        available_height=150,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )

    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg
    assert not all_marks_degenerate(svg)


def test_zero_compact_columns_fails_loudly_instead_of_dividing_by_zero() -> None:
    """The classifier divides legend entries by compact_columns
    (``ceil(entries / legend_columns)``); an authored ``compact_columns: 0``
    must be rejected at compile time, not reach that division as a
    resolve-time ZeroDivisionError.
    """
    with pytest.raises(ValidationError, match="compact_columns"):
        _resolved(12, _NARROW_WIDTH, legend=LegendStylePatch(compact_columns=0))
