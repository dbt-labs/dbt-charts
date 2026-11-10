"""Authored callout chart — static message, no chrome, no query."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dbt_charts.core.compile.models.chart.authored._base import IgnoredChartCode
from dbt_charts.core.compile.models.markers import DisplayText
from dbt_charts.core.compile.models.style.authored import CalloutChartStylePatch


class CalloutChart(BaseModel):
    """Static callout/message chart. Minimal: no chrome, no styling, no query."""

    model_config = ConfigDict(extra="forbid")

    type: Annotated[Literal["callout"], Field(description="Selects the chart family.")]
    message: Annotated[
        str, DisplayText(), Field(description="Body text the callout displays.")
    ]
    title: Annotated[
        str | None,
        DisplayText(),
        Field(
            default=None, description="Optional chart title shown above the message."
        ),
    ] = None
    style: Annotated[
        CalloutChartStylePatch | None,
        Field(
            default=None,
            description="Appearance overrides for this chart alone (tone).",
        ),
    ] = None
    warnings_ignore: Annotated[
        list[IgnoredChartCode] | None,
        Field(
            default=None,
            description="Codes of render warnings to suppress for this chart. Query-domain codes such as WARN-REAGGREGATION go in `queries.<name>.ignore` instead.",
        ),
    ] = None

    @model_validator(mode="before")
    @classmethod
    def _reject_tone_at_chart_root(cls, data: Any) -> Any:
        if isinstance(data, dict) and "tone" in data:
            raise ValueError(
                "'tone:' has moved from the callout chart root into the style namespace.\n"
                "Use style.tone instead:\n\n"
                "  style:\n"
                "    tone: negative\n"
            )
        return data
