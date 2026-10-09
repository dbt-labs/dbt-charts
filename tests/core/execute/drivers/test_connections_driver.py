"""The connection-scoped probes open a driver source on the driver, not on dbt.

`test_connection`, `bulk_schema_for_config` and `probe_relation_readability` each
open a fresh connection for one purpose. A driver source takes its own arm in
each; the existing dbt arms are covered, unchanged, by `test_connections.py`.
None of these applies a query cap, so the driver gets none beyond its own default.
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pyarrow as pa
import pytest

from dbt_charts.core.compile.models.source import LakeComputeTokenSource
from dbt_charts.core.connections import (
    WarehouseProbeError,
    bulk_schema_for_config,
    probe_relation_readability,
    test_connection as run_test_connection,
)
from dbt_charts.core.execute.adapters.base import RowFetchLimit
from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    AdapterNotInstalledError,
)
from dbt_charts.core.inspect.relations import Relation

from ...._fake_driver import FakeDriverConnection, fake_lakecompute_driver

_COLUMNS = pa.table(
    {
        "table_schema": ["main", "main", "main"],
        "table_name": ["orders", "orders", "users"],
        "column_name": ["id", "total", "id"],
        "data_type": ["BIGINT", "DOUBLE", "BIGINT"],
    }
)


@pytest.fixture(autouse=True)
def _dbt_path_is_forbidden() -> Generator[None]:
    with patch(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
        side_effect=AssertionError("a driver source must never build a dbt adapter"),
    ):
        yield


def _source() -> LakeComputeTokenSource:
    return LakeComputeTokenSource(type="lakecompute", token="t")


class TestTestConnection:
    def test_a_reachable_source_pings_and_releases_its_session(self) -> None:
        with fake_lakecompute_driver(pa.table({"x": [1]})) as (connection, opener):
            ok, error = run_test_connection(_source())
        assert (ok, error) == (True, None)
        assert connection.statements == ["SELECT 1"]
        assert connection.closed
        assert opener.call_args.args == (_source(), None)

    def test_a_connect_failure_is_a_classified_probe_error(self) -> None:
        with patch(
            "dbt_charts.core.execute.drivers.lakecompute.open_lakecompute",
            side_effect=RuntimeError("401 from api.example"),
        ):
            ok, error = run_test_connection(_source())
        assert ok is False
        assert isinstance(error, WarehouseProbeError)
        assert error.detail is not None
        assert "401 from api.example" in error.detail

    def test_a_missing_extra_propagates_unclassified(self) -> None:
        with (
            patch(
                "dbt_charts.core.execute.drivers.lakecompute.open_lakecompute",
                side_effect=AdapterNotInstalledError("missing"),
            ),
            pytest.raises(AdapterNotInstalledError),
        ):
            run_test_connection(_source())

    def test_the_session_is_released_even_when_the_ping_fails(self) -> None:
        connection = _RejectingConnection()
        with patch(
            "dbt_charts.core.execute.drivers.lakecompute.open_lakecompute",
            return_value=connection,
        ):
            ok, _ = run_test_connection(_source())
        assert ok is False
        assert connection.closed


class _RejectingConnection(FakeDriverConnection):
    def __init__(self) -> None:
        super().__init__(pa.table({"x": []}))

    def execute(self, sql: str, limit: RowFetchLimit) -> pa.Table:
        raise RuntimeError("rejected")


class TestBulkSchema:
    def test_the_tree_comes_from_the_dialects_bulk_query(self) -> None:
        with fake_lakecompute_driver(_COLUMNS) as (connection, _):
            tree = bulk_schema_for_config(_source())
        assert tree == {
            "main": {
                "orders": {"id": "BIGINT", "total": "DOUBLE"},
                "users": {"id": "BIGINT"},
            }
        }
        assert "information_schema.columns" in connection.statements[0]
        assert connection.closed

    def test_a_result_cut_short_by_the_ceiling_is_an_error_not_a_partial_tree(
        self,
    ) -> None:
        with (
            fake_lakecompute_driver(_COLUMNS),
            patch(
                "dbt_charts.core.execute.adapters.base.resolve_effective_row_limit",
                return_value=_limit(effective=2),
            ),
            pytest.raises(RuntimeError, match="truncated"),
        ):
            bulk_schema_for_config(_source())

    def test_a_failed_query_is_a_runtime_error(self) -> None:
        connection = _RejectingConnection()
        with (
            patch(
                "dbt_charts.core.execute.drivers.lakecompute.open_lakecompute",
                return_value=connection,
            ),
            pytest.raises(RuntimeError, match="bulk_schema_for_config query failed"),
        ):
            bulk_schema_for_config(_source())
        assert connection.closed


def _limit(effective: int) -> RowFetchLimit:
    return RowFetchLimit(
        fetch_limit=effective + 1, effective_limit=effective, ceiling_binding=True
    )


class TestProbeRelationReadability:
    def test_the_relation_is_quoted_by_the_dialect(self) -> None:
        relation = Relation(database="lake", schema="main", name="orders")
        with fake_lakecompute_driver(pa.table({"x": []})) as (connection, _):
            ok, message = probe_relation_readability(_source(), relation)
        assert (ok, message) == (True, "")
        assert connection.statements == ['SELECT * FROM "lake"."main"."orders" LIMIT 0']
        assert connection.closed

    def test_a_refusal_is_reported_not_raised(self) -> None:
        connection = _RejectingConnection()
        relation = Relation(database=None, schema="main", name="orders")
        with patch(
            "dbt_charts.core.execute.drivers.lakecompute.open_lakecompute",
            return_value=connection,
        ):
            ok, message = probe_relation_readability(_source(), relation)
        assert ok is False
        assert message == "rejected"
