"""dbt charts typographic system: title size, weight, and family resolution.

Two title roles, one H-stack
----------------------------

``style.title.sizes`` is the canonical H1-H6 ramp. Two roles index into it:

  1. **Semantic headings** — board titles, prose section titles, Markdown
     headings. They index the H stack by **semantic level** (``board.level``
     or the authored Markdown level). ``board_title_spec`` and the mdsvg path
     own this role.

  2. **Object labels** — chart, table, and spark titles. They pick an H slot
     by **outer card width tier alone**. Board nesting does not affect the
     result. ``chart_title_spec`` owns this role.

Object titles use this fixed mapping (preserving the existing tier ladder):

  tiny    (< 352.5px):              H5  / theme compact weight
  narrow  (352.5px to < 540.5px):   H3  / weight from config
  medium  (540.5px to < 1104.5px):  H2  / weight from config
  wide    (>= 1104.5px):            H2  / weight from config

Internally the mapping uses theme YAML's ``style.title.width_offsets.*``
added to a fixed object-title anchor of 2, so a theme can shift the ladder
without changing engine code.

Font family rules (theme-driven; values below are per-theme):
- Tiny + narrow object titles resolve to ``style.font.family`` (the body
  family). On every shipped theme that's a sans family.
- Medium + wide object titles resolve to ``style.title.font.family`` (the
  title-slot family) - sans on ``stark``, serif on ``default``/``cream``.
- ``use_title_family=True`` forces the theme's title stack at any width
  (used for prose-tagged titles, whatever width tier they land in).

Board/section titles are **purely semantic**: their size depends only on
``board.level`` (or an explicit ``style.title.level`` override). Width tiers
never apply — a board title in a narrow column stays the same pixel size as
the same-level board title in a wide column.

Chart labels (axis, legend, tick) are separately themed - not controlled here.

Usage::

    from dbt_charts.core.compile.resolve.style.typography import chart_title_spec, board_title_spec

    # Object title (chart/table/spark) - width-only, no board level input
    font_size, weight, family = chart_title_spec(
        width, resolved_chart_style=resolved_style.charts
    )

    # Semantic board title - level-only, no width input
    font_size, weight = board_title_spec(level=board_level)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.primitives import FontStyle, ResolvedFontStyle
    from dbt_charts.core.compile.models.style.context import ChartStyleContext
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle

# ---------------------------------------------------------------------------
# Pixel-width thresholds that separate the four width tiers.
#
# These are column-grid geometry, not user preference. They are also absolute:
# a card of a given pixel width gets the same label treatment on every board,
# because an object's label belongs to the object, not to the board's outline.
# The grid below is a single REFERENCE — the shipped default board — used to
# derive the thresholds once. It is never recomputed per board; doing so would
# give two same-sized cards different labels on differently-sized boards.
#
# Each boundary sits half a column below a design line:
#
#   tiny   — narrower than a third of the board   (line:  8 of 24 cols)
#   narrow — narrower than half the board         (line: 12 of 24 cols)
#   wide   — essentially the full board           (line: 24 of 24 cols)
#
# The half-column offset is gap tolerance, not slack: a card spanning exactly
# half the grid measures a few px under half once card_gap is deducted, and has
# to still count as sitting on the line.
#
# Keep the exact products. With an odd-pixel column unit, half-column boundaries
# land on .5px, matching the fractional widths the layout engine can emit.
#
# If the default board's geometry changes, re-derive from the new column unit —
# do not hand-pick pixel values. Rationale, the two title roles, and the
# procedure: docs/guides/typographic-tiers.md
#
# Level offsets for each tier live in theme YAML under style.title.width_offsets.*.
# ---------------------------------------------------------------------------

_REFERENCE_CONTENT_WIDTH: float = 1128.0  # 1200px board − 2 × 36px margin
_GRID_COLUMNS: int = 24
_COLUMN_UNIT: float = _REFERENCE_CONTENT_WIDTH / _GRID_COLUMNS  # 47.0px

_TINY_MAX: float = 7.5 * _COLUMN_UNIT  # 352.5px — below a third
_NARROW_MAX: float = 11.5 * _COLUMN_UNIT  # 540.5px — below a half
_WIDE_MIN: float = 23.5 * _COLUMN_UNIT  # 1104.5px — essentially full width
# 540.5px to < 1104.5px → medium


WidthTier: TypeAlias = Literal["tiny", "narrow", "medium", "wide"]


def _coerce_weight(raw: str | float | int) -> int | str:
    """Return a CSS-valid font-weight value from FontStyle.weight.

    FontStyle.weight is ``str | float | None``.  SVG and CSS both accept
    numeric weights (int) and keyword weights (``"bold"``, ``"normal"``).
    Cast numeric values to int so SVG emits ``font-weight="500"`` not
    ``font-weight="500.0"``; leave string keywords unchanged.
    """
    if isinstance(raw, str):
        return raw
    return int(raw)


def width_tier(width: float) -> WidthTier:
    """Classify a card's pixel width in the shared four-tier ladder."""
    if width >= _WIDE_MIN:
        return "wide"
    if width >= _NARROW_MAX:
        return "medium"
    if width >= _TINY_MAX:
        return "narrow"
    return "tiny"


