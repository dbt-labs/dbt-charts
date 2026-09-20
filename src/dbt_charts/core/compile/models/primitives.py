"""Cross-cutting compile primitives shared across all compile stages.

Contains leaf value types used by both authored-input models (types.py) and
compiled-output models (models/board/compiled.py, models/style/compiled.py), and therefore
cannot live in either without creating a circular import.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from dbt_charts.core.compile.models.factories import (
    _PatchBase,
    build_patch_model_ext,
    register_as_own_patch,
    register_patch,
)
from dbt_charts.core.compile.models.markers import (
    Color,
    FontFamily,
    Inherit,
    Merge,
    Palette,
    Strategy,
)
from dbt_charts.core.compile.models.schema_names import (
    ScalePaletteName,
    StopsPaletteName,
)
from dbt_charts.core.text.format_d3 import Notation

# A frozenset dumps in Python's per-process hash-seed order, so two otherwise-
# identical processes emit different orderings for the same set. Every model
# carrying variable names dumps them sorted for a deterministic wire shape.
VariableDependencies = Annotated[
    frozenset[str], PlainSerializer(sorted, return_type=list[str])
]

# Within-group spacing for grouped bars. Keywords resolve to an overlap fraction
# of bar width at render time; a number is that fraction directly (>0 overlaps,
# 0 touches, <0 gaps). None means "not authored" → renderer 'auto'.
OverlapSpec = Literal["auto", "none", "flush", "partial", "full"] | float | None

# Line/area interpolation curve, shared by theme-stage (LineMarkStyle/
# AreaMarkStyle) and resolved-stage (ResolvedLineMarkStyle/ResolvedAreaMarkStyle)
# mark models — lives here (not in style/theme/ or style/resolved/) because both
# packages import it and either direction would be a circular import. A curated
# subset of Vega-Lite's `interpolate` values: no "step-band" (retired — the
# band-aware plateau is the plain `step` keyword on a categorical x-axis, see
# render/chart/step_band.py), no bundle/open/closed/catmull-rom variants.
Curve = Literal[
    "linear",
    "monotone",
    "natural",
    "basis",
    "cardinal",
    "step",
    "step-before",
    "step-after",
]

# Semantic tone vocabulary shared by KPI value/glyph and table conditional
# glyphs. Lives here (not in the authored kpi model) because
# _conditional_formatting.py needs it too, and importing from kpi.py would
# create an import cycle (kpi -> _base -> _conditional_formatting).
ToneLiteral = Literal["positive", "negative", "warning", "info"]

# HTML rendering tier for board body text (`html_policy` field). Ordered from
# most to least restrictive — declaration order is the canonical ordering, so
# `get_args(HtmlPolicy)` gives the tuple used by cap_html_policy. Lives here
# (not in models/board/authored or compile/config) because authored, normalized,
# resolved, and config all use it and each pair is a circular edge.
HtmlPolicy = Literal["none", "safe-subset", "trusted-raw"]

# SVG/Vega-Lite stroke-linecap vocabulary. One home: border dash caps, the
# shared StrokeStyle, and the resolved stroke all name the same three values.
LineCap = Literal["butt", "round", "square"]

# Incremental refresh setting, shared by board (AuthoredBoard.incremental) and
# query (_BaseQueryFields.incremental): a watermark column name enables the
# tail path, `false` opts out of an inherited setting, `None` inherits it. A
# bare `True` is rejected by validate_incremental_value — a single field, so
# there is no separate key field left to name a column on.
IncrementalValue = str | Literal[False] | None


def validate_incremental_value(
    v: Any,  # type-state: explicit_any — mode="before" validator input; raw YAML value
) -> Any:  # type-state: explicit_any — passthrough of the same boundary value
    """Reject a bare `incremental: true` — it has no watermark column to key on.

    Used as a `field_validator(mode="before")` body on both AuthoredBoard and
    the shared query field, so the message is identical at either scope.
    """
    if v is True:
        raise ValueError(
            "incremental: true is not valid — give the watermark column name "
            "instead, e.g. incremental: updated_at"
        )
    if isinstance(v, str) and not v.strip():
        raise ValueError(
            "incremental: '' is not valid — give the watermark column name, "
            "or omit the field (or set false) to disable incremental refresh"
        )
    return v


# =============================================================================
# FORMAT CONFIG
# =============================================================================


class FormatConfig(BaseModel):
    """Format configuration for value display.

    This model supports both simple format strings and complex formatting
    with custom prefix/suffix.

    Attributes:
        spec: D3 format string, preset name, or Excel pattern
        prefix: Custom prefix to prepend to formatted value
        suffix: Custom suffix to append to formatted value

    Example YAML:
        format:
          spec: ",.0f"
          prefix: "$"
          suffix: " USD"
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec: str | None = Field(
        default=None,
        description="D3 format string (e.g., ',.0f'), preset name (e.g., 'currency'), or Excel pattern.",
    )
    prefix: str | None = Field(
        default=None,
        description="Text placed before the formatted value (e.g., '$').",
    )
    suffix: str | None = Field(
        default=None,
        description="Text placed after the formatted value (e.g., ' USD', '%').",
    )
    notation: Notation | None = Field(
        default=None,
        description="Notation style: 'analytic' for SI-prefix (1 B, 1 M) or 'narrative' for prose-style (1bn, 1mn).",
    )


