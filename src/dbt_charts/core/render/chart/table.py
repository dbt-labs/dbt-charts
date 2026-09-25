"""Table SVG rendering for dbt charts dashboards.

Per-column paint comes from ``style.columns``; ``TableChart`` declares no
mark channels (``color``/``background``/``opacity``/``stroke_*`` are
``extra_forbidden`` on the authored model), so there is nothing to lower here.
"""

from __future__ import annotations

import html as html_module
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, Any

from dbt_charts.core.colors import (
    is_sanitizable_color,
    sanitize_color,
)
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.chart.authored import (
    TableColumnConfig,
)
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.style.authored import (
    PaginationConfig,
    fill_table_column_defaults,
)
from dbt_charts.core.compile.models.style.resolved import ResolvedTableColumnConfig
from dbt_charts.core.compile.resolve import infer_pivot_measure_names
from dbt_charts.core.font_measure import centered_baseline_offset
from dbt_charts.core.utils import (
    coerce_numeric_cell,
    is_date_like,
)

if TYPE_CHECKING:
    import datetime as dt

    from dbt_charts.core.compile.models.chart.authored import (
        FieldConditionalFormatting,
        SparkConfig,
    )
    from dbt_charts.core.compile.models.chart.resolved.table import ResolvedTableChart
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
    from dbt_charts.core.compile.models.style.authored import TableColumnDefaultsConfig
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedChartDefaults,
        ResolvedStyle,
        ResolvedTableStyle,
    )
    from mdsvg.fonts import FontMeasurer

