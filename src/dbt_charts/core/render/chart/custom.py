"""Custom chart SVG rendering: a sandboxed Jinja template draws, the platform frames.

The template emits SVG *content*. This module renders it with the chart's rows,
validates it against the SVG allowlist, and wraps it in a root ``<svg>`` under
a title drawn with the Vega-Lite title geometry.
"""

from __future__ import annotations

import html as html_module
import math
import re
from collections.abc import Callable
from typing import Any

from pydantic import JsonValue

from dbt_charts.core.compile.models.board.normalized import (
    NO_TITLE_SHIFT,
    TitleShift,
)
from dbt_charts.core.compile.models.chart.resolved import ResolvedCustomChart
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.template.custom_chart_env import (
    MATH_NAMESPACE,
    CustomTemplateAbort,
    custom_chart_env,
)
from dbt_charts.core.compile.template.output_budget import (
    TemplateOutputBudgetExceeded,
    render_with_budget,
)
from dbt_charts.core.diagnostics.codes_render import (
    ERR_CUSTOM_RENDER,
    ERR_CUSTOM_SVG,
)
from dbt_charts.core.font_measure import (
    css_weight_to_axis,
    get_weighted_font_measurer,
)
from dbt_charts.core.render.chart.spec_builders import (
    VEGA_SUBTITLE_PADDING,
    VEGA_TITLE_OFFSET,
)
from dbt_charts.core.render.chart.svg_allowlist import (
    SvgViolation,
    checked_svg_content,
)
from dbt_charts.core.render.chart.table import title_ascent
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.svg_utils import authored_kind_attr, escape_attr
from dbt_charts.core.text.case import apply_case


def title_block_metrics(chart: ResolvedCustomChart) -> tuple[float, float]:
    """(top to first baseline, first baseline to body top), Vega-Lite title geometry.

    Both are zero for an untitled chart.
    """
    if not chart.title:
        return 0.0, 0.0
    size = float(chart.style.title_font.size)
    ascent = title_ascent(size)
    subtitle = 0.0
    if chart.subtitle:
        subtitle_size = chart.title_style.subtitle.font.size
        assert subtitle_size is not None, "the cascade fills the subtitle font size"
        subtitle = float(subtitle_size) + VEGA_SUBTITLE_PADDING
    offset = chart.title_style.position.offset
    body_top = size + subtitle + (VEGA_TITLE_OFFSET if offset is None else offset)
    return ascent, body_top - ascent


def _title_svg(chart: ResolvedCustomChart, title_dy: float) -> str:
    font = chart.style.title_font
    ascent, _ = title_block_metrics(chart)
    title = chart.title
    assert title, "only a titled chart draws a title"
    if font.case != "none":
        title = apply_case(title, font.case)
    parts = [
        f'<text x="0" y="{escape_attr(ascent + title_dy)}" '
        f'font-size="{escape_attr(font.size)}" font-weight="{escape_attr(font.weight)}" '
        f'fill="{escape_attr(font.color)}" font-family="{escape_attr(font.family)}"'
        f"{authored_kind_attr('title')}>{html_module.escape(title)}</text>"
    ]
    if chart.subtitle:
        sub = chart.title_style.subtitle.font
        assert sub.size is not None and sub.color is not None
        assert sub.family is not None
        y = title_dy + font.size + VEGA_SUBTITLE_PADDING + title_ascent(sub.size)
        parts.append(
            f'<text x="0" y="{escape_attr(y)}" font-size="{escape_attr(sub.size)}" '
            f'fill="{escape_attr(sub.color)}" font-family="{escape_attr(sub.family)}"'
            f"{authored_kind_attr('subtitle')}>{html_module.escape(chart.subtitle)}</text>"
        )
    return "".join(parts)


def _is_number(value: Any) -> bool:  # type-state: explicit_any — any template value
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _font_weight(
    weight: Any,  # type-state: explicit_any — any template value
) -> int:
    """A whole font weight from any value the style tree carries: 400, "600", "bold"."""
    if isinstance(weight, str) or _is_number(weight):
        try:
            axis = css_weight_to_axis(weight)
        except (ValueError, OverflowError):
            pass
        else:
            if axis.is_integer() and 1 <= axis <= 1000:
                return int(axis)
    raise CustomTemplateAbort(
        "text_width(): weight must be a whole number from 1 to 1000 like 400, "
        f"or a CSS keyword like bold, got {weight!r}"
    )


def _color_for(chart: ResolvedCustomChart) -> Callable[..., str]:
    assigned: dict[str, str] = {}

    def color_for(
        value: Any,  # type-state: explicit_any — a template passes any row value
        column: Any = None,  # type-state: explicit_any — validated below
    ) -> str:
        if column is not None and not isinstance(column, str):
            raise CustomTemplateAbort(
                f"color_for(): column must be a string or none, got {column!r}"
            )
        key = str(value)
        if column is not None:
            pins = chart.category_pins.get(column)
            if pins is not None and key in pins:
                return pins[key]
        if key not in assigned:
            if len(assigned) == len(chart.palette):
                raise CustomTemplateAbort(
                    f"color_for ran out of palette colors ({len(chart.palette)}) "
                    f"at value {key!r}; pin the extra values under "
                    "style.charts.category_colors"
                )
            assigned[key] = chart.palette[len(assigned)]
        return assigned[key]

    return color_for