def _width_offset(width: float, chart_style_context: ChartStyleContext) -> int:
    """Return the additive level offset for a card at the given pixel width.

    Reads ``title.width_offsets`` from the active resolved style so board-local
    theme overrides flow through. Width breakpoints are engine constants
    (``_TINY_MAX``, ``_NARROW_MAX``, ``_WIDE_MIN``).

    Args:
        width: Pixel width of the card/chart.
        resolved_chart_style: Active ``ChartStyleContext`` for the board.

    Returns:
        Integer offset to add to the base heading level before indexing ``sizes``.
    """
    wo = chart_style_context.title.width_offsets
    tier = width_tier(width)
    if tier == "wide":
        return wo.wide
    if tier == "medium":
        return wo.medium
    if tier == "narrow":
        return wo.narrow
    return wo.tiny


# Anchor level for object titles before adding the width-tier offset.
# Combined with the theme's default width_offsets (tiny:+3, narrow:+1,
# medium:0, wide:0) this yields tiny->H5, narrow->H3, medium->H2, wide->H2.
# Object titles do NOT incorporate board.level - that is the whole point of
# the chart/table/spark vs board-title split.
_OBJECT_TITLE_ANCHOR_LEVEL: int = 2


def chart_title_spec(
    width: float,
    *,
    chart_style_context: ChartStyleContext,
    use_title_family: bool | None = None,
) -> tuple[int, int | str, str]:
    """Return ``(font_size, font_weight, font_family)`` for an object title.

    Used by chart, table, and spark renderers. The title picks an H-slot
    from ``style.title.sizes`` by outer card width tier alone - board nesting
    does not affect the result.

    Reads from the *active* resolved style passed in - not from the global
    theme default, so board-local and chart-local style patches flow through.
    The resolved style has emoji families already baked into every
    ``FontStyle.family`` by ``resolve_style``'s ``_append_emoji_family`` pass.

    H-slot mapping (with default theme width_offsets): see the tier table in the
    module docstring. Deliberately not repeated here — the boundaries are derived
    from the board's column grid and move whenever it does, so a second copy of
    the pixel values is a copy that goes stale.

    Family resolution by width tier:
    - tiny + narrow -> ``resolved_chart_style.font_family`` (body family - sans
      on every shipped theme)
    - medium + wide -> ``resolved_chart_style.title.font.family`` (title-slot
      family - sans on ``stark``, serif on ``default``/``cream``), falling back
      to the body family when the title slot is unset.

    Font weight comes from ``resolved_chart_style.title.compact_weight`` at
    tiny width and ``title.font.weight`` at every other tier.

    Args:
        width:  Outer pixel width of the chart card. Must be the same width
            basis at every call site (chart/table/spark) - otherwise adjacent
            objects can land in different tiers and disagree on title size.
        resolved_chart_style:  Active ``ChartStyleContext`` for the board.
            Produced upstream by ``resolve_style(board_style).charts``.
        use_title_family:  ``True`` forces the title-slot family even on
            narrow/tiny cards (so prose-tagged titles use the theme's title
            family at every width). ``False`` forces the body family at every
            width. ``None`` uses the width default (title-slot family at
            medium/wide, body family at narrow/tiny).

    Returns:
        ``(font_size_px, css_font_weight, css_font_family_string)``
    """
    sizes = chart_style_context.title.sizes
    tier = width_tier(width)
    offset = _width_offset(width, chart_style_context)
    effective_level = max(1, min(_OBJECT_TITLE_ANCHOR_LEVEL + offset, len(sizes)))
    font_size = int(sizes[effective_level - 1])

    # TitleStyle.font is FontStyle (all Optional) — InheritSlot fills from parent;
    # None post-cascade is a cascade bug, not a valid state.
    _chart_title_weight = chart_style_context.title.font.weight
    assert _chart_title_weight is not None, (
        "title.font.weight must be populated by cascade"
    )
    weight = _coerce_weight(
        chart_style_context.title.compact_weight
        if tier == "tiny"
        else _chart_title_weight
    )

    pick_title = (
        (tier in ("medium", "wide")) if use_title_family is None else use_title_family
    )
    body = chart_style_context.font_family
    assert body is not None, "resolved_chart_style.font_family must be populated"
    title_family = chart_style_context.title.font.family
    family = (title_family or body) if pick_title else body
    return font_size, weight, family


