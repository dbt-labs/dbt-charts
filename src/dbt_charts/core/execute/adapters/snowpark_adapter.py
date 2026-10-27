"""Snowpark query adapter — runs SQL through a live Snowpark session.

Stage: EXECUTE
Purpose: Execute SQL against Snowflake through a session the host passes in
    (e.g. the one Streamlit in Snowflake hands an app) instead of a dbt profile.

Imports nothing from ``snowflake.*``: the session is duck-typed against
``SnowparkSession``, so this module adds no dependency.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC
from itertools import islice
from typing import TYPE_CHECKING, Any, Protocol

from dbt_charts.core.column_types import (
    ColumnKind,
    ColumnType,
    column_type_error,
    reject_duplicate_columns,
)
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import AnyQuery, is_sql_query
from dbt_charts.core.compile.models.source import SnowflakeSourceConfig
from dbt_charts.core.dialects import SQLDialect
from dbt_charts.core.execute.adapters.base import (
    BaseAdapter,
    QueryParams,
    QueryResult,
    apply_row_limit_truncation,
    classify_warehouse_error,
    plain_error,
)
from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS
from dbt_charts.core.project import Project

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig


def _raw_name(identifier: str) -> str:
    # df.columns is identifier-quoted; result keys are raw names.
    if len(identifier) >= 2 and identifier[0] == identifier[-1] == '"':
        return identifier[1:-1].replace('""', '"')
    return identifier


_Cells = Iterable[Any]  # type-state: explicit_any — untyped warehouse cells


class SnowparkField(Protocol):
    datatype: Any  # type-state: explicit_any — a snowflake.snowpark.types instance


class SnowparkSchema(Protocol):
    fields: list[SnowparkField]


class SnowparkDataFrame(Protocol):
    columns: list[str]
    schema: SnowparkSchema

    def to_local_iterator(self) -> Iterator[_Cells]: ...


class SnowparkSession(Protocol):
    """The slice of ``snowflake.snowpark.Session`` this adapter uses.

    ``get_current_database`` and ``get_current_schema`` return the name the way
    Snowpark does: double-quoted, in its resolved case (``'"ANALYTICS"'``), or
    ``None`` when the session has none selected.
    """

    def sql(self, query: str, /) -> SnowparkDataFrame: ...

    def get_current_database(self) -> str | None: ...

    def get_current_schema(self) -> str | None: ...


# Keyed by class name so this module imports nothing from snowflake.*.
_SNOWPARK_SCALARS: dict[str, ColumnKind] = {
    **dict.fromkeys(("ByteType", "ShortType", "IntegerType", "LongType"), "integer"),
    **dict.fromkeys(("FloatType", "DoubleType"), "float"),
    **dict.fromkeys(
        ("StringType", "NullType", "GeographyType", "GeometryType"), "string"
    ),
    "BinaryType": "bytes",
    "BooleanType": "boolean",
    "DateType": "date",
    "TimeType": "time",
    # Snowflake hands semi-structured values back as JSON text.
    **dict.fromkeys(("VariantType", "ArrayType", "MapType", "StructType"), "json"),
}


def _snowpark_column_type(
    name: str,
    datatype: Any,  # type-state: explicit_any — snowpark type instance
) -> ColumnType:
    type_name = type(datatype).__name__
    if type_name == "TimestampType":
        # NTZ is wall-clock; TZ and LTZ are instants. DEFAULT is the session's
        # own mapping, which the result schema never reports.
        zone = datatype.tz.name
        if zone not in ("NTZ", "LTZ", "TZ"):
            raise column_type_error(
                name, f"the Snowpark timestamp zone {zone} has no declared mapping"
            )
        return ColumnType(kind="datetime" if zone == "NTZ" else "datetime_tz")
    if type_name == "DecimalType":
        return ColumnType(kind="integer" if datatype.scale == 0 else "decimal")
    if type_name not in _SNOWPARK_SCALARS:
        raise column_type_error(
            name, f"the Snowpark type {type_name} has no declared mapping"
        )
    return ColumnType(kind=_SNOWPARK_SCALARS[type_name])


def _resolved_name(name: str) -> str:
    """How Snowflake resolves an identifier: quoted keeps its case, unquoted
    folds to upper."""
    if len(name) > 1 and name[0] == name[-1] == '"':
        return name[1:-1].replace('""', '"')
    return name.upper()


class SnowparkAdapter(BaseAdapter):
    """Runs SQL against Snowflake through an already-open Snowpark session.

    SQL is prepared by ``SqlAdapter.prepare_sql``, so a query sends the same
    text through a session as it does through a dbt connection.

    The session belongs to the host, so this adapter never changes it. It
    defines no ``close()``, and the registry closing never closes the session.
    The source's ``account``, ``role``, ``warehouse`` and
    ``max_query_duration_seconds`` are not applied: queries run in the session's
    account, with its role's privileges, on its warehouse, under its own
    statement timeout.

    Database and schema are different, because they decide where an unqualified
    table name resolves. A query is refused unless the session's current
    database and schema are the ones the source declares.

    Example:
        >>> adapter = SnowparkAdapter(session=active_session, project=project)
        >>> result = adapter.execute(query, source_config=snowflake_source)
    """

    def __init__(self, *, session: SnowparkSession, project: Project) -> None:
        """
        Args:
            session: The host's live Snowpark session.
            project: Read while preparing SQL, to resolve dbt refs.
        """
        self._session = session
        self._sql_prep = SqlAdapter(
            project=project, dbt_project_path=None, profile_type="snowflake"
        )

    @property
    def supported_types(self) -> set[str]:
        return {"sql"}

    def _can_execute(
        self, query: AnyQuery, source_config: ResolvedSourceConfig | None
    ) -> bool:
        return is_sql_query(query) and isinstance(source_config, SnowflakeSourceConfig)

    def param_render_dialect(self, source_config: ResolvedSourceConfig) -> SQLDialect:
        """Params flatten to literals here too, same as ``SqlAdapter`` —
        Snowpark's own driver never binds them, so the collision-free inline
        style must match what ``self._sql_prep.prepare_sql`` assumes."""
        return INLINE_PLACEHOLDERS

    def _execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        if not is_sql_query(query):
            return plain_error(f"Expected SQL query, got {query.query_type}")

        if query.setup_sql:
            return plain_error(
                "setup_sql is not supported for Snowpark sessions. "
                "Remove the setup_sql block."
            )

        if not isinstance(source_config, SnowflakeSourceConfig):
            return plain_error("A Snowpark session runs only snowflake sources.")
        declared = [
            _resolved_name(source_config.database),
            _resolved_name(source_config.schema_),
        ]

        prepared = self._sql_prep.prepare_sql(
            query, variables=variables, params=params, source_config=source_config
        )
        if isinstance(prepared, QueryResult):
            return prepared

        try:
            current = [
                None if name is None else _resolved_name(name)
                for name in (
                    self._session.get_current_database(),
                    self._session.get_current_schema(),
                )
            ]
            if current != declared:
                return plain_error(
                    f"The source declares database {declared[0]} and schema "
                    f"{declared[1]}, but the Snowpark session is using database "
                    f"{current[0]} and schema {current[1]}, so unqualified table "
                    "names would resolve in the wrong place. Point the session at "
                    "the declared database and schema (session.use_database, "
                    "session.use_schema), or change the source to match."
                )
            # Streamed and cut off at the fetch limit, so the SQL text is never
            # rewritten (DataFrame.limit() would wrap it in a subquery).
            frame = self._session.sql(prepared.sql)
            columns = [_raw_name(name).lower() for name in frame.columns]
            datatypes = [field.datatype for field in frame.schema.fields]
            rows = list(
                islice(frame.to_local_iterator(), prepared.row_fetch_limit.fetch_limit)
            )
        except Exception as e:  # noqa: BLE001 — Snowpark driver error boundary
            return classify_warehouse_error("snowflake SQL execution", e, "snowflake")

        rows, truncated_reason = apply_row_limit_truncation(
            rows, prepared.row_fetch_limit
        )
        reject_duplicate_columns(columns)
        schema = {
            name: _snowpark_column_type(name, datatype)
            for name, datatype in zip(columns, datatypes, strict=True)
        }
        json_columns = [name for name, kind in schema.items() if kind.kind == "json"]
        instant_columns = [
            name for name, kind in schema.items() if kind.kind == "datetime_tz"
        ]
        data = [dict(zip(columns, row, strict=True)) for row in rows]
        for row in data:
            for name in instant_columns:
                if row[name] is not None:
                    row[name] = row[name].astimezone(UTC)
            for name in json_columns:
                if row[name] is not None:
                    row[name] = json.loads(row[name])
        return QueryResult(
            data=data,
            columns=columns,
            schema=schema,
            resolved_relations=prepared.resolved_relations or None,
            truncated_reason=truncated_reason,
        )
