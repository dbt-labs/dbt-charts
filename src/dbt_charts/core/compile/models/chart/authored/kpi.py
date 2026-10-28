"""Authored KPI chart and its support-line block."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from dbt_charts.core.compile.models.constraints import (
    AtLeastOneOf,
    AtMostOneOf,
    ConstrainedModel,
    Constraint,
    IsSet,
    Requires,
)
from dbt_charts.core.compile.models.markers import Channel, DisplayText
from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

from ._base import _BaseChartFields, _ConditionalFormattingField


class KpiSupportConfig(ConstrainedModel):
    """Support-line block authored alongside a KPI's main value."""

    model_config = ConfigDict(extra="forbid")

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        AtLeastOneOf(
            ("value", "label", "glyph"),
            "Empty `support:` block. Set at least one of `value`, "
            "`label`, or `glyph`, or omit the block entirely.",
        ),
        Requires(
            ("value",),
            "`support.good` needs `support.value`: the direction is read from "
            "the value's sign.",
            when=(IsSet("good"),),
        ),
        AtMostOneOf(
            ("good", "glyph"),
            "`support.good` and `support.glyph` cannot both be set: `good` "
            "picks the glyph from the value's sign. Choose the symbols with "
            "`style.support.glyph.up`, `.down`, and `.neutral`.",
        ),
    )

    value: str | None = Field(
        default=None,
        description="Column reference (string column name) for the support number/text.",
    )
    label: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description="Trailing explainer text rendered beside the support value.",
    )
    glyph: str | None = Field(
        default=None,
        description="Text shown before the value (e.g. '▲', '▼', '●').",
    )
    good: Literal["up", "down"] | None = Field(
        default=None,
        description=(
            "Which direction of `value` is good. The glyph and tone follow the "
            "value's sign: a change in the good direction reads positive, the "
            "opposite reads negative, and zero or null reads neutral."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_color_shortcuts(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "value_color" in data:
                raise ValueError(
                    "value_color no longer accepted on support. "
                    "Use style.support.tone instead."
                )
            if "glyph_color" in data:
                raise ValueError(
                    "glyph_color no longer accepted on support. "
                    "Use style.support.tone instead."
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
            description="Caption naming what the headline value measures; `style.variant` places it.",
        ),
    ]
    # None = no glyph before the headline value.
    glyph: Annotated[
        str | None,
        Field(
            default=None,
            description="Text shown before the headline value (e.g. '▲', '●').",
        ),
    ]
    good: Annotated[
        Literal["up", "down"] | None,
        Field(
            default=None,
            description=(
                "Which direction of `value` is good. The headline glyph and tone "
                "follow the value's sign: a change in the good direction reads "
                "positive, the opposite reads negative, and zero or null reads "
                "neutral."
            ),
        ),
    ]
    support: Annotated[
        KpiSupportConfig | None,
        Field(
            default=None,
            description="Secondary block, a delta, comparison, or note; `style.variant` places it.",
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

    @model_validator(mode="after")
    def _reject_good_beside_static_marks_or_catch_all(self) -> KpiChart:
        if self.good is not None:
            if self.glyph is not None:
                raise ValueError(
                    "`good` and the chart-root `glyph` cannot both be set: `good` "
                    "picks the glyph from the value's sign. Choose the symbols "
                    "with `style.value.glyph.up`, `.down`, and `.neutral`."
                )
            self._reject_default_rule_beside_good(self.value, "good")
        support = self.support
        if support is None or support.good is None:
            return self
        # A runtime patch leaves every style field optional, `support` included.
        support_style = self.style.support if self.style is not None else None
        if support_style is not None and support_style.tone is not None:
            raise ValueError(
                "`support.good` and `style.support.tone` cannot both be set: "
                "`good` picks the tone from the value's sign. Remove one."
            )
        # A column shared with the headline keeps its rules on the headline.
        if support.value is not None and support.value != self.value:
            self._reject_default_rule_beside_good(support.value, "support.good")
        return self

    def _reject_default_rule_beside_good(self, column: str, key: str) -> None:
        if self.conditional_formatting is None:
            return
        entry = self.conditional_formatting.get(column)
        if entry is not None and any(rule.default for rule in entry.when):
            raise ValueError(
                f"conditional_formatting.{column} has a `default: true` rule "
                f"beside `{key}`, whose sign rules cover every nonzero value, "
                "so the default rule would apply only at zero or null. Replace "
                "it with an explicit predicate such as `eq: 0` or `between`."
            )

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
            if "tone" in data:
                raise ValueError(
                    "'tone:' is not a KPI chart root field.\n"
                    "Tone lives on the block it paints: the headline value stays "
                    "neutral unless `good:` colors it by sign, and the support row "
                    "takes a static tone.\n"
                    "Use style.support.tone instead:\n\n"
                    "  style:\n"
                    "    support:\n"
                    "      tone: positive\n"
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
