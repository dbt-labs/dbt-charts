"""The link pass reads planned schema; it never asks the warehouse itself."""

from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.core.diagnostics.execution import ExecutionError, QueryError
from dbt_charts.core.execute.link_schema import (
    LinkProbe,
    LinkSchema,
    UnplannedLinkSchemaError,
)

_LOC = ("dw", "main", "orders")


def test_an_unplanned_lookup_is_an_error_not_an_empty_answer() -> None:
    schema = LinkSchema({_LOC: []}, {("dw", "orders"): []}, {"dw": "duckdb"}, {})
    assert schema.columns_of(_LOC) == []
    with pytest.raises(UnplannedLinkSchemaError, match="main.customers"):
        schema.columns_of(("dw", "main", "customers"))
    with pytest.raises(UnplannedLinkSchemaError, match="customers"):
        schema.fk_edges_of("dw", "customers")
    with pytest.raises(UnplannedLinkSchemaError, match="other"):
        schema.dialect_of("other")


def test_the_probe_runs_each_lookup_once_and_freezes_the_answers() -> None:
    rows = [{"name": "id", "actual_type": "INTEGER"}]
    probe = LinkProbe(MagicMock())
    with (
        patch(
            "dbt_charts.core.execute.link_schema.run_query",
            return_value=MagicMock(error=None, data=rows),
        ) as run,
        patch(
            "dbt_charts.core.execute.link_schema.fetch_fk_edges", return_value=[]
        ) as fetch,
        patch(
            "dbt_charts.core.execute.link_schema.engine_dialect",
            return_value="duckdb",
        ),
    ):
        assert probe.columns_of(_LOC) == rows
        assert probe.columns_of(_LOC) == rows
        probe.fk_edges_of("dw", "orders")
        probe.fk_edges_of("dw", "orders")
    assert run.call_count == 1
    assert fetch.call_count == 1
    frozen = probe.freeze()
    assert frozen.columns_of(_LOC) == rows
    assert frozen.fk_edges_of("dw", "orders") == []
    assert frozen.dialect_of("dw") == "duckdb"


def test_columns_are_answered_as_board_names() -> None:
    rows = [
        {"name": "PRIORITY", "actual_type": "TEXT"},
        {"name": "Status", "actual_type": "TEXT"},
    ]
    probe = LinkProbe(MagicMock())
    with (
        patch(
            "dbt_charts.core.execute.link_schema.run_query",
            return_value=MagicMock(error=None, data=rows),
        ),
        patch(
            "dbt_charts.core.execute.link_schema.engine_dialect",
            return_value="snowflake",
        ),
    ):
        names = [row["name"] for row in probe.columns_of(("db", "SALES", "ORDERS"))]
    assert names == ["priority", "Status"]


def test_a_failed_schema_query_is_raised_not_an_empty_answer() -> None:
    missing = QueryError("Table main.orders not found")
    probe = LinkProbe(MagicMock())
    with (
        patch(
            "dbt_charts.core.execute.link_schema.run_query",
            return_value=MagicMock(error=missing, data=[]),
        ),
        pytest.raises(QueryError, match="not found"),
    ):
        probe.columns_of(_LOC)
    with pytest.raises(QueryError, match="not found"):
        probe.freeze().columns_of(_LOC)


def test_a_failed_lookup_is_recorded_and_raised_to_its_reader() -> None:
    boom = ExecutionError("corrupt manifest")
    probe = LinkProbe(MagicMock())
    with (
        patch("dbt_charts.core.execute.link_schema.fetch_fk_edges", side_effect=boom),
        patch(
            "dbt_charts.core.execute.link_schema.engine_dialect",
            return_value="duckdb",
        ),
    ):
        with pytest.raises(ExecutionError):
            probe.fk_edges_of("dw", "orders")
        with pytest.raises(ExecutionError):
            probe.fk_edges_of("dw", "orders")
    with pytest.raises(ExecutionError, match="corrupt manifest"):
        probe.freeze().fk_edges_of("dw", "orders")
