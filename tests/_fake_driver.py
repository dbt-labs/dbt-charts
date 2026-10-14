"""A stand-in for a warehouse on the Arrow driver seam.

Records what the engine asked it to send and answers with a fixed Arrow table.
Patching ``open_lakecompute`` is patching what `open_driver` calls, so the code
under test runs its own seam and nothing past it.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import nanoarrow as na
import pyarrow as pa

from dbt_charts.core.execute.adapters.base import RowFetchLimit


class FakeDriverConnection:
    """A ``DriverConnection`` that records its traffic instead of connecting."""

    def __init__(self, table: pa.Table) -> None:
        self._table = na.Array(table)
        self.statements: list[str] = []
        self.session_statements: list[str] = []
        self.closed = False
        self.limit: RowFetchLimit | None = None

    def execute(self, sql: str, limit: RowFetchLimit) -> na.Array:
        self.statements.append(sql)
        self.limit = limit
        return self._table

    def session_sql(self, sql: str) -> None:
        self.session_statements.append(sql)

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@contextmanager
def fake_lakecompute_driver(
    table: pa.Table,
) -> Generator[tuple[FakeDriverConnection, MagicMock]]:
    """Route every lake compute open in the block to one recording connection.

    Yields the connection and the patched opener, whose call args are the
    `(config, timeout_seconds)` the seam passed.
    """
    connection = FakeDriverConnection(table)
    with patch(
        "dbt_charts.core.execute.drivers.lakecompute.open_lakecompute",
        return_value=connection,
    ) as opener:
        yield connection, opener
