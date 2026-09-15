"""What the bar labeling default does, and every case it steps aside for.

A bar names its series directly when it can: a vertical stack labels the last
column's segment midpoints, a horizontal stack takes the top-row rail. It falls
back to a legend wherever the rail could not name every series — see
``_bar_endpoint_labels_for_stack``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.authored._support_table import (
    ChartSupportTable,
    ChartSupportTablePerSeries,
)
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
    EndpointLabelsConfigPatch,
    LegendStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_Row = dict[str, str | int]

# Well clear of the tiny tier, whose own rule swaps endpoint labels for a top
# legend regardless of orientation.
_WIDTH = 900.0


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


# Temporal x → vertical orientation; discrete x → horizontal.
_TEMPORAL_DATA: list[_Row] = [
    {"month": "2024-01-01", "revenue": 10, "stage": "New"},
    {"month": "2024-02-01", "revenue": 8, "stage": "New"},
    {"month": "2024-01-01", "revenue": 5, "stage": "Won"},
    {"month": "2024-02-01", "revenue": 3, "stage": "Won"},
]

_DISCRETE_DATA: list[_Row] = [
    {"region": "East", "revenue": 10, "stage": "New"},
    {"region": "West", "revenue": 8, "stage": "New"},
    {"region": "East", "revenue": 5, "stage": "Won"},
    {"region": "West", "revenue": 3, "stage": "Won"},
]

_DISCRETE_DATA_SERIES_ABSENT_AT_ANCHOR: list[_Row] = [
    {"region": "East", "revenue": 10, "stage": "New"},
    {"region": "West", "revenue": 8, "stage": "New"},
    {"region": "West", "revenue": 3, "stage": "Won"},
]

_NEGATIVE_DATA: list[_Row] = [
    {"month": "2024-01-01", "revenue": 10, "stage": "New"},
    {"month": "2024-02-01", "revenue": 8, "stage": "New"},
    {"month": "2024-01-01", "revenue": -5, "stage": "Won"},
    {"month": "2024-02-01", "revenue": 3, "stage": "Won"},
]


def _resolved(
    x: str,
    data: list[_Row],
    orientation: str | None = None,
    stack: str = "zero",
    endpoint_labels: EndpointLabelsConfigPatch | None = None,
) -> ResolvedBarChart:
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x=x,
        y="revenue",
        color="stage",
        style=BarChartStylePatch(
            stack=stack,
            orientation=orientation,
            # Omitted rather than passed as None: the patch model rejects an
            # explicit None here.
            **({"endpoint_labels": endpoint_labels} if endpoint_labels else {}),
        ),
    )
    resolved = resolve(
        chart, data, resolve_chart_style_context(get_theme_style()), width=_WIDTH
    )
    assert isinstance(resolved, ResolvedBarChart)
    return resolved


def test_vertical_stacked_bar_labels_its_series_directly() -> None:
    resolved = _resolved("month", _TEMPORAL_DATA)

    assert resolved.orientation == "vertical"
    assert resolved.style.endpoint_labels.visible is True


def test_vertical_stacked_bar_drops_the_legend_for_endpoint_labels() -> None:
    """Direct labeling replaces the color legend, as it does for line/area."""
    resolved = _resolved("month", _TEMPORAL_DATA)

    assert resolved.legend.visible is False


def test_horizontal_bar_gets_the_same_default() -> None:
    """Horizontal bars route to their own label rail, not the vertical cascade.

    The rail places one label per series above the top categorical row. It
    reads cleanly at low series counts and overprints once the labels are
    wider than their segments — an open question, not settled behavior, so
    this pins only that the default reaches both orientations alike.
    """
    resolved = _resolved("region", _DISCRETE_DATA)

    assert resolved.orientation == "horizontal"
    assert resolved.style.endpoint_labels.visible is True


def test_grouped_bar_keeps_its_legend() -> None:
    """Grouped bars label poorly: every series' last bar ends at a similar
    height, so the cascade compresses all the labels into one another. A
    legend is the honest treatment.
    """
    resolved = _resolved("month", _TEMPORAL_DATA, stack="none")

    assert resolved.stack == "none"
    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is not False


def test_grouped_bar_labels_directly_when_the_author_asks_for_it() -> None:
    """Every disqualifier steers the *default*. An author who writes
    ``endpoint_labels.visible: true`` on a grouped bar gets the bar-top labels
    that shape has always rendered, and the legend steps aside for them.
    """
    resolved = _resolved(
        "month",
        _TEMPORAL_DATA,
        stack="none",
        endpoint_labels=EndpointLabelsConfigPatch(visible=True),
    )

    assert resolved.stack == "none"
    assert resolved.style.endpoint_labels.visible is True
    assert resolved.legend.visible is False


def test_author_opt_in_reaches_the_render_layer_raise() -> None:
    """A negative measure is disqualified because render refuses to place a
    cumulative midpoint across zero. An explicit opt-in resolves ``True``
    anyway, so the author meets that refusal by name rather than a chart that
    quietly declines to label.
    """
    resolved = _resolved(
        "month",
        _NEGATIVE_DATA,
        endpoint_labels=EndpointLabelsConfigPatch(visible=True),
    )

    assert resolved.style.endpoint_labels.visible is True


def test_negative_data_bar_keeps_its_legend() -> None:
    """A stacked bar with a negative measure would hit the render-layer
    negative-value raise if endpoint labels defaulted on — the default must
    steer around that failure, not into it.
    """
    resolved = _resolved("month", _NEGATIVE_DATA)

    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is not False


def test_horizontal_series_absent_at_anchor_keeps_its_legend() -> None:
    """The horizontal rail has no dodge resolver for a series missing at its
    anchor row (``_every_series_reaches_the_anchor_row``) — its label would
    land on the zero-width seam and overprint the neighbor. The theme's bar
    legend override is what keeps this chart from carrying neither rail nor
    legend.
    """
    resolved = _resolved("region", _DISCRETE_DATA_SERIES_ABSENT_AT_ANCHOR)

    assert resolved.orientation == "horizontal"
    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is not False


def test_grouped_bar_legend_sits_on_top_untitled() -> None:
    """Top horizontal, matching the left-to-right reading order of the bars
    themselves. The title auto-hides at top+horizontal, and the entry count is
    left to the renderer rather than clamped to the compact wrap.
    """
    resolved = _resolved("month", _TEMPORAL_DATA, stack="none")

    assert resolved.legend.position == "top"
    assert resolved.legend.direction == "horizontal"
    assert resolved.legend.title.visible is False
    assert resolved.legend.columns == 0


def test_author_can_still_turn_the_legend_off() -> None:
    """Top placement decides *where* a legend goes, not *whether* there is one.

    Grouped bars get a legend by default because they cannot endpoint-label.
    An author who says this chart carries none outranks that.
    """
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        color="stage",
        style=BarChartStylePatch(
            stack="none",
            legend=LegendStylePatch.model_validate({"visible": False}),
        ),
    )
    resolved = resolve(
        chart,
        _TEMPORAL_DATA,
        resolve_chart_style_context(get_theme_style()),
        width=_WIDTH,
    )
    assert isinstance(resolved, ResolvedBarChart)

    assert resolved.legend.visible is False


_ONE_POINT_PER_SERIES: list[_Row] = [
    {"month": "2024-01-01", "revenue": 10, "stage": "New"},
    {"month": "2024-02-01", "revenue": 8, "stage": "Won"},
]

_SERIES_MISSING_FROM_LAST_COLUMN: list[_Row] = [
    {"month": "2024-01-01", "revenue": 10, "stage": "New"},
    {"month": "2024-01-01", "revenue": 5, "stage": "Won"},
    {"month": "2024-02-01", "revenue": 8, "stage": "New"},
]


def test_stacked_bar_keeps_its_rail_when_a_series_misses_the_last_column() -> None:
    """A gap in the anchor column is not a reason to change naming mechanism.

    The absent series anchors on its zero-height stack seam, so it stays named
    and the reader still counts as many names as colors. Disqualifying here
    would switch a chart between rail and legend on a data gap alone — and
    series that start late are ordinary in a stack.
    """
    resolved = _resolved("month", _SERIES_MISSING_FROM_LAST_COLUMN)

    assert resolved.style.endpoint_labels.visible is True


_SINGLE_SERIES_TWO_COLUMNS: list[_Row] = [
    {"month": "2024-01-01", "revenue": 10, "stage": "New"},
    {"month": "2024-02-01", "revenue": 8, "stage": "New"},
]

_ALL_NULL_MEASURE: list[_Row] = [
    {"month": "2024-01-01", "revenue": None, "stage": "New"},
    {"month": "2024-01-01", "revenue": None, "stage": "Won"},
    {"month": "2024-02-01", "revenue": None, "stage": "New"},
]


def test_single_series_bar_falls_back_to_a_legend() -> None:
    """One series across many columns stacks against nothing.

    Its segment spans the whole bar, so a rail would be one name pinned beside
    one band — the legend it already had, moved. Pinned so an edit to the
    disqualifier can't flip it silently.
    """
    resolved = _resolved("month", _SINGLE_SERIES_TWO_COLUMNS)

    assert resolved.style.endpoint_labels.visible is False


def test_all_null_measure_falls_back_to_a_legend() -> None:
    """No row carries a value, so no column stacks and nothing is drawn.

    The rail anchors on segment geometry; with no segments anywhere it would
    pile every name on the baseline of an empty plot.
    """
    resolved = _resolved("month", _ALL_NULL_MEASURE)

    assert resolved.style.endpoint_labels.visible is False


def test_one_point_per_series_falls_back_to_a_legend() -> None:
    """`color:` equal to `x:` gives every series a single, distinct column.

    Nothing stacks, so there are no segments to name and the rail would
    degenerate into a badly-laid-out legend. This is the shape a
    category-colored bar takes, and it must not sprout a one-entry label.
    """
    resolved = _resolved("month", _ONE_POINT_PER_SERIES)

    assert resolved.style.endpoint_labels.visible is False


_TWO_ROWS: list[_Row] = [
    {"region": "East", "revenue": 10, "stage": "New"},
    {"region": "West", "revenue": 8, "stage": "New"},
    {"region": "West", "revenue": 5, "stage": "Won"},
]


def _horizontal(sort: object = None, stack: str = "zero") -> ResolvedBarChart:
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="region",
        y="revenue",
        color="stage",
        sort=sort,
        style=BarChartStylePatch(stack=stack),
    )
    resolved = resolve(
        chart, _TWO_ROWS, resolve_chart_style_context(get_theme_style()), width=_WIDTH
    )
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.orientation == "horizontal"
    return resolved


def test_sorted_horizontal_stack_still_labels_directly() -> None:
    """A `sort:` moves the anchor row rather than disqualifying the rail.

    The rail anchors on the first row of the *rendered* domain, so it follows
    the sort instead of refusing it — sorting a stacked bar by value is
    ordinary authoring.
    """
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="region",
        y="revenue",
        color="stage",
        sort={"by": "revenue", "order": "desc"},
        style=BarChartStylePatch(stack="zero"),
    )
    resolved = resolve(
        chart,
        _DISCRETE_DATA,
        resolve_chart_style_context(get_theme_style()),
        width=_WIDTH,
    )
    assert isinstance(resolved, ResolvedBarChart)

    assert resolved.orientation == "horizontal"
    assert resolved.style.endpoint_labels.visible is True


def test_sorted_vertical_stack_still_labels_directly() -> None:
    """Same on the vertical pane, which anchors on the last rendered column."""
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        color="stage",
        sort={"by": "revenue", "order": "desc"},
        style=BarChartStylePatch(stack="zero", orientation="vertical"),
    )
    resolved = resolve(
        chart,
        _TEMPORAL_DATA,
        resolve_chart_style_context(get_theme_style()),
        width=_WIDTH,
    )
    assert isinstance(resolved, ResolvedBarChart)

    assert resolved.orientation == "vertical"
    assert resolved.style.endpoint_labels.visible is True


def test_center_stacked_horizontal_keeps_its_legend() -> None:
    """The horizontal rail anchors on the cumulative axis, not center-stack's."""
    resolved = _horizontal(stack="center")

    assert resolved.style.endpoint_labels.visible is False


