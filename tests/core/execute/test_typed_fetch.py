"""dbt-adapter results keep the warehouse's declared types and nested cells."""

from __future__ import annotations

import datetime
import decimal
from types import SimpleNamespace
from typing import Any

import duckdb
import pyarrow as pa
import pytest
from clickhouse_connect.datatypes.registry import get_from_name
from dbt.adapters.base.connections import BaseConnectionManager
from google.cloud.bigquery import SchemaField

from dbt_charts.core.column_types import ColumnType
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_RESULT_COLUMN_TYPE,
    ERR_RESULT_NO_TYPE_READER,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    SUPPORTED_ADAPTER_TYPES,
)
from dbt_charts.core.execute.adapters.typed_fetch import (
    _CLIENTS,
    _FAMILIES,
    fetch_typed,
)

STRING = ColumnType(kind="string")
INTEGER = ColumnType(kind="integer")
JSON = ColumnType(kind="json")


class _AllOnlyCursor:
    """A Spark-style cursor: fetchall and no fetchmany."""

    def __init__(
        self, description: list[tuple[Any, ...]] | None, rows: list[tuple]
    ) -> None:
        self.description = description
        self.rows = rows

    def fetchall(self) -> list[tuple]:
        return list(self.rows)

    def nextset(self) -> bool:
        return False

    def close(self) -> None:
        pass


class _Cursor(_AllOnlyCursor):
    def __init__(
        self, description: list[tuple[Any, ...]] | None, rows: list[tuple]
    ) -> None:
        super().__init__(description, rows)
        self.fetchmany_sizes: list[int] = []

    def fetchmany(self, size: int) -> list[tuple]:
        self.fetchmany_sizes.append(size)
        return self.rows[:size]


class _PgTypeCursor:
    """pg_type answers that no unknown OID is an enum; SQL Server's
    sp_describe_first_result_set answers with the server's column types."""

    def __init__(self, described: list[str]) -> None:
        self.described = described
        self.describing = False

    def execute(self, sql: str, *params: Any) -> None:
        self.describing = sql.startswith("EXEC sp_describe")

    def fetchall(self) -> list[Any]:
        return (
            [
                SimpleNamespace(system_type_name=name, is_hidden=False)
                for name in self.described
            ]
            if self.describing
            else []
        )

    def close(self) -> None:
        pass


class _Connections:
    def __init__(self, cursor: _AllOnlyCursor, described: list[str]) -> None:
        self.cursor = cursor
        self.sent: list[str] = []
        self.described = described

    profile = SimpleNamespace(credentials=SimpleNamespace(retries=0, retry_all=False))

    def get_thread_connection(self) -> SimpleNamespace:
        return SimpleNamespace(
            handle=SimpleNamespace(cursor=lambda: _PgTypeCursor(self.described))
        )

    def _add_query_comment(self, sql: str) -> str:
        return f"/*attribution*/{sql}"

    def add_query(
        self, sql: str, auto_begin: bool = True
    ) -> tuple[None, _AllOnlyCursor]:
        assert auto_begin is False
        self.sent.append(sql)
        return None, self.cursor


def _adapter(
    cursor: _AllOnlyCursor, described: list[str] | None = None
) -> SimpleNamespace:
    if described is None:
        described = ["unused"] * len(cursor.description or [])
    return SimpleNamespace(connections=_Connections(cursor, described))


def _desc(*cols: tuple[str, Any]) -> list[tuple[Any, ...]]:
    return [(name, code, None, None, None, None, None) for name, code in cols]


def test_postgres_array_column_is_a_list_of_its_element_type() -> None:
    cursor = _Cursor(_desc(("id", 23), ("tags", 1009)), [(1, ["a", "b"])])
    adapter = _adapter(cursor)
    typed = fetch_typed(adapter, "postgres", "SELECT 1", None)
    assert typed.columns == ["id", "tags"]
    assert typed.rows == [(1, ["a", "b"])]
    assert typed.types == [INTEGER, ColumnType(kind="list", element=STRING)]
    assert adapter.connections.sent == ["/*attribution*/SELECT 1"]