def _check_columns(chart: ResolvedCustomChart, columns: list[str]) -> None:
    """Channel and column-option values must name returned columns.

    No rows means no columns to check against, and nothing to draw.
    """
    if not columns:
        return
    named: dict[str, str | list[str] | JsonValue] = {
        f"channel {name!r}": value for name, value in chart.channels.items()
    } | {f"option {name!r}": chart.options[name] for name in chart.column_options}
    for label, value in named.items():
        if value is None:
            continue
        wanted = [value] if isinstance(value, str) else value
        assert isinstance(wanted, list)
        missing = [column for column in wanted if column not in columns]
        if missing:
            raise _error(
                chart,
                f"{label} names column {missing[0]!r}, but the query "
                f"returned {columns}",
            )


def _error(chart: ResolvedCustomChart, reason: str) -> RenderError:
    return RenderError.from_code(ERR_CUSTOM_RENDER, chart_id=chart.id, reason=reason)


def _template_line(exc: BaseException) -> int | None:
    """Template line of the innermost jinja frame in ``exc``'s traceback."""
    line = None
    tb = exc.__traceback__
    while tb is not None:
        if "__jinja_template__" in tb.tb_frame.f_globals:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def _render_template(
    chart: ResolvedCustomChart,
    rows: list[dict[str, Any]],  # type-state: explicit_any — DB rows
    width: float,
    height: float,
    board_style: ResolvedStyle,
) -> str:
    columns = list(rows[0]) if rows else []
    _check_columns(chart, columns)
    font_family = board_style.font.family

    def text_width(
        text: Any,  # type-state: explicit_any — a template passes any value
        size: Any,  # type-state: explicit_any — validated below
        weight: Any = 400,  # type-state: explicit_any — validated below
    ) -> float:
        if not isinstance(text, str):
            raise CustomTemplateAbort(
                f"text_width(): text must be a string, got {text!r}"
            )
        if not _is_number(size) or not math.isfinite(size) or size <= 0:
            raise CustomTemplateAbort(
                f"text_width(): size must be a positive number, got {size!r}"
            )
        return get_weighted_font_measurer(font_family, _font_weight(weight)).measure(
            text, float(size)
        )

    def raise_error(message: str) -> None:
        raise CustomTemplateAbort(message)

    context = {
        "rows": rows,
        "columns": columns,
        "channels": chart.channels,
        "options": chart.options,
        "width": width,
        "height": height,
        "uid": "custom-" + re.sub(r"[^A-Za-z0-9_-]", "-", chart.id),
        "style": chart.style.tree,
        "math": MATH_NAMESPACE,
        "text_width": text_width,
        "color_for": _color_for(chart),
        "raise_error": raise_error,
    }
    try:
        template = custom_chart_env().from_string(chart.template_source)
        return render_with_budget(template, context)
    except TemplateOutputBudgetExceeded as exc:
        raise _error(chart, str(exc)) from exc
    except CustomTemplateAbort as exc:
        raise _error(chart, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — a template is user code; whatever it raises is that chart's error
        line = _template_line(exc)
        where = f"template {chart.template_name}" + (f", line {line}" if line else "")
        raise _error(chart, f"{type(exc).__name__}: {exc} ({where})") from exc


def render_custom_svg(
    chart: ResolvedCustomChart,
    data: list[dict[str, Any]],  # type-state: explicit_any — DB rows
    width: float,
    height: float,
    *,
    board_style: ResolvedStyle,
    title_shift: TitleShift = NO_TITLE_SHIFT,
) -> str:
    """Render a ``ResolvedCustomChart`` as a root ``<svg>`` of exactly ``width × height``."""
    ascent, tail = title_block_metrics(chart)
    body_top = ascent + tail + title_shift.body_dy if chart.title else 0.0
    body_height = max(height - body_top, 0.0)
    content = _render_template(chart, data, width, body_height, board_style)
    try:
        content = checked_svg_content(content)
    except SvgViolation as exc:
        raise RenderError.from_code(
            ERR_CUSTOM_SVG, chart_id=chart.id, reason=str(exc)
        ) from exc
    title = _title_svg(chart, title_shift.title_dy) if chart.title else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{escape_attr(width)}" '
        f'height="{escape_attr(height)}" '
        f'viewBox="0 0 {escape_attr(width)} {escape_attr(height)}">{title}'
        f'<g transform="translate(0, {escape_attr(body_top)})">{content}</g></svg>'
    )


__all__ = ["render_custom_svg", "title_block_metrics"]
