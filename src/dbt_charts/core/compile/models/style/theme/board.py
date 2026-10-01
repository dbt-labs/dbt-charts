"""Theme-stage style classes: frame/board dimensions, title, text, placeholder."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dbt_charts.core.compile.models.markers import (
    Color,
    InheritSlot,
    Merge,
    SkipInheritSlots,
    Strategy,
)
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    FontStyle,
)

OverflowMode = Literal["clip", "truncate", "wrap-two", "wrap"]


def _normalize_overflow_value(value: Any) -> Any:
    """Normalize overflow mode casing/separator: snake_case → kebab-case, lower.

    Membership is enforced by the field's ``OverflowMode``/``Literal`` type
    itself — this only canonicalizes spelling before that check runs.
    """
    if value is None or not isinstance(value, str):
        return value
    return value.strip().lower().replace("_", "-")


VALID_FONT_WEIGHTS: frozenset[str] = frozenset(
    {"normal", "bold", "100", "200", "300", "400", "500", "600", "700", "800", "900"}
)


def font_weight_as_css(w: str | float) -> str:
    """Normalize a FontStyle.weight value to a CSS font-weight string."""
    if isinstance(w, float) and w.is_integer():
        return str(int(w))
    return str(w)


class PaddingStyle(BaseModel):
    """Per-chart padding inset (px). All 4 sides required; theme YAML supplies defaults.

    The board layer adds ``card_padding`` to each side additively so per-chart
    padding composes with the global card layout rather than replacing it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    left: float = Field(description="Left padding in pixels.")
    right: float = Field(description="Right padding in pixels.")
    top: float = Field(description="Top padding in pixels.")
    bottom: float = Field(description="Bottom padding in pixels.")


class ColumnRuleStyle(BaseModel):
    """Vertical rule drawn between prose columns, structured like BorderStyle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    width: float = Field(gt=0, description="Rule line width in pixels.")
    color: Annotated[str, Color()] = Field(
        description="Rule color as a CSS color string."
    )
    style: Literal["solid", "dashed", "dotted"] = Field(
        default="solid", description="Rule line style."
    )


class TextColumnStyle(BaseModel):
    """Author overrides for the column layout of board body text.

    Body text is measured and flowed whether or not any of these are set. A
    prose column is an invisible card sitting on the board's card grid, so
    where columns start and how far apart they are follows from the board,
    not from text settings. These narrow the renderer's choice rather than
    switching it on.

    - ``max_number``: ceiling on the count. The renderer may still choose
      fewer, when the text is too short to fill them.
    - ``max_chars``: the measure, overriding the shipped default. It caps the
      text inside a column; it never moves the column.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_number: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Ceiling on the column count. The renderer may choose fewer when there "
            "is not enough text to fill them. None = no ceiling."
        ),
    )
    rule: ColumnRuleStyle | None = Field(
        default=None,
        description="Vertical rule drawn between columns. None = no rule.",
    )
    max_chars: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Column text width as a character count, overriding the shipped "
            "measure. It caps the text inside a column; a column narrower "
            "than this limits the text first."
        ),
    )