def test_postgres_limit_bounds_the_fetch() -> None:
    cursor = _Cursor(_desc(("id", 23)), [(1,), (2,), (3,)])
    typed = fetch_typed(_adapter(cursor), "postgres", "SELECT 1", 2)
    assert typed.rows == [(1,), (2,)]
    assert cursor.fetchmany_sizes == [2]


def test_postgres_unmapped_type_names_the_column_and_the_code() -> None:
    cursor = _Cursor(_desc(("gap", 1186)), [])  # interval
    with pytest.raises(ExecutionError) as excinfo:
        fetch_typed(_adapter(cursor), "postgres", "SELECT 1", None)
    assert excinfo.value.code is ERR_RESULT_COLUMN_TYPE
    assert "'gap'" in str(excinfo.value)
    assert "1186" in str(excinfo.value)


def test_snowflake_decodes_exactly_the_declared_semi_structured_columns() -> None:
    # type codes: TEXT 2, ARRAY 10; the TEXT cell looks like JSON and stays text
    cursor = _Cursor(
        _desc(("txt", 2), ("arr", 10)), [("[1, 2]", '["a", 1, {"k": null}]')]
    )
    typed = fetch_typed(_adapter(cursor), "snowflake", "SELECT 1", None)
    assert typed.rows == [("[1, 2]", ["a", 1, {"k": None}])]
    assert typed.types == [STRING, JSON]


def test_snowflake_does_not_comment_twice() -> None:
    # dbt-snowflake's add_query applies the query comment itself
    adapter = _adapter(_Cursor(_desc(("n", 2)), []))
    fetch_typed(adapter, "snowflake", "SELECT 1", None)
    assert adapter.connections.sent == ["SELECT 1"]


def test_redshift_super_is_decoded_by_its_declared_type() -> None:
    cursor = _Cursor(_desc(("s", 4000), ("xs", 1007)), [('{"a": [1]}', [1, 2])])
    typed = fetch_typed(_adapter(cursor), "redshift", "SELECT 1", None)
    assert typed.rows == [({"a": [1]}, [1, 2])]
    assert typed.types == [JSON, ColumnType(kind="list", element=INTEGER)]


def test_duckdb_reads_nested_types_from_the_cursor() -> None:
    cursor = duckdb.connect().cursor()
    cursor.execute("SELECT [1, 2] AS xs, {'a': 'x'} AS s")

    class Connections:
        def _add_query_comment(self, sql: str) -> str:
            return sql

        def add_query(self, sql: str, auto_begin: bool = True) -> tuple[None, Any]:
            return None, cursor

    typed = fetch_typed(SimpleNamespace(connections=Connections()), "duckdb", "", None)
    assert typed.rows == [([1, 2], {"a": "x"})]
    assert typed.types == [
        ColumnType(kind="list", element=INTEGER),
        ColumnType(kind="struct", fields=(("a", STRING),)),
    ]


class _ArrowCursor:
    """A databricks-sql cursor: results come back as a pyarrow Table. dbt-databricks
    hands it back wrapped in a ``CursorWrapper`` that has no Arrow fetch."""

    query_id = "q"

    def __init__(self, table: pa.Table) -> None:
        self.table = table
        self.fetchmany_sizes: list[int] = []
        self.closed = False
        self.connection = SimpleNamespace(get_session_id_hex=lambda: "s")

    def fetchall_arrow(self) -> pa.Table:
        return self.table

    def fetchmany_arrow(self, size: int) -> pa.Table:
        self.fetchmany_sizes.append(size)
        return self.table.slice(0, size)

    def close(self) -> None:
        self.closed = True


def test_databricks_arrow_declares_timestamp_ntz_and_arrays_by_arrow_type() -> None:
    instant = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
    table = pa.table(
        {
            "tz": pa.array([instant], pa.timestamp("us", tz="Etc/UTC")),
            "ntz": pa.array([instant.replace(tzinfo=None)], pa.timestamp("us")),
            "arr": pa.array([[1, 2]], pa.list_(pa.int32())),
        }
    )
    handle = pytest.importorskip("dbt.adapters.databricks.handle")
    cursor = _ArrowCursor(table)
    typed = fetch_typed(
        _adapter(handle.CursorWrapper(cursor), []), "databricks", "SELECT 1", 5
    )
    assert typed.types == [
        ColumnType(kind="datetime_tz"),
        ColumnType(kind="datetime"),
        ColumnType(kind="list", element=INTEGER),
    ]
    assert typed.rows == [(instant, instant.replace(tzinfo=None), [1, 2])]
    assert typed.rows[0][0].utcoffset() == datetime.timedelta(0)
    assert cursor.fetchmany_sizes == [5]
    assert cursor.closed


