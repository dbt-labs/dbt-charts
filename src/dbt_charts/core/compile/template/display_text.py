"""Display text: board titles and chart text, resolved the way board text is.

Stage: COMPILE (pure; the caller supplies variables and query results)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from dbt_charts.core.compile.format import resolve_format
from dbt_charts.core.compile.format_value import (
    format_table_cell_value,
    is_temporal_value,
)
from dbt_charts.core.compile.models.primitives import FormatConfig, FormatTable
from dbt_charts.core.compile.template.jinja import resolve_jinja_template
from dbt_charts.core.compile.template.markdown_fence import resolve_outside_fences
from dbt_charts.core.compile.template.query_result import QueryResult
from dbt_charts.core.diagnostics import ERR_TEXT_FORMAT_USAGE
from dbt_charts.core.diagnostics.chart_data import ChartDataError

_TemplateArg = Any  # type-state: explicit_any — filter args are template values


@dataclass(frozen=True)
class DisplayTextInputs:
    variables: Mapping[str, Any]  # type-state: explicit_any — board variable values
    query_results: Mapping[str, QueryResult]


ChartDisplayText = DisplayTextInputs | Literal["raw"] | None


def _is_formattable(value: _TemplateArg) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float, Decimal, date))


def _is_number_text(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _is_format_spec(spec: str, formats: FormatTable | None) -> bool:
    from d3_format import parse as d3_parse
    from d3_format.errors import D3FormatError
    from dbt_charts.core.text.format_d3 import (
        find_unsupported_directive,
        is_time_format,
    )
    from dbt_charts.core.text.predefined_formats import ALL_PREDEFINED_NAMES

    if formats is not None and spec in formats:
        return True
    resolved = resolve_format(spec, formats)
    if not resolved:
        return False
    if resolved in ALL_PREDEFINED_NAMES:
        return True
    if is_time_format(resolved):
        return find_unsupported_directive(resolved) is None
    try:
        d3_parse(resolved)
    except D3FormatError:
        return False
    return True


def _text_format_filter(formats: FormatTable | None) -> Callable[..., str]:
    def format_cell(
        value: _TemplateArg, spec: _TemplateArg = None, *extra: _TemplateArg
    ) -> str:
        if spec is None:
            problem = "no spec"
        elif extra:
            problem = f"{len(extra)} extra argument(s)"
        elif not isinstance(spec, str) or not _is_format_spec(spec, formats):
            problem = f"spec {spec!r}"
        elif isinstance(value, str) and not (
            is_temporal_value(value) or _is_number_text(value)
        ):
            problem = f"value {value!r} is not a number or date"
        elif not isinstance(value, str) and not _is_formattable(value):
            problem = f"value of type '{type(value).__name__}' is not a number or date"
        else:
            return format_table_cell_value(value, _narrative(spec), formats)
        raise ChartDataError.from_code(ERR_TEXT_FORMAT_USAGE, detail=problem)

    def _narrative(spec: str) -> str | FormatConfig:
        # Prose reads "$4.2mn", not the axis-style "$4.2 M"; an alias that sets
        # its own notation keeps it.
        alias = formats.aliases.get(spec) if formats is not None else None
        if isinstance(alias, FormatConfig) and alias.notation is not None:
            return spec
        return FormatConfig(spec=spec, notation="narrative")

    return format_cell


def resolve_display_text(
    text: str,
    variables: Mapping[str, Any],  # type-state: explicit_any — board variable values
    query_results: Mapping[str, QueryResult],
    formats: FormatTable | None,
) -> str:
    """Resolve display-text Jinja leniently; fenced code passes through."""
    format_cell = _text_format_filter(formats)
    return resolve_outside_fences(
        text,
        lambda masked: resolve_jinja_template(
            masked,
            variables,
            strict=False,
            query_results=query_results,
            format_cell=format_cell,
        ),
    )
