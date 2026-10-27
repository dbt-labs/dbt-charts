"""Tests for SnowparkAdapter — SQL execution through an injected live Snowpark session.

Covers:
- ``_can_execute`` claims only SQL resolved against a ``snowflake`` source
- ``_execute`` prepares SQL the way ``SqlAdapter`` does (Jinja variables, param
  inlining) and sends the result through the injected session
- Column names come back lowercased regardless of the session's own casing
- A session failure surfaces as a QueryResult error, never an empty success
- The module imports nothing from ``snowflake.*`` at load time
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import ColumnType
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
    SnowflakeSourceConfig,
)
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_RESULT_DUPLICATE_COLUMN,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.snowpark_adapter import SnowparkAdapter
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS


class _FakeRow:
    """Duck-typed stand-in for a Snowpark ``Row`` — iterates its values."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __iter__(self) -> Iterator[Any]:
        return iter(self._data.values())


# Named after the snowflake.snowpark.types classes the adapter keys on.
class LongType:
    pass


class StringType:
    pass


class VariantType:
    pass


class NullType:
    pass


class VectorType:
    pass


class GeographyType:
    pass


@dataclass
class DecimalType:
    scale: int


@dataclass
class _Zone:
    name: str


@dataclass
class TimestampType:
    tz: _Zone


@dataclass
class _FakeField:
    datatype: Any


@dataclass
class _FakeSchema:
    fields: list[_FakeField]


class _FakeResultSet:
    def __init__(
        self,
        rows: list[_FakeRow],
        fetched: list[_FakeRow],
        columns: list[str],
        datatypes: list[Any],
    ) -> None:
        self._rows = rows
        self._fetched = fetched
        self.columns = columns
        self.schema = _FakeSchema([_FakeField(datatype) for datatype in datatypes])

    def to_local_iterator(self) -> Iterator[_FakeRow]:
        for row in self._rows:
            self._fetched.append(row)
            yield row