def test_spark_reads_with_fetchall_and_decodes_hive_json_text() -> None:
    cursor = _AllOnlyCursor(
        _desc(("a", "ARRAY_TYPE"), ("s", "STRING_TYPE")), [('["x", "y"]', "[1]")]
    )
    typed = fetch_typed(_adapter(cursor), "spark", "SELECT 1", None)
    assert typed.rows == [(["x", "y"], "[1]")]
    assert typed.types == [JSON, STRING]


class _RowIterator:
    def __init__(self, schema: list[SchemaField], rows: list[tuple]) -> None:
        self.schema = schema
        self._rows = rows

    def __iter__(self) -> Any:
        return (SimpleNamespace(values=lambda row=row: row) for row in self._rows)


def test_bigquery_repeated_and_record_fields() -> None:
    schema = [
        SchemaField("n", "INTEGER"),
        SchemaField("tags", "STRING", mode="REPEATED"),
        SchemaField(
            "pt",
            "RECORD",
            fields=[
                SchemaField("x", "FLOAT"),
                SchemaField("ys", "INTEGER", "REPEATED"),
            ],
        ),
    ]
    iterator = _RowIterator(schema, [(1, ["a"], {"x": 1.5, "ys": [2]})])
    seen: list[tuple[str, int | None]] = []

    def raw_execute_with_comment(sql: str, limit: int | None = None) -> tuple:
        seen.append((sql, limit))
        return None, iterator

    adapter = SimpleNamespace(
        connections=SimpleNamespace(raw_execute_with_comment=raw_execute_with_comment)
    )
    typed = fetch_typed(adapter, "bigquery", "SELECT 1", 7)
    assert seen == [("SELECT 1", 7)]
    assert typed.rows == [(1, ["a"], {"x": 1.5, "ys": [2]})]
    assert typed.types == [
        INTEGER,
        ColumnType(kind="list", element=STRING),
        ColumnType(
            kind="struct",
            fields=(
                ("x", ColumnType(kind="float")),
                ("ys", ColumnType(kind="list", element=INTEGER)),
            ),
        ),
    ]


def test_bigquery_unmapped_field_type_names_the_column() -> None:
    iterator = _RowIterator([SchemaField("span", "INTERVAL")], [])
    adapter = SimpleNamespace(
        connections=SimpleNamespace(
            raw_execute_with_comment=lambda sql, limit=None: (None, iterator)
        )
    )
    with pytest.raises(ExecutionError) as excinfo:
        fetch_typed(adapter, "bigquery", "SELECT 1", None)
    assert excinfo.value.code is ERR_RESULT_COLUMN_TYPE
    assert "'span'" in str(excinfo.value)
    assert "INTERVAL" in str(excinfo.value)


def test_a_dialect_without_a_type_reader_raises_before_running_the_query() -> None:
    adapter = _adapter(_Cursor([], []))
    with pytest.raises(ExecutionError) as excinfo:
        fetch_typed(adapter, "oracle", "SELECT 1", None)
    assert excinfo.value.code is ERR_RESULT_NO_TYPE_READER
    assert "oracle" in str(excinfo.value)
    assert adapter.connections.sent == []


def test_mysql_scalars_and_json_by_declared_type() -> None:
    # LONG 3, VAR_STRING 253, JSON 245: the string cell that looks like JSON stays text
    cursor = _Cursor(
        _desc(("n", 3), ("s", 253), ("j", 245)), [(1, "[1]", '{"a": [1, 2]}')]
    )
    typed = fetch_typed(_adapter(cursor), "mysql", "SELECT 1", None)
    assert typed.rows == [(1, "[1]", {"a": [1, 2]})]
    assert typed.types == [INTEGER, STRING, JSON]


