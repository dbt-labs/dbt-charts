"""Normalized callout (annotation text) chart.

Mirrors authored CalloutChart: it is a minimal static text box with no query,
no link, no filters, no chrome — inherits BaseModel directly, not the shared base.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.primitives import VariableDependencies
from dbt_charts.core.compile.models.style.authored import CalloutChartStylePatch


class CalloutChart(BaseModel):
    """Normalized callout chart.

    Minimal: no query, no link, no filters, no conditional_formatting.
    Mirrors authored CalloutChart which also inherits BaseModel directly.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique identifier within the dashboard.")
    type: Literal["callout"]
    message: str = Field(description="Callout message text (required).")
    title: str | None = Field(
        default=None, description="Optional title shown above the message."
    )
    style: CalloutChartStylePatch | None = Field(
        default=None, description="Chart-local style overrides."
    )
    source_path: str = Field(
        default="",
        description="YAML path for edit-back support.",
    )
    defined_in_other_file: bool = Field(
        default=False,
        description=(
            "Internal: True when a ChartRef pulled this definition in from "
            "another file. Declared here rather than inherited: this model "
            "deliberately does not extend _BaseChartFields."
        ),
    )
    variable_dependencies: VariableDependencies = Field(
        default_factory=frozenset,
        description="Variable names this chart depends on.",
    )
    query_name: None = Field(
        default=None,
        description="Always None — callout has no query. Present so build_resolved_board "
        "can access compiled.query_name directly without isinstance guards.",
    )
    notes: str = Field(
        default="",
        description="Always empty — callout has no notes. Present so generic "
        "chart-iteration code (ChartIdentity.from_normalized) can access "
        "chart.notes without isinstance guards, mirroring the same "
        "structural-parity field on ResolvedCalloutChart.",
    )
    query_is_inline: bool = Field(
        default=False,
        description="True when query is defined inline; False for named reference.",
    )
    rhythm_slot: int = Field(
        default=0,
        description="Always 0 — callout is not single-series eligible.",
    )
    warnings_ignore: list[str] = Field(
        default_factory=list,
        description="Warning codes to suppress for this chart.",
    )
