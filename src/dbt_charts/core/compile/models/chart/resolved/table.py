"""Resolved table chart model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.models.chart.authored import TableSpanner
from dbt_charts.core.compile.models.style.authored import TableColumnDefaultsConfig
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedTableColumnConfig,
    ResolvedTableStyle,
)

from ._base import _SharedResolvedChartFields


class ResolvedTableChart(_SharedResolvedChartFields):
    """Render-ready table chart."""

    chart_type: Literal["table"] = Field(
        description="Discriminator key; always 'table'.",
    )
    rows: list[str] | None = Field(
        default=None,
        description="Pivot row dimension fields.",
    )
    pivot_columns: list[str] | None = Field(
        default=None,
        description="Pivot column dimension fields.",
    )
    values: list[str] | None = Field(
        default=None,
        description="Pivot measure fields.",
    )
    spanners: list[TableSpanner] | None = Field(
        default=None,
        description="Authored column spanners.",
    )
    min_height: float | None = Field(default=None, description="Minimum height.")
    max_height: float | None = Field(default=None, description="Maximum height.")
    # Promoted from ChartStylePatch so the renderer reads from a typed field,
    # not from source_chart.style (which no longer exists on Resolved*Chart).
    columns: dict[str, ResolvedTableColumnConfig] | None = Field(
        default=None,
        description=(
            "Final per-column display config (width, format, label, …), keyed by "
            "column name. Complete for every column resolve can see: defaults, "
            "explicit overrides, and runtime FK links are already merged. Excludes "
            "pivot measure columns (see `column_defaults`) — their real key space "
            "only exists after render's pivot transform runs. None means no "
            "column-level config or runtime link facts apply — render falls back "
            "to plain query columns."
        ),
    )
    header_overflow: Literal["clip", "truncate", "wrap-two", "wrap"] | None = Field(
        default=None,
        description="How column headers overflow their cell.",
    )
    column_defaults: TableColumnDefaultsConfig | None = Field(
        default=None,
        description=(
            "Table-level column defaults, kept only for render's two render-native "
            "column key spaces — pivot leaf/measure columns and transpose's "
            "__metric__/__value__ pair — neither of which exists until a "
            "render-time data transform runs. Every other column (explicit or "
            "query-inferred) already has defaults merged into `columns`."
        ),
    )
    style: ResolvedTableStyle = Field(
        description="Table family style slice.",
    )