def test_mysql_blob_code_is_split_by_the_binary_flag() -> None:
    def flagged(name: str, flags: int) -> tuple[Any, ...]:
        return (name, 252, None, None, None, None, True, flags)

    cursor = _Cursor([flagged("txt", 16), flagged("blob", 144)], [("a", b"b")])
    typed = fetch_typed(_adapter(cursor), "mysql", "SELECT 1", None)
    assert typed.types == [STRING, ColumnType(kind="bytes")]


def test_mysql_blob_without_reported_flags_raises() -> None:
    cursor = _Cursor(_desc(("body", 252)), [])
    with pytest.raises(ExecutionError, match="'body'"):
        fetch_typed(_adapter(cursor), "mysql", "SELECT 1", None)


def test_athena_scalars_and_driver_parsed_json() -> None:
    cursor = _Cursor(
        _desc(("n", "bigint"), ("t", "timestamp"), ("j", "json")),
        [(1, datetime.datetime(2020, 1, 1), {"a": 1})],
    )
    typed = fetch_typed(_adapter(cursor), "athena", "SELECT 1", 10)
    assert typed.types == [INTEGER, ColumnType(kind="datetime"), JSON]
    assert typed.rows == [(1, datetime.datetime(2020, 1, 1), {"a": 1})]


def test_athena_array_must_be_cast_to_json() -> None:
    cursor = _Cursor(_desc(("tags", "array")), [])
    with pytest.raises(ExecutionError, match=r"'tags'.*array.*CAST\(tags AS JSON\)"):
        fetch_typed(_adapter(cursor), "athena", "SELECT 1", None)


def test_athena_json_column_passes_through_parsed() -> None:
    cursor = _Cursor(_desc(("j", "json")), [({"a": [1]},)])
    typed = fetch_typed(_adapter(cursor), "athena", "SELECT 1", None)
    assert typed.types == [JSON]
    assert typed.rows == [({"a": [1]},)]


def test_null_typed_columns_are_declared_string() -> None:
    mysql = _Cursor(_desc(("n", 6)), [(None,)])
    athena = _Cursor(_desc(("n", "unknown")), [(None,)])
    snowflake_geo = _Cursor(_desc(("g", 14)), [('{"type": "Point"}',)])
    for dialect, cursor in (
        ("mysql", mysql),
        ("athena", athena),
        ("snowflake", snowflake_geo),
    ):
        assert fetch_typed(_adapter(cursor), dialect, "SELECT 1", None).types == [
            STRING
        ]
    nothing = _ChResult(["n", "m"], ["Nothing", "Nullable(Nothing)"], [(None, None)])
    typed = fetch_typed(
        _clickhouse_adapter(nothing, []), "clickhouse", "SELECT 1", None
    )
    assert typed.types == [STRING, STRING]


def test_sqlserver_drains_trailing_result_sets() -> None:
    class Failing(_Cursor):
        def nextset(self) -> bool:
            raise RuntimeError("Invalid column name 'later'")

    cursor = Failing(_desc(("n", int)), [(1,)])
    with pytest.raises(RuntimeError, match="later"):
        fetch_typed(_adapter(cursor), "sqlserver", "SELECT 1; SELECT later", None)


def test_sqlserver_reads_pyodbc_type_classes() -> None:
    cursor = _Cursor(
        _desc(("n", int), ("amt", decimal.Decimal), ("at", datetime.datetime)), []
    )
    typed = fetch_typed(_adapter(cursor), "sqlserver", "SELECT 1", 3)
    assert typed.types == [
        INTEGER,
        ColumnType(kind="decimal"),
        ColumnType(kind="datetime"),
    ]
    assert cursor.fetchmany_sizes == [3]


def test_sqlserver_unmapped_type_class_raises() -> None:
    cursor = _Cursor(_desc(("u", complex)), [])
    with pytest.raises(ExecutionError, match="'u'.*complex"):
        fetch_typed(_adapter(cursor), "sqlserver", "SELECT 1", None)


class _ChResult:
    def __init__(self, names: list[str], types: list[str], rows: list[tuple]) -> None:
        self.column_names = names
        self.column_types = [get_from_name(t) for t in types]
        self.result_set = rows