def test_horizontal_rail_steps_aside_when_a_series_misses_its_anchor_row() -> None:
    """'Won' appears only in the last row; the horizontal rail anchors on the first.

    The vertical rail seats that series on its zero-width seam and lets the
    label cascade push it clear. The horizontal rail has no cascade, so the
    seam label would overprint its neighbor — a legend is the honest
    treatment. Vertical is unaffected: see
    `test_stacked_bar_keeps_its_rail_when_a_series_misses_the_last_column`.
    """
    resolved = _horizontal()

    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is not False


_CROWDED_LONG_NAMES: list[_Row] = [
    {"region": "East", "revenue": 10, "stage": name}
    for name in (
        "extensions_contrib",
        "replication_report",
        "build",
        "platform",
        "warehouse_sync",
        "connector_health",
        "usage_metering",
    )
]


def test_horizontal_rail_falls_back_to_legend_when_labels_would_collide() -> None:
    """Real pixel-width measurement disqualifies a genuinely crowded rail.

    Seven long series names sharing one narrow top row cannot all fit their
    own label without overlapping a neighbor on a narrow chart — the
    default steers back to a legend even though every disqualifier above
    this one (support table, stack shape, multiples, negative values, sort,
    center stack, single-series, anchor row) already passed.
    """
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="region",
        y="revenue",
        color="stage",
        style=BarChartStylePatch(stack="zero", orientation="horizontal"),
    )
    resolved = resolve(
        chart,
        _CROWDED_LONG_NAMES,
        resolve_chart_style_context(get_theme_style()),
        width=480.0,
    )
    assert isinstance(resolved, ResolvedBarChart)

    assert resolved.orientation == "horizontal"
    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is not False


