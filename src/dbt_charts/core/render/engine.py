"""Seam to the in-process Rust render engine (`dbt_charts._engine`).

`build_chart_payload` is the one definition of the engine's chart payload.
`render_native` renders one chart with it and translates the engine's
`{code, params}` diagnostics into what the rest of the pipeline already
consumes: a raised `ChartDataError` for an error, and records in the same
warning sinks the Python renderers write for a warning. An engine refusal
(`ERR-ENGINE-REFUSED`) is an engine gap and raises like any other error.
"""

from __future__ import annotations

import datetime
import decimal
import json
import math
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Literal

from dbt_charts import _engine
from dbt_charts.core.compile.format import (
    format_fields,
    sub_unit_spec,
)
from dbt_charts.core.compile.image_assets import rewrite_markdown_text_images
from dbt_charts.core.compile.models.board.normalized import (
    NO_TITLE_SHIFT,
    TitleShift,
    VariableValues,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedCalloutChart,
    ResolvedChart,
    ResolvedCustomChart,
    ResolvedKpiChart,
    ResolvedSparkBarChart,
    ResolvedTableChart,
)
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.models.style.resolved.callout import ResolvedCalloutStyle
from dbt_charts.core.compile.resolve.style.markdown_style import build_mdsvg_style
from dbt_charts.core.compile.template.output_budget import (
    TemplateOutputBudgetExceeded,
    draw_down_template_output,
    remaining_template_output_bytes,
)
from dbt_charts.core.constants.estimates import (
    SPARK_BAR_TITLE_BASELINE_Y_PX,
    SPARK_BAR_TITLE_HEIGHT_PX,
)
from dbt_charts.core.constants.layout import SUPPORT_TABLE_COLUMN_BLOCK_SHARE_WARN_RATIO
from dbt_charts.core.diagnostics import REGISTRY, Diagnostic, display_message
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_CUSTOM_RENDER
from dbt_charts.core.diagnostics.registry import ErrorCode, build_doc_url
from dbt_charts.core.fonts import get_fonts_dir
from dbt_charts.core.render.board_links import get_link_context, resolve_href
from dbt_charts.core.render.board_variables import (
    current_board_variables,
    current_root_board_width,
)
from dbt_charts.core.render.chart.auto_link import get_filter_variables_context
from dbt_charts.core.render.chart.axis_label_collision import (
    AxisLabelCollision,
    record_axis_label_collision,
)
from dbt_charts.core.render.chart.endpoint_label_overflow import (
    EndpointLabelGapOverflow,
    record_endpoint_label_gap_overflow,
)
from dbt_charts.core.render.chart.pie_total_overflow import (
    PieTotalOverflow,
    record_pie_total_overflow,
)
from dbt_charts.core.render.chart.plot_width_floor_record import (
    record_plot_width_share_warning,
)
from dbt_charts.core.render.chart.series_label_truncation import (
    record_series_label_truncations,
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
    StaticPagination,
    record_static_pagination,
)
from dbt_charts.core.render.chart.text_truncation import (
    TruncationSurface,
    record_text_truncation,
)
from dbt_charts.core.render.chart.x_domain_paint_order import (
    XDomainPaintOrder,
    record_x_domain_paint_order,
)
from dbt_charts.core.render.controls import controls_are_interactive
from dbt_charts.core.render.sizing import TABLE_PAGINATION_CONTROL_HEIGHT
from dbt_charts.core.render.svg_utils import css_color
from dbt_charts.core.text.format_d3 import (
    HouseLocale,
    TimeLocaleDefinition,
    active_format_locale,
    active_time_locale,
    locale_definition,
)
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_SPECS,
    PREDEFINED_SUB_UNIT_FALLBACK,
)
from dbt_charts.core.utils import CellValue, Rows

JsonDict = dict[str, Any]  # type-state: explicit_any — JSON payload and engine params