def _clickhouse_adapter(result: Any, sent: list[str]) -> SimpleNamespace:
    def query(sql: str, query_id: str, tz_mode: str) -> Any:
        assert query_id
        assert tz_mode == "schema"
        sent.append(sql)
        return result

    handle = SimpleNamespace(query=query)
    return SimpleNamespace(
        connections=SimpleNamespace(
            get_thread_connection=lambda: SimpleNamespace(handle=handle),
            _add_query_comment=lambda sql: f"/*c*/{sql}",
        )
    )


def test_clickhouse_nested_types_come_from_the_client_result() -> None:
    result = _ChResult(
        ["n", "tags", "pt"],
        [
            "Nullable(UInt8)",
            "Array(LowCardinality(String))",
            "Tuple(x Float64, y Int8)",
        ],
        [(1, ["a"], {"x": 1.5, "y": 2})],
    )
    sent: list[str] = []
    typed = fetch_typed(
        _clickhouse_adapter(result, sent), "clickhouse", "SELECT 1", None
    )
    assert sent == ["/*c*/SELECT 1"]
    assert typed.rows == [(1, ["a"], {"x": 1.5, "y": 2})]
    assert typed.types == [
        INTEGER,
        ColumnType(kind="list", element=STRING),
        ColumnType(
            kind="struct",
            fields=(("x", ColumnType(kind="float")), ("y", INTEGER)),
        ),
    ]


def test_clickhouse_unmapped_type_and_native_client_raise() -> None:
    uuid_result = _ChResult(["id"], ["UUID"], [])
    with pytest.raises(ExecutionError, match="'id'.*UUID"):
        fetch_typed(
            _clickhouse_adapter(uuid_result, []), "clickhouse", "SELECT 1", None
        )
    native = SimpleNamespace(column_names=["a"], result_set=[])
    with pytest.raises(ExecutionError, match="native protocol") as native_error:
        fetch_typed(_clickhouse_adapter(native, []), "clickhouse", "SELECT 1", None)
    assert native_error.value.code is ERR_RESULT_NO_TYPE_READER


def test_dbts_query_comment_hook_reaches_the_sent_sql() -> None:
    """The real dbt hook, so a dbt release that renames it fails here."""
    from dbt.adapters.bigquery.connections import BigQueryConnectionManager
    from dbt.adapters.postgres.connections import PostgresConnectionManager

    header = SimpleNamespace(add=lambda sql: f"/* attribution */ {sql}")
    cursor = _Cursor(_desc(("n", 23)), [(1,)])
    sent: list[str] = []

    class Postgres(PostgresConnectionManager):
        def add_query(self, sql: str, auto_begin: bool = True) -> tuple[None, Any]:
            sent.append(sql)
            return None, cursor

    class BigQuery(BigQueryConnectionManager):
        def raw_execute(self, sql: str, **_: Any) -> tuple[None, Any]:
            sent.append(sql)
            return None, _RowIterator([SchemaField("n", "INTEGER")], [(1,)])

    for manager_class, dialect in ((Postgres, "postgres"), (BigQuery, "bigquery")):
        manager = object.__new__(manager_class)
        assert isinstance(manager, BaseConnectionManager)
        manager.query_header = header
        fetch_typed(SimpleNamespace(connections=manager), dialect, "SELECT 1", None)
    assert sent == ["/* attribution */ SELECT 1"] * 2


@pytest.mark.parametrize("adapter_type", sorted(SUPPORTED_ADAPTER_TYPES))
def test_every_supported_dbt_adapter_type_has_a_reader(adapter_type: str) -> None:
    dialect = get_dialect(adapter_type).name
    assert dialect in _FAMILIES or dialect in _CLIENTS


def test_spark_cursor_is_not_closed_because_it_is_the_connection() -> None:
    class Wrapper(_AllOnlyCursor):
        handle_open = True

        def close(self) -> None:
            self.handle_open = False

    cursor = Wrapper(_desc(("s", "STRING_TYPE")), [("x",)])
    fetch_typed(_adapter(cursor), "spark", "SELECT 1", None)
    assert cursor.handle_open


