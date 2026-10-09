"""Lake compute, for real. Skipped unless `DCT_LAKECOMPUTE_TOKEN` is set.

Every query here goes through `CdnDriverConnection.execute`, which raises when the
backend attaches a warning to the result (`adbc.dbt.last_warnings`). A test that
passes is therefore also a record that an ordinary query produces none, which is
what the strict raise-on-warning policy assumes.
"""

from __future__ import annotations

import os

import pytest

from dbt_charts.core.compile.models.source import LakeComputeTokenSource
from dbt_charts.core.connections import bulk_schema_for_config
from dbt_charts.core.execute.adapters.base import resolve_effective_row_limit
from dbt_charts.core.execute.drivers import open_driver
from dbt_charts.core.execute.result import arrow_to_query_result

pytestmark = pytest.mark.skipif(
    not os.environ.get("DCT_LAKECOMPUTE_TOKEN"),
    reason="set DCT_LAKECOMPUTE_TOKEN to run against lake compute",
)


def _source() -> LakeComputeTokenSource:
    return LakeComputeTokenSource(
        type="lakecompute", token=os.environ["DCT_LAKECOMPUTE_TOKEN"]
    )


def test_select_one() -> None:
    connection = open_driver(_source(), None)
    try:
        table = connection.execute("SELECT 1 AS one", resolve_effective_row_limit(10))
    finally:
        connection.close()
    assert arrow_to_query_result(table, resolve_effective_row_limit(10)).data == [
        {"one": 1}
    ]


def test_a_ten_thousand_row_fetch() -> None:
    limit = resolve_effective_row_limit(None)
    connection = open_driver(_source(), None)
    try:
        table = connection.execute("SELECT * FROM range(10000) t(n)", limit)
    finally:
        connection.close()
    assert len(arrow_to_query_result(table, limit).data) == 10_000


def test_an_information_schema_listing() -> None:
    tree = bulk_schema_for_config(_source())
    assert isinstance(tree, dict)