def json_default(obj: CellValue) -> str | float:
    """JSON hook for query cells: ISO dates, numeric Decimals as numbers (a
    str()'d Decimal would reach the engine as a string cell and flip its
    numeric-type verdicts), str() for anything else."""
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    return str(obj)


_JsonValue = Any  # type-state: explicit_any — a query cell or a JSON container of them


def _encode_cells(value: _JsonValue) -> _JsonValue:
    if isinstance(value, float | decimal.Decimal):
        if not math.isfinite(value):
            kind = "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
            return {"non_finite": kind, "text": str(value)}
        if isinstance(value, decimal.Decimal):
            return {"decimal": str(value)}
    if isinstance(value, dict):
        return {k: _encode_cells(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_encode_cells(v) for v in value]
    return value


def _cell_kind(cell: CellValue) -> str:
    # bool before int (a subclass), datetime before date (a subclass). Anything
    # json_default stringifies (UUID, time, bytes, ...) travels as a string cell.
    if isinstance(cell, bool):
        return "boolean"
    if isinstance(cell, int):
        return "integer"
    if isinstance(cell, float):
        return "float"
    if isinstance(cell, decimal.Decimal):
        return "decimal"
    if isinstance(cell, datetime.datetime):
        return "datetime"
    if isinstance(cell, datetime.date):
        return "date"
    if isinstance(cell, dict | list | tuple):
        return "nested"
    return "string"


def column_types(rows: Rows) -> dict[str, str]:
    """Each column's type for the engine, which never infers it from the JSON
    cells (a date and a string both arrive as strings): one of `string`,
    `integer`, `float`, `decimal`, `boolean`, `date`, `datetime`, `nested`,
    `null` for a column with no non-null cell, or `mixed` for a column whose
    cells are of several kinds (a VALUES query can hold `1`, `1.0` and `true`).

    Integers and floats in one column widen to `float`. `mixed` is reported,
    not resolved: the engine errors if a family needs the type of such a column.
    """
    kinds: dict[str, set[str]] = {}
    for row in rows:
        for name, cell in row.items():
            seen = kinds.setdefault(name, set())
            if cell is not None:
                seen.add(_cell_kind(cell))
    types: dict[str, str] = {}
    for name, seen in kinds.items():
        if seen == {"integer", "float"}:
            seen = {"float"}
        types[name] = "mixed" if len(seen) > 1 else next(iter(seen), "null")
    return types


def dumps_payload(payload: JsonDict) -> str:
    """The payload as JSON text.

    JSON has no `NaN`/`Infinity` and no exact Decimal, so a query cell that is
    one is tagged: a finite Decimal as `{"decimal": str(value)}`, a non-finite
    float or Decimal as `{"non_finite": "nan" | "inf" | "-inf", "text":
    str(value)}`. The engine decodes both once, when it parses the payload;
    `text` labels a pivot dimension. Only `rows` and `datasets` hold query
    cells: a non-finite value anywhere else is a bug, and `allow_nan=False`
    raises on it.
    """
    cells = {k: _encode_cells(payload[k]) for k in ("rows", "datasets") if k in payload}
    return json.dumps({**payload, **cells}, default=json_default, allow_nan=False)


def board_formats(board_style: ResolvedStyle) -> JsonDict | None:
    """The board's `style.formats` aliases, for the engine to resolve a
    redefined format name (`date_short`, `number`, `currency`). An alias to a
    spec-kind name (`id`, `latitude`) or to another of the board's names
    (`number: currency` beside `currency: latitude`) ships as compile's own
    resolution of it: the engine knows no spec-kind name and follows one hop.
    `raw` ships only when it names a format the engine has its own rules for."""
    formats = board_style.chart_defaults.formats
    if formats is None:
        return None
    wire: JsonDict = {}
    for name, fmt in formats.aliases.items():
        spec = fmt.spec if isinstance(fmt, FormatConfig) else fmt
        chained = spec != name and spec in formats
        if chained or (spec in PREDEFINED_SPECS and PREDEFINED_SPECS[spec].kind):
            fields = format_fields(fmt, formats)
            raw = fields["raw"]
            if raw in PREDEFINED_SPECS and PREDEFINED_SPECS[raw].kind:
                del fields["raw"]
            wire[name] = {k: v for k, v in fields.items() if v is not None}
        else:
            wire[name] = (
                fmt
                if isinstance(fmt, str)
                else fmt.model_dump(mode="json", exclude_none=True)
            )
    return wire


def board_sub_unit_specs(board_style: ResolvedStyle) -> dict[str, str]:
    """Each adaptive format's plain-digit sibling, resolved against the
    board's formats (`sub_unit_spec`)."""
    return {
        raw: sub_unit_spec(raw, board_style.chart_defaults.formats)
        for raw in PREDEFINED_SUB_UNIT_FALLBACK
    }


def format_locale_wire(locale: HouseLocale) -> JsonDict:
    """A `regional.format_locale` as the engine's `NumberLocale` reads it: d3's
    locale definition plus the words its magnitudes print in each register."""
    return {
        **locale_definition(locale),
        "analytic": list(locale.analytic),
        "narrative": list(locale.narrative),
    }


def board_style_envelope(
    board_style: ResolvedStyle,
    board_width: float | None,
    format_locale: HouseLocale,
    time_locale: TimeLocaleDefinition,
) -> JsonDict:
    """The board-level style tokens every chart payload carries beyond its own
    style slice (KPI support-row tones, the pie attached-table view height, the
    placeholder overlay's board font) and
    the root board width WARN-TABLE-CRAMPED sizes its fix from. `None` is a
    render with no board; a table that needs the width then errors."""
    rule = board_style.vega_config.get("rule")
    return {
        "muted": board_style.muted,
        "tones": board_style.tones.model_dump(mode="json"),
        "title_case": board_style.title.font.case,
        "continuous_height": board_style.chart_defaults.view.continuous_height,
        "rule_stroke_width": None if rule is None else rule.get("strokeWidth"),
        "font": board_style.vega_config.get("font"),
        "font_color": board_style.font.color,
        "board_width": board_width,
        "text_mark_font": {
            "family": board_style.chart_defaults.marks.text.font.family,
            "size": board_style.chart_defaults.marks.text.font.size,
        },
        "pagination_control_height": TABLE_PAGINATION_CONTROL_HEIGHT,
        "column_block_share_warn_ratio": SUPPORT_TABLE_COLUMN_BLOCK_SHARE_WARN_RATIO,
        "spark_bar_title": {
            "height": SPARK_BAR_TITLE_HEIGHT_PX,
            "baseline_y": SPARK_BAR_TITLE_BASELINE_Y_PX,
        },
        "placeholder": {
            "opacity": board_style.placeholder.opacity,
            "overlay": {
                "text": board_style.placeholder.overlay.text,
                "background": board_style.placeholder.overlay.background,
                "font": {
                    "family": board_style.font.family,
                    "size": board_style.placeholder.overlay.font.size,
                    "weight": board_style.placeholder.overlay.font.weight,
                    "color": board_style.placeholder.overlay.font.color,
                },
            },
        },
        "sub_unit_specs": board_sub_unit_specs(board_style),
        "formats": board_formats(board_style),
        "format_locale": format_locale_wire(format_locale),
        "time_locale": time_locale,
    }


def check_callout_css(style: ResolvedCalloutStyle) -> None:
    """Reject a CSS-breaking style value the engine would write into its CSS.

    The engine builds one mdsvg `Style` per callout block from these values;
    `build_mdsvg_style` is the guard that raises `ERR-STYLE-CSS-VALUE-INVALID`
    for them, so it runs on the same inputs here.
    """
    for font in (style.title.font, style.message.font):
        weight = int(font.weight) if isinstance(font.weight, float) else font.weight
        build_mdsvg_style(
            font_family=font.family,
            base_font_size=font.size,
            font_weight=None if str(weight).lower() in {"400", "normal"} else weight,
            bold_font_weight=style.bold_font_weight,
            text_color=font.color,
            heading_color=font.color,
            link_color=style.title.font.color,
            code_color=font.color,
        )


def check_table_link_colors(chart: ResolvedTableChart) -> None:
    """Reject a linked column's `font.color` that is not exactly one CSS color.

    It becomes a live `style="color: ..."` declaration on the cell's anchor;
    `css_color` is the one validation law (`ERR-CSS-COLOR-INVALID-AT-RENDER`).
    """
    if chart.columns is None:
        return
    for column in chart.columns.values():
        if column.link and column.font and column.font.color:
            css_color(column.font.color)


def build_chart_payload(
    chart: ResolvedChart,
    datasets: dict[str | None, Rows],
    *,
    width: float,
    height: float,
    padding: dict[str, float] | None,
    board_style: ResolvedStyle,
    board_width: float | None,
    variables: VariableValues,
    title_shift: TitleShift,
    is_placeholder: bool,
) -> JsonDict:
    """The engine payload for one chart.

    `datasets` is `layout_sizing.build_chart_datasets`'s full result, forwarded
    unreshaped: `rows` is `datasets[chart.query_name]` and a layer with its own
    `query_name` reads its rows from the map. `variables` are the board's
    variable values (a table reads `{chart_id}_page`).
    """
    link_context = get_link_context()
    resolved = chart.model_dump(mode="json")
    if isinstance(chart, ResolvedKpiChart) and chart.link:
        context = get_link_context()
        resolved["link"] = resolve_href(chart.link, context) if context else chart.link
    elif isinstance(chart, ResolvedSparkBarChart):
        # A spark_bar has never drawn a link, authored or auto-linked; the
        # engine refuses a family it does not link, so it never receives one.
        resolved["link"] = None
        # The bar's label prints `str(cell)`, and a datetime's differs from the
        # ISO form every other cell travels in.
        datasets = {
            name: [
                {
                    k: str(v) if isinstance(v, datetime.datetime) else v
                    for k, v in row.items()
                }
                for row in rows
            ]
            for name, rows in datasets.items()
        }
    if isinstance(chart, ResolvedTableChart):
        check_table_link_colors(chart)
    if isinstance(chart, ResolvedCalloutChart):
        check_callout_css(chart.style)
        # The engine reads no project files: it gets every image already resolved.
        resolved["message"] = rewrite_markdown_text_images(
            chart.message, chart.base_dir
        )
        if chart.title:
            resolved["title"] = rewrite_markdown_text_images(
                chart.title, chart.base_dir
            )
    return {
        "resolved": resolved,
        "rows": datasets[chart.query_name],
        "datasets": datasets,
        "column_types": {
            "rows": column_types(datasets[chart.query_name]),
            "datasets": {name: column_types(rows) for name, rows in datasets.items()},
        },
        "variables": variables,
        "title_shift": title_shift.model_dump(mode="json"),
        "filter_variables": sorted(get_filter_variables_context()),
        "interactive_controls": controls_are_interactive(),
        "link_context": None if link_context is None else asdict(link_context),
        "width": width,
        "height": height,
        "background": board_style.background,
        "fonts_dir": str(get_fonts_dir()),
        "padding": padding,
        "board_style": board_style_envelope(
            board_style, board_width, active_format_locale(), active_time_locale()
        ),
        "is_placeholder": is_placeholder,
        # A custom chart's template draws on the board's one output budget:
        # the engine gets what is left, and the render charges what it emits.
        "max_template_output_bytes": (
            remaining_template_output_bytes()
            if isinstance(chart, ResolvedCustomChart)
            else None
        ),
    }


def _chart_data_error(exc: _engine.RenderDiagnosticError) -> ChartDataError:
    registered = REGISTRY.get(exc.code)
    if not isinstance(registered, ErrorCode):
        raise TypeError(
            f"engine raised {exc.code}, which is not an error code"
        ) from exc
    return ChartDataError.from_code(registered, **exc.params)


def measure_table_natural_width(
    chart: ResolvedChart, rows: Rows, board_style: ResolvedStyle
) -> float | None:
    """The width a table needs to show every column (columns and outer
    padding), or `None` with nothing to hug. Raises `ChartDataError` for a
    table the engine cannot measure."""
    payload = {
        "resolved": chart.model_dump(mode="json"),
        "rows": rows,
        "column_types": {"rows": column_types(rows)},
        "fonts_dir": str(get_fonts_dir()),
        "format_locale": format_locale_wire(active_format_locale()),
        "time_locale": active_time_locale(),
        "sub_unit_specs": board_sub_unit_specs(board_style),
        "formats": board_formats(board_style),
    }
    try:
        return _engine.measure_table_natural_width(dumps_payload(payload))
    except _engine.RenderDiagnosticError as exc:
        raise _chart_data_error(exc) from exc


def table_title_geometry(
    chart: ResolvedChart, slot_width: float
) -> tuple[float, float]:
    """A table's title band as the engine lays it out: the first baseline's
    distance below the card's inset top, and the height from that baseline down
    to the header. Raises `ChartDataError` for a table the engine cannot lay
    out."""
    payload = {
        "resolved": chart.model_dump(mode="json"),
        "width": slot_width,
        "fonts_dir": str(get_fonts_dir()),
    }
    try:
        return _engine.table_title_geometry(dumps_payload(payload))
    except _engine.RenderDiagnosticError as exc:
        raise _chart_data_error(exc) from exc


def _truncations(
    surface: TruncationSurface, field: str, count: int, chart_id: str
) -> None:
    # The truncation detectors count records and read the field; the cut text
    # is not in the engine's params.
    for _ in range(count):
        record_text_truncation(chart_id, surface, "", field)


def _endpoint_rail_degraded(
    p: JsonDict,
    cause: Literal["gap_did_not_fit", "no_slope", "rail_overflow"],
    dropped_series: tuple[str, ...] = (),
) -> None:
    record_endpoint_label_gap_overflow(
        p["chart_id"],
        EndpointLabelGapOverflow(
            series_count=p["series_count"],
            gap_px=p["gap_px"],
            cause=cause,
            dropped_series=dropped_series,
        ),
    )


_WARNING_SINKS: dict[str, Callable[[JsonDict], None]] = {
    "WARN-AXIS-LABEL-COLLISION": lambda p: record_axis_label_collision(
        p["chart_id"],
        AxisLabelCollision(field=p["field"], label_count=p["label_count"]),
    ),
    "WARN-ENDPOINT-LABEL-GAP-OVERFLOW": lambda p: _endpoint_rail_degraded(
        p, "gap_did_not_fit"
    ),
    "WARN-ENDPOINT-LABEL-RAIL-OVERFLOW": lambda p: _endpoint_rail_degraded(
        p, "rail_overflow", tuple(p["dropped_series"])
    ),
    "WARN-ENDPOINT-LABEL-RAIL-TIED": lambda p: _endpoint_rail_degraded(p, "no_slope"),
    "WARN-AXIS-TITLE-TRUNCATED": lambda p: record_text_truncation(
        p["chart_id"], "axis_title", p["authored_text"], p["authored_field"]
    ),
    "WARN-CHART-TITLE-TRUNCATED": lambda p: record_text_truncation(
        p["chart_id"], "chart_title", p["authored_text"], p["authored_field"]
    ),
    "WARN-CALLOUT-TEXT-TRUNCATED": lambda p: record_text_truncation(
        p["chart_id"], "callout_text", p["authored_text"], p["authored_field"]
    ),
    "WARN-LAYER-X-DOMAIN-PAINT-ORDER": lambda p: record_x_domain_paint_order(
        p["chart_id"],
        XDomainPaintOrder(x_field=p["x_field"], layer_only=tuple(p["layer_only"])),
    ),
    "WARN-SUPPORT-TABLE-CELL-OVERFLOW": lambda p: record_text_truncation(
        p["chart_id"], "support_table_cell_overflow", p["detail"], "support_table"
    ),
    "WARN-PLOT-WIDTH-BELOW-MINIMUM": lambda p: record_plot_width_share_warning(
        p["chart_id"],
        column_block_width_px=p["block_width"],
        plot_width_px=p["plot_width"],
        card_width_px=p["card_width"],
    ),
    "WARN-SERIES-LABEL-TRUNCATED": lambda p: record_series_label_truncations(
        p["chart_id"], p["authored_field"], p["series_names"]
    ),
    "WARN-KPI-ALIGN-OVERFLOW": lambda p: record_text_truncation(
        p["chart_id"], "kpi_align_overflow", "", "style.align"
    ),
    "WARN-KPI-INLINE-VARIANT-FALLBACK-TO-STACKED": lambda p: record_text_truncation(
        p["chart_id"], "kpi_inline_fallback", p["authored_text"], "style.variant"
    ),
    "WARN-KPI-LABEL-TRUNCATED": lambda p: record_text_truncation(
        p["chart_id"], "kpi_label", p["authored_text"], "label"
    ),
    "WARN-KPI-SUPPORT-TRUNCATED": lambda p: record_text_truncation(
        p["chart_id"], "kpi_support", p["authored_text"], "support.label"
    ),
    "WARN-SPARK-LABEL-TRUNCATED": lambda p: _truncations(
        "spark_label", "y", p["truncation_count"], p["chart_id"]
    ),
    "WARN-TABLE-TEXT-TRUNCATED": lambda p: _truncations(
        "table_cell", p["authored_field"], p["truncation_count"], p["chart_id"]
    ),
    # Reported for every static multi-page table; only a dropped cap note is a
    # user warning (see the detector), the rest feed the text format's counts.
    "WARN-STATIC-PAGINATION-CAPPED": lambda p: record_static_pagination(
        p["chart_id"],
        StaticPagination(
            page_rows=p["page_rows"],
            total_rows=p["total_rows"],
            rendered_pages=p["rendered_pages"],
            total_pages=p["total_pages"],
            cap_note_dropped=p["cap_note_dropped"],
        ),
    ),
    "WARN-PIE-TOTAL-EXCEEDS-INNER-RADIUS": lambda p: record_pie_total_overflow(
        p["chart_id"],
        PieTotalOverflow(
            formatted_value=p["formatted_value"],
            text_width=p["text_width"],
            hole_diameter=p["hole_diameter"],
            slot_width=p["slot_width"],
            slot_height=p["slot_height"],
        ),
    ),
    "WARN-TABLE-COLUMNS-OVERFLOW": lambda p: record_table_overflow(
        p["chart_id"],
        TableOverflow(
            required_width=p["needed_width"], available_width=p["available_width"]
        ),
    ),
    "WARN-TABLE-CRAMPED": lambda p: record_table_cramping(
        p["chart_id"],
        TableCramping(
            required_width=p["needed_width"],
            available_width=p["available_width"],
            wrapped_headers=p["wrapped_headers"],
            column_count=p["column_count"],
            suggested_width=p["suggested_width"],
        ),
    ),
    "WARN-TABLE-PAGE-SQUEEZED": lambda p: record_table_page_squeeze(
        p["chart_id"],
        TablePageSqueeze(
            drawn_rows=p["drawn_rows"],
            page_rows=p["page_rows"],
            total_rows=p["total_rows"],
        ),
    ),
}


def render_native(
    chart: ResolvedChart,
    data: Rows,
    *,
    width: float,
    height: float,
    padding: dict[str, float] | None,
    board_style: ResolvedStyle,
    board_width: float | None = None,
    variables: VariableValues | None = None,
    title_shift: TitleShift = NO_TITLE_SHIFT,
    is_placeholder: bool = False,
    record_warnings: bool = True,
) -> tuple[str, float | None]:
    """Render one chart in the engine; return `(svg, natural_height)`.

    `variables` and `board_width` default to the board's current values
    (`board_variables`); outside a board scope there are none. A sizing probe
    passes `record_warnings=False`: its render is never shown, so its warnings
    are not the board's. Raises `ChartDataError` for any engine error,
    including an `ERR-ENGINE-REFUSED` gap.
    """
    if variables is None:
        scoped = current_board_variables()
        variables = {} if scoped is None else scoped
    if board_width is None:
        board_width = current_root_board_width()
    payload = build_chart_payload(
        chart,
        {chart.query_name: data},
        width=width,
        height=height,
        padding=padding,
        board_style=board_style,
        board_width=board_width,
        variables=variables,
        title_shift=title_shift,
        is_placeholder=is_placeholder,
    )
    authored = (
        {"title": chart.title, "message": chart.message}
        if isinstance(chart, ResolvedCalloutChart)
        else None
    )
    svg, natural_height = _render_payload(
        payload, authored, record_warnings=record_warnings
    )
    if isinstance(chart, ResolvedCustomChart):
        # The engine refused the template's own output past the budget; the
        # title, card and link framing it adds can still cross it here.
        try:
            draw_down_template_output(len(svg.encode("utf-8")))
        except TemplateOutputBudgetExceeded as exc:
            raise ChartDataError.from_code(
                ERR_CUSTOM_RENDER, chart_id=chart.id, reason=str(exc)
            ) from exc
    return svg, natural_height


def _render_payload(
    payload: JsonDict,
    authored: dict[str, str | None] | None = None,
    *,
    record_warnings: bool = True,
) -> tuple[str, float | None]:
    """Render `payload`; `authored` maps a callout field to its text as written,
    which a truncation warning reports in place of the resolved image URLs."""
    try:
        svg, warnings, natural_height = _engine.render_chart(dumps_payload(payload))
    except _engine.RenderDiagnosticError as exc:
        raise _chart_data_error(exc) from exc
    for warning in warnings:
        if warning["code"] not in _WARNING_SINKS:
            raise ValueError(f"engine emitted unmapped warning {warning['code']}")
        if not record_warnings:
            continue
        params = warning["params"]
        if authored is not None and "authored_field" in params:
            params = {**params, "authored_text": authored[params["authored_field"]]}
        _WARNING_SINKS[warning["code"]](params)
    return svg, natural_height


def render_callout_card(
    *,
    chart_id: str,
    title: str | None = None,
    message: str,
    style: ResolvedCalloutStyle,
    width: float,
    height: float = 0.0,
    placard: JsonDict | None = None,
) -> str:
    """A callout card with no chart behind it: the engine draws `message` (and
    `title`) as markdown, or as plain text when `placard` marks it an error tile.
    `height` 0 draws at the card's natural height."""
    check_callout_css(style)
    svg, _ = _render_payload(
        {
            "resolved": {
                "chart_type": "callout",
                "id": chart_id,
                "title": title,
                "message": message,
                "style": style.model_dump(mode="json"),
                "placard": placard,
            },
            "rows": [],
            "column_types": {"rows": {}},
            "width": width,
            "height": height,
            "background": None,
            "fonts_dir": str(get_fonts_dir()),
            "padding": None,
        }
    )
    return svg


def render_error_placard(
    diagnostic: Diagnostic,
    *,
    chart_id: str,
    width: float,
    height: float,
    style: ResolvedCalloutStyle,
) -> str:
    """The tile core paints into the slot of every chart that failed."""
    return render_callout_card(
        chart_id=chart_id,
        title=REGISTRY.get(diagnostic.code).title,
        message=display_message(diagnostic),
        style=style,
        width=width,
        height=height,
        placard={
            "code": diagnostic.code,
            "code_url": build_doc_url(diagnostic.code),
            "hint": diagnostic.hint,
            "doc_url": REGISTRY.get(diagnostic.code).doc_url,
        },
    )