def test_snowflake_fixed_splits_on_scale() -> None:
    cursor = _Cursor(
        [("whole", 0, None, None, 38, 0, True), ("money", 0, None, None, 10, 2, True)],
        [],
    )
    typed = fetch_typed(_adapter(cursor), "snowflake", "SELECT 1", None)
    assert typed.types == [INTEGER, ColumnType(kind="decimal")]


def test_sqlserver_skips_leading_statements_without_rows() -> None:
    class TwoSets(_Cursor):
        def __init__(self) -> None:
            super().__init__(None, [(1,)])
            self.after_nextset = _desc(("n", int))

        def nextset(self) -> bool:
            if self.description is not None:
                return False
            self.description = self.after_nextset
            return True

    typed = fetch_typed(
        _adapter(TwoSets(), ["int"]), "sqlserver", "SET NOCOUNT ON", None
    )
    assert typed.columns == ["n"]
    assert typed.rows == [(1,)]


def test_spark_odbc_wrapper_reports_python_type_classes() -> None:
    cursor = _AllOnlyCursor(_desc(("s", str), ("n", int)), [("a", 1)])
    typed = fetch_typed(_adapter(cursor), "spark", "SELECT 1", None)
    assert typed.types == [STRING, INTEGER]


def test_hive_null_and_variant_columns_are_declared() -> None:
    cursor = _Cursor(_desc(("x", "null"), ("v", "variant")), [(None, '{"a": 1}')])
    typed = fetch_typed(_adapter(cursor), "spark", "SELECT 1", None)
    assert typed.types == [STRING, JSON]
    assert typed.rows == [(None, {"a": 1})]
    void = _AllOnlyCursor(_desc(("x", "void")), [])
    assert fetch_typed(_adapter(void), "spark", "SELECT 1", None).types == [STRING]


def test_spark_session_simple_string_types_are_parsed() -> None:
    cursor = _AllOnlyCursor(
        _desc(("xs", "array<int>"), ("s", "struct<Id:int,tags:array<string>>")),
        [([1, 2], ((7, ["a"])))],
    )
    typed = fetch_typed(_adapter(cursor), "spark", "SELECT 1", None)
    assert typed.types == [
        ColumnType(kind="list", element=INTEGER),
        ColumnType(
            kind="struct",
            fields=(
                ("Id", INTEGER),
                ("tags", ColumnType(kind="list", element=STRING)),
            ),
        ),
    ]
    assert typed.rows == [([1, 2], {"Id": 7, "tags": ["a"]})]


def test_postgres_enum_oids_are_declared_string_by_pg_type() -> None:
    asked: list[Any] = []

    class PgCursor:
        def execute(self, sql: str, params: tuple) -> None:
            asked.append(params)

        def fetchall(self) -> list[tuple[int]]:
            return [(90001,)]

        def close(self) -> None:
            pass

    adapter = _adapter(_Cursor(_desc(("mood", 90001), ("n", 23)), [("sad", 1)]))
    adapter.connections.get_thread_connection = lambda: SimpleNamespace(
        handle=SimpleNamespace(cursor=PgCursor)
    )
    typed = fetch_typed(adapter, "postgres", "SELECT 1", None)
    assert typed.types == [STRING, INTEGER]
    assert asked == [([90001],)]

    other = _adapter(_Cursor(_desc(("x", 90002)), []))
    other.connections.get_thread_connection = adapter.connections.get_thread_connection
    with pytest.raises(ExecutionError, match="'x'.*90002"):
        fetch_typed(other, "postgres", "SELECT 1", None)


def test_redshift_retries_what_its_execute_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    cursor = _Cursor(_desc(("n", 23)), [(1,)])
    adapter = _adapter(cursor)
    connections = adapter.connections
    connections.profile = SimpleNamespace(
        credentials=SimpleNamespace(retries=1, retry_all=False)
    )
    connections.get_thread_connection = lambda: "conn"
    reopened: list[str] = []
    connections.close = lambda conn: reopened.append("close")
    connections.open = lambda conn: reopened.append("open")
    calls = []
    add_query = connections.add_query

    def flaky(sql: str, auto_begin: bool = True) -> tuple[None, _Cursor]:
        calls.append(sql)
        if len(calls) == 1:
            raise RuntimeError("could not open relation with OID 42")
        return add_query(sql, auto_begin)

    connections.add_query = flaky
    typed = fetch_typed(adapter, "redshift", "SELECT 1", None)
    assert typed.rows == [(1,)]
    assert len(calls) == 2
    assert reopened == ["close", "open"]

    calls.clear()
    connections.add_query = lambda *_a, **_k: (_ for _ in ()).throw(
        RuntimeError("syntax error")
    )
    with pytest.raises(RuntimeError, match="syntax error"):
        fetch_typed(adapter, "redshift", "SELECT 1", None)


