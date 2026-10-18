"""Load project file sources into a private, sandboxed DuckDB."""

from __future__ import annotations

import codecs
import contextlib
import hashlib
import json
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path  # noqa: TID251 — scratch files for DuckDB's readers
from typing import TYPE_CHECKING, Any, TypeAlias

from dbt_charts.core.compile.config import (
    get_execution_config,
    resolve_file_source_max_bytes,
    resolve_file_source_max_tables,
)
from dbt_charts.core.compile.models.source import (
    CsvSourceConfig,
    JsonSourceConfig,
    ParquetSourceConfig,
)
from dbt_charts.core.compile.sql_guard import double_alias_advice
from dbt_charts.core.compile.template.parameterized import render_parameterized
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_BINDER_UNKNOWN_COLUMN,
    ERR_FILE_SOURCE_DECIMAL_TOO_WIDE,
    ERR_FILE_SOURCE_DUPLICATE_COLUMN,
    ERR_FILE_SOURCE_TOO_LARGE,
    ERR_FILE_SOURCE_TOO_MANY_TABLES,
    ERR_FILE_SOURCE_UNSUPPORTED_TYPE,
    ERR_GLOB_EMPTY,
    ERR_GLOB_SCHEMA_MISMATCH,
    ERR_GLOB_TOO_MANY,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.adapters.base import QueryParams
from dbt_charts.core.execute.adapters.duckdb_adapter import duckdb_error_code
from dbt_charts.core.execute.arrow_ingest import RelationStream, ingest, insert_rows
from dbt_charts.core.execute.cache_composition import quote_identifier, rows_from_result
from dbt_charts.core.execute.column_advice import duckdb_unknown_column_advice
from dbt_charts.core.execute.duckdb_cache import compute_source_hash
from dbt_charts.core.project import is_glob

if TYPE_CHECKING:
    import duckdb

    from dbt_charts.core.project import Project

_JsonRow: TypeAlias = dict[str, Any]
_FileSource = CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig
_BYTES_PER_MB = 1_000_000


def _bindable_value(
    value: Any,  # type-state: explicit_any — a json.loads cell is dynamically typed
) -> Any:  # type-state: explicit_any — the cell, or JSON text for a list/dict
    """Turn a parsed JSON cell into something DuckDB's INSERT accepts.

    A nested list or dict becomes JSON text for the JSON column; a scalar binds
    as it is.
    """
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return value


def _infer_type(
    value: Any,  # type-state: explicit_any — a json.loads cell is dynamically typed
) -> str:
    """The DuckDB column type for a parsed JSON cell.

    Rows reach this only from ``json.loads``, so a cell is None, bool, int,
    float, str, list or dict and nothing else.
    """
    if value is None:
        return "VARCHAR"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "BIGINT"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, (list, dict)):
        return "JSON"
    return "VARCHAR"


def default_local_materializer_factory(
    project: Project,
) -> Callable[[], FileSourceMaterializer]:
    """Build the local materializer only when a file query needs it."""

    def _build() -> FileSourceMaterializer:
        return FileSourceMaterializer(project)

    return _build


def resolve_local_file_materializer_factory(
    project: Project,
    file_materializer: FileSourceMaterializer | None,
) -> Callable[[], FileSourceMaterializer] | None:
    """Use an injected materializer or lazily build the local one."""
    if file_materializer is not None:
        return None
    return default_local_materializer_factory(project)


def _version_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _glob_vkey(sorted_pairs: list[tuple[str, str]]) -> str:
    combined = "\n".join(f"{relpath}:{version}" for relpath, version in sorted_pairs)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


