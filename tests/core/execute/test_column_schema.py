"""Every successful result declares its column types; none are read off cells."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import nanoarrow as na
import pyarrow as pa
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import (
    ColumnKind,
    ColumnType,
    literal_schema,
    map_to_json,
)
from dbt_charts.core.compile.models.query.normalized import (
    HttpQuery,
    SqlQuery,
    ValuesQuery,
)
from dbt_charts.core.compile.models.source import (
    CsvSourceConfig,
    DuckDBSourceConfig,
    SQLiteSourceConfig,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_RESULT_COLUMN_TYPE
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.base import QueryResult, RowFetchLimit
from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter
from dbt_charts.core.execute.adapters.http_adapter import HttpAdapter
from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter
from dbt_charts.core.execute.adapters.values_adapter import ValuesAdapter
from dbt_charts.core.execute.duckdb_types import duckdb_column_type
from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer
from dbt_charts.core.execute.result import arrow_to_query_result

FLOAT = ColumnType(kind="float")
LIST_FLOAT = ColumnType(kind="list", element=FLOAT)
LIMIT = RowFetchLimit(fetch_limit=100, effective_limit=100, ceiling_binding=False)


def test_driver_list_of_doubles_is_list_float() -> None:
    table = pa.table({"xs": pa.array([[1.5, 2.0]], type=pa.list_(pa.float64()))})
    assert arrow_to_query_result(na.Array(table), LIMIT).schema == {"xs": LIST_FLOAT}


def test_driver_struct_and_scalars() -> None:
    table = pa.table(
        {
            "s": pa.array([{"a": 1, "b": "x"}]),
            "d": pa.array([0], type=pa.date32()),
            "n": pa.array([1], type=pa.int8()),
        }
    )
    assert arrow_to_query_result(na.Array(table), LIMIT).schema == {
        "s": ColumnType(
            kind="struct",
            fields=(
                ("a", ColumnType(kind="integer")),
                ("b", ColumnType(kind="string")),
            ),
        ),
        "d": ColumnType(kind="date"),
        "n": ColumnType(kind="integer"),
    }


def test_driver_unmappable_type_names_column_and_type() -> None:
    table = pa.table({"gap": pa.array([1], type=pa.duration("s"))})
    with pytest.raises(ExecutionError) as excinfo:
        arrow_to_query_result(na.Array(table), LIMIT)
    assert excinfo.value.code is ERR_RESULT_COLUMN_TYPE
    assert "'gap'" in str(excinfo.value)
    assert "cast" in str(excinfo.value)


def test_driver_null_typed_column_is_string() -> None:
    table = pa.table({"gap": pa.array([None], type=pa.null())})
    assert arrow_to_query_result(na.Array(table), LIMIT).schema == {
        "gap": ColumnType(kind="string")
    }


def test_driver_dictionary_column_declares_its_value_type() -> None:
    table = pa.table({"d": pa.array(["a", "b", "a"]).dictionary_encode()})
    assert arrow_to_query_result(na.Array(table), LIMIT).schema == {
        "d": ColumnType(kind="string")
    }


def test_values_list_literal_is_list_float() -> None:
    query = ValuesQuery(rows=[{"xs": [1, 2.5]}, {"xs": [3.0]}])
    result = ValuesAdapter().execute(query)
    assert result.schema == {"xs": LIST_FLOAT}


def test_values_column_mixing_kinds_is_rejected() -> None:
    with pytest.raises(ExecutionError) as excinfo:
        ValuesAdapter().execute(ValuesQuery(rows=[{"v": 1}, {"v": "a"}]))
    assert excinfo.value.code is ERR_RESULT_COLUMN_TYPE
    assert "'v'" in str(excinfo.value)


def test_duckdb_declares_nested_types_even_for_zero_rows() -> None:
    adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
    result = adapter.execute(
        SqlQuery(sql="select [1.5::double] as xs, 1::hugeint as n where false")
    )
    assert result.schema == {"xs": LIST_FLOAT, "n": ColumnType(kind="integer")}


def test_successful_result_without_schema_is_rejected() -> None:
    with pytest.raises(ValueError, match="schema"):
        QueryResult(data=[{"a": 1}])


def _duckdb(sql: str) -> QueryResult:
    adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
    return adapter.execute(SqlQuery(sql=sql))


def test_duckdb_cells_match_their_declarations() -> None:
    result = _duckdb(
        "select '{\"a\":1}'::json j, ['[1]']::json[] jl, {'k': '2'::json} sj, "
        "map {'x': 1} m, [1, 2]::int[2] fixed, 1::bignum b, "
        "'6f1a2b3c-0000-4000-8000-000000000000'::uuid u"
    )
    assert result.error is None, result.error
    assert result.data == [
        {
            "j": {"a": 1},
            "jl": [[1]],
            "sj": {"k": 2},
            "m": {"x": 1},
            "fixed": [1, 2],
            "b": 1,
            "u": "6f1a2b3c-0000-4000-8000-000000000000",
        }
    ]
    assert result.schema["m"] == ColumnType(kind="json")
    assert result.schema["fixed"] == ColumnType(
        kind="list", element=ColumnType(kind="integer")
    )


def test_duckdb_interval_raises_with_a_cast_hint() -> None:
    result = _duckdb("select interval 1 day as span")
    assert result.error is not None
    assert result.error.code is ERR_RESULT_COLUMN_TYPE
    assert "'span'" in str(result.error)
    assert "cast" in str(result.error)


def test_values_column_with_only_nulls_is_rejected() -> None:
    with pytest.raises(ExecutionError) as excinfo:
        ValuesAdapter().execute(ValuesQuery(rows=[{"v": None}, {"v": None}]))
    assert excinfo.value.code is ERR_RESULT_COLUMN_TYPE


def test_compile_reports_a_values_column_that_mixes_kinds() -> None:
    from dbt_charts.core.compile import compile

    result = compile(
        "id: b\nqueries:\n  q:\n    rows:\n      - v: 1\n      - v: a\n"
        "rows:\n  - chart:\n      type: table\n      query: q\n"
    )
    assert result.board is None
    assert any("'v'" in d.message for d in result.errors)


def test_literal_kinds_keep_bool_before_int_and_datetime_before_date() -> None:
    schema = literal_schema(
        [{"b": True, "i": 1, "dt": datetime(2020, 1, 1), "d": date(2020, 1, 1)}]
    )
    assert [schema[c].kind for c in ("b", "i", "dt", "d")] == [
        "boolean",
        "integer",
        "datetime",
        "date",
    ]


def test_column_type_carries_exactly_the_children_its_kind_needs() -> None:
    with pytest.raises(ValueError, match="element"):
        ColumnType(kind="list")
    with pytest.raises(ValueError, match="fields"):
        ColumnType(kind="integer", fields=())


def test_sqlite_schema_is_read_from_values_and_empty_columns_are_string(
    tmp_path: Path,
) -> None:
    db = tmp_path / "t.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("create table t (a integer, b text)")
        conn.execute("insert into t values (1, null)")
        conn.commit()
    adapter = SqliteAdapter()
    source = SQLiteSourceConfig(type="sqlite", path=str(db))
    result = adapter._execute(
        SqlQuery(sql="select a, b from t", source="db"), source_config=source
    )
    assert result.schema == {
        "a": ColumnType(kind="integer"),
        "b": ColumnType(kind="string"),
    }
    empty = adapter._execute(
        SqlQuery(sql="select a from t where a > 9", source="db"), source_config=source
    )
    assert empty.schema == {"a": ColumnType(kind="string")}


def test_http_schema_is_read_from_the_json_and_empty_columns_are_string() -> None:
    rows = [{"id": 1, "note": None}, {"id": 2, "note": None}]
    response = MagicMock()
    response.json.return_value = rows
    with patch("dbt_charts.core.execute.adapters.http_adapter.httpx.Client") as client:
        client.return_value.__enter__.return_value.get.return_value = response
        result = HttpAdapter().execute(HttpQuery(url="https://api.example.com/"))
    assert result.schema == {
        "id": ColumnType(kind="integer"),
        "note": ColumnType(kind="string"),
    }


def test_file_source_result_declares_duckdb_types(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "s.csv").write_text("n,label\n1,a\n2,b\n")
    materializer = FileSourceMaterializer(local_project(tmp_path))
    rows, schema = materializer.materialize_and_run(
        CsvSourceConfig(type="csv", files={"s": "s.csv"}),
        "select n, label from s order by n",
        {},
        "src",
    )
    assert rows == [{"n": 1, "label": "a"}, {"n": 2, "label": "b"}]
    assert schema == {
        "n": ColumnType(kind="integer"),
        "label": ColumnType(kind="string"),
    }


def test_duckdb_map_becomes_a_json_object_with_string_keys_and_json_values() -> None:
    result = _duckdb(
        "select map {1: 1.5::decimal(4,2)} n, map {'d': date '2020-01-01'} d"
    )
    assert result.error is None, result.error
    assert result.data == [{"n": {"1": 1.5}, "d": {"d": "2020-01-01"}}]


def test_duckdb_time_ns_is_time() -> None:
    import duckdb

    assert duckdb_column_type("t", duckdb.type("TIME_NS")) == ColumnType(kind="time")


def test_duckdb_map_keyed_by_a_struct_raises() -> None:
    result = _duckdb("select map([{'a': 1}], [2]) as m")
    assert result.error is not None
    assert result.error.code is ERR_RESULT_COLUMN_TYPE
    assert "'m'" in str(result.error)


def test_map_to_json_rejects_keys_that_collide_and_values_without_json() -> None:
    with pytest.raises(ExecutionError, match="twice"):
        map_to_json("m", [(1, "a"), ("1", "b")])
    with pytest.raises(ExecutionError, match="bytes"):
        map_to_json("m", [("k", b"x")])
    with pytest.raises(ExecutionError, match="list"):
        map_to_json("m", [([1], "x")])


def test_arrow_map_with_a_repeated_key_raises() -> None:
    entries = pa.map_(pa.string(), pa.int64())
    table = pa.table({"m": pa.array([[("k", 1), ("k", 2)]], entries)})
    with pytest.raises(ExecutionError, match="twice"):
        arrow_to_query_result(na.Array(table), LIMIT)


def test_duckdb_null_type_is_string() -> None:
    from duckdb.sqltypes import SQLNULL

    assert duckdb_column_type("n", SQLNULL) == ColumnType(kind="string")


def test_literal_columns_are_the_union_of_every_rows_keys() -> None:
    rows = [{"v": 1}, {"v": 2, "z": 1}, {"v": 3, "z": 2}]
    assert list(literal_schema(rows)) == ["v", "z"]
    with pytest.raises(ExecutionError, match="'z'"):
        literal_schema([{"v": 1}, {"v": 2, "z": 1}, {"v": 3, "z": "a"}])


def test_a_literal_column_of_integers_and_floats_holds_floats() -> None:
    result = ValuesAdapter().execute(
        ValuesQuery(rows=[{"x": 1, "xs": [1, 2.5]}, {"x": 2.5, "xs": [3]}])
    )
    assert result.schema["x"] == FLOAT
    assert result.data == [{"x": 1.0, "xs": [1.0, 2.5]}, {"x": 2.5, "xs": [3.0]}]
    assert all(isinstance(row["x"], float) for row in result.data)


def test_compile_reports_a_mixed_inline_chart_query() -> None:
    from dbt_charts.core.compile import compile

    result = compile(
        "id: b\ncharts:\n  c:\n    type: table\n    query:\n      type: values\n"
        "      rows:\n        - v: 1\n        - v: a\nrows:\n  - c\n"
    )
    assert result.board is None
    assert any("'v'" in d.message for d in result.errors)


@pytest.mark.parametrize(
    "module",
    ["duckdb_types", "cache_composition", "file_source_materializer"],
)
def test_execute_modules_import_standalone(module: str) -> None:
    subprocess.run(
        [sys.executable, "-c", f"import dbt_charts.core.execute.{module}"],
        check=True,
        capture_output=True,
    )


def test_duckdb_map_values_are_converted_by_their_own_type() -> None:
    result = _duckdb("select map {'a': '{\"x\": 1}'::json} j, map {'b': 12::bignum} n")
    assert result.error is None, result.error
    assert result.data == [{"j": {"a": {"x": 1}}, "n": {"b": 12}}]


def test_a_literal_struct_with_a_non_text_key_raises_a_column_error() -> None:
    from dbt_charts.core.compile import compile

    result = compile(
        "id: b\nqueries:\n  q:\n    rows:\n      - v: {1: a}\n"
        "rows:\n  - chart:\n      type: table\n      query: q\n"
    )
    assert result.board is None
    assert any("'v'" in d.message for d in result.errors)


@pytest.mark.parametrize(
    "key_type", [pa.struct([("a", pa.int64())]), pa.binary()], ids=["struct", "binary"]
)
def test_arrow_map_with_a_key_that_has_no_json_form_raises(
    key_type: pa.DataType,
) -> None:
    table = pa.table({"m": pa.array([], pa.map_(key_type, pa.int64()))})
    with pytest.raises(ExecutionError) as excinfo:
        arrow_to_query_result(na.Array(table), LIMIT)
    assert excinfo.value.code is ERR_RESULT_COLUMN_TYPE


def test_duckdb_map_keyed_by_a_bit_raises() -> None:
    result = _duckdb("select map {'0101'::bit: 1} as m")
    assert result.error is not None
    assert result.error.code is ERR_RESULT_COLUMN_TYPE


def test_map_json_values_are_decoded_on_the_file_source_and_composition_paths(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    import duckdb

    from dbt_charts.core.execute.cache_composition import rows_from_result

    sql = "select map {'a': '{\"x\": 1}'::json} m"
    (tmp_path / "s.csv").write_text("n\n1\n")
    rows, _ = FileSourceMaterializer(local_project(tmp_path)).materialize_and_run(
        CsvSourceConfig(type="csv", files={"s": "s.csv"}), sql, {}, "src"
    )
    assert rows == [{"m": {"a": {"x": 1}}}]
    assert rows_from_result(duckdb.connect().execute(sql)) == rows


def test_duckdb_timestamptz_declares_datetime_tz_with_aware_utc_cells() -> None:
    result = _duckdb(
        "select timestamptz '2024-01-01 12:00:00+00' tz, "
        "timestamp '2024-01-01 12:00:00' n, timestamp_ns '2024-01-01 12:00:00' ns"
    )
    assert result.error is None, result.error
    assert [result.schema[c].kind for c in ("tz", "n", "ns")] == [
        "datetime_tz",
        "datetime",
        "datetime",
    ]
    row = result.data[0]
    assert row["tz"] == datetime(2024, 1, 1, 12, tzinfo=UTC)
    assert row["tz"].tzinfo is UTC
    assert row["n"].tzinfo is None and row["ns"].tzinfo is None


def test_literal_schema_tells_naive_from_aware_and_rejects_a_mix() -> None:
    aware = datetime(2024, 1, 1, 12, tzinfo=timezone(timedelta(hours=2)))
    schema = literal_schema([{"n": datetime(2024, 1, 1), "a": aware}])
    assert [schema[c].kind for c in ("n", "a")] == ["datetime", "datetime_tz"]
    with pytest.raises(ExecutionError, match="mixes datetime, datetime_tz"):
        literal_schema([{"x": datetime(2024, 1, 1)}, {"x": aware}])


def test_aware_literal_cells_are_normalized_to_utc() -> None:
    aware = datetime(2024, 1, 1, 12, tzinfo=timezone(timedelta(hours=2)))
    result = ValuesAdapter().execute(ValuesQuery(rows=[{"t": aware}]))
    assert result.data[0]["t"] == datetime(2024, 1, 1, 10, tzinfo=UTC)
    assert result.data[0]["t"].utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("kind", "cell"),
    [
        ("datetime", datetime(2024, 1, 1, tzinfo=UTC)),
        ("datetime_tz", datetime(2024, 1, 1)),
    ],
)
def test_a_cell_that_disagrees_with_its_datetime_kind_is_rejected(
    kind: ColumnKind, cell: datetime
) -> None:
    with pytest.raises(ExecutionError) as raised:
        QueryResult(
            data=[{"t": cell}],
            columns=["t"],
            schema={"t": ColumnType(kind=kind)},
        )
    assert raised.value.code is ERR_RESULT_COLUMN_TYPE
    assert "'t'" in str(raised.value)


@pytest.mark.parametrize(
    ("arrow_type", "kind", "tz_aware"),
    [
        (pa.timestamp("us"), "datetime", False),
        (pa.timestamp("ns"), "datetime", False),
        (pa.timestamp("us", tz="UTC"), "datetime_tz", True),
        (pa.timestamp("ms", tz="America/New_York"), "datetime_tz", True),
    ],
)
def test_arrow_timestamp_declares_its_kind_by_timezone(
    arrow_type: pa.DataType, kind: str, tz_aware: bool
) -> None:
    table = pa.table({"t": pa.array([datetime(2024, 1, 1, 12)], arrow_type)})
    result = arrow_to_query_result(na.Array(table), LIMIT)
    assert result.schema["t"].kind == kind
    cell = result.data[0]["t"]
    assert (cell.utcoffset() == timedelta(0)) if tz_aware else cell.tzinfo is None
    if tz_aware:
        assert cell.tzinfo is UTC


def test_arrow_is_built_from_rows_by_kind() -> None:
    from dbt_charts.core.execute.arrow_ingest import infer_type

    naive = na.c_schema(infer_type([datetime(2024, 1, 1)]))
    aware = na.c_schema(infer_type([datetime(2024, 1, 1, tzinfo=UTC)]))
    assert (naive.format, aware.format) == ("tsu:", "tsu:UTC")