def coerce_gap(v: Any) -> float | None:
    """Coerce gap value from int, float, or CSS px string."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"(\d+(?:\.\d+)?)", v)
        if m:
            return float(m.group(1))
    raise ValueError(f"Cannot parse gap from {v!r}")


class RootFontStyle(FontStyle):
    """Root-level font configuration: FontStyle fields plus a required emoji mode.

    The ``emoji`` field is only meaningful at the root — nested FontStyle instances in the
    cascade (axis label, legend, title, etc.) do not carry it. ``extra='forbid'``
    is inherited from FontStyle so nested fonts reject ``emoji:`` at construction.
    """

    emoji: Literal["monochrome", "system-default", "disabled"] = Field(
        description="Emoji rendering mode for the dashboard font stack."
    )


class FrameStyle(BaseModel):
    """Board-level structural dimensions. Do NOT cascade to child boards."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # None is the default state, not a fallback: no theme supplies a width —
    # authoring one anywhere in the cascade opts the board into an exact
    # width, and unset boards size themselves from max_width and content.
    width: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Exact board width in pixels. Set it, on a board, a template it "
            "extends, or a project's meta.yml, and the board is exactly "
            "this wide; the layout distributes it. Leave it unset and the "
            "board sizes itself to its content, bounded by max_width. "
            "Rendered as an on-screen pixel size everywhere except the dct "
            "HTML page (dct serve, dct render --format html), which scales "
            "the board to its container."
        ),
    )
    max_width: float = Field(
        gt=0,
        description=(
            "Widest a board without an exact width may grow, in pixels. A "
            "board with no width of its own measures its charts' preferred "
            "widths and hugs them up to this bound: a single small chart "
            "stays a small card. Ignored when width is set. Themes supply "
            "the default; a project's meta.yml can lower or raise it."
        ),
    )
    min_height: float = Field(description="Minimum board height in pixels.")
    margin: float = Field(description="Board outer margin in pixels.")
    card_padding: float = Field(
        description="Padding added to each card side in pixels."
    )
    card_gap: float = Field(description="Gap between cards in pixels.")


class TitleWidthOffsetsStyle(BaseModel):
    """Additive level offsets applied to a board/chart title's heading level, by card pixel width.

    Added to the base heading level before indexing into ``style.title.sizes``.
    Tier width breakpoints are constants in ``typography.py``; only the per-tier
    offset lives here so themes own the full type stack without duplicating the
    ``sizes`` ramp.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tiny: int = Field(
        description="Level offset for the tiny tier (cards narrower than ~360px)."
    )
    narrow: int = Field(
        description="Level offset for the narrow tier (cards ~360–559px)."
    )
    medium: int = Field(
        description="Level offset for the medium tier (cards ~560–1099px)."
    )
    wide: int = Field(
        description="Level offset for the wide tier (cards ~1100px and up)."
    )


class TitlePositionStyle(BaseModel):
    """Vega-Lite title positioning pass-throughs grouped as a sub-object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    anchor: str = Field(description="Title anchor position; theme always sets this.")
    # Only set in themes that override angle; None lets Vega-Lite choose.
    angle: float | None = Field(
        default=None,
        description="Title rotation angle in degrees; None lets Vega-Lite choose.",
    )
    # Default theme supplies 10; None means inherit Vega-Lite's built-in default.
    offset: float | None = Field(
        default=None,
        description="Title offset from its anchor in pixels; None uses Vega-Lite's default.",
    )
    # Only set in themes that override baseline; None lets Vega-Lite choose.
    baseline: str | None = Field(
        default=None,
        description="Title text baseline alignment; None uses Vega-Lite's default.",
    )