class FileSourceMaterializer:
    """ProjectSession-owned file parser and author-SQL engine."""

    def __init__(self, project: Project) -> None:
        self._project = project
        self._lock = threading.Lock()
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._loaded: dict[tuple[str, str], str] = {}

    def _connection(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            import duckdb

            self._conn = duckdb.connect(
                ":memory:",
                config={"enable_external_access": "false"},
            )
            self._conn.execute("SET TimeZone='UTC'")
        return self._conn

    def close(self) -> None:
        """Release the private DuckDB, if file execution opened it."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._loaded.clear()

    def materialize_and_run(
        self,
        source: _FileSource,
        sql: str,
        variables: dict[str, Any],
        source_name: str,
        params: QueryParams = None,
        strict: bool = True,
    ) -> list[_JsonRow]:
        """Load exact file versions once, install aliases, and run author SQL."""
        source_hash = compute_source_hash(source)
        glob_cap = get_execution_config().max_glob_file_count
        byte_cap = resolve_file_source_max_bytes()

        table_cap = resolve_file_source_max_tables()
        if len(source.files) > table_cap:
            raise DbtChartsError.from_code(
                ERR_FILE_SOURCE_TOO_MANY_TABLES,
                source_name=source_name,
                count=len(source.files),
                cap=table_cap,
            )

        table_specs: list[tuple[str, str, list[str]]] = []
        for table_name, path_or_glob in source.files.items():
            if is_glob(path_or_glob):
                relpaths = sorted(
                    path.relpath for path in self._project.files.glob(path_or_glob)
                )
                if not relpaths:
                    raise DbtChartsError.from_code(
                        ERR_GLOB_EMPTY,
                        source_name=source_name,
                        table_name=table_name,
                        pattern=path_or_glob,
                    )
                if len(relpaths) > glob_cap:
                    raise DbtChartsError.from_code(
                        ERR_GLOB_TOO_MANY,
                        source_name=source_name,
                        table_name=table_name,
                        pattern=path_or_glob,
                        count=len(relpaths),
                        cap=glob_cap,
                    )
                versions = [
                    (relpath, _version_key(self._project.file_version(relpath)))
                    for relpath in relpaths
                ]
                version = _glob_vkey(versions)
            else:
                relpaths = [path_or_glob]
                version = _version_key(self._project.file_version(path_or_glob))
            table_specs.append((table_name, version, relpaths))

        if params is None:
            parameterized = render_parameterized(
                sql, variables, profile_type="duckdb", strict=strict
            )
            resolved_sql = parameterized.sql
            resolved_params = parameterized.params
        else:
            resolved_sql, resolved_params = sql, params

        with self._lock:
            conn = self._connection()
            for table_name, version, relpaths in table_specs:
                key = (source_hash, table_name)
                physical = self._physical_name(source_hash, table_name)
                if self._loaded.get(key) != version:
                    load = TableLoad(source_name, table_name, relpaths, byte_cap)
                    if isinstance(source, JsonSourceConfig):
                        self._replace_table_from_rows(
                            conn, physical, table_name, self._json_rows(source, load)
                        )
                    else:
                        self._replace_table_from_files(conn, physical, source, load)
                    self._loaded[key] = version

                conn.execute(
                    f"CREATE OR REPLACE VIEW {quote_identifier(table_name)} AS "
                    f"SELECT * FROM {quote_identifier(physical)}"
                )
            try:
                return rows_from_result(conn.execute(resolved_sql, resolved_params))
            except DbtChartsError:
                raise
            except Exception as exc:  # noqa: BLE001 — DuckDB exposes many execution exception classes at this boundary
                code = duckdb_error_code(exc)
                advice = (
                    duckdb_unknown_column_advice(conn, resolved_sql, str(exc))
                    if code is ERR_BINDER_UNKNOWN_COLUMN
                    else double_alias_advice(resolved_sql)
                )
                raise QueryError(
                    "\n".join(
                        part
                        for part in (str(exc), advice, f"SQL:\n{resolved_sql}")
                        if part
                    ),
                    code=code,
                ) from exc

    @staticmethod
    def _physical_name(source_hash: str, table_name: str) -> str:
        digest = hashlib.sha256(f"{source_hash}:{table_name}".encode()).hexdigest()[:24]
        return f"_dct_file_{digest}"

    def _json_rows(self, source: JsonSourceConfig, load: TableLoad) -> list[_JsonRow]:
        """Parse every matched JSON file into one row list, header-checked."""
        rows: list[_JsonRow] = []
        first: tuple[str, frozenset[str]] | None = None
        materialized_bytes = 0
        for relpath in load.relpaths:
            file_bytes = self._project.read_bytes(relpath)
            materialized_bytes += len(file_bytes)
            load.check_size(relpath, len(file_bytes), materialized_bytes)
            file_rows = _parse_json(file_bytes, relpath)
            if not file_rows:
                continue
            keys = frozenset(file_rows[0])
            if first is None:
                first = (relpath, keys)
            elif not source.union_by_name:
                load.check_header(relpath, keys, *first)
            rows.extend(file_rows)
        if source.union_by_name and rows:
            all_keys = dict.fromkeys(key for row in rows for key in row)
            for row in rows:
                for column in all_keys:
                    row.setdefault(column, None)
        if not rows:
            load.raise_empty()
        return rows

    def _replace_table_from_files(
        self,
        conn: duckdb.DuckDBPyConnection,
        physical: str,
        source: CsvSourceConfig | ParquetSourceConfig,
        load: TableLoad,
    ) -> None:
        """Read CSV/Parquet files with DuckDB's own readers into *physical*.

        The readers need file access, which *conn* (it runs author SQL) never
        gets, so they run on a private scratch connection over temp copies of
        the files. Each file becomes one shard table; the shards' types are
        merged per column (see :func:`_wider`) and the union crosses into
        *conn* as one Arrow stream.
        """
        import duckdb

        with (
            tempfile.TemporaryDirectory(  # noqa: TID251 — DuckDB's readers take a path; the copies die with the load
                prefix="dct_file_"
            ) as tmp,
            contextlib.closing(duckdb.connect(":memory:")) as scratch,
        ):
            scratch.execute("SET TimeZone='UTC'")
            # Keeps DuckDB-only types (JSON, UUID) intact across the Arrow hop.
            scratch.execute("SET arrow_lossless_conversion = true")
            shards: list[_Shard] = []
            merged: dict[str, str | None] = {}
            materialized_bytes = 0
            for index, relpath in enumerate(load.relpaths):
                file_bytes = self._project.read_bytes(relpath)
                path = str(Path(tmp) / str(index))
                shard = _Shard(f"_shard_{index}", relpath, {})
                if isinstance(source, CsvSourceConfig):
                    Path(path).write_bytes(_as_utf8(file_bytes, source.encoding))
                    size = len(file_bytes)
                else:
                    Path(path).write_bytes(file_bytes)
                    size = _parquet_bytes(scratch, path)
                materialized_bytes += size
                load.check_size(relpath, len(file_bytes), materialized_bytes)
                null_typed: set[str] = set()
                if isinstance(source, CsvSourceConfig):
                    load.check_unique(
                        relpath, _csv_header(scratch, path, source.delimiter)
                    )
                else:
                    load.check_decimals(scratch, path, relpath)
                    top = _parquet_columns(scratch, path)
                    load.check_unique(relpath, [name for name, _ in top])
                    null_typed = {name for name, is_null in top if is_null}
                if isinstance(source, CsvSourceConfig):
                    _load_csv(scratch, shard.name, path, source.delimiter)
                else:
                    scratch.execute(
                        f"CREATE TABLE {shard.name} AS SELECT * FROM read_parquet($1)",
                        [path],
                    )
                described = scratch.execute(f"DESCRIBE {shard.name}").fetchall()
                columns = [row[0] for row in described]
                types = [row[1] for row in described]
                counts = scratch.execute(
                    "SELECT count(*), "
                    + ", ".join(
                        f"count({quote_identifier(column)})" for column in columns
                    )
                    + f" FROM {shard.name}"
                ).fetchone()
                assert counts is not None
                if counts[0] == 0:
                    continue
                if shards:
                    load.check_header(
                        relpath,
                        frozenset(columns),
                        shards[0].relpath,
                        frozenset(shards[0].types),
                    )
                for column, type_, count in zip(
                    columns, types, counts[1:], strict=True
                ):
                    # A CSV column with no value carries no type; a Parquet one
                    # keeps the type its footer declares unless that is null.
                    csv = isinstance(source, CsvSourceConfig)
                    untyped = not count if csv else column in null_typed
                    typed = None if untyped else type_
                    shard.types[column] = typed
                    known = merged.get(column)
                    if typed is None or typed == known:
                        merged.setdefault(column, None)
                        continue
                    wide = typed if known is None else _wider(known, typed)
                    if wide is None:
                        raise load.mismatch(
                            relpath,
                            shards[-1].relpath,
                            f"Column {column!r} is {typed} here and {known} "
                            "across the files before it. ",
                        )
                    merged[column] = wide
                shards.append(shard)
            if not shards:
                load.raise_empty()
            _refuse_lossy_nanoseconds(scratch, load, shards, merged)
            union = " UNION ALL BY NAME ".join(
                "SELECT "
                + ", ".join(
                    _select(column, typed, merged[column])
                    for column, typed in shard.types.items()
                )
                + f" FROM {shard.name}"
                for shard in shards
            )
            try:
                ingest(
                    conn, quote_identifier(physical), RelationStream(scratch.sql(union))
                )
            except duckdb.Error as exc:
                raise DbtChartsError.from_code(
                    ERR_FILE_SOURCE_UNSUPPORTED_TYPE,
                    table_name=load.table_name,
                    detail=str(exc).splitlines()[0],
                ) from exc

    @staticmethod
    def _replace_table_from_rows(
        conn: duckdb.DuckDBPyConnection,
        physical: str,
        table_name: str,
        rows: list[_JsonRow],
    ) -> None:
        import duckdb

        staging = 'temp."_dct_file_rows"'
        keys = list(rows[0])
        columns = [
            f"{quote_identifier(column)} "
            + _infer_type(
                next(
                    (row[column] for row in rows if row.get(column) is not None),
                    None,
                )
            )
            for column in keys
        ]
        coerced = [
            {column: _bindable_value(row.get(column)) for column in keys}
            for row in rows
        ]
        try:
            conn.execute(
                f"CREATE OR REPLACE TEMP TABLE {staging} ({', '.join(columns)})"
            )
            insert_rows(conn, staging, coerced)
            conn.execute(
                f"CREATE OR REPLACE TABLE {quote_identifier(physical)} AS SELECT * FROM {staging}"
            )
        except duckdb.Error as exc:
            raise DbtChartsError.from_code(
                ERR_FILE_SOURCE_UNSUPPORTED_TYPE,
                table_name=table_name,
                detail=str(exc).splitlines()[0],
            ) from exc
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {staging}")


@dataclass(frozen=True)
class TableLoad:
    """One file table being loaded, and the errors that name it."""

    source_name: str
    table_name: str
    relpaths: list[str]
    byte_cap: int

    def check_size(self, relpath: str, raw_bytes: int, materialized_bytes: int) -> None:
        if materialized_bytes > self.byte_cap:
            raise DbtChartsError.from_code(
                ERR_FILE_SOURCE_TOO_LARGE,
                source_name=self.source_name,
                table_name=self.table_name,
                relpath=relpath,
                raw_mb=raw_bytes / _BYTES_PER_MB,
                size_mb=materialized_bytes / _BYTES_PER_MB,
                cap_mb=self.byte_cap / _BYTES_PER_MB,
            )

    def check_header(
        self,
        relpath: str,
        keys: frozenset[str],
        first_relpath: str,
        first_keys: frozenset[str],
    ) -> None:
        if keys == first_keys:
            return
        extra = sorted(keys - first_keys)
        missing = sorted(first_keys - keys)
        raise self.mismatch(
            relpath,
            first_relpath,
            (f"Extra: {extra}. " if extra else "")
            + (f"Missing: {missing}. " if missing else ""),
        )

    def mismatch(self, relpath: str, first_relpath: str, detail: str) -> DbtChartsError:
        return DbtChartsError.from_code(
            ERR_GLOB_SCHEMA_MISMATCH,
            source_name=self.source_name,
            table_name=self.table_name,
            path=relpath,
            first_path=first_relpath,
            detail=detail,
        )

    def check_decimals(
        self, scratch: duckdb.DuckDBPyConnection, path: str, relpath: str
    ) -> None:
        """Refuse a Parquet decimal wider than DuckDB's 38 digits before reading.

        DuckDB reads such a column as DOUBLE, and at some precisions as wrong
        values, so it is never read at all.
        """
        wide = scratch.execute(
            "SELECT name, precision, scale FROM parquet_schema($1) "
            "WHERE precision > 38 LIMIT 1",
            [path],
        ).fetchone()
        if wide is not None:
            raise DbtChartsError.from_code(
                ERR_FILE_SOURCE_DECIMAL_TOO_WIDE,
                source_name=self.source_name,
                table_name=self.table_name,
                column=wide[0],
                relpath=relpath,
                column_type=f"DECIMAL({wide[1]},{wide[2]})",
            )

    def check_unique(self, relpath: str, columns: list[str]) -> None:
        """Refuse a repeated column name, which DuckDB's readers would rename."""
        seen: set[str] = set()
        for column in columns:
            if column.lower() in seen:
                raise DbtChartsError.from_code(
                    ERR_FILE_SOURCE_DUPLICATE_COLUMN,
                    source_name=self.source_name,
                    table_name=self.table_name,
                    relpath=relpath,
                    column=column,
                )
            seen.add(column.lower())

    def raise_empty(self) -> None:
        raise ValueError(
            f"File table {self.table_name!r}: parsed file(s) contain no rows. "
            "Empty file sources are not supported; provide a file with "
            "at least one data row."
        )


@dataclass
class _Shard:
    """One matched file loaded on the scratch connection.

    ``types`` maps each column to its DuckDB type, or None when the column
    holds no value, so it takes whatever type its siblings give it.
    """

    name: str
    relpath: str
    types: dict[str, str | None]


def _as_utf8(file_bytes: bytes, encoding: str) -> bytes:
    if codecs.lookup(encoding).name == "utf-8":
        return file_bytes
    return file_bytes.decode(encoding).encode("utf-8")


def _parquet_bytes(scratch: duckdb.DuckDBPyConnection, path: str) -> int:
    """A Parquet file's uncompressed size from its footer: the quantity
    ``len()`` gives for CSV and JSON, so one byte cap means one thing."""
    row = scratch.execute(
        "SELECT coalesce(sum(row_group_bytes), 0) FROM "
        "(SELECT DISTINCT row_group_id, row_group_bytes FROM parquet_metadata($1))",
        [path],
    ).fetchone()
    assert row is not None
    return int(row[0])


def _csv_header(
    scratch: duckdb.DuckDBPyConnection, path: str, delimiter: str
) -> list[str]:
    row = scratch.execute(
        "SELECT * FROM read_csv($1, delim=$2, header=false, all_varchar=true) LIMIT 1",
        [path, delimiter],
    ).fetchone()
    return [] if row is None else [str(name) for name in row]


def _select(column: str, typed: str | None, target: str | None) -> str:
    """One shard column as the merged table holds it."""
    if typed is not None and typed == target:
        return quote_identifier(column)
    # A column no file gave a value is text.
    cast_to = "VARCHAR" if target is None else target
    return (
        f"CAST({quote_identifier(column)} AS {cast_to}) AS {quote_identifier(column)}"
    )


# Fields a CSV export writes for a missing value.
_CSV_NULLS = [
    "",
    "#N/A",
    "#N/A N/A",
    "#NA",
    "-1.#IND",
    "-1.#QNAN",
    "-NaN",
    "-nan",
    "1.#IND",
    "1.#QNAN",
    "N/A",
    "NA",
    "NULL",
    "NaN",
    "n/a",
    "nan",
    "null",
]


def _load_csv(
    scratch: duckdb.DuckDBPyConnection, table: str, path: str, delimiter: str
) -> None:
    """Read a CSV into *table*, typing each column from its values.

    A missing-value token is null in a typed column, but a text column keeps
    every field as written, an empty one as the empty string. A column of
    nothing but missing values is null throughout.
    """
    read = "read_csv($1, delim=$2, header=true, sample_size=-1"
    scratch.execute(
        f"CREATE TABLE {table} AS SELECT * FROM {read}, nullstr=$3)",
        [path, delimiter, _CSV_NULLS],
    )
    text = [
        column
        for column, type_ in scratch.execute(
            f"SELECT column_name, column_type FROM (DESCRIBE {table})"
        ).fetchall()
        if type_ == "VARCHAR"
    ]
    if not text:
        return
    counts = scratch.execute(
        f"SELECT {', '.join(f'count({quote_identifier(c)})' for c in text)} FROM {table}"
    ).fetchone()
    assert counts is not None
    text = [column for column, count in zip(text, counts, strict=True) if count]
    if not text:
        return
    raw = ", ".join(
        f"coalesce(raw.{quote_identifier(c)}, '') AS {quote_identifier(c)}"
        for c in text
    )
    scratch.execute(
        f"CREATE OR REPLACE TABLE {table} AS SELECT typed.* REPLACE ({raw}) "
        f"FROM {table} typed POSITIONAL JOIN {read}, all_varchar=true) raw",
        [path, delimiter],
    )


def _parquet_columns(
    scratch: duckdb.DuckDBPyConnection, path: str
) -> list[tuple[str, bool]]:
    """The top-level columns in a Parquet footer, repeats included, each with
    whether its declared type is null.

    ``parquet_schema`` lists the schema tree depth-first, root first, so a
    top-level column is every node reached by skipping its siblings' subtrees.
    """
    nodes = scratch.execute(
        "SELECT name, coalesce(num_children, 0), logical_type = 'NullType()' "
        "FROM parquet_schema($1)",
        [path],
    ).fetchall()

    def _after(index: int) -> int:
        end = index + 1
        for _ in range(nodes[index][1]):
            end = _after(end)
        return end

    columns: list[tuple[str, bool]] = []
    index = 1
    while index < len(nodes):
        columns.append((nodes[index][0], bool(nodes[index][2])))
        index = _after(index)
    return columns


_INTEGERS = (
    ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT"),
    ("UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT"),
)
_FLOATS = ("FLOAT", "DOUBLE")
# Finer is wider — except nanoseconds, whose int64 range ends in 2262 and
# would put a 9999-12-31 sentinel out of bounds. DuckDB's TIMESTAMP is
# microseconds, so that is where a unit pair caps.
_TIMESTAMPS = {"TIMESTAMP_S": 0, "TIMESTAMP_MS": 1, "TIMESTAMP": 2, "TIMESTAMP_NS": 2}
_TIMESTAMP_AT_RANK = ("TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP")
_DECIMAL = re.compile(r"DECIMAL\((\d+),(\d+)\)")


def _wider(a: str, b: str) -> str | None:
    """The lossless common DuckDB type of two widths of one kind, or None when
    the pair is a genuine conflict (int vs double, string vs numeric, nested)."""
    for family in (*_INTEGERS, _FLOATS):
        if a in family and b in family:
            return max(a, b, key=family.index)
    if a in _TIMESTAMPS and b in _TIMESTAMPS:
        return _TIMESTAMP_AT_RANK[max(_TIMESTAMPS[a], _TIMESTAMPS[b])]
    da, db = _DECIMAL.fullmatch(a), _DECIMAL.fullmatch(b)
    if da and db and da[2] == db[2]:
        return a if int(da[1]) >= int(db[1]) else b
    return None


def _refuse_lossy_nanoseconds(
    scratch: duckdb.DuckDBPyConnection,
    load: TableLoad,
    shards: list[_Shard],
    merged: dict[str, str | None],
) -> None:
    """Raise when merging a nanosecond shard to microseconds would drop digits.

    The cap in ``_TIMESTAMPS`` makes ns → us the one widening that can lose a
    value, and DuckDB's CAST truncates silently.
    """
    for index, shard in enumerate(shards):
        for column, typed in shard.types.items():
            if typed != "TIMESTAMP_NS" or merged[column] != "TIMESTAMP":
                continue
            row = scratch.execute(
                f"SELECT count(*) FROM {shard.name} "
                f"WHERE epoch_ns({quote_identifier(column)}) % 1000 <> 0"
            ).fetchone()
            if row is not None and row[0]:
                raise load.mismatch(
                    shard.relpath,
                    shards[index - 1 if index else 1].relpath,
                    f"Column {column!r} holds nanoseconds that TIMESTAMP "
                    "cannot represent. ",
                )


# ── Module-level parse helpers (no DuckDB filesystem access) ─────────────────


def _parse_json(
    file_bytes: bytes,
    relpath: str,
) -> list[_JsonRow]:
    """Parse JSON or NDJSON bytes to list[dict].

    The parser is chosen by extension: ``.jsonl`` → NDJSON, ``.json`` → standard
    JSON.  The extension is validated at compile time, so only these two values
    are possible at runtime.

    Raises ValueError when the file cannot be parsed in the selected format.
    """
    if relpath.lower().endswith(".jsonl"):
        return _parse_ndjson(file_bytes)

    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"JSON file is not valid UTF-8: {e}") from e

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON file is not valid JSON: {e}") from e

    if isinstance(parsed, dict):
        return [parsed]
    if not isinstance(parsed, list):
        raise ValueError(
            f"JSON file must contain a top-level object or array of objects, "
            f"got: {type(parsed).__name__!r}."
        )
    return parsed


def _parse_ndjson(file_bytes: bytes) -> list[_JsonRow]:
    """Parse newline-delimited JSON (NDJSON) bytes to list[dict].

    Each non-empty line must be a JSON object ``{...}``.

    Raises ValueError on any line that is not a valid JSON object.
    """
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"NDJSON file is not valid UTF-8: {e}") from e

    rows: list[_JsonRow] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"NDJSON line {lineno} is not valid JSON: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError(
                f"NDJSON line {lineno} must be a JSON object, "
                f"got: {type(obj).__name__!r}."
            )
        rows.append(obj)
    return rows
