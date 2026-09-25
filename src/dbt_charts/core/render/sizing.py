"""Layout sizing calculation module.

Purpose: Calculate dimensions for layout items using static, non-data-aware
defaults (aspect ratios, theme/config) — no query data, no executor.

Entry Points:
    - calculate_layout_height(layout, ...) -> float
      Returns the total container height for a layout tree.
    - calculate_layout_items(layout, ...) -> None
      Assigns pixel dimensions to all items in a layout tree in place.

Data-aware sizing (render-first Vega sizing, table row counts) lives in
render/layout_sizing.py and injects a HeightProvider callback into these
algorithms at render time.

Inputs:
    - Board with layout but no dimensions

Outputs:
    - Board with calculated width/height on all layout items

Key Principles:
    1. Height is determined by layout AND content type
       - KPIs get smaller heights (100px default)
       - Charts get standard heights (300px default)
       - Titles/content heights are estimated based on text/font
       - In cols: all items have the SAME height (max of required heights)
       - In rows: each item gets its content-appropriate height

    2. Width is determined by layout type
       - In cols: width is divided among items equally (by default)
       - In rows: all items get full width

    3. Padding philosophy
       - Charts have internal padding (handled by Vega-Lite config)
       - Boards have NO padding unless explicitly set in style
       - Root dashboard has page padding (handled in renderer)

    4. Nested layouts follow these rules recursively
       - A nested rows layout inside a cols item gets the full height of that item
       - Items within that nested layout then divide that height

Example:
    cols:
      - chart1           # Gets 50% width, 100% height
      - rows:            # Gets 50% width, 100% height
          - chart2       # Gets 100% width of parent, 50% of parent height
          - chart3       # Gets 100% width of parent, 50% of parent height

    Result with 800x400 container:
    ┌────────────────────┬────────────────────┐
    │                    │      chart2        │
    │      chart1        │   400x200px        │
    │    400x400px       ├────────────────────┤
    │                    │      chart3        │
    │                    │   400x200px        │
    └────────────────────┴────────────────────┘

Dependencies:
    - compile.models.board.normalized (Board, Layout, LayoutItem)
    - compile.config (CompileConfig)

See also:
    - compile/normalize/dispatch.py: produces the Board this module sizes

Cross-module API:
    render/layout_sizing.py imports the following functions as part of the
    HeightProvider injection pattern.  They are sizing algorithm internals
    intentionally exported for use by the data-aware render-time sizing pass:
        calculate_layout_height, calculate_layout_items,
        get_item_content_height
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from PIL import ImageFont

from dbt_charts.core.compile.resolve.style.typography import board_title_spec

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.resolved import (
        ResolvedLayoutItem,
    )
    from dbt_charts.core.compile.models.style.theme import TextStyle
    from dbt_charts.core.render.variables_layout import VariablesLayout
    from mdsvg.fonts import FontFace
    from mdsvg.renderer import SVGRenderer

from dbt_charts.core.compile.config import (
    get_chart_rendering,
    get_theme_style,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    Layout,
    LayoutItem,
    VariableValues,
)
from dbt_charts.core.compile.models.chart.normalized import (
    Chart,
    PieChart,
    SparkBarChart,
    TableChart,
    _CartesianChartFields,
    _GeoChartFields,
)
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedStyle,
    effective_padding as _effective_padding,
)
from dbt_charts.core.compile.models.style.theme import (
    VariablesStyle,
    font_weight_as_css,
)
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.resolve.chart._kwargs import _FAMILY_SLOT
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.compile.resolve.style.tokens import apply_emoji_to_family
from dbt_charts.core.compile.sizing import (
    DEFAULT_GRID_COLUMNS,
    grid_span_width,
    item_grid_span,
    parse_dimension,
    resolve_cols_widths,
    rows_item_width,
)
from dbt_charts.core.compile.template.jinja import resolve_jinja_template
from dbt_charts.core.fonts import get_font_path
from dbt_charts.core.numeric import aspect_ratio_height
from dbt_charts.core.render.variables_layout import WRAP_EPSILON, lay_out_variables
from dbt_charts.core.render.variables_resolve import resolve_controls
from dbt_charts.core.text.case import apply_case

# ============================================================================
# HEIGHT PROVIDER PROTOCOL
# ============================================================================


class HeightProvider(Protocol):
    """Callback that returns the content height for a layout item.

    The static path uses the built-in get_item_content_height (aspect-ratio-
    based, no data access). The render-time path injects a data-aware provider
    from render/layout_sizing.py that renders Vega charts and queries data.
    """

    def __call__(
        self,
        item: LayoutItem,
        card_gap: float,
        gap: float,
        width: float,
        variable_values: dict[str, Any] | None,
        resolved_style: ResolvedStyle,
    ) -> float: ...


def _resolve_height(
    item: LayoutItem,
    card_gap: float,
    gap: float,
    width: float,
    variable_values: dict[str, Any] | None,
    height_provider: HeightProvider | None,
    resolved_style: ResolvedStyle,
) -> float:
    """Dispatch to height_provider if set, else use static get_item_content_height."""
    if height_provider is not None:
        return height_provider(
            item, card_gap, gap, width, variable_values, resolved_style
        )
    return get_item_content_height(
        item,
        card_gap,
        gap,
        width,
        variable_values,
        height_provider=None,
        resolved_style=resolved_style,
    )


def get_chart_content_height(
    chart: Chart | None,
    *,
    width: float | None = None,
    resolved_style: ResolvedStyle,
) -> float:
    """Get the appropriate content height for a chart based on its type.

    Resolution order for plot-style charts (bar, line, area, scatter, …):
      1. chart.height — explicit chart-root override; returned as-is (no clamping).
      2. chart.aspect_ratio — chart-root fallback; height = width / aspect_ratio,
         clamped to theme min_height / max_height.
      3. Per-family theme aspect_ratio (e.g. style.charts.bar.aspect_ratio).
      4. Global theme aspect_ratio (style.charts.aspect_ratio).
      5. chart_rendering.default_chart_height when no width is available.

    SVG-family renderers own their own sizing contract:
      - kpi    → resolved_style.chart_defaults.kpi.default_height (theme cascade)
      - table  → chart_rendering.default_table_height (replaced by row-count sizing at render)
      - callout → 0.0 placeholder (replaced by natural render-first sizing)
      - spark_bar → config-derived upper bound from max_bars/bar.height

    This is the static height calculator. It uses aspect ratios
    and config defaults only — no executor, no data, no rendering.
    Layout slot height (``LayoutItem.layout_height``) wins at render time when
    authored; the render-first pass in ``render/layout_sizing.py`` uses this
    value as a fallback when no explicit slot height is declared.

    Args:
        chart: The compiled chart to get height for
        width: Available width in pixels; enables aspect-ratio-driven sizing
        resolved_style: Board-resolved style; KPI height reads from
            resolved_style.chart_defaults.kpi.default_height.

    Returns:
        Appropriate height in pixels for this chart type
    """
    if chart is None:
        return resolved_style.chart_defaults.default_chart_height

    chart_type = chart.type
    if chart_type == "kpi":
        return float(resolved_style.chart_defaults.kpi.default_height)
    elif chart_type == "table":
        return resolved_style.chart_defaults.default_table_height
    elif chart_type == "callout":
        # Natural-height element: render-first sizing supplies the real height at
        # render time; the static estimate is just a placeholder.
        return 0.0
    elif chart_type == "spark_bar":
        # Static upper bound using max_bars. Render-first data-aware sizing
        # in layout_sizing.py overrides this with the actual natural height.
        # Includes title_height when chart.title is set to match the renderer's
        # body + title addition (spark_bar.py:265-269).
        sb = resolved_style.chart_defaults.spark_bar
        body = sb.max_bars * (sb.bar.height + sb.bar.padding) + sb.bar.padding
        # SparkBarChart has title via _SharedChartFields; isinstance narrows for pyright.
        has_title = isinstance(chart, SparkBarChart) and chart.title
        return (
            body + get_chart_rendering().spark_bar.title_height if has_title else body
        )

    # After kpi/table/callout/spark_bar early returns, remaining types are
    # _CartesianChartFields, _GeoChartFields, and PieChart —
    # all of which declare height/min_height/max_height.
    if not isinstance(chart, (_CartesianChartFields, _GeoChartFields, PieChart)):
        return resolved_style.chart_defaults.default_chart_height

    # Step 1: explicit chart-root height wins everything (no clamping).
    if chart.height is not None:
        return float(chart.height)

    # Steps 2–4: aspect-ratio-driven sizing for plot-style charts.
    if width is not None and width > 0:
        # Resolve clamps: start from board/theme resolved_style, override with
        # chart-root min_height / max_height when explicitly set by the author.
        # Cartesian families declare min/max_height; pie and geo size from the
        # theme's per-family aspect only.
        chart_min_height = (
            chart.min_height if isinstance(chart, _CartesianChartFields) else None
        )
        chart_max_height = (
            chart.max_height if isinstance(chart, _CartesianChartFields) else None
        )
        chart_aspect_ratio = (
            chart.aspect_ratio
            if isinstance(chart, (_CartesianChartFields, _GeoChartFields))
            else None
        )
        min_h = (
            float(chart_min_height)
            if chart_min_height is not None
            else float(resolved_style.chart_defaults.min_height)
        )
        max_h = (
            float(chart_max_height)
            if chart_max_height is not None
            else float(resolved_style.chart_defaults.max_height)
        )
        # Step 2: chart-root aspect_ratio (validated gt=0 at compile time).
        if chart_aspect_ratio is not None:
            return aspect_ratio_height(width, chart_aspect_ratio, min_h, max_h)
        # Steps 3–4: per-family resolved-style aspect_ratio, then global default.
        # _FAMILY_SLOT routes type aliases (donut→pie, map→geoshape,
        # bubble_map→point_map) to the family that actually carries the
        # style — ResolvedChartDefaults declares no attribute under the
        # alias name itself, so this must go through the slot map, not the
        # bare chart_type.
        type_config = getattr(resolved_style.chart_defaults, _FAMILY_SLOT[chart_type])
        aspect = (
            float(type_config.aspect_ratio)
            if type_config.aspect_ratio
            else float(resolved_style.chart_defaults.aspect_ratio)
        )
        if aspect > 0:
            return aspect_ratio_height(width, aspect, min_h, max_h)

    return resolved_style.chart_defaults.default_chart_height


def is_item_collapsed_summary(
    item: LayoutItem,
    variable_values: dict[str, Any] | None,
) -> bool:
    return bool(
        item.details_variable
        and item.details_summary
        and not _is_details_item_expanded(item, variable_values)
    )


def _is_details_item_expanded(
    item: LayoutItem, variables: dict[str, Any] | None
) -> bool:
    """Check if a details item is expanded based on variable value or default."""
    if variables and item.details_variable in variables:
        return str(variables[item.details_variable]).lower() == "true"
    # Fall back to default from board meta
    if item.board and item.board.meta:
        return bool(item.board.meta.get("details_expanded_default", False))
    return False


def details_chrome_height(
    item: LayoutItem | ResolvedLayoutItem,
    gap: float,
    card_gap: float,
    resolved_style: ResolvedStyle,
) -> float:
    """Vertical overhead the details chrome adds above the nested board content.

    Returns summary_height + gap + card_gap for details items, 0 otherwise.
    Both the sizing pass and render pass must use this so they stay in sync.
    """
    if not (item.details_variable and item.details_summary):
        return 0.0
    return float(resolved_style.layout.details.summary_height) + gap + card_gap


# mdsvg.Style fields owned entirely by mdsvg (spacing/layout/image/measurement
# constants). These are NOT driven by the dct theme cascade. New mdsvg fields must
# be bucketed here (structural) or wired into get_compact_style (theme-driven).
# The dct<->mdsvg contract test in tests/core/test_text_box_style_cascade.py
# enforces this: every mdsvg.Style field is either mapper-fed or in this set.
def _mdsvg_case(case: str | None, scope: str) -> str:
    """Map a FontStyle ``case`` value to what mdsvg paints (upper/lower only).

    mdsvg is dependency-light and only does upper/lower string transforms. The
    richer dct cases (``sentence``/``title``/``slug``/``camel``) rely on the
    ``titlecase`` dep and would silently no-op in mdsvg, so reject them loudly
    rather than ship a dead knob.
    """
    if case is None or case == "none":
        return ""
    if case in {"upper", "lower"}:
        return case
    raise CompilationError(
        f"style.text.{scope}.font.case={case!r} is not supported for markdown "
        f"prose; use 'upper' or 'lower'."
    )


MDSVG_STRUCTURAL_FIELDS: frozenset[str] = frozenset(
    {
        # Font metrics / text measurement
        "mono_font_family",  # mdsvg detects mono font; dct passes font_family separately
        "link_underline",  # always True; no dct token
        "char_width_ratio",
        "bold_char_width_ratio",
        "italic_char_width_ratio",
        # Heading scales (unused — we pass absolute h1..h6 sizes instead)
        "h1_scale",
        "h2_scale",
        "h3_scale",
        "h4_scale",
        "h5_scale",
        "h6_scale",
        # Em-of-heading margin fallbacks (unused — dct drives heading_margin_top_px /
        # heading_margin_bottom_px so the after-heading gap stays one body line
        # regardless of H level)
        "heading_margin_top",
        "heading_margin_bottom",
        # Layout / spacing constants (not theme-cascaded)
        "list_indent",
        "list_item_spacing",
        "code_block_padding",
        "code_block_overflow",
        # Code size as a fraction of its surrounding text. mdsvg owns the ratio;
        # the theme sets no code font size, so this is what actually sizes code.
        # Promote to a theme token only if a theme needs a different ratio —
        # style.text.code.font.size is already the per-board absolute escape hatch.
        "code_font_scale",
        # Inline code chip geometry — not in the dbt charts theme yet; mdsvg owns
        # the defaults (3px padding, 3px radius). Add to the mapper when dct
        # exposes style.text.code.inline_padding / .inline_radius theme tokens.
        "inline_code_padding",
        "inline_code_border_radius",
        "blockquote_padding",
        "table_cell_padding",
        "hr_height",
        # Image layout constants
        "image_width",
        "image_height",
        "image_fallback_aspect_ratio",
        "image_enforce_aspect_ratio",
    }
)


def get_compact_style(
    resolved_style: ResolvedStyle | None = None,
    text_align: Literal["left", "center", "right"] = "left",
    heading_font_weight: int | str | None = None,
    font_family: str | None = None,
    h1_size: float | None = None,
    heading_margin_scale: Literal["prose", "chrome"] = "prose",
) -> Any:
    """Get a compact mdsvg Style with reduced heading margins (theme-derived colors).

    Thin wrapper over :func:`compact_style_kwargs` (the dct→mdsvg color/size mapper);
    the contract test reads the fed-field set from that helper directly.
    """
    from mdsvg import Style

    return Style(
        **compact_style_kwargs(
            resolved_style,
            text_align,
            heading_font_weight,
            font_family,
            h1_size,
            heading_margin_scale,
        )
    )


def compact_style_kwargs(
    resolved_style: ResolvedStyle | None = None,
    text_align: Literal["left", "center", "right"] = "left",
    heading_font_weight: int | str | None = None,
    font_family: str | None = None,
    h1_size: float | None = None,
    heading_margin_scale: Literal["prose", "chrome"] = "prose",
) -> dict[str, Any]:
    """Build the ``mdsvg.Style`` kwargs from the resolved theme — the dct→mdsvg mapper.

    The default mdsvg style has large heading margins (1.5em top, 0.5em bottom)
    which produces ~2x more vertical space than HTML. This compact style
    reduces margins for tighter dashboard/UI layouts.

    Heading sizes (h1–h6) are pinned to absolute pixel values from
    ``style.title.sizes`` rather than computed as multiples of the body font
    size. Heading weight defaults to ``style.title.font.weight``; heading
    color comes from ``style.title.font.color``.

    All prose colors derive from resolved theme tokens — no hardcoded table.
    Paragraph spacing is derived from the body rhythm:
    ``font_size * line_height * 0.5``.

    Args:
        resolved_style: Optional ResolvedStyle for color resolution. Falls back to
            the global config default when None.
        heading_font_weight: Override the heading font weight. Board titles pass
            the tier-specific weight (600 narrow, 500 medium/wide).
        font_family: Override the body font family used by mdsvg.
        h1_size: Override the h1 pixel size. Board titles pass the
            level-resolved pixel size from ``board_title_spec`` so the rendered
            h1 lands exactly on the semantic-level target. When ``None``, h1
            uses ``style.title.sizes[0]`` like the other levels.

    Returns:
        A dict of ``mdsvg.Style`` keyword arguments. Every key is an
        ``mdsvg.Style`` field; the dct<->mdsvg contract test asserts each
        ``Style`` field is fed here or listed in ``MDSVG_STRUCTURAL_FIELDS``.
    """
    from dbt_charts.core.compile.resolve.style.typography import _coerce_weight

    _rs = resolved_style or resolve_style(get_theme_style())

    _text_font_size = _rs.text.font.size
    assert _text_font_size is not None, "style.text.font.size must be configured"
    # Heading margins are in line-height units, so they need a font size to resolve
    # against. "prose" is right for a markdown heading inside a text block — the gap
    # under it should equal one line of the prose it introduces. "chrome" is right for
    # a board title, which labels a card that may hold a chart, a table, or nothing
    # textual at all: its spacing must not move when prose is resized. Chart card
    # titles already work this way (size varies by width tier, gap does not).
    _chrome_font_size = _rs.font.size
    assert _chrome_font_size is not None, "style.font.size must be configured"
    _chrome_line_height = _rs.font.line_height
    assert _chrome_line_height is not None, "style.font.line_height must be configured"
    _text_font_weight = _rs.text.font.weight
    assert _text_font_weight is not None, "style.text.font.weight must be configured"
    _text_line_height = _rs.text.font.line_height
    assert _text_line_height is not None, (
        "style.text.font.line_height must be configured"
    )
    # One line of the chosen scale, in px. A heading margin of 1.5 means "one and a
    # half lines of the text this heading belongs to", so size and leading must come
    # from the same scale — mixing prose size with chrome leading (or vice versa)
    # silently produces a unit that matches neither.
    _heading_margin_unit = (
        float(_text_font_size) * float(_text_line_height)
        if heading_margin_scale == "prose"
        else float(_chrome_font_size) * float(_chrome_line_height)
    )
    _title_line_height = _rs.title.font.line_height
    assert _title_line_height is not None, (
        "style.title.font.line_height must be configured"
    )

    _text_font_family = _rs.text.font.family
    assert _text_font_family is not None, "style.text.font.family must be configured"
    _default_family = apply_emoji_to_family(_text_font_family, _rs.emoji_mode)

    _heading_font_family = _rs.title.font.family
    assert _heading_font_family is not None, (
        "style.title.font.family must be configured"
    )
    _heading_family = apply_emoji_to_family(_heading_font_family, _rs.emoji_mode)

    _text_font_color = _rs.text.font.color
    assert _text_font_color is not None, "style.text.font.color must be configured"

    title_sizes = _rs.title.sizes
    if len(title_sizes) != 6:
        raise ValueError(
            f"style.title.sizes must have 6 entries (H1-H6); got {len(title_sizes)}"
        )

    effective_weight = (
        heading_font_weight
        if heading_font_weight is not None
        else _coerce_weight(_rs.title.font.weight or 500)
    )
    text_weight: int | str | None = _coerce_weight(_text_font_weight)
    if str(text_weight).lower() in {"400", "normal"}:
        text_weight = None

    # Code box tokens from theme cascade
    _code = _rs.text.code
    _bq = _rs.text.blockquote
    _bold = _rs.text.bold
    # code_color: prefer code font color if set; fall back to board chrome ink
    _code_color: str = _code.font.color or _rs.font.color

    # table header background: prefer table.header.background, fall back to canvas
    _table_header_bg: str = _rs.chart_defaults.table.header.background or _rs.background

    kwargs: dict[str, Any] = {
        "font_family": font_family if font_family is not None else _default_family,
        # Headings (board titles and markdown H1-H6 alike) read the title
        # family, independent of whichever family `font_family` above resolved
        # to for body/paragraph text — the same split the size, color, and
        # weight keys below already make for `heading_*`.
        "heading_font_family": _heading_family,
        "base_font_size": float(_text_font_size),
        "font_weight": text_weight,
        "line_height": float(_text_line_height),
        # Block margins from theme: font_size × line_height × margin_* (in lh units).
        # mdsvg ≥ 1.0 skips paragraph_spacing on heading-adjacent transitions and
        # collapses H↔H margins to max — so heading_margin_*_px are the literal
        # visible space above / below a heading.
        "heading_margin_top_px": _heading_margin_unit
        * float(_rs.text.heading.margin_top),
        "heading_margin_bottom_px": _heading_margin_unit
        * float(_rs.text.heading.margin_bottom),
        "paragraph_spacing": float(_text_font_size)
        * float(_text_line_height)
        * float(max(_rs.text.paragraph.margin_top, _rs.text.paragraph.margin_bottom)),
        "text_color": _text_font_color,
        "heading_color": _rs.title.font.color,
        "link_color": _rs.accent,
        "code_color": _code_color,
        "code_background": _code.background,
        "code_block_border_color": _code.border.color,
        "code_block_border_width": float(_code.border.width),
        "code_block_border_radius": float(_code.border.radius),
        "code_highlight": _code.highlight,
        "code_theme": _code.theme,
        # Code font axes
        "code_font_family": _code.font.family or "",
        "code_font_weight": (
            font_weight_as_css(_code.font.weight)
            if _code.font.weight is not None
            else ""
        ),
        "code_font_style": _code.font.style or "",
        "code_font_size": (
            float(_code.font.size) if _code.font.size is not None else 0.0
        ),
        "code_font_decoration": _code.font.decoration or "",
        "code_font_case": _mdsvg_case(_code.font.case, "code"),
        "blockquote_color": _bq.font.color or _rs.font.color,
        "blockquote_border_color": _bq.border.color,
        "blockquote_border_width": float(_bq.border.width),
        "blockquote_border_radius": float(_bq.border.radius),
        "blockquote_background": _bq.background,
        # Blockquote font axes
        "blockquote_font_family": _bq.font.family or "",
        "blockquote_font_weight": (
            font_weight_as_css(_bq.font.weight) if _bq.font.weight is not None else ""
        ),
        "blockquote_font_style": _bq.font.style or "",
        "blockquote_font_size": (
            float(_bq.font.size) if _bq.font.size is not None else 0.0
        ),
        "blockquote_font_decoration": _bq.font.decoration or "",
        "blockquote_font_case": _mdsvg_case(_bq.font.case, "blockquote"),
        "table_border_color": _rs.text.rule.color,
        "table_header_background": _table_header_bg,
        "hr_color": _rs.text.rule.color,
        "text_align": text_align,
        # Top-align letterboxed markdown images (default xMidYMid centers them).
        "image_preserve_aspect_ratio": "xMidYMin meet",
        "h1_size": float(h1_size) if h1_size is not None else float(title_sizes[0]),
        "h2_size": float(title_sizes[1]),
        "h3_size": float(title_sizes[2]),
        "h4_size": float(title_sizes[3]),
        "h5_size": float(title_sizes[4]),
        "h6_size": float(title_sizes[5]),
        "heading_font_weight": effective_weight,
        "heading_line_height": float(_title_line_height),
        "bold_font_weight": font_weight_as_css(_bold.weight),
        # dbt charts opts in; mdsvg leaves it off for other consumers.
        "avoid_runts": True,
    }

    return kwargs


def body_text_font_family(resolved_style: ResolvedStyle) -> str:
    """Return the resolved body markdown family."""
    family = resolved_style.text.font.family
    assert family is not None, "style.text.font.family must be configured"
    return family


def title_font_family(resolved_style: ResolvedStyle) -> str:
    """The family a board title is drawn in.

    Every board title — prose or not — is drawn as a heading
    (``board_title_markdown`` always emits ``# {title}``), and
    ``style.title.font.family`` is the one token that names a heading's
    family; a board title never draws in the body family.

    Measuring and drawing have to ask this once and get the same answer —
    reserving a column in one family and painting the title in another is the
    defect this exists to prevent.
    """
    family = resolved_style.title.font.family
    assert family is not None, "style.title.font.family must be configured"
    return family


# One sentence of ordinary English prose, used to calibrate character width.
# OS/2 xAvgCharWidth averages over a font's whole glyph repertoire rather than
# over letter frequency, so it runs well wide of real text -- enough to turn a
# 66-character target into low eighties delivered.
_PROSE_SAMPLE = (
    "North America's primary issue is insufficient MQL supply, not a regional "
    "Stage 0 target miss or a broad loss of selling capacity."
)


def _prose_char_ratio(face: FontFace) -> float:
    """Mean character width of real prose, as a fraction of font size.

    Measured from the same face the renderer paints with, so the width a column
    is sized to and the width its lines are broken at agree.
    """
    from dbt_charts.core.font_measure import measurer_for_face

    measurer = measurer_for_face(face)
    return measurer.measure(_PROSE_SAMPLE, 1.0) / len(_PROSE_SAMPLE)


def max_chars_to_px(face: FontFace, font_size: float, max_chars: int) -> float:
    """Pixel width of a ``max_chars``-character readable measure for a face."""
    return _prose_char_ratio(face) * font_size * max_chars


def get_markdown_text_height(
    text: str | None,
    width: float,
    variable_values: dict[str, Any] | None = None,
    *,
    text_style: TextStyle,
    resolved_style: ResolvedStyle,
    allow_raw_html: bool = False,
) -> float:
    """Height the renderer will draw this markdown at, measured by rendering it.

    Delegates rather than reimplementing: column height is the output of a
    penalty-driven packer whose breaks depend on widow, orphan and
    keep-with-next costs, and a second implementation would drift from it the
    first time a penalty changed. A sizing/render disagreement shows up as
    clipped or floating text rather than a test failure.
    """
    if not text:
        return 0.0

    from dbt_charts.core.compile.template.jinja import resolve_jinja_template
    from dbt_charts.core.render.prose import render_prose_svg

    resolved_content = resolve_jinja_template(text, variable_values or {}, strict=False)
    _, height = render_prose_svg(
        resolved_content, width, text_style, resolved_style, allow_raw_html
    )
    return height


def get_title_height(
    title: str | None,
    width: float,
    variable_values: dict[str, Any] | None = None,
    level: int = 1,
    *,
    resolved_style: ResolvedStyle | None = None,
) -> float:
    """Get the actual height needed for a title by measuring it.

    Uses mdsvg's measure() to calculate height for the title rendered
    as a markdown heading, accounting for word-wrapping and font sizing.

    Args:
        title: The title text
        width: Available width for the title (drives tier selection)
        variable_values: Variable values to resolve Jinja against
        resolved_style: Optional resolved board style for title typography measurement

    Returns:
        Actual height in pixels for the rendered title
    """
    if not title:
        return 0.0

    from dbt_charts.core.compile.resolve.style.typography import board_title_markdown
    from dbt_charts.core.font_measure import markdown_font_faces
    from mdsvg import measure as measure_markdown

    # Resolve any Jinja templates using variable defaults
    resolved_title = resolve_jinja_template(title, variable_values or {}, strict=False)

    # Measure the markdown to get actual dimensions (using compact style).
    # text_align is intentionally omitted: mdsvg measure() only computes
    # line-wrapped height, which is independent of horizontal alignment.
    _rs = resolved_style or resolve_style(get_theme_style())

    # Apply the same case transform the renderer applies at the same width,
    # so measure and draw see identical text and width — the two invariants
    # that keep the reserved title box equal to the drawn title box.
    _title_case = _rs.title.font.case
    if _title_case is not None:
        resolved_title = apply_case(resolved_title, _title_case)

    markdown_title, h1_size, heading_weight = board_title_markdown(
        resolved_title, level=level, resolved_style=resolved_style
    )
    # Ask title_font_family, same as title_renderer does — a hand-rolled branch
    # here is exactly the drift that let this measurement disagree with the
    # family render_title actually paints the title in.
    font_family = title_font_family(_rs)

    title_style = get_compact_style(
        _rs,
        h1_size=h1_size,
        heading_font_weight=heading_weight,
        font_family=font_family,
        # Must match render_title exactly — this is the measure half of the same
        # title block; a mismatch desyncs reserved height from drawn height.
        heading_margin_scale="chrome",
    )
    size = measure_markdown(
        markdown_title,
        width=width,
        padding=0.0,
        style=title_style,
        fonts=markdown_font_faces(font_family, title_style),
    )

    return size.height


def title_renderer(
    resolved_style: ResolvedStyle,
    level: int,
    text_align: Literal["left", "center", "right"] = "left",
) -> tuple[SVGRenderer, str]:
    """The mdsvg renderer a board title is drawn with, and the family it resolves to.

    One construction shared by every question asked about a title — what it looks
    like, where its baseline lands, which part of its block is text — because they
    have to be asked of the same renderer. Built separately they can disagree
    about the face or the heading size, and the answers then describe a title
    nobody drew.

    ``font_family`` is always the title family, unconditionally: the title's own
    ink is always a heading, which paints via ``Style.heading_font_family``
    regardless of ``font_family`` — but a title's markdown can still carry a
    non-heading span (an emphasis run, or a second block on a multi-line
    title), and that span paints through mdsvg's ``.md-*-text`` rule, which
    ``font_family`` does control. Leaving it unset there let a measure and a
    paint of the SAME non-heading span disagree on family whenever the
    caller's own choice of ``font_family`` differed from the title family —
    the same defect this whole module exists to prevent, just one rule over.
    """
    from dbt_charts.core.font_measure import markdown_font_faces
    from mdsvg.renderer import SVGRenderer

    font_size, heading_weight = board_title_spec(
        level=level, resolved_style=resolved_style
    )
    family = title_font_family(resolved_style)
    style = get_compact_style(
        resolved_style,
        text_align=text_align,
        h1_size=font_size,
        heading_font_weight=heading_weight,
        font_family=family,
        # Title spacing is chrome: it must not grow when body prose is resized.
        heading_margin_scale="chrome",
    )
    return SVGRenderer(style=style, fonts=markdown_font_faces(family, style)), family


def title_baseline_offset(resolved_style: ResolvedStyle, level: int) -> float:
    """How far below a title block's top edge its first baseline sits.

    Asked of the renderer that draws the title rather than derived from the block
    height, because the two only stay in step by construction. Anything aligning
    itself to a board title — the controls beside it in the title-inline band —
    needs the number the title was actually drawn with.
    """
    # Level 1 is what gets asked, whatever the board's level: board_title_markdown
    # always emits `# {title}`, and title_renderer has already baked the
    # level-resolved size into the style's h1. Passing the board level here would
    # read a different rung of the heading ramp than the title was drawn at —
    # which past h6, where board_title_spec clamps and the ramp does not, is a
    # different number.
    return title_renderer(resolved_style, level)[0].heading_baseline(1)


def title_line_box(
    block_height: float,
    level: int,
    resolved_style: ResolvedStyle,
) -> tuple[float, float]:
    """The ``(top, height)`` of the text inside a title block of ``block_height``.

    A heading block is a top margin, the line boxes, and a smaller bottom margin.
    The margins are layout — the rhythm between the heading and what surrounds it
    — and only the middle is text, which is what a selection mark should trace.

    ``block_height`` must be the *measured* height, before
    ``max(..., style.title.min_height)``: that clamp only ever pads the bottom, so
    a clamped height would stretch the span below the text it describes.

    The title text does not enter. ``board_title_spec`` sizes a board title from
    its semantic level alone, so the span is the same whatever the title says.
    """
    # Level 1 for the same reason as `title_baseline_offset`: the title is drawn
    # as an h1 whose size the renderer already carries.
    return title_renderer(resolved_style, level)[0].heading_line_box(1, block_height)


# ============================================================================
# VARIABLE CONTROLS HEIGHT
# ============================================================================

# Minimum title column width when sharing a row with variables so titles never
# collapse to zero when the variable strip is very wide.
_MIN_TITLE_INLINE_TITLE_COLUMN_PX = 48.0
# Hard ceiling on title column width as a fraction of the band's inner width.
# At 0.8, pathologically long titles wrap before they can eliminate the
# variables column — the strip stays parseable even when authors give a board
# a 60-character title and 7 filters.
_TITLE_INLINE_TITLE_MAX_INNER_RATIO = 0.8
_TITLE_INLINE_VARIABLE_MAX_ROWS = 2

# Strip a leading `#+\s+` markdown heading prefix without eating standalone
# `#` characters or trailing punctuation — `lstrip("# ")` would mangle titles
# like "#1 Product" or "## Q3 ##".
_HEADING_PREFIX_RE = re.compile(r"^#+\s+")


def _measure_title_single_line_width(
    title: str | None,
    variable_values: dict[str, Any] | None,
    resolved_style: ResolvedStyle,
    level: int,
) -> float:
    """Width the title text needs to render on a single line at its natural size.

    Measured in the typography the title will actually be drawn in: the size
    ``board_title_spec`` gives this board at this level, in the family the board's
    resolved title style names. Reading the *default* theme's tier here instead
    reserved a column sized for a board nobody was rendering — a board that
    enlarges its title then drew that title straight under the controls the
    band packs against the opposite edge.

    mdsvg's measure() can't be used here — it returns the constraint width when
    passed an unbounded width, not the natural content extent.
    """
    if not title:
        return 0.0

    resolved = resolve_jinja_template(title, variable_values or {}, strict=False)
    # Strip a leading `#+\s+` markdown heading prefix without eating standalone
    # `#` characters or trailing punctuation — `lstrip("# ")` would mangle
    # titles like "#1 Product" or "## Q3 ##".
    plain = _HEADING_PREFIX_RE.sub("", resolved).strip()

    font_size, _weight = board_title_spec(level=level, resolved_style=resolved_style)
    family = title_font_family(resolved_style)
    return float(_load_title_font(float(font_size), family).getlength(plain))


@functools.lru_cache(maxsize=16)
def _load_title_font(size_px: float, family: str) -> ImageFont.FreeTypeFont:
    """Load the title font for ``family`` at the requested size, cached.

    `_measure_title_single_line_width` is called per-board during both sizing
    and render; without caching, ImageFont.truetype reparses the .ttf file
    on every call. The cache is bounded — `size_px` comes from the title
    tier ramp (6 entries) plus the level-driven offsets, over the handful of
    families we vendor a measurable board for.
    """
    return ImageFont.truetype(get_font_path(family), size_px)


def resolve_title_variables_inline_widths(
    inner: float,
    resolved_style: ResolvedStyle,
    visible_variables: dict[str, Variable],
    title: str | None,
    variable_values: dict[str, Any] | None,
    level: int,
) -> tuple[float, float]:
    """Split inner title+variables width into (title_w, variables_w).

    Title precedence: reserve the title's measured single-line width first,
    give the rest to variables, and let the variables strip flex-wrap into
    multiple rows when its share is too narrow for one row. ``compute_
    variable_controls_height`` handles the multi-row height so the band
    grows correctly.

    ``title_inline_title_max_width`` (theme) still caps the title when > 0;
    a hard ceiling of 80% of inner protects pathologically long titles from
    eliminating the variables column entirely (title wraps before that).

    ``variable_values`` are the values this board is being drawn for, not the
    compiled defaults: a Jinja title resolves against them, and so does the
    control the other column has to hold.
    """
    variables_style = resolved_style.variables
    col_gap = float(variables_style.gap)
    if not visible_variables:
        return max(inner, 1.0), 0.0

    # Title's natural single-line width + a small breathing-room pad so the
    # measured width doesn't wrap on sub-pixel rendering jitter.
    natural = _measure_title_single_line_width(
        title, variable_values, resolved_style, level
    )
    natural_with_pad = (
        natural + 8.0 if natural > 0 else _MIN_TITLE_INLINE_TITLE_COLUMN_PX
    )

    # Hard ceiling: never let the title eat more than this fraction of inner,
    # even when natural width is larger — keeps the variables column wide
    # enough that authors see their filters before having to wrap.
    title_w = min(natural_with_pad, inner * _TITLE_INLINE_TITLE_MAX_INNER_RATIO)
    title_w = max(title_w, _MIN_TITLE_INLINE_TITLE_COLUMN_PX)

    # Theme-level override still wins when set.
    cap = float(variables_style.title_inline_title_max_width)
    if cap > 0.0:
        title_w = min(title_w, cap)

    vars_w = max(inner - title_w - col_gap, 1.0)
    return title_w, vars_w


def _variables_layout(
    variable_defs: dict[str, Variable],
    width: float,
    variable_values: dict[str, Any] | None,
    variables_style: VariablesStyle,
) -> VariablesLayout:
    """Lay the controls out the way the strip renderer will.

    Sizing and rendering call the same engine, so a band and its controls can no
    longer be measured two different ways. They are not measured from identical
    *inputs*, though: this pass has no executor, so a widget the control's own
    query would refine is invisible here. Values are not part of that gap —
    callers pass the values the board is being drawn for, because a control is
    as wide as the value it displays.
    """
    return lay_out_variables(
        [
            control.spec
            for control in resolve_controls(
                variable_defs, variable_values or {}, None, variables_style
            )
        ],
        width,
        variables_style,
    )


def compute_variable_controls_height(
    variable_defs: dict[str, Variable],
    width: float,
    variable_values: dict[str, Any] | None,
    variables_style: VariablesStyle,
) -> float:
    """Height of the band these controls need at ``width``."""
    return _variables_layout(
        variable_defs, width, variable_values, variables_style
    ).height


def should_use_title_inline_band(
    title: str | None,
    visible_variables: dict[str, Variable],
    content_width: float,
    resolved_style: ResolvedStyle,
    card_padding: float,
    variable_values: dict[str, Any] | None,
    level: int,
) -> bool:
    """Return whether variables fit the compact title-inline band contract.

    Fitting means both dimensions. Rows alone is not a fit test: the layout
    engine places the first control of a row whatever its width, so a single
    control too wide for the column still reports one row — and the band then
    packs it against the far edge of a column it does not fit in.
    """
    variables_style = resolved_style.variables
    if variables_style.position != "title-inline" or not title or not visible_variables:
        return False

    inner = max(content_width - 2 * card_padding, 1.0)
    _title_w, vars_w = resolve_title_variables_inline_widths(
        inner, resolved_style, visible_variables, title, variable_values, level
    )
    layout = _variables_layout(
        visible_variables, vars_w, variable_values, variables_style
    )
    if layout.rows > _TITLE_INLINE_VARIABLE_MAX_ROWS:
        return False
    widest = max(box.x + box.width for box in layout.boxes)
    return widest <= vars_w + WRAP_EPSILON


def compute_title_variables_inline_baseline_layout(
    title_h: float,
    vars_h: float,
    title_baseline: float,
    label_font_size: float,
    label_font_family: str,
    pad: float,
) -> tuple[float, float, float]:
    """Baseline-aligned layout for the title-inline band.

    Returns ``(title_dy, vars_dy, band_h)`` — the y-translation each column needs
    to land its first text baseline on the same shared baseline, plus the total
    band height that results.

    Both baselines are derived, not measured-and-frozen. ``title_baseline`` comes
    from the renderer that draws the title (``title_baseline_offset``), and the
    label's comes from its own font: a control label is vertically centered in its
    flex row, so its content box straddles the center and the baseline falls half
    an ascent above the middle and half a descent below — ``(ascent - descent)/2``
    past the center. A ratio fitted to one font at one size is wrong for every
    other, which is what a band drifting out of alignment across font tiers was.

    Both columns are then shifted so the deeper baseline becomes the shared
    target, which guarantees neither column extends above the band's top edge.

    Args:
        pad: Bottom padding below the band; comes from
            ``resolved_style.variables.title_inline_band_bottom_pad``.
    """
    from dbt_charts.core.font_measure import centered_baseline_offset

    vars_baseline = vars_h / 2.0 + centered_baseline_offset(
        label_font_family, label_font_size
    )
    target = max(title_baseline, vars_baseline)
    title_dy = target - title_baseline
    vars_dy = target - vars_baseline
    band_h = max(title_dy + title_h, vars_dy + vars_h) + pad
    return title_dy, vars_dy, band_h


def compute_title_variables_inline_band_height(
    board: Board,
    content_width: float,
    variable_values: dict[str, Any] | None,
) -> float:
    """Band height for ``variables.position: title-inline``.

    Computes the height the baseline-aligned band needs to fit both columns.
    Delegates to :func:`compute_title_variables_inline_baseline_layout` so the
    sizing pass and the render pass share one source of truth.
    """
    vs = board.resolved_style.variables
    card_pad = float(board.resolved_style.frame.card_padding)
    inner = max(content_width - 2 * card_pad, 1.0)
    _, vars_w = resolve_title_variables_inline_widths(
        inner,
        board.resolved_style,
        board.visible_variables,
        board.title,
        variable_values,
        board.level,
    )
    if not board.title:
        return 0.0
    # Measure at inner (the full band width) so the title's measured height
    # matches the height _render_title_svg produces at that same width.
    # The narrower title column (vars_w companion) would over-reserve when
    # a case transform widens the title past its wrap threshold.
    title_h = max(
        get_title_height(
            board.title,
            inner,
            variable_values,
            level=board.level,
            resolved_style=board.resolved_style,
        ),
        float(board.resolved_style.title.min_height),
    )
    if not board.visible_variables:
        return title_h
    var_h = compute_variable_controls_height(
        board.visible_variables, vars_w, variable_values, vs
    )
    assert vs.font.size is not None, "style.variables.font.size must be configured"
    assert vs.font.family is not None, "style.variables.font.family must be configured"
    _title_dy, _vars_dy, band_h = compute_title_variables_inline_baseline_layout(
        title_h,
        var_h,
        title_baseline_offset(board.resolved_style, board.level),
        float(vs.font.size),
        vs.font.family,
        float(vs.title_inline_band_bottom_pad),
    )
    return band_h


def get_item_content_height(
    item: LayoutItem,
    card_gap: float,
    gap: float,
    width: float = 400.0,
    variable_values: dict[str, Any] | None = None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> float:
    """Get the content-appropriate height for a single layout item (static).

    This is the static height calculator using aspect ratios and config
    defaults only. No executor, no data, no rendering. The render-time path
    uses a HeightProvider callback instead.

    Args:
        item: The layout item
        card_gap: Gap between cards (inter-item spacing, applied N-1 times)
        gap: Gap between items (used for nested layouts)
        width: Available width for the item (used for text wrapping estimates)
        variable_values: Variable values to resolve Jinja against
        resolved_style: Board-resolved style for cascade-aware height calculations.

    Returns:
        Appropriate height for this item's content
    """
    if item.type == "chart" and item.chart:
        card_pad = float(resolved_style.frame.card_padding)
        if item.chart.type == "callout":
            return get_chart_content_height(
                item.chart, width=width, resolved_style=resolved_style
            )
        # Vega-family charts render at full item width with card_pad as internal
        # Vega padding; SVG-family types ignore the width parameter anyway.
        # The + 2*card_pad accounts for Vega's top+bottom padding in its output height.
        return (
            get_chart_content_height(
                item.chart, width=width, resolved_style=resolved_style
            )
            + 2 * card_pad
        )

    elif item.type == "board" and item.board:
        # Details (collapsible section): collapsed = summary bar only
        if is_item_collapsed_summary(item, variable_values):
            return float(resolved_style.layout.details.summary_height)

        # For nested boards, calculate their content height using the nested board's
        # own resolved_style (each board can have its own theme cascade).
        nested_board = item.board
        nested_rs = nested_board.resolved_style

        content_width, nested_non_layout_height, child_gap = (
            nested_board_sizing_context(nested_board, width, card_gap, variable_values)
        )

        nested_height = nested_non_layout_height
        # Add layout content height (only if there are items)
        if nested_board.layout.items:
            nested_height += calculate_layout_height(
                nested_board.layout,
                card_gap,
                gap=child_gap,
                min_height=0,
                available_width=content_width,
                variable_values=variable_values,
                height_provider=height_provider,
                resolved_style=nested_rs,
            )

        # Add effective padding and margin to total height
        nested_height += _effective_padding(nested_rs).vertical + (
            nested_rs.margin.vertical if nested_rs.margin else 0.0
        )

        content_h = nested_height

        # Expanded details: add chrome (summary bar + gap) above content
        if item.details_variable and item.details_summary:
            return (
                details_chrome_height(item, gap, card_gap, resolved_style) + content_h
            )

        return content_h

    # Fallback (leaf item — treat as card)
    return resolved_style.chart_defaults.default_chart_height + 2 * float(
        resolved_style.frame.card_padding
    )


def calculate_layout_height(
    layout: Layout,
    card_gap: float,
    gap: float,
    min_height: float,
    available_width: float = 400.0,
    variable_values: dict[str, Any] | None = None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> float:
    """Calculate required height for a layout.

    Uses mdsvg to render and measure actual content heights:
    - KPI charts get smaller heights
    - Standard charts get normal heights
    - Tables use default height (data-aware sizing via HeightProvider at render time)
    - Nested boards accumulate their content heights
    - Titles are rendered and measured
    - Markdown content with word-wrapping is rendered and measured

    For rows layout: sum of each item's height + gaps
    For cols layout: max of item heights (all items share same height)
    For grid layout: based on rows and content types
    For tabs layout: active tab's content height + tab bar (not the union
      of all tabs)

    Args:
        layout: The layout to calculate height for
        card_gap: Gap between cards (inter-item spacing)
        gap: Gap between items
        min_height: Minimum height to return
        available_width: Available width for text wrapping calculations
        variable_values: Variable values to resolve Jinja against

    Returns:
        Required height in pixels
    """
    if not layout.items:
        return min_height

    if layout.type == "rows":
        measured_height = _measure_rows_layout_height(
            layout.items,
            card_gap,
            gap,
            available_width,
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )
    elif layout.type == "cols":
        measured_height = _measure_cols_layout_height(
            layout.items,
            card_gap,
            gap,
            available_width,
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )
    elif layout.type == "grid":
        measured_height = _measure_grid_layout_height(
            layout,
            card_gap,
            gap,
            available_width,
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )
    elif layout.type == "tabs":
        measured_height = _measure_tabs_layout_height(
            layout,
            card_gap,
            gap,
            available_width,
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )
    else:
        return min_height
    return max(measured_height, min_height)


def _clamp_to_authored_ceiling(authored: float, content: float) -> float:
    """Cap content at an authored ceiling — unless content has no real signal.

    An authored ``height:`` on a rows item is a ceiling, not a floor or an
    exact allocation: content that resolves shorter shrinks the row to match.
    Content that resolves taller keeps the authored value — that overflow
    direction is a different, already-handled failure mode
    (WARN-LAYOUT-MIN-EXCEEDS-HEIGHT).

    ``content <= 0`` means there was nothing independently measurable inside
    (no chart, no text, no nested content — e.g. a decorative color-swatch
    cell whose only content IS the box it's given). That is not "a shorter
    resolution to shrink to", it's an absence of one, so the authored height
    stays authoritative rather than clamping the row to nothing.
    """
    return min(authored, content) if content > 0 else authored


def _table_row_slot_height(authored: float, content: float) -> float:
    """A table's authored ``rows:`` height is a floor for a small overflow.

    Every other chart type treats an authored height as a hard ceiling
    (``_clamp_to_authored_ceiling``) — squeezing a Vega chart a few pixels
    shorter than requested is an unremarkable visual rescale. A table is
    different: shrinking its slot squeezes row text toward illegibility or
    forces pagination chrome the author never asked for.

    When the table's natural content height (grow-by-2-aware, already
    pagination-free for a small overflow — see ``_get_table_height_from_data``)
    exceeds the authored ceiling by no more than one pagination-control bar's
    worth of room, growing the slot is strictly better than the alternative:
    that same vertical space was going to be spent on either row squeeze or a
    reserved control bar anyway, so spend it on showing every row instead. A
    larger shortfall keeps the ceiling — pagination is the honest answer once
    the overflow is real, not a rounding artifact of the authored value.
    """
    from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT

    if content > authored and content - authored <= _PAGINATION_CONTROL_HEIGHT:
        return content
    return _clamp_to_authored_ceiling(authored, content)


def _rows_item_ceiling_height(
    item: LayoutItem, authored: float, content: float
) -> float:
    """Per-item authored-height rule for a ``rows:`` slot, shared by measurement and assignment.

    ``_measure_rows_layout_height`` (the container budget) and
    ``_calculate_rows_dimensions`` (the item assignment) must compute the
    identical height for the same item, or the budget one pass reserves and
    the height the other assigns diverge — an under-measured container
    silently squeezes every auto-sized sibling in the same stack.
    """
    return (
        _table_row_slot_height(authored, content)
        if _is_single_table_item(item)
        else _clamp_to_authored_ceiling(authored, content)
    )


def _is_single_table_item(item: LayoutItem) -> bool:
    """True for a ``height:``-wrapper around exactly one table chart.

    Authoring ``height:`` beside a chart reference always produces a nested
    ``type: board`` wrapper (``normalize/layout.py``) — there is no single-level
    "chart with an authored height" shape, so a bare ``type: chart`` item
    never has ``layout_height`` set and never reaches the caller's
    ``authored_height is not None`` guard. This looks through the one level
    of wrapping so the floor in ``_table_row_slot_height`` reaches the common
    "pin one table to a pixel height in a rows: stack" authoring pattern.
    A multi-item wrapper is left to the ordinary ceiling — the floor is only
    well-defined for a slot whose entire content is the one table.
    """
    return (
        item.board is not None
        and len(item.board.layout.items) == 1
        and isinstance(item.board.layout.items[0].chart, TableChart)
    )


def _measure_rows_layout_height(
    items: list[LayoutItem],
    card_gap: float,
    gap: float,
    available_width: float,
    variable_values: dict[str, Any] | None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> float:
    """Measure total height for a rows layout."""
    total_height = 0.0
    for item in items:
        content = _resolve_height(
            item,
            card_gap,
            gap,
            # Match _calculate_rows_dimensions: a width-pinned item's height is
            # measured at its pinned width, not the full row.
            rows_item_width(item, available_width),
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )
        # Percentages resolve to 0 here (no available_height context) and
        # fall through; _calculate_rows_dimensions resolves them later.
        authored = _resolve_layout_height(item, 0.0)
        total_height += (
            content
            if authored is None
            else _rows_item_ceiling_height(item, authored, content)
        )
    if len(items) > 1:
        total_height += (gap + card_gap) * (len(items) - 1)
    return total_height


def _measure_cols_layout_height(
    items: list[LayoutItem],
    card_gap: float,
    gap: float,
    available_width: float,
    variable_values: dict[str, Any] | None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> float:
    """Measure shared row height for a cols layout."""
    max_height = 0.0
    n_items = len(items)
    total_gap = (gap + card_gap) * (n_items - 1) if n_items > 1 else 0
    content_width = available_width - total_gap

    specified_widths: list[float | None] = []
    for item in items:
        parsed_width = parse_dimension(item.user_width, content_width)
        specified_widths.append(parsed_width)

    assigned_widths = resolve_cols_widths(
        specified_widths, content_width=content_width, item_count=n_items
    )
    for i, item in enumerate(items):
        # User-specified height takes precedence (px only here;
        # percentages need available_height, resolved in _calculate_cols_dimensions)
        resolved = _resolve_layout_height(item, 0.0)
        if resolved is not None:
            max_height = max(max_height, resolved)
        else:
            item_width = assigned_widths[i]
            item_height = _resolve_height(
                item,
                card_gap,
                gap,
                item_width,
                variable_values,
                height_provider,
                resolved_style=resolved_style,
            )
            max_height = max(max_height, item_height)
    return max_height


def _measure_grid_layout_height(
    layout: Layout,
    card_gap: float,
    gap: float,
    available_width: float,
    variable_values: dict[str, Any] | None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> float:
    """Measure required height for a grid layout.

    Budget and demand must be the same number for a grid whose items state
    their own ``row`` (what compile authors), so this measures the grid the way
    ``_calculate_grid_dimensions`` lays it out: each row costs its tallest
    item, and only rows that hold something cost anything. An auto-flow grid
    assembled in-process is the one exception — assignment's placement pass
    mutates ``item.row`` after this has already read it. A mean over items
    instead of a per-row max under-asks for every grid whose items-per-row
    varies — which, for a grid authored tile-by-tile, is the normal case.
    """
    columns = layout.columns if layout.columns is not None else DEFAULT_GRID_COLUMNS
    effective_gap = gap + card_gap
    total_gap_x = effective_gap * (columns - 1)
    col_width = (available_width - total_gap_x) / columns

    row_heights: dict[int, float] = {}
    for item in layout.items:
        item_row = item.row or 0  # type-state: silent_fallback — unset row is row 0
        item_col_span, item_row_span = item_grid_span(item)
        resolved = _resolve_layout_height(item, 0.0)
        if resolved is None:
            item_width = grid_span_width(col_width, item_col_span, effective_gap)
            resolved = _resolve_height(
                item,
                card_gap,
                gap,
                item_width,
                variable_values,
                height_provider,
                resolved_style=resolved_style,
            )
        height_per_row = resolved / item_row_span
        for r in range(item_row, item_row + item_row_span):
            if r not in row_heights or height_per_row > row_heights[r]:
                row_heights[r] = height_per_row

    if not row_heights:
        return resolved_style.chart_defaults.default_chart_height
    return sum(row_heights.values()) + effective_gap * (len(row_heights) - 1)


def resolve_active_tab_index(
    n_items: int,
    tab_variable: str | None,
    tab_slugs: list[str] | None,
    default_tab: int | None,
    variable_values: dict[str, Any] | None,
) -> int:
    """Resolve which tab is active: a variable override wins, else the
    authored default, else the first tab.

    Single source of truth for tab-active resolution — both the sizing pass
    (this module) and the renderer (``render/layouts.py``'s
    ``render_tabs_layout``) call this so the reserved height always matches
    the tab actually emitted.
    """
    active = 0 if default_tab is None else default_tab
    if tab_variable and tab_slugs and variable_values:
        var_value = variable_values.get(tab_variable)
        if var_value and str(var_value) in tab_slugs:
            active = tab_slugs.index(str(var_value))
    return min(active, n_items - 1)


def active_layout_items(
    layout: Layout, variable_values: VariableValues
) -> list[LayoutItem]:
    """Items whose subtree the sizing/resolution walk should descend into.

    A tabs layout renders exactly one tab at a time; every other tree walk
    that recurses into nested boards (nested-board sizing, slot-height
    fixups, cols alignment, resolved-variant bookkeeping) must recurse into
    that same one item, not all of them — the other tabs are never emitted,
    so resolving or rendering their charts is pure discarded work. Every
    other layout type still walks all of its items.
    """
    if layout.type != "tabs" or not layout.items:
        return layout.items
    active = resolve_active_tab_index(
        len(layout.items),
        layout.tab_variable,
        layout.tab_slugs,
        layout.default_tab,
        variable_values,
    )
    return [layout.items[active]]


def _measure_tabs_layout_height(
    layout: Layout,
    card_gap: float,
    gap: float,
    available_width: float,
    variable_values: dict[str, Any] | None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> float:
    """Measure required height for a tabs layout — the active tab only.

    A tabbed board renders one tab at a time; the reserved height must match
    that tab's own content, not the union of every tab. Unlike rows/cols/grid
    items, a tab item never carries an authored ``layout_height`` (``TabItem``
    has no ``height`` field) — content-aware measurement is the only path.
    """
    tab_bar_height = float(resolved_style.layout.tabs.bar_height)
    active_item = layout.items[
        resolve_active_tab_index(
            len(layout.items),
            layout.tab_variable,
            layout.tab_slugs,
            layout.default_tab,
            variable_values,
        )
    ]
    content_height = _resolve_height(
        active_item,
        card_gap,
        gap,
        available_width,
        variable_values,
        height_provider,
        resolved_style=resolved_style,
    )
    return content_height + tab_bar_height


def _set_render_ready_sizing(item: LayoutItem) -> None:
    """Set render-ready sizing fields (calculated_width, calculated_height, aspect_ratio).

    These fields preserve the calculated pixel dimensions for consistent rendering
    across SVG and HTML outputs. The aspect_ratio is used for responsive CSS rendering.

    Args:
        item: LayoutItem to set sizing fields on
    """
    # Set calculated dimensions (same as width/height for now, but preserved separately)
    item.calculated_width = item.width if item.width > 0 else None
    item.calculated_height = item.height if item.height > 0 else None

    # Calculate aspect ratio for responsive rendering
    if item.calculated_width and item.calculated_height and item.calculated_height > 0:
        item.aspect_ratio = item.calculated_width / item.calculated_height
    else:
        item.aspect_ratio = None


def calculate_layout_items(
    layout: Layout,
    available_width: float,
    available_height: float,
    card_gap: float,
    gap: float,
    variable_values: dict[str, Any] | None = None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> None:
    """Calculate dimensions for all items in a layout.

    Modifies items in place, setting their dimensions and positions.

    Args:
        layout: Layout to calculate dimensions for
        available_width: Available container width in pixels
        available_height: Available container height in pixels
        card_gap: Gap between cards (inter-item spacing)
        gap: Gap between items in pixels
        variable_values: Variable values to resolve Jinja against
        resolved_style: Board-resolved style for cascade-aware height calculations.
    """
    if not layout.items:
        return

    _apply_layout_dimensions(
        layout,
        available_width,
        available_height,
        card_gap,
        gap,
        variable_values,
        height_provider,
        resolved_style=resolved_style,
    )

    # active_layout_items requires a real dict — this function's own
    # variable_values stays Optional (pre-existing, widely-called contract).
    active_variable_values = variable_values if variable_values is not None else {}
    for item in active_layout_items(layout, active_variable_values):
        _calculate_nested_board_layout(
            item,
            card_gap,
            gap,
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )


def _apply_layout_dimensions(
    layout: Layout,
    available_width: float,
    available_height: float,
    card_gap: float,
    gap: float,
    variable_values: dict[str, Any] | None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> None:
    """Apply the correct layout dimension calculator based on layout type."""
    if layout.type == "rows":
        _calculate_rows_dimensions(
            layout.items,
            available_width,
            available_height,
            card_gap,
            gap,
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )
        return
    if layout.type == "cols":
        _calculate_cols_dimensions(
            layout.items,
            available_width,
            available_height,
            card_gap,
            gap,
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )
        return
    if layout.type == "grid":
        _calculate_grid_dimensions(
            layout.items,
            available_width,
            available_height,
            (layout.columns if layout.columns is not None else DEFAULT_GRID_COLUMNS),
            card_gap,
            gap,
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )
        return
    if layout.type == "tabs":
        _calculate_tabs_dimensions(
            layout,
            available_width,
            available_height,
            card_gap,
            gap,
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )
        return
    _calculate_rows_dimensions(
        layout.items,
        available_width,
        available_height,
        card_gap,
        gap,
        variable_values,
        height_provider,
        resolved_style=resolved_style,
    )


@dataclass(frozen=True)
class BoardContentBox:
    """Heights of a board's non-layout content elements with their stacking gaps.

    All callers of compute_board_content_box() use this as the single source of
    truth for vertical stacking arithmetic — sizing pass and render pass alike.
    """

    content_top: float  # inset from the block's top edge to the first element
    non_layout_height: float  # content_top + elements + their inter-element gaps
    gap_before_layout: float  # gap prepended to layout block (0 if no layout items or no content above)


def compute_board_content_box(
    *,
    title_height: float,
    text_height: float,
    variables_height: float,
    inline_band_height: float,
    gap: float,
    has_layout_items: bool,
    card_padding: float,
) -> BoardContentBox:
    """Single owner of vertical stacking arithmetic for board non-layout content.

    Gap rules:
    - content_top: ``card_padding`` before the first element, and 0 when there is
      none.  A chart's ink sits ``card_padding`` inside its own box on all four
      sides; prose is inset the same way so the two line up along the top of a
      row.  Once per band, not per block — title→text is deliberately flush.
    - title→text: 0 gap.  The title is measured via mdsvg, which draws a heading
      opening its document as ``text_height + margin_bottom`` (the top margin
      collapses), so heading_margin_bottom_px IS baked into title_height.
      Adding a board gap on top double-counts it.
    - All other adjacent pairs (inline_band→text, text→variables, title→variables
      when no text, inline_band→variables): full ``gap``.
    - gap_before_layout: ``gap`` iff has_layout_items AND non_layout_height > 0.

    Args:
        title_height: Pre-measured title height (0 if absent or use_title_inline).
        text_height: Pre-measured markdown text height (0 if absent).
        variables_height: Pre-measured variable controls height (0 if absent or
            merged into inline band).
        inline_band_height: Pre-measured title+variables inline band height (0 if
            not using inline band).  When > 0, title_height and variables_height
            are 0.
        gap: Board-level gap (used for all inter-element spacing except title→text).
        has_layout_items: True when the board has layout items following the
            non-layout elements.
        card_padding: The inset a chart's ink carries inside its own card, and so
            the inset this band's first element carries inside the block's.

    Returns:
        BoardContentBox with element heights and stacking totals.
    """
    height = 0.0
    has_prev = False
    prev_is_title = False

    if inline_band_height > 0.0:
        height += inline_band_height
        has_prev = True
        prev_is_title = False  # inline band → text uses normal gap
    elif title_height > 0.0:
        height += title_height
        has_prev = True
        prev_is_title = True

    if text_height > 0.0:
        if has_prev:
            height += 0.0 if prev_is_title else gap
        height += text_height
        has_prev = True
        prev_is_title = False

    if variables_height > 0.0:
        if has_prev:
            height += gap
        height += variables_height
        has_prev = True

    gap_before_layout = gap if (has_layout_items and has_prev) else 0.0
    content_top = card_padding if has_prev else 0.0

    return BoardContentBox(
        content_top=content_top,
        non_layout_height=height + content_top,
        gap_before_layout=gap_before_layout,
    )


def nested_board_sizing_context(
    nested_board: Board,
    width: float,
    card_gap: float,
    variable_values: dict[str, Any] | None,
) -> tuple[float, float, float]:
    """Return (content_width, non_layout_height, child_gap).

    non_layout_height includes title + text + variables + gaps between them,
    plus the gap before the layout block if layout items follow.
    """
    nrs = nested_board.resolved_style
    ep = _effective_padding(nrs)
    board_margin_horizontal = nrs.margin.horizontal if nrs.margin else 0.0
    content_width = width - ep.horizontal - board_margin_horizontal
    child_gap = nrs.gap if nrs.gap is not None else 0.0
    effective_child_gap = child_gap + card_gap

    card_pad = float(nested_board.resolved_style.frame.card_padding)
    inner = max(content_width - 2 * card_pad, 1.0)

    use_title_inline = should_use_title_inline_band(
        nested_board.title,
        nested_board.visible_variables,
        content_width,
        nrs,
        card_pad,
        variable_values,
        nested_board.level,
    )

    if use_title_inline:
        inline_band_h = compute_title_variables_inline_band_height(
            nested_board, content_width, variable_values
        )
        title_h = 0.0
        variables_h = 0.0
    else:
        inline_band_h = 0.0
        if nested_board.title:
            title_h = max(
                get_title_height(
                    nested_board.title,
                    inner,
                    variable_values,
                    level=nested_board.level,
                    resolved_style=nrs,
                ),
                float(nested_board.resolved_style.title.min_height),
            )
        else:
            title_h = 0.0
        if nested_board.visible_variables:
            variables_h = compute_variable_controls_height(
                nested_board.visible_variables,
                content_width,
                variable_values,
                nested_board.resolved_style.variables,
            )
        else:
            variables_h = 0.0

    text_h = (
        get_markdown_text_height(
            nested_board.text,
            inner,
            variable_values,
            text_style=nested_board.resolved_style.text,
            resolved_style=nrs,
            allow_raw_html=nested_board.html_policy == "trusted-raw",
        )
        if nested_board.text
        else 0.0
    )

    box = compute_board_content_box(
        title_height=title_h,
        text_height=text_h,
        variables_height=variables_h,
        inline_band_height=inline_band_h,
        gap=effective_child_gap,
        has_layout_items=bool(nested_board.layout.items),
        card_padding=float(nrs.frame.card_padding),
    )
    non_layout_height = box.non_layout_height + box.gap_before_layout

    return content_width, non_layout_height, child_gap


def _calculate_nested_board_layout(
    item: LayoutItem,
    card_gap: float,
    gap: float,
    variable_values: dict[str, Any] | None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> None:
    """Recursively size a nested board once the parent item dimensions are known.

    resolved_style is the *container's* style — the details chrome (summary
    bar) is chrome the container draws around the nested board, matching what
    render_layout_item reads (render/chart/rendering.py). The nested board's
    own resolved_style (nrs2) only governs its own padding/margin/content.
    """
    if is_item_collapsed_summary(item, variable_values) or not item.board:
        return

    nested_board = item.board
    nested_board.layout.width = item.width
    nested_board.layout.height = item.height

    content_width, non_layout_height, child_gap = nested_board_sizing_context(
        nested_board, item.width, card_gap, variable_values
    )

    nrs2 = nested_board.resolved_style
    layout_available_height = (
        item.height
        - details_chrome_height(item, gap, card_gap, resolved_style)
        - _effective_padding(nrs2).vertical
        - (nrs2.margin.vertical if nrs2.margin else 0.0)
        - non_layout_height
    )

    nested_board.layout.content_width = content_width
    nested_board.layout.content_height = max(layout_available_height, 0)

    calculate_layout_items(
        nested_board.layout,
        content_width,
        max(layout_available_height, 0),
        card_gap,
        gap=child_gap,
        variable_values=variable_values,
        height_provider=height_provider,
        resolved_style=nested_board.resolved_style,
    )


def _calculate_rows_dimensions(
    items: list[LayoutItem],
    available_width: float,
    available_height: float,
    card_gap: float,
    gap: float,
    variable_values: dict[str, Any] | None = None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> None:
    """Calculate dimensions for items in a rows layout.

    In a rows layout:
    - Items stack vertically
    - All items get full width
    - Height respects user-specified values (e.g., "200px", "50%")
    - Remaining height is distributed among auto items based on content type
    - If auto items exceed remaining space, they scale proportionally
    - Specified items are never scaled; if they exceed available space the layout overflows

    Args:
        items: List of layout items
        available_width: Available width in pixels
        available_height: Available height in pixels
        card_gap: Gap between cards (inter-item spacing)
        gap: Gap between items in pixels
        variable_values: Variable values to resolve Jinja against
    """
    n = len(items)
    if n == 0:
        return

    # Calculate total gap space
    total_gap = (gap + card_gap) * (n - 1)
    available_content_height = available_height - total_gap

    # First pass: parse user-specified heights (capped to content — an authored
    # height is a ceiling, not an exact allocation) and get content heights for
    # auto items.
    specified_heights: list[float | None] = []
    content_heights: list[float] = []
    total_specified = 0.0

    # An authored width: pins the item's slot (capped at the row) — heights
    # must be resolved at the width the item will actually render at.
    item_widths = [rows_item_width(item, available_width) for item in items]

    for i, item in enumerate(items):
        authored_height = _resolve_layout_height(item, available_content_height)
        if authored_height is not None:
            content_height = _resolve_height(
                item,
                card_gap,
                gap,
                item_widths[i],
                variable_values,
                height_provider,
                resolved_style=resolved_style,
            )
            ceiling_height = _rows_item_ceiling_height(
                item, authored_height, content_height
            )
            specified_heights.append(ceiling_height)
            total_specified += ceiling_height
            content_heights.append(ceiling_height)
        else:
            specified_heights.append(None)
            height = _resolve_height(
                item,
                card_gap,
                gap,
                item_widths[i],
                variable_values,
                height_provider,
                resolved_style=resolved_style,
            )
            content_heights.append(height)

    # Distribute height: specified items keep their height, auto items share the rest
    remaining = max(available_content_height - total_specified, 0.0)
    auto_total = sum(
        h for h, s in zip(content_heights, specified_heights, strict=True) if s is None
    )

    item_heights: list[float] = []
    for i, _item in enumerate(items):
        spec_h = specified_heights[i]
        if spec_h is not None:
            item_heights.append(spec_h)
        elif auto_total > 0 and remaining < auto_total:
            # Auto items need scaling to fit
            item_heights.append(content_heights[i] * (remaining / auto_total))
        else:
            item_heights.append(content_heights[i])

    # Second pass: assign dimensions
    current_y = 0.0
    for i, item in enumerate(items):
        item.width = item_widths[i]
        item.width_fraction = (
            item.width / available_width if available_width > 0 else 1.0
        )
        item.height = item_heights[i]
        item.x = 0.0
        item.y = current_y
        current_y += item.height + gap + card_gap
        # Set render-ready sizing fields
        _set_render_ready_sizing(item)


def _resolve_layout_height(item: LayoutItem, available: float) -> float | None:
    """Return resolved layout-wrapper height in px, or None for auto.

    Percentages resolve against ``available``; measurement functions pass
    ``available=0.0`` so percentages fall through to content-aware sizing
    (``parse_dimension("50%", 0.0)`` returns 0.0, filtered by ``h > 0``).

    Explicit ``height: 0`` is rejected at compile time in
    ``normalize.layout._validate_dimension``.
    """
    h = parse_dimension(item.layout_height, available)
    return h if h is not None and h > 0 else None


def _calculate_cols_dimensions(
    items: list[LayoutItem],
    available_width: float,
    available_height: float,
    card_gap: float,
    gap: float,
    variable_values: dict[str, Any] | None = None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> None:
    """Calculate dimensions for items in a cols layout.

    In a cols layout:
    - Items arrange horizontally
    - Width respects user-specified values (e.g., "30%", "200px")
    - Remaining width is distributed equally among items without specified widths
    - ALL items get the SAME height (max of content heights, capped at available)

    This ensures alignment: all items in a row have the same height.
    The height is the max of what each item needs, but won't exceed available.

    Args:
        items: List of layout items
        available_width: Available width in pixels
        available_height: Available height in pixels (upper bound)
        card_gap: Gap between cards (inter-item spacing)
        gap: Gap between items in pixels
        variable_values: Variable values to resolve Jinja against
    """
    n = len(items)
    if n == 0:
        return

    # Calculate total gap space
    effective_gap = gap + card_gap
    total_gap = effective_gap * (n - 1)
    content_width = available_width - total_gap

    # First pass: parse user-specified widths and calculate remaining space
    specified_widths: list[float | None] = []

    for item in items:
        parsed_width = parse_dimension(item.user_width, content_width)
        specified_widths.append(parsed_width)

    resolved_widths = resolve_cols_widths(
        specified_widths, content_width=content_width, item_count=n
    )

    # Find max content height (all items in cols share this height).
    # Track specified and content maxima separately so overflow semantics
    # are order-independent: if max_specified >= max_content, the user's
    # explicit height wins and the row overflows rather than being capped.
    max_specified = 0.0
    max_auto = 0.0
    for i, item in enumerate(items):
        # Percentages resolve against available_height (no vertical gap in cols)
        resolved = _resolve_layout_height(item, available_height)
        if resolved is not None:
            max_specified = max(max_specified, resolved)
        else:
            item_w = resolved_widths[i]
            ch = _resolve_height(
                item,
                card_gap,
                gap,
                item_w,
                variable_values,
                height_provider,
                resolved_style=resolved_style,
            )
            max_auto = max(max_auto, ch)

    max_content_height = max(max_specified, max_auto)
    # Cap to available height unless a user-specified height drove the max.
    # >= so that a specified height equal to the content max still wins
    # (the user explicitly asked for this height, so don't clamp it).
    row_height = (
        max_content_height
        if max_specified >= max_auto
        else min(max_content_height, available_height)
    )

    # Second pass: assign dimensions
    current_x = 0.0
    for i, item in enumerate(items):
        item_width = resolved_widths[i]
        item.width_fraction = (
            item_width / content_width if content_width > 0 else 1.0 / n
        )
        item.width = item_width
        item.height = row_height  # All items get same height
        item.x = current_x
        item.y = 0.0
        # Add gap between items, but not after the last item
        if i < n - 1:
            current_x += item_width + effective_gap
        else:
            current_x += item_width
        # Set render-ready sizing fields
        _set_render_ready_sizing(item)


def _calculate_grid_dimensions(
    items: list[LayoutItem],
    available_width: float,
    available_height: float,
    columns: int,
    card_gap: float,
    gap: float,
    variable_values: dict[str, Any] | None = None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> None:
    """Calculate dimensions for items in a grid layout.

    In a grid layout:
    - Items can have explicit row, col positions and row_span, col_span
    - Items WITHOUT explicit positions are auto-flowed like CSS grid
    - Width is based on column count
    - Row height is calculated based on content types

    Args:
        items: List of layout items with grid positions
        available_width: Available width in pixels
        available_height: Available height in pixels
        columns: Number of grid columns
        card_gap: Gap between cards (inter-item spacing)
        gap: Gap between items in pixels
        variable_values: Variable values to resolve Jinja against
    """
    if not items:
        return

    # Calculate column width
    effective_gap = gap + card_gap
    total_gap_x = effective_gap * (columns - 1)
    col_width = (available_width - total_gap_x) / columns

    # Auto-flow items without explicit positions
    # Track occupied cells as a set of (col, row) tuples
    occupied: set[tuple[int, int]] = set()

    # First pass: mark cells occupied by items with explicit positions
    for item in items:
        if item.col is not None and item.row is not None:
            item_col_span, item_row_span = item_grid_span(item)
            for c in range(item.col, item.col + item_col_span):
                for r in range(item.row, item.row + item_row_span):
                    occupied.add((c, r))

    # Second pass: auto-place items without explicit positions
    current_col = 0
    current_row = 0

    for item in items:
        if item.col is None or item.row is None:
            item_col_span, item_row_span = item_grid_span(item)

            # Find next available position that fits the item
            placed = False
            while not placed:
                # Check if item fits at current position
                fits = True
                if current_col + item_col_span > columns:
                    # Doesn't fit, move to next row
                    current_col = 0
                    current_row += 1
                    continue

                # Check if all cells are available
                for c in range(current_col, current_col + item_col_span):
                    for r in range(current_row, current_row + item_row_span):
                        if (c, r) in occupied:
                            fits = False
                            break
                    if not fits:
                        break

                if fits:
                    # Place the item
                    item.col = current_col
                    item.row = current_row
                    # Mark cells as occupied
                    for c in range(current_col, current_col + item_col_span):
                        for r in range(current_row, current_row + item_row_span):
                            occupied.add((c, r))
                    placed = True
                    # Move to next column for next item
                    current_col += item_col_span
                else:
                    # Try next column
                    current_col += 1

    # Calculate content-aware row height
    # Find max content height per row, then average
    row_content_heights: dict[int, float] = {}  # row_index -> max_height

    # Track which rows have heights driven by user-specified values.
    # A row with any specified-height item is exempt from scaling,
    # even if the row's max came from an auto item.  This is intentional:
    # the user pinned at least one item in that row, so we respect the
    # resulting row height rather than squashing it.
    specified_rows: set[int] = set()

    for item in items:
        item_row = item.row or 0
        item_col_span, item_row_span = item_grid_span(item)

        # User-specified height takes precedence
        resolved = _resolve_layout_height(item, available_height)
        if resolved is not None:
            content_height = resolved
            for r in range(item_row, item_row + item_row_span):
                specified_rows.add(r)
        else:
            item_width = grid_span_width(col_width, item_col_span, effective_gap)
            content_height = _resolve_height(
                item,
                card_gap,
                gap,
                item_width,
                variable_values,
                height_provider,
                resolved_style=resolved_style,
            )
        # Distribute height across spanned rows
        height_per_row = content_height / item_row_span

        for r in range(item_row, item_row + item_row_span):
            current_max = row_content_heights.get(r, 0.0)
            row_content_heights[r] = max(current_max, height_per_row)

    # Calculate total content height. Gaps are charged per occupied row, not
    # per row index: a grid whose items sit at rows 0 and 40 draws two rows
    # with one gap between them, not 41 rows with 40 gaps.
    total_content_height = sum(row_content_heights.values())
    total_gap_y = effective_gap * max(len(row_content_heights) - 1, 0)

    # Use content height if it fits, otherwise scale auto rows to fit.
    # Rows with user-specified heights are exempt from scaling (overflow).
    if total_content_height + total_gap_y <= available_height:
        row_heights = row_content_heights
    else:
        specified_total = sum(
            row_content_heights[r] for r in specified_rows if r in row_content_heights
        )
        auto_total = total_content_height - specified_total
        auto_budget = max(available_height - total_gap_y - specified_total, 0.0)
        auto_scale = auto_budget / auto_total if auto_total > 0 else 1.0
        row_heights = {
            r: h if r in specified_rows else h * auto_scale
            for r, h in row_content_heights.items()
        }

    # Calculate row Y positions. An unoccupied row index has no content to
    # draw, so it advances nothing — charging it default_chart_height is what
    # turned a grid with items at rows 0 and 40 into a 286-megapixel render.
    row_y_positions: dict[int, float] = {}
    current_y = 0.0
    for r in sorted(row_heights):
        row_y_positions[r] = current_y
        current_y += row_heights[r] + effective_gap

    # Position each item
    for item in items:
        item_col = item.col or 0
        item_row = item.row or 0
        item_col_span, item_row_span = item_grid_span(item)

        # Calculate pixel position
        item.x = item_col * (col_width + effective_gap)
        item.y = row_y_positions[item_row]

        # Calculate pixel dimensions
        item.width = grid_span_width(col_width, item_col_span, effective_gap)

        # Height spans multiple rows
        item_height = 0.0
        for r in range(item_row, item_row + item_row_span):
            item_height += row_heights[r]
        item_height += effective_gap * (item_row_span - 1)  # Internal gaps
        item.height = item_height

        # Calculate width fraction
        item.width_fraction = item_col_span / columns

        # Set render-ready sizing fields
        _set_render_ready_sizing(item)


def _calculate_tabs_dimensions(
    layout: Layout,
    available_width: float,
    available_height: float,
    card_gap: float,
    gap: float,
    variable_values: dict[str, Any] | None = None,
    height_provider: HeightProvider | None = None,
    *,
    resolved_style: ResolvedStyle,
) -> None:
    """Calculate dimensions for items in a tabs layout.

    In a tabs layout:
    - Each tab gets the full container size (minus tab bar height)
    - Only one tab is visible at a time
    - Height comes from the active tab's content only, not the union
      across all tabs

    A tab item never carries an authored ``layout_height`` (``TabItem`` has
    no ``height`` field) — content-aware measurement, capped to what's
    available, is the only path.

    Args:
        layout: The tabs layout (items = one per tab, plus tab_variable /
            tab_slugs / default_tab for active-tab resolution)
        available_width: Available width in pixels
        available_height: Available height in pixels
        card_gap: Gap between cards (inter-item spacing)
        gap: Gap (unused for tabs)
        variable_values: Variable values to resolve Jinja against
    """
    tab_bar_height = float(resolved_style.layout.tabs.bar_height)
    items = layout.items
    tab_available = available_height - tab_bar_height
    active_item = items[
        resolve_active_tab_index(
            len(items),
            layout.tab_variable,
            layout.tab_slugs,
            layout.default_tab,
            variable_values,
        )
    ]
    ch = _resolve_height(
        active_item,
        card_gap,
        gap,
        available_width,
        variable_values,
        height_provider,
        resolved_style=resolved_style,
    )
    content_height = min(ch, tab_available)

    for item in items:
        item.width_fraction = 1.0
        item.width = available_width
        item.height = content_height
        item.x = 0.0
        item.y = tab_bar_height
        # Set render-ready sizing fields
        _set_render_ready_sizing(item)