def test_duckdb_cells_match_their_declarations() -> None:
    cursor = duckdb.connect().cursor()
    cursor.execute(
        "SELECT '{\"a\": [1]}'::JSON AS j, uuid() AS id, "
        "[{'k': '{}'::JSON}] AS nested, [1, 2]::INTEGER[2] AS fixed"
    )

    class Connections:
        def _add_query_comment(self, sql: str) -> str:
            return sql

        def add_query(self, sql: str, auto_begin: bool = True) -> tuple[None, Any]:
            return None, cursor

    typed = fetch_typed(SimpleNamespace(connections=Connections()), "duckdb", "", None)
    row = typed.rows[0]
    assert row[0] == {"a": [1]}
    assert isinstance(row[1], str)
    assert row[2] == [{"k": {}}]
    assert row[3] == [1, 2]
    assert typed.types[3] == ColumnType(kind="list", element=INTEGER)


def test_clickhouse_map_cells_become_json_objects_at_any_depth() -> None:
    result = _ChResult(
        ["m", "xs"],
        ["Map(UInt8, Date)", "Array(Map(String, Decimal(5, 2)))"],
        [({1: datetime.date(2020, 1, 2)}, [{"a": decimal.Decimal("1.5")}])],
    )
    typed = fetch_typed(_clickhouse_adapter(result, []), "clickhouse", "SELECT 1", None)
    assert typed.rows == [({"1": "2020-01-02"}, [{"a": 1.5}])]
    assert typed.types[0] == JSON


def test_spark_nested_text_that_is_not_json_names_the_column() -> None:
    cursor = _AllOnlyCursor(_desc(("tags", "ARRAY_TYPE")), [("[a, b]",)])
    with pytest.raises(ExecutionError, match="'tags'.*to_json") as raised:
        fetch_typed(_adapter(cursor), "spark", "SELECT 1", None)
    assert raised.value.code is ERR_RESULT_COLUMN_TYPE


def test_mysql_blob_code_uses_the_binary_charset_when_reported() -> None:
    def column(name: str, charset: int) -> tuple[Any, ...]:
        return (name, 252, None, None, None, None, True, 0, charset)

    cursor = _Cursor([column("txt", 255), column("blob", 63)], [])
    typed = fetch_typed(_adapter(cursor), "mysql", "SELECT 1", None)
    assert typed.types == [STRING, ColumnType(kind="bytes")]


_PLUS_TWO = datetime.timezone(datetime.timedelta(hours=2))
_NOON_UTC = datetime.datetime(2024, 1, 1, 12, tzinfo=datetime.UTC)
_NOON_PLUS_TWO = datetime.datetime(2024, 1, 1, 14, tzinfo=_PLUS_TWO)
_NOON_NAIVE = datetime.datetime(2024, 1, 1, 12)
DATETIME = ColumnType(kind="datetime")
DATETIME_TZ = ColumnType(kind="datetime_tz")


def test_snowflake_zoned_timestamps_are_utc_instants_and_ntz_stays_naive() -> None:
    # type codes: TIMESTAMP_LTZ 6, TIMESTAMP_TZ 7, TIMESTAMP_NTZ 8
    cursor = _Cursor(
        _desc(("ltz", 6), ("tz", 7), ("ntz", 8)),
        [(_NOON_PLUS_TWO, _NOON_PLUS_TWO, _NOON_NAIVE)],
    )
    typed = fetch_typed(_adapter(cursor), "snowflake", "SELECT 1", None)
    assert typed.types == [DATETIME_TZ, DATETIME_TZ, DATETIME]
    assert typed.rows == [(_NOON_UTC, _NOON_UTC, _NOON_NAIVE)]
    assert typed.rows[0][0].utcoffset() == datetime.timedelta(0)
    assert typed.rows[0][2].tzinfo is None