def test_per_series_support_table_already_names_the_series() -> None:
    """A `support_table` with a `per_series:` entry prints one row per series,
    labeled in that series' own ink. The rail would name them twice — and it
    costs the plot both height and the axis side it needs.
    """
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        color="stage",
        support_table=ChartSupportTable(
            entries=[ChartSupportTablePerSeries(per_series="revenue")]
        ),
        style=BarChartStylePatch(stack="zero"),
    )
    resolved = resolve(
        chart,
        _TEMPORAL_DATA,
        resolve_chart_style_context(get_theme_style()),
        width=_WIDTH,
    )
    assert isinstance(resolved, ResolvedBarChart)

    assert resolved.style.endpoint_labels.visible is False


def test_histogram_never_draws_an_endpoint_label_rail() -> None:
    """A histogram bins x and aggregates y to a count.

    It reads the bar family's style slot, so the theme default would switch a
    rail on — but there is no per-row value for it to anchor to, and the
    positions would correspond to no rendered mark.
    """
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="histogram",
        x="revenue",
        y="revenue",
        color="stage",
    )
    resolved = resolve(
        chart,
        _TEMPORAL_DATA,
        resolve_chart_style_context(get_theme_style()),
        width=_WIDTH,
    )

    assert resolved.style.endpoint_labels.visible is False