class TitleSubtitleStyle(BaseModel):
    """VL subtitle font pass-through, grouped for consistency with TitleStyle.font."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # FontStyle is an all-Optional overlay; default_factory creates an empty
    # instance that the cascade fills from theme YAML (same pattern as TitleStyle.font).
    font: Annotated[FontStyle, InheritSlot(from_path="Style.font")] = Field(
        default_factory=FontStyle,
        description="Subtitle font style overrides (color, family, size, weight).",
    )
    # Cascade-managed sentinel: _base.yaml supplies the overflow value for every
    # theme; None here keeps TitleSubtitleStyle() constructable as a cascade
    # placeholder (it is built via default_factory=TitleSubtitleStyle on
    # TitleStyle.subtitle). Render code reads this via resolve_title_overflow(),
    # which already falls back to "wrap-two" when None.
    overflow: OverflowMode | None = Field(
        default=None,
        description="Text overflow mode for the subtitle (clip, truncate, wrap-two, wrap). "
        "None means not set at this cascade level (theme floor is wrap-two).",
    )

    @field_validator("overflow", mode="before")
    @classmethod
    def _normalize_overflow(cls, value: Any) -> Any:
        return _normalize_overflow_value(value)


class TitleStyle(BaseModel):
    """Board and board titles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    font: Annotated[FontStyle, InheritSlot(from_path="Style.font")] = Field(
        default_factory=FontStyle, description="Title font style overrides."
    )
    compact_weight: str | float = Field(
        description="Object-title font weight on tiny cards."
    )
    sizes: Annotated[list[float], Merge(Strategy.OVERRIDE)] = Field(
        description=(
            "Font sizes for the H1–H6 heading ramp, indexed by ``board.level - 1``. "
            "Board/prose titles index this ramp directly (no width input). "
            "Object titles (chart/table/spark) combine ``width_offsets`` with a "
            "fixed object-title anchor to pick a slot."
        )
    )
    # SkipInheritSlots(cascade=True): a no-op at this field's own canonical
    # position (Style.title.width_offsets), but inside an outer slot (e.g.
    # ChartsStyle.title) it gives apply_inherit a container-level link, so a
    # patch that sets some *other* title field and leaves width_offsets
    # entirely unset still copies it wholesale — apply_inherit's leaf writes
    # can't fill a field whose own parent container is None.
    width_offsets: Annotated[TitleWidthOffsetsStyle, SkipInheritSlots(cascade=True)] = (
        Field(
            description=(
                "Additive level offsets by card width (tiny/narrow/medium/wide). "
                "Object titles add the tier offset to a fixed anchor to pick an H "
                "slot from ``sizes``. Board/prose titles are level-only and do not "
                "consult these."
            )
        )
    )
    min_height: float = Field(description="Minimum title row height in pixels.")
    overflow: OverflowMode = Field(
        description="Text overflow mode (clip, truncate, wrap-two, wrap)."
    )
    # SkipInheritSlots(cascade=True): see width_offsets above.
    position: Annotated[TitlePositionStyle, SkipInheritSlots(cascade=True)] = Field(
        description="Vega-Lite title positioning: anchor, angle, offset, baseline."
    )
    # Author/theme override for heading level. "auto" = compute from titled-ancestor count;
    # an integer locks all titles in this scope to that H-level and propagates to descendants.
    # Theme YAML supplies "auto" as the universal default.
    level: int | Literal["auto"] = Field(
        description=(
            "Heading level override for board titles. ``'auto'`` (default) computes the "
            "level semantically as the count of titled ancestors. An integer value "
            "locks all titles in this board and its descendants to that H-level."
        ),
    )
    # VL subtitle font config (color lives inside font, not as a sibling field).
    # SkipInheritSlots(cascade=True): see width_offsets above.
    subtitle: Annotated[TitleSubtitleStyle, SkipInheritSlots(cascade=True)] = Field(
        default_factory=TitleSubtitleStyle,
        description="Subtitle font styles.",
    )

    @field_validator("level", mode="before")
    @classmethod
    def _validate_level(cls, value: Any) -> Any:
        if value == "auto":
            return value
        if isinstance(value, int):
            if value < 1:
                raise ValueError(f"style.title.level must be >= 1, got {value!r}")
            return value
        if isinstance(value, str):
            raise ValueError(
                f"style.title.level must be 'auto' or a positive integer, got {value!r}"
            )
        return value

    @field_validator("overflow", mode="before")
    @classmethod
    def _normalize_overflow(cls, value: Any) -> Any:
        return _normalize_overflow_value(value)

    @model_validator(mode="after")
    def _validate_level_within_ramp(self) -> TitleStyle:
        if isinstance(self.level, int) and self.level > len(self.sizes):
            raise ValueError(
                f"style.title.level={self.level} exceeds the H-ramp "
                f"(sizes has {len(self.sizes)} entries; valid range is 1..{len(self.sizes)})"
            )
        return self


