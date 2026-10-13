"""Trino, for real. Skipped unless `DCT_TRINO_HOST` is set.

Any Trino answers these: every query reads the built-in `tpch` catalog or no
table at all. Start one with `docker run -p 8080:8080 trinodb/trino` and set
`DCT_TRINO_HOST=localhost`.
"""

from __future__ import annotations

import os
import threading

import pytest

from dbt_charts.core.compile.models.source import TrinoSourceConfig
from dbt_charts.core.connections import bulk_schema_for_config
from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.execute.adapters.base import resolve_effective_row_limit
from dbt_charts.core.execute.drivers import open_driver
from dbt_charts.core.execute.result import arrow_to_query_result

pytestmark = pytest.mark.skipif(
    not os.environ.get("DCT_TRINO_HOST"),
    reason="set DCT_TRINO_HOST to run against a Trino coordinator",
)


def _source() -> TrinoSourceConfig:
    return TrinoSourceConfig.model_validate(
        {
            "type": "trino",
            "host": os.environ["DCT_TRINO_HOST"],
            "port": int(os.environ.get("DCT_TRINO_PORT", "8080")),
            "user": "dbt-charts-tests",
            "catalog": "tpch",
            "schema": "tiny",
        }
    )


def test_select_one() -> None:
    limit = resolve_effective_row_limit(10)
    connection = open_driver(_source(), None)
    try:
        table = connection.execute("SELECT 1 AS one", limit)
    finally:
        connection.close()
    assert arrow_to_query_result(table, limit).data == [{"one": 1}]


def test_a_fetch_stops_near_the_row_limit() -> None:
    limit = resolve_effective_row_limit(100)
    connection = open_driver(_source(), None)
    try:
        table = connection.execute("SELECT * FROM tpch.sf1.lineitem", limit)
    finally:
        connection.close()
    assert limit.fetch_limit <= table.num_rows < 6_000_000
    assert len(arrow_to_query_result(table, limit).data) == 100


def test_cancel_stops_a_running_statement() -> None:
    connection = open_driver(_source(), None)
    timer = threading.Timer(2, connection.cancel)
    timer.start()
    try:
        with pytest.raises(Exception, match="USER_CANCELED|cancel"):
            connection.execute(
                "SELECT count(*) FROM tpch.sf100.lineitem a, tpch.sf100.lineitem b",
                resolve_effective_row_limit(1),
            )
    finally:
        timer.cancel()
        connection.close()


def test_the_query_cap_is_enforced_and_recognized() -> None:
    connection = open_driver(_source(), 1)
    try:
        with pytest.raises(Exception) as excinfo:  # noqa: PT011 — the client's own error type
            connection.execute(
                "SELECT count(*) FROM tpch.sf100.lineitem a, tpch.sf100.lineitem b",
                resolve_effective_row_limit(1),
            )
    finally:
        connection.close()
    assert get_dialect("trino").is_statement_timeout_error(excinfo.value)


def test_an_information_schema_listing() -> None:
    tree = bulk_schema_for_config(_source())
    assert "customer" in tree["tiny"]
