"""Every file format × every column type, asserted through author SQL.

One fixture table carries every type a format can express; it is serialized to
each of the four formats and materialized through ``FileSourceMaterializer``.
Each cell then asserts both the SQL-visible
``typeof(col)`` and the value an aggregate over it produces — the pair the
Parquet DECIMAL bug slipped between, where the value looked plausible and the
type was VARCHAR.

Cloud uses this same session-owned materializer, so this one matrix is
authoritative for both hosts.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.source import (
    CsvSourceConfig,
    JsonSourceConfig,
    ParquetSourceConfig,
)
from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer

_UTC = timezone.utc

# Every value is exactly representable as a double, so a DOUBLE cell's sum is
# assertable without approx and reads the same as the DECIMAL cell's. Four
# physical types carry a NULL, in two different rows, so a host that drops or
# coerces one column's NULL cannot be masked by another column's.
_TABLE = pa.table(
    {
        "i": pa.array([1, 2, 3, 4], pa.int64()),
        "f": pa.array([1.5, 2.5, 3.5, 4.5], pa.float64()),
        "d": pa.array(
            [Decimal("1.25"), Decimal("2.50"), Decimal("3.25"), None],
            pa.decimal128(18, 2),
        ),
        "b": pa.array([True, False, True, None], pa.bool_()),
        "s": pa.array(["north", "south", "east", "west"], pa.string()),
        "dt": pa.array(
            [date(2026, 1, 2), date(2026, 2, 3), date(2026, 3, 4), None], pa.date32()
        ),
        "ts": pa.array(
            [
                datetime(2026, 1, 2, 3, 4, 5),
                datetime(2026, 2, 3, 4, 5, 6),
                datetime(2026, 3, 4, 5, 6, 7),
                datetime(2026, 4, 5, 6, 7, 8),
            ],
            pa.timestamp("us"),
        ),
        "ts_tz": pa.array(
            [
                datetime(2026, 1, 2, 3, 4, 5, tzinfo=_UTC),
                datetime(2026, 2, 3, 4, 5, 6, tzinfo=_UTC),
                datetime(2026, 3, 4, 5, 6, 7, tzinfo=_UTC),
                datetime(2026, 4, 5, 6, 7, 8, tzinfo=_UTC),
            ],
            pa.timestamp("us", tz="UTC"),
        ),
        "n": pa.array([10, None, 20, 30], pa.int64()),
    }
)

# typeof() rides along in the same statement so a cell asserts the type SQL
# saw, not one re-derived after.
_AGGREGATES = {
    "i": "SUM(i)",
    "f": "SUM(f)",
    "d": "SUM(d)",
    "b": "COUNT(*) FILTER (WHERE b)",
    "s": "MAX(s)",
    "dt": "MAX(dt)",
    "ts": "MAX(ts)",
    "ts_tz": "MAX(ts_tz)",
    "n": "SUM(n)",
}

_SHARED_VALUES: dict[str, Any] = {
    "i": 10,
    "f": 12.0,
    "d": 7.0,
    "b": 2,
    "s": "west",
    "dt": date(2026, 3, 4),
    "ts": datetime(2026, 4, 5, 6, 7, 8),
    "ts_tz": datetime(2026, 4, 5, 6, 7, 8, tzinfo=_UTC),
    "n": 60,
}
_JSON_VALUES: dict[str, Any] = {
    **_SHARED_VALUES,
    "dt": "2026-03-04",
    "ts": "2026-04-05T06:07:08",
    "ts_tz": "2026-04-05T06:07:08+00:00",
}

_JSON_TYPES = {
    "i": "BIGINT",
    "f": "DOUBLE",
    "d": "DOUBLE",
    "b": "BOOLEAN",
    "s": "VARCHAR",
    "dt": "VARCHAR",
    "ts": "VARCHAR",
    "ts_tz": "VARCHAR",
    "n": "BIGINT",
}

# format → column → (DuckDB type author SQL sees, value the aggregate returns).
_MATRIX: dict[str, dict[str, tuple[str, Any]]] = {
    "csv": {
        col: (t, _SHARED_VALUES[col])
        for col, t in {
            "i": "BIGINT",
            "f": "DOUBLE",
            # CSV declares no type: pyarrow infers double from "1.25", and no
            # scale survives to make it a DECIMAL.
            "d": "DOUBLE",
            "b": "BOOLEAN",
            "s": "VARCHAR",
            "dt": "DATE",
            # PyArrow's CSV reader infers nanosecond precision from the
            # microsecond-tailed literal; DuckDB keeps that unit.
            "ts": "TIMESTAMP_NS",
            "ts_tz": "TIMESTAMP WITH TIME ZONE",
            "n": "BIGINT",
        }.items()
    },
    "parquet": {
        col: (t, _SHARED_VALUES[col])
        for col, t in {
            "i": "BIGINT",
            "f": "DOUBLE",
            "d": "DECIMAL(18,2)",
            "b": "BOOLEAN",
            "s": "VARCHAR",
            "dt": "DATE",
            "ts": "TIMESTAMP",
            "ts_tz": "TIMESTAMP WITH TIME ZONE",
            "n": "BIGINT",
        }.items()
    },
    "json": {col: (t, _JSON_VALUES[col]) for col, t in _JSON_TYPES.items()},
    "jsonl": {col: (t, _JSON_VALUES[col]) for col, t in _JSON_TYPES.items()},
}

_CELLS = [(fmt, col) for fmt, cols in _MATRIX.items() for col in cols]


def _json_default(value: Any) -> Any:
    """Serialize the fixture's non-JSON types the way an author's file would."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"unserializable fixture value: {value!r}")


