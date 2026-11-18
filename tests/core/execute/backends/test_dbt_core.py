"""The dbt-adapter backend reads its result on the pool's worker thread."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from dbt_charts.core.compile.models.source import DbtTargetSourceConfig
from dbt_charts.core.execute.backends.dbt_core import DbtBackend
from dbt_charts.core.execute.compose import ResolvedQuery
from dbt_charts.core.execute.query_result import RowFetchLimit
from dbt_charts.core.text.time_zones import UTC

_POSTGRES = DbtTargetSourceConfig(
    type="postgres", host="h", dbname="db", user="u", password="p"
)


class _RecordingCursor:
    """A psycopg2-style cursor that records the thread each fetch runs on."""

    description = [("n", 23, None, None, None, None, None)]

    def __init__(self, rows: int) -> None:
        self.rows = [(n,) for n in range(rows)]
        self.fetch_threads: list[str] = []

    def fetchmany(self, size: int) -> list[tuple[int]]:
        self.fetch_threads.append(threading.current_thread().name)
        chunk, self.rows = self.rows[:size], self.rows[size:]
        return chunk


def test_the_fetch_is_finished_on_the_worker_before_execute_returns() -> None:
    cursor = _RecordingCursor(rows=5)
    adapter = MagicMock()
    adapter.connections.add_query.return_value = (None, cursor)
    limit = RowFetchLimit(limit=3, ceiling_binding=True, max_bytes=10_000_000)

    with patch(
        "dbt_charts.core.execute.backends.dbt_adapter_factory.build_adapter",
        return_value=adapter,
    ):
        typed = DbtBackend(None).execute(
            _POSTGRES,
            ResolvedQuery(
                sql="SELECT n", params=(), setup_sql=(), limit=limit, time_zone=UTC
            ),
        )

    assert typed.rows == [(0,), (1,), (2,)]
    assert typed.truncated_reason == "max_rows"
    assert cursor.fetch_threads
    assert all(name.startswith("dct-srcpool") for name in cursor.fetch_threads)
