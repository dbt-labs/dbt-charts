from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.agent_api.query_csv import (
    MODEL_RESULT_BYTE_BUDGET,
    fit_failure,
    model_facing_result,
    model_json,
    with_csv,
)

HINT = "Narrow it."


def _payload(rows: list[dict[str, Any]], *, truncated: bool = False) -> dict[str, Any]:
    return {
        "success": True,
        "columns": list(rows[0]) if rows else [],
        "data": rows,
        "errors": [],
        "row_count": len(rows),
        "truncated": truncated,
    }


def _emitted_bytes(result: dict[str, Any]) -> int:
    return len(model_json(result).encode("utf-8"))


def test_rows_become_csv_and_data_trims_to_the_rows_in_it() -> None:
    rows = [{"city": "Oslo", "sales": i} for i in range(3)]
    result = with_csv(_payload(rows), hint=HINT)
    assert result["csv"] == "city,sales\nOslo,0\nOslo,1\nOslo,2\n"
    assert result["data"] == rows
    assert result["truncated"] is False
    assert "notice" not in result
    assert "data" not in model_facing_result(result)


def test_zero_rows_yield_a_well_formed_result() -> None:
    """The engine derives columns from the first row, so an empty result has none."""
    result = with_csv(_payload([]), hint=HINT)
    assert result["csv"] == "\n"
    assert result["data"] == []
    assert result["truncated"] is False
    assert "notice" not in result


@pytest.mark.parametrize(
    "value",
    ["x" * 5000, "界" * 2000, "😀" * 1500, '\\"q"\\' * 800, "line\n" * 800],
    ids=["ascii", "cjk", "emoji", "quote_backslash", "newlines"],
)
def test_emitted_bytes_stay_within_the_budget_for_hostile_text(value: str) -> None:
    rows = [{"id": i, "body": value} for i in range(1000)]
    result = with_csv(_payload(rows), hint=HINT)
    assert _emitted_bytes(result) <= MODEL_RESULT_BYTE_BUDGET
    shown = len(result["data"])
    assert 0 < shown < 1000
    assert result["truncated"] is True
    assert result["row_count"] == shown
    assert result["csv"].count("\n") == shown + 1
    assert f"{shown} of 1000" in result["notice"]
    assert HINT in result["notice"]


def test_a_huge_non_row_field_leaves_a_header_only_result_with_a_notice() -> None:
    payload = _payload([{"n": 1}, {"n": 2}])
    payload["sql"] = "x" * (MODEL_RESULT_BYTE_BUDGET + 10)
    result = with_csv(payload, hint=HINT)
    assert result["csv"] == "n\n"
    assert result["data"] == []
    assert result["truncated"] is True
    assert "0 of 2" in result["notice"]


def test_engine_truncation_is_reported_even_when_every_fetched_row_fits() -> None:
    result = with_csv(_payload([{"n": 1}], truncated=True), hint=HINT)
    assert result["truncated"] is True
    assert "more rows than were fetched" in result["notice"]
    assert HINT in result["notice"]


def test_model_facing_result_drops_data_only_when_there_is_a_csv() -> None:
    assert model_facing_result({"csv": "a\n", "data": [1]}) == {"csv": "a\n"}
    assert model_facing_result({"data": "yaml"}) == {"data": "yaml"}


def test_a_short_failure_is_returned_as_is() -> None:
    failed = {
        "success": False,
        "errors": ["Warehouse rejected the query: boom."],
        "error_code": "ERR-WAREHOUSE-RUNTIME",
    }

    assert fit_failure(failed) == failed


def test_a_very_long_warehouse_error_is_cut_to_the_budget_with_a_marker() -> None:
    failed = {
        "success": False,
        "errors": ["Warehouse rejected the query: " + "é\n" * 60_000, "Second."],
        "error_code": "ERR-WAREHOUSE-RUNTIME",
    }

    fitted = fit_failure(failed)

    assert _emitted_bytes(fitted) <= MODEL_RESULT_BYTE_BUDGET
    assert fitted["errors"][0].endswith(" (truncated)")
    assert fitted["errors"][0].startswith("Warehouse rejected the query: ")
    assert fitted["errors"][1] == "Second."
    assert fitted["error_code"] == "ERR-WAREHOUSE-RUNTIME"


def test_an_oversized_echoed_sql_is_cut_before_the_error_text() -> None:
    message = "Query 'q' uses variable 'v' (input: text). Pass a binding for it."
    failed = {
        "success": False,
        "errors": [message],
        "error_code": "ERR-VARIABLE-UNBOUND",
        "sql": "select 1 -- " + "x" * 60_000,
    }

    fitted = fit_failure(failed)

    assert _emitted_bytes(fitted) <= MODEL_RESULT_BYTE_BUDGET
    assert fitted["errors"] == [message]
    assert fitted["sql"].endswith(" (truncated)")
    assert fitted["sql"].startswith("select 1")


@pytest.mark.parametrize(
    "errors",
    [
        [f"Compile error number {i}: " + "x" * 90 for i in range(500)],
        [f"第{i}个错误：" + "界" * 300 for i in range(60)],
    ],
)
def test_many_errors_keep_the_first_and_count_the_rest(errors: list[str]) -> None:
    failed = {"success": False, "errors": errors, "error_code": "ERR-X"}

    fitted = fit_failure(failed)

    assert _emitted_bytes(fitted) <= MODEL_RESULT_BYTE_BUDGET
    assert fitted["errors"][0] == errors[0]
    kept = len(fitted["errors"]) - 1
    assert fitted["errors"][-1] == f"+{len(errors) - kept} more errors"


def test_a_failure_that_cannot_fit_says_so_instead_of_returning_over_budget() -> None:
    failed = {
        "success": False,
        "errors": ["boom"],
        "diagnostics": [{"message": "y" * 60_000}],
    }

    with pytest.raises(ValueError, match="cannot be fitted"):
        fit_failure(failed)


def test_model_json_keeps_non_ascii_text_unescaped() -> None:
    assert "界" in model_json({"csv": "界"})