def _matrix_bytes(fmt: str) -> bytes:
    if fmt == "csv":
        buf = io.BytesIO()
        pacsv.write_csv(_TABLE, buf)
        return buf.getvalue()
    if fmt == "parquet":
        buf = io.BytesIO()
        pq.write_table(_TABLE, buf)
        return buf.getvalue()
    rows = _TABLE.to_pylist()
    if fmt == "json":
        return json.dumps(rows, default=_json_default).encode()
    return b"\n".join(json.dumps(row, default=_json_default).encode() for row in rows)


def _matrix_source(
    fmt: str, tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> tuple[FilesystemProject, CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig]:
    relpath = f"data/matrix.{fmt}"
    dest = tmp_path / relpath
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_matrix_bytes(fmt))
    files = {"matrix": relpath}
    if fmt == "csv":
        return local_project(tmp_path), CsvSourceConfig(type="csv", files=files)
    if fmt == "parquet":
        return local_project(tmp_path), ParquetSourceConfig(type="parquet", files=files)
    return local_project(tmp_path), JsonSourceConfig(type="json", files=files)


@pytest.mark.parametrize(("fmt", "column"), _CELLS)
def test_file_column_type_and_value(
    fmt: str,
    column: str,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    project, source = _matrix_source(fmt, tmp_path, local_project)
    mat = FileSourceMaterializer(project)
    expected_type, expected_value = _MATRIX[fmt][column]

    rows = mat.materialize_and_run(
        source,
        f"SELECT ANY_VALUE(typeof({column})) AS t, {_AGGREGATES[column]} AS v "
        "FROM matrix",
        {},
        "matrix_source",
    )

    assert rows[0]["t"] == expected_type
    assert rows[0]["v"] == expected_value


@pytest.mark.parametrize("fmt", sorted(_MATRIX))
def test_null_cells_survive_materialization(
    fmt: str,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    project, source = _matrix_source(fmt, tmp_path, local_project)
    mat = FileSourceMaterializer(project)

    rows = mat.materialize_and_run(
        source, "SELECT b, d, dt, n FROM matrix ORDER BY i", {}, "matrix_source"
    )

    assert [row["b"] for row in rows] == [True, False, True, None]
    assert [row["d"] for row in rows] == [1.25, 2.50, 3.25, None]
    assert [row["n"] for row in rows] == [10, None, 20, 30]
    # dt is a date on the file formats and an ISO string on the JSON ones, so
    # only its nullness is comparable across formats.
    assert [row["dt"] is None for row in rows] == [False, False, False, True]