class _FakeSession:
    """Duck-typed stand-in for a Snowpark ``Session`` that records every SQL
    string it was asked to run."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        raises: Exception | None = None,
        database: str | None = '"ANALYTICS"',
        schema: str | None = '"PUBLIC"',
        columns: list[str] | None = None,
        datatypes: list[Any] | None = None,
    ) -> None:
        self._datatypes = datatypes
        self._rows = [_FakeRow(r) for r in (rows or [])]
        self._columns = columns or list((rows or [{}])[0])
        self._raises = raises
        self._database = database
        self._schema = schema
        self.received_sql: list[str] = []
        self.fetched: list[_FakeRow] = []

    def get_current_database(self) -> str | None:
        return self._database

    def get_current_schema(self) -> str | None:
        return self._schema

    def sql(self, sql: str) -> _FakeResultSet:
        self.received_sql.append(sql)
        if self._raises is not None:
            raise self._raises
        datatypes = self._datatypes or [
            LongType() if isinstance(value, int) else StringType()
            for value in (self._rows[0] if self._rows else [None] * len(self._columns))
        ]
        return _FakeResultSet(self._rows, self.fetched, self._columns, datatypes)


def _snowflake_source() -> SnowflakeSourceConfig:
    return SnowflakeSourceConfig(
        type="snowflake", account="xy12345", database="analytics"
    )


def _duckdb_source() -> DuckDBSourceConfig:
    return DuckDBSourceConfig(type="duckdb", path=":memory:")


class TestCanExecute:
    def test_claims_sql_against_snowflake_source(self, tmp_path: Any) -> None:
        adapter = SnowparkAdapter(
            session=_FakeSession(), project=FilesystemProject(tmp_path)
        )
        query = SqlQuery(sql="SELECT 1", source="sf")
        assert adapter.can_execute(query, _snowflake_source()) is True

    def test_declines_non_snowflake_source(self, tmp_path: Any) -> None:
        adapter = SnowparkAdapter(
            session=_FakeSession(), project=FilesystemProject(tmp_path)
        )
        query = SqlQuery(sql="SELECT 1", source="db")
        assert adapter.can_execute(query, _duckdb_source()) is False

    def test_declines_when_source_config_is_none(self, tmp_path: Any) -> None:
        adapter = SnowparkAdapter(
            session=_FakeSession(), project=FilesystemProject(tmp_path)
        )
        query = SqlQuery(sql="SELECT 1", source="sf")
        assert adapter.can_execute(query, None) is False


class TestExecute:
    def test_sends_rendered_sql_to_the_session(self, tmp_path: Any) -> None:
        session = _FakeSession(rows=[{"ID": 1}])
        adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
        query = SqlQuery(sql="SELECT * FROM t WHERE id = {{ id }}", source="sf")
        result = adapter.execute(
            query, variables={"id": 5}, source_config=_snowflake_source()
        )
        assert result.is_success, result.error
        assert session.received_sql == ["SELECT * FROM t WHERE id = 5"]

    def test_lowercases_uppercase_snowpark_columns(self, tmp_path: Any) -> None:
        session = _FakeSession(rows=[{"ID": 1, "NAME": "alice"}])
        adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
        query = SqlQuery(sql="SELECT id, name FROM t", source="sf")
        result = adapter.execute(query, source_config=_snowflake_source())
        assert result.is_success, result.error
        assert result.columns == ["id", "name"]
        assert result.data == [{"id": 1, "name": "alice"}]

    def test_session_failure_returns_error_not_empty_success(
        self, tmp_path: Any
    ) -> None:
        session = _FakeSession(raises=RuntimeError("SQL compilation error: bad table"))
        adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
        query = SqlQuery(sql="SELECT * FROM missing", source="sf")
        result = adapter.execute(query, source_config=_snowflake_source())
        assert result.is_success is False
        assert result.error is not None and result.error.code is ERR_WAREHOUSE_RUNTIME
        assert result.data == []
        assert "bad table" in str(result.error)

    def test_setup_sql_is_not_supported(self, tmp_path: Any) -> None:
        session = _FakeSession(rows=[{"X": 1}])
        adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
        query = SqlQuery(
            sql="SELECT 1", source="sf", setup_sql="CREATE TEMP TABLE x AS SELECT 1"
        )
        result = adapter.execute(query, source_config=_snowflake_source())
        assert result.is_success is False
        assert session.received_sql == []

    def test_param_render_dialect_is_inline_placeholders(self, tmp_path: Any) -> None:
        """Params flatten to literals here too, same as SqlAdapter — Snowpark's
        own driver never binds them."""
        adapter = SnowparkAdapter(
            session=_FakeSession(), project=FilesystemProject(tmp_path)
        )
        assert adapter.param_render_dialect(_snowflake_source()) is INLINE_PLACEHOLDERS


def test_module_has_no_top_level_snowflake_import() -> None:
    """The adapter must be importable with no snowflake package installed at all."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import dbt_charts.core.execute.adapters.snowpark_adapter; "
            "import sys; print('snowflake' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"


class TestRowLimit:
    def test_ceiling_bounds_the_fetch_and_flags_truncation(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "3")
        session = _FakeSession(rows=[{"ID": i} for i in range(10)])
        adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))

        result = adapter.execute(
            SqlQuery(sql="SELECT id FROM t", source="sf"),
            source_config=_snowflake_source(),
        )

        assert len(session.fetched) == 4
        assert session.received_sql == ["SELECT id FROM t"]
        assert len(result.data) == 3
        assert result.truncated_reason == "max_rows"

    def test_authors_own_limit_is_not_flagged_as_truncation(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "100")
        session = _FakeSession(rows=[{"ID": i} for i in range(10)])
        adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))

        result = adapter.execute(
            SqlQuery(sql="SELECT id FROM t", source="sf", limit=2),
            source_config=_snowflake_source(),
        )

        assert len(session.fetched) == 2
        assert len(result.data) == 2
        assert result.truncated_reason is None


class _MidStreamFailure:
    """A session whose stream raises after yielding one row."""

    def __init__(self) -> None:
        self.stream_closed = False

    def sql(self, sql: str) -> _MidStreamFailure:
        return self

    def get_current_database(self) -> str | None:
        return '"ANALYTICS"'

    def get_current_schema(self) -> str | None:
        return '"PUBLIC"'

    columns = ["ID"]

    def to_local_iterator(self) -> Iterator[_FakeRow]:
        try:
            yield _FakeRow({"ID": 1})
            raise RuntimeError("connection reset")
        finally:
            self.stream_closed = True


