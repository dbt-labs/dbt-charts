"""Arrow → QueryResult. The only Arrow-to-rows conversion in the codebase.

Stage: EXECUTE

Every driver on the Arrow seam returns a ``pyarrow.Table`` and hands it here, so
the type policy is written once instead of once per warehouse. The policy reads
the Arrow type and nothing else: a board's number format is a chart-layer
concern, and letting it reach the data layer would make one warehouse answer
differently depending on which chart asked.

Decimals are narrowed here with ``safe=True``: a whole number that does not fit
raises rather than wrapping into a plausible wrong value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.compute as pc

from dbt_charts.core.diagnostics.codes_execute import ERR_RESULT_NUMERIC_OVERFLOW
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.base import (
    QueryResult,
    RowFetchLimit,
    apply_row_limit_truncation,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from dbt_charts.core.execute.adapters.base import ResolvedRelation


def read_bounded_table(
    batches: Iterable[pa.RecordBatch], schema: pa.Schema, limit: RowFetchLimit
) -> pa.Table:
    """Read *batches* until ``limit.fetch_limit`` rows are in hand.

    Every driver bounds its fetch by reading the Arrow stream rather than by
    rewriting the SQL, so an author's own LIMIT, a SHOW and a DESCRIBE all
    behave exactly as they would without a ceiling. Batches are large, so the
    bound is coarse; what it buys is not fetching a million rows to return a
    thousand.

    *schema* is passed rather than taken from the first batch because a query
    that returns no rows returns no batches either, and the result still has
    to carry its columns.
    """
    collected: list[pa.RecordBatch] = []
    fetched = 0
    for batch in batches:
        collected.append(batch)
        fetched += batch.num_rows
        if fetched >= limit.fetch_limit:
            break
    return pa.Table.from_batches(collected, schema)


def arrow_to_query_result(
    table: pa.Table,
    limit: RowFetchLimit,
    resolved_relations: list[ResolvedRelation] | None = None,
) -> QueryResult:
    """Convert a driver's Arrow table into the engine's row-dict result.

    Column names keep the case the query gave them, as the in-process DuckDB
    adapter does.

    ``column_descriptions`` is deliberately left unset. Arrow carries a schema,
    not PEP 249 ``cursor.description`` tuples, and nothing downstream reads them
    off a warehouse result — filling ``display_size`` / ``precision`` / ``null_ok``
    from an Arrow field would invent values rather than report them.

    Args:
        table: The driver's result, already bounded to ``limit.fetch_limit`` rows
            (drivers stop reading batches once they have that many).
        limit: The resolved row limit the driver fetched against. Truncation is
            flagged only when the ``execution.max_rows`` ceiling — not the
            author's own ``Query.limit`` — was the binding value.
        resolved_relations: dbt refs/sources resolved while preparing the SQL.

    Raises:
        ExecutionError: ERR-RESULT-NUMERIC-OVERFLOW when a scale-0 decimal
            column holds a value wider than a 64-bit integer.
    """
    narrowed = _narrow_decimals(table)
    rows, truncated_reason = apply_row_limit_truncation(narrowed.to_pylist(), limit)
    return QueryResult(
        data=rows,
        columns=table.column_names,
        resolved_relations=resolved_relations,
        truncated_reason=truncated_reason,
    )


def _narrow_decimals(table: pa.Table) -> pa.Table:
    """Cast every decimal column to the Python number it is actually holding.

    Scale 0 is a whole number, so it becomes ``int64``; anything scaled becomes
    ``float64``, the same Decimal→float decision the agate path made implicitly,
    now stated. ``safe=True`` throughout: the integer cast is the one that can
    lose information, and it must fail loudly when it would.
    """
    for index, field in enumerate(table.schema):
        if not (pa.types.is_decimal(field.type)):
            continue
        target = pa.int64() if field.type.scale == 0 else pa.float64()
        try:
            narrowed = pc.cast(
                table.column(index), target, safe=True
            )  # type-state: cast — pyarrow.compute.cast converts values, not types: it is the opposite of laundering a type past the checker, and safe=True is what makes it raise rather than wrap
        except pa.ArrowInvalid as error:
            raise ExecutionError.from_code(
                ERR_RESULT_NUMERIC_OVERFLOW, column=field.name
            ) from error
        table = table.set_column(index, field.with_type(target), narrowed)
    return table
