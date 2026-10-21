"""Layout sizing calculation module.

Purpose: Calculate dimensions for layout items using static, non-data-aware
defaults (aspect ratios, theme/config). Query results are read only to
resolve board text, from the pre-executed cache.

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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.resolved import (
        ResolvedLayoutItem,
    )
    from dbt_charts.core.compile.models.style.theme import TextStyle
    from dbt_charts.core.render.variables_layout import VariablesLayout

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    Layout,
    LayoutItem,
    ProsePlan,
    VariableValues,
)
from dbt_charts.core.compile.models.chart.normalized import (
    Chart,
    CustomChart,
    PieChart,
    SparkBarChart,
    TableChart,
    _CartesianChartFields,
    _GeoChartFields,
)
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedChartDefaults,
    ResolvedStyle,
    effective_padding as _effective_padding,
)
from dbt_charts.core.compile.models.style.theme import (
    VariablesStyle,
)
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.resolve.chart._kwargs import _FAMILY_SLOT
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.compile.resolve.style.markdown_style import (
    title_blocks,
    title_max_content_width,
    title_renderer,
)
from dbt_charts.core.compile.sizing import (
    DEFAULT_GRID_COLUMNS,
    grid_span_width,
    item_grid_span,
    nested_content_geometry,
    parse_dimension,
    resolve_cols_widths,
    rows_item_width,
)
from dbt_charts.core.constants.estimates import SPARK_BAR_TITLE_HEIGHT_PX
from dbt_charts.core.diagnostics.codes_render import ERR_TAB_UNKNOWN
from dbt_charts.core.numeric import aspect_ratio_height
from dbt_charts.core.render.conditions import item_is_drawn
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.variables_layout import WRAP_EPSILON, lay_out_variables
from dbt_charts.core.render.variables_resolve import resolve_controls

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


def custom_chart_height(
    aspect_ratio: float | None,
    min_height: float | None,
    max_height: float | None,
    width: float | None,
    defaults: ResolvedChartDefaults,
) -> float:
    """Height of a custom chart's whole box; authored sizing wins over the theme's."""
    if width is None or width <= 0:
        return defaults.default_chart_height
    return aspect_ratio_height(
        width,
        defaults.aspect_ratio if aspect_ratio is None else aspect_ratio,
        float(defaults.min_height if min_height is None else min_height),
        float(defaults.max_height if max_height is None else max_height),
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
      5. style.charts.default_chart_height when no width is available.

    SVG-family renderers own their own sizing contract:
      - kpi    → resolved_style.chart_defaults.kpi.default_height (theme cascade)
      - table  → style.charts.default_table_height (replaced by row-count sizing at render)
      - callout → 0.0 placeholder (replaced by natural render-first sizing)
      - spark_bar → config-derived upper bound from max_bars/bar.height

    This is the static height calculator. It uses aspect ratios
    and config defaults only — no rendering, except board text (see module docstring).
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
        # body + title addition (the engine's spark_bar).
        sb = resolved_style.chart_defaults.spark_bar
        body = sb.max_bars * (sb.bar.height + sb.bar.padding) + sb.bar.padding
        # SparkBarChart has title via _SharedChartFields; isinstance narrows for pyright.
        has_title = isinstance(chart, SparkBarChart) and chart.title
        return body + SPARK_BAR_TITLE_HEIGHT_PX if has_title else body

    if isinstance(chart, CustomChart):
        return custom_chart_height(
            chart.aspect_ratio,
            chart.min_height,
            chart.max_height,
            width,
            resolved_style.chart_defaults,
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
        # The donut slot is not a chart type, so it cannot go through _FAMILY_SLOT.
        type_config = getattr(
            resolved_style.chart_defaults,
            "donut"
            if isinstance(chart, PieChart) and chart.is_donut
            else _FAMILY_SLOT[chart_type],
        )
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


def get_markdown_text_height(
    text: str | None,
    width: float,
    variable_values: dict[str, Any] | None = None,
    *,
    text_style: TextStyle,
    resolved_style: ResolvedStyle,
    plan: ProsePlan | None,
    base_dir: str | None,
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

    from dbt_charts.core.render.boards import resolve_display_text_from_provider
    from dbt_charts.core.render.prose import render_prose_svg

    resolved_content = resolve_display_text_from_provider(
        text,
        variable_values,
        resolved_style=resolved_style,
        base_dir=base_dir,
    )
    _, height = render_prose_svg(
        resolved_content,
        width,
        text_style,
        resolved_style,
        plan,
        text,
        base_dir,
        None,
        allow_raw_html,
    )
    return height


def get_title_height(
    title: str | None,
    width: float,
    variable_values: dict[str, Any] | None = None,
    level: int = 1,
    *,
    resolved_style: ResolvedStyle | None = None,
    base_dir: str | None,
) -> float:
    """The height ``render_title`` draws this title at, wrapped to ``width``.

    Measured by the renderer that draws the title, on the blocks it draws, so
    the reserved box and the drawn one cannot disagree.
    """
    if not title:
        return 0.0

    _rs = resolved_style or resolve_style(get_theme_style())
    from dbt_charts.core.render.boards import resolve_display_text_from_provider

    renderer, _ = title_renderer(_rs, level)
    resolved_title = resolve_display_text_from_provider(
        title, variable_values, resolved_style=_rs
    )
    blocks = title_blocks(title, resolved_title, level, _rs, base_dir, None)
    return renderer.measure(blocks, width=width, padding=0.0).height


def title_baseline_offset(
    title: str,
    width: float,
    variable_values: VariableValues | None,
    resolved_style: ResolvedStyle,
    level: int,
    base_dir: str | None,
) -> float:
    """How far below a title block's top edge its first baseline sits.

    Asked of the renderer that draws the title, on the blocks it draws, rather
    than derived from the block height: an inline image taller than the text
    moves the baseline down. Anything aligning itself to a board title — the
    controls beside it in the title-inline band — needs the number the title
    was actually drawn with.
    """
    from dbt_charts.core.render.boards import resolve_display_text_from_provider

    renderer, _ = title_renderer(resolved_style, level)
    resolved_title = resolve_display_text_from_provider(
        title, variable_values, resolved_style=resolved_style
    )
    blocks = title_blocks(title, resolved_title, level, resolved_style, base_dir, None)
    return renderer.first_baseline(blocks, width)


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
    # Level 1 is what gets asked, whatever the board's level: board_title_markdown
    # always emits `# {title}`, and title_renderer has already baked the
    # level-resolved size into the style's h1.
    return title_renderer(resolved_style, level)[0].heading_line_box(1, block_height)


# ============================================================================
# VARIABLE CONTROLS HEIGHT
# ============================================================================

# Height of a paginated table's control bar: the slack a table slot may grow by
# before pagination chrome is worth reserving (the engine draws the bar itself).
TABLE_PAGINATION_CONTROL_HEIGHT = 28

# Minimum title column width when sharing a row with variables so titles never
# collapse to zero when the variable strip is very wide.
_MIN_TITLE_INLINE_TITLE_COLUMN_PX = 48.0
# Hard ceiling on title column width as a fraction of the band's inner width.
# At 0.8, pathologically long titles wrap before they can eliminate the
# variables column — the strip stays parseable even when authors give a board
# a 60-character title and 7 filters.
_TITLE_INLINE_TITLE_MAX_INNER_RATIO = 0.8
_TITLE_INLINE_VARIABLE_MAX_ROWS = 2


def resolve_title_variables_inline_widths(
    inner: float,
    resolved_style: ResolvedStyle,
    visible_variables: dict[str, Variable],
    title: str | None,
    variable_values: dict[str, Any] | None,
    level: int,
    base_dir: str | None,
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
    natural = (
        title_max_content_width(
            title,
            level,
            resolved_style,
            {} if variable_values is None else variable_values,
            base_dir,
        )
        if title
        else 0.0
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
    base_dir: str | None,
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
        inner,
        resolved_style,
        visible_variables,
        title,
        variable_values,
        level,
        base_dir,
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
    base_dir = board.base_dir
    _, vars_w = resolve_title_variables_inline_widths(
        inner,
        board.resolved_style,
        board.visible_variables,
        board.title,
        variable_values,
        board.level,
        base_dir,
    )
    if not board.title:
        return 0.0
    # Measure at inner (the full band width) so the title's measured height
    # matches the height render_title produces at that same width.
    # The narrower title column (vars_w companion) would over-reserve when
    # a case transform widens the title past its wrap threshold.
    title_h = max(
        get_title_height(
            board.title,
            inner,
            variable_values,
            level=board.level,
            resolved_style=board.resolved_style,
            base_dir=base_dir,
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
        title_baseline_offset(
            board.title,
            inner,
            variable_values,
            board.resolved_style,
            board.level,
            base_dir,
        ),
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
    # active_layout_items needs a real dict; variable_values stays Optional for
    # this widely-called signature.
    drawn = active_layout_items(
        layout, variable_values if variable_values is not None else {}
    )
    if not drawn:
        return min_height

    if layout.type == "rows":
        measured_height = _measure_rows_layout_height(
            drawn,
            card_gap,
            gap,
            available_width,
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )
    elif layout.type == "cols":
        measured_height = _measure_cols_layout_height(
            drawn,
            card_gap,
            gap,
            available_width,
            variable_values,
            height_provider,
            resolved_style=resolved_style,
        )
    elif layout.type == "grid":
        measured_height = _measure_grid_layout_height(
            drawn,
            layout.columns,
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
    if content > authored and content - authored <= TABLE_PAGINATION_CONTROL_HEIGHT:
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
    items: list[LayoutItem],
    layout_columns: int | None,
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
    columns = layout_columns if layout_columns is not None else DEFAULT_GRID_COLUMNS
    effective_gap = gap + card_gap
    total_gap_x = effective_gap * (columns - 1)
    col_width = (available_width - total_gap_x) / columns

    row_heights: dict[int, float] = {}
    for item in items:
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
    the tab actually emitted. The variable value is runtime user input, so a
    value naming no tab raises rather than quietly showing another tab.
    """
    active = 0 if default_tab is None else default_tab
    if tab_variable and tab_slugs and variable_values:
        var_value = variable_values.get(tab_variable)
        if var_value:
            if str(var_value) not in tab_slugs:
                raise RenderError.from_code(
                    ERR_TAB_UNKNOWN,
                    variable=tab_variable,
                    value=str(var_value),
                    slugs=list(tab_slugs),
                )
            active = tab_slugs.index(str(var_value))
    return min(active, n_items - 1)


