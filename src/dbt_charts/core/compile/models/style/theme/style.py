"""Theme-stage style classes: Style root."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import Color, Merge, Strategy
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    FormatConfig,
    SpacingValues,
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

    # nested=CHILD: FrameStyle's own docstring says it must not cascade to
    # child boards — each board's structural frame is its own, the same as
    # gap/margin/padding below.
    frame: Annotated[FrameStyle, Merge(Strategy.DEEP, nested=Strategy.CHILD)] = Field(
        description="Board-level structural frame dimensions."
    )
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
        description="Root font configuration including emoji mode."
    )
    border: BorderStyle = Field(
        description="Default border style cascaded to all chart cards."
    )
    box_shadow: str | None = Field(
        default=None,
        description="CSS box shadow for chart cards; None means no shadow.",
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
    # nested=CHILD: rows/cols/grid spacing and grid column count are each
    # board's own, not thematic identity. The marker has to sit on this
    # whole container, not on LayoutStyle's own rows/cols/grid fields:
    # merge_patches only checks a Merge marker on the field it walks, so an
    # inner marker would never fire unless a nested board separately
    # authored something else under layout: too. That also stops
    # tabs/details styling from crossing a board boundary.
    layout: Annotated[LayoutStyle, Merge(Strategy.DEEP, nested=Strategy.CHILD)] = Field(
        description="Spacing and arrangement inside the containers (rows, cols, grid, tabs, details)."
    )
    variables: VariablesStyle = Field(description="Variable controls chrome style.")
    # nested=CHILD: footer/timestamp are root-board-only chrome — a nested
    # board has no footer or timestamp line of its own to draw, so an
    # ancestor's authored values here have nothing to reach.
    footer: Annotated[FooterStyle, Merge(Strategy.DEEP, nested=Strategy.CHILD)] = Field(
        description="Page footer chrome visibility."
    )
    timestamp: Annotated[
        TimestampStyle, Merge(Strategy.DEEP, nested=Strategy.CHILD)
    ] = Field(
        description="Data-freshness chrome: visibility, placement, format, and font."
    )
    # Cascade-managed sentinel — None means "no aliases at this cascade level" (not empty).
    # The default theme ships None (all built-in names are engine-predefined, not theme aliases).
    # Board YAML patches may supply user-defined aliases (→ dict) or omit (→ None).
    # merge_onto_base key-wise merges board-level aliases onto theme aliases so board keys win
    # while unredefined theme keys propagate. None is intentional here — unlike other
    # Compiled fields the theme populates, this sentinel distinguishes "no override"
    # from "empty override" across every cascade step, not just the base.
    formats: dict[str, str | FormatConfig] | None = Field(
        default=None,
        description="Format alias map; None means no aliases at this cascade level.",
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
            "Theme palette role assignments: open dict mapping role name to palette file name. "
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
    # Optional bare aliases: e.g. ink → chrome.heading. Board authors write the
    # bare name; resolver dispatches via the dotted palette address.
    # None = no top-level aliases at this cascade level.
    roles: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional top-level theme role aliases: bare name → role.alias. "
            "e.g. ink: chrome.heading"
        ),
    )
    # padding/margin/gap below: none of them are theme-populated (no shipped
    # theme sets any of the three), matching their own "per-board"
    # descriptions. Each carries nested=CHILD so a nested board that authors
    # any style of its own never inherits an ancestor's value for these —
    # the same rows/cols/grid pattern. A nested board authoring no style at
    # all still inherits everything, these three included, via the
    # compile_board_resolved_style fast path (verbatim reuse of the parent).
    padding: Annotated[
        SpacingValues | None, Merge(Strategy.DEEP, nested=Strategy.CHILD)
    ] = Field(
        default=None,
        description="Per-board padding override (CSS shorthand or structured).",
    )
    margin: Annotated[
        SpacingValues | None, Merge(Strategy.DEEP, nested=Strategy.CHILD)
    ] = Field(
        default=None,
        description="Per-board margin override (CSS shorthand or structured).",
    )
    gap: Annotated[float | None, Merge(Strategy.OVERRIDE, nested=Strategy.CHILD)] = (
        Field(default=None, description="Per-board gap between layout items in pixels.")
    )
