"""Theme-stage style classes: page chrome (inputs, footer, timestamp)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dbt_charts.core.compile.models.markers import (
    Color,
    Inherit,
    Url,
)
from dbt_charts.core.compile.models.primitives import (
    CornerStyle,
    FontStyle,
    SpacingValues,
)
from dbt_charts.core.text.format_d3 import portable_strftime


class InputWidths(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: float = Field(description="Default width for text inputs in pixels.")
    number: float = Field(description="Default width for number inputs in pixels.")
    range: float = Field(description="Default width for range inputs in pixels.")
    slider_value_min: float = Field(
        description="Minimum width for slider value display in pixels."
    )
    checkbox: float = Field(description="Default width for checkbox inputs in pixels.")
    daterange: float = Field(
        description="Default width for daterange chip triggers in pixels."
    )


class RangeDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default_min: float = Field(description="Default minimum value for range inputs.")
    default_max: float = Field(description="Default maximum value for range inputs.")
    default_step: float = Field(description="Default step size for range inputs.")


class InputStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    height: float = Field(description="Input control height in pixels.")
    border: CornerStyle = Field(description="Corner rounding for input controls.")
    focus_color: Annotated[str | None, Inherit(from_path="Style.accent"), Color()] = (
        Field(
            default=None,
            description="Input focus ring color.",
        )
    )
    background: Annotated[str, Color()] = Field(description="Input background color.")
    padding: SpacingValues = Field(description="Input inner padding in pixels.")
    widths: InputWidths = Field(description="Per-input-type default widths.")
    range: RangeDefaults = Field(description="Range input default min/max/step values.")


class FooterRule(BaseModel):
    """Hairline rule above the footer attribution text. None = no rule."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    color: Annotated[str, Color()] = Field(description="Rule stroke color.")
    stroke_width: float = Field(description="Rule stroke width in pixels.")


class FooterStyle(BaseModel):
    """Page footer chrome: visibility, attribution text, font, and rule."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(description="Show the footer attribution line.")
    text: str = Field(description="Attribution text shown in the footer.")
    # Cascade sentinel: None (theme sets link: null) or absent = no link, the
    # brand phrase renders unlinked (still set heavier — that is brand styling,
    # not a link affordance). When set, the *first* occurrence of the brand
    # phrase links here; a second occurrence is left plain.
    link: Annotated[str | None, Url()] = Field(
        default=None,
        description="URL the footer brand phrase 'dbt charts' links to; null renders plain text.",
    )
    font: FontStyle = Field(
        default_factory=FontStyle,
        description="Footer text font style (size and color required).",
    )
    y_offset: float = Field(description="Vertical offset from bottom edge in pixels.")
    # None = no hairline rule above the footer text — a legitimate theme choice.
    # Cascade: None in a patch inherits the parent value (merge_onto_base semantics).
    # To disable the rule, set rule: null in the base theme YAML (no per-board clearing needed).
    rule: FooterRule | None = Field(
        default=None,
        description="Hairline rule above footer text; null disables the rule.",
    )

    @model_validator(mode="after")
    def _require_font_size_and_color(self) -> FooterStyle:
        """The footer renderer writes font-size and fill directly into SVG.

        Require both at cascade-resolve time so misconfiguration fails loudly
        rather than emitting empty or literal-None attribute values.
        """
        if self.font.size is None:
            raise ValueError("style.footer.font.size is required (float)")
        if self.font.color is None:
            raise ValueError("style.footer.font.color is required (color string)")
        return self


class TimestampStyle(BaseModel):
    """Authored data-freshness chrome: visibility, placement, strftime format, font, and y-offset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(description="Show the data-freshness line.")
    position: Literal["top", "footer"] = Field(
        description="Timestamp row: top page chrome or footer baseline."
    )
    align: Literal["left", "right"] = Field(
        description="Timestamp horizontal alignment within its row."
    )
    format: str = Field(
        description="strftime format for the data-freshness line, including any "
        "literal label text (e.g. '%H:%M %Z on %-d %b %Y'). The value is always "
        "UTC; a format that prints a clock must disclose the zone (%Z or a literal "
        "'UTC'), else compile rejects it: an unlabeled clock reads as local."
    )
    y: float = Field(description="Y-coordinate for top-positioned timestamp in pixels.")
    font: FontStyle = Field(
        default_factory=FontStyle,
        description="Timestamp font style overrides (size, color, weight, ...).",
    )

    @model_validator(mode="after")
    def _require_font_size_and_color(self) -> TimestampStyle:
        """The timestamp renderer writes font-size and fill directly into SVG.

        Require both at cascade-resolve time so misconfiguration fails loudly
        rather than emitting empty or literal-None attribute values.
        """
        if self.font.size is None:
            raise ValueError("style.timestamp.font.size is required (float)")
        if self.font.color is None:
            raise ValueError("style.timestamp.font.color is required (color string)")
        return self

    @model_validator(mode="after")
    def _clock_format_discloses_zone(self) -> TimestampStyle:
        """The stamped value is always UTC. A custom format that prints a clock
        but never shows the zone reads as local time, so reject it. The clock is
        detected behaviorally (the output shifts when the instant moves an hour),
        which catches %T/%R/%X/%-H that a directive scan would miss.
        """
        probe = datetime(2001, 1, 1, 9, 30, tzinfo=timezone.utc)
        shown = portable_strftime(probe, self.format)
        prints_clock = shown != portable_strftime(
            probe + timedelta(hours=1), self.format
        )
        discloses_zone = (
            "UTC" in shown.upper() or "GMT" in shown.upper() or "+0000" in shown
        )
        if prints_clock and not discloses_zone:
            raise ValueError(
                "style.timestamp.format prints a clock time but never shows its "
                "zone; the value is UTC, so add %Z (or a literal 'UTC') — e.g. "
                "'%H:%M %Z'"
            )
        return self
