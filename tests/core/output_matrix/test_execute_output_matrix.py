"""Pins what the execute layer sends to each local engine and what comes back.

One snapshot per case under ``snapshots/``: the SQL text and bindings handed to
the driver, and the rows the executor returns. Refactors of source resolution,
query composition, and the backend seam diff against these files; only an
intended change (a binding replacing an inlined literal) may move them.

Capture is a test-only monkeypatch at the driver call: ``DuckDBBackend.
execute``, ``sqlite3.connect`` (a recording connection), the cache-ref
composition call ``compose_over_named_rows``, and the file
materializer's private DuckDB connection (only its SELECT/WITH statements; the
CREATE VIEW / SET plumbing carries machine-specific names).

Regenerate with ``uv run pytest dbt-charts/tests/core/output_matrix
--snapshot-update`` and review the diff.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.models.source import ResolvedSourceConfig
from dbt_charts.core.execute import cache_composition
from dbt_charts.core.execute.backends.duckdb_local import DuckDBBackend
from dbt_charts.core.execute.compose import ResolvedQuery
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

from ..._run import Run

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

Call = dict[str, Any]  # type-state: explicit_any — driver bindings and rows


class _RecordingConnection:
    def __init__(self, inner: sqlite3.Connection, calls: list[Call]) -> None:
        self._inner = inner
        self._calls = calls

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        self._calls.append({"sql": sql, "params": list(params)})
        return self._inner.execute(sql, params)

    def __getattr__(self, name: str) -> Any:  # type-state: explicit_any — proxy
        return getattr(self._inner, name)


class _RecordingDuckDB:
    def __init__(self, inner: duckdb.DuckDBPyConnection, calls: list[Call]) -> None:
        self._inner = inner
        self._calls = calls

    def execute(self, sql: str, params: Any = None) -> duckdb.DuckDBPyConnection:
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            self._calls.append({"sql": sql, "params": list(params or [])})
        return self._inner.execute(sql, params)

    def __getattr__(self, name: str) -> Any:  # type-state: explicit_any — proxy
        return getattr(self._inner, name)


@dataclass(frozen=True)
class Case:
    id: str
    sources: str
    """Body of ``sources:`` in dbt_charts.yml."""
    board: str
    run: tuple[str, ...]
    variables: dict[str, Any] = field(default_factory=dict)
    # type-state: explicit_any — variable values


def _build_data(root: Path) -> None:
    (root / "data").mkdir()
    for path in (root / "data" / "t.duckdb", root / "data" / "t.sqlite"):
        conn = duckdb.connect(str(path)) if path.suffix == ".duckdb" else None
        if conn is not None:
            conn.execute("CREATE TABLE t (id INTEGER, region VARCHAR, amount DOUBLE)")
            conn.execute(
                "INSERT INTO t VALUES (1,'North',10.5),(2,'South',20),(3,'N''orth',5)"
            )
            conn.close()
    lite = sqlite3.connect(root / "data" / "t.sqlite")
    lite.execute("CREATE TABLE t (id INTEGER, region TEXT, amount REAL)")
    lite.executemany(
        "INSERT INTO t VALUES (?,?,?)",
        [(1, "North", 10.5), (2, "South", 20), (3, "N'orth", 5)],
    )
    lite.commit()
    lite.close()
    (root / "data" / "t.csv").write_text(
        "id,region,amount\n1,North,10.5\n2,South,20\n3,N'orth,5\n"
    )


_CHART = "charts:\n  c:\n    type: table\n    query: q\n"
_REGION_VAR = "variables:\n  region:\n    default: North\n"
_DUCK = "  db:\n    type: duckdb\n    path: data/t.duckdb\n"
_LITE = "  db:\n    type: sqlite\n    path: data/t.sqlite\n"
_CSV = "  db:\n    type: csv\n    files:\n      t: data/t.csv\n"

CASES = [
    Case(
        "duckdb_variable",
        _DUCK,
        "source: db\n" + _REGION_VAR + "queries:\n  q: SELECT id, amount FROM t"
        " WHERE region = {{ region }} ORDER BY id\n" + _CHART,
        ("q",),
        {"region": "N'orth"},
    ),
    Case(
        "sqlite_variable",
        _LITE,
        "source: db\n" + _REGION_VAR + "queries:\n  q: SELECT id, amount FROM t"
        " WHERE region = {{ region }} ORDER BY id\n" + _CHART,
        ("q",),
        {"region": "N'orth"},
    ),
    Case(
        "csv_file_variable",
        _CSV,
        "source: db\n" + _REGION_VAR + "queries:\n  q: SELECT id, amount FROM t"
        " WHERE region = {{ region }} ORDER BY id\n" + _CHART,
        ("q",),
        {"region": "North"},
    ),
    Case(
        "duckdb_cache_ref",
        _DUCK,
        "source: db\nqueries:\n"
        "  base: SELECT region, sum(amount) AS total FROM t GROUP BY region\n"
        "  q: SELECT region, total * 2 AS doubled FROM {{ queries.base.cache }}"
        " ORDER BY region\n" + _CHART,
        ("q",),
    ),
    Case(
        "duckdb_setup_sql",
        _DUCK,
        "source: db\nqueries:\n  q:\n"
        "    setup_sql: CREATE TEMP VIEW big AS SELECT * FROM t WHERE amount > 6\n"
        "    sql: SELECT id FROM big ORDER BY id\n" + _CHART,
        ("q",),
    ),
]


def _scrub(value: Any, root: Path) -> Any:  # type-state: explicit_any — JSON tree
    if isinstance(value, str):
        return value.replace(str(root.resolve()), "<root>").replace(str(root), "<root>")
    if isinstance(value, list):
        return [_scrub(v, root) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v, root) for k, v in value.items()}
    return value


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[Call]:
    recorded: list[Call] = []
    real_execute: Callable[..., Any] = DuckDBBackend.execute

    def execute(
        self: DuckDBBackend, source: ResolvedSourceConfig, query: ResolvedQuery
    ) -> Any:
        for statement in query.setup_sql:
            recorded.append({"sql": statement, "params": []})
        recorded.append({"sql": query.sql, "params": list(query.params)})
        return real_execute(self, source, query)

    real_connection = FileSourceMaterializer._connection  # pyright: ignore[reportPrivateUsage] — wrapping the driver seam

    def connection(self: FileSourceMaterializer) -> Any:
        return _RecordingDuckDB(real_connection(self), recorded)

    real_compose: Callable[..., Any] = cache_composition.compose_over_named_rows

    def compose(
        sql: str,
        named_rows: dict[str, Any],
        params: list[Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        recorded.append(
            {"sql": sql, "params": list(params), "tables": sorted(named_rows)}
        )
        return real_compose(sql, named_rows, params, *args, **kwargs)

    real_connect = sqlite3.connect

    def connect(*args: Any, **kwargs: Any) -> _RecordingConnection:
        return _RecordingConnection(real_connect(*args, **kwargs), recorded)

    monkeypatch.setattr(DuckDBBackend, "execute", execute)
    monkeypatch.setattr(FileSourceMaterializer, "_connection", connection)
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(cache_composition, "compose_over_named_rows", compose)
    return recorded


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_execute_output_matrix(
    case: Case,
    tmp_path: Path,
    calls: list[Call],
    request: pytest.FixtureRequest,
) -> None:
    _build_data(tmp_path)
    (tmp_path / "dbt_charts.yml").write_text(f"sources:\n{case.sources}")
    project = FilesystemProject(tmp_path)
    compiled = compile_board(case.board)
    assert compiled.board is not None, compiled.errors

    executor = Run(
        compiled.board,
        build_exec_env(project),
        query_registry=compiled.query_registry,
        result_cache=TrivialDuckDBCache(),
    )
    snapshot: dict[str, Any] = {}  # type-state: explicit_any — JSON tree
    for name in case.run:
        calls.clear()
        rows = executor.execute_query(name, case.variables)
        snapshot[name] = {"calls": list(calls), "rows": rows}

    actual = json.dumps(
        _scrub(snapshot, tmp_path), indent=2, sort_keys=True, default=str
    )
    path = SNAPSHOT_DIR / f"{case.id}.json"
    if request.config.getoption("--snapshot-update"):
        path.write_text(actual + "\n", encoding="utf-8")
    assert path.exists(), f"no snapshot; run with --snapshot-update ({path})"
    assert actual + "\n" == path.read_text(encoding="utf-8")
