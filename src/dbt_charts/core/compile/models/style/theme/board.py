"""Theme-stage style classes: frame/board dimensions, title, text, placeholder."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from dbt_charts.core.compile.models.factories import (
    FontAlias,
    FontSlotBase,
    build_patch_model_ext,
    register_patch,
)
from dbt_charts.core.compile.models.markers import (
    AUTO,
    Absent,
    Color,
    FontFamily,
    FontWeightValue,
    InheritSlot,
    SkipInheritSlots,
)
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    DividerStyle,
    FontStyle,
    one_error,
)

OverflowMode = Literal["clip", "truncate", "wrap-two", "wrap"]


FontWeight = Literal[
    "normal", "bold", "100", "200", "300", "400", "500", "600", "700", "800", "900",
    100, 200, 300, 400, 500, 600, 700, 800, 900,
]  # fmt: skip
VALID_FONT_WEIGHTS: frozenset[str] = frozenset(
    w for w in get_args(FontWeight) if isinstance(w, str)
)

# Pygments' built-in styles as of 2.21, the floor in pyproject.toml.
PygmentsTheme = Literal[
    "abap",
    "algol",
    "algol_nu",
    "arduino",
    "autumn",
    "borland",
    "bw",
    "coffee",
    "colorful",
    "default",
    "dracula",
    "emacs",
    "friendly",
    "friendly_grayscale",
    "fruity",
    "github-dark",
    "gruvbox-dark",
    "gruvbox-light",
    "igor",
    "inkpot",
    "lightbulb",
    "lilypond",
    "lovelace",
    "manni",
    "material",
    "monokai",
    "murphy",
    "native",
    "night-owl",
    "nord",
    "nord-darker",
    "one-dark",
    "paraiso-dark",
    "paraiso-light",
    "pastie",
    "perldoc",
    "rainbow_dash",
    "rrt",
    "sas",
    "solarized-dark",
    "solarized-light",
    "staroffice",
    "stata-dark",
    "stata-light",
    "tango",
    "trac",
    "vim",
    "vs",
    "xcode",
    "zenburn",
]


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
      text inside a column and is the width prose and board titles ask their
      board for; it never moves the column.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_number: Annotated[int | None, Absent("no ceiling")] = Field(
        default=None,
        ge=1,
        description=(
            "Ceiling on the column count. The renderer may choose fewer when there "
            "is not enough text to fill them. None = no ceiling."
        ),
    )
    divider: DividerStyle = Field(
        description="Vertical line drawn between columns.",
    )
    max_chars: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Column text width as a character count, overriding the shipped "
            "measure. It caps the text inside a column, and is the width prose "
            "and board titles ask a content-sized board for; a column narrower "
            "than this limits the text first."
        ),
    )


class RootFontStyle(FontStyle):
    """Root-level font configuration: FontStyle fields plus a required emoji mode.

    The ``emoji`` field is only meaningful at the root — nested FontStyle instances in the
    cascade (axis label, legend, title, etc.) do not carry it. ``extra='forbid'``
    is inherited from FontStyle so nested fonts reject ``emoji:`` at construction.

    The root font is the cascade floor every other font inherits from, not a role,
    so it takes no registry name: a bare name or ``extends`` raises.
    """

    accepts_name: ClassVar[bool] = False

    emoji: Literal["monochrome", "system-default", "disabled"] = Field(
        description="Emoji rendering mode for the dashboard font stack."
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_bare_name(
        cls,
        value: Any,  # type-state: explicit_any — raw pre-validation slot value
    ) -> Any:  # type-state: explicit_any — passes accepted input through
        return reject_named_root_font(value)


_NAMED_ROOT_FONT_ERROR = (
    "style.font is the root font and cannot reference a named font; "
    "set style.fonts.body to restyle the base text."
)


def reject_named_root_font(
    value: Any,  # type-state: explicit_any — raw pre-validation slot value
) -> Any:  # type-state: explicit_any — passes accepted input through
    if isinstance(value, str) or (
        isinstance(value, dict) and value.get("extends") is not None
    ):
        raise ValueError(_NAMED_ROOT_FONT_ERROR)
    return value


class _RootFontPatchBase(FontSlotBase):
    accepts_name: ClassVar[bool] = False

    # Authored input: any `extends` key is an attempt to name a font, even null.
    # The compiled model sees `extends: None` on every internal round trip.
    @model_validator(mode="before")
    @classmethod
    def _coerce_bare_name(
        cls,
        value: Any,  # type-state: explicit_any — raw pre-validation slot value
    ) -> Any:  # type-state: explicit_any — passes accepted input through
        if isinstance(value, dict) and "extends" in value:
            raise ValueError(_NAMED_ROOT_FONT_ERROR)
        return reject_named_root_font(value)


register_patch(RootFontStyle)(
    build_patch_model_ext(RootFontStyle, base_cls=_RootFontPatchBase)
)


class FrameStyle(BaseModel):
    """Board-level structural dimensions; cascades to child boards."""

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

    Added to the base heading level before indexing into ``style.fonts.heading.sizes``.
    Tier width breakpoints are constants in ``typography.py``; only the per-tier
    offset lives here so themes own the full type stack without duplicating the
    heading ramp.
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


class ChartTitleFontStyle(FontSlotBase):
    """A chart title's font: a font whose ``size``, ``weight`` and ``family`` also
    take ``auto``, the card width tier's choice."""

    extends: FontAlias | None = Field(
        default=None,
        description=(
            "Name of a ``style.fonts`` entry this slot starts from; the fields "
            "set beside it override that entry."
        ),
    )
    family: Annotated[str | Literal["auto"] | None, FontFamily(keywords=(AUTO,))] = (
        Field(
            default=None,
            description="Font family name, or 'auto' to follow the card width.",
        )
    )
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Text color as a CSS color string."
    )
    size: float | Literal["auto"] | None = Field(
        default=None,
        description="Font size in pixels, or 'auto' to follow the card width.",
    )
    weight: Annotated[
        str | float | Literal["auto"] | None,
        FontWeightValue(keywords=(AUTO,)),
    ] = Field(
        default=None,
        description=(
            "How heavy the type is drawn (e.g., 'bold', 400, 700), or 'auto' "
            "to follow the card width."
        ),
    )
    style: Literal["normal", "italic"] | None = Field(
        default=None, description="Upright or slanted type (normal or italic)."
    )
    decoration: Literal["none", "line-through", "underline"] | None = Field(
        default=None,
        description="Line drawn on the text (underline, line-through, or none).",
    )
    case: (
        Literal["none", "sentence", "title", "upper", "lower", "slug", "camel"] | None
    ) = Field(
        default=None,
        description="Letter-case transform applied at render time.",
    )
    line_height: float | None = Field(
        default=None,
        description="Line height as a unitless multiple of font size.",
    )