def resolve_title_font(
    chart_style_context: ChartStyleContext,
    width: float,
    authored_title_font: FontStyle | None = None,
) -> ResolvedFontStyle:
    """Return a fully-resolved ResolvedFontStyle for an object title at the given width.

    Delegates size/weight/family to chart_title_spec; reads the remaining font
    fields (color, style, decoration, case, line_height) from the cascade-populated
    charts.title.font slot.

    authored_title_font: optional chart-local FontStyle patch (from style.title.font).
        Non-None family/size/weight fields override the width-derived values from
        chart_title_spec — the chart-level title style wins over the width default.
    """
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
    from dbt_charts.core.fonts import font_is_tabular

    font_size, weight, family = chart_title_spec(
        width, chart_style_context=chart_style_context
    )
    if authored_title_font is not None:
        if authored_title_font.family is not None:
            family = authored_title_font.family
        if authored_title_font.size is not None:
            font_size = int(authored_title_font.size)
        if authored_title_font.weight is not None:
            weight = _coerce_weight(authored_title_font.weight)
    tf = chart_style_context.title.font
    assert tf.color is not None, "charts.title.font.color must be populated by cascade"
    assert tf.style is not None, "charts.title.font.style must be populated by cascade"
    assert tf.decoration is not None, (
        "charts.title.font.decoration must be populated by cascade"
    )
    assert tf.case is not None, "charts.title.font.case must be populated by cascade"
    assert tf.line_height is not None, (
        "charts.title.font.line_height must be populated by cascade"
    )
    return ResolvedFontStyle(
        family=family,
        color=tf.color,
        size=font_size,
        weight=weight,
        style=tf.style,
        decoration=tf.decoration,
        case=tf.case,
        line_height=tf.line_height,
        tabular_figures=font_is_tabular(family),
    )


def board_title_spec(
    *,
    level: int,
    resolved_style: ResolvedStyle | None = None,
) -> tuple[int, int | str]:
    """Return ``(font_size, font_weight)`` for a board/page title.

    Board titles are **semantic headings**: the rendered size depends only
    on ``board.level`` (or an authored ``style.title.level`` override).
    Width tiers do not apply — a narrow column does not shrink a board title.
    See ``chart_title_spec`` for the width-responsive object-title path.

    Args:
        level: Heading level for this board title (``board.level``). Indexes
            ``style.title.sizes`` directly: level=1 → sizes[0], etc.
        resolved_style: Optional resolved board style whose title typography
            should override global config.

    Returns:
        ``(font_size_px, css_font_weight)``
    """
    from dbt_charts.core.compile.config import get_theme_style

    title_style = (
        resolved_style.title if resolved_style is not None else get_theme_style().title
    )
    sizes = title_style.sizes
    effective_level = max(1, min(level, len(sizes)))
    font_size = int(sizes[effective_level - 1])

    # TitleStyle.font is FontStyle (all Optional) — InheritSlot fills from parent;
    # None post-cascade is a cascade bug, not a valid state.
    _title_weight = title_style.font.weight
    assert _title_weight is not None, "title.font.weight must be populated by cascade"
    weight = _coerce_weight(_title_weight)
    return font_size, weight


def board_title_markdown(
    title: str,
    *,
    level: int = 1,
    resolved_style: ResolvedStyle | None = None,
) -> tuple[str, float, int | str]:
    """Return ``(markdown, h1_size, font_weight)`` for a board title.

    Formats the title as an h1 heading (``# title``). Callers pass ``h1_size``
    to ``get_compact_style(h1_size=…)`` so the h1 renders at the exact pixel
    size, and ``font_weight`` to ``heading_font_weight`` so the dbt charts weight tier
    is applied. The pixel size comes from ``style.title.sizes`` indexed by the
    semantic level — width does not enter (see ``board_title_spec``).

    Args:
        title: Title text (Jinja already resolved by the caller).
        level: Heading level for this board's title (``board.level``; default 1
               for root boards).

    Returns:
        ``(markdown_string, h1_size, font_weight)`` — pass to
        ``get_compact_style(h1_size=…, heading_font_weight=…)``.
    """
    font_size, weight = board_title_spec(level=level, resolved_style=resolved_style)
    return f"# {title}", float(font_size), weight