from dbt_charts.core.compile.format import decimal_pad_table_for, resolve_format
from dbt_charts.core.compile.models.style.theme import (
    VALID_FONT_WEIGHTS,
    PaginatorStyle,
    TableChartStyle,
    TableRowNumbersStyle,
    TableRowRoleStyle,
    TitleStyle,
    font_weight_as_css,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.fonts import (
    DBT_SANS_TABULAR_FONT_FAMILY,
    SOURCE_SERIF_4_FONT_FAMILY,
)
from dbt_charts.core.render.board_variables import current_board_variables
from dbt_charts.core.render.chart.auto_link import (
    get_filter_variables_context,
    resolve_filter_cell_link,
)
from dbt_charts.core.render.chart.table_overflow import (
    TableCramping,
    TableOverflow,
    record_table_cramping,
    record_table_overflow,
)
from dbt_charts.core.render.chart.table_page_squeeze import (
    TablePageSqueeze,
    record_table_page_squeeze,
)
from dbt_charts.core.render.chart.table_static_pagination import (
    StaticPaginationCap,
    record_static_pagination_cap,
)
from dbt_charts.core.render.chart.table_support import (
    _VALID_ROW_ROLES,
    _glyph_possible,
    _raise_if_time_format_on_numeric,
    calculate_column_layout,
    cell_glyph_run,
    format_table_cell_value,
    glyph_run,
    is_summary_role,
    is_total_role,
    measure_column_demands,
    measure_column_word_floors,
    parse_column_width,
    resolve_cell_conditional_styles,
    resolve_cell_glyph,
    resolve_cell_glyph_from_overrides,
    resolve_cell_link_with_board,
    resolve_conditional_styles,
    resolve_header_overflow,
    resolve_row_role,
    resolve_table_style_value,
    resolve_wrapped_headers,
)
from dbt_charts.core.render.chart.text_truncation import record_text_truncation
from dbt_charts.core.render.chart.time_unit_detect import calendar_bucket_key
from dbt_charts.core.render.chart.title_overflow import (
    compute_title_limit,
    prepare_title_text,
    resolve_title_overflow,
)
from dbt_charts.core.render.controls import controls_are_interactive
from dbt_charts.core.render.format_utils import MAGNITUDE_SUFFIXES, format_kpi_parts
from dbt_charts.core.render.script_embedding import embed_svg_script
from dbt_charts.core.render.svg_utils import authored_kind_attr, card_box, px
from dbt_charts.core.render.utils import (
    normalize_data_types,
    slug_to_text,
)
from dbt_charts.core.text.case import apply_case
from dbt_charts.core.text.numeral_scale import decimal_pad_for
from mdsvg.fonts import truncate_text_precise, wrap_text_precise

# Height of the pagination control bar (chevrons + page numbers).
_PAGINATION_CONTROL_HEIGHT = 28

# A static export pre-renders every page's rows into the artifact so
# pagination works without a JS runtime hitting a server (see
# static_multi_page below). An uncapped loop makes export size scale with
# total row count instead of page size, defeating pagination's purpose.
# Default page_rows (20) x this cap covers a generously large standalone
# table (400 rows) before the export starts truncating.
_STATIC_MULTI_PAGE_MAX_PAGES = 20

# Extra height reserved below the pager row when a static export hits the
# cap above: the "Showing pages 1-N of M" note gets its own line rather than
# sharing the row-range label's baseline (they collided pixel-for-pixel --
# both left-anchored at the same x/y -- before this reservation existed).
_PAGINATION_CAP_NOTE_HEIGHT = 20

# Engine politeness for table pagination. When total_rows would overflow the
# resolved page_rows by this many or fewer AND the rows physically fit in the
# available slot height, render every row on one page without pagination
# chrome. Paired with the layout-sizing-side grow-by-2 rule in
# ``_get_table_height_from_data`` so the slot is sized to fit (case b), and so
# constrained-slot cases also gain the politeness when the slot already has
# the room (e.g., card grew for a sibling chart).
_PAGINATION_GROW_CAP = 2

# Anti-dangle: collapse a small trailing page by squeezing row_height.
# Only fire when the overflow is 1-2 rows (not a "real" second page).
_ANTI_DANGLE_MAX_OVERFLOW = 2
# Maximum row_height reduction ratio (15%): 32px → 27px still reads fine.
_ANTI_DANGLE_MAX_SQUEEZE = 0.15
# Hard pixel floor: below 20px text clips descenders at our smallest body size.
_ANTI_DANGLE_MIN_ROW_H = 20

# Absorbs float-rounding noise on percentage-pinned column widths, not real
# overflow — a real overflow is always many orders of magnitude larger.
_WIDTH_OVERFLOW_EPSILON = 1e-6

# Sentinel column key for the synthetic row-number column. The key uses a
# private-use character that YAML-authored column names cannot reach, so
# style.columns lookups and conditional_formatting dicts keyed by column name
# never match it. It is transparent to column_configs and when_rules.
_ROW_NUMBER_COL = "\u0001__row_number__"

# Approximation of the ascent fraction Vega-Lite's title/subtitle text layout
# uses when placing a baseline within its line box. Fitted against the emitted
# role-title-text / role-title-subtitle baselines at all three width-tiered
# object-title sizes typography.py can produce (11px/14px/18px) — round(size *
# _TITLE_ASCENT_RATIO) reproduces Vega-Lite's title baseline (measured from the
# tile top) exactly at each. Reused for the title's own baseline placement and
# for its descent (the 1 - ratio remainder), so the table's hand-drawn layout
# tracks whatever title size the width tier picks instead of a flat constant
# that only matched the one size it was tuned against.
_TITLE_ASCENT_RATIO = 0.8


def _format_svg_numeric(value: float) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(numeric)


def _svg_font_family(family: str) -> str:
    """Normalize a CSS font-family string for an SVG presentation attribute.

    SVG ``font-family`` treats single quotes as literal characters rather than
    CSS string delimiters, so ``'Source Serif 4'`` must lose its quotes. The
    ``Source Serif 4 Web`` alias is prepended so browsers prefer the bundled
    woff2 over a locally installed system font.

    Both the sizer and the renderer measure against this string — measuring the
    raw family while painting the normalized one would wrap text to the wrong
    width.
    """
    normalized = family.replace("'", "").replace('"', "")
    if "Source Serif 4" in normalized and "Source Serif 4 Web" not in normalized:
        normalized = normalized.replace(
            "Source Serif 4", "Source Serif 4 Web, Source Serif 4"
        )
    return normalized


def _subtitle_baseline_below_title(
    title_baseline: float,
    title_font_size: float,
    title_subtitle_gap: float,
    subtitle_font_size: float,
) -> float:
    """Place the subtitle baseline below the title, using the same ascent/
    descent approximation (_TITLE_ASCENT_RATIO) the title's own baseline uses.

    title_subtitle_gap is pure whitespace between the title's descent and the
    subtitle's ascent — not a baseline-to-baseline distance. Deriving both
    ends from the font sizes (rather than a flat additive offset applied to a
    single font-size term) is what lets one gap constant track Vega-Lite's
    title->subtitle spacing across every width-tiered title size, not just
    the one size a flat constant would be tuned against.
    """
    title_descent = title_font_size * (1 - _TITLE_ASCENT_RATIO)
    subtitle_ascent = subtitle_font_size * _TITLE_ASCENT_RATIO
    return float(
        round(title_baseline + title_descent + title_subtitle_gap + subtitle_ascent)
    )


@dataclass(frozen=True)
class TableTitleBlockLayout:
    """Resolved title-block geometry shared between sizer and renderer.

    The renderer consumes every field; the sizer only reads ``height``. Both
    paths run through ``compute_table_title_block_layout`` so the height the
    sizer reserves matches what the renderer actually paints.
    """

    height: int
    rendered_title: str = ""
    title_lines: tuple[str, ...] = ()
    subtitle_lines: tuple[str, ...] = ()
    subtitle_font_size: float = 0.0


def compute_table_title_block_layout(
    *,
    chart_title: str,
    chart_subtitle: str,
    table_width: float,
    tc: TableChartStyle,
    padding: int,
    title_style: TitleStyle,
    card_padding: float,
    title_font: ResolvedFontStyle,
) -> TableTitleBlockLayout:
    """Resolve the title-block layout used by both renderer and sizer.

    Single source of truth — runs ``prepare_title_text`` once, computes
    subtitle font size once. The renderer reads every field for SVG
    emission; the sizer reads only ``height``.

    Args:
        card_padding: Board-level card padding.
    Returns a zero-height layout when ``chart_title`` is empty.
    """
    if not chart_title:
        return TableTitleBlockLayout(height=0)
    # Apply style.title.font.case before measuring/wrapping so the sizer's
    # height and the renderer's wrap width agree, and so the cased text — not
    # the authored text — drives downstream layout. Matches set_chart_title()
    # in spec_builders.py for Vega chart titles.
    _case = title_style.font.case
    if _case and _case != "none":
        chart_title = apply_case(chart_title, _case)
    title_font_size = int(title_font.size)
    title_font_family = title_font.family
    title_line_height = title_font_size + 2
    rendered_title, _ = prepare_title_text(
        chart_title,
        overflow=resolve_title_overflow(title_style),
        limit=compute_title_limit(table_width, {"left": padding, "right": padding}),
        font_size=title_font_size,
        font_family=title_font_family,
    )
    title_lines = tuple(rendered_title.splitlines() or [chart_title])
    # Derived from the title's own font size (_TITLE_ASCENT_RATIO), not a flat
    # theme constant — reproduces Vega-Lite's title baseline (measured from
    # the tile top) at every width-tiered title size, not just one.
    title_baseline = padding + float(round(title_font_size * _TITLE_ASCENT_RATIO))
    last_title_baseline = title_baseline + ((len(title_lines) - 1) * title_line_height)
    title_bottom = last_title_baseline + (title_font_size * 0.5)
    # Natural title-only height — no floor yet. The floor (tc.title_row.height)
    # only applies when a subtitle is present; without one the natural content
    # height is correct and the floor creates a visible dead gap.
    height = int(title_bottom - padding + 8)
    subtitle_font_size = 0.0
    subtitle_lines: tuple[str, ...] = ()
    if chart_subtitle:
        # Font size comes from style.title.subtitle — the same source every
        # chart family reads for its Vega-Lite subtitle — not a table-only
        # constant.
        assert title_style.subtitle.font.size is not None, (
            "theme must supply title.subtitle.font.size"
        )
        subtitle_font_size = float(title_style.subtitle.font.size)
        assert tc.font.family is not None, "style.font.family must be configured"
        subtitle_family = tc.font.family
        # Subtitles share the board's title.subtitle overflow mode with Vega
        # charts (apply_title_overflow_to_spec) — one knob, both render paths.
        rendered_subtitle, _ = prepare_title_text(
            chart_subtitle,
            overflow=resolve_title_overflow(title_style.subtitle),
            limit=compute_title_limit(table_width, {"left": padding, "right": padding}),
            font_size=subtitle_font_size,
            font_family=_svg_font_family(subtitle_family),
        )
        subtitle_lines = tuple(rendered_subtitle.splitlines() or [chart_subtitle])
        subtitle_line_height = subtitle_font_size + 2
        subtitle_baseline = _subtitle_baseline_below_title(
            title_baseline=last_title_baseline,
            title_font_size=title_font_size,
            title_subtitle_gap=tc.title_subtitle_gap,
            subtitle_font_size=subtitle_font_size,
        ) + ((len(subtitle_lines) - 1) * subtitle_line_height)
        subtitle_bottom = subtitle_baseline + (subtitle_font_size * 0.5)
        height = max(
            int(tc.title_row.height), height, int(subtitle_bottom - padding + 8)
        )
    return TableTitleBlockLayout(
        height=height,
        rendered_title=rendered_title,
        title_lines=title_lines,
        subtitle_lines=subtitle_lines,
        subtitle_font_size=subtitle_font_size,
    )


def _row_number_column_width(
    row_numbers: TableRowNumbersStyle,
    total_row_count: int,
    table_config: TableChartStyle,
    font: FontStyle,
    measurer: FontMeasurer,
) -> float:
    """Compute the synthetic row-number column width.

    Sized from ``total_row_count`` digit count — not per-page count — so the
    column width is the same on every page of a paginated table.
    """
    # size is derived from tc.font.size → float(font_size) when constructing _cell_font;
    # tc.font.size is guaranteed non-None by the cascade floor (_base.yaml).
    assert font.size is not None, (
        "font.size must be non-None (cascade floor: _base.yaml font.size)"
    )
    font_size = float(font.size)
    digits = max(len(str(max(total_row_count, 1))), len(row_numbers.header))
    text_w = max(
        measurer.measure("9" * digits, font_size),
        measurer.measure(row_numbers.header, font_size),
    )
    cell_pad = int(table_config.column_layout.cell_padding)
    return float(text_w + cell_pad * 2)


# CSS font stack for numeric table cells — matches Vega axisQuantitative config
_SANS_NUMERIC_FONT_STACK = (
    f"'{DBT_SANS_TABULAR_FONT_FAMILY}', Inter, system-ui, sans-serif"
)


def _table_numeric_cell_font(font_family: str | None) -> str:
    """Return the font family constant for numeric cells in this table.

    Source Serif themes use the serif face for numeric cells (CSS tabular-nums
    handles digit alignment). All other fonts use dbt Sans Tabular.
    """
    if font_family and "Source Serif" in font_family:
        return SOURCE_SERIF_4_FONT_FAMILY
    return DBT_SANS_TABULAR_FONT_FAMILY


def _is_numeric_cell(value: Any, col_config: TableColumnConfig | None) -> bool:
    """Determine if a cell should receive numeric styling (tabular font, three-lane layout).

    Checks the Python type first, then falls back to probing string values.
    A column with an explicit numeric format config (currency, percent, etc.)
    is always treated as numeric regardless of the runtime value type — CSV
    adapters deliver everything as strings.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True

    # If the column has a format config, treat it as numeric — the author
    # declared intent by attaching a number format.
    if col_config and col_config.format is not None:
        return True

    # Probe string values that look like numbers (from CSV adapters)
    if isinstance(value, str) and value:
        stripped = value.strip()
        if stripped:
            try:
                float(stripped)
                return True
            except ValueError:
                pass
    return False


def _wants_tabular_font(value: Any, col_config: TableColumnConfig | None) -> bool:
    """Return True if the cell should use tabular (monospaced-digit) font.

    Covers numbers AND date-like strings — anything where digits need to
    align vertically across rows.
    """
    return _is_numeric_cell(value, col_config) or is_date_like(value)


# Swatch column constants. Swatch cells render a small rounded color square
# instead of text — used for series-keyed tables (e.g. donut-attached tables
# where each row carries the parent chart's palette color). The square sits
# inside the cell with the standard cell-x padding (4) and is vertically
# centered against the row's effective band by the caller.
_SWATCH_SIZE = 14
_SWATCH_CORNER_RADIUS = 3


def _render_swatch_cell(value: Any, col: str, row_idx: int, chart_id: str) -> str:
    """Render a small rounded color square for a swatch-typed cell.

    Cell value must be a hex color string (e.g. ``#3164a3``) or one of the
    sanitizer-accepted keywords (``transparent`` / ``none``). Raises
    ``ChartDataError`` on any non-color value — a column declared
    ``swatch: true`` whose cell content is empty or non-color is a misconfig,
    not paint. Failing fast points the author at the wrong column / wrong
    data rather than silently shipping a half-broken table.
    """
    color = (
        sanitize_color(value, None)
        if isinstance(value, str) and is_sanitizable_color(value)
        else None
    )
    if not color:
        raise ChartDataError(
            f"Column {col!r} declares swatch: true but row {row_idx} value "
            f"{value!r} is not a CSS color. Swatch cells must be hex "
            f"(e.g. '#3164a3') or 'transparent'. Either point swatch: true "
            f"at a different column, or fix the data.",
            chart_id=chart_id,
        )
    return (
        f'<rect width="{_SWATCH_SIZE}" height="{_SWATCH_SIZE}" '
        f'rx="{_SWATCH_CORNER_RADIUS}" fill="{color}"/>'
    )


def _render_spark_cell(
    value: Any,
    spark_config: SparkConfig,
    cell_width: float,
    row_height: float,
    cell_font: FontStyle | None = None,
    resolved_style: ResolvedChartDefaults | None = None,
    column_max: float | None = None,
    has_negative: bool = False,
) -> tuple[str, int, int]:
    """Render a spark chart for a table cell.

    Args:
        value: Cell value (array for line/area/columns; scalar for bar/bar-normalize).
        spark_config: SparkConfig instance with typed configuration
        cell_width: Available cell width
        row_height: Nominal row height used to size the spark mark. The mark's
            visual size stays pinned to the nominal row height so wrapped
            rows don't grow their spark vertically — the caller centers the
            mark within the row's effective (possibly grown) height instead.
        cell_font: Table FontStyle to inherit for bar/bar-normalize value labels.
        column_max: Pre-computed column max for `bar` auto-max (None = use default).
        has_negative: True when the table column contains a negative value
            somewhere — bar/column switch to midline-anchored layout.

    Returns:
        ``(svg_content, spark_width, spark_height)``. ``svg_content`` is the
        inner SVG (sans wrapper) for embedding under a translate-wrapped
        ``<g>``; width + height are reported so the caller can position the
        mark within the cell — most marks fill the cell width, but narrow
        marks (`column`) need explicit alignment math. Empty string + 0 + 0
        when the value can't be rendered (e.g. non-numeric for bar variants).
    """
    from dbt_charts.core.render.chart.spark import render_spark

    if resolved_style is None:
        raise ValueError(
            "_render_spark_cell requires a resolved charts style (resolved_style)"
        )
    spark_type = spark_config.type

    # Calculate spark dimensions to fit in cell
    padding = 8
    if spark_type == "column":
        # `column` is a narrow vertical mark — don't stretch to the cell width
        # the way bar/columns do. Width default comes from theme
        # (style.spark.column.width); author can override via spark.width.
        spark_width = spark_config.width or int(resolved_style.spark.column.width)
    else:
        spark_width = spark_config.width or int(cell_width - (padding * 2))
    spark_height = spark_config.height or int(row_height - 8)

    # Cap dimensions
    spark_width = min(spark_width, int(cell_width - padding))
    spark_height = min(spark_height, int(row_height - 4))

    # Build options dict from SparkConfig (exclude type/width/height, drop None values)
    options = spark_config.model_dump(
        exclude={"type", "width", "height"},
        exclude_none=True,
    )

    # Auto-max: `bar` (no explicit ceiling) auto-scales width to the column
    # data max. `bar-normalize` always uses an explicit `max:` (or the theme
    # `default_max`) — the whole point of the variant is "% of ceiling," so
    # silently rescaling to the data max would defeat the contract.
    if spark_type == "bar" and spark_config.max is None and column_max is not None:
        options["max"] = column_max

    # For single-bar sparklines, only render when value is numeric and finite.
    # This lets mixed tables (text + numeric rows) use one spark config without
    # replacing text cells with zero-width bars, and applies the same null
    # rule as every other numeric-cell consumer (utils.coerce_numeric_cell):
    # NaN/±Infinity render nothing rather than a full-extent bar.
    if spark_type in ("bar", "bar-normalize", "column"):
        if coerce_numeric_cell(value) is None:
            return "", 0, 0

    # Render the spark SVG
    spark_svg = render_spark(
        value,
        spark_type,
        width=spark_width,
        height=spark_height,
        font=cell_font,
        has_negative=has_negative,
        resolved_style=resolved_style,
        **options,
    )

    # Extract inner content from SVG (remove wrapper)
    inner_match = re.search(r"<svg[^>]*>(.*?)</svg>", spark_svg, re.DOTALL)
    if inner_match is None:
        raise ValueError(f"render_spark returned invalid SVG: {spark_svg[:100]}")
    return inner_match.group(1), spark_width, spark_height


def _largest_safe_page_rows(
    row_heights: list[int], available: float, max_page_rows: int
) -> int:
    """Largest ``size <= max_page_rows`` such that every contiguous
    ``size``-row window sums to ≤ ``available``.

    Safe under mixed row heights: page 1 fitting doesn't imply page 2 fits.
    Returns 1 at minimum so the table always renders something.
    """
    n = len(row_heights)
    if n == 0:
        return 1
    for size in range(min(max_page_rows, n), 0, -1):
        if all(sum(row_heights[s : s + size]) <= available for s in range(0, n, size)):
            return size
    return 1


def _max_page_sum(row_heights: list[int], page_rows: int) -> int:
    """Max over every contiguous page-sized window of ``row_heights``.

    Shared between ``_resolve_visible_rows`` (table_height for paginated
    auto-height tables) and the more-rows indicator placement so both sit
    at the same y-coordinate across pages.
    """
    if not row_heights or page_rows <= 0:
        return 0
    return max(
        sum(row_heights[s : s + page_rows])
        for s in range(0, len(row_heights), page_rows)
    )


def _max_page_summary_gap(
    data: list[dict[str, Any]],
    row_role_spec: str | None,
    page_rows: int,
    gap: int,
) -> int:
    """Max, over every contiguous page-sized window of ``data``, of the
    summary/total-row breathing-room gap within that window.

    Mirrors ``_max_page_sum``: a static multi-page export paints every page
    into its own toggle group on one shared canvas, so sizing must budget
    for whichever page needs the most gap, not just the page the caller
    happens to be looking at. A single-page caller passes
    ``page_rows=len(data)`` so the "every window" loop degenerates to one
    window covering the whole list.
    """
    if not row_role_spec or not data or page_rows <= 0:
        return 0
    best = 0
    for s in range(0, len(data), page_rows):
        page = data[s : s + page_rows]
        total = 0
        for i, row in enumerate(page):
            if i == 0:
                continue
            role = resolve_row_role(row_role_spec, row)
            prev_role = resolve_row_role(row_role_spec, page[i - 1])
            if is_summary_role(role) and not is_summary_role(prev_role):
                total += gap
        best = max(best, total)
    return best


def _resolve_visible_rows(
    data: list[dict[str, Any]],
    height: float | None,
    title_height: float,
    header_height: float,
    padding: int,
    row_height: int,
    bottom_padding: int,
    pagination: PaginationConfig | None = None,
    page: int = 1,
    row_heights: list[int] | None = None,
    header_visible: bool = True,
    chart_id: str | None = None,
) -> tuple[float, list[dict[str, Any]], int, int, list[int] | None, int, int]:
    """Resolve table height, visible rows, total page count, page offset, and rows-height.

    Returns ``(table_height, visible_data, total_pages, page_offset,
    visible_row_heights, rows_height, effective_row_height)``.
    ``rows_height`` is the vertical extent used for the data-row section
    (tallest page when paginated + multi-page, else this page's own rows) —
    it sizes ``table_height`` so the card doesn't resize between pages.
    Pagination indicators do NOT place off this value: they place off the
    CURRENT page's own rows height instead (each caller computes that
    separately from ``visible_row_heights``), so a short page's pager sits
    right below its own last row rather than inheriting the tallest page's
    whitespace. The pager therefore sits at a different y on a short page
    than a tall one -- an accepted design trade-off (favors no dead space
    over a fixed pager position), not a bug.

    ``effective_row_height`` equals ``row_height`` unless the anti-dangle
    heuristic fired and squeezed it to collapse a 1-2 row trailing page.
    The caller must use this value for all per-row drawing so the squeezed
    geometry is consistent end-to-end.

    When ``row_heights`` is provided, pagination splitting and fixed-height
    row-count calculations operate on real cumulative heights; otherwise
    the uniform ``row_height`` is used.

    ``chart_id`` opts this call into recording a slot-squeezed page for
    TABLE_PAGE_SQUEEZED. Only the one call that produces the rendered table
    passes it — the layout probe and the per-page re-renders resolve the same
    rows again and would record the same squeeze twice.
    """
    # Chart-local pagination is pre-merged into ``pagination`` by the cascade —
    # read the resolved page_rows off the merged value. When pagination is
    # enabled but no page_rows is set, default to ``len(data)`` so the bounded
    # auto-shrink branch still fires: a small cell will then split into pages
    # rather than silently dropping rows into the "+ N more rows" footer.
    if pagination is not None and pagination.enabled:
        page_rows = (
            pagination.page_rows
            if pagination.page_rows is not None
            else (len(data) if data else 1)
        )
    else:
        page_rows = None
    page = max(1, page)

    # Header-body gap is the visual buffer between the header rule and the first
    # data row. When the header is hidden, this gap must collapse to zero so the
    # sizer (layout_sizing._get_table_height_from_data) and the renderer's final
    # placement (current_y bump after the header section, ~line 2762) agree on
    # total height. The squeezed-row path below mirrors the same conditional.
    header_body_gap = int(row_height * 0.25) if header_visible else 0

    def _slice_heights(start: int, count: int) -> list[int] | None:
        if row_heights is None:
            return None
        return row_heights[start : start + count]

    def _rows_height(visible_heights: list[int] | None, visible_n: int) -> int:
        if visible_heights is not None:
            return sum(visible_heights)
        return visible_n * row_height

    if height and height > 0:
        table_height = height
        available_height = (
            table_height
            - title_height
            - header_height
            - header_body_gap
            - padding
            - bottom_padding
        )
        if page_rows is not None:
            # Grow-by-2 short-circuit (engine politeness; pairs with the
            # layout-side grow rule in ``_get_table_height_from_data``).
            # When (a) every row physically fits in the available slot at its
            # natural row height, and (b) the overflow vs the resolved
            # page_rows is within the cap, render every row on one page with
            # no pagination chrome. This catches the case where the sizer
            # allocated extra room for an overflow ≤ _PAGINATION_GROW_CAP
            # rows and the renderer would otherwise still cap at page_rows.
            if row_heights is not None:
                total_data_height: float = float(sum(row_heights))
            else:
                total_data_height = float(len(data) * row_height)
            if (
                total_data_height <= available_height
                and len(data) <= page_rows + _PAGINATION_GROW_CAP
            ):
                # Shrink to content: the slot may be oversized (e.g. sizer
                # reserved a full page_rows height but the pivot table only
                # rendered 1 wide row). Return the content-sized height so the
                # table SVG never pads below the last row.
                # Note: for tables in rows/stack layouts, actual_item_height
                # propagates the shrunk height and next-tile offsets collapse.
                # For tables in cols/equalized layouts, the card is floored at
                # the tallest sibling (boards.py:1233), so the whitespace moves
                # to between the table and the card edge rather than disappearing
                # entirely. Full elimination in cols requires also fixing the
                # sizer's pivot row count (Solution A).
                content_table_height = (
                    title_height
                    + header_height
                    + header_body_gap
                    + total_data_height
                    + padding
                    + bottom_padding
                )
                return (
                    content_table_height,
                    data,
                    1,
                    0,
                    row_heights,
                    int(total_data_height),
                    row_height,
                )

            # Reserve pagination control height whenever pagination will
            # fire — either because page_rows splits the data, or because
            # the allotted height fits fewer rows than the data.
            #
            # With variable row heights, the probe must check every page's
            # window, not just the leading rows — otherwise a short page 1
            # leads to a picked `effective` that overflows page 2.
            def _safe_page_rows(avail: float) -> int:
                if row_heights is not None:
                    return _largest_safe_page_rows(row_heights, avail, page_rows)
                return min(max(1, int(avail / row_height)), page_rows)

            probe_effective = _safe_page_rows(available_height)
            if len(data) > probe_effective:
                overflow = len(data) - probe_effective
                # Anti-dangle: if the overflow is ≤ 2 rows, try squeezing
                # row_height to fit all rows in available_height.  Only fire
                # when row_heights is None (uniform rows); variable-height
                # tables have row-specific measurements that can't be rescaled.
                #
                # Known pre-existing limitation: when row_role_spec inserts
                # summary gaps (~0.4 * row_height per summary transition),
                # available_height here does not subtract that reservation,
                # so the squeezed layout can exceed the bounded card height
                # by ~13px per gap. The non-squeeze bounded path below has
                # the same blind spot. Fixing requires plumbing
                # row_role_spec into pick_table_layout so we can count gaps
                # over the visible slice; deferred pre-launch.
                if row_heights is None and 0 < overflow <= _ANTI_DANGLE_MAX_OVERFLOW:
                    squeezed = max(
                        _ANTI_DANGLE_MIN_ROW_H, int(available_height // len(data))
                    )
                    squeeze_ratio = squeezed / row_height
                    if (
                        squeeze_ratio >= (1.0 - _ANTI_DANGLE_MAX_SQUEEZE)
                        and squeezed * len(data) <= available_height
                    ):
                        squeezed_rh = len(data) * squeezed
                        return (
                            table_height,
                            data,
                            1,
                            0,
                            None,
                            squeezed_rh,
                            squeezed,
                        )
                # Pagination controls will render — re-pick effective with
                # the reduced budget so rows leave room for the controls.
                effective = _safe_page_rows(
                    available_height - _PAGINATION_CONTROL_HEIGHT
                )
            else:
                effective = probe_effective
            # The slot, not the table, decided the page size: the sizer reserved
            # room for `requested` rows and only `effective` fit. Record it so
            # TABLE_PAGE_SQUEEZED can say so — otherwise the export photographs
            # as a faithful table showing a fraction of the data.
            requested = min(page_rows, len(data))
            if chart_id is not None and effective < requested:
                record_table_page_squeeze(
                    chart_id,
                    TablePageSqueeze(
                        drawn_rows=effective,
                        page_rows=requested,
                        total_rows=len(data),
                    ),
                )
            total_pages = max(1, -(-len(data) // effective)) if data else 1
            page = min(page, total_pages)
            start = (page - 1) * effective
            visible_heights = _slice_heights(start, effective)
            # Use the tallest page's rows_height for table_height, so the
            # card doesn't resize as the user clicks prev/next -- callers
            # place the pager off the CURRENT page's own rows instead (see
            # this function's docstring), so this value governs sizing only.
            if row_heights is not None and total_pages > 1:
                rows_height_out = _max_page_sum(row_heights, effective)
            else:
                rows_height_out = _rows_height(visible_heights, effective)
            return (
                table_height,
                data[start : start + effective],
                total_pages,
                start,
                visible_heights,
                rows_height_out,
                row_height,
            )
        # With pagination disabled, do not reinterpret the slot height as a row
        # limit. Fall through to the natural-height path so the static table stays
        # truthful instead of hiding rows behind a non-actionable "+ N more rows".

    if page_rows is not None:
        n_data = len(data)
        overflow = n_data - page_rows
        # Anti-dangle (unbounded path): if a 1-2 row tail would create a
        # second page, squeeze row_height to fit all rows on one page.
        # Only fire for uniform-height rows (row_heights is None).
        if (
            row_heights is None
            and n_data > 0
            and 0 < overflow <= _ANTI_DANGLE_MAX_OVERFLOW
        ):
            squeeze_ratio = page_rows / n_data
            if squeeze_ratio >= (1.0 - _ANTI_DANGLE_MAX_SQUEEZE):
                squeezed = max(
                    _ANTI_DANGLE_MIN_ROW_H, int(round(row_height * squeeze_ratio))
                )
                squeezed_rows_height = squeezed * n_data
                squeezed_header_body_gap = int(squeezed * 0.25) if header_visible else 0
                squeezed_table_height = (
                    title_height
                    + header_height
                    + squeezed_header_body_gap
                    + squeezed_rows_height
                    + padding
                    + bottom_padding
                )
                return (
                    squeezed_table_height,
                    data,
                    1,
                    0,
                    None,
                    squeezed_rows_height,
                    squeezed,
                )
        total_pages = max(1, -(-n_data // page_rows)) if data else 1
        page = min(page, total_pages)
        start = (page - 1) * page_rows
        visible_data = data[start : start + page_rows]
        visible_heights = _slice_heights(start, page_rows)
        # Size to the tallest page so every page renders at the same height.
        if row_heights is not None and total_pages > 1:
            rows_height = _max_page_sum(row_heights, page_rows)
        else:
            # Use page_rows (not len(visible_data)) for uniform height across
            # pages when paginating uniform-height rows; the last page may
            # have fewer rows but keeps the same table height.
            row_slots = page_rows if total_pages > 1 else len(visible_data)
            rows_height = _rows_height(visible_heights, row_slots)
        table_height = (
            title_height
            + header_height
            + header_body_gap
            + rows_height
            + padding
            + bottom_padding
        )
        return (
            table_height,
            visible_data,
            total_pages,
            start,
            visible_heights,
            rows_height,
            row_height,
        )

    num_rows = len(data)
    rows_height = sum(row_heights) if row_heights is not None else num_rows * row_height
    table_height = (
        title_height
        + header_height
        + header_body_gap
        + rows_height
        + padding
        + bottom_padding
    )
    return table_height, data, 1, 0, row_heights, rows_height, row_height


def _compute_content_span(
    columns: list[str],
    col_widths: dict[str, float],
    col_x_offsets: list[float],
    padding_x: int,
    cell_pad: float,
    table_width: float,
) -> tuple[float, float]:
    """Compute (x1, x2) for horizontal elements spanning all columns.

    Returns the cell-content edge: from the first column's content start
    to the last column's content end. Used for header background, header
    rules (continuous), row stripes, row rules, and summary/total rules
    — all horizontal spans use this one function so they share a visible
    right edge.

    The cell-content edge sits cell_pad inside the outer cell bounds,
    matching where text is already laid out. That gives text and rects
    a shared visible edge.
    """
    if not columns or not col_x_offsets:
        return 0, table_width
    first_cell_x = padding_x + col_x_offsets[0]
    last_col = columns[-1]
    last_cell_x = padding_x + col_x_offsets[-1]
    last_cw = col_widths.get(last_col, 100)
    x1 = first_cell_x + cell_pad
    x2 = last_cell_x + last_cw - cell_pad
    return x1, x2


def _render_pivot_group_header(
    svg_parts: list[str],
    *,
    levels: list[list[tuple[str, int, int]]],
    columns: list[str],
    colors: dict[str, str],
    table_width: float,
    group_row_height: int,
    current_y: float,
    padding_x: int,
    col_x_offsets: list[float],
    col_widths: dict[str, float],
    header_font: FontStyle,
    cell_pad: int,
) -> None:
    """Render N group-label rows for a multi-dim or multi-measure pivot table.

    Draws one header row per descriptor level, stacked top→bottom (outermost
    column dim on top, measure level at the bottom when present). Each span label
    is centered horizontally over the leaf columns it covers.

    ``levels`` is a list of descriptor levels, each being a list of
    ``(label, first_leaf_idx, n_leaves)`` where ``first_leaf_idx`` is 0-indexed
    into the leaf portion of ``columns`` (after any non-leaf columns).

    Invariant: leaf columns are the ONLY columns whose key contains
    ``_PIVOT_LEAF_SEP`` (the only producer of that key shape; the separator is
    illegal in SQL aliases). Every other entry in ``columns`` — row-dimension fields
    AND any synthetic injected column such as ``_ROW_NUMBER_COL`` — precedes the
    leaves and is counted as a non-leaf below, so ``n_row_dims`` aligns the group
    spans with ``col_x_offsets`` even when row numbers are enabled.
    """
    assert header_font.size is not None, "header_font.size must be set"
    assert header_font.weight is not None, "header_font.weight must be set"
    font_size = int(header_font.size)
    font_weight = str(header_font.weight)
    font_family = header_font.family or ""

    # How many non-leaf columns (row dims + any synthetic columns) precede the leaves?
    n_row_dims = sum(1 for c in columns if _PIVOT_LEAF_SEP not in c)

    rule_color = colors.get("label_color") or colors["color"]
    _RULE_H = 1.0
    _hdr_bg = colors["header_background"]

    row_y = current_y
    for level in levels:
        # Background for this group row.
        if _hdr_bg and _hdr_bg.lower() != "transparent":
            svg_parts.append(
                f'<rect x="{padding_x}" y="{row_y}" '
                f'width="{table_width - 2 * padding_x}" height="{group_row_height}" '
                f'fill="{_hdr_bg}"/>',
            )

        label_y = (
            row_y
            + group_row_height / 2
            + centered_baseline_offset(font_family, font_size)
        )

        for group_label, first_leaf_idx, n_leaves in level:
            abs_first = n_row_dims + first_leaf_idx
            abs_last = abs_first + n_leaves - 1
            x_left = padding_x + col_x_offsets[abs_first]
            last_col_key = columns[abs_last]
            x_right = (
                padding_x + col_x_offsets[abs_last] + col_widths.get(last_col_key, 0)
            )
            center_x = (x_left + x_right) / 2

            # Separator rule along the bottom of this span.
            rule_y = row_y + group_row_height - _RULE_H
            if x_right > x_left + cell_pad:
                svg_parts.append(
                    f'<rect x="{x_left + cell_pad}" y="{rule_y}" '
                    f'width="{x_right - x_left - 2 * cell_pad}" height="{_RULE_H}" '
                    f'fill="{rule_color}" shape-rendering="crispEdges"/>',
                )

            escaped = html_module.escape(str(group_label))
            svg_parts.append(
                f'<text x="{center_x}" y="{label_y}" '
                f'font-size="{font_size}" font-weight="{font_weight}" '
                f'fill="{colors["label_color"]}" text-anchor="middle" '
                f'font-family="{font_family}">'
                f"{escaped}</text>",
            )

        row_y += group_row_height


def _render_header_section(
    svg_parts: list[str],
    columns: list[str],
    column_configs: Mapping[str, TableColumnConfig],
    colors: dict[str, str],
    table_config: TableChartStyle,
    table_width: float,
    header_height: float,
    current_y: float,
    padding_x: int,
    col_x_offsets: list[float],
    col_widths: dict[str, float],
    col_lane_positions: dict[str, tuple[float, float, float, float, float]],
    header_font: FontStyle,
    truncated_headers: dict[str, bool],
    wrapped_headers: dict[str, list[str]] | None = None,
    cell_pad: int | None = None,
    header_rule_width: float = 1.0,
    rule_color: str | None = None,
    header_rule_continuous: bool = False,
    row_numbers: TableRowNumbersStyle | None = None,
    chart_id: str = "",
) -> None:
    """Render header background, border, and labels."""
    # size is derived from header_font_size (= tc.header.font.size or body font_size);
    # weight is derived from tc.header.font.weight, guaranteed by _base.yaml floor.
    # Both are always set when constructing _header_font — None here is a cascade bug.
    assert header_font.size is not None, (
        "header_font.size must be non-None (set at _header_font construction)"
    )
    assert header_font.weight is not None, (
        "header_font.weight must be non-None (cascade floor: _base.yaml table.header.font.weight)"
    )
    header_font_size = int(header_font.size)
    header_font_weight = str(header_font.weight)
    effective_font_family = header_font.family
    # Header background paints edge-to-edge across the full column span.
    bg_x1, bg_x2 = _compute_content_span(
        columns=columns,
        col_widths=col_widths,
        col_x_offsets=col_x_offsets,
        padding_x=padding_x,
        cell_pad=0,
        table_width=table_width,
    )
    # Skip the header background rect when transparent — no point emitting
    # invisible paint.
    _hdr_bg = colors["header_background"]
    if _hdr_bg and _hdr_bg.lower() != "transparent":
        svg_parts.append(
            f'<rect x="{bg_x1}" y="{current_y}" width="{bg_x2 - bg_x1}" '
            f'height="{header_height}" '
            f'fill="{_hdr_bg}"/>',
        )
    effective_rule_color = rule_color or colors["color"]
    if header_rule_width > 0:
        # Rules rendered as <rect> for consistent thickness at any browser
        # scale. Integer y + integer height = exact pixel coverage.
        rule_y = int(current_y + header_height - header_rule_width)
        effective_pad = (
            cell_pad
            if cell_pad is not None
            else table_config.column_layout.cell_padding
        )
        if header_rule_continuous and columns and col_x_offsets:
            # Single unbroken rule spanning the cell-content edge — same
            # extent as stripes and row rules so all right edges align.
            x1, x2 = _compute_content_span(
                columns=columns,
                col_widths=col_widths,
                col_x_offsets=col_x_offsets,
                padding_x=padding_x,
                cell_pad=effective_pad,
                table_width=table_width,
            )
            svg_parts.append(
                f'<rect x="{x1}" y="{rule_y}" width="{x2 - x1}" '
                f'height="{header_rule_width}" fill="{effective_rule_color}" '
                f'shape-rendering="crispEdges"/>',
            )
        else:
            # Per-column rules: EVERY rule centers on the cell midpoint,
            # matching where the header text and number tspan center.
            # Rule width = max(widest wrapped header line, value lane
            # extent) so the rule always covers whatever is visually
            # anchored above it.  Text columns (no lane) use the widest
            # header line plus minimal breathing.
            _RULE_GAP = 4  # min px gap between adjacent per-column rules
            measurer = get_font_measurer(effective_font_family)
            resolved_wrapped = wrapped_headers or {}
            for i, col in enumerate(columns):
                cw = col_widths.get(col, 100)
                cell_x = padding_x + col_x_offsets[i]
                cell_left_bound = cell_x + _RULE_GAP
                cell_right_bound = cell_x + cw - _RULE_GAP

                col_config = column_configs.get(col)
                display_name = (
                    col_config.label if col_config and col_config.label else col
                )
                if display_name == col:
                    display_name = slug_to_text(display_name)
                # Use the WIDEST WRAPPED LINE (what's actually rendered),
                # not the full unwrapped name.
                lines = resolved_wrapped.get(col) or [display_name]
                widest_line = max(lines, key=len)
                label_w = measurer.measure(widest_line, header_font_size)

                if col in col_lane_positions:
                    # Numeric column: center rule on cell midpoint.
                    # Rule width covers the widest content element — the
                    # header line or the value lane, whichever is wider.
                    _, _, _, content_left, content_right = col_lane_positions[col]
                    lane_w = content_right - content_left
                    rule_w = max(label_w, lane_w)
                    center = cell_x + cw / 2
                    x1 = center - rule_w / 2
                    x2 = center + rule_w / 2
                else:
                    # Text column: header is left-aligned, so the rule is
                    # too.  Width at least spans the label.
                    x1 = cell_x + effective_pad
                    x2 = x1 + max(label_w, cw - 2 * effective_pad)

                # Clamp to cell + gap so adjacent column rules don't touch.
                x1 = max(x1, cell_left_bound)
                x2 = min(x2, cell_right_bound)
                if x2 > x1:
                    svg_parts.append(
                        f'<rect x="{x1}" y="{rule_y}" width="{x2 - x1}" '
                        f'height="{header_rule_width}" fill="{effective_rule_color}" '
                        f'shape-rendering="crispEdges"/>',
                    )

    header_line_height = header_font_size + table_config.text_baseline_offset
    resolved_wrapped_headers = wrapped_headers or {}

    # Bottom-align all headers: position every header so its last line
    # sits at the same baseline, creating a firm line before values start.
    bottom_baseline = current_y + header_height - table_config.text_baseline_offset

    for i, col in enumerate(columns):
        cell_x = padding_x + col_x_offsets[i]

        effective_pad = (
            cell_pad
            if cell_pad is not None
            else table_config.column_layout.cell_padding
        )
        cw = col_widths.get(col, 100)

        # Synthetic row-number column: render row_numbers.header with the
        # author-selected alignment. Skip all column-config lookups.
        if col == _ROW_NUMBER_COL and row_numbers is not None:
            display_name = row_numbers.header
            text_fill = colors["label_color"]
            if row_numbers.align == "right":
                x = cell_x + cw - effective_pad
                anchor = "end"
            else:
                x = cell_x + effective_pad
                anchor = "start"
            y = bottom_baseline
            escaped_name = html_module.escape(display_name)
            svg_parts.append(
                f'<text x="{x}" y="{y}" '
                f'font-size="{header_font_size}" font-weight="{header_font_weight}" fill="{text_fill}" '
                f'text-anchor="{anchor}" '
                f'font-family="{effective_font_family}">'
                f"{escaped_name}</text>",
            )
            continue

        col_config = column_configs.get(col)
        display_name = (col_config.label if col_config else None) or slug_to_text(col)
        _header_case = header_font.case or "none"
        display_name = apply_case(display_name, _header_case)
        display_lines = resolved_wrapped_headers.get(col) or [display_name]
        header_link = col_config.header_link if col_config else None
        text_fill = colors["label_color"]

        # STRONG CENTER-ON-MIDPOINT INVARIANT:
        #   - Numeric/date-lane columns: header centers on cell midpoint
        #     (same point where the value tspan centers and the per-column
        #     rule centers).
        #   - align: "center" columns without a lane: header centers on the
        #     cell midpoint too, matching the cell branch's own center path
        #     (which is independent of lane membership).
        #   - Everything else, including a no-lane ``align: "right"``
        #     column: header left-aligns at cell_x + pad. Unlike centering,
        #     a right-anchored header would not visually pair with
        #     flush-right cells the way a centered header pairs with
        #     centered cells, so this stays left — matches the pre-existing,
        #     unchanged behavior for a right-aligned text column.
        # If the header would overflow, the wrap path shrinks or
        # token-splits it in ``resolve_wrapped_headers``.
        if col in col_lane_positions or (
            col_config is not None and col_config.align == "center"
        ):
            x = cell_x + cw / 2
            anchor = "middle"
        else:
            x = cell_x + effective_pad
            anchor = "start"

        if header_link:
            escaped_href = html_module.escape(header_link, quote=True)
            svg_parts.append(f'<a href="{escaped_href}">')

        if len(display_lines) > 1:
            lines = display_lines
            start_y = bottom_baseline - (len(lines) - 1) * header_line_height
            is_truncated = bool(truncated_headers.get(col))
            if is_truncated and chart_id:
                record_text_truncation(chart_id, "table_header", display_name, col)
            multi_title = (
                f"<title>{html_module.escape(display_name)}</title>"
                if is_truncated
                else ""
            )
            svg_parts.append(
                f'<text x="{x}" '
                f'font-size="{header_font_size}" font-weight="{header_font_weight}" fill="{text_fill}" '
                f'text-anchor="{anchor}" '
                f'font-family="{effective_font_family}">'
                f"{multi_title}",
            )
            for li, line in enumerate(lines):
                ly = start_y + li * header_line_height
                escaped_line = html_module.escape(line)
                svg_parts.append(f'<tspan x="{x}" y="{ly}">{escaped_line}</tspan>')
            svg_parts.append("</text>")
        else:
            y = bottom_baseline
            visible = display_lines[0]
            escaped_name = html_module.escape(visible)
            is_truncated = bool(truncated_headers.get(col))
            if is_truncated and chart_id:
                record_text_truncation(chart_id, "table_header", display_name, col)
            title_attr = (
                f"<title>{html_module.escape(display_name)}</title>"
                if is_truncated
                else ""
            )
            svg_parts.append(
                f'<text x="{x}" y="{y}" '
                f'font-size="{header_font_size}" font-weight="{header_font_weight}" fill="{text_fill}" '
                f'text-anchor="{anchor}" '
                f'font-family="{effective_font_family}">'
                f"{title_attr}{escaped_name}</text>",
            )

        if header_link:
            svg_parts.append("</a>")


_FIT_BREATH = 4  # px breathing room — content shouldn't kiss cell edges


def _has_overflow(
    columns: list[str],
    data: list[dict[str, Any]],
    column_configs: Mapping[str, TableColumnConfig],
    col_widths: dict[str, float],
    cell_pad: int,
    cell_font: FontStyle,
    *,
    header_font: FontStyle | None = None,
    wrap: bool,
    formats: dict[str, str] | None = None,
    header_visible: bool = True,
    column_when_rules: Mapping[str, tuple[Any, ...]],
) -> bool:
    """Return True if any column's widest value OR header exceeds its content area.

    When *header_font* is given, also checks whether the column label
    (display name) fits in a single line.  Headers that would need to wrap
    count as overflow so the fit cascade can shrink fonts to avoid awkward
    line breaks.

    When *wrap* is True, text cells are skipped — they'll wrap rather than
    overflow, so only numeric/date columns drive the cascade.

    When *header_visible* is False, the header label is excluded from the
    overflow check — a hidden header can't drive the fit cascade.
    """
    # size is derived from tc.font.size (cascade floor: _base.yaml) → font_size → _cell_font.size
    assert cell_font.size is not None, (
        "cell_font.size must be non-None (cascade floor: _base.yaml font.size)"
    )
    font_size = float(cell_font.size)
    measurer = get_font_measurer(cell_font.family)
    for col in columns:
        col_config = column_configs.get(col)
        # Swatch columns render a fixed 14×14 <rect>, not the cell text. A 7-char
        # hex string in a 24px-wide swatch column would otherwise trip the
        # text-overflow check and kick off the table-wide fit cascade.
        if col_config and col_config.swatch:
            continue
        fmt = col_config.format if col_config else None
        cw = col_widths.get(col, 100)
        content_area = cw - cell_pad * 2 - _FIT_BREATH

        # Check header label overflow (single-line fit) — only for short
        # labels in multi-column layouts where wrapping looks awkward.
        # Long headers in single-column tables should wrap gracefully.
        # Skipped when header.visible: false — a hidden label can't overflow.
        if header_font is not None and header_visible and len(columns) > 1:
            _hfs = (
                float(header_font.size) if header_font.size is not None else font_size
            )
            display_name = col_config.label if col_config and col_config.label else col
            display_name = (
                slug_to_text(display_name) if display_name == col else display_name
            )
            _header_case = header_font.case or "none"
            display_name = apply_case(display_name, _header_case)
            # Only flag short labels (≤12 chars) as overflow candidates —
            # "Growth", "Margin" shouldn't wrap, but "Opportunities
            # Subscription Start Date" is fine to wrap.
            if len(display_name) <= 12:
                header_w = measurer.measure(display_name, _hfs)
                if header_w > content_area:
                    return True

        glyph_possible = _glyph_possible(column_configs, column_when_rules, col)
        for row in data[:50]:
            val = row.get(col, "")
            # In wrap mode, text cells wrap instead of overflow — skip them.
            if wrap and not (_is_numeric_cell(val, col_config) or is_date_like(val)):
                continue
            shared_scale = (
                col_config.shared_scale
                if isinstance(col_config, ResolvedTableColumnConfig)
                else None
            )
            try:
                num_val = float(val) if isinstance(val, str) else val
            except (ValueError, TypeError):
                num_val = val
            if (
                shared_scale is not None
                and isinstance(num_val, (int, float))
                and not isinstance(num_val, bool)
            ):
                # A shared-scale column's real cell text is the scaled digit
                # string, not the whole-precision SI string
                # format_table_cell_value would render -- measure what will
                # actually paint. Not always narrower: a REPEAT-mode column
                # with a wide magnitude spread can render more digits than
                # native SI notation would ("876.54M" vs "877 M"), so this
                # can also make the fit cascade fire *more* often than
                # before -- correctly, since that's the width the column
                # will really occupy. No guard needed here: shared_scale is
                # only ever set when is_d3_si_spec(fmt) is true, which is
                # false for every time format -- the two are mutually
                # exclusive by construction (compile/resolve/chart/_table.py).
                _p, _n, _s = format_kpi_parts(
                    num_val,
                    fmt,
                    formats,
                    default_number=True,
                    shared_scale=shared_scale,
                    is_anchor=True,
                )
                rendered = _p + _n + _s
            else:
                rendered = format_table_cell_value(val, fmt, formats)
            if glyph_possible:
                rendered = (
                    cell_glyph_run(column_configs, column_when_rules, col, val)
                    + rendered
                )
            text_w = measurer.measure(rendered, font_size)
            if text_w > content_area:
                return True
    return False


def _compute_lane_positions(
    rows: list[dict[str, Any]],
    columns: list[str],
    column_configs: Mapping[str, ResolvedTableColumnConfig],
    col_widths: dict[str, float],
    col_x_offsets: list[float],
    padding_x: int,
    cell_pad: int,
    cell_font: FontStyle,
    column_when_rules: Mapping[str, tuple[Any, ...]],
    formats: dict[str, str] | None = None,
) -> dict[str, tuple[float, float, float, float, float]]:
    """Compute fixed (prefix_x, number_x, suffix_x, content_left, content_right) per numeric column.

    Positions are constant across all rows so currency symbols, magnitude
    letters, and percent signs form clean vertical columns.

    **Center-on-midpoint invariant** (see the Implementation philosophy in this package's ``AGENTS.md``): the number tspan
    is anchored so its horizontal center sits at the cell's content
    midpoint. Because the tspan renders with ``text-anchor="end"``, that
    means its right edge (``number_x``) sits at
    ``cell_midpoint + max_number_w / 2``. Header and per-column rule both
    center on the same midpoint — everything reads as one column.

    ``content_left`` and ``content_right`` mark the extent of the full
    content including prefix and suffix; they're what the header rule
    uses to size itself.

    A column earns a lane (a ``positions[col]`` entry) when either: any cell
    is numeric-and-not-date-like (the numeric three-lane invariant, always
    on and unconditional on ``align`` — see the table render AGENTS.md), or
    the column's resolved ``align`` is ``"right"`` (the verdict
    ``fill_table_column_defaults``/``classify_date_column_align`` already
    finalized at resolve or render's own leaf synthesis — this function
    trusts it rather than re-deciding alignment) *and* the column actually
    has date-like content, so a plain right-aligned text column (a pie
    attachment's "share" percent-string column, e.g.) never earns a
    content-fit lane just because an author right-aligned it for unrelated
    reasons.
    """
    # numeric=True critical here: numeric cells render with the dbt Sans
    # Tabular stack (tabular digit widths), which are noticeably wider
    # than Inter's proportional digits.  Measuring with Inter would
    # under-estimate content width by ~4px per number at 11px, pushing
    # the prefix right on top of the value.
    # size is derived from tc.font.size (cascade floor: _base.yaml) → font_size → _cell_font.size
    assert cell_font.size is not None, (
        "cell_font.size must be non-None (cascade floor: _base.yaml font.size)"
    )
    font_size = float(cell_font.size)
    measurer = get_font_measurer(cell_font.family, numeric=True)
    positions: dict[str, tuple[float, float, float, float, float]] = {}
    for i, col in enumerate(columns):
        col_config = column_configs.get(col)
        fmt = col_config.format if col_config else None
        # resolve_format is pure; resolve once per column, not per row.
        resolved_fmt = resolve_format(fmt, formats)
        cw = col_widths.get(col, 100)
        cell_x = padding_x + col_x_offsets[i]
        # Content midpoint respects horizontal padding on both sides so
        # that if a caller configures asymmetric padding, the midpoint
        # still sits in the middle of the *visible content area*.
        content_area_left = cell_x + cell_pad
        content_area_right = cell_x + cw - cell_pad
        cell_midpoint = (content_area_left + content_area_right) / 2

        max_content_w = 0.0
        max_prefix_w = 0.0
        max_suffix_w = 0.0
        col_suffix = ""
        is_date_col = False
        when_rules = column_when_rules.get(col)
        glyph_possible = _glyph_possible(column_configs, column_when_rules, col)
        # The date path trusts col_config.align — the verdict resolve (or
        # render's own pivot/transpose leaf synthesis) already finalized via
        # classify_date_column_align — as the single source for *whether*
        # this column right-aligns; render never re-decides that question.
        # has_any_date_like_cell is a narrower, different question: given
        # the column IS right-aligned, does it actually have date-shaped
        # content, so its cells fold via the date measurer/clamp path below
        # rather than being flush-anchored text (see the align == "right"
        # fallback further down)? A plain right-aligned text column (a pie
        # attachment's "share" percent-string column, e.g.) has no
        # date-like cells and so never enters this branch at all.
        column_is_date_right = (
            col_config is not None
            and col_config.align == "right"
            and any(is_date_like(v) for v in (row.get(col) for row in rows))
        )
        # A date-shaped outlier inside an otherwise-numeric column (e.g. a
        # bare year mixed with real numbers) still needs to land in that
        # column's existing numeric lane — invariant 11's numeric three-lane
        # path is unconditional on align, so this fold can't gate on
        # column_is_date_right alone or the outlier renders outside the
        # lane its neighbors already sized.
        has_numeric_cell = any(
            _is_numeric_cell(v, col_config) and not is_date_like(v)
            for v in (row.get(col) for row in rows)
        )
        for row in rows:
            raw = row.get(col, "")
            # Mirror the render-side lane guard (`not is_date_like`): a year
            # string like "2024" is numeric-ish but renders verbatim via
            # format_table_cell_value, so it must not be lane-sized as an SI
            # number here or the measured width drifts from the rendered text.
            if _is_numeric_cell(raw, col_config) and not is_date_like(raw):
                try:
                    num_val = float(raw) if isinstance(raw, str) else raw
                except (ValueError, TypeError):
                    continue
                if not isinstance(num_val, (int, float)) or isinstance(num_val, bool):
                    continue
                # format_kpi_parts (below) bypasses format_table_cell_value's
                # own guard against a time format on a numeric column; this
                # gate (a real int/float survives the coercion above) is the
                # same one format_table_cell_value's numeric branch uses, so
                # a legitimately time-formatted date/timestamp cell -- which
                # never reaches this point -- is never flagged.
                _raise_if_time_format_on_numeric(resolved_fmt)
                # is_anchor=True unconditionally: this column's suffix lane
                # must be sized for the widest case that will actually
                # paint -- the anchor row's real suffix in ANCHOR mode, or
                # every row's real suffix in REPEAT mode (is_anchor is a
                # no-op there, mode already forces the suffix on). Measuring
                # with is_anchor=False would size an ANCHOR-mode column's
                # suffix lane to zero, since every row would report an empty
                # suffix, even though the anchor row itself renders one.
                _p, num_str, _s = format_kpi_parts(
                    num_val,
                    fmt,
                    formats,
                    default_number=True,
                    shared_scale=col_config.shared_scale if col_config else None,
                    is_anchor=True,
                )
                if not col_suffix and _s:
                    col_suffix = _s
                # Decimal-alignment pad: append before measuring so max_content_w
                # accounts for the padded advance (measurement and paint must
                # both read the same padded string).
                if col_config is not None and col_config.decimal_pad_table:
                    num_str += decimal_pad_for(col_config.decimal_pad_table, num_str)
                num_str = num_str.removeprefix("-")
                w = measurer.measure(num_str, font_size)
                # Glyph replaces the format prefix in the prefix lane,
                # so measure whichever the cell will render.
                cell_glyph: str | None = None
                if glyph_possible:
                    cell_glyph, _ = resolve_cell_glyph(col_config, raw, when_rules)
                effective_prefix = cell_glyph or _p
                if effective_prefix:
                    max_prefix_w = max(
                        max_prefix_w,
                        measurer.measure(effective_prefix, font_size),
                    )
                if _s:
                    max_suffix_w = max(
                        max_suffix_w,
                        measurer.measure(_s, font_size),
                    )
                max_content_w = max(max_content_w, w)
            elif not (raw is None or (isinstance(raw, str) and not raw.strip())) and (
                # A date-right column folds every remaining non-blank cell
                # (not just the ones that individually match is_date_like —
                # acceptance criterion: the lane must reflect the column's
                # real content, not the detector-matching subset). A
                # numeric column's date-shaped outlier only folds when it
                # actually looks like a date, since has_numeric_cell alone
                # says nothing about this specific cell.
                column_is_date_right or (has_numeric_cell and is_date_like(raw))
            ):
                # Measure with the board this cell actually renders with —
                # _wants_tabular_font is False for content the date
                # patterns don't recognize, and the shared `measurer` above
                # is tabular-only.
                is_date_col = True
                cell_measurer = get_font_measurer(
                    cell_font.family, numeric=_wants_tabular_font(raw, col_config)
                )
                w = cell_measurer.measure(
                    format_table_cell_value(raw, fmt, formats), font_size
                )
                # _render_data_rows wraps/truncates this same string to the
                # cell, so an unclamped lane pushes number_x past the
                # column edge whenever the column is narrower than its
                # widest value (see test_date_lane_number_x_clamps_to_the_column_content_area).
                w = min(w, content_area_right - content_area_left)
                max_content_w = max(max_content_w, w)

        if max_content_w > 0:
            # CORE INVARIANT: number tspan center == cell midpoint.
            # tspan is right-anchored at number_x, so its left edge is at
            # (number_x - max_content_w). Setting the center to midpoint
            # gives number_x = cell_midpoint + max_content_w / 2.
            number_x = cell_midpoint + max_content_w / 2
            if is_date_col:
                suffix_gap = 0
            else:
                suffix_gap = 3 if col_suffix.strip() in MAGNITUDE_SUFFIXES else 0
            suffix_x = number_x + suffix_gap
            # Prefix sits 3px left of the widest number so the two never
            # visually kiss.  Matches suffix_gap above.  Rows narrower
            # than the widest get a larger gap naturally.  Restores the
            # 2px gap from the original three-lane rendering (PR #1186)
            # that #1244 removed on the premise that measured tabular
            # widths were accurate enough for zero-gap to read as
            # "touching, not overlapping" — in practice it reads as a
            # kiss, not a separator.
            prefix_gap = 3 if max_prefix_w > 0 else 0
            prefix_x = number_x - max_content_w - prefix_gap
            # Content extent: from prefix's left edge to suffix's right
            # edge — what per-column rules and header rules reference.
            content_left = prefix_x - max_prefix_w
            content_right = suffix_x + max_suffix_w
            # Defensive clamp: if prefix or suffix would sit outside the
            # cell's content area (asymmetric value — e.g. "$" prefix but
            # no suffix), shift the whole band toward the side that has
            # headroom. The shift amount is capped by the available
            # headroom so we never trade one overflow for another. The
            # fit cascade should have already shrunk font size to prevent
            # real overflow; this clamp handles the subtler asymmetric
            # padding case.
            overflow_left = content_area_left - content_left
            overflow_right = content_right - content_area_right
            if overflow_left > 0 and overflow_right < 0:
                # Content is too far left; shift right up to the right
                # headroom (-overflow_right).
                shift = min(overflow_left, -overflow_right)
                prefix_x += shift
                number_x += shift
                suffix_x += shift
                content_left += shift
                content_right += shift
            elif overflow_right > 0 and overflow_left < 0:
                # Content is too far right; shift left up to the left
                # headroom (-overflow_left).
                shift = min(overflow_right, -overflow_left)
                prefix_x -= shift
                number_x -= shift
                suffix_x -= shift
                content_left -= shift
                content_right -= shift
            positions[col] = (
                prefix_x,
                number_x,
                suffix_x,
                content_left,
                content_right,
            )
    return positions


def _compute_wrap_layout(
    rows: list[dict[str, Any]],
    text_columns: list[str],
    column_configs: Mapping[str, TableColumnConfig],
    col_widths: dict[str, float],
    cell_pad: int,
    font_size: int,
    row_height: int,
    text_baseline_offset: float,
    measurer: FontMeasurer,
    column_when_rules: Mapping[str, tuple[Any, ...]],
    formats: dict[str, str] | None = None,
) -> tuple[list[int], list[dict[str, list[str]]]]:
    """Pre-compute per-row heights and cached wrapped lines for text cells.

    Returns ``(heights, wrapped_lines_per_row)``. ``wrapped_lines_per_row[i]``
    maps a text column to its wrapped line list for row ``i``. Cells that
    need no wrapping (single-line fit, empty value, or the column has no
    measurable width) are absent from the map — render path falls back to
    the single-line path for those.

    Rows whose cells all fit on one line keep the configured ``row_height``
    verbatim; wrapped rows grow by
    ``max_lines * line_height - text_baseline_offset + 2*cell_pad`` (the
    offset subtraction keeps top/bottom margins symmetric).
    We never shrink below ``row_height`` (``max(row_height, grown)``) so a
    user who set a generous ``row.height`` keeps their spacing.
    ``text_columns`` must already exclude the row-number synthetic column.
    """
    line_height = font_size + text_baseline_offset
    heights: list[int] = []
    wrapped_by_row: list[dict[str, list[str]]] = []
    # Hoisted out of the row loop: this is the one measurement site that walks
    # every row rather than a 50-row sample, so the per-cell glyph resolution
    # is worth gating per column.
    glyph_columns = {
        col: _glyph_possible(column_configs, column_when_rules, col)
        for col in text_columns
    }
    for row in rows:
        max_lines = 1
        row_wraps: dict[str, list[str]] = {}
        for col in text_columns:
            col_config = column_configs.get(col)
            value = row.get(col, "")
            # Swatch columns render a fixed-size <rect>, not text — the cell's
            # value (a hex color) is paint, not content. Excluding them keeps
            # narrow swatch columns from inflating row height by "wrapping"
            # the hex string into one-character-per-line.
            if col_config and col_config.swatch:
                continue
            if _is_numeric_cell(value, col_config) or is_date_like(value):
                continue
            fmt = col_config.format if col_config else None
            display = format_table_cell_value(value, fmt, formats)
            if not display:
                continue
            content_w = col_widths.get(col, 100) - cell_pad * 2
            # Evaluated ahead of the glyph subtraction below, deliberately:
            # a cell that leaves this loop without a cache entry is painted
            # downstream as one unwrapped, untruncated line, so the glyph
            # must never be what pushes a cell through this guard. Past the
            # guard the search tolerates a non-positive width — it degrades
            # to one character per line, clipped but contained.
            if content_w <= 0:
                continue
            # The glyph shares the first line with the text it precedes, so
            # the wrap search runs against the width left over after it.
            if glyph_columns.get(col):
                content_w -= measurer.measure(
                    cell_glyph_run(column_configs, column_when_rules, col, value),
                    float(font_size),
                )
            # Fast path: if the cell already fits on one line, skip the
            # (much more expensive) word-boundary wrap search. Saves the
            # bulk of wrap_text_precise calls on wide columns / short text.
            if measurer.measure(display, float(font_size)) <= content_w:
                continue
            wrapped_lines, _ = wrap_text_precise(
                display, content_w, float(font_size), measurer
            )
            if wrapped_lines:
                row_wraps[col] = wrapped_lines
                max_lines = max(max_lines, len(wrapped_lines))
        wrapped_by_row.append(row_wraps)
        if max_lines <= 1:
            heights.append(row_height)
        else:
            # Subtract one text_baseline_offset so the bottom margin equals
            # the top margin (cell_pad on each side). Without this the last
            # baseline sits too close to the bottom edge and wrapped cells
            # read as top-weighted.
            grown = int(
                round(max_lines * line_height - text_baseline_offset + 2 * cell_pad)
            )
            heights.append(max(row_height, grown))
    return heights, wrapped_by_row


def _spark_column_layout(
    columns: list[str],
    column_configs: dict[str, ResolvedTableColumnConfig],
    rows: list[dict[str, Any]],  # type-state: explicit_any — query rows
    row_role_spec: str | None,
) -> tuple[dict[str, float | None], dict[str, bool]]:
    """Pre-compute per-table-column `bar` auto-max and signed (midline) layout.

    ``rows`` must be the FULL dataset, not a page slice — every row in a
    column must share one anchor (edge vs. midline) and one auto-max
    ceiling, or a column's bars change meaning between pages of the same
    paginated table (the row-number gutter width uses the same
    whole-dataset rule; see ``_row_number_column_width``).

    Summary/total rows are excluded from the scan, same rule and same
    reason as ``_render_data_rows``'s ``scale_rows``: a grand-total value is
    often 10-100x any detail value and would re-anchor or rescale every
    detail row's bar for a sign/magnitude no detail row actually has.

    Uses magnitude (abs), not the signed max, for `bar`'s auto-max ceiling —
    a column mixing -100 and 50 scales against 100, otherwise -100 would
    clamp against the smaller positive max and lose its true extent.
    Equivalent to the old `max(non_null)` for any all-positive column (abs
    is a no-op there).

    `bar-normalize` is excluded from signed layout entirely: its background
    track is a fixed-width "% of max" ruler (see `spark.py`'s
    `bar-normalize` docstring), and halving the fill against an unchanged
    track would silently rescale every reading (50% would read as 25%).
    It keeps the original clamp-to-zero behavior for negatives.
    """
    from dbt_charts.core.compile.models.chart.authored import SparkConfig

    detail_rows = [
        row for row in rows if not is_summary_role(resolve_row_role(row_role_spec, row))
    ]

    bar_auto_max: dict[str, float | None] = {}
    signed_layout_columns: dict[str, bool] = {}
    for col in columns:
        col_cfg = column_configs.get(col)
        if not (col_cfg and isinstance(col_cfg.spark, SparkConfig)):
            continue
        spark_type = col_cfg.spark.type
        if spark_type not in ("bar", "column"):
            continue
        non_null = [
            n
            for row in detail_rows
            if (n := coerce_numeric_cell(row.get(col))) is not None
        ]
        if spark_type == "bar" and col_cfg.spark.max is None:
            bar_auto_max[col] = max((abs(n) for n in non_null), default=None)
        signed_layout_columns[col] = any(n < 0 for n in non_null)
    return bar_auto_max, signed_layout_columns


def _render_data_rows(
    svg_parts: list[str],
    *,
    table_config: TableChartStyle,
    rows: list[dict[str, Any]],
    columns: list[str],
    column_configs: dict[str, ResolvedTableColumnConfig],
    column_when_rules: dict[str, tuple[Any, ...]],
    colors: dict[str, str],
    col_widths: dict[str, float],
    col_x_offsets: list[float],
    col_lane_positions: dict[str, tuple[float, float, float, float, float]],
    padding_x: int,
    current_y: float,
    row_height: int,
    cell_font: FontStyle,
    table_width: float,
    cell_pad: int | None = None,
    symbol_mode: str = "all",
    row_rule_width: float = 0.0,
    summary_rule_width: float = 0.0,
    rule_color: str | None = None,
    row_role_spec: str | None = None,
    summary_font_weight: str | None = None,
    role_summary: TableRowRoleStyle | None = None,
    role_total: TableRowRoleStyle | None = None,
    resolved_style: ResolvedChartDefaults | None = None,
    formats: dict[str, str] | None = None,
    row_numbers: TableRowNumbersStyle | None = None,
    page_offset: int = 0,
    row_heights: list[int] | None = None,
    wrapped_lines_by_row: list[dict[str, list[str]]] | None = None,
    wrap: bool,
    chart_root_link: str | None = None,
    chart_id: str = "",
    bar_auto_max: dict[str, float | None],
    signed_layout_columns: dict[str, bool],
) -> None:
    """Render all visible table rows.

    symbol_mode controls prefix/suffix rendering:
      "all"      — every row shows full formatted value (default)
      "anchors"  — first data row and summary/total rows show full value;
                   plain middle data rows strip currency prefix and
                   magnitude/unit suffix. The "anchor" rows structurally
                   guide the reader at the top and bottom of the value field.

    bar_auto_max / signed_layout_columns: per-table-column `bar`/`column`
    spark layout, computed by ``_spark_column_layout`` over the FULL
    dataset (not ``rows``, which may be a single page) — see that
    function's docstring for why a page-scoped scan is a bug.
    """
    from dbt_charts.core.compile.models.chart.authored import SparkConfig

    effective_font_family: str = cell_font.family  # type: ignore[assignment]
    assert effective_font_family is not None, (
        "_render_data_rows requires cell_font.family"
    )
    # size is derived from tc.font.size (cascade floor: _base.yaml) → font_size → _cell_font.size
    assert cell_font.size is not None, (
        "cell_font.size must be non-None (cascade floor: _base.yaml font.size)"
    )
    # The sole caller passes chart.resolved_style (a required field), so this is
    # never None in prod — narrow it once here so the reads below stay clean
    # rather than each carrying a dead ``if resolved_style else None`` fallback.
    assert resolved_style is not None, "_render_data_rows requires resolved_style"
    font_size = int(cell_font.size)
    if cell_pad is None:
        cell_pad = int(table_config.column_layout.cell_padding)
    text_offset = table_config.text_baseline_offset
    truncation_measurer = get_font_measurer(cell_font.family)

    # Anchor row for a shared-scale column's magnitude suffix: the first row
    # (in paint order) that can actually carry it. A zero or non-numeric
    # first row structurally cannot show a magnitude suffix (format_kpi_parts
    # never puts one on a zero value), so defaulting to row 0 unconditionally
    # would leave the column's magnitude declared nowhere. Falls back to 0
    # when every row is zero/non-numeric -- nothing to anchor either way.
    shared_scale_anchor_row: dict[str, int] = {}
    for col in columns:
        col_cfg = column_configs.get(col)
        if col_cfg is None or col_cfg.shared_scale is None:
            continue
        anchor_idx = next(
            (
                idx
                for idx, row in enumerate(rows)
                if (n := coerce_numeric_cell(row.get(col))) is not None and n != 0
            ),
            0,
        )
        shared_scale_anchor_row[col] = anchor_idx

    # Column names for link resolution (stable across rows)
    all_data_columns = list(rows[0].keys()) if rows else []

    # Row-scoped link band geometry (stable across rows). The chart-root link
    # renders as one clickable band per value row, NOT wired to every cell.
    # The band excludes the synthetic row-number gutter so the row link never
    # covers the index column. cell_pad=0 matches the *content* extent of the
    # row-stripe span; the stripe itself still spans the gutter, so with row
    # numbers visible the band's left edge sits one column inward of the stripe.
    row_link_x1 = row_link_x2 = 0.0
    row_link_label_col = ""  # first non-gutter column — names the row anchor
    if chart_root_link:
        row_link_columns = [c for c in columns if c != _ROW_NUMBER_COL]
        row_link_offsets = [
            col_x_offsets[idx] for idx, c in enumerate(columns) if c != _ROW_NUMBER_COL
        ]

        # Name the anchor after the first *textual* column: a swatch/spark
        # column would make AT announce a hex string or a numeric list. Fall
        # back to the first column if every non-gutter column is a swatch/spark.
        def _is_textual_label_col(col: str) -> bool:
            cfg = column_configs.get(col)
            return not (cfg and (cfg.swatch or cfg.spark is not None))

        row_link_label_col = next(
            (c for c in row_link_columns if _is_textual_label_col(c)),
            row_link_columns[0] if row_link_columns else "",
        )
        row_link_x1, row_link_x2 = _compute_content_span(
            columns=row_link_columns,
            col_widths=col_widths,
            col_x_offsets=row_link_offsets,
            padding_x=padding_x,
            cell_pad=0,
            table_width=table_width,
        )

    # Extra breathing room before summary/total rows so the double rule
    # doesn't crowd the last data row. Roughly half a row_height.
    _SUMMARY_GAP = int(row_height * 0.4)
    summary_gap_accum = 0
    # Running y-offset from variable per-row heights (used when row_heights provided)
    cumulative_row_height = 0.0

    # Row rules are deferred here and flushed AFTER the loop so they paint on
    # top of all fill rects. SVG document order = z-order: later = higher.
    row_rule_parts: list[str] = []

    # Scale domain is computed from detail rows only. Including summary/total rows
    # would skew min/max — a grand-total cell is often 10-100x any single detail
    # value, which crushes the detail-row gradient toward the low end of the palette.
    scale_rows = [
        r for r in rows if not is_summary_role(resolve_row_role(row_role_spec, r))
    ]

    for row_idx, row in enumerate(rows):
        # Resolve per-row semantic role (value/summary/total)
        role = resolve_row_role(row_role_spec, row)
        row_is_summary = is_summary_role(role)
        row_is_total = is_total_role(role)

        # Add breathing room before the first summary/total row.
        if row_is_summary and row_idx > 0:
            prev_role = resolve_row_role(row_role_spec, rows[row_idx - 1])
            if not is_summary_role(prev_role):
                summary_gap_accum += _SUMMARY_GAP

        per_row_height = row_heights[row_idx] if row_heights is not None else row_height
        # Snap row_y to integer: cumulative float additions (row_height, padding)
        # can drift sub-pixel and push 1px rules onto fractional rows, causing them to
        # rasterize across two rows at reduced opacity. Round at the emit boundary so
        # the sizing pipeline retains float precision while SVG coordinates stay crisp.
        if row_heights is not None:
            row_y = int(round(current_y + cumulative_row_height + summary_gap_accum))
        else:
            row_y = int(round(current_y + (row_idx * row_height) + summary_gap_accum))

        # Compute row-rule draw condition and reserve at the TOP of the loop —
        # before any fill rects are emitted. Every fill in this iteration uses
        # fill_height (= per_row_height - rule_reserve_px) so the rule band
        # at the bottom of the row stays clear and fills can't bleed past it.
        is_last_row = row_idx == len(rows) - 1
        next_is_summary = False
        if not is_last_row:
            next_role = resolve_row_role(row_role_spec, rows[row_idx + 1])
            next_is_summary = is_summary_role(next_role)
        will_draw_row_rule = (
            row_rule_width > 0
            and not is_last_row
            and not next_is_summary
            and not row_is_summary
        )
        # Promote sub-pixel rule widths to 1px: a 0.5px rule is invisible on
        # every display we ship to. Zero means "no rule" and is never rounded up.
        rule_reserve_px = (
            max(1, int(round(row_rule_width))) if will_draw_row_rule else 0
        )
        fill_height = per_row_height - rule_reserve_px

        # Stripe: suppress on summary rows (they stand out against clean bg).
        # Also skip when the stripe color is transparent — no point emitting
        # invisible rects, and some SVG renderers still treat "transparent"
        # as a paint operation that interacts with other elements.
        stripe_fill = colors["row_stripe"]
        stripes_enabled = stripe_fill and stripe_fill.lower() != "transparent"
        if row_idx % 2 == 1 and not row_is_summary and stripes_enabled:
            stripe_x1, stripe_x2 = _compute_content_span(
                columns=columns,
                col_widths=col_widths,
                col_x_offsets=col_x_offsets,
                padding_x=padding_x,
                cell_pad=0,
                table_width=table_width,
            )
            svg_parts.append(
                f'<rect x="{stripe_x1}" y="{row_y}" '
                f'width="{stripe_x2 - stripe_x1}" height="{fill_height}" '
                f'fill="{stripe_fill}"/>',
            )

        # Per-role background fill from row.roles.summary / row.roles.total.
        role_bg_raw = None
        if row_is_total and role_total and role_total.background:
            role_bg_raw = role_total.background
        elif row_is_summary and role_summary and role_summary.background:
            role_bg_raw = role_summary.background
        role_bg = sanitize_color(role_bg_raw, None) if role_bg_raw else None
        if role_bg:
            bg_x1, bg_x2 = _compute_content_span(
                columns=columns,
                col_widths=col_widths,
                col_x_offsets=col_x_offsets,
                padding_x=padding_x,
                cell_pad=0,
                table_width=table_width,
            )
            svg_parts.append(
                f'<rect x="{bg_x1}" y="{row_y}" '
                f'width="{bg_x2 - bg_x1}" height="{fill_height}" '
                f'fill="{role_bg}"/>',
            )

        # Summary rule ABOVE the summary row. Decoupled from row_rule_width:
        # summary_rule_width defaults to row_rule_width (backward compat) but
        # can be set independently so BI/Classic variants get summary rules
        # without body row rules.
        # Single rule for "summary"; double rule (two rects + 1px gap) for "total".
        if row_is_summary and summary_rule_width > 0:
            effective_rule_color = rule_color or colors["color"]
            rule_x1, rule_x2 = _compute_content_span(
                columns=columns,
                col_widths=col_widths,
                col_x_offsets=col_x_offsets,
                padding_x=padding_x,
                cell_pad=cell_pad,
                table_width=table_width,
            )
            line_h = max(1, int(summary_rule_width))
            if row_is_total:
                gap = 1
                y_upper = row_y - line_h - gap - line_h
                y_lower = row_y - line_h
                svg_parts.append(
                    f'<rect x="{rule_x1}" y="{y_upper}" '
                    f'width="{rule_x2 - rule_x1}" height="{line_h}" '
                    f'fill="{effective_rule_color}" '
                    f'shape-rendering="crispEdges"/>',
                )
                svg_parts.append(
                    f'<rect x="{rule_x1}" y="{y_lower}" '
                    f'width="{rule_x2 - rule_x1}" height="{line_h}" '
                    f'fill="{effective_rule_color}" '
                    f'shape-rendering="crispEdges"/>',
                )
            else:
                y_single = row_y - line_h
                svg_parts.append(
                    f'<rect x="{rule_x1}" y="{y_single}" '
                    f'width="{rule_x2 - rule_x1}" height="{line_h}" '
                    f'fill="{effective_rule_color}" '
                    f'shape-rendering="crispEdges"/>',
                )

        # Row rule BELOW each row — deferred into row_rule_parts so it paints
        # on top of all fill rects. Draw condition and reserve were computed at
        # the top of this iteration; will_draw_row_rule / rule_reserve_px drive
        # both the fill geometry and the rule geometry from the same values.
        if will_draw_row_rule:
            effective_rule_color = rule_color or colors["color"]
            # rule_y sits at the bottom of the fill band. row_y is integer
            # (pixel-snapped); per_row_height may be float (anti-dangle
            # squeeze), so rule_y may be fractional — SVG renders it fine.
            rule_y = row_y + per_row_height - rule_reserve_px
            rule_x1, rule_x2 = _compute_content_span(
                columns=columns,
                col_widths=col_widths,
                col_x_offsets=col_x_offsets,
                padding_x=padding_x,
                cell_pad=cell_pad,
                table_width=table_width,
            )
            # When the table has a row-link band, the decorative divider must
            # not catch the pointer or it re-opens a dead strip in the hover
            # band that paints beneath it (bands span the full per_row_height).
            # Only emitted for linked tables so non-linked table goldens are
            # byte-identical — the class is inert without a band anyway. A class
            # the host stylesheet reads, never a pointer-events attribute:
            # interaction does not ship inside a board.
            rule_pe_attr = ' class="dbt-pointer-inert"' if chart_root_link else ""
            row_rule_parts.append(
                f'<rect x="{rule_x1}" y="{rule_y}" width="{rule_x2 - rule_x1}" '
                f'height="{rule_reserve_px}" fill="{effective_rule_color}"'
                f'{rule_pe_attr} shape-rendering="crispEdges"/>',
            )

        cumulative_row_height += per_row_height

        # Row-scoped link band. One <a> per value row wrapping a transparent
        # hover rect behind the row — the whole-row affordance for a chart-root
        # link. Summary/total rows never get it. Emitted BEFORE the cells so
        # cell/filter anchors paint on top and win the click (SVG document
        # order = z-order). aria-label names the otherwise-empty anchor for AT
        # and keyboard focus. Non-link cell content in this row is made
        # pointer-transparent below so hover + click reach the band rather than
        # dying on the painted glyphs/backgrounds on top of it.
        row_has_band = False
        if chart_root_link and not row_is_summary and not row_is_total:
            row_link = resolve_cell_link_with_board(
                chart_root_link, row, all_data_columns
            )
            if row_link:
                row_has_band = True
                escaped_row_href = html_module.escape(row_link, quote=True)
                # Accessible name for the otherwise-empty row anchor: the first
                # non-gutter cell value, else the destination URL so the anchor
                # is never announced as a bare "link" by a screen reader.
                row_label = (
                    str(row[row_link_label_col]) if row_link_label_col in row else ""
                )
                if not row_label:
                    row_label = row_link
                escaped_row_label = html_module.escape(row_label, quote=True)
                # Band spans the full per-row height (not fill_height) so the
                # hover highlight is continuous down a column — fill_height
                # reserves the row-rule strip, which would leave a 1px dead gap
                # between adjacent bands where hover blinks off. The rule paints
                # on top after the loop but is pointer-transparent, so hover
                # still reaches the band across the boundary.
                svg_parts.append(
                    f'<a href="{escaped_row_href}" aria-label="{escaped_row_label}">'
                    f'<rect class="dbt-table-row-link" fill="transparent" '
                    f'style="--dbt-link: {colors["link"]}" '
                    f'x="{row_link_x1}" '
                    f'y="{row_y}" width="{row_link_x2 - row_link_x1}" '
                    f'height="{per_row_height}"/></a>',
                )

        # In a banded row, painted cell content that is NOT itself a link must
        # not swallow the band's pointer events — otherwise clicking/hovering a
        # value would hit the glyph on top and never reach the row link behind.
        # A class the host stylesheet reads (`.dbt-chart .dbt-pointer-inert`), on
        # the swatch <g>, spark <g>, and cell-background <rect>; cell <text> takes
        # `.dbt-table-cell-inert`, whose rule is qualified with the element so it
        # outranks `.dbt-chart text { pointer-events: auto }`. Never an attribute:
        # interaction does not ship inside a board. Cell/filter anchors are left
        # event-bearing so they still win the click.
        cell_pe_attr = ' class="dbt-pointer-inert"' if row_has_band else ""

        for i, col in enumerate(columns):
            cw = col_widths.get(col, 100)
            value = row.get(col, "")
            cell_x = padding_x + col_x_offsets[i]
            y = row_y + (per_row_height / 2) + text_offset

            # Synthetic row-number cell — absolute 1-based index across pages.
            # Summary/total rows render blank (the sequence is for data rows).
            if col == _ROW_NUMBER_COL and row_numbers is not None:
                if not (row_is_summary or row_is_total):
                    absolute_index = page_offset + row_idx + 1
                    if row_numbers.align == "right":
                        rn_x = cell_x + cw - cell_pad
                        rn_anchor = "end"
                    else:
                        rn_x = cell_x + cell_pad
                        rn_anchor = "start"
                    svg_parts.append(
                        f'<text x="{rn_x}" y="{y}" '
                        f'font-size="{font_size}" fill="{colors["muted"]}" '
                        f'text-anchor="{rn_anchor}" '
                        f'font-family="{_SANS_NUMERIC_FONT_STACK}">'
                        f"{absolute_index}</text>",
                    )
                continue

            col_config = column_configs.get(col)
            spark_config = col_config.spark if col_config else None

            if col_config and col_config.swatch:
                # _render_swatch_cell raises ChartDataError on a non-color
                # value — a swatch column with a bad cell is a misconfig,
                # not a rendering choice. The error names column + row index
                # so the author can find the cell that broke.
                swatch_content = _render_swatch_cell(
                    value, col=col, row_idx=page_offset + row_idx, chart_id=chart_id
                )
                swatch_x = px(cell_x + 4)
                swatch_y = row_y + (per_row_height - _SWATCH_SIZE) / 2
                svg_parts.append(
                    f'<g transform="translate({swatch_x}, {swatch_y})"'
                    f"{cell_pe_attr}>{swatch_content}</g>",
                )
                continue

            if isinstance(spark_config, SparkConfig) and value is not None:
                spark_content, spark_width, spark_height = _render_spark_cell(
                    value,
                    spark_config,
                    cw,
                    row_height,
                    cell_font=cell_font,
                    resolved_style=resolved_style,
                    column_max=bar_auto_max.get(col),
                    # signed_layout_columns only has entries for bar/column
                    # spark types (_spark_column_layout); every other spark
                    # type (line/area/columns/bar-normalize) correctly
                    # defaults to unsigned/edge-anchored layout.
                    has_negative=signed_layout_columns.get(
                        col, False
                    ),  # type-state: silent_fallback — see comment above
                )
                if spark_content:
                    # `column` is the only narrow mark — it needs explicit
                    # alignment to its header to honor the center-on-midpoint
                    # invariant (chart AGENTS.md): the header, number tspan, and
                    # column rule all anchor on the cell midpoint for numeric
                    # columns, and the spark must match. `col_config.align`
                    # is a text/digit-positioning knob and does not move the
                    # spark. Other spark marks fill the cell width and stay
                    # at the established left-edge anchor (cell_x + 4) — no
                    # behavior change for `bar`/`columns`/`line`/`area`.
                    if spark_config.type == "column":
                        if col in col_lane_positions:
                            spark_x = px(cell_x + (cw - spark_width) / 2)
                        else:
                            spark_x = px(cell_x + cell_pad)
                    else:
                        spark_x = px(cell_x + 4)
                    # Center the spark mark within the row's effective band.
                    # per_row_height >= nominal row_height; wrapped rows grow
                    # to fit their tallest text column, so a fixed top-of-row
                    # offset would leave the spark hugging the top.
                    spark_y = row_y + (per_row_height - spark_height) / 2
                    svg_parts.append(
                        f'<g transform="translate({spark_x}, {spark_y})"'
                        f"{cell_pe_attr}>{spark_content}</g>",
                    )
                    continue

            # Resolve base styles (may be field refs resolved per-row)
            cell_background = None
            if col_config and col_config.background:
                _bg_raw = resolve_table_style_value(col_config.background, row)
                cell_background = (
                    sanitize_color(_bg_raw, None)
                    if isinstance(_bg_raw, str) and is_sanitizable_color(_bg_raw)
                    else None
                )

            # Overlay scale + when conditional formatting
            has_scale = col_config and col_config.scale
            when_rules = column_when_rules.get(col)
            overrides: dict[str, Any] = (
                resolve_conditional_styles(
                    when_rules,
                    value,
                    tones=resolved_style.tones,
                )
                if when_rules
                else {}
            )
            cond_color: str | None = None
            cond_fw: str | float | None = None
            cond_style: str | None = None
            cond_decoration: str | None = None

            if has_scale:
                assert col_config is not None
                col_format = (
                    resolve_format(
                        col_config.format,
                        formats,
                    )
                    or None
                )
                cond_bg, cond_clr, cond_fw, cond_style, cond_decoration = (
                    resolve_cell_conditional_styles(
                        col_config,
                        value,
                        scale_rows,
                        when_rules=when_rules,
                        col_format=col_format,
                        row_role=role,
                        col_name=col,
                    )
                )
                if cond_bg is not None:
                    cell_background = (
                        sanitize_color(cond_bg, None)
                        if is_sanitizable_color(cond_bg)
                        else None
                    )
                if cond_clr is not None:
                    cond_color = cond_clr
            elif when_rules:
                if "background" in overrides:
                    _when_bg = overrides["background"]
                    cell_background = (
                        sanitize_color(_when_bg, None)
                        if isinstance(_when_bg, str) and is_sanitizable_color(_when_bg)
                        else None
                    )
                cond_color = overrides.get("color")
                cond_fw = overrides.get("weight")
                cond_style = overrides.get("style")
                cond_decoration = overrides.get("decoration")

            if cell_background:
                svg_parts.append(
                    f'<rect x="{cell_x}" y="{row_y}" width="{cw}" height="{fill_height}" '
                    f'fill="{cell_background}"{cell_pe_attr}/>',
                )

            cell_glyph, cell_glyph_color = resolve_cell_glyph_from_overrides(
                col_config, overrides
            )

            is_numeric = _is_numeric_cell(value, col_config)
            fmt = col_config.format if col_config else None

            # Resolve cell link.  Precedence (highest to lowest):
            #   1. Explicit per-column link: (column config)
            #   2. Per-column filter link ?col=encoded-value (declared board variable)
            # The chart-root link is NOT a per-cell fallthrough — it renders as a
            # single row-scoped band (see the row-link emission above), so a plain
            # dimension cell stays text and never inherits the row destination.
            link_raw = col_config.link if col_config else None
            cell_link: str | None = None
            if link_raw:
                cell_link = resolve_cell_link_with_board(
                    link_raw, row, all_data_columns
                )
            else:
                _filter_vars = get_filter_variables_context()
                if _filter_vars:
                    cell_link = resolve_filter_cell_link(col, value, _filter_vars)

            fill_color = colors["color"]
            if cond_color:
                fill_color = (
                    sanitize_color(cond_color, fill_color)
                    if is_sanitizable_color(cond_color)
                    else fill_color
                )
            elif col_config and col_config.font and col_config.font.color:
                _fc_raw = resolve_table_style_value(col_config.font.color, row)
                fill_color = (
                    sanitize_color(_fc_raw, fill_color)
                    if isinstance(_fc_raw, str) and is_sanitizable_color(_fc_raw)
                    else fill_color
                )
            elif cell_link:
                fill_color = colors["link"]

            font_weight_attr = ""
            if cond_fw and cond_fw in VALID_FONT_WEIGHTS:
                font_weight_attr = f' font-weight="{cond_fw}"'
            elif col_config and col_config.font and col_config.font.weight:
                resolved_weight = resolve_table_style_value(
                    font_weight_as_css(col_config.font.weight), row
                )
                if resolved_weight in VALID_FONT_WEIGHTS:
                    font_weight_attr = f' font-weight="{resolved_weight}"'
            # Summary/total rows: per-role font.weight from row.roles
            # takes precedence, then flat summary_font_weight, then
            # default medium (500). All values validated against
            # VALID_FONT_WEIGHTS before interpolation into SVG.
            if not font_weight_attr and row_is_summary:
                _rw = None
                _role_total_weight = (
                    role_total.font.weight if role_total and role_total.font else None
                )
                _role_summary_weight = (
                    role_summary.font.weight
                    if role_summary and role_summary.font
                    else None
                )
                if row_is_total and _role_total_weight:
                    _candidate = font_weight_as_css(_role_total_weight)
                    if _candidate in VALID_FONT_WEIGHTS:
                        _rw = _candidate
                if not _rw and _role_summary_weight:
                    _candidate = font_weight_as_css(_role_summary_weight)
                    if _candidate in VALID_FONT_WEIGHTS:
                        _rw = _candidate
                if not _rw:
                    _rw = summary_font_weight or "500"
                font_weight_attr = f' font-weight="{_rw}"'
            if not font_weight_attr and cell_link:
                font_weight_attr = ' font-weight="500"'

            font_style_attr = ""
            if cond_style is not None:
                font_style_attr = f' font-style="{cond_style}"'

            font_decoration_attr = ""
            if cond_decoration is not None:
                font_decoration_attr = f' text-decoration="{cond_decoration}"'

            use_tabular = _wants_tabular_font(value, col_config)
            if use_tabular:
                # Source Serif tables use the serif font for numeric cells
                # (CSS tabular-nums handles digit alignment); all other fonts
                # use the dbt Sans Tabular stack for tabular digit widths.
                if effective_font_family and "Source Serif" in effective_font_family:
                    cell_font_family = effective_font_family
                else:
                    cell_font_family = _SANS_NUMERIC_FONT_STACK
            else:
                cell_font_family = effective_font_family or ""
            # tabular-nums + lining-nums ensures consistent column alignment.
            # font-feature-settings is the OpenType belt-and-suspenders for
            # renderers that don't support font-variant-numeric.
            numeric_style = (
                ' style="font-variant-numeric: tabular-nums lining-nums;'
                " font-feature-settings: 'tnum' 1, 'lnum' 1;\""
                if use_tabular
                else ""
            )

            if cell_link:
                escaped_href = html_module.escape(cell_link, quote=True)
                escaped_link_color = html_module.escape(fill_color, quote=True)
                svg_parts.append(
                    f'<a href="{escaped_href}"><g class="dbt-table-link" '
                    f'style="color: {escaped_link_color}">',
                )
            # Class on the cell <text>: the link-text class for wired cells;
            # else, in a banded row, the inert-cell class whose stylesheet rule
            # (emitted below, qualified as ``.dbt-chart text.dbt-table-cell-inert``
            # so it outranks the board's ``.dbt-chart text`` on specificity)
            # drops pointer events. A presentation attribute would lose to that
            # board rule, so the click + hover would never reach the row band.
            if cell_link:
                cell_link_class_attr = ' class="dbt-table-link-text"'
            elif row_has_band:
                cell_link_class_attr = ' class="dbt-table-cell-inert"'
            else:
                cell_link_class_attr = ""

            # Three-lane rendering for numeric cells (not date-like strings
            # which happen to parse as numbers, e.g. "2024")
            if is_numeric and col in col_lane_positions and not is_date_like(value):
                # Coerce string values from CSV adapter
                try:
                    num_value = float(value) if isinstance(value, str) else value
                except (ValueError, TypeError):
                    num_value = value

                shared_scale = col_config.shared_scale if col_config else None
                # A column's anchor row/rows: the first data row and any
                # summary/total row, EXCEPT the shared-scale anchor position
                # is the first row that can actually carry the magnitude
                # suffix (shared_scale_anchor_row, precomputed above) rather
                # than always row 0 -- a zero or non-numeric first row can
                # never show a magnitude suffix (format_kpi_parts never puts
                # one on a zero value), so anchoring there would leave the
                # column's magnitude declared nowhere. Under `symbol_mode:
                # all` every row counts as anchor for this purpose: "all"
                # means "show the full formatted value on every row"
                # (unchanged by this task), so an ANCHOR-mode shared_scale
                # must not suppress the magnitude suffix there the way it
                # does under "anchors". Direct subscript, not `.get(col, 0)`:
                # shared_scale_anchor_row always has an entry for a column
                # with a resolved shared_scale (precomputed for every such
                # column above), so a fallback default is never load-bearing
                # -- and a silent `0` would be wrong for a column whose real
                # anchor sits at any other row.
                magnitude_anchor_row = (
                    shared_scale_anchor_row[col] if shared_scale is not None else None
                )
                is_anchor_row = (
                    symbol_mode != "anchors"
                    or row_is_summary
                    or (
                        magnitude_anchor_row is not None
                        and row_idx == magnitude_anchor_row
                    )
                )

                if isinstance(num_value, (int, float)) and not isinstance(
                    num_value,
                    bool,
                ):
                    # No guard needed here: a date/timestamp value fails this
                    # isinstance check and never reaches this branch, and any
                    # genuinely numeric value under a mismatched format was
                    # already raised on by _compute_lane_positions's identical
                    # gate, computed over the full dataset before any row here
                    # paints.
                    prefix, number_str, suffix = format_kpi_parts(
                        num_value,
                        fmt,
                        formats,
                        default_number=True,
                        shared_scale=shared_scale,
                        is_anchor=is_anchor_row,
                    )
                    if col_config is not None and col_config.decimal_pad_table:
                        number_str += decimal_pad_for(
                            col_config.decimal_pad_table, number_str
                        )
                else:
                    prefix, number_str, suffix = (
                        "",
                        format_table_cell_value(
                            value,
                            fmt,
                            formats,
                        ),
                        "",
                    )

                # Anchors: only the first row + summary rows show the prefix
                # (currency) and unit suffix. A magnitude suffix (K/M/B) on a
                # column with no resolved shared_scale carries per-row value
                # independently, so it stays on every row for those columns —
                # stripping it would render 3000 as a bare "3". A column WITH
                # a resolved ANCHOR-mode shared_scale already gated its
                # magnitude suffix on is_anchor_row above (mirrors an axis
                # ruler's own ANCHOR/REPEAT contract), so it arrives here
                # already blank on a non-anchor row and this same check
                # strips it like any other non-magnitude suffix — no special
                # case needed. The combined suffix leads with the magnitude
                # token (magnitude + unit), so a prefix match keeps it even
                # when an explicit unit follows.
                if symbol_mode == "anchors" and row_idx != 0 and not row_is_summary:
                    prefix = ""
                    if not any(suffix.startswith(m) for m in MAGNITUDE_SUFFIXES):
                        suffix = ""

                # When a glyph is active for this cell, it replaces the
                # format prefix (currency etc.) so the prefix lane carries
                # one colored indicator instead of two stacked symbols.
                full_prefix = cell_glyph or prefix

                # Use pre-computed fixed lane positions for this column
                prefix_x, number_x, suffix_x, *_ = col_lane_positions[col]
                escaped_number = html_module.escape(number_str)

                svg_parts.append(
                    f'<text{cell_link_class_attr} y="{y}" font-size="{font_size}" '
                    f'fill="{fill_color}" '
                    f'font-family="{cell_font_family}"{numeric_style}{font_weight_attr}{font_style_attr}{font_decoration_attr}>',
                )

                # Prefix tspan: end-anchored at prefix_x (left of number).
                # A glyph carries its own fill so it can stand out against
                # the cell's default text color.
                if full_prefix:
                    escaped_prefix = html_module.escape(full_prefix)
                    glyph_fill_attr = (
                        f' fill="{sanitize_color(cell_glyph_color, fill_color)}"'
                        if cell_glyph and cell_glyph_color
                        else ""
                    )
                    svg_parts.append(
                        f'<tspan x="{prefix_x}" text-anchor="end"{glyph_fill_attr}>'
                        f"{escaped_prefix}</tspan>",
                    )

                # Number tspan: end-anchored so its center sits at cell
                # midpoint (number_x = midpoint + max_number_w / 2).
                svg_parts.append(
                    f'<tspan x="{number_x}" text-anchor="end">{escaped_number}</tspan>',
                )

                # Suffix tspan: left-aligned at fixed position
                if suffix:
                    escaped_suffix = html_module.escape(suffix)
                    svg_parts.append(
                        f'<tspan x="{suffix_x}" text-anchor="start">'
                        f"{escaped_suffix}</tspan>",
                    )

                svg_parts.append("</text>")
            else:
                display_value = format_table_cell_value(value, fmt, formats)
                # The glyph is painted inline ahead of the value, so it eats
                # into the room the value has before it must be truncated.
                content_area = (
                    cw
                    - cell_pad * 2
                    - truncation_measurer.measure(
                        glyph_run(cell_glyph), float(font_size)
                    )
                )

                # align is a column-level verdict, finalized at resolve —
                # see fill_table_column_defaults / render/chart/AGENTS.md
                # invariant 11.
                align = col_config.align if col_config and col_config.align else None
                if align == "center":
                    x = cell_x + (cw / 2)
                    anchor = "middle"
                elif (
                    align == "right" or is_date_like(value)
                ) and col in col_lane_positions:
                    # col_lane_positions holds a lane only for the numeric
                    # three-lane invariant or a resolved align == "right"
                    # column that actually has date-like content (see
                    # _compute_lane_positions) — never for align == "right"
                    # alone, so is_date_like(value) here only ever routes an
                    # already-eligible column's date-shaped outlier (e.g. a
                    # bare year among real numbers) onto its neighbors' lane.
                    _prefix_x, number_x, *_ = col_lane_positions[col]
                    x = number_x
                    anchor = "end"
                elif align == "right":
                    x = cell_x + cw - cell_pad
                    anchor = "end"
                else:  # "left" or unset
                    x = cell_x + cell_pad
                    anchor = "start"

                if wrap and not is_date_like(value) and not is_numeric:
                    # _compute_wrap_layout ran upstream; missing-cache means
                    # the cell was skipped (empty display or content_w <= 0).
                    # Render those as a single line — no need to re-measure.
                    # Numeric cells that fall through here (no lane position)
                    # take the else-branch so they keep tabular-nums CSS and
                    # a hard ellipsis rather than word-wrap.
                    cached = (
                        wrapped_lines_by_row[row_idx].get(col)
                        if wrapped_lines_by_row is not None
                        else None
                    )
                    lines = cached if cached is not None else [display_value]
                else:
                    pre_trunc = display_value
                    display_value = truncate_text_precise(
                        display_value,
                        content_area,
                        font_size,
                        truncation_measurer,
                        ellipsis=True,
                    )
                    if display_value != pre_trunc and chart_id:
                        record_text_truncation(chart_id, "table_cell", pre_trunc, col)
                    lines = [display_value]

                # Glyph for non-three-lane cells (text columns, dates, nulls).
                # Inlined as a leading colored tspan; multi-line cells carry
                # the glyph on the first line only.
                glyph_prefix_inline = ""
                if cell_glyph:
                    escaped_glyph = html_module.escape(cell_glyph)
                    glyph_fill_attr = (
                        f' fill="{sanitize_color(cell_glyph_color, fill_color)}"'
                        if cell_glyph_color
                        else ""
                    )
                    glyph_prefix_inline = (
                        f"<tspan{glyph_fill_attr}>{escaped_glyph} </tspan>"
                    )

                if len(lines) > 1:
                    line_height = font_size + text_offset
                    total_text_h = len(lines) * line_height
                    first_y = row_y + (per_row_height - total_text_h) / 2 + font_size
                    svg_parts.append(
                        f'<text{cell_link_class_attr} x="{x}" '
                        f'font-size="{font_size}" fill="{fill_color}" '
                        f'text-anchor="{anchor}" '
                        f'font-family="{cell_font_family}"{font_weight_attr}{font_style_attr}{font_decoration_attr}>'
                    )
                    for li, line in enumerate(lines):
                        ly = first_y + li * line_height
                        prefix = glyph_prefix_inline if li == 0 else ""
                        svg_parts.append(
                            f'<tspan x="{x}" y="{ly}">{prefix}{html_module.escape(line)}</tspan>'
                        )
                    svg_parts.append("</text>")
                else:
                    svg_parts.append(
                        f'<text{cell_link_class_attr} x="{x}" y="{y}" '
                        f'font-size="{font_size}" fill="{fill_color}" '
                        f'text-anchor="{anchor}" '
                        f'font-family="{cell_font_family}"{numeric_style}{font_weight_attr}{font_style_attr}{font_decoration_attr}>'
                        f"{glyph_prefix_inline}{html_module.escape(lines[0])}</text>",
                    )

            if cell_link:
                svg_parts.append("</g></a>")

    # Flush deferred row rules last so they paint above all fill rects.
    # SVG z-order is document order: later = higher.
    svg_parts.extend(row_rule_parts)


def _extract_page_from_variables(
    chart_id: str,
    variables: dict[str, Any] | None,
) -> int:
    """Extract the current page number from the variables dict.

    The page variable is named ``{chart_id}_page`` and is 1-based.
    Returns 1 if no page variable is present or the value is invalid.
    """
    if not variables:
        return 1
    raw = variables.get(f"{chart_id}_page")
    if raw is None:
        return 1
    try:
        return max(1, int(raw))
    except (ValueError, TypeError):
        return 1


_PAGINATOR_PREV_CHEVRON = "\u2039"  # \u2039
_PAGINATOR_NEXT_CHEVRON = "\u203a"  # \u203a
_PAGINATOR_ELLIPSIS = "\u2026"  # \u2026
# siblingCount=1 shows ±1 around the current page (e.g. `4 5 6` mid-window)
# so the paginator carries some context, not just the single current digit.
# The aux-slot squeeze keeps total width modest; the MUI small-gap
# expansion fills in single hidden pages instead of ellipsizing them.
_PAGINATOR_SIBLING_COUNT = 1
_PAGINATOR_BOUNDARY_COUNT = 1

# Chevrons and ellipses sit in narrower slots than digits so they read as
# pairs with their adjacent page numbers rather than floating a full slot
# away. 0.66 keeps the pairing tight without overlapping glyph bounds.
_PAGINATOR_AUX_SLOT_RATIO = 0.66


def _paginator_window(
    page: int,
    total: int,
    sibling: int = _PAGINATOR_SIBLING_COUNT,
    boundary: int = _PAGINATOR_BOUNDARY_COUNT,
) -> list[int | str]:
    """Build the windowed page sequence: numeric pages with ``"\u2026"`` for gaps.

    Always-show set:
      * ``[1 .. boundary]`` and ``[total-boundary+1 .. total]`` (the
        boundary pages)
      * ``[page-sibling .. page+sibling]`` (siblings around current)

    Gaps between consecutive must-show pages become a single ``"\u2026"``
    sentinel, regardless of gap size. We deliberately do NOT expand
    single-page gaps into the hidden page \u2014 that produces "5-in-a-row"
    runs near the edges (e.g. page 1 of 8 \u2192 ``1 2 3 4 5 \u2026 8``) which
    overweight the start/end states. Siblings carry the local context;
    ellipsis carries the "more here" signal.
    """
    if total <= 0:
        return []
    if total == 1:
        return [1]

    must_show: set[int] = set()
    must_show.update(range(1, min(boundary, total) + 1))
    must_show.update(range(max(total - boundary + 1, 1), total + 1))
    must_show.update(range(max(page - sibling, 1), min(page + sibling, total) + 1))

    sorted_pages = sorted(must_show)
    items: list[int | str] = []
    prev = 0
    for p in sorted_pages:
        if p > prev + 1:
            items.append(_PAGINATOR_ELLIPSIS)
        items.append(p)
        prev = p
    return items


def _render_pagination_controls(
    page: int,
    total_pages: int,
    page_var_name: str,
    table_width: float,
    y: float,
    font_family: str,
    paginator: PaginatorStyle,
    row_start: int,
    row_end: int,
    total_rows: int,
    padding: float,
) -> str:
    """Render a right-aligned paginator: ``\u2039 1 \u2026 4 5 6 \u2026 12 \u203a``.

    Layout: each item occupies a fixed ``item_width`` slot; the rightmost
    slot's right edge sits at ``table_width``. Clickable items (live
    chevrons and inactive page numbers) get an invisible ``<rect>`` with an
    ``data-dbt-page-var`` the runtime commits \u2014 this is the hit target.
    Disabled chevrons, the active page, and the ellipsis are non-interactive.
    Disabled state is signaled by color (``color_disabled``), not opacity.

    A muted ``"Rows {row_start}\u2013{row_end} of {total_rows}"`` label sits
    left-aligned at ``padding`` in the same control band \u2014 an unlabeled
    ``\u2039 1 2 \u2026 37 \u203a`` reads as "37 pages of dashboards", not "this
    table has 37 pages of rows". ``row_start``/``row_end`` are this page's real
    1-based row range (the caller's own page offset and painted row count, so
    a short last page reports its true end, never ``page * page_rows``);
    ``total_rows`` is ``len(data)``, never a page count. Dropped entirely
    (never shrunk, and never shrinks the pager) if it would collide with the
    right-anchored sequence.
    """
    window = _paginator_window(page=page, total=total_pages)

    # Build the full visual sequence: leading chevron, page items, trailing
    # chevron. Each entry is (role, glyph, target_page).
    sequence: list[tuple[str, str, int]] = []
    prev_target = page - 1 if page > 1 else 0
    next_target = page + 1 if page < total_pages else 0
    sequence.append(("prev", _PAGINATOR_PREV_CHEVRON, prev_target))
    for item in window:
        if isinstance(item, str):
            sequence.append(("ellipsis", item, 0))
        else:
            sequence.append(("page", str(item), int(item)))
    sequence.append(("next", _PAGINATOR_NEXT_CHEVRON, next_target))

    font_size = int(paginator.font.size) if paginator.font.size is not None else 11
    item_width = paginator.item_width
    text_y = y + 18  # baseline within the reserved control band
    # Hit rect hugs the glyph tightly so cursor:pointer matches the visible
    # character. We don't want adjacent rects to butt up against each other —
    # that makes the gap between items feel clickable when it isn't.
    rect_w = float(font_size) + 6.0
    rect_h = max(font_size * 1.8, 18.0)
    rect_y = y + 4

    safe_var = html_module.escape(page_var_name, quote=True)
    safe_font = html_module.escape(font_family, quote=True)
    safe_active = html_module.escape(paginator.color_active, quote=True)
    safe_inactive = html_module.escape(paginator.color_inactive, quote=True)
    safe_disabled = html_module.escape(paginator.color_disabled, quote=True)

    # Right-anchored layout: walk slot widths so chevrons and ellipses
    # get narrower slots than digits, sitting closer to their boundary
    # neighbors. Total width is the sum of slot widths; the rightmost
    # slot's right edge sits at ``table_width``.
    aux_slot_w = item_width * _PAGINATOR_AUX_SLOT_RATIO
    slot_widths = [
        aux_slot_w if role in ("prev", "next", "ellipsis") else item_width
        for role, _, _ in sequence
    ]
    total_width = sum(slot_widths)
    cursor_left = table_width - total_width

    # Built outside the <g class="dbt-paginator"> group (returned separately
    # below) so it survives strip_pagination_chrome: a raster/PDF export
    # strips the clickable-looking chrome but keeps content, and this label
    # is content -- the one line that answers "how much am I not seeing?" on
    # a surface where the reader can't click a page number to find out.
    label_svg = ""
    label_text = f"Rows {row_start}–{row_end} of {total_rows}"
    label_w = get_font_measurer(font_family).measure(label_text, float(font_size))
    if padding + label_w <= cursor_left:
        safe_label = html_module.escape(label_text, quote=True)
        label_svg = (
            f'<text class="dbt-paginator-label" x="{padding:.1f}" '
            f'y="{text_y:.1f}" font-size="{font_size}" '
            f'fill="{safe_inactive}" font-family="{safe_font}" '
            f'font-weight="{paginator.weight_inactive}" '
            f'style="font-variant-numeric: tabular-nums;">'
            f"{safe_label}</text>\n"
        )

    parts: list[str] = [f'<g class="dbt-paginator" data-paginator="{safe_var}">']

    for i, (role, glyph, target) in enumerate(sequence):
        slot_w = slot_widths[i]
        slot_left = cursor_left
        center_x = slot_left + slot_w / 2
        cursor_left += slot_w

        is_active_page = role == "page" and target == page
        is_disabled_chevron = role in ("prev", "next") and target == 0
        is_ellipsis = role == "ellipsis"

        if is_active_page:
            color = safe_active
            weight = paginator.weight_active
            clickable = False
        elif is_disabled_chevron:
            color = safe_disabled
            weight = (
                paginator.weight_chevron
            )  # silhouette stays heavy; tone signals disabled
            clickable = False
        elif role in ("prev", "next"):
            color = safe_active
            weight = paginator.weight_chevron
            clickable = True
        elif is_ellipsis:
            color = safe_inactive
            weight = paginator.weight_inactive
            clickable = False
        else:  # inactive page number
            color = safe_inactive
            weight = paginator.weight_inactive
            clickable = True

        if clickable:
            # Rect is centered on the glyph and narrower than the slot so
            # cursor:pointer doesn't extend into the gap between items.
            rect_x = center_x - rect_w / 2
            # Interactive hosts (dct serve, Cloud) ship variables.js and can
            # act on data-dbt-page-var — see render/controls.py. A
            # static export ships neither, so its hit target instead carries
            # a plain data attribute the embedded table_pagination.js script
            # reads to toggle which pre-rendered page is visible.
            # Code never ships inside a board. On a live host the button names
            # the variable it drives and the runtime commits it (variables.js
            # binds data-dbt-page-var); a static export names only the target,
            # which its standalone runtime uses to toggle pre-drawn pages.
            page_var = (
                f' data-dbt-page-var="{safe_var}"' if controls_are_interactive() else ""
            )
            parts.append(
                f'<rect class="dbt-page-target" x="{rect_x:.1f}" y="{rect_y:.1f}" '
                f'width="{rect_w:.1f}" height="{rect_h:.1f}" '
                f'fill="transparent"{page_var} data-dbt-page-target="{target}"/>'
            )

        text_style = "font-variant-numeric: tabular-nums;"
        data_attrs = f' data-paginator-role="{role}"'
        if is_active_page:
            data_attrs += f' data-pagination-current="{safe_var}"'
        # class="dbt-paginator-glyph" so the underlying <rect> catches hover/
        # click over the painted glyph. A pointer-events="none" *attribute*
        # cannot do this: the board stylesheet ships
        # ``.dbt-chart text { pointer-events: auto }`` and a CSS declaration
        # always beats a presentation attribute, so the glyph would take the
        # pointer back — the same trap cell text solves via
        # .dbt-table-cell-inert (see the reasoning above, and the matching
        # rule emitted below).
        parts.append(
            f'<text x="{center_x:.1f}" y="{text_y:.1f}" '
            f'font-size="{font_size}" fill="{color}" text-anchor="middle" '
            f'font-family="{safe_font}" font-weight="{weight}" '
            f'class="dbt-paginator-glyph" '
            f'style="{text_style}"{data_attrs}>{glyph}</text>'
        )

    parts.append("</g>")
    return label_svg + "\n".join(parts)


def _render_static_pagination_cap_note(
    rendered_pages: int,
    total_pages: int,
    padding: float,
    y: float,
    font_family: str,
    paginator: PaginatorStyle,
) -> str:
    """Left-aligned note for a static export that hit the pre-render cap.

    A standalone artifact has no other channel to reach its reader — sitting
    it in the paginator's own control row states plainly that the export
    stops short of every row, instead of leaving the reader to assume the
    file holds everything.
    """
    font_size = int(paginator.font.size) if paginator.font.size is not None else 11
    safe_font = html_module.escape(font_family, quote=True)
    safe_color = html_module.escape(paginator.color_inactive, quote=True)
    text = html_module.escape(
        f"Showing pages 1–{rendered_pages} of {total_pages} in this static export"
    )
    return (
        f'<text class="dbt-paginator-label" x="{padding:.1f}" y="{y + 18:.1f}" '
        f'font-size="{font_size}" fill="{safe_color}" font-family="{safe_font}" '
        f'font-weight="{paginator.weight_inactive}">{text}</text>'
    )


@cache
def _table_pagination_script() -> str:
    """The static-export pagination toggle, embedded inline like the tooltip runtime.

    Every page's rows and paginator are pre-rendered into the table's own SVG
    (the ``static_multi_page`` branch of ``_render_table_svg_core``); this
    script only flips which ``data-dbt-table-page`` group is visible on
    click — no server, no re-render. Cached: the packaged asset can't change
    under a running process.
    """
    source = (
        files("dbt_charts.core.render")
        / "templates"
        / "scripts"
        / "table_pagination.js"
    ).read_text(encoding="utf-8")
    return embed_svg_script(source)


_PAGINATOR_GROUP_RE = re.compile(r'<g class="dbt-paginator".*?</g>', re.DOTALL)


def strip_pagination_chrome(svg: str) -> str:
    """Remove pagination controls from a rendered board SVG.

    For a surface that can never run ``table_pagination.js`` — a raster
    (PNG/PDF) or an SVG referenced via ``<img src>`` — the static-export
    paginator would draw as pixels that look clickable but do nothing.
    Rather than shipping that, the host strips the control before
    rasterizing; the visible page's rows are untouched, so a table simply
    shows its first page with no chrome — honest, not broken-looking.
    """
    return _PAGINATOR_GROUP_RE.sub("", svg)


def _as_resolved_table_column(
    config: TableColumnConfig,
    formats: dict[str, str] | None,
    font_family: str,
) -> ResolvedTableColumnConfig:
    """Upgrade a plain ``TableColumnConfig`` built at render time to the
    resolved subtype.

    Render synthesizes a handful of columns whose key space only exists
    after a render-time transform (transpose's ``__metric__``/``__value__``,
    a pivot's leaf columns) -- resolve's own ``_with_resolved_scale_stops``
    never saw them, so it never produced ``ResolvedTableColumnConfig``
    instances for them. They never author a ``scale``, so there is nothing
    to bake for the color mapping. ``decimal_pad_table`` is computed here so
    pivot columns with a trim-enabled format still align correctly.

    ``font_family`` must be the numeric cell font (dbt Sans Tabular or
    Source Serif), not the body font. Pivot/transpose leaf columns are
    synthesized at render time from data that is not visible at resolve time,
    so the mixed-depth gate cannot run here -- ``decimal_pad_table_for`` bakes
    from the spec alone, producing a non-empty table for any trim-enabled
    fixed-point format (including columns where all rows share the same depth).
    """
    resolved_fmt = resolve_format(config.format, formats)
    pad_table = decimal_pad_table_for(resolved_fmt, font_family)
    return ResolvedTableColumnConfig.model_validate(
        {**config.model_dump(exclude_none=True), "decimal_pad_table": pad_table}
    )


def _transpose_data_for_render(
    formats: dict[str, str] | None,
    columns: dict[str, ResolvedTableColumnConfig] | None,
    column_defaults: TableColumnDefaultsConfig | None,
    data: list[dict[str, Any]],
    font_family: str,
) -> tuple[list[dict[str, Any]], dict[str, ResolvedTableColumnConfig]]:
    """Pivot a single wide row into N (label, value) rows for the normal renderer.

    Returns (pivoted_data, pivoted_columns) where:
    - pivoted_data is a list of {"__metric__": label, "__value__": formatted_value}
    - pivoted_columns maps "__metric__" (40% width) and "__value__" (60%, right-align)
      with "Metric" / "Value" header labels.

    ``columns`` (the resolved, source-column-keyed mapping — column_defaults
    already baked in there for the *source* columns) supplies each row's
    label/format only. The two output columns below are a render-native key
    space — they don't exist until this transform runs — so ``column_defaults``
    is applied to them here via ``fill_table_column_defaults``, filling only
    whichever fields "Metric"/"Value"/40%-width/right-align don't already
    set — "__value__" has no width of its own, so ``column_defaults.width``
    does pin it if authored.

    Raises ChartDataError when data does not have exactly one row.
    """
    if len(data) != 1:
        raise ChartDataError(
            f"style.table.transpose requires exactly one data row; "
            f"got {len(data)}. Remove transpose: true or change the query."
        )

    # columns carries the promoted per-column configs (label, format, etc.)
    # set during resolve — the caller passes it directly, no chart reach-back needed.
    column_configs: dict[str, ResolvedTableColumnConfig] = dict(columns or {})
    row = data[0]
    # Same unreachable-authoring guard as the core render path: a `visible:`
    # entry naming no query column would silently do nothing here too.
    _unaddressed = sorted(
        key
        for key, cfg in column_configs.items()
        if cfg.visible is False and key not in row
    )
    if _unaddressed:
        raise ChartDataError(
            f"style.table.transpose: style.columns sets `visible:` on"
            f" {_unaddressed}, which match no query column — the transposable"
            f" columns are {sorted(row)}. Check for a typo."
        )
    src_cols = [
        c
        for c in list(column_configs) + [k for k in row if k not in column_configs]
        if (_cfg := column_configs.get(c)) is None or _cfg.visible is not False
    ]

    pivoted: list[dict[str, Any]] = []
    for col in src_cols:
        col_cfg = column_configs.get(col)
        label = (
            col_cfg.label
            if (col_cfg and col_cfg.label is not None)
            else slug_to_text(col)
        )
        fmt = col_cfg.format if col_cfg else None
        formatted = format_table_cell_value(row.get(col), fmt, formats=formats)
        pivoted.append({"__metric__": label, "__value__": formatted})

    pivoted_columns: dict[str, ResolvedTableColumnConfig] = {
        "__metric__": _as_resolved_table_column(
            fill_table_column_defaults(
                TableColumnConfig(label="Metric", width="40%"), column_defaults
            ),
            formats,
            font_family,
        ),
        "__value__": _as_resolved_table_column(
            fill_table_column_defaults(
                TableColumnConfig(label="Value", align="right"), column_defaults
            ),
            formats,
            font_family,
        ),
    }
    return pivoted, pivoted_columns


def _fanned_leaf_config(
    base: ResolvedTableColumnConfig,
    *,
    fallback_label: str,
    measure_identity: bool,
    defaults: TableColumnDefaultsConfig | None,
    values: list[Any],  # type-state: explicit_any — raw leaf cell values
) -> ResolvedTableColumnConfig:
    """Construct the leaf-key variant of a measure-keyed config.

    Pivot leaf columns are a render-native key space, so this goes through
    the same constructor pair as every other render-synthesized column
    (``fill_table_column_defaults`` + ``_as_resolved_table_column``) — the
    leaf value is built once with every field final, never by
    copying-with-update a Resolved* value. The measure-keyed entry supplies
    the styling source; ``fallback_label`` names the leaf.
    ``measure_identity`` says whose identity the leaf header carries: True
    when an entry exists in the resolved mapping under this leaf's display
    name (a measure sub-label, or a leaf-part key — note this is a name
    match against the resolved mapping, so a pivoted value string-equal to
    another column's name adopts that entry), and False for entries reached
    via the single-measure fallback, whose headers ARE pivoted values — the
    measure's label and header link must not stamp themselves across every
    column. The sizing slot (``width``/``max_width``) never fans: a
    per-column width multiplied across N leaves would inflate the table (it
    was inert on the old semantics).
    Resolved-only fields the fill pair cannot see (``shared_scale``,
    ``decimal_pad_table``, the baked ``scale``) are carried from ``base``
    into the same construction, so the carried facts — shared-magnitude
    anchoring, decimal padding, baked scale stops — match the flat-table
    control (a data-domain gradient still computes its color domain per
    rendered column at paint time).
    """
    fields = {name: getattr(base, name) for name in TableColumnConfig.model_fields}
    fields["width"] = None
    fields["max_width"] = None
    if not measure_identity:
        fields["label"] = None
        fields["header_link"] = None
    working = fill_table_column_defaults(
        TableColumnConfig(**fields),
        defaults,
        fallback_label=fallback_label,
        values=values,
    )
    # Carry every Resolved*-only field from the base (the format is the
    # measure's own, so resolve's data-gated bakes are right for every leaf),
    # computed from the model diff so a future resolved field can't silently
    # vanish on pivot leaves. ``scale`` is annotated on both models, so its
    # resolved instance is carried explicitly.
    resolved_extras: dict[str, Any] = {  # type-state: explicit_any — dump payload
        name: getattr(base, name)
        for name in set(ResolvedTableColumnConfig.model_fields)
        - set(TableColumnConfig.model_fields)
    }
    if base.scale is not None:
        resolved_extras["scale"] = base.scale
    return ResolvedTableColumnConfig.model_validate(
        {**working.model_dump(exclude_none=True), **resolved_extras}
    )


def _render_table_svg_core(
    *,
    chart_id: str,
    title: str | None,
    subtitle: str | None,
    link: str | None,
    rows: list[str] | None,
    pivot_columns: list[str] | None,
    values: list[str] | None,
    columns_promoted: dict[str, ResolvedTableColumnConfig] | None,
    column_defaults_promoted: TableColumnDefaultsConfig | None,
    header_overflow_promoted: str | None,
    conditional_formatting: dict[str, FieldConditionalFormatting] | None,
    table_style: ResolvedTableStyle,
    # board_style carries board/placeholder layout config, muted, link ink,
    # and the existing inline-spark and KPI-tone tokens consumed by row cells.
    # Table-local title, formats, pagination, and family style come only from
    # table_style.
    board_style: ResolvedStyle,
    data: list[dict[str, Any]],
    width: float | None,
    height: float | None,
    is_placeholder: bool,
    variables: dict[str, Any] | None,
    inset: dict[str, float] | None,
) -> str:
    """Shared SVG body for the table renderer.

    ``render_table_svg`` maps the resolved chart onto this signature; this is
    the single place the actual table SVG gets built.
    """
    tc = table_style.table

    # Chart-level pivot (rows/columns/values channels): reshape long → wide.
    # Called unconditionally — a flat table (no pivot_columns) and empty data
    # both pass straight through, so the layout sizer can call the same way and
    # can't disagree with us about which shapes reshape.
    # groups is None for flat tables and single-dim single-measure pivot;
    # list-of-levels otherwise (triggers N-row header rendering below).
    # row_role_spec is hoisted here (ahead of the row loop that also reads it) so
    # pivot_table_data can bucket a query-emitted total row into the bottom grid
    # row. TableRowStyle.role defaults to None — resolve_row_role/is_total_role
    # both accept None (treat every row as "value"), so unset is a valid case.
    row_role_spec = tc.row.role
    data, _pivot_groups, _pivot_effective_values = pivot_table_data(
        data,
        rows=rows or [],
        columns=pivot_columns,
        values=values,
        row_role_spec=row_role_spec,
    )

    # Build colors dict from chart-local TableChartStyle (which already has
    # board-level values cascaded in). board_style is only needed for font
    # color (link color), muted, and placeholder — do not add more reads
    # from it.
    #
    # TitleStyle.font is FontStyle (all Optional) — InheritSlot fills from _base.yaml;
    # all built-in themes set title.font.color, so None post-cascade is a cascade bug.
    assert table_style.title.font.color is not None, (
        "title.font.color must be populated by cascade"
    )
    # Subtitle fill comes from the same style.title.subtitle chart families use
    # for their Vega-Lite subtitle — not board_style.muted, a generic secondary-
    # text role that happens to diverge from it in some themes (e.g. editorial).
    assert table_style.title.subtitle.font.color is not None, (
        "title.subtitle.font.color must be populated by cascade"
    )
    colors: dict[str, str] = {
        "background": tc.background or "",
        "header_background": tc.header.background or "",
        "label_color": tc.header.font.color or "",
        "row_stripe": (tc.row.stripe.color if tc.row.stripe else None) or "",
        "color": tc.font.color or "",
        "title_color": table_style.title.font.color,
        "subtitle_color": table_style.title.subtitle.font.color,
        "muted": board_style.muted,
        # Table links render as body ink + weight 500 (the weight bump is
        # applied in the row loop) — emphasis comes from weight, not from
        # the colorful accent, matching the "bolder, not colorful" link
        # treatment used table-wide.
        "link": board_style.font.color,
    }
    colors["header_background"] = sanitize_color(
        tc.header.background,
        colors["header_background"],
    )
    colors["label_color"] = sanitize_color(
        tc.header.font.color,
        colors["label_color"],
    )
    colors["row_stripe"] = sanitize_color(
        tc.row.stripe.color if tc.row.stripe else None, colors["row_stripe"]
    )
    # Link color rides as the --dbt-link custom property on the row-link rect (row-link
    # hover) and into cell fill attributes, so it must be a validated color —
    # an authored font.color is free-form and would otherwise be a CSS/attr
    # injection sink. sanitize_color raises on anything but hex/transparent.
    colors["link"] = sanitize_color(board_style.font.color, colors["color"])
    # If tc.color has a static override, use it for text color.
    _tc_color_static = tc.color.static if tc.color is not None else None
    if _tc_color_static is not None:
        colors["color"] = sanitize_color(_tc_color_static, colors["color"])

    # tc IS both the style and the layout constants (TableChartStyle has all fields)
    table_config = tc

    row_height = int(tc.row.height)
    padding = int(tc.outer_padding)
    assert tc.font.size is not None, (
        "TableChartStyle.font.size must be set after cascade"
    )
    font_size = int(tc.font.size)
    # Header font size: when not explicitly set, MATCH the body font and
    # track it through the fit cascade.  BI's apparatus tier (11px) is
    # an explicit override that does NOT track body — header stays at 11
    # even when body shrinks to 8.  Minimal and Classic leave it unset
    # so headers travel with body 14 → 11 → 8.
    _header_inherits_body = tc.header.font.size is None
    header_font_size = (
        int(tc.header.font.size) if tc.header.font.size is not None else font_size
    )
    # Header weight: compact tier (body ≤11px) may use font_compact.weight to
    # apply a lighter weight when body text shrinks — e.g. BI theme uses 500 at
    # 11px vs. 600 at the default 14px body size.
    # tc.header.font.weight is guaranteed non-None by the cascade floor (_base.yaml sets '600').
    assert tc.header.font.weight is not None, (
        "tc.header.font.weight must be non-None (cascade floor: _base.yaml table.header.font.weight)"
    )
    _default_header_weight = str(tc.header.font.weight)
    if (
        font_size <= 11
        and tc.header.font_compact is not None
        and tc.header.font_compact.weight is not None
    ):
        header_font_weight = str(tc.header.font_compact.weight)
    else:
        header_font_weight = _default_header_weight
    symbol_mode = tc.symbol_mode
    wrap_cells = tc.wrap
    header_rule_width = float(tc.header.rule.width)
    # Header height: theme-required field (never unset after cascade).
    # Collapse to 0 when the header is hidden so the layout assigns all
    # vertical room to data rows.
    header_height = 0 if not tc.header.visible else int(tc.header.height)
    row_rule_width = float(tc.row.rule.width)
    # Summary rule: falls back to row_rule_width when not explicitly set.
    summary_rule_width = float(
        tc.row.roles.summary.rule_width or row_rule_width,
    )
    _summary_role_font = tc.row.roles.summary.font
    summary_font_weight = (
        font_weight_as_css(_summary_role_font.weight)
        if _summary_role_font is not None and _summary_role_font.weight is not None
        else None
    )
    # Per-role presentation from row.roles (summary / total).
    _role_summary = tc.row.roles.summary
    _role_total = tc.row.roles.total
    # Table font family can be overridden at the table level (e.g. Classic
    # variant uses Source Serif), falling back to the global body font.
    # Strip CSS-style quotes from font names for SVG compatibility — SVG
    # font-family attributes treat single quotes as literal characters, not
    # CSS string delimiters.  'Source Serif 4' → Source Serif 4.
    # Also prepend "Source Serif 4 Web" alias so the browser prefers our
    # bundled woff2 over a locally installed system font.
    # tc.font.family is always filled by apply_inherit in production paths
    assert tc.font.family is not None, "style.font.family must be configured"
    table_font_family = _svg_font_family(tc.font.family)
    numeric_cell_font = _table_numeric_cell_font(tc.font.family)
    header_rule_continuous = bool(tc.header.rule.continuous or False)
    # Rules default to the body text color for strong visibility.
    # Row-level rule color takes precedence over table-level rule color.
    # Sanitize all user-provided colors; fall back to theme text color.
    _raw_rule_color = tc.row.rule.color or (tc.rule.color if tc.rule else None)
    rule_color = sanitize_color(_raw_rule_color, colors["color"])
    bottom_padding = int(tc.bottom_padding)
    title_text = title  # authored; the visible canvas text gets cased
    # by compute_table_title_block_layout. Keep title_text uncased so the
    # tooltip <title> shows the original authored text for hover/screen-readers.
    subtitle_text = subtitle

    table_width: float = tc.preferred_width if width is None else width
    # The card is the slot, not the table: on column overflow table_width
    # widens past the slot (below) but the card must not follow it into the
    # neighbor, so the wrapper's box and this rect agree on the outer edge.
    slot_width = table_width
    title_font = table_style.title_font
    title_font_weight: int | str = int(title_font.weight)
    title_font_size = int(title_font.size)
    title_font_family_str = title_font.family
    title_style = table_style.title
    title_line_height = title_font_size + 2
    title_block = compute_table_title_block_layout(
        chart_title=str(title_text or ""),
        chart_subtitle=str(subtitle_text or ""),
        table_width=table_width,
        tc=tc,
        padding=padding,
        title_style=title_style,
        card_padding=float(board_style.frame.card_padding),
        title_font=title_font,
    )
    title_height = title_block.height
    rendered_title = title_block.rendered_title
    title_lines = list(title_block.title_lines)
    subtitle_lines = list(title_block.subtitle_lines)
    subtitle_font_size = title_block.subtitle_font_size

    # columns_promoted is already the complete, final per-column config
    # mapping (defaults + explicit overrides + FK links baked in at resolve).
    column_configs: dict[str, ResolvedTableColumnConfig] = dict(columns_promoted or {})

    # Tables read conditional_formatting rules directly at render time — no
    # internal lowering into column configs.
    column_when_rules: dict[str, tuple[Any, ...]] = {
        col: tuple(entry.when) for col, entry in (conditional_formatting or {}).items()
    }

    # For multi-dim / multi-measure pivot, expand measure-keyed configs/rules to leaf
    # keys. Authors key configs/rules by measure field name; leaf keys encode the full
    # column tuple (and measure when multi-measure) joined by _PIVOT_LEAF_SEP.
    # Single-dim single-measure leaves are plain col-values (no separator) — skipped.
    # _pivot_effective_values comes directly from pivot_table_data (no heuristics).
    _addressed_via_expansion: set[str] = set()
    if _pivot_groups is not None and data:
        _leaf_cols_all = [k for k in data[0] if _PIVOT_LEAF_SEP in k]
        _effective_values_set = set(_pivot_effective_values)

        _expanded_cc: dict[str, ResolvedTableColumnConfig] = {}
        # Multi-dim single-measure leafs carry no measure suffix, so the
        # measure-keyed lookup below can never hit — fan the single measure's
        # entry onto them here, same contract as every other pivot shape.
        _single_measure_cfg: ResolvedTableColumnConfig | None = None
        if len(_pivot_effective_values) == 1:
            _single_measure_cfg = column_configs.get(_pivot_effective_values[0])
        for leaf in _leaf_cols_all:
            _parts = leaf.split(_PIVOT_LEAF_SEP)
            # Measure is the last part when it's a known measure; otherwise no measure.
            _measure_part = _parts[-1] if _parts[-1] in _effective_values_set else None
            _display_label = _measure_part if _measure_part is not None else _parts[-1]
            existing = column_configs.get(_display_label)
            _authored_here = existing is not None
            if existing is None and _measure_part is None and _single_measure_cfg:
                existing = _single_measure_cfg
                _addressed_via_expansion.add(_pivot_effective_values[0])
            elif existing is not None:
                _addressed_via_expansion.add(_display_label)
            if existing is not None:
                # Identity survives the fan when the entry was authored under
                # this leaf's own display name (a measure sub-label, or a
                # leaf-part key); an entry reached via the single-measure
                # fallback is a dimension-value leaf and keeps its own.
                _expanded_cc[leaf] = _fanned_leaf_config(
                    existing,
                    fallback_label=slug_to_text(_display_label),
                    measure_identity=_authored_here,
                    defaults=column_defaults_promoted,
                    values=[row.get(leaf) for row in data],
                )
            else:
                # Measure not explicitly authored — its key space (this leaf)
                # only exists after the pivot transform above, so resolve
                # excluded it from column_configs; apply column_defaults here.
                _expanded_cc[leaf] = _as_resolved_table_column(
                    fill_table_column_defaults(
                        None,
                        column_defaults_promoted,
                        fallback_label=slug_to_text(_display_label),
                        values=[row.get(leaf) for row in data],
                    ),
                    table_style.formats,
                    numeric_cell_font,
                )
        column_configs = {**column_configs, **_expanded_cc}
        # Remap column_when_rules: measure-name key → leaf keys.
        _expanded_cwr: dict[str, tuple[Any, ...]] = {}
        for leaf in _leaf_cols_all:
            _parts = leaf.split(_PIVOT_LEAF_SEP)
            _measure_part = _parts[-1] if _parts[-1] in _effective_values_set else None
            if _measure_part and _measure_part in column_when_rules:
                _expanded_cwr[leaf] = column_when_rules[_measure_part]
        column_when_rules = {**column_when_rules, **_expanded_cwr}
    elif pivot_columns and data:
        # Single-dim single-measure pivot: leaf keys are the bare pivoted
        # values themselves (no separator). A measure-keyed entry fans out to
        # every leaf, same authoring shape as the multi-measure branch — so
        # `visible:` (and any styling) on the measure name means the same
        # thing on both pivot shapes. A leaf-keyed entry still wins over the
        # measure-keyed one. Un-authored leafs get column_defaults applied
        # here (their key space only exists after the pivot transform above).
        _measure_cfg: ResolvedTableColumnConfig | None = None
        _single_measure = next(iter(_pivot_effective_values), None)
        if _single_measure is not None and _single_measure in column_configs:
            _measure_cfg = column_configs[_single_measure]
        for leaf in data[0]:
            if leaf == row_role_spec or leaf in column_configs:
                continue
            if _measure_cfg is not None and _single_measure is not None:
                # Registered as addressed only when a fan actually happens —
                # if every leaf carries its own entry, the measure key stays
                # unaddressed and `visible:` on it fails loud instead of
                # silently doing nothing.
                _addressed_via_expansion.add(_single_measure)
                column_configs[leaf] = _fanned_leaf_config(
                    _measure_cfg,
                    fallback_label=slug_to_text(leaf),
                    measure_identity=False,
                    defaults=column_defaults_promoted,
                    values=[row.get(leaf) for row in data],
                )
                continue
            column_configs[leaf] = _as_resolved_table_column(
                fill_table_column_defaults(
                    None,
                    column_defaults_promoted,
                    fallback_label=slug_to_text(leaf),
                    values=[row.get(leaf) for row in data],
                ),
                table_style.formats,
                numeric_cell_font,
            )

    header_overflow = resolve_header_overflow(table_config, header_overflow_promoted)

    # A `visible: false` entry must address something the render can act on: a
    # post-pivot column, or a measure key the leaf expansion above fanned
    # out. Anything else is unreachable authoring — a typo, the wrong key
    # form for this pivot shape, or the row-role marker (stripped
    # unconditionally, so `visible:` on it can never do anything) — and
    # hiding it would silently do nothing, which is exactly the failure
    # `visible:` replaced. Only checkable when data exists (an empty result
    # has no key space to compare against; the empty-state render keeps its
    # headers).
    if data:
        _addressable = (set(data[0]) | _addressed_via_expansion) - {row_role_spec}
        # Derived style-input hides carry visible=False the author never
        # wrote; a pivot reshape can consume such a column, so exempt any key
        # another entry references as a style input — the guard is for
        # authored typos, not derivation.
        _style_ref_targets = {
            spec
            for cfg in column_configs.values()
            for spec in (
                cfg.background,
                cfg.font.color if cfg.font else None,
                cfg.font.weight if cfg.font else None,
            )
            if isinstance(spec, str)
        }
        _unaddressed = sorted(
            key
            for key, cfg in column_configs.items()
            if cfg.visible is False
            and key not in _addressable
            and key not in _style_ref_targets
            # The row-role marker is stripped unconditionally — hiding it is
            # already satisfied, not unreachable authoring.
            and key != row_role_spec
        )
        if _unaddressed:
            # Multi-measure leaf keys are _PIVOT_LEAF_SEP joins no author can
            # type — show the parts, the same as the authorable measure names.
            _shown = sorted(
                {key.replace(_PIVOT_LEAF_SEP, " / ") for key in _addressable}
            )
            raise ChartDataError(
                f"table {chart_id!r}: style.columns sets `visible:` on"
                f" {_unaddressed}, which match no rendered column — this"
                f" table's addressable columns are {_shown}."
                " Check for a typo, or key the entry by the name the rendered"
                " column actually carries (a pivot's measure name, or a bare"
                " pivoted value)."
            )

    # The rendered column list is the query's own (post-pivot) result shape —
    # style.columns is styling-only, so naming a column there never removes
    # any column, named or not, and its key order never reorders the table.
    # `visible: false` on a resolved entry is the sole way to hide a column.
    # An empty `data` means the pivot transform above never ran (there is no
    # post-pivot key space) — fall back to the resolved mapping's keys so an
    # authored table still shows its declared headers in the empty-state
    # render.
    if data:
        columns = list(data[0].keys())
    elif columns_promoted:
        columns = list(columns_promoted)
    else:
        columns = []
    # row.role is a reshape/styling signal, never a display column — strip it
    # unconditionally, whichever branch supplied the list, else it renders as
    # a spurious "Row Role" header column. (`row.role` cascades from the
    # theme/board, so it lands on tables that never asked for it.)
    if row_role_spec is not None:
        columns = [c for c in columns if c != row_role_spec]
    columns = [
        c
        for c in columns
        if (_cfg := column_configs.get(c)) is None or _cfg.visible is not False
    ]
    # Group-span descriptors index the pre-filter leaf list — remap them to
    # the surviving leaves so hiding a measure narrows each span instead of
    # shifting every later group label off its columns (and drop spans whose
    # leaves are all hidden).
    if _pivot_groups is not None and data:
        _pre_leaves = [k for k in data[0] if _PIVOT_LEAF_SEP in k]
        _kept_leaves = {c for c in columns if _PIVOT_LEAF_SEP in c}
        if len(_kept_leaves) != len(_pre_leaves):
            _kept_before = [0]
            for k in _pre_leaves:
                _kept_before.append(_kept_before[-1] + (k in _kept_leaves))
            _pivot_groups = [
                [
                    (label, _kept_before[first], n_kept)
                    for label, first, n in level
                    if (n_kept := _kept_before[first + n] - _kept_before[first]) > 0
                ]
                for level in _pivot_groups
            ]

    # Computed once over the full (post-pivot) dataset — not per page — so a
    # column's bar/column spark layout can't flip anchor or auto-max between
    # pages of the same paginated table. See _spark_column_layout.
    bar_auto_max, signed_layout_columns = _spark_column_layout(
        columns, column_configs, data, row_role_spec
    )

    available_width = table_width - (padding * 2)
    cell_pad = int(table_config.column_layout.cell_padding)

    # Fit cascade: if content overflows, progressively reduce padding then
    # font sizes.  11px is the floor — below that, truncation handles what
    # can't fit.  8px is unreadable and visually worse than ellipsis.
    _COMPACT_PAD = max(cell_pad // 2, 6)  # reduced padding floor
    _COMPACT_FONT = 11  # stepped-down body size (also the floor)
    _COMPACT_HEADER_FONT = 11  # header floor matches body

    _cell_font = FontStyle(size=float(font_size), family=table_font_family)
    _header_font = FontStyle(
        size=float(header_font_size),
        family=table_font_family,
        weight=header_font_weight,
        case=tc.header.font.case,
    )

    # Row-number column: compute its width NOW, before calculate_column_layout,
    # so the data-column layout pass gets a budget already reduced by the
    # synthetic column. This prevents the SVG widening when row_numbers is
    # toggled on for a table whose real columns already fill available_width.
    # Width is stable across pages (uses total row count, not per-page count).
    row_numbers = tc.row_numbers
    row_number_width = 0.0
    if row_numbers.visible:
        row_number_width = _row_number_column_width(
            row_numbers=row_numbers,
            total_row_count=len(data),
            table_config=table_config,
            font=_cell_font,
            measurer=get_font_measurer(table_font_family),
        )
    data_column_budget = available_width - row_number_width

    col_demands, col_header_demands = measure_column_demands(
        columns,
        column_configs,
        data,
        get_font_measurer(table_font_family),
        font_size=float(font_size),
        header_font_size=float(header_font_size),
        header_case=_header_font.case or "none",
        cell_pad=cell_pad,
        formats=table_style.formats,
        header_visible=tc.header.visible,
        column_when_rules=column_when_rules,
    )
    col_word_floors = measure_column_word_floors(
        columns,
        column_configs,
        data,
        get_font_measurer(table_font_family),
        font_size=float(font_size),
        header_font_size=float(header_font_size),
        header_case=_header_font.case or "none",
        cell_pad=cell_pad,
        header_visible=tc.header.visible,
        column_when_rules=column_when_rules,
    )
    col_widths, col_x_offsets, actual_content_width = calculate_column_layout(
        columns,
        column_configs,
        data_column_budget,
        demands=col_demands,
        header_demands=col_header_demands,
        width_similarity_threshold=table_config.column_layout.width_similarity_threshold,
        word_floors=col_word_floors,
        content_headroom=table_config.column_layout.content_headroom,
    )

    _formats = table_style.formats
    if _has_overflow(
        columns,
        data,
        column_configs,
        col_widths,
        cell_pad,
        _cell_font,
        header_font=_header_font,
        wrap=wrap_cells,
        formats=_formats,
        header_visible=tc.header.visible,
        column_when_rules=column_when_rules,
    ):
        # Step 1: reduce cell padding (table-wide)
        cell_pad = _COMPACT_PAD
        if _has_overflow(
            columns,
            data,
            column_configs,
            col_widths,
            cell_pad,
            _cell_font,
            header_font=_header_font,
            wrap=wrap_cells,
            formats=_formats,
            header_visible=tc.header.visible,
            column_when_rules=column_when_rules,
        ):
            # Step 2: step down font size to 11px (table-wide, cascade stops here)
            font_size = _COMPACT_FONT
            row_height = max(row_height - 4, 16)
            if _header_inherits_body:
                header_font_size = font_size  # Minimal/Classic: track body
            else:
                header_font_size = _COMPACT_HEADER_FONT
            # Rebuild FontStyle after cascade changes font sizes.
            _cell_font = FontStyle(size=float(font_size), family=table_font_family)
            _header_font = FontStyle(
                size=float(header_font_size),
                family=table_font_family,
                weight=header_font_weight,
                case=tc.header.font.case,
            )
            # Remaining overflow is handled by per-cell truncation (ellipsis).

    # After the cascade, body font size may have shrunk. When row numbers are
    # shown, row_number_width is font-dependent and must be recomputed so the
    # data-column budget reflects the final synthetic column width.
    _initial_font_size = float(int(tc.font.size))  # asserted non-None above
    if font_size < _initial_font_size and row_numbers.visible:
        row_number_width = _row_number_column_width(
            row_numbers=row_numbers,
            total_row_count=len(data),
            table_config=table_config,
            font=_cell_font,
            measurer=get_font_measurer(table_font_family),
        )
        data_column_budget = available_width - row_number_width
        col_demands, col_header_demands = measure_column_demands(
            columns,
            column_configs,
            data,
            get_font_measurer(table_font_family),
            font_size=float(font_size),
            header_font_size=float(header_font_size),
            header_case=_header_font.case or "none",
            cell_pad=cell_pad,
            formats=table_style.formats,
            header_visible=tc.header.visible,
            column_when_rules=column_when_rules,
        )
        col_word_floors = measure_column_word_floors(
            columns,
            column_configs,
            data,
            get_font_measurer(table_font_family),
            font_size=float(font_size),
            header_font_size=float(header_font_size),
            header_case=_header_font.case or "none",
            cell_pad=cell_pad,
            header_visible=tc.header.visible,
            column_when_rules=column_when_rules,
        )
        col_widths, col_x_offsets, actual_content_width = calculate_column_layout(
            columns,
            column_configs,
            data_column_budget,
            demands=col_demands,
            header_demands=col_header_demands,
            width_similarity_threshold=table_config.column_layout.width_similarity_threshold,
            word_floors=col_word_floors,
            content_headroom=table_config.column_layout.content_headroom,
        )

    # Now stitch the synthetic column into the layout: prepend at x=0 and
    # shift all data-column x_offsets right by row_number_width.
    if row_numbers.visible:
        columns = [_ROW_NUMBER_COL, *columns]
        col_widths = {_ROW_NUMBER_COL: row_number_width, **col_widths}
        col_x_offsets = [0.0, *[off + row_number_width for off in col_x_offsets]]
        actual_content_width += row_number_width

    # When the columns' honored widths sum past the width the table was given,
    # the table can only widen past its slot — in a dashboard tile that means it
    # spills over / collides with its neighbor. Record it (a no-op unless a
    # warning sink is open) so TABLE_COLUMNS_OVERFLOW can surface it, using the
    # renderer's own widen boundary as the threshold — no separate cutoff.
    if actual_content_width > available_width + _WIDTH_OVERFLOW_EPSILON:
        record_table_overflow(
            chart_id,
            TableOverflow(
                required_width=actual_content_width + padding * 2,
                available_width=table_width,
            ),
        )
        table_width = actual_content_width + padding * 2

    # Extract the data-only column/offset views once; reused by lane_positions
    # and resolve_wrapped_headers (both must skip the synthetic column).
    real_columns = [c for c in columns if c != _ROW_NUMBER_COL]
    real_col_x_offsets = [
        col_x_offsets[i] for i, c in enumerate(columns) if c != _ROW_NUMBER_COL
    ]

    # Compute lane positions FIRST (before wrap decisions) so the wrap
    # logic knows where the value lane sits in each column.  Use full
    # `data` here (not visible_data which depends on header_height that
    # we haven't determined yet — chicken-and-egg).  Lane positions are
    # max widths across rows; using all data is a slight over-estimate
    # but avoids the dependency cycle.
    lane_positions = _compute_lane_positions(
        rows=data,
        columns=real_columns,
        column_configs=column_configs,
        col_widths=col_widths,
        col_x_offsets=real_col_x_offsets,
        padding_x=padding,
        cell_pad=cell_pad,
        cell_font=_cell_font,
        column_when_rules=column_when_rules,
        formats=table_style.formats,
    )

    # When the header row is hidden, skip header wrapping/sizing entirely —
    # the resolver would otherwise re-compute a non-zero header_height even
    # though no header text will render. Keep header_height at 0 so layout
    # gives every pixel to data rows.
    wrapped_headers: dict[str, list[str]]
    truncated_headers: dict[str, bool]
    if not tc.header.visible:
        wrapped_headers = {}
        truncated_headers = {}
    else:
        wrapped_headers, truncated_headers, header_height_resolved = (
            resolve_wrapped_headers(
                real_columns,
                column_configs,
                col_widths,
                header_overflow=header_overflow,
                header_height=header_height,
                header_font=_header_font,
                padding=padding,
                table_config=table_config,
                cell_pad=cell_pad,
                measurer=get_font_measurer(table_font_family),
            )
        )
        header_height = int(header_height_resolved)

    # Multi-dim / multi-measure pivot: add one group-header row per descriptor level
    # above the leaf-header row. Each group row has the same height as one leaf-header
    # row (before the bump). _pivot_group_row_height is the height of a single row.
    _pivot_group_row_height: int = 0
    if _pivot_groups is not None and tc.header.visible:
        _pivot_group_row_height = header_height
        header_height = header_height + _pivot_group_row_height * len(_pivot_groups)

    # Pagination: extract current page from variables using chart ID
    current_page = _extract_page_from_variables(chart_id, variables) if chart_id else 1

    # Pre-compute per-row heights AND cache wrapped lines for the full
    # dataset. Heights flow into pagination/height-constrained slicing;
    # cached lines skip the re-wrap work in _render_data_rows.
    all_row_heights: list[int] | None = None
    all_wrapped_lines: list[dict[str, list[str]]] | None = None
    if wrap_cells and data:
        all_row_heights, all_wrapped_lines = _compute_wrap_layout(
            data,
            real_columns,
            column_configs,
            col_widths,
            cell_pad,
            font_size,
            row_height,
            table_config.text_baseline_offset,
            get_font_measurer(table_font_family),
            column_when_rules,
            formats=_formats,
        )

    (
        table_height,
        visible_data,
        total_pages,
        page_offset,
        per_row_heights,
        _max_page_rows_height,  # tallest-page sum; already folded into table_height above
        row_height,
    ) = _resolve_visible_rows(
        data,
        height=height,
        title_height=title_height,
        header_height=header_height,
        padding=padding,
        row_height=row_height,
        bottom_padding=bottom_padding,
        pagination=tc.pagination,
        page=current_page,
        row_heights=all_row_heights,
        header_visible=tc.header.visible,
        chart_id=chart_id,
    )
    # Cramping capture: the renderer has now settled its column budget and its
    # header wrapping, so the width rung of the degradation ladder is known.
    # Recorded into the sink WARN_TABLE_CRAMPED reads (a no-op unless a
    # warning sink is open). The height rung — a slot cutting rows-per-page —
    # is recorded separately as a TablePageSqueeze where the paginator
    # overrides the sizer.
    #
    # The demand is each column's own content width, or its header label plus
    # its cell padding where that is wider — the padded width the wrap
    # threshold compares against, so a suggestion sized from this sum actually
    # unwraps the headers it names. (Measured on the base pad/font basis; when
    # the fit cascade has dropped to the compact basis the threshold is
    # narrower, so this over-states demand — the suggestion still clears.) A column the author pinned (``width:``)
    # demands exactly its pin — met by construction — and a ``max_width:`` cap
    # bounds what its column could ever take, so neither inflates the
    # shortfall with growth no board width can deliver. Compared against the
    # budget the columns were actually divided into, not against the settled
    # widths: calculate_column_layout scales columns down to fit, so after
    # allocation the two always agree and the shortfall is gone.
    # A collapsed slot can hand the table a zero (or negative) column
    # budget; no width arithmetic is meaningful there — the physical
    # overflow capture owns that territory, and a %-pin would divide by
    # the budget below.
    if data_column_budget > 0:
        _demand = 0.0
        _relative_fraction = 0.0
        for col in real_columns:
            _cfg = column_configs.get(col)
            _width_hint = _cfg.width if _cfg is not None else None
            _pinned = parse_column_width(_width_hint, data_column_budget)
            if _pinned is not None:
                _demand += _pinned
                if isinstance(_width_hint, str) and _width_hint.strip().endswith("%"):
                    # A %-pinned column's demand is a slice of the budget itself;
                    # the detector solves for the budget where the absolute rest
                    # fits into what these leave over.
                    _relative_fraction += _pinned / data_column_budget
                continue
            _col_demand = max(col_demands[col], col_header_demands[col] + 2 * cell_pad)
            _max_hint = _cfg.max_width if _cfg is not None else None
            _cap = parse_column_width(_max_hint, data_column_budget)
            # A %-string max_width is itself budget-relative — treating the raw
            # demand as absolute over-states it, which only overshoots the
            # suggestion; a px cap bounds the demand exactly.
            if _cap is not None and not (
                isinstance(_max_hint, str) and _max_hint.strip().endswith("%")
            ):
                _col_demand = min(_col_demand, _cap)
            _demand += _col_demand
        _wrapped_count = sum(1 for lines in wrapped_headers.values() if len(lines) > 1)
        # Mirror record_table_page_squeeze: recorded only when the renderer
        # actually degraded — both arms, matching the detector, so a record the
        # only reader would ignore is never stored (and can never overwrite a
        # real one under last-write-wins).
        if _demand > data_column_budget and _wrapped_count:
            record_table_cramping(
                chart_id,
                TableCramping(
                    required_width=_demand,
                    available_width=data_column_budget,
                    wrapped_headers=_wrapped_count,
                    column_count=len(real_columns),
                    relative_demand_fraction=_relative_fraction,
                ),
            )

    # Slice cached wrapped-lines to align with visible_data.
    visible_wrapped_lines: list[dict[str, list[str]]] | None = None
    if all_wrapped_lines is not None:
        visible_wrapped_lines = all_wrapped_lines[
            page_offset : page_offset + len(visible_data)
        ]

    # A host that ships variables.js (dct serve, Cloud — see render/controls.py)
    # can act on data-dbt-page-var, so a single page renders and clicking
    # a control asks the host to re-render. A static export ships neither, so
    # every page is pre-rendered into its own toggle group and a small inline
    # script (table_pagination.js) flips which one is visible — the same
    # "the artifact must work standalone" reasoning chart_interactivity.js
    # already applies to hover tooltips.
    pagination_active = total_pages > 1
    static_multi_page = (
        pagination_active and bool(chart_id) and not controls_are_interactive()
    )
    # Computed early -- needs only total_pages, already known from the outer
    # _resolve_visible_rows call above -- so table_height can reserve room
    # for the cap note's own line before table_height_s is finalized below.
    static_export_capped = (
        static_multi_page
        and min(total_pages, _STATIC_MULTI_PAGE_MAX_PAGES) < total_pages
    )

    # Add breathing room before summary/total rows so double rules don't
    # crowd the last data row. Compute here to adjust total SVG height.
    _SUMMARY_GAP = int(row_height * 0.4)
    if static_multi_page:
        # Every page paints into the same canvas — budget for whichever
        # rendered page needs the most gap, not just `visible_data`'s page.
        # `page=1`'s window length is the per-page row count for every page
        # (constant regardless of which page is queried); reuse it to window
        # the full dataset the same way `_max_page_sum` windows row_heights.
        page_1_visible = _resolve_visible_rows(
            data,
            height=height,
            title_height=title_height,
            header_height=header_height,
            padding=padding,
            row_height=row_height,
            bottom_padding=bottom_padding,
            pagination=tc.pagination,
            page=1,
            row_heights=all_row_heights,
            header_visible=tc.header.visible,
        )[1]
        summary_gap_total = _max_page_summary_gap(
            data, row_role_spec, len(page_1_visible), _SUMMARY_GAP
        )
    else:
        summary_gap_total = _max_page_summary_gap(
            visible_data, row_role_spec, len(visible_data), _SUMMARY_GAP
        )
    # Apply summary gap when: (a) auto-sized (no explicit slot), or (b) the
    # renderer shrunk an oversized slot to content (table_height < height).
    # The sizer never includes summary gaps in its height estimate, so we
    # cannot rely on slot slack to absorb them.
    # Invariant: in the explicit-slot branch, every non-shrink path returns
    # table_height == height (squeezed/anti-dangle and pagination paths echo
    # the slot), so `table_height < height` is equivalent to "shrink fired."
    if summary_gap_total and (not (height and height > 0) or table_height < height):
        table_height += summary_gap_total

    # Reserve space for pagination controls when needed.
    # When height is explicit (from sizing), it already includes the control
    # height — only add it for auto-sized tables.
    pagination_control_height = _PAGINATION_CONTROL_HEIGHT if pagination_active else 0
    if pagination_active and not (height and height > 0):
        table_height += pagination_control_height

    # The cap note's own line. Gated the same way as pagination_control_height
    # just above: layout_sizing._get_table_height_from_data already reserves
    # _PAGINATION_CAP_NOTE_HEIGHT (beside its own _PAGINATION_CONTROL_HEIGHT
    # add) whenever it estimates more pages than the static-export cap, so an
    # explicit height already includes it -- adding it again here would
    # double-reserve and, on a grid: layout, paint 20px into whatever sits
    # below (grid items are placed at a precomputed pixel_y that a sibling's
    # height never corrects, unlike rows:/cols:).
    if static_export_capped and not (height and height > 0):
        table_height += _PAGINATION_CAP_NOTE_HEIGHT

    # Start building SVG
    svg_parts: list[str] = []
    table_width_s = _format_svg_numeric(table_width)
    table_height_s = _format_svg_numeric(table_height)

    # Background — omit rect when table.background is unset or transparent.
    if colors["background"] and colors["background"].lower() != "transparent":
        bx, by, bw, bh = card_box(slot_width, table_height, inset)
        svg_parts.append(
            f'<rect x="{_format_svg_numeric(bx)}" y="{_format_svg_numeric(by)}" '
            f'width="{_format_svg_numeric(bw)}" height="{_format_svg_numeric(bh)}" '
            f'fill="{colors["background"]}" rx="4"/>',
        )

    current_y = padding

    # Title
    if title_text:
        # Derived from the title's own font size (_TITLE_ASCENT_RATIO), not a
        # flat theme constant — matches compute_table_title_block_layout().
        title_baseline = current_y + float(round(title_font_size * _TITLE_ASCENT_RATIO))
        # Emit inner <title> when the rendered text differs from the authored —
        # catches all overflow modes (clip, truncate, wrap-two). The old "…" sniff
        # missed clip mode which shortens without an ellipsis. Compare against the
        # case-transformed authored text since rendered_title is cased upstream.
        _title_case = table_style.title.font.case
        _cased_authored = (
            apply_case(str(title_text), _title_case)
            if _title_case and _title_case != "none"
            else str(title_text)
        )
        is_title_truncated = rendered_title != _cased_authored
        inner_title = (
            f"<title>{html_module.escape(str(title_text))}</title>"
            if is_title_truncated
            else ""
        )
        svg_parts.append(
            f'<text x="{padding}" y="{title_baseline}" '
            f'font-size="{title_font_size}" font-weight="{title_font_weight}" fill="{colors["title_color"]}" '
            f'font-family="{title_font_family_str}"{authored_kind_attr("title")}>{inner_title}',
        )
        for line_index, line in enumerate(title_lines):
            line_y = title_baseline + (line_index * title_line_height)
            svg_parts.append(
                f'<tspan x="{padding}" y="{line_y}">{html_module.escape(line)}</tspan>',
            )
        svg_parts.append("</text>")
        if subtitle_text:
            last_title_baseline = title_baseline + (
                (len(title_lines) - 1) * title_line_height
            )
            subtitle_y = _subtitle_baseline_below_title(
                title_baseline=last_title_baseline,
                title_font_size=title_font_size,
                title_subtitle_gap=table_config.title_subtitle_gap,
                subtitle_font_size=subtitle_font_size,
            )
            subtitle_line_height = subtitle_font_size + 2
            # Same contract as the title above: when wrap-two clips a very long
            # subtitle, the inner <title> keeps the full text reachable on hover
            # and to screen readers.
            subtitle_inner_title = (
                f"<title>{html_module.escape(subtitle_text)}</title>"
                if " ".join(subtitle_lines) != subtitle_text
                else ""
            )
            svg_parts.append(
                f'<text x="{padding}" '
                f'font-size="{subtitle_font_size}" fill="{colors["subtitle_color"]}" '
                f'font-family="{table_font_family}"{authored_kind_attr("subtitle")}>{subtitle_inner_title}',
            )
            for line_index, line in enumerate(subtitle_lines):
                line_y = subtitle_y + (line_index * subtitle_line_height)
                svg_parts.append(
                    f'<tspan x="{padding}" y="{line_y}">{html_module.escape(line)}</tspan>',
                )
            svg_parts.append("</text>")
        current_y += title_height

    # lane_positions already computed above (before resolve_wrapped_headers)

    # Skip header rendering when style.header.visible is False. Header
    # contributes 0 to layout (header_height has been zeroed above) so
    # data rows start immediately after the title (or at the table top
    # when no title). The header-body gap is also skipped.
    if tc.header.visible:
        # Multi-dim / multi-measure pivot: render N group-label rows (one per level)
        # stacked above the leaf-header row.
        if _pivot_groups is not None and _pivot_group_row_height > 0:
            _render_pivot_group_header(
                svg_parts,
                levels=_pivot_groups,
                columns=columns,
                colors=colors,
                table_width=table_width,
                group_row_height=_pivot_group_row_height,
                current_y=current_y,
                padding_x=padding,
                col_x_offsets=col_x_offsets,
                col_widths=col_widths,
                header_font=_header_font,
                cell_pad=cell_pad,
            )
        _n_group_rows = len(_pivot_groups) if _pivot_groups is not None else 0
        _leaf_header_y = current_y + _pivot_group_row_height * _n_group_rows
        _leaf_header_h = header_height - _pivot_group_row_height * _n_group_rows
        _render_header_section(
            svg_parts,
            columns=columns,
            column_configs=column_configs,
            colors=colors,
            table_config=table_config,
            table_width=table_width,
            header_height=_leaf_header_h,
            current_y=_leaf_header_y,
            padding_x=padding,
            col_x_offsets=col_x_offsets,
            col_widths=col_widths,
            col_lane_positions=lane_positions,
            header_font=_header_font,
            wrapped_headers=wrapped_headers,
            truncated_headers=truncated_headers,
            cell_pad=cell_pad,
            header_rule_width=header_rule_width,
            rule_color=rule_color,
            header_rule_continuous=header_rule_continuous,
            row_numbers=row_numbers if row_numbers.visible else None,
            chart_id=chart_id,
        )

    current_y += header_height
    # Small whitespace gap between header and first data row for visual
    # hierarchy. Scales with row height. Skipped when header is disabled —
    # data rows start at the table top (after the title, if any).
    header_body_gap = int(row_height * 0.25) if tc.header.visible else 0
    current_y += header_body_gap

    def _paint_data_rows(
        target: list[str],
        rows: list[dict[str, Any]],
        offset: int,
        row_heights_page: list[int] | None,
        wrapped_lines_page: list[dict[str, list[str]]] | None,
    ) -> None:
        _render_data_rows(
            target,
            table_config=table_config,
            rows=rows,
            columns=columns,
            column_configs=column_configs,
            column_when_rules=column_when_rules,
            colors=colors,
            col_widths=col_widths,
            col_x_offsets=col_x_offsets,
            col_lane_positions=lane_positions,
            padding_x=padding,
            current_y=current_y,
            row_height=row_height,
            cell_font=_cell_font,
            table_width=table_width,
            cell_pad=cell_pad,
            symbol_mode=symbol_mode,
            row_rule_width=row_rule_width,
            summary_rule_width=summary_rule_width,
            rule_color=rule_color,
            row_role_spec=row_role_spec,
            summary_font_weight=summary_font_weight,
            role_summary=_role_summary,
            role_total=_role_total,
            resolved_style=board_style.chart_defaults,
            formats=table_style.formats,
            row_numbers=row_numbers if row_numbers.visible else None,
            page_offset=offset,
            row_heights=row_heights_page,
            wrapped_lines_by_row=wrapped_lines_page,
            wrap=wrap_cells,
            chart_root_link=link,
            chart_id=chart_id,
            bar_auto_max=bar_auto_max,
            signed_layout_columns=signed_layout_columns,
        )

    # The pager sits off the CURRENT page's own rows height, not `rows_height`
    # (the tallest page in the dataset — see _max_page_sum). Table sizing still
    # reserves the tallest-page height so the card doesn't resize between
    # pages; only the pager's y-position follows the page actually painted,
    # so a short page doesn't carry a taller page's whitespace above it.
    # `per_row_heights`/`visible_data`/`row_height` (effective) are the
    # current page's own values regardless of pagination — _resolve_visible_rows
    # only substitutes the max-page sum into the sizing return, not these.
    current_page_rows_height = (
        sum(per_row_heights)
        if per_row_heights is not None
        else len(visible_data) * row_height
    )
    indicator_y = current_y + current_page_rows_height + bottom_padding

    if static_multi_page:
        assert chart_id is not None  # static_multi_page requires a truthy chart_id
        page_var_name = f"{chart_id}_page"
        safe_chart_id = html_module.escape(chart_id, quote=True)
        rendered_pages = min(total_pages, _STATIC_MULTI_PAGE_MAX_PAGES)
        capped = static_export_capped
        if capped:
            record_static_pagination_cap(
                chart_id,
                StaticPaginationCap(
                    rendered_pages=rendered_pages, total_pages=total_pages
                ),
            )
        # A deep-linked page past what the export can pre-render has nothing
        # to show — fall back to the last rendered page rather than leaving
        # every toggle group hidden.
        initial_page = min(current_page, rendered_pages)
        # Tracks the tallest RENDERED page's own indicator_y, for the cap
        # note's anchor below -- the outer _max_page_sum value spans every
        # page in the whole dataset, including pages past the rendered_pages
        # cap that never paint, which would push the note lower than
        # necessary.
        max_rendered_page_indicator_y: float | None = None
        for page_n in range(1, rendered_pages + 1):
            (
                _page_table_h,
                page_visible_data,
                _page_total_pages,
                page_offset_n,
                page_row_heights,
                _page_rows_height,
                page_row_h,
            ) = _resolve_visible_rows(
                data,
                height=height,
                title_height=title_height,
                header_height=header_height,
                padding=padding,
                row_height=row_height,
                bottom_padding=bottom_padding,
                pagination=tc.pagination,
                page=page_n,
                row_heights=all_row_heights,
                header_visible=tc.header.visible,
            )
            # This page's own pager y — see the outer indicator_y comment
            # above: each pre-rendered toggle group carries its own rows
            # height, not the tallest page's, so the pager sits right below
            # ITS rows when toggled visible.
            page_current_rows_height = (
                sum(page_row_heights)
                if page_row_heights is not None
                else len(page_visible_data) * page_row_h
            )
            page_indicator_y = current_y + page_current_rows_height + bottom_padding
            if (
                max_rendered_page_indicator_y is None
                or page_indicator_y > max_rendered_page_indicator_y
            ):
                max_rendered_page_indicator_y = page_indicator_y
            page_wrapped_lines = (
                all_wrapped_lines[
                    page_offset_n : page_offset_n + len(page_visible_data)
                ]
                if all_wrapped_lines is not None
                else None
            )
            page_parts: list[str] = []
            _paint_data_rows(
                page_parts,
                page_visible_data,
                page_offset_n,
                page_row_heights,
                page_wrapped_lines,
            )
            page_parts.append(
                _render_pagination_controls(
                    page=page_n,
                    total_pages=rendered_pages,
                    page_var_name=page_var_name,
                    table_width=table_width,
                    y=page_indicator_y,
                    font_family=table_font_family,
                    paginator=table_config.paginator,
                    row_start=page_offset_n + 1,
                    row_end=page_offset_n + len(page_visible_data),
                    total_rows=len(data),
                    padding=padding,
                )
            )
            display = "" if page_n == initial_page else "none"
            svg_parts.append(
                f'<g class="dbt-table-page" data-dbt-table-page="{safe_chart_id}" '
                f'data-page="{page_n}" style="display:{display}">'
                + "".join(page_parts)
                + "</g>"
            )
        if capped:
            assert max_rendered_page_indicator_y is not None  # loop always sets it
            # One line below the tallest RENDERED page's own pager + label:
            # no rendered page's own indicator_y can exceed this value, so
            # the note clears every one of them regardless of which page is
            # initially visible.
            cap_note_y = max_rendered_page_indicator_y + _PAGINATION_CAP_NOTE_HEIGHT
            # A slot shorter than the table's natural height squeezes out the
            # band the note was going to occupy. Drop the note rather than
            # clamp it upward — clamping lands it on the row-range label, two
            # muted strings at the same baseline, which is the collision the
            # band exists to prevent. Same choice the label itself makes when
            # it would collide with the pager, and the author still learns
            # about the truncation from WARN-STATIC-PAGINATION-CAPPED, which
            # does not depend on this line rendering.
            # No second bottom_padding on the right-hand side: cap_note_y is
            # measured off page_indicator_y, which already carries one, and
            # table_height reserves the note's band as a flat
            # _PAGINATION_CAP_NOTE_HEIGHT on both the sizer and the renderer
            # side. Subtracting bottom_padding again demands room nobody
            # reserved, dropping the note under any bottom_padding above ~8.
            note_bottom = cap_note_y + _PAGINATION_CAP_NOTE_HEIGHT
            if note_bottom <= table_height:
                svg_parts.append(
                    _render_static_pagination_cap_note(
                        rendered_pages=rendered_pages,
                        total_pages=total_pages,
                        padding=padding,
                        y=cap_note_y,
                        font_family=table_font_family,
                        paginator=table_config.paginator,
                    )
                )
        svg_parts.append(_table_pagination_script())
    else:
        _paint_data_rows(
            svg_parts, visible_data, page_offset, per_row_heights, visible_wrapped_lines
        )

        # Show pagination controls or "more rows" indicator if data was truncated.
        if len(data) > len(visible_data):
            if pagination_active and chart_id:
                # Interactive pagination controls
                page_var_name = f"{chart_id}_page"
                controls_svg = _render_pagination_controls(
                    page=current_page,
                    total_pages=total_pages,
                    page_var_name=page_var_name,
                    table_width=table_width,
                    y=indicator_y,
                    font_family=table_font_family,
                    paginator=table_config.paginator,
                    row_start=page_offset + 1,
                    row_end=page_offset + len(visible_data),
                    total_rows=len(data),
                    padding=padding,
                )
                svg_parts.append(controls_svg)
            else:
                more_count = len(data) - len(visible_data)
                svg_parts.append(
                    f'<text x="{table_width / 2}" y="{indicator_y}" '
                    f'font-size="{table_config.more_rows.font.size}" fill="{colors["muted"]}" text-anchor="middle" font-style="italic" '
                    f'font-family="{table_font_family}">'
                    f"+ {more_count} more rows</text>",
                )

    # Empty state (only show if not placeholder - placeholder has data)
    if not data and not is_placeholder:
        svg_parts.append(
            f'<text x="{table_width / 2}" y="{table_height / 2}" '
            f'font-size="{table_config.empty_state.font.size}" fill="{colors["muted"]}" text-anchor="middle" '
            f'font-family="{table_font_family}">'
            f"No data</text>",
        )

    # Row-scoped link affordance: a transparent band that reveals a subtle
    # selection background on hover. A tint of the link/text color reads on
    # both light and dark themes (text contrasts with its background by
    # definition). Distinct from the ink+underline cell-link treatment so the
    # two affordances read differently. Only emitted when the table has a

    # Wrap in SVG
    svg_result = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{table_width_s}" height="{table_height_s}" viewBox="0 0 {table_width_s} {table_height_s}">
{"".join(svg_parts)}
</svg>"""

    # Apply placeholder styling if needed
    if is_placeholder:
        from dbt_charts.core.render.placeholder import (
            add_placeholder_overlay,
            apply_placeholder_opacity,
        )

        svg_result = apply_placeholder_opacity(svg_result, resolved_style=board_style)
        svg_result = add_placeholder_overlay(
            svg_result,
            table_width,
            table_height,
            font=FontStyle(family=table_font_family),
            resolved_style=board_style,
        )

    return svg_result


def render_table_svg(
    chart: ResolvedTableChart,
    data: list[dict[str, Any]],
    width: float | None = None,
    height: float | None = None,
    *,
    board_style: ResolvedStyle,
    is_placeholder: bool = False,
    variables: VariableValues | None = None,
    inset: dict[str, float] | None = None,
) -> str:
    """Render a ResolvedTableChart as SVG.

    The resolved chart owns every chart-local presentation decision. The board
    style supplies only board chrome and placeholder tokens.

    ``variables`` is an explicit override for direct/test callers. A caller
    that omits it (every production caller except tests) gets the board's
    current variable values from ``current_board_variables()`` instead of
    silently pagination-ing to page 1 — the ContextVar is the only source a
    caller cannot forget to wire up, unlike a parameter each call site has to
    remember to pass. See ``board_variables.py``.
    """
    effective_variables = (
        variables if variables is not None else current_board_variables()
    )
    data = normalize_data_types(data)
    columns = chart.columns

    if chart.style.table.transpose and data:
        _transpose_numeric_font = _table_numeric_cell_font(
            chart.style.table.font.family
        )
        data, columns = _transpose_data_for_render(
            chart.style.formats,
            columns,
            chart.column_defaults,
            data,
            _transpose_numeric_font,
        )

    return _render_table_svg_core(
        chart_id=chart.id,
        title=chart.title,
        subtitle=chart.subtitle,
        link=chart.link,
        rows=chart.rows,
        pivot_columns=chart.pivot_columns,
        values=chart.values,
        columns_promoted=columns,
        column_defaults_promoted=chart.column_defaults,
        header_overflow_promoted=chart.header_overflow,
        conditional_formatting=chart.conditional_formatting,
        table_style=chart.style,
        board_style=board_style,
        data=data,
        width=width,
        height=height,
        is_placeholder=is_placeholder,
        variables=effective_variables,
        inset=inset,
    )


# ---------------------------------------------------------------------------
# Pivot helper — long-form → wide-form (cross-tab) transformation
# ---------------------------------------------------------------------------


_PIVOT_LEAF_SEP = "\x1e"  # ASCII record-separator; not valid in SQL column names


def _pivot_leaf_key(col_tuple: tuple[str, ...], measure: str) -> str:
    """Leaf key for multi-measure pivots: col_vals + measure joined by _PIVOT_LEAF_SEP."""
    return _PIVOT_LEAF_SEP.join(list(col_tuple) + [measure])


def _pivot_leaf_key_no_measure(col_tuple: tuple[str, ...]) -> str:
    """Leaf key for single-measure multi-dim pivots: col_vals joined by _PIVOT_LEAF_SEP."""
    return _PIVOT_LEAF_SEP.join(col_tuple)


def _validate_pivot_fields(
    data: list[dict[str, Any]],
    rows: list[str],
    columns: list[str],
    values: list[str] | None,
    row_role_spec: str | None,
) -> tuple[list[str], list[str]]:
    """Validate pivot field names and resolve inferred ``rows``/``values``.

    Returns ``(rows, effective_values)`` — either may be inferred from the
    observed query columns when omitted, preserving query order. Raises
    ``ChartDataError`` on a typo'd ``columns``/``values``/``rows`` field, on an
    empty measure set, or on the one ``row_role_spec`` misuse (a literal role
    value used where a column name belongs).
    """
    all_keys = list(data[0].keys())
    observed = sorted(all_keys)

    for c in columns:
        if c not in all_keys:
            raise ChartDataError(
                f"pivot.column {c!r} not in data rows; observed keys: {observed}"
            )

    # ``row.role`` cascades from the theme/board, so an ABSENT column name is
    # harmless — ``resolve_row_role`` maps it to "value" as for a flat table.
    # Reject only a literal role value (``row.role: total``) that is not itself a
    # real column: under a pivot it would resolve every row to that role and
    # silently drop the reshape.
    if (
        row_role_spec is not None
        and row_role_spec not in all_keys
        and row_role_spec in _VALID_ROW_ROLES
    ):
        raise ChartDataError(
            f"pivot.row_role {row_role_spec!r} is a literal role, not a query"
            f" column; under a pivot row.role must name the query column that"
            f" carries the per-row role — observed keys: {observed}"
        )

    effective_values = infer_pivot_measure_names(
        all_keys, rows, columns, values, row_role_spec
    )
    for v in effective_values:
        if v not in all_keys:
            raise ChartDataError(
                f"pivot.value {v!r} not in data rows; observed keys: {observed}"
            )
    if not effective_values:
        raise ChartDataError(
            "pivot: no measure columns — `values` resolved to empty; declare at"
            f" least one measure field. observed keys: {observed}"
        )

    if not rows:
        rows = [
            k
            for k in all_keys
            if k not in columns and k not in effective_values and k != row_role_spec
        ]
    else:
        for r in rows:
            if r not in all_keys:
                raise ChartDataError(
                    f"pivot.row {r!r} not in data rows; observed keys: {observed}"
                )

    return rows, effective_values


def _build_pivot_levels(
    col_tuples: list[tuple[str, ...]],
    columns: list[str],
    n_measures: int,
) -> list[list[tuple[str, int, int]]]:
    """Build the col-dim group-header descriptor rows (rendered above the leaf row).

    Multi-measure emits all ``k`` col-dim levels; single-measure emits the outer
    ``k - 1`` (the innermost dim is itself the leaf label, so emitting it again
    would duplicate the header). Each level is a list of
    ``(label, first_leaf_idx, n_leaves)`` spans over the ordered ``col_tuples``.
    """
    n_col_tuples = len(col_tuples)
    leaves_per_tuple = n_measures if n_measures > 1 else 1
    n_group_dim_levels = len(columns) if n_measures > 1 else len(columns) - 1

    levels: list[list[tuple[str, int, int]]] = []
    for dim_idx in range(n_group_dim_levels):
        level: list[tuple[str, int, int]] = []
        i = 0
        while i < n_col_tuples:
            label = col_tuples[i][dim_idx]
            # Count how many consecutive col_tuples share the same dim_idx value.
            j = i
            while j < n_col_tuples and col_tuples[j][dim_idx] == label:
                j += 1
            first_leaf = i * leaves_per_tuple
            n_span_leaves = (j - i) * leaves_per_tuple
            level.append((label, first_leaf, n_span_leaves))
            i = j
        levels.append(level)
    return levels


# Per-dimension order key: (0, comparable) for a real value, (1, 0) for null.
# The comparable is homogeneous within a dimension (all instants, all numbers,
# or all first-seen ints), so the union is never cross-compared.
_PivotDimOrderKey = tuple[int, "dt.datetime | int | float | Decimal"]


def _order_col_tuples(
    col_tuples: list[tuple[str, ...]],
    columns: list[str],
    data: list[dict[str, Any]],  # type-state: explicit_any — query rows
) -> list[tuple[str, ...]]:
    """Order pivot col-tuples canonically where a dimension has such an order.

    A reader assumes a pivot dimension's columns follow its natural order —
    chronological for dates, ascending for numbers (every reference tool sorts
    them) — so first-seen order there is actively misleading while looking
    deliberate. Per dimension: chronological when every non-null raw value
    parses to a calendar instant (``calendar_bucket_key`` — date/datetime
    objects, ISO strings, bucket strings like "Jan 2026"), ascending when every
    non-null raw value is a number (bools and NaN excluded — bools aren't
    values a reader ranks, and NaN doesn't order), first-seen otherwise. Plain
    strings have no canonical order, so query order stays the author's lever
    (business-ordered categories, the documented trailing-"Total" column); a
    dimension mixing parseable and unparseable values keeps first-seen order
    too — sorting half a dimension would be guessing. Nulls order last in a
    sorted dimension.

    When no dimension has a canonical order the tuples come back unchanged —
    including any first-seen interleaving of outer-dim values. Otherwise the
    sort key covers every dimension: an unsortable dimension is keyed by each
    value's first appearance (one consistent order across the whole header,
    not per-outer-group query order), so outer-dim groups come out contiguous
    and ``_build_pivot_levels`` emits one span per group — barring distinct
    labels that tie on the same sort key (two spellings of one instant), where
    the tie falls back to inner-dim order.
    """
    dim_keys: list[dict[str, _PivotDimOrderKey] | None] = []
    for col in columns:
        raw_by_str: dict[str, Any] = {}  # type-state: explicit_any — raw cells
        for row in data:
            raw_by_str.setdefault(str(row[col]), row[col])
        non_null = {s: v for s, v in raw_by_str.items() if v is not None}
        instants = {
            s: instant
            for s, v in non_null.items()
            if (instant := calendar_bucket_key(v)) is not None
        }
        keys: dict[str, _PivotDimOrderKey] | None
        if non_null and len(instants) == len(non_null):
            keys = {s: (0, i) for s, i in instants.items()}
        elif non_null and all(
            # v == v is the NaN filter: Decimal("NaN") raises on <, float NaN
            # sorts arbitrarily — either poisons the whole dimension's order.
            isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) and v == v
            for v in non_null.values()
        ):
            keys = {s: (0, v) for s, v in non_null.items()}
        else:
            keys = None
        if keys is not None:
            for s in raw_by_str:
                keys.setdefault(s, (1, 0))  # nulls after every real value
        dim_keys.append(keys)

    if all(k is None for k in dim_keys):
        return col_tuples

    first_seen: list[dict[str, int]] = []
    for dim_idx in range(len(columns)):
        seen: dict[str, int] = {}
        for ct in col_tuples:
            seen.setdefault(ct[dim_idx], len(seen))
        first_seen.append(seen)

    def tuple_key(ct: tuple[str, ...]) -> tuple[_PivotDimOrderKey, ...]:
        return tuple(
            keys[v] if keys is not None else (0, first_seen[i][v])
            for i, (v, keys) in enumerate(zip(ct, dim_keys, strict=True))
        )

    return sorted(col_tuples, key=tuple_key)


def pivot_table_data(
    data: list[dict[str, Any]],
    *,
    rows: list[str],
    columns: list[str] | None,
    values: list[str] | None,
    row_role_spec: str | None = None,
) -> tuple[list[dict[str, Any]], list[list[tuple[str, int, int]]] | None, list[str]]:
    """Reshape long-form tidy data into a wide pivot matrix.

    Also the single owner of the "is this a pivot?" decision: no ``columns``
    means a flat table and the rows pass through untouched. The renderer and
    the layout sizer (``layout_sizing._get_table_height_from_data``) both call
    unconditionally, so the height the sizer reserves is always the row count
    the renderer draws.

    Args:
        data: Long-form source rows.
        rows: Row-dimension field names (form the composite row key). When
            empty, infers as all query columns not in ``columns``, not in the
            resolved ``values``, and not the role marker — preserving query
            column order (mirrors ``values``'s own inference).
        columns: Pivot-column field(s) whose distinct values become column headers.
            Single field → single-dimension pivot. Multiple fields → nested
            multi-dimension pivot (outer dim first, inner dim last). Empty or
            ``None`` → not a pivot; ``data`` comes back unchanged. Column order
            per dimension: canonical when one exists — chronological for
            temporal values, ascending for numeric — else first-seen (query)
            order, which keeps ``ORDER BY`` the author's lever for
            business-ordered categories and the trailing "Total" column
            (``_order_col_tuples``).
        values: Measure field names.  When ``None``, infers as all query columns
            not in ``rows``, not any column field, and not the role marker,
            preserving query column order.
        row_role_spec: Optional row-role column name (the same
            ``style.table.row.role`` column flat tables resolve). Because that
            setting cascades from the theme/board, an absent column name is
            harmless (resolves to "value", like a flat table); only a literal
            role value used as the spec raises. "total"-role tidy rows bucket by
            their rows-dim tuple exactly like every other row and keep first-seen
            (query) order — so a single grand total (one consistent label, e.g.
            ``region='Total'``) merges into one row, and distinct total labels
            stay distinct rather than collapsing into a fabricated one. The
            marker is copied onto every wide row so non-total roles keep their
            styling; the caller strips it from the rendered headers. Row totals
            (a trailing "Total" column) need nothing here — a literal "Total"
            ``columns`` value reshapes into a trailing leaf like any other.

    Returns:
        ``(wide_data, levels, effective_values)`` where:
        - ``wide_data`` is a list of dicts with row-dim keys + leaf columns.
          Rows come in first-seen (query) order — total-role rows included;
          render never reorders rows, so a query that wants a bottom total row
          orders it last. Leaf-column order is ``_order_col_tuples``'s
          contract (canonical per dimension where one exists, else first-seen).
        - ``effective_values`` is the resolved measure list (inferred or explicit).
        - ``levels`` is ``None`` for the single-dim single-measure case (leaf keys
          are the raw col-values).
        - Otherwise ``levels`` is a list of col-dim-only group-header descriptor
          levels. These are the rows rendered ABOVE the leaf header. The leaf header
          row shows: measure names (multi-measure) or the innermost col-dim value
          (single-measure multi-dim). Consequently:
          - multi-measure: ``len(levels) == len(columns)`` (all col-dims)
          - single-measure, k col-dims: ``len(levels) == k - 1`` (outer k-1 dims;
            innermost is the leaf row)
          Each level is a list of ``(label, first_leaf_idx, n_leaves)`` spans.

    Raises:
        ChartDataError: when a ``columns``, ``values``, or declared ``rows``
            field is not a key of the observed data rows — a typo must never
            silently render blank cells or a raw ``KeyError``. ``row_role_spec``
            raises only when it is a literal role value rather than a column name
            (an absent column resolves to "value", like a flat table).
        ChartDataError: when both ``rows`` and ``values`` are omitted — the row
            and measure dimensions are then ambiguous and cannot be inferred.
        ChartDataError: when a (row-key, col-tuple, measure) combination maps to
            more than one source row — the message ends with "— move the extra
            field onto rows or columns." (a total row keys by rows-dim like any
            other, so this covers both detail and total collisions).
    """
    if not columns or not data:
        return data, None, []

    # Both dims omitted → the remaining fields can't be split into rows vs
    # measures without guessing. Validate-and-error rather than degenerate into
    # a single row with the intended row dimension silently turned into a value.
    if values is None and not rows:
        raise ChartDataError(
            "pivot: declare at least one of `rows` or `values` — with both"
            " omitted the row and measure dimensions are ambiguous."
        )

    rows, effective_values = _validate_pivot_fields(
        data, rows, columns, values, row_role_spec
    )
    # An absent cascading role column is the documented-harmless case: every
    # table inherits style.table.row.role from the theme, but most queries have
    # no such column. Collapse it to "no role" so we never write a phantom role
    # key onto the wide rows. When the column IS present, thread it through so
    # total rows keep their marker for the flat-table double-rule styling.
    role_col = row_role_spec if row_role_spec in data[0] else None

    n_measures = len(effective_values)
    single_dim = len(columns) == 1

    # Enumerate the ACTUAL distinct col-tuples across ALL rows (detail + total).
    # A total row's column value earns a leaf slot even when no detail row
    # shares it — dropping that cell would silently discard a number the query
    # returned. Do NOT use itertools.product — that would create phantom
    # columns for sparse data where not all (outer, inner, …) combinations are
    # present. Dimensions with a canonical order (temporal, numeric) then sort;
    # others keep this first-seen order — see _order_col_tuples.
    col_tuples: list[tuple[str, ...]] = list(
        dict.fromkeys(tuple(str(row[c]) for c in columns) for row in data)
    )
    col_tuples = _order_col_tuples(col_tuples, columns, data)

    # Build ordered leaf columns.
    # Leaf key format:
    #   multi-measure: SEP.join(col_vals + [measure])
    #   single-measure (single- or multi-dim): SEP.join(col_vals), which for a
    #   1-tuple is just the bare col-value.
    if n_measures > 1:
        leaf_cols = [
            _pivot_leaf_key(ct, m) for ct in col_tuples for m in effective_values
        ]
    else:
        leaf_cols = [_pivot_leaf_key_no_measure(ct) for ct in col_tuples]

    # Bucket every row — total-tagged included — by its rows-dim tuple. Totals
    # get no special key: keying them like every other row keeps each distinct
    # total label its own wide row (so per-group subtotals over disjoint columns
    # render their own numbers instead of being fabricated into one row under a
    # first-seen label), while the collision guard still rejects a genuine
    # duplicate cell.
    row_map: dict[tuple[Any, ...], dict[str, Any]] = {}
    filled: dict[tuple[Any, ...], set[str]] = {}
    for row in data:
        row_key = tuple(row.get(k) for k in rows)
        if row_key not in row_map:
            row_map[row_key] = {k: row.get(k) for k in rows}
            if role_col is not None:
                row_map[row_key][role_col] = row.get(role_col)
            for leaf in leaf_cols:
                row_map[row_key][leaf] = None
            filled[row_key] = set()
        elif role_col is not None:
            stored_role = resolve_row_role(role_col, row_map[row_key])
            this_role = resolve_row_role(role_col, row)
            if this_role != stored_role:
                row_key_repr = ", ".join(f"{k}={row_map[row_key][k]!r}" for k in rows)
                raise ChartDataError(
                    f"pivot_table_data: conflicting row roles for row"
                    f" ({row_key_repr}): {stored_role!r} vs {this_role!r} — a"
                    f" merged pivot row must resolve to a single role."
                )
        col_tuple = tuple(str(row[c]) for c in columns)
        # Carry the bare measure name ("" for the single-measure arm), not a
        # pre-formatted fragment: the ", measure …" text is only ever consumed
        # by the duplicate-cell error below, so building it per cell would be a
        # throwaway allocation on the hot path (n_measures × rows per render).
        leaf_cells: list[tuple[str, str, Any]] = (
            [(_pivot_leaf_key(col_tuple, m), m, row.get(m)) for m in effective_values]
            if n_measures > 1
            else [
                (
                    _pivot_leaf_key_no_measure(col_tuple),
                    "",
                    row.get(effective_values[0]),
                )
            ]
        )
        for leaf, measure_name, cell_value in leaf_cells:
            if leaf in filled[row_key]:
                row_key_repr = ", ".join(f"{k}={row_map[row_key][k]!r}" for k in rows)
                measure_repr = f", measure {measure_name!r}" if measure_name else ""
                raise ChartDataError(
                    f"pivot_table_data: duplicate value for row ({row_key_repr})"
                    f" and column {col_tuple!r}{measure_repr} — move the extra"
                    f" field onto rows or columns."
                )
            row_map[row_key][leaf] = cell_value
            filled[row_key].add(leaf)

    # Wide rows come out in first-seen (query) order — total-role rows included.
    # Render never reorders rows (chart AGENTS.md #3: data belongs to queries,
    # wrong ordering is fixed in the query). A query that wants a bottom total
    # row orders it last — see the ORDER BY in the docs example. Leaf-column
    # order is different: that axis is synthesized by this reshape and carries
    # #3's pivot carve-out — see _order_col_tuples.
    wide_rows = list(row_map.values())

    if single_dim and n_measures == 1:
        # Leaf keys are bare col-values; no group descriptor needed.
        return wide_rows, None, effective_values

    levels = _build_pivot_levels(col_tuples, columns, n_measures)
    return wide_rows, levels, effective_values