class BoxStyle(BaseModel):
    """Shared boxed-surface group: font + background + border.

    The same primitive grouping a chart card, callout, and tooltip compose.
    Reused for the markdown prose surfaces (code, blockquote) so they style
    like every other box. Required fields — theme YAML supplies all defaults.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    font: FontStyle = Field(
        default_factory=FontStyle,
        description=(
            "Box text font overrides: full FontStyle "
            "(family, color, size, weight, style, decoration, case)."
        ),
    )
    background: Annotated[str, Color()] = Field(description="Box background fill.")
    border: BorderStyle = Field(description="Box border (width, color, radius).")


class TextCodeStyle(BoxStyle):
    """Inline and fenced code spans in markdown prose. Box group; mono font by default."""

    highlight: bool = Field(description="Syntax-highlight fenced markdown code blocks.")
    theme: str = Field(
        description="Pygments style name for fenced markdown code tokens."
    )

    @field_validator("theme")
    @classmethod
    def _validate_pygments_theme(cls, value: str) -> str:
        from pygments.styles import get_style_by_name
        from pygments.util import ClassNotFound

        try:
            get_style_by_name(value)
        except ClassNotFound as exc:
            raise ValueError(f"Invalid Pygments code theme: {value!r}") from exc
        return value


class TextBlockquoteStyle(BoxStyle):
    """Blockquote prose. Box group; border is the left rule."""


class TextBoldStyle(BaseModel):
    """Inline **bold** text runs in markdown prose, distinct from heading weight."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weight: str | float = Field(
        description="CSS font-weight for bold text runs and bold table cells."
    )

    @field_validator("weight")
    @classmethod
    def _validate_weight(cls, value: str | float) -> str | float:
        if font_weight_as_css(value) not in VALID_FONT_WEIGHTS:
            raise ValueError(
                f"text.bold.weight {value!r} is not a valid CSS font-weight; "
                "use a numeric string (e.g. '700') or 'normal'/'bold'."
            )
        return value


class BlockMarginStyle(BaseModel):
    """Top and bottom margin for a prose block role, in line-height units.

    Values are unitless multiples of the current line height (font_size * line_height).
    Adjacent block margins collapse to max (CSS-style) — the visible gap between two
    blocks is max(block_a.margin_bottom, block_b.margin_top), not their sum.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    margin_top: float = Field(
        description="Space above the block, in line-height units (lh × font_size × value)."
    )
    margin_bottom: float = Field(
        description="Space below the block, in line-height units (lh × font_size × value)."
    )


class TextRuleStyle(BaseModel):
    """Horizontal rules in markdown prose: `---` and markdown-table gridlines.

    One slot, because mdsvg paints both from it and always has. Splitting it
    would invent a knob no theme has ever needed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    color: Annotated[str, Color()] = Field(
        description="Color of markdown horizontal rules and markdown-table gridlines."
    )


class TextStyle(BaseModel):
    """Markdown / plain text content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    font: Annotated[FontStyle, InheritSlot(from_path="Style.font")] = Field(
        default_factory=FontStyle, description="Text font style overrides."
    )
    align: Literal["left", "center", "right"] = Field(
        description="Text alignment for the board body-text block."
    )
    paragraph: BlockMarginStyle = Field(
        description="Spacing above/below each paragraph block (in line-height units)."
    )
    heading: BlockMarginStyle = Field(
        description="Spacing above/below heading blocks (H1–H6) in line-height units."
    )
    column: TextColumnStyle = Field(
        default_factory=TextColumnStyle,
        description="Multi-column layout for the board body-text block.",
    )
    code: TextCodeStyle = Field(
        description="Inline + fenced code box styling (font, background, border)."
    )
    blockquote: TextBlockquoteStyle = Field(
        description="Blockquote box styling (font, background, border/left-rule)."
    )
    bold: TextBoldStyle = Field(
        description="Inline bold-run styling (weight), distinct from heading weight.",
    )
    rule: TextRuleStyle = Field(
        description="Markdown horizontal-rule and table-gridline color."
    )


class PlaceholderOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(description="Placeholder overlay text shown on empty charts.")
    background: Annotated[str, Color()] = Field(description="Overlay background color.")
    font: Annotated[FontStyle, InheritSlot(from_path="Style.font")] = Field(
        default_factory=FontStyle, description="Overlay font style overrides."
    )


class PlaceholderStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    opacity: float = Field(description="Opacity of the placeholder overlay (0–1).")
    overlay: PlaceholderOverlay = Field(
        description="Overlay text and background style."
    )
