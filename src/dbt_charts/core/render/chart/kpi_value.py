"""A KPI value as text, for the terminal and text renderers.

The SVG card is drawn by the engine; these format the value cell the way the
card's formatter does (`format_kpi_parts`).
"""

from __future__ import annotations

from dbt_charts.core.compile.format import column_format
from dbt_charts.core.compile.format_value import (
    format_kpi_parts,
    format_temporal_value,
    is_temporal_value,
)
from dbt_charts.core.compile.models.primitives import (
    FormatConfig,
    FormatTable,
    ResolvedFormat,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_KPI_FORMAT_KIND_MISMATCH,
    ERR_KPI_TEMPORAL_FORMAT_INVALID,
    ERR_KPI_VALUE_COLUMN_NOT_FOUND,
)
from dbt_charts.core.text.format_d3 import is_time_format
from dbt_charts.core.utils import CellValue, coerce_numeric_cell


def resolve_kpi_value(raw: str, row: dict[str, CellValue], chart_id: str) -> CellValue:
    """The KPI/support cell named by column ``raw``.

    Raises ``ChartDataError`` when the column is not in the query result row.
    """
    if raw in row:
        return row[raw]
    raise ChartDataError.from_code(
        ERR_KPI_VALUE_COLUMN_NOT_FOUND,
        chart_id=chart_id,
        value_column=raw,
    )


def kpi_value_text(
    cell: CellValue,
    format_input: ResolvedFormat | None,
    chart_id: str,
    formats: FormatTable | None,
    *,
    native: bool = False,
    format_may_be_cascaded: bool = False,
) -> str:
    """A KPI value as one flat string.

    A temporal cell prints through a strftime spec (``date_short``, or a
    ``style.formats`` alias resolving to one). ``format_may_be_cascaded`` is True
    only for the headline value, whose format can be a board-wide default shared
    by every KPI: a non-strftime spec there falls back to ``date_short``, where
    on the support value it raises. A broken strftime directive raises either
    way. A numeric cell prints through ``format_kpi_parts``; any other cell
    prints as plain text (status-style KPIs like ``"At risk"``).
    """
    if is_temporal_value(cell):
        # Re-resolved as authored: a name the resolve-time table could not see (a
        # render-time alias) resolves here.
        authored = (
            FormatConfig(
                spec=format_input.spec or None,
                prefix=format_input.prefix or None,
                suffix=format_input.suffix or None,
                notation=format_input.notation,
            )
            if isinstance(format_input, ResolvedFormat)
            else format_input
        )
        dated = column_format("time", formats, authored)
        if not is_time_format(dated.spec):
            if not format_may_be_cascaded:
                raise ChartDataError.from_code(
                    ERR_KPI_FORMAT_KIND_MISMATCH, chart_id=chart_id, spec=dated.spec
                )
            dated = column_format("time", formats)
        try:
            date_text = format_temporal_value(cell, dated.spec)
        except ValueError as e:
            raise ChartDataError.from_code(
                ERR_KPI_TEMPORAL_FORMAT_INVALID,
                chart_id=chart_id,
                cell=cell,
                spec=dated.spec,
                reason=str(e),
            ) from e
        return f"{dated.prefix}{date_text}{dated.suffix}"

    numeric = coerce_numeric_cell(cell)
    if numeric is None:
        return "" if cell is None else str(cell)
    prefix, number_str, suffix = format_kpi_parts(
        numeric,
        format_input,
        formats,
        native=native,
        keep_affix_gaps=True,
        sign_in_prefix=True,
    )
    return f"{prefix}{number_str}{suffix}"
