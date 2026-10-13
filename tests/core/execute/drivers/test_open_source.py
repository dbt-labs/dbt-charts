"""One home decides whether a source runs on the Arrow driver seam.

Every other warehouse keeps its dbt adapter, untouched. The seam answers two
questions — `is_driver_source` and `open_driver` — and each call site asks the
first before it ever builds a dbt adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest

from dbt_charts.core.compile.models.source import (
    DbtTargetSourceConfig,
    DuckDBSourceConfig,
    LakeComputeTokenSource,
    PostgresSourceConfig,
    ResolvedSourceConfig,
    TrinoSourceConfig,
)
from dbt_charts.core.execute.drivers import (
    DRIVER_SOURCE_TYPES,
    is_driver_source,
    open_driver,
)


def _lakecompute() -> LakeComputeTokenSource:
    return LakeComputeTokenSource(type="lakecompute", token="t")


def _trino() -> TrinoSourceConfig:
    return TrinoSourceConfig.model_validate(
        {"type": "trino", "host": "h", "catalog": "c", "schema": "s", "user": "u"}
    )


_BUILDERS: dict[str, Callable[[], ResolvedSourceConfig]] = {
    "lakecompute": _lakecompute,
    "trino": _trino,
}


def test_the_driver_types_are_lakecompute_and_trino() -> None:
    """Every other warehouse stays on its dbt adapter."""
    assert {"lakecompute", "trino"} == DRIVER_SOURCE_TYPES


@pytest.mark.parametrize("source_type", sorted(DRIVER_SOURCE_TYPES))
def test_every_advertised_driver_type_really_opens_on_the_driver(
    source_type: str,
) -> None:
    """`DRIVER_SOURCE_TYPES` names the seam's warehouses for error messages;
    iterating it is what keeps that list honest against the dispatch."""
    config = _BUILDERS[source_type]()
    assert is_driver_source(config)
    with patch(
        f"dbt_charts.core.execute.drivers.{source_type}.open_{source_type}"
    ) as opener:
        connection = open_driver(config, None)
    assert connection is opener.return_value


def test_the_timeout_reaches_the_driver_opener() -> None:
    with patch(
        "dbt_charts.core.execute.drivers.lakecompute.open_lakecompute"
    ) as opener:
        open_driver(_lakecompute(), 45)
    assert opener.call_args.args == (_lakecompute(), 45)


@pytest.mark.parametrize(
    "config",
    [
        PostgresSourceConfig(
            type="postgres", host="h", database="d", user="u", password="p"
        ),
        DuckDBSourceConfig(type="duckdb", path=":memory:"),
        DbtTargetSourceConfig.model_validate({"type": "snowflake", "threads": 4}),
    ],
    ids=["postgres", "duckdb", "dbt_target"],
)
def test_every_other_source_is_not_a_driver_source(
    config: ResolvedSourceConfig,
) -> None:
    assert not is_driver_source(config)


def test_opening_a_non_driver_source_is_a_caller_bug() -> None:
    """The call sites check `is_driver_source` first, so reaching `open_driver`
    with anything else is a defect to surface, not a type to guess a driver for."""
    config = DuckDBSourceConfig(type="duckdb", path=":memory:")
    with pytest.raises(ValueError, match="lakecompute.*trino"):
        open_driver(config, None)