# =============================================================================
# SPACING PRIMITIVE
# =============================================================================


class SpacingValues(BaseModel):
    """Pre-parsed CSS spacing (margin/padding).

    CSS spacing supports 1-4 values:
    - "16px" → all sides equal
    - "8px 16px" → vertical, horizontal
    - "8px 16px 12px" → top, horizontal, bottom
    - "8px 16px 12px 4px" → top, right, bottom, left

    Attributes:
        top: Top spacing in pixels
        right: Right spacing in pixels
        bottom: Bottom spacing in pixels
        left: Left spacing in pixels
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    top: float = Field(default=0.0, description="Top spacing in pixels.")
    right: float = Field(default=0.0, description="Right spacing in pixels.")
    bottom: float = Field(default=0.0, description="Bottom spacing in pixels.")
    left: float = Field(default=0.0, description="Left spacing in pixels.")

    @property
    def horizontal(self) -> float:
        """Total horizontal spacing (left + right)."""
        return self.left + self.right

    @property
    def vertical(self) -> float:
        """Total vertical spacing (top + bottom)."""
        return self.top + self.bottom

    @classmethod
    def from_css(cls, value: str | None) -> SpacingValues:
        """Parse CSS spacing value into SpacingValues."""
        if not value:
            return cls()
        nums = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)", str(value))]
        if not nums:
            return cls()
        if len(nums) == 1:
            return cls(top=nums[0], right=nums[0], bottom=nums[0], left=nums[0])
        elif len(nums) == 2:
            return cls(top=nums[0], right=nums[1], bottom=nums[0], left=nums[1])
        elif len(nums) == 3:
            return cls(top=nums[0], right=nums[1], bottom=nums[2], left=nums[1])
        return cls(top=nums[0], right=nums[1], bottom=nums[2], left=nums[3])

    @model_validator(mode="before")
    @classmethod
    def _coerce_css_string(cls, v: Any) -> Any:
        if isinstance(v, (str, int, float)):
            # int/float treated as all-sides pixel value (e.g. padding: 12 → 12px all sides)
            parsed = cls.from_css(str(v))
            return {
                "top": parsed.top,
                "right": parsed.right,
                "bottom": parsed.bottom,
                "left": parsed.left,
            }
        return v


# SpacingValues serves as its own patch: it accepts CSS strings via its
# @model_validator, has defaults on all fields, and should NOT be replaced by
# an auto-generated all-Optional stripped variant in build_patch_model.
register_patch(SpacingValues)(SpacingValues)


# =============================================================================
# LEAF VALUE TYPES
# =============================================================================


class FontStyle(_PatchBase):
    """Text appearance. Merged as a unit.

    All fields Optional — FontStyle doubles as a patch at every level.
    """

    family: Annotated[str | None, FontFamily()] = Field(
        default=None, description="Font family name (e.g., 'sans-serif', 'Roboto')."
    )
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Text color as a CSS color string."
    )
    size: float | None = Field(default=None, description="Font size in pixels.")
    weight: str | float | None = Field(
        default=None,
        description="How heavy the type is drawn (e.g., 'bold', 400, 700).",
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
        description=(
            "Letter-case transform applied at render time. "
            "'title' uses Chicago/Gruber rules and preserves tokens with internal "
            "capitals (ARR, iPhone). 'sentence' uppercases only the first character. "
            "'none' (default) emits the string without any letter-case change."
        ),
    )
    line_height: float | None = Field(
        default=None,
        description=(
            "Line height as a unitless multiple of font size. Cascades through "
            "Style.font to all text roles that carry a FontStyle slot. Body prose "
            "defaults to 1.25; titles override tighter via their font patch in theme YAML."
        ),
    )


_CSS_NAMED_COLORS = frozenset(
    {
        "black",
        "white",
        "red",
        "green",
        "blue",
        "yellow",
        "orange",
        "purple",
        "pink",
        "gray",
        "grey",  # codespell:ignore grey
        "cyan",
        "magenta",
        "lime",
        "maroon",
        "navy",
        "olive",
        "teal",
        "aqua",
        "silver",
        "fuchsia",
        "transparent",
    }
)


class ResolvedFontStyle(BaseModel):
    """Fully resolved font — all fields concrete after cascade."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    family: str
    color: str
    size: float
    weight: str | float
    style: Literal["normal", "italic"]
    decoration: Literal["none", "line-through", "underline"]
    case: Literal["none", "sentence", "title", "upper", "lower", "slug", "camel"]
    line_height: float
    # Whether `family` guarantees tabular (fixed-advance) digits with no CSS
    # feature toggle — set from the vendored font registry (fonts.font_is_tabular),
    # never authored directly. A column-forming quantitative axis (the ruler
    # consumer) requires this before it can ship the suffix-field
    # reservation (font_measure.compose_suffix_reservation): the padding is
    # composed from measured space characters to match the suffix's
    # advance, and the column holds together only because every digit
    # shares one fixed advance, not the proportional one every other
    # character (including the padding itself) has.
    tabular_figures: bool