class _TitleBase(BaseModel):
    """The title fields every title shares; the font differs by title."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    compact_weight: str | float = Field(
        description="Object-title font weight on tiny cards."
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
                "slot from ``style.fonts.heading.sizes``. Board/prose titles are level-only and do not "
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
    level: Annotated[
        Annotated[int, Field(ge=1)] | Literal["auto"],
        one_error("title.level must be 'auto' or a positive integer, got {value}"),
    ] = Field(
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


class TitleStyle(_TitleBase):
    """Typography shared by every heading: board, prose, chart, table, and spark titles."""

    font: Annotated[FontStyle, InheritSlot(from_path="Style.font")] = Field(
        default_factory=FontStyle, description="Title font style overrides."
    )


class ChartTitleStyle(_TitleBase):
    """A chart's title style, whose font also takes ``auto``."""

    font: Annotated[ChartTitleFontStyle, InheritSlot(from_path="Style.font")] = Field(
        default_factory=ChartTitleFontStyle,
        description="Title font style overrides.",
    )


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
    theme: PygmentsTheme = Field(
        description="Pygments style name for fenced markdown code tokens."
    )


class TextBlockquoteStyle(BoxStyle):
    """Blockquote prose. Box group; border is the left bar."""


class TextBoldStyle(BaseModel):
    """Inline **bold** text runs in markdown prose, distinct from heading weight."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weight: FontWeight = Field(
        description="CSS font-weight for bold text runs and bold table cells."
    )


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
        description="Multi-column layout for the board body-text block.",
    )
    code: TextCodeStyle = Field(
        description="Inline + fenced code box styling (font, background, border)."
    )
    blockquote: TextBlockquoteStyle = Field(
        description="Blockquote box styling (font, background, border/left bar)."
    )
    bold: TextBoldStyle = Field(
        description="Inline bold-run styling (weight), distinct from heading weight.",
    )
    divider: DividerStyle = Field(
        description="Markdown horizontal rules (`---`) and markdown-table gridlines."
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
