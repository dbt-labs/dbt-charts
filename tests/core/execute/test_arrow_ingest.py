"""Python rows → Arrow → DuckDB, with no pyarrow on the path."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import duckdb
import pytest

from dbt_charts.core.execute.arrow_ingest import ingest, insert_rows, rows_to_arrow


def _load(rows: list[dict[str, Any]]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(config={"enable_external_access": False})
    conn.execute("SET TimeZone='UTC'")
    ingest(conn, "t", rows_to_arrow(rows))
    return conn


@pytest.mark.parametrize(
    ("value", "duckdb_type"),
    [
        (True, "BOOLEAN"),
        (7, "BIGINT"),
        (1.5, "DOUBLE"),
        ("x", "VARCHAR"),
        (b"\x00\x01", "BLOB"),
        (date(2026, 1, 2), "DATE"),
        (datetime(2026, 1, 2, 3, 4, 5, 6), "TIMESTAMP"),
        (
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            "TIMESTAMP WITH TIME ZONE",
        ),
        (time(1, 2, 3, 4), "TIME"),
        (timedelta(seconds=3), "INTERVAL"),
        (Decimal("-12.50"), "DECIMAL(4,2)"),
    ],
)
def test_each_kind_round_trips_with_its_type(value: Any, duckdb_type: str) -> None:
    conn = _load([{"v": value}, {"v": None}])
    assert conn.execute("SELECT typeof(v) FROM t LIMIT 1").fetchone() == (duckdb_type,)
    assert conn.execute("SELECT v FROM t").fetchall() == [(value,), (None,)]


def test_aware_datetimes_are_stored_as_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    conn = _load([{"v": datetime(2026, 1, 2, 5, 0, tzinfo=plus_two)}])
    assert conn.execute("SELECT v FROM t").fetchone() == (
        datetime(2026, 1, 2, 3, 0, tzinfo=UTC),
    )


def test_int_and_float_mix_is_double() -> None:
    conn = _load([{"v": 1}, {"v": 2.5}])
    assert conn.execute("SELECT typeof(v), sum(v) FROM t GROUP BY 1").fetchone() == (
        "DOUBLE",
        3.5,
    )


def test_decimals_share_the_widest_scale() -> None:
    conn = _load([{"v": Decimal("1.5")}, {"v": Decimal("123.25")}])
    assert conn.execute("SELECT typeof(v) FROM t LIMIT 1").fetchone() == (
        "DECIMAL(5,2)",
    )
    assert conn.execute("SELECT v FROM t").fetchall() == [
        (Decimal("1.50"),),
        (Decimal("123.25"),),
    ]


def test_list_and_dict_are_json_text() -> None:
    conn = _load([{"v": [1, 2]}, {"v": {"a": 1}}])
    assert conn.execute("SELECT v FROM t").fetchall() == [("[1, 2]",), ('{"a": 1}',)]


def test_all_null_column_loads() -> None:
    conn = _load([{"a": 1, "v": None}])
    assert conn.execute("SELECT a, v FROM t").fetchall() == [(1, None)]


def test_columns_are_the_first_rows_keys() -> None:
    conn = _load([{"a": 1}, {"a": 2, "b": 3}])
    assert [c[0] for c in conn.execute("DESCRIBE t").fetchall()] == ["a"]


def test_mixed_kinds_raise() -> None:
    with pytest.raises(TypeError, match="mixes int, str"):
        rows_to_arrow([{"v": 1}, {"v": "x"}])


_PLUS_FIVE = timezone(timedelta(hours=5))


@pytest.mark.parametrize(
    ("column_type", "values"),
    [
        (
            "TIMESTAMP",
            [datetime(2026, 1, 2, 3), datetime(2026, 1, 2, 3, tzinfo=_PLUS_FIVE)],
        ),
        ("VARCHAR", ["ab", b"ab"]),
        ("VARCHAR", ["x", True]),
        ("VARCHAR", ["x", 5, 1.5]),
        ("BIGINT", [1, True]),
        ("BIGINT", [1, "2", None]),
        ("DOUBLE", [1.5, "2"]),
    ],
)
def test_insert_rows_converts_mixed_cells_as_bound_parameters_do(
    column_type: str, values: list[Any]
) -> None:
    """The oracle is DuckDB's own per-parameter bind, which ``executemany`` does."""
    rows = [{"v": value} for value in values]
    expected = duckdb.connect()
    expected.execute(f"CREATE TABLE t (v {column_type})")
    expected.executemany("INSERT INTO t VALUES (?)", [[value] for value in values])
    actual = duckdb.connect()
    actual.execute(f"CREATE TABLE t (v {column_type})")

    insert_rows(actual, "t", rows)

    query = "SELECT v FROM t"
    assert actual.execute(query).fetchall() == expected.execute(query).fetchall()


