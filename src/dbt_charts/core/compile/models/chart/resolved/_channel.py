"""Resolved style channel data type.

This module holds only the frozen dataclass that represents a single
resolved style channel — the OUTPUT of the channel parsing ENGINE in
``dbt_charts.core.compile.resolve.chart.channel``.  Placing the data type here lets the
render layer import the resolved IR without pulling in compile internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from dbt_charts.core.compile.models.chart.authored import ConditionalRule
from dbt_charts.core.compile.models.primitives import ResolvedScaleTarget


@dataclass(frozen=True)
class ResolvedStyleChannel:
    channel: str
    mode: Literal["series", "literal", "gradient", "conditional"]
    data_field: str = ""
    literal_value: Any = None
    scale: ResolvedScaleTarget | None = None
    rules: tuple[ConditionalRule, ...] = ()
    # When mode == "conditional", optional gradient fallback used when no
    # threshold rule matches.  Enables Looker-style "scale with rule override"
    # where threshold rules take priority and scale shows through otherwise.
    fallback_scale: ResolvedScaleTarget | None = None
    # True when the bound field's rows are numeric even though mode stayed
    # "series" -- a bare `color: <field>` authoring never inspects data, so
    # mode alone cannot tell a categorical color from a continuous measure
    # rendered as one. Every cartesian family's bare, undecorated color
    # authoring can hit this case: channel_to_encoding independently
    # re-infers the same fact from data at emit time
    # (infer_vega_type_from_data) to decide the VL encoding type. Set
    # generically for every cartesian family by _flag_quantitative_color
    # (compile/resolve/chart/_channels.py); False for every other mode and
    # for every family that never binds a data-backed color channel.
    quantitative_data: bool = False
    # True when a board category-color scale binds this channel's field. A
    # binding means the field's values are categories, so the emitter types
    # the encoding nominal instead of inferring from data -- year-shaped text
    # like "2020" would otherwise infer ordinal, which paints a sequential
    # ramp and never reads the bound scale.
    categorical: bool = False