class RuleStyle(BaseModel):
    """Shared rule-line primitive: width, color, continuous mode.

    Used for table header rules, table row rules, support-table dividers, and
    support-table inter-row rules. All three fields are required; theme YAML
    supplies every instance. Support-table contexts set `continuous: true` in
    the theme; render code in that context does not read it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    width: float = Field(description="Rule line width in pixels.")
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Rule color; None inherits from theme."
    )
    continuous: bool = Field(
        description=(
            "Draw a continuous full-width rule (true) or only under columns (false)."
        ),
    )


class BorderStyle(BaseModel):
    """Box border. width/color/radius are required, produced by cascade from
    theme YAML; dash_array/line_cap/dash_offset are optional authoring opt-ins
    (unset means solid) and no theme is required to supply them.

    Does NOT cascade (ADR-003: box properties reset per level).
    'No border' is expressed as width=0, color='transparent', radius=0.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    width: float = Field(description="Border width in pixels.")
    color: str = Field(description="Border color as a CSS color string.")
    radius: float = Field(description="Border corner radius in pixels.")
    # dash_array/line_cap/dash_offset are genuinely optional, not a theme-population
    # gap: unset means a solid border (the common case, and every built-in theme's
    # default); dashing is an authoring opt-in, not something themes must supply.
    dash_array: Annotated[list[float] | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        description=(
            "SVG stroke-dasharray pattern in pixels (e.g. [4, 4]). "
            "None means a solid border."
        ),
    )
    line_cap: LineCap | None = Field(
        default=None,
        description="How each dash's ends are finished on a dashed border. None uses the renderer default (butt).",
    )
    dash_offset: float | None = Field(
        default=None,
        description="How far into the dash pattern the line starts, in pixels. None means 0.",
    )


