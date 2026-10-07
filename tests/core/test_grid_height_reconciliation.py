"""Grid height budget and grid height assignment must agree.

`_measure_grid_layout_height` decides how tall a grid band needs to be;
`_calculate_grid_dimensions` decides how tall each row actually is. Both answer
"how tall is this grid", so a grid can only lay out correctly when they compute
it the same way — items 16 and 33 of the render-layer gap register.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.sizing import (
    _calculate_grid_dimensions,
    _measure_grid_layout_height,
)

_GAP = 16.0
_WIDTH = 1200.0


def _item(
    row: int, col: int, height: float, col_span: int = 1, row_span: int = 1
) -> LayoutItem:
    return LayoutItem(
        type="chart",
        chart=KpiChart(id=f"k{row}_{col}", type="kpi", value="value"),
        row=row,
        col=col,
        col_span=col_span,
        row_span=row_span,
        layout_height=f"{height}px",
    )


def _measure(layout: Layout) -> float:
    return _measure_grid_layout_height(
        layout.items,
        layout.columns,
        card_gap=0.0,
        gap=_GAP,
        available_width=_WIDTH,
        variable_values=None,
        resolved_style=resolve_style(get_theme_style()),
    )


def _assign(layout: Layout, available_height: float) -> None:
    _calculate_grid_dimensions(
        layout.items,
        _WIDTH,
        available_height,
        columns=layout.columns or 12,
        card_gap=0.0,
        gap=_GAP,
        resolved_style=resolve_style(get_theme_style()),
    )


def test_budget_uses_per_row_max_not_mean_over_items() -> None:
    """Item 16: one tall item on row 0, three short ones on row 1.

    Assignment lays each row out at that row's max height (400 + 100); the
    budget must ask for the same, not `rows × mean-item-height` (which
    averages the three short items against the tall one and under-asks).
    """
    layout = Layout(
        type="grid",
        columns=12,
        items=[
            _item(row=0, col=0, height=400.0, col_span=12),
            _item(row=1, col=0, height=100.0, col_span=4),
            _item(row=1, col=4, height=100.0, col_span=4),
            _item(row=1, col=8, height=100.0, col_span=4),
        ],
    )
    measured = _measure(layout)
    assert measured == 400.0 + 100.0 + _GAP

    _assign(layout, measured)
    assert layout.items[0].height == 400.0
    assert layout.items[1].height == 100.0


def test_sparse_grid_is_not_billed_for_unoccupied_rows() -> None:
    """Item 33: items at row 0 and row 40, nothing between.

    An unoccupied row index draws nothing, so it must cost nothing — billing
    each one `charts.default_chart_height` is what turned a two-tile board into
    a 286-megapixel render.
    """
    layout = Layout(
        type="grid",
        columns=12,
        items=[
            _item(row=0, col=0, height=200.0, col_span=12),
            _item(row=40, col=0, height=200.0, col_span=12),
        ],
    )
    measured = _measure(layout)
    assert measured == 200.0 + 200.0 + _GAP

    _assign(layout, measured)
    assert layout.items[0].y == 0.0
    assert layout.items[1].y == 200.0 + _GAP


def test_a_spanning_item_bills_its_height_once_across_the_rows_it_covers() -> None:
    """A `row_span: 2` item is one tile, not two — its height is divided over
    the rows it occupies, so the budget asks for the height it actually draws.

    Without the division the budget charges the full height to every spanned
    row and the grid renders with a band of dead whitespace below it — the
    item-16 defect, in the direction the fix was meant to close.
    """
    layout = Layout(
        type="grid",
        columns=12,
        items=[_item(row=0, col=0, height=400.0, col_span=12, row_span=2)],
    )
    measured = _measure(layout)
    assert measured == 400.0 + _GAP

    _assign(layout, measured)
    assert layout.items[0].height == measured


def test_a_spanning_item_is_billed_against_the_rows_it_shares() -> None:
    """A tall spanning item beside short ones: each spanned row is billed the
    max of (its own items, the spanner's per-row share), never their sum."""
    layout = Layout(
        type="grid",
        columns=12,
        items=[
            _item(row=0, col=0, height=300.0, col_span=6, row_span=3),
            _item(row=0, col=6, height=100.0, col_span=6),
            _item(row=2, col=6, height=100.0, col_span=6),
        ],
    )
    measured = _measure(layout)
    assert measured == 100.0 + 100.0 + 100.0 + 2 * _GAP

    _assign(layout, measured)
    assert layout.items[0].height == measured
    assert layout.items[1].height == 100.0