def active_layout_items(
    layout: Layout, variable_values: VariableValues
) -> list[LayoutItem]:
    """Items that are drawn: the one place a layout decides what is painted.

    A hidden item (``visible:`` false) takes no space and nothing of it is
    measured or resolved. A tabs layout renders exactly one tab at a time; every other tree walk
    that recurses into nested boards (nested-board sizing, slot-height
    fixups, cols alignment, resolved-variant bookkeeping) must recurse into
    that same one item, not all of them — the other tabs are never emitted,
    so resolving or rendering their charts is pure discarded work.
    """
    items = layout.items
    if layout.type == "tabs" and items:
        active = resolve_active_tab_index(
            len(items),
            layout.tab_variable,
            layout.tab_slugs,
            layout.default_tab,
            variable_values,
        )
        items = [items[active]]
    return [i for i in items if item_is_drawn(i.visible, variable_values)]


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
    # active_layout_items needs a real dict; variable_values stays Optional for
    # this widely-called signature.
    active_variable_values = variable_values if variable_values is not None else {}
    drawn = active_layout_items(layout, active_variable_values)
    if not drawn:
        return

    _apply_layout_dimensions(
        layout,
        drawn,
        available_width,
        available_height,
        card_gap,
        gap,
        variable_values,
        height_provider,
        resolved_style=resolved_style,
    )

    for item in drawn:
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
    items: list[LayoutItem],
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
            items,
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
            items,
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
            items,
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
        items,
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
    content_width, child_gap = nested_content_geometry(nested_board, width)
    effective_child_gap = child_gap + card_gap

    card_pad = float(nested_board.resolved_style.frame.card_padding)
    inner = max(content_width - 2 * card_pad, 1.0)

    base_dir = nested_board.base_dir
    use_title_inline = should_use_title_inline_band(
        nested_board.title,
        nested_board.visible_variables,
        content_width,
        nrs,
        card_pad,
        variable_values,
        nested_board.level,
        base_dir,
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
                    base_dir=base_dir,
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
            base_dir=base_dir,
            plan=nested_board.prose_plan,
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
