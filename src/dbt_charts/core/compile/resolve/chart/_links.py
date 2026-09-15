"""Auto-link eligibility checks for resolved charts."""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.normalized import (
    Chart,
    TableChart,
)
from dbt_charts.core.compile.models.chart.normalized._base import (
    _BaseChartFields,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartRows

__all__ = [
    "auto_link_excludes_x",
    "should_fetch_table_fk_links",
    "should_synthesize_auto_link",
]


def auto_link_excludes_x(normalized: Chart, data: ChartRows) -> bool:
    """Whether x is a measure rather than an automatic-link identity.

    Not gated on bar orientation: a bar's x is always the category/time
    channel, y is always the measure, regardless of style.orientation.
    """
    x_field = getattr(normalized, "x", None)
    return (
        x_field is not None
        and bool(data)
        and all(isinstance(row.get(x_field), float) for row in data[:10])
    )


def should_synthesize_auto_link(
    normalized: Chart,
    data: ChartRows,
    auto_link_enabled: bool,
) -> bool:
    """Whether runtime orchestration should gather a root-link candidate."""
    if (
        not auto_link_enabled
        or not isinstance(normalized, _BaseChartFields)
        or normalized.query is None
        or normalized.link is not None
    ):
        return False
    if isinstance(normalized, TableChart):
        return True
    x_field = getattr(normalized, "x", None)
    color_field = getattr(normalized, "color", None)
    return bool(color_field) or bool(
        x_field and not auto_link_excludes_x(normalized, data)
    )


def should_fetch_table_fk_links(normalized: Chart, auto_link_enabled: bool) -> bool:
    """Whether runtime orchestration should gather table FK-link candidates."""
    return (
        auto_link_enabled
        and isinstance(normalized, TableChart)
        and normalized.query is not None
    )
