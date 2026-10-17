"""The registry text of a query failure tells the reader what to do next."""

from __future__ import annotations

from dbt_charts.core.diagnostics.codes_compile import ERR_FILE_NOT_FOUND
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_ADAPTER_UNAVAILABLE,
    ERR_BOARD_QUERY_NOT_FOUND,
    ERR_QUERY_DURATION_EXCEEDED,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import QueryError


def test_duration_exceeded_says_to_narrow_the_query() -> None:
    message = str(
        QueryError.from_code(ERR_QUERY_DURATION_EXCEEDED, seconds=30, source="wh")
    )

    assert "Narrow the query" in message


def test_runtime_rejection_says_to_change_the_sql() -> None:
    message = str(QueryError.from_code(ERR_WAREHOUSE_RUNTIME, detail="boom"))

    assert message.startswith("Warehouse rejected the query: boom. ")
    assert "Change the SQL" in message


def test_adapter_unavailable_says_what_to_ask_for() -> None:
    message = str(QueryError.from_code(ERR_ADAPTER_UNAVAILABLE, reason="No adapter."))

    assert "ask whoever operates it" in message


def test_file_not_found_advice_holds_for_any_file() -> None:
    message = str(QueryError.from_code(ERR_FILE_NOT_FOUND, path="rec.json"))

    assert message == "File not found: rec.json. Check the path for typos."


def test_unknown_query_on_an_empty_board_does_not_point_at_a_list() -> None:
    message = str(
        QueryError.from_code(
            ERR_BOARD_QUERY_NOT_FOUND, path="b.yml", query_name="q", available=[]
        )
    )

    assert (
        message == "Board b.yml has no query 'q'. Available queries: none configured."
    )