def test_mid_stream_failure_returns_a_warehouse_error(tmp_path: Any) -> None:
    adapter = SnowparkAdapter(
        session=_MidStreamFailure(), project=FilesystemProject(tmp_path)
    )
    result = adapter.execute(
        SqlQuery(sql="SELECT id FROM t", source="sf"),
        source_config=_snowflake_source(),
    )
    assert result.error is not None and result.error.code is ERR_WAREHOUSE_RUNTIME
    assert not result.data


def test_stream_is_closed_when_the_ceiling_stops_the_fetch_early(
    tmp_path: Any, monkeypatch: Any
) -> None:
    monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "1")
    closed: list[bool] = []

    class _Stream:
        columns = ["ID"]
        schema = _FakeSchema([_FakeField(LongType())])

        def sql(self, sql: str) -> _Stream:
            return self

        def get_current_database(self) -> str | None:
            return '"ANALYTICS"'

        def get_current_schema(self) -> str | None:
            return '"PUBLIC"'

        def to_local_iterator(self) -> Iterator[_FakeRow]:
            try:
                for i in range(10):
                    yield _FakeRow({"ID": i})
            finally:
                closed.append(True)

    adapter = SnowparkAdapter(session=_Stream(), project=FilesystemProject(tmp_path))
    adapter.execute(
        SqlQuery(sql="SELECT id FROM t", source="sf"),
        source_config=_snowflake_source(),
    )
    assert closed == [True]


def test_variant_is_json_and_decoded(tmp_path: Any) -> None:
    session = _FakeSession(
        rows=[{"V": '{"a": [1, 2]}'}, {"V": None}], datatypes=[VariantType()]
    )
    adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
    result = adapter.execute(
        SqlQuery(sql="SELECT v FROM t", source="sf"), source_config=_snowflake_source()
    )
    assert result.schema == {"v": ColumnType(kind="json")}
    assert result.data == [{"v": {"a": [1, 2]}}, {"v": None}]


def test_null_type_is_string(tmp_path: Any) -> None:
    session = _FakeSession(rows=[{"N": None}], datatypes=[NullType()])
    adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
    result = adapter.execute(
        SqlQuery(sql="SELECT null AS n", source="sf"), source_config=_snowflake_source()
    )
    assert result.schema == {"n": ColumnType(kind="string")}


def test_an_unmapped_type_raises_naming_the_column(tmp_path: Any) -> None:
    session = _FakeSession(rows=[{"V": 1}], datatypes=[VectorType()])
    adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
    with pytest.raises(ExecutionError) as excinfo:
        adapter.execute(
            SqlQuery(sql="SELECT v FROM t", source="sf"),
            source_config=_snowflake_source(),
        )
    assert "'v'" in str(excinfo.value)
    assert "VectorType" in str(excinfo.value)


def test_geography_is_declared_string(tmp_path: Any) -> None:
    session = _FakeSession(rows=[{"G": "{}"}], datatypes=[GeographyType()])
    adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
    result = adapter.execute(
        SqlQuery(sql="SELECT g FROM t", source="sf"), source_config=_snowflake_source()
    )
    assert result.schema == {"g": ColumnType(kind="string")}


def test_decimal_scale_splits_integer_from_decimal(tmp_path: Any) -> None:
    session = _FakeSession(
        rows=[{"A": 1, "B": 1}], datatypes=[DecimalType(0), DecimalType(2)]
    )
    adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
    result = adapter.execute(
        SqlQuery(sql="SELECT a, b FROM t", source="sf"),
        source_config=_snowflake_source(),
    )
    assert result.schema == {
        "a": ColumnType(kind="integer"),
        "b": ColumnType(kind="decimal"),
    }


def test_timestamp_zone_splits_wall_clock_from_instant(tmp_path: Any) -> None:
    plus_two = dt.timezone(dt.timedelta(hours=2))
    session = _FakeSession(
        rows=[
            {
                "N": dt.datetime(2024, 1, 1, 12),
                "Z": dt.datetime(2024, 1, 1, 12, tzinfo=plus_two),
                "L": dt.datetime(2024, 1, 1, 12, tzinfo=plus_two),
            }
        ],
        datatypes=[
            TimestampType(_Zone("NTZ")),
            TimestampType(_Zone("TZ")),
            TimestampType(_Zone("LTZ")),
        ],
    )
    adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
    result = adapter.execute(
        SqlQuery(sql="SELECT n, z, l FROM t", source="sf"),
        source_config=_snowflake_source(),
    )
    assert [result.schema[c].kind for c in ("n", "z", "l")] == [
        "datetime",
        "datetime_tz",
        "datetime_tz",
    ]
    row = result.data[0]
    assert row["n"].tzinfo is None
    assert row["z"] == dt.datetime(2024, 1, 1, 10, tzinfo=dt.UTC)
    assert row["z"].tzinfo is dt.UTC


