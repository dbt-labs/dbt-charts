"""The link pass reads planned schema; it never asks the warehouse itself."""

from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.link_schema import (
    LinkProbe,
    LinkSchema,
    UnplannedLinkSchemaError,
)

_LOC = ("dw", "main", "orders")


def test_an_unplanned_lookup_is_an_error_not_an_empty_answer() -> None:
    schema = LinkSchema({_LOC: []}, {"orders": []}, {})
    assert schema.columns_of(_LOC) == []
    with pytest.raises(UnplannedLinkSchemaError, match="main.customers"):
        schema.columns_of(("dw", "main", "customers"))
    with pytest.raises(UnplannedLinkSchemaError, match="customers"):
        schema.fk_edges_of("customers")


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
    ):
        assert probe.columns_of(_LOC) == rows
        assert probe.columns_of(_LOC) == rows
        probe.fk_edges_of("orders")
        probe.fk_edges_of("orders")
    assert run.call_count == 1
    assert fetch.call_count == 1
    frozen = probe.freeze()
    assert frozen.columns_of(_LOC) == rows
    assert frozen.fk_edges_of("orders") == []


def test_a_failed_lookup_is_recorded_and_raised_to_its_reader() -> None:
    boom = ExecutionError("corrupt manifest")
    probe = LinkProbe(MagicMock())
    with patch("dbt_charts.core.execute.link_schema.fetch_fk_edges", side_effect=boom):
        with pytest.raises(ExecutionError):
            probe.fk_edges_of("orders")
        with pytest.raises(ExecutionError):
            probe.fk_edges_of("orders")
    with pytest.raises(ExecutionError, match="corrupt manifest"):
        probe.freeze().fk_edges_of("orders")
