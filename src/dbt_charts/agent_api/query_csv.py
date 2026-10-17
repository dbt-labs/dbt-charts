"""The CSV a query result is read as, cut to one byte budget.

The MCP tool handler and ``dct query`` both call :func:`with_csv`, and every
transport serializes with :func:`model_json`, so the budget is measured with the
serializer that emits.
"""

from __future__ import annotations

import json
from typing import Any

from dbt_charts.core.text.row_table import rows_csv_lines

# Claude Code rejects an MCP tool result above 25,000 tokens, and `dct query`
# output lands in a shell agent's context under the same limit. Tabular text
# measures about 1.5 bytes per token with the Anthropic counter, and core takes
# no tokenizer dependency, so the budget is 25,000 x 1.5 UTF-8 bytes of the
# serialized result.
MODEL_RESULT_BYTE_BUDGET = 37_500

SQL_QUERY_HINT = (
    "Narrow the query instead of repeating it: select fewer columns, filter, "
    "aggregate, or page with LIMIT and OFFSET."
)
BOARD_QUERY_HINT = (
    "The SQL is the board author's: use the board's variables to filter, or run "
    "a narrower query with the sql form."
)


def _notice(shown: int, fetched: int, *, more_beyond_fetch: bool, hint: str) -> str:
    more = " The query has more rows than were fetched." if more_beyond_fetch else ""
    return f"Showing {shown} of {fetched} fetched rows.{more} {hint}"


def model_facing_result(result: dict[str, Any]) -> dict[str, Any]:
    """Drop ``data`` when the result carries the same rows as ``csv``."""
    if "csv" in result:
        return {key: value for key, value in result.items() if key != "data"}
    return result


def model_json(result: dict[str, Any]) -> str:
    """Serialize the model's copy of *result*; every transport uses this."""
    return json.dumps(model_facing_result(result), ensure_ascii=False, default=str)


ERROR_TEXT_FLOOR = 400


def _cut(text: str, cap: int) -> str:
    return text if len(text) <= cap else f"{text[:cap].rstrip()} (truncated)"


def _over_budget(result: dict[str, Any]) -> bool:
    return len(model_json(result).encode("utf-8")) > MODEL_RESULT_BYTE_BUDGET


def fit_failure(result: dict[str, Any]) -> dict[str, Any]:
    """Cut a failed result to the budget, losing the error text last.

    The echoed ``sql`` and ``notes`` shrink first, then each error down to
    ``ERROR_TEXT_FLOOR`` characters (cut text ends ``(truncated)``), then drops
    trailing errors for one ``+K more errors`` line.

    Raises:
        ValueError: if something other than the errors (e.g. ``diagnostics``)
            alone exceeds the budget.
    """
    fitted = result
    for field in ("sql", "notes"):
        cap = len(result.get(field) or "")
        while _over_budget(fitted) and cap:
            cap //= 2
            fitted = {**fitted, field: _cut(result[field], cap)}
    cap = max((len(error) for error in result["errors"]), default=0)
    while _over_budget(fitted) and cap > ERROR_TEXT_FLOOR:
        cap = max(cap // 2, ERROR_TEXT_FLOOR)
        fitted = {**fitted, "errors": [_cut(error, cap) for error in result["errors"]]}
    result_errors = fitted["errors"]
    kept = len(fitted["errors"])
    while _over_budget(fitted) and kept > 1:
        kept -= 1
        fitted = {
            **fitted,
            "errors": [
                *result_errors[:kept],
                f"+{len(result_errors) - kept} more errors",
            ],
        }
    if _over_budget(fitted):
        raise ValueError(
            "A failed result cannot be fitted to the model budget: "
            f"{result.get('error_code')} carries too much outside its error text."
        )
    return fitted


def with_csv(payload: dict[str, Any], *, hint: str) -> dict[str, Any]:
    """Add a ``csv`` of the rows that fit the budget and trim ``data`` to them.

    Hosts read ``data`` and the model reads ``csv``; both hold the same rows,
    so the cut happens once, here. *hint* tells the model how to narrow a cut
    result. The header is always kept, even if the other fields leave no room.
    """
    rows: list[dict[str, Any]] = payload["data"]
    worst_notice = _notice(len(rows), len(rows), more_beyond_fetch=True, hint=hint)
    overhead = len(
        model_json(
            {**payload, "csv": "", "truncated": True, "notice": worst_notice}
        ).encode("utf-8")
    )
    used = overhead
    lines: list[str] = []
    for line in rows_csv_lines(rows, payload["columns"]):
        # Escaping is per character, so a line's cost in the emitted JSON is
        # independent of its neighbors; the surrounding quotes are in overhead.
        cost = len(json.dumps(line + "\n", ensure_ascii=False).encode("utf-8")) - 2
        if lines and used + cost > MODEL_RESULT_BYTE_BUDGET:
            break
        lines.append(line)
        used += cost
    shown = len(lines) - 1
    result = {
        **payload,
        "csv": "".join(f"{line}\n" for line in lines),
        "data": rows[:shown],
        "row_count": shown,
    }
    if shown < len(rows) or payload["truncated"]:
        result["truncated"] = True
        result["notice"] = _notice(
            shown, len(rows), more_beyond_fetch=payload["truncated"], hint=hint
        )
    return result