def test_sort_by_a_non_numeric_column_keeps_its_legend() -> None:
    """Both rails place their labels from the order the sorted axis draws, and
    dbt Charts confirms that order only for a numeric sort column — so the
    default steps aside rather than risk labeling from an order Vega-Lite does
    not draw.
    """
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="region",
        y="revenue",
        color="stage",
        sort={"by": "region", "order": "desc"},
        style=BarChartStylePatch(stack="zero"),
    )
    resolved = resolve(
        chart,
        _DISCRETE_DATA,
        resolve_chart_style_context(get_theme_style()),
        width=_WIDTH,
    )
    assert isinstance(resolved, ResolvedBarChart)

    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is not False


def _resolved_wide_by_dimension(
    data: list[dict[str, object]], measures: list[str], width: float = _WIDTH
) -> ResolvedBarChart:
    chart = BarChart(
        id="wide",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="region",
        y=measures,
        color="segment",
        style=BarChartStylePatch(stack="zero"),
    )
    resolved = resolve(
        chart, data, resolve_chart_style_context(get_theme_style()), width=width
    )
    assert isinstance(resolved, ResolvedBarChart)
    return resolved


_WIDE_BY_SEGMENT = [
    {"region": "North", "segment": "SMB", "won": 30, "lost": 12},
    {"region": "North", "segment": "Ent", "won": 22, "lost": 9},
    {"region": "South", "segment": "SMB", "won": 18, "lost": 15},
    {"region": "South", "segment": "Ent", "won": 25, "lost": 6},
]