def test_postgres_timestamptz_is_a_utc_instant_scalar_and_array() -> None:
    # OIDs: timestamp 1114, timestamptz 1184, timestamptz[] 1185
    cursor = _Cursor(
        _desc(("naive", 1114), ("zoned", 1184), ("zs", 1185)),
        [(_NOON_NAIVE, _NOON_PLUS_TWO, [_NOON_PLUS_TWO, None])],
    )
    typed = fetch_typed(_adapter(cursor), "postgres", "SELECT 1", None)
    assert typed.types == [
        DATETIME,
        DATETIME_TZ,
        ColumnType(kind="list", element=DATETIME_TZ),
    ]
    assert typed.rows == [(_NOON_NAIVE, _NOON_UTC, [_NOON_UTC, None])]
    assert typed.rows[0][1].utcoffset() == datetime.timedelta(0)
    assert typed.rows[0][0].tzinfo is None


def test_bigquery_timestamp_is_an_instant_and_datetime_is_naive_at_any_depth() -> None:
    schema = [
        SchemaField("ts", "TIMESTAMP"),
        SchemaField("dt", "DATETIME"),
        SchemaField("tss", "TIMESTAMP", mode="REPEATED"),
        SchemaField("rec", "RECORD", fields=[SchemaField("at", "TIMESTAMP")]),
    ]
    iterator = _RowIterator(
        schema, [(_NOON_UTC, _NOON_NAIVE, [_NOON_UTC], {"at": _NOON_UTC})]
    )
    adapter = SimpleNamespace(
        connections=SimpleNamespace(
            raw_execute_with_comment=lambda sql, limit=None: (None, iterator)
        )
    )
    typed = fetch_typed(adapter, "bigquery", "SELECT 1", None)
    assert typed.types == [
        DATETIME_TZ,
        DATETIME,
        ColumnType(kind="list", element=DATETIME_TZ),
        ColumnType(kind="struct", fields=(("at", DATETIME_TZ),)),
    ]
    assert typed.rows == [(_NOON_UTC, _NOON_NAIVE, [_NOON_UTC], {"at": _NOON_UTC})]


def test_clickhouse_zoned_datetime_is_a_utc_instant_and_bare_stays_naive() -> None:
    result = _ChResult(
        ["utc", "berlin", "bare"],
        ["DateTime('UTC')", "DateTime64(3, 'Europe/Berlin')", "DateTime"],
        [(_NOON_UTC, _NOON_PLUS_TWO, _NOON_NAIVE)],
    )
    typed = fetch_typed(_clickhouse_adapter(result, []), "clickhouse", "SELECT 1", None)
    assert typed.types == [DATETIME_TZ, DATETIME_TZ, DATETIME]
    assert typed.rows == [(_NOON_UTC, _NOON_UTC, _NOON_NAIVE)]


def test_spark_session_timestamp_is_wall_clock_datetime() -> None:
    cursor = _AllOnlyCursor(
        _desc(("t", "TIMESTAMP_TYPE"), ("n", "TIMESTAMP_NTZ_TYPE")),
        [(_NOON_NAIVE, _NOON_NAIVE)],
    )
    typed = fetch_typed(_adapter(cursor), "spark", "SELECT 1", None)
    assert typed.types == [DATETIME, DATETIME]
    assert typed.rows == [(_NOON_NAIVE, _NOON_NAIVE)]


def test_sqlserver_datetimeoffset_is_an_instant_by_the_servers_description() -> None:
    cursor = _Cursor(
        _desc(("at", datetime.datetime), ("wall", datetime.datetime)),
        [(_NOON_PLUS_TWO, _NOON_NAIVE)],
    )
    adapter = _adapter(cursor, ["datetimeoffset(7)", "datetime2(7)"])
    typed = fetch_typed(adapter, "sqlserver", "SELECT 1", None)
    assert typed.types == [DATETIME_TZ, DATETIME]
    assert typed.rows == [(_NOON_UTC, _NOON_NAIVE)]
