"""Theme-stage style classes: Style root."""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from dbt_charts.core.compile.models.markers import (
    Absent,
    Color,
    Merge,
    NotInherited,
    Strategy,
)
from dbt_charts.core.compile.models.primitives import (
    BOX_SHADOW_PATTERN,
    BorderStyle,
    FormatConfig,
    NamedFont,
    SpacingValues,
    check_box_shadow,
)
from dbt_charts.core.compile.models.schema_names import PaletteName
from dbt_charts.core.compile.models.style.theme.board import (
    FrameStyle,
    PlaceholderStyle,
    RootFontStyle,
    TextStyle,
    TitleStyle,
)
from dbt_charts.core.compile.models.style.theme.charts import (
    ChartsStyle,
)
from dbt_charts.core.compile.models.style.theme.kpi import (
    KpiTonesStyle,
)
from dbt_charts.core.compile.models.style.theme.layout import (
    LayoutStyle,
)
from dbt_charts.core.compile.models.style.theme.page import (
    FooterStyle,
    TimestampStyle,
)
from dbt_charts.core.compile.models.style.theme.variables import (
    VariablesStyle,
)


class Style(BaseModel):
    """Authoritative compiled style. Built from a single theme YAML file.

    All sections present with defaults matching themes/stark.yaml.
    This is the 'compiled from theme' layer — not yet resolved/cascaded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    frame: FrameStyle = Field(description="Board-level structural frame dimensions.")
    background: Annotated[str, Color()] = Field(
        description="Working-surface background color (board and card fills)."
    )
    # Semantic color tokens — set once at root level, cascade to UI chrome.
    # accent: focus_color, input.focus_color (via Inherit)
    # muted: secondary TEXT only (KPI support rows, table/spark subtitles,
    # more-rows/empty-state lines). Bar-track fills are required theme
    # values (spark_bar.bar.background, table.spark.bar.background) — they
    # used to Inherit from muted, which conflated fill- and text-grade grays.
    # font.color: tick.stroke.color, rule.stroke.color (via Inherit)
    # spark colors (spark.color, spark.bar.color, spark_bar.bar.color) seed from
    # single_series_palette[0] in resolved.py — not Inherit, since list indexing
    # can't be expressed as a declarative path.
    accent: Annotated[str, Color()] = Field(
        description="Accent color token cascaded to sparklines, bars, and focus rings."
    )
    muted: Annotated[str, Color()] = Field(
        description="Muted secondary-text color token (KPI support rows, table and spark subtitles)."
    )
    font: RootFontStyle = Field(
        description="Root font: literal family, size, weight, color; never a `fonts` name."
    )
    border: BorderStyle = Field(
        description="Frame drawn around the whole board: `{width, color, radius}` or `'2px solid #333'`. Omit the key for no border."
    )
    box_shadow: Annotated[
        str | None,
        Absent("no shadow"),
        BeforeValidator(
            check_box_shadow,
            json_schema_input_type=Optional[  # noqa: UP045 (pyright rejects `Annotated[...] | None`)
                Annotated[str, StringConstraints(pattern=f"^{BOX_SHADOW_PATTERN}$")]
            ],
        ),
    ] = Field(
        default=None,
        description="Drop shadow under chart cards: x offset, y offset, blur, color.",
    )
    opacity: float = Field(description="Default mark opacity (0–1).")
    title: TitleStyle = Field(
        description="Typography for every heading: board and prose titles, and chart, table, and spark object titles."
    )
    text: TextStyle = Field(description="Markdown and plain text content style.")
    placeholder: PlaceholderStyle = Field(
        description="Appearance of the stand-in drawn on a chart with no data."
    )
    charts: ChartsStyle = Field(
        description="Root of all chart-type styles and shared chart configuration."
    )
    layout: LayoutStyle = Field(
        description="Spacing and arrangement inside the containers (rows, cols, grid, tabs, details)."
    )
    variables: VariablesStyle = Field(description="Variable controls chrome style.")
    # footer/timestamp are root-board-only chrome — a nested
    # board has no footer or timestamp line of its own to draw, so an
    # ancestor's authored values here have nothing to reach.
    footer: Annotated[FooterStyle, NotInherited()] = Field(
        description="Page footer chrome visibility."
    )
    timestamp: Annotated[TimestampStyle, NotInherited()] = Field(
        description="Data-freshness chrome: visibility, placement, format, and font."
    )
    # Cascade-managed sentinel — None means "no aliases at this cascade level" (not empty).
    # The default theme ships None (all built-in names are engine-predefined, not theme aliases).
    # Board YAML patches may supply user-defined aliases (→ dict) or omit (→ None).
    # merge_onto_base key-wise merges board-level aliases onto theme aliases so board keys win
    # while unredefined theme keys propagate. None is intentional here — unlike other
    # Compiled fields the theme populates, this sentinel distinguishes "no override"
    # from "empty override" across every cascade step, not just the base.
    formats: Annotated[dict[str, str | FormatConfig] | None, Absent("no aliases")] = (
        Field(
            default=None,
            description="Format alias map; None means no aliases at this cascade level.",
        )
    )
    # Named-font registry, merged key-wise like ``formats`` (board keys win, other
    # theme keys propagate). Names are expanded into slots by
    # ``expand_named_fonts`` once the whole cascade has merged.
    fonts: Annotated[dict[str, NamedFont] | None, Merge(Strategy.DEEP)] = Field(
        default=None,
        description=(
            "Named fonts as ``<name>: {family, size, weight, color}``, never a bare "
            "family string; other font slots reference one as ``font: <name>`` or "
            "``font: {extends: <name>, ...}``. ``heading`` carries the H1–H6 "
            "ramp in ``sizes``. An entry no slot references paints nothing."
        ),
    )
    # Theme palette role assignments.
    # Keys (role names) stay open — no enforced enum. Values are looked up as
    # an exact key against the shipped palette index (palette.py's
    # color_from_theme); unlike ScaleTargetConfig.palette/
    # CategoricalColorStyle.palette, nothing here ever goes through the
    # name:N_r shorthand parser, so a bare PaletteName is the only legal
    # value — no str fallback arm needed.
    # Default theme seeds conventional roles (chrome, info, negative, positive,
    # warning, category, sequence, diverge). Child themes may override
    # individual roles.
    # None = not authored at this cascade level.
    palettes: dict[str, PaletteName] | None = Field(
        default=None,
        description=(
            "Theme palette role assignments: role name to a built-in palette name, never a color. "
            "Custom colors go in ``charts.color.categorical.palette: [hex, ...]``. "
            "Default seed: chrome, info, negative, positive, warning, category, sequence, diverge."
        ),
    )
    # Semantic tone palette shared by the KPI support row, table
    # conditional-formatting glyphs, and in-cell spark marks' opt-in
    # negative_color — a board-level slot (not one family's) since all three
    # read it.
    tones: KpiTonesStyle = Field(
        description="Semantic tone color palette (positive/negative/warning/info) for KPI support rows, table conditional glyphs, and spark negative_color."
    )
    # Top-level theme role shortcuts.
    # Optional bare aliases: e.g. ink → chrome[10]. Board authors write the
    # bare name; resolver dispatches via the role's address.
    # None = no top-level aliases at this cascade level.
    roles: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional top-level theme role aliases: bare name → role address "
            "(`role[N]` or `role.alias`). e.g. ink: chrome[10]"
        ),
    )
    # padding/margin are per-board and never theme-populated; NotInherited
    # keeps an ancestor's value from crossing into a nested board.
    padding: Annotated[
        SpacingValues | None, NotInherited(), Absent("no per-board override")
    ] = Field(
        default=None,
        description="Per-board padding override (CSS shorthand or structured).",
    )
    margin: Annotated[
        SpacingValues | None, NotInherited(), Absent("no per-board override")
    ] = Field(
        default=None,
        description="Per-board margin override (CSS shorthand or structured).",
    )
    gap: Annotated[float | None, Absent("the layout type's own gap")] = Field(
        default=None, description="Gap between layout items in pixels."
    )

    @model_validator(mode="after")
    def _validate_fonts(self) -> Style:
        for name, font in self.fonts.items() if self.fonts is not None else ():
            if name != "heading" and font.sizes is not None:
                raise ValueError(
                    f"Only the 'heading' font takes sizes (the H1–H6 ramp); "
                    f"'{name}' sets size."
                )
            if name == "heading" and font.size is not None:
                raise ValueError("The 'heading' font takes sizes (H1–H6), not size.")
        ramp = self.heading_ramp()
        level = self.title.level
        if isinstance(level, int) and level > len(ramp):
            raise ValueError(
                f"style.title.level={level} exceeds the H-ramp (style.fonts.heading."
                f"sizes has {len(ramp)} entries; valid range is 1..{len(ramp)})"
            )
        return self

    def heading_ramp(self) -> list[float]:
        """The H1–H6 sizes from ``style.fonts.heading.sizes``."""
        heading = self.fonts.get("heading") if self.fonts is not None else None
        if heading is None or heading.sizes is None:
            raise ValueError(
                "style.fonts.heading.sizes is required: board titles, markdown "
                "headings and chart titles read the H1–H6 ramp from it."
            )
        return heading.sizes