def test_decimals_past_28_digits_encode_exactly() -> None:
    value = Decimal("-12345678901234567890123456789012.345678")
    conn = _load([{"v": value}])
    assert conn.execute("SELECT v FROM t").fetchone() == (value,)


def test_uuid_is_text() -> None:
    value = UUID("12345678-1234-5678-1234-567812345678")
    conn = _load([{"v": value}])
    assert conn.execute("SELECT v FROM t").fetchone() == (str(value),)


def test_decimal_wider_than_38_digits_raises() -> None:
    with pytest.raises(ValueError, match="precision 39"):
        rows_to_arrow([{"v": Decimal("1" * 39)}])


def test_runtime_paths_do_not_import_pyarrow(tmp_path: Path) -> None:
    """File sources, the result cache, composition and result conversion run
    with pyarrow hidden."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    data = tmp_path / "data"
    data.mkdir()
    (data / "s.csv").write_text("region,amount\nNorth,1\nSouth,2\n")
    pq.write_table(  # type: ignore[no-untyped-call]  # type-state: type_ignore — pyarrow's stubs are placeholders; upstream gap
        pa.table({"amount": pa.array([Decimal("1.50")], pa.decimal128(10, 2))}),
        data / "p.parquet",
    )
    (data / "j.json").write_text('[{"a": 1}, {"a": "2"}]')
    script = textwrap.dedent(
        f"""
        import sys
        from decimal import Decimal
        from pathlib import Path

        class _Hide:
            def find_spec(self, name, path=None, target=None):
                if name == "pyarrow" or name.startswith("pyarrow."):
                    raise ModuleNotFoundError(name)

        sys.meta_path.insert(0, _Hide())

        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.compile.models.source import (
            CsvSourceConfig, JsonSourceConfig, ParquetSourceConfig,
        )
        from dbt_charts.core.execute.adapters.base import RowFetchLimit
        from dbt_charts.core.execute.arrow_ingest import rows_to_arrow
        from dbt_charts.core.execute.cache_composition import compose_over_named_rows
        from dbt_charts.core.execute.file_source_materializer import (
            FileSourceMaterializer,
        )
        from dbt_charts.core.execute.result import arrow_to_query_result
        from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

        mat = FileSourceMaterializer(FilesystemProject(Path({str(tmp_path)!r})))
        assert mat.materialize_and_run(
            CsvSourceConfig(type="csv", files={{"s": "data/s.csv"}}),
            "SELECT sum(amount) AS n FROM s", {{}}, "src",
        ) == [{{"n": 3}}]
        assert mat.materialize_and_run(
            ParquetSourceConfig(type="parquet", files={{"p": "data/p.parquet"}}),
            "SELECT amount FROM p", {{}}, "src",
        ) == [{{"amount": 1.5}}]
        assert mat.materialize_and_run(
            JsonSourceConfig(type="json", files={{"j": "data/j.json"}}),
            "SELECT count(*) AS n FROM j", {{}}, "src",
        ) == [{{"n": 2}}]

        cache = TrivialDuckDBCache()
        cache.put("s", "q", "v", [{{"a": 1}}, {{"a": "2"}}], board_slug="b", query_name="q")
        assert cache.get("s", "q", "v").rows == [{{"a": 1}}, {{"a": "2"}}]

        assert compose_over_named_rows(
            "SELECT sum(a) AS n FROM up", {{"up": [{{"a": 1}}, {{"a": 2}}]}}, []
        ) == [{{"n": 3}}]

        limit = RowFetchLimit(fetch_limit=10, effective_limit=9, ceiling_binding=False)
        result = arrow_to_query_result(rows_to_arrow([{{"n": Decimal("4")}}]), limit)
        assert result.data == [{{"n": 4}}], result.data
        assert "pyarrow" not in sys.modules
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