def test_horizontal_wide_by_dimension_bar_labels_its_composites() -> None:
    """The rail gates read the composites a wide + `color:` bar paints —
    two segments x two measures stack four segments per row, every one of
    them present on the anchor row — so the rail fires."""
    resolved = _resolved_wide_by_dimension(_WIDE_BY_SEGMENT, ["won", "lost"])

    assert resolved.orientation == "horizontal"
    assert resolved.style.endpoint_labels.visible is True


def test_horizontal_wide_by_dimension_bar_with_one_value_still_stacks() -> None:
    """One dimension value x two measures still stacks two segments per row;
    counting the dimension alone would read one series and hand the rail
    back to a legend."""
    data = [row for row in _WIDE_BY_SEGMENT if row["segment"] == "SMB"]
    resolved = _resolved_wide_by_dimension(data, ["won", "lost"])

    assert resolved.style.endpoint_labels.visible is True


def test_horizontal_wide_by_dimension_bar_missing_a_measure_at_the_anchor() -> None:
    """A measure null on every row of one segment leaves that composite
    unseatable — and, at this width, its labels colliding too, so either
    gate hands the rail back to a legend (the 1600px case below isolates the
    anchor gate)."""
    data = [
        dict(row, lost=None) if row["segment"] == "Ent" else row
        for row in _WIDE_BY_SEGMENT
    ]
    resolved = _resolved_wide_by_dimension(data, ["won", "lost"])

    assert resolved.style.endpoint_labels.visible is False


def test_horizontal_wide_by_dimension_rail_collides_on_composite_names() -> None:
    """The collision measurement reads the composite labels the rail paints,
    which run longer than either the measure or the segment name alone:
    three segments x two measures at 900px overprint, so the rail yields."""
    data = [
        {"region": region, "segment": segment, "won": 10 + i, "lost": 4 + i}
        for i, region in enumerate(("North", "South"))
        for segment in ("Enterprise accounts", "Mid-market accounts", "Self-serve")
    ]
    resolved = _resolved_wide_by_dimension(data, ["won", "lost"])

    assert resolved.style.endpoint_labels.visible is False


def test_horizontal_wide_by_dimension_anchor_row_missing_one_composite() -> None:
    """Short names on a wide plot never collide, so the anchor gate alone
    decides: a measure null on the anchor row (the first region) for one
    segment is a composite the rail cannot seat, and it yields."""
    complete = _resolved_wide_by_dimension(_WIDE_BY_SEGMENT, ["won", "lost"], 1600)
    assert complete.style.endpoint_labels.visible is True

    data = [
        dict(row, lost=None)
        if row["region"] == "North" and row["segment"] == "Ent"
        else row
        for row in _WIDE_BY_SEGMENT
    ]
    resolved = _resolved_wide_by_dimension(data, ["won", "lost"], 1600)

    assert resolved.style.endpoint_labels.visible is False