class CornerStyle(BaseModel):
    """Corner rounding only, for a slot whose renderer never draws a stroke.

    A full BorderStyle's width/color/dash would validate cleanly here and
    do nothing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    radius: float = Field(description="Corner radius in pixels.")


@register_patch(BorderStyle)
class BorderStylePatch(BaseModel):
    # Hand-written (not build_patch_model): carries from_css() classmethod
    """Box border. Does NOT cascade (ADR-003: box properties reset per level).

    Doubles as CSS shorthand parser via from_css() for StylePatch.border.
    """

    model_config = ConfigDict(extra="forbid")

    radius: float | None = Field(
        default=None, description="Border corner radius in pixels."
    )
    color: Annotated[str | None, Color()] = Field(
        default=None, description="Border color (CSS color string)."
    )
    width: float | None = Field(default=None, description="Border width in pixels.")
    dash_array: Annotated[list[float] | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        description=(
            "SVG stroke-dasharray pattern in pixels (e.g. [4, 4]). "
            "None means a solid border."
        ),
    )
    line_cap: LineCap | None = Field(
        default=None,
        description="How each dash's ends are finished on a dashed border. None uses the renderer default (butt).",
    )
    dash_offset: float | None = Field(
        default=None,
        description="How far into the dash pattern the line starts, in pixels. None means 0.",
    )

    @classmethod
    def from_css(cls, value: str | None) -> BorderStylePatch:
        """Parse CSS border shorthand '2px #333' → BorderStylePatch.

        Supports hex (#abc, #aabbcc), rgb()/rgba(), and CSS named colors.
        CSS stroke-style tokens (solid/dashed/dotted/etc.) are ignored — style
        is not rendered and has been removed from the model.
        Does NOT extract radius — set border.radius explicitly in YAML.
        """
        if not value:
            return cls()
        border_str = str(value)
        parsed: dict[str, float | str] = {}

        w_match = re.search(r"(\d+(?:\.\d+)?)\s*px", border_str)
        if w_match:
            parsed["width"] = float(w_match.group(1))

        hex_match = re.search(r"#[0-9a-fA-F]{3,8}\b", border_str)
        if hex_match:
            parsed["color"] = hex_match.group(0)
        else:
            rgb_match = re.search(r"rgba?\([^)]+\)", border_str)
            if rgb_match:
                parsed["color"] = rgb_match.group(0)
            else:
                for word in re.findall(r"[a-zA-Z]+", border_str):
                    if word.lower() in _CSS_NAMED_COLORS:
                        parsed["color"] = word.lower()
                        break

        return cls.model_validate(parsed)

    @model_validator(mode="before")
    @classmethod
    def _coerce_css_string(cls, v: Any) -> Any:
        if isinstance(v, str):
            return cls.from_css(v).model_dump(exclude_unset=True)
        return v


# BorderStylePatch is all-Optional and is its own patch — prevent build_patch_model
# from synthesizing a double-suffixed BorderStylePatchPatch when BarChartStyle
# (which inherits border: BorderStylePatch | None) is patched for authored overlays.
register_as_own_patch(BorderStylePatch)


class StrokeStyle(BaseModel):
    """Stroke appearance sub-block shared across mark families.

    All fields optional — StrokeStyle doubles as the overlay type for cascade
    patches and as a nullable sentinel (``stroke: StrokeStyle | None``) on
    MarkStyle. Compiled mark families (LineStyle, AreaStyle, etc.) use it as a
    required sub-block and enforce their specific sub-field requirements via
    ``@model_validator`` on the family class itself.

    None semantics per field:
      - color: line/area use the mark's own fill color; other families require it.
      - width: absent = VL default (1 px). Required by most compiled families.
      - cap/join: absent = VL default (butt/miter). Only line/arc set these.
      - dasharray: absent = solid line. Only spark.empty sets this.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    color: Annotated[str | None, Color()] = Field(
        default=None, description="Stroke color as a CSS color string."
    )
    width: float | None = Field(default=None, description="Stroke width in pixels.")
    cap: LineCap | None = Field(
        default=None,
        description="How the stroke's ends are finished (butt, round, or square).",
    )
    join: Literal["miter", "round", "bevel"] | None = Field(
        default=None,
        description="How two stroke segments are joined (miter, round, or bevel).",
    )
    dasharray: str | None = Field(
        default=None,
        description="Lengths of the dashes and the blanks between them (e.g. '4 2').",
    )


class FontColorStrokeStyle(StrokeStyle):
    """Stroke for rule marks whose color defaults to the root font color.

    Subclasses StrokeStyle to scope the Inherit marker: line/area marks also use
    StrokeStyle but must NOT inherit font.color (they inherit stroke from their own
    fill via slot expansion). Annotating StrokeStyle.color globally would override
    those per-family slot-derived links; a subclass confines the inheritance to
    rule only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    color: Annotated[str | None, Inherit(from_path="Style.font.color"), Color()] = (
        Field(
            default=None,
            description="Stroke color for rule marks.",
        )
    )


# Vega/Vega-Lite's built-in named color schemes (https://vega.github.io/vega/docs/schemes/),
# the only strings ``ScaleTargetConfig.palette`` forwards straight through to VL's
# ``scale.scheme``. Sampled-discrete shorthand like "blues-9" is not a real scheme
# name — VL fails deep inside rendering ("Cannot read properties of undefined")
# rather than rejecting it up front, so this is validated at compile time instead.
#
# Empirically verified against this repo's pinned vl-convert (not hand-copied from
# docs alone): every name below was round-tripped through
# ``vlc.vegalite_to_svg`` with a quantitative ``scale.scheme`` encoding and
# confirmed to render — see ``test_vega_scheme_names_parity.py``, which re-runs
# that sweep and fails if this set and the bundled renderer ever diverge.
VEGA_SCHEME_NAMES: frozenset[str] = frozenset(
    {
        # Categorical
        "accent",
        "category10",
        "category20",
        "category20b",
        "category20c",
        "dark2",
        "observable10",
        "paired",
        "pastel1",
        "pastel2",
        "set1",
        "set2",
        "set3",
        "tableau10",
        "tableau20",
        # Sequential (single-hue)
        "blues",
        "tealblues",
        "teals",
        "greens",
        "browns",
        "greys",  # codespell:ignore greys
        "oranges",
        "purples",
        "reds",
        "warmgreys",
        # Sequential (multi-hue)
        "viridis",
        "magma",
        "inferno",
        "plasma",
        "cividis",
        "turbo",
        "bluegreen",
        "bluepurple",
        "goldgreen",
        "goldorange",
        "goldred",
        "greenblue",
        "orangered",
        "purplebluegreen",
        "purpleblue",
        "purplered",
        "redpurple",
        "yellowgreenblue",
        "yellowgreen",
        "yelloworangebrown",
        "yelloworangered",
        # Diverging
        "blueorange",
        "brownbluegreen",
        "purplegreen",
        "pinkyellowgreen",
        "purpleorange",
        "redblue",
        "redgrey",
        "redyellowblue",
        "redyellowgreen",
        "spectral",
        # Cyclical
        "rainbow",
        "sinebow",
        # For dark backgrounds
        "darkblue",
        "darkgold",
        "darkgreen",
        "darkmulti",
        "darkred",
        # For light backgrounds
        "lightgreyred",
        "lightgreyteal",
        "lightmulti",
        "lightorange",
        "lighttealblue",
    }
)


def _might_be_palette_role(value: str) -> bool:
    """True when ``value`` could still name a theme palette role.

    Deliberately shape-only: a role is whatever a theme's ``palettes:`` block
    binds, and that map is not final at validation time. What is *not* a role is
    decidable now, and must stay a hard error here rather than be deferred:

    - anything carrying ``:N``/``_r`` shorthand or a leading ``#``
    - a Vega scheme name, which is legal in a gradient but never in a stops slot
    - a perceptual anti-pattern (`jet`, `rainbow`, `hsv`), whose rejection is a
      deliberate gate that deferral would silently swallow
    """
    from dbt_charts.core.compile.resolve.style.palette import (  # noqa: PLC0415
        is_hard_fail_name,
    )

    return (
        bool(value)
        and ":" not in value
        and not value.startswith("#")
        and value not in VEGA_SCHEME_NAMES
        and not is_hard_fail_name(value)
    )


class ScaleTargetPaletteValidationMixin(BaseModel):
    """Shared authored-input validator for ``ScaleTargetConfig.palette``.

    Inherited by both ``ScaleTargetConfig`` and ``ScaleTargetConfigPatch`` so
    chart-local authored patches reject a bad palette at the compile boundary
    (where ``normalize_board()`` wraps the resulting ``pydantic.ValidationError``
    into a proper ``ERR-VALIDATION-FIELD`` diagnostic) instead of only at
    resolve time, where the raw pydantic error leaks past dbt charts' error
    formatting — ``build_patch_model_ext`` only carries over validators that
    live on the patch model's base class (mirrors ``ScaleDomainValidationMixin``).
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _validate_named_palette(cls, data: object) -> object:
        """A string palette is either a literal Vega scheme name (forwarded
        as-is to VL's ``scale.scheme`` by geo/heatmap gradient rendering) or a
        dbt charts named palette (resolved to hex stops downstream by
        ``bake_scale_target_stops``/``resolve_palette_stops``). Reject anything
        that resolves as neither.

        Deliberately does *not* bake ``resolved_stops`` here: this validator
        also runs on every intermediate ``merge_onto_base`` cascade step, so
        baking here can leave a stale value from a *previous* palette on a
        chart-local override. Baking happens explicitly, once, at each
        resolve-time construction site instead — see ``bake_scale_target_stops``
        below.

        A dash-suffixed name whose prefix is a real scheme (``"blues-9"``,
        VL's sampled-discrete shorthand, which dbt charts does not forward) gets
        a specific hint naming the continuous scheme instead of the generic
        unknown-palette message — that shape is the exact mistake this
        validator exists to catch: an author reaching for N discrete buckets
        that dbt charts doesn't support yet, not a random typo.

        ``mode="before"`` on the raw dict (not ``field_validator``) so this
        runs unchanged on both ``ScaleTargetConfig`` and the generated
        ``ScaleTargetConfigPatch`` — a field-scoped validator would need the
        field to exist on this mixin itself, which a patch base never does.
        """
        if not isinstance(data, dict):
            return data
        value = data.get("palette")
        if not isinstance(value, str) or value in VEGA_SCHEME_NAMES:
            return data
        from dbt_charts.core.compile.resolve.style.palette import (  # noqa: PLC0415
            UnknownPaletteError,
            palette as resolve_palette,
        )

        try:
            resolve_palette(value)
        except UnknownPaletteError as exc:
            base, _, suffix = value.rpartition("-")
            if base in VEGA_SCHEME_NAMES and suffix.isdigit():
                raise ValueError(
                    f"palette {value!r} requests a {suffix}-step discrete "
                    f"variant of the '{base}' scheme. dbt charts does not support "
                    "bucketed/quantized color scales yet — use the continuous "
                    f"scheme '{base}' instead."
                ) from None
            if _might_be_palette_role(value):
                # A theme palette role, unresolvable until the extends chain has
                # merged. `expand_palette_refs` substitutes it the moment the
                # role map is final, and raises there if it is not one.
                return data
            raise ValueError(str(exc)) from None
        return data


class ScaleTargetConfig(ScaleTargetPaletteValidationMixin):
    """Scale configuration for a single style target (background or color).

    Used as a chart channel annotation (``ColumnScaleConfig``) and as the
    ``gradient`` field of ``ColorStyle`` / ``StaticGradientColorStyle``.
    Lives in primitives so both ``chart/authored.py`` and ``style/authored.py``
    can import it without creating a circular dependency.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    palette: Annotated[
        ScalePaletteName | list[str] | list[float] | str,
        Merge(Strategy.OVERRIDE),
        Palette(),
    ] = Field(
        description="Which colors the scale draws from: a built-in palette name or Vega scheme, a CSS color list for categorical, or a float list for relative stops."
    )
    domain: Literal["data"] | None = Field(
        default="data",
        description=(
            "Scale domain source ('data' uses the data extent, widened to "
            "nice round bounds when nice is true; None uses explicit min/max)."
        ),
    )
    min: float | int | None = Field(
        default=None, description="Minimum scale domain value (overrides data minimum)."
    )
    max: float | int | None = Field(
        default=None, description="Maximum scale domain value (overrides data maximum)."
    )
    nice: bool = Field(
        default=True,
        description=(
            "Widen a data-derived domain to round ('nice') bounds and label "
            "every nice tick on the legend, mirroring Vega-Lite's own "
            "scale.nice. Applies to the color scale a gradient legend "
            "labels, not the x/y position scale. Ignored once min or max is "
            "set; a single-sided bound already fixes that edge exactly. "
            "Widening is currently honored on heatmap's themed color "
            "gradient only; geoshape's choropleth honors `nice: false` "
            "(exact endpoint labels) but not `nice: true`'s widening."
        ),
    )
    null_color: Annotated[str | None, Color()] = Field(
        default=None, description="Color assigned to null values."
    )
    hinge: float | Literal["auto"] | None = Field(
        default=None,
        description="Diverging scale midpoint value, or 'auto' to use the data midpoint.",
    )
    arm_mode: Literal["asymmetric", "symmetric"] = Field(
        default="asymmetric",
        description="How diverging scale arms are stretched: 'asymmetric' (proportional) or 'symmetric' (equal arms).",
    )


class ResolvedScaleTargetConfig(ScaleTargetConfig):
    """Bake ran; palette was an inline list or a Vega scheme name — nothing
    to resolve. No resolved_stops field — this variant structurally never
    has one, so a caller cannot read a stale/absent value by mistake."""

    @model_validator(mode="after")
    def _reject_unbaked_named_palette(self) -> ResolvedScaleTargetConfig:
        # type(self) is exact: subclass ResolvedNamedPaletteScaleTargetConfig
        # legitimately holds a named palette string, so we only guard the base class.
        if type(self) is ResolvedScaleTargetConfig and isinstance(self.palette, str):
            if self.palette not in VEGA_SCHEME_NAMES:
                raise ValueError(
                    f"palette {self.palette!r} is a named dbt-charts palette and must be "
                    "baked to resolved_stops before construction — "
                    "use bake_scale_target_stops() which produces ResolvedNamedPaletteScaleTargetConfig."
                )
        return self


class ResolvedNamedPaletteScaleTargetConfig(ResolvedScaleTargetConfig):
    """Bake ran; palette was a string that was resolved to stops.

    Covers both dbt-charts named palettes (resolved via bake_scale_target_stops)
    and the WCAG-safe table column path (_with_resolved_scale_stops). The
    resolved_stops field carries the baked hex stops.
    """

    resolved_stops: tuple[str, ...] = Field(
        description=(
            "Hex stops baked once at resolve-time. Structurally required — only "
            "this variant has the field, so callers cannot read a stale/absent "
            "value on the wrong type."
        ),
    )


# Union alias: every pydantic field that HOLDS a resolved scale target must use
# this, not the bare ResolvedScaleTargetConfig. Pydantic serializes to the
# declared field type; a ResolvedNamedPaletteScaleTargetConfig stored in a
# ResolvedScaleTargetConfig-annotated field silently drops resolved_stops on
# dump and fails extra_forbidden on reload. This union lets pydantic's smart
# union pick the correct member in both directions.
ResolvedScaleTarget = ResolvedNamedPaletteScaleTargetConfig | ResolvedScaleTargetConfig


def bake_scale_target_stops(target: ScaleTargetConfig) -> ResolvedScaleTargetConfig:
    """Construct the correct resolved subtype for a scale target.

    Named dbt charts palette → ResolvedNamedPaletteScaleTargetConfig with fresh
    resolved_stops. Vega scheme name or inline stop list → plain
    ResolvedScaleTargetConfig (no resolved_stops — structurally absent, not None).

    Called explicitly at each resolve-time construction site (chart channels
    via `compile/resolve/chart/channel.py::_parse_channel_scale`, heatmap/geo theme-cascade
    gradients) — never from `ScaleTargetConfig`'s own validator, which also
    runs on every intermediate `merge_onto_base` cascade step and would bake
    a stale value there before the final palette is known.
    `_with_resolved_scale_stops` (compile/resolve/chart/_table.py) is the WCAG
    `surface="table"` analog for table column scales.
    """
    if not isinstance(target.palette, str) or target.palette in VEGA_SCHEME_NAMES:
        return ResolvedScaleTargetConfig.model_validate(
            target.model_dump(exclude_unset=True)
        )
    from dbt_charts.core.compile.resolve.style.palette import (
        palette as resolve_palette,
    )  # noqa: PLC0415

    return ResolvedNamedPaletteScaleTargetConfig.model_validate(
        {
            **target.model_dump(exclude_unset=True),
            "resolved_stops": tuple(resolve_palette(target.palette)),
        }
    )


# Patch type for ScaleTargetConfig — generated (and registered) here, right after
# the compiled class, so every later recursive build_patch_model_ext call that
# encounters ScaleTargetConfig as a nested field (StaticGradientColorStyle.gradient,
# ColumnScaleConfig.color/background) reuses this exact registered class instead of
# a vanilla auto-generated one with no palette validation.
#
# resolved_stops is absent from ScaleTargetConfig itself (it lives only on the
# resolved subtypes), so ScaleTargetConfigPatch correctly excludes it by
# construction — no explicit exclude= needed.
if TYPE_CHECKING:

    class ScaleTargetConfigPatch(ScaleTargetConfig):
        pass

else:

    class _ScaleTargetConfigPatchBase(_PatchBase, ScaleTargetPaletteValidationMixin):
        """Base for ScaleTargetConfigPatch: extra="forbid" from _PatchBase plus
        the palette validation from ScaleTargetPaletteValidationMixin."""

    ScaleTargetConfigPatch = register_patch(ScaleTargetConfig)(
        build_patch_model_ext(
            ScaleTargetConfig,
            base_cls=_ScaleTargetConfigPatchBase,
        )
    )


class CategoricalColorStyle(BaseModel):
    """Categorical palette config: per-series colors and single-series ink list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # The StopsPaletteName arm on palette/single_series_palette is schema-facing
    # only — _expand_palette_names below eagerly resolves a named palette to
    # its list[str] stops during validation, so the name itself never
    # survives onto a compiled instance. Stops-only, not every shipped name:
    # a tone resolves to no stops, so offering one is an authored board that
    # parses and dies at resolve.
    palette: Annotated[
        StopsPaletteName | list[str] | str | None, Merge(Strategy.OVERRIDE), Palette()
    ] = Field(
        default=None,
        description="Categorical color palette: list of stops or a named palette. Expanded to list[str] at validation time.",
    )
    single_series_palette: Annotated[
        StopsPaletteName | list[str] | str | None, Merge(Strategy.OVERRIDE), Palette()
    ] = Field(
        default=None,
        description="Ordered list of single-series mark inks (must be non-empty when set), or a palette name.",
    )
    requested_alias_palette: Annotated[str | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        exclude=True,
        description=(
            "Internal: set when this layer authors palette/single_series_palette "
            "as a known WARN-PALETTE-UNSUPPORTED anti-pattern name (e.g. RdYlGn); "
            "holds the originally-requested name for the render-stage detector. "
            "Computed by _expand_palette_names — not user-authorable."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _expand_palette_names(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        from dbt_charts.core.compile.resolve.style.palette import (  # noqa: PLC0415
            is_warn_alias,
            resolve_palette_alias,
        )

        # The merge cascade re-validates an already-compiled model through a
        # dict of every field, provenance included — so the key's presence
        # can't be the tell. Only a value this resolver would never produce is
        # one an author fabricated.
        authored = data.get("requested_alias_palette")
        if authored is not None and not is_warn_alias(authored):
            raise ValueError(
                "requested_alias_palette is computed internally from an aliased "
                "palette/single_series_palette name and cannot be authored directly."
            )

        from dbt_charts.core.compile.resolve.style.palette import (  # noqa: PLC0415
            UnknownPaletteError,
        )

        data = dict(data)
        for field_name in ("palette", "single_series_palette"):
            value = data.get(field_name)
            if not isinstance(value, str):
                continue
            try:
                stops, requested = resolve_palette_alias(value)
            except UnknownPaletteError:
                if not _might_be_palette_role(value):
                    raise
                # Possibly a theme palette role (`category`). Roles are not
                # knowable here: the theme's `palettes:` map is not final until
                # its whole extends chain has merged. `expand_palette_refs`
                # runs the moment it is, substitutes the role, and raises on a
                # name that is neither. Nothing downstream sees a bare name.
                continue
            data[field_name] = stops
            if requested is not None:
                data["requested_alias_palette"] = requested
        return data

    @field_validator("single_series_palette", mode="after")
    @classmethod
    def _single_series_palette_non_empty(
        cls, value: list[str] | None
    ) -> list[str] | None:
        if value is not None and not value:
            raise ValueError(
                "single_series_palette must contain at least one ink stop; "
                "empty lists break the spark cascade and the single-series render path."
            )
        return value


class StaticGradientColorStyle(BaseModel):
    """Color config for geo/point_map/table families, no categorical arm.

    Authoring ``categorical`` on these families raises a pydantic.ValidationError
    (extra_forbidden). Use ``ColorStyle`` for cartesian/pie families.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    static: Annotated[str | None, Color()] = Field(
        default=None,
        description="One explicit color: the marks on most families when no color column is encoded, the cell text on a table.",
    )
    gradient: ScaleTargetConfig | None = Field(
        default=None,
        description="Continuous gradient scale for the color encoding.",
    )


class ColorStyle(StaticGradientColorStyle):
    """Unified chart color config: static paint, categorical palette, gradient scale.

    Cartesian and radial families use this type; ``categorical`` carries the
    series color palette. Geo/table families use the narrower
    ``StaticGradientColorStyle`` (no ``categorical`` arm).
    """

    categorical: CategoricalColorStyle | None = Field(
        default=None,
        description="Palettes used when color encodes distinct categories, or a lone series.",
    )
