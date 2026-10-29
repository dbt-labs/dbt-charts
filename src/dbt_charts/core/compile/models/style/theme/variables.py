"""Theme-stage style classes: variable controls chrome."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dbt_charts.core.compile.models.markers import (
    InheritSlot,
)
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    FontStyle,
)
from dbt_charts.core.compile.models.style.theme.page import (
    InputStyle,
)
from dbt_charts.core.compile.template.daterange import parse_daterange_preset


class VariablesLabelStyle(BaseModel):
    """Per-label font substyle for variable controls. Cascades from variables.font."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: variables.label.font fills from variables.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.variables.font")] = Field(
        default_factory=FontStyle,
        description="Variable label font style overrides.",
    )


class VariablesValueStyle(BaseModel):
    """Per-value font substyle for variable controls. Cascades from variables.font."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: variables.value.font fills from variables.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.variables.font")] = Field(
        default_factory=FontStyle,
        description="Variable value font style overrides.",
    )


class VariablesPlaceholderStyle(BaseModel):
    """Per-placeholder font substyle for variable controls.

    Placeholder = unselected/hint text in variable inputs, such as an empty text
    input or a select before the user picks a value. Reads lighter than the
    selected-value text so the strip is scannable. Cascades from variables.font
    like value/label.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: variables.placeholder.font fills from variables.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.variables.font")] = Field(
        default_factory=FontStyle,
        description="Variable placeholder-text font style overrides.",
    )


class VariablesStyle(BaseModel):
    """Variable controls chrome styling.

    Note: `input.background` styles the *inputs only*. The variables strip/
    container itself is transparent (no background band) and sits directly on the
    board canvas — see `render/variables_strip.py`. This matters on dark themes:
    an opaque container bg that differs from the canvas (e.g. neon input bg
    #222222 over canvas #161616) renders as a visible band; a transparent
    container has none, on any theme. The old chart_themes system had a separate
    `variables.background` for the container; if a theme-controlled container
    band is ever wanted again, add `container_background: str | None` here.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(description="Show the variables control panel.")
    position: Literal["top", "bottom", "title-inline"] = Field(
        description="Variables strip placement: stacked under the title (top/bottom) "
        "or on one horizontal band with the board title (title-inline)."
    )
    title_inline_band_bottom_pad: float = Field(
        description="Bottom padding in pixels added below the title-inline band."
    )
    gap: float = Field(description="Gap between variable controls in pixels.")
    label_position: str = Field(
        description="Position of labels relative to their input controls (e.g. 'left', 'top')."
    )
    title_inline_title_max_width: float = Field(
        description=(
            "When position is title-inline: max title column width in px. "
            "0 means no cap (title uses remaining width after reserving space for variables)."
        )
    )
    # InheritSlot: variables.font fills from root font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.font")] = Field(
        default_factory=FontStyle,
        description="Variables panel base font style overrides.",
    )
    label: VariablesLabelStyle = Field(description="Variable label typography.")
    value: VariablesValueStyle = Field(description="Variable value typography.")
    placeholder: VariablesPlaceholderStyle = Field(
        description="Style for unselected/hint text in variable inputs."
    )
    container_height: float = Field(
        description="Height of the variables panel container in pixels."
    )
    border: BorderStyle = Field(description="Variables panel border style.")
    control_gap: float = Field(
        description="Gap between label and input within a single control in pixels."
    )
    input: InputStyle = Field(description="Input control style.")
    presets: tuple[str, ...] = Field(
        min_length=1,
        description=(
            "The preset menu every daterange picker offers, in order; a "
            "variable's own `presets:` replaces it. Each is a preset name "
            "(`last_30_days`, `previous_3_quarters`, `this_fiscal_quarter`)."
        ),
    )

    @field_validator("presets")
    @classmethod
    def _presets_are_in_the_grammar(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for name in value:
            parse_daterange_preset(name)
        return value
