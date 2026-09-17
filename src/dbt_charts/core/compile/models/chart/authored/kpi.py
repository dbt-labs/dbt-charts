"""Authored KPI chart, semantic tone, and support-line block."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dbt_charts.core.compile.models.markers import Channel, DisplayText, Format
from dbt_charts.core.compile.models.primitives import FormatConfig, ToneLiteral
from dbt_charts.core.compile.models.schema_names import FormatAlias
from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

from ._base import _BaseChartFields, _ConditionalFormattingField


class KpiSupportConfig(BaseModel):
    """Support-line block authored alongside a KPI's main value."""

    model_config = ConfigDict(extra="forbid")

    value: str | None = Field(
        default=None,
        description="Column reference (string column name) for the support number/text.",
    )
    label: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description="Trailing explainer text rendered beside the support value.",
    )
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="How the value is written: a D3 spec, a preset name, or a format block. A date value defaults to date_short when unformatted.",
    )
    glyph: str | None = Field(
        default=None,
        description="Text shown before the value (e.g. '▲', '▼', '●').",
    )
    tone: ToneLiteral | None = Field(
        default=None,
        description="Semantic styling for the support value/glyph.",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_color_shortcuts(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "value_color" in data:
                raise ValueError(
                    "value_color no longer accepted on support. "
                    "Use support.tone instead."
                )
            if "glyph_color" in data:
                raise ValueError(
                    "glyph_color no longer accepted on support. "
                    "Use support.tone instead."
                )
        return data

    @model_validator(mode="before")
    @classmethod
    def reject_literal_support_value(cls, data: Any) -> Any:
        if isinstance(data, dict) and not isinstance(data.get("value"), bool):
            v = data.get("value")
            if isinstance(v, (int, float)):
                raise ValueError(
                    f"support.value must be a column reference (string column name).\n"
                    f"Got numeric literal: {v}.\n"
                    "Channels are always column references; data values come from the query.\n"
                    "Update to:\n"
                    f'  query: {{ sql: "select revenue, {v} as delta_pct from revenue_q" }}\n'
                    "  value: revenue\n"
                    "  support:\n"
                    "    value: delta_pct"
                )
        return data

    @model_validator(mode="after")
    def _require_non_empty(self) -> KpiSupportConfig:
        if self.value is None and self.label is None and self.glyph is None:
            raise ValueError(
                "Empty `support:` block. Set at least one of `value`, "
                "`label`, or `glyph`, or omit the block entirely."
            )
        return self


class KpiChart(_BaseChartFields, _ConditionalFormattingField):
    """Authored patch for KPI (key performance indicator) charts.

    KPI charts do not use title: (use label: instead) and require value:.
    """

    model_config = ConfigDict(extra="forbid")

    type: Annotated[Literal["kpi"], Field(description="Selects the chart family.")]
    value: Annotated[
        str,
        Channel(),
        Field(
            description="Column reference (string column name) for the headline number/text."
        ),
    ]
    # None = no label alongside the headline value.
    label: Annotated[
        str | None,
        DisplayText(),
        Field(
            default=None,
            description="Caption naming what the headline value measures; `variant` decides where it sits.",
        ),
    ]
    variant: Annotated[
        Literal["stacked", "inline", "compact"],
        Field(
            default="stacked",
            description=(
                "Layout variant. 'stacked' (default) shows value, label, and support "
                "on three vertical lines. 'inline' lays value, label, and support "
                "out on a single baseline-aligned row. 'compact' is 2-column: big "
                "value on the left, up to two stacked lines on the right with the "
                "bottom line sharing baseline with the value; a lone `support` "
                "block splits across the two right-column lines."
            ),
        ),
    ]
    support: Annotated[
        KpiSupportConfig | None,
        Field(
            default=None,
            description="Secondary block, a delta, comparison, or note; `variant` decides where it sits.",
        ),
    ]
    style: Annotated[
        KpiChartStylePatch | None,
        Field(default=None, description="Appearance overrides for this chart alone."),
    ]
    background: Annotated[
        str | dict[str, Any] | None,
        Field(
            default=None,
            description=(
                "Gradient background channel, {column, scale} shape. "
                "Paints the card background by the value's position in the scale."
            ),
        ),
    ]

    @field_validator("value", mode="before")
    @classmethod
    def _reject_literal_value(cls, v: Any) -> Any:
        if not isinstance(v, bool) and isinstance(v, (int, float)):
            raise ValueError(
                "value at chart root must be a column reference (string column name).\n"
                f"Got numeric literal: {v}.\n"
                "Channels are always column references; data values come from the query.\n"
                "Update to:\n"
                f"  query:\n    rows:\n      - count: {v}\n"
                "  value: count\n"
                "or write a SQL query:\n"
                f'  query: "select {v} as count"\n'
                "  value: count"
            )
        return v

    @model_validator(mode="before")
    @classmethod
    def _reject_kpi_title_and_subtitle(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "title" in data:
                raise ValueError(
                    "`title:` is not used on `type: kpi`. Use `label:` instead."
                )
            if "subtitle" in data:
                from dbt_charts.core.compile.normalize.charts import (  # noqa: PLC0415
                    kpi_subtitle_error,
                )

                raise ValueError(kpi_subtitle_error(data.get("id")))
        return data

    @model_validator(mode="before")
    @classmethod
    def _reject_moved_fields_at_chart_root(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "glyph" in data:
                raise ValueError(
                    "'glyph:' has moved from the KPI chart root into the style namespace.\n"
                    "Use style.glyph.character instead:\n\n"
                    "  style:\n"
                    "    glyph:\n"
                    "      character: '▲'\n"
                )
            if "tone" in data:
                raise ValueError(
                    "'tone:' is not a KPI chart root field.\n"
                    "Tone lives on the block it paints: the headline value stays "
                    "neutral, and only the support row carries semantic color.\n"
                    "Use support.tone instead:\n\n"
                    "  support:\n"
                    "    value: <delta_column>\n"
                    "    tone: positive\n"
                )
            for key in ("format", "formatter"):
                if key in data:
                    raise ValueError(
                        f"'{key}:' is not a KPI chart root field. Use style.value.format "
                        "instead:\n\n"
                        "  style:\n"
                        "    value:\n"
                        f"      format: {data[key]!r}\n"
                    )
        return data