def test_identifier_quoted_columns_become_raw_names(tmp_path: Any) -> None:
    session = _FakeSession(
        rows=[{"X": 1, "Y": 2, "Z": 3}], columns=["ID", '"my col"', '"a""b"']
    )
    adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
    result = adapter.execute(
        SqlQuery(sql="SELECT 1", source="sf"), source_config=_snowflake_source()
    )
    assert result.data == [{"id": 1, "my col": 2, 'a"b': 3}]


@pytest.mark.parametrize("columns", [["ID", "ID"], ["ID", "id"], ["A", '"a"']])
def test_repeated_or_case_colliding_columns_raise(
    tmp_path: Any, columns: list[str]
) -> None:
    session = _FakeSession(rows=[{"X": 1, "Y": 2}], columns=columns)
    adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
    with pytest.raises(ExecutionError) as excinfo:
        adapter.execute(
            SqlQuery(sql="SELECT 1 AS id, 2 AS id", source="sf"),
            source_config=_snowflake_source(),
        )
    assert excinfo.value.code is ERR_RESULT_DUPLICATE_COLUMN


class TestSessionContext:
    """Unqualified names resolve in the session's current database and schema,
    so those must be the ones the source declares."""

    def _run(
        self, tmp_path: Any, session: _FakeSession, source: SnowflakeSourceConfig
    ) -> Any:
        adapter = SnowparkAdapter(session=session, project=FilesystemProject(tmp_path))
        return adapter.execute(
            SqlQuery(sql="SELECT id FROM orders", source="sf"), source_config=source
        )

    def test_a_different_database_is_refused_before_any_sql(
        self, tmp_path: Any
    ) -> None:
        session = _FakeSession(rows=[{"ID": 1}], database='"RAW"')

        result = self._run(tmp_path, session, _snowflake_source())

        assert result.error is not None
        assert "RAW" in str(result.error)
        assert "ANALYTICS" in str(result.error)
        assert session.received_sql == []

    def test_a_different_schema_is_refused_before_any_sql(self, tmp_path: Any) -> None:
        session = _FakeSession(rows=[{"ID": 1}], schema='"STAGING"')

        result = self._run(tmp_path, session, _snowflake_source())

        assert result.error is not None
        assert "STAGING" in str(result.error)
        assert "PUBLIC" in str(result.error)
        assert session.received_sql == []

    def test_no_current_database_is_refused(self, tmp_path: Any) -> None:
        session = _FakeSession(rows=[{"ID": 1}], database=None)

        result = self._run(tmp_path, session, _snowflake_source())

        assert result.error is not None
        assert session.received_sql == []

    def test_unquoted_source_name_matches_its_uppercase_form(
        self, tmp_path: Any
    ) -> None:
        session = _FakeSession(rows=[{"ID": 1}], database='"ANALYTICS"')

        result = self._run(tmp_path, session, _snowflake_source())

        assert result.is_success, result.error

    def test_quoted_source_name_is_case_sensitive(self, tmp_path: Any) -> None:
        source = SnowflakeSourceConfig(
            type="snowflake", account="xy12345", database='"MixedCase"'
        )

        same = self._run(
            tmp_path, _FakeSession(rows=[{"ID": 1}], database='"MixedCase"'), source
        )
        folded = self._run(
            tmp_path, _FakeSession(rows=[{"ID": 1}], database='"MIXEDCASE"'), source
        )

        assert same.is_success, same.error
        assert folded.error is not None

    def test_declared_role_and_warehouse_do_not_gate(self, tmp_path: Any) -> None:
        source = SnowflakeSourceConfig(
            type="snowflake",
            account="xy12345",
            database="analytics",
            role="ANALYST",
            warehouse="REPORTING_WH",
        )

        result = self._run(tmp_path, _FakeSession(rows=[{"ID": 1}]), source)

        assert result.is_success, result.error
