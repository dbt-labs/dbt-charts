"""Snowpark backend — runs SQL through a live Snowpark session.

Stage: EXECUTE
Purpose: Execute SQL against Snowflake through a session the host passes in
    (e.g. the one Streamlit in Snowflake hands an app) instead of a dbt profile.
    The host routes ``snowflake`` to it in its backend map.

Imports nothing from ``snowflake.*``: the session is duck-typed against
``SnowparkSession``, so this module adds no dependency.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC
from itertools import islice
from typing import TYPE_CHECKING, Any, Protocol

from dbt_charts.core.column_types import (
    ColumnKind,
    ColumnType,
    TypedRows,
    column_type_error,
)
from dbt_charts.core.compile.models.source import SnowflakeSourceConfig
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.dialects import SQLDialect
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.execute.compose import ResolvedQuery


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
    """The slice of ``snowflake.snowpark.Session`` this backend uses.

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


_Cell = Any  # type-state: explicit_any — untyped warehouse cell
_Decoder = Callable[[_Cell], _Cell]
_DECODERS: dict[ColumnKind, _Decoder] = {
    "datetime_tz": lambda cell: cell.astimezone(UTC),
    "json": json.loads,
}


class SnowparkBackend:
    """Runs SQL against Snowflake through an already-open Snowpark session.

    SQL is composed by :func:`compose`, so a query sends the same text through
    a session as it does through a dbt connection.

    The session belongs to the host, so this backend never changes it. Its
    ``interrupt`` and ``close`` do nothing, and the registry closing never
    closes the session. The source's ``account``, ``role``, ``warehouse`` and
    ``max_query_duration_seconds`` are not applied: queries run in the session's
    account, with its role's privileges, on its warehouse, under its own
    statement timeout.

    Database and schema are different, because they decide where an unqualified
    table name resolves. A query is refused unless the session's current
    database and schema are the ones the source declares.

    Example:
        >>> routes = {"snowflake": SnowparkBackend(session=active_session)}
    """

    param_style: SQLDialect = INLINE_PLACEHOLDERS

    def __init__(self, *, session: SnowparkSession) -> None:
        """
        Args:
            session: The host's live Snowpark session.
        """
        self._session = session

    def execute(self, source: ResolvedSourceConfig, query: ResolvedQuery) -> TypedRows:
        if query.setup_sql:
            raise DbtChartsError(
                "setup_sql is not supported for Snowpark sessions. "
                "Remove the setup_sql block."
            )
        if not isinstance(source, SnowflakeSourceConfig):
            raise DbtChartsError("A Snowpark session runs only snowflake sources.")
        declared = [_resolved_name(source.database), _resolved_name(source.schema_)]
        current = [
            None if name is None else _resolved_name(name)
            for name in (
                self._session.get_current_database(),
                self._session.get_current_schema(),
            )
        ]
        if current != declared:
            raise DbtChartsError(
                f"The source declares database {declared[0]} and schema "
                f"{declared[1]}, but the Snowpark session is using database "
                f"{current[0]} and schema {current[1]}, so unqualified table "
                "names would resolve in the wrong place. Point the session at "
                "the declared database and schema (session.use_database, "
                "session.use_schema), or change the source to match."
            )
        # Streamed and cut off at the fetch limit, so the SQL text is never
        # rewritten (DataFrame.limit() would wrap it in a subquery).
        frame = self._session.sql(query.inline("snowflake"))
        columns = [_raw_name(name).lower() for name in frame.columns]
        types = [
            _snowpark_column_type(name, field.datatype)
            for name, field in zip(columns, frame.schema.fields, strict=True)
        ]
        decoders = [_DECODERS.get(kind.kind) for kind in types]
        raw_rows = islice(frame.to_local_iterator(), query.limit.fetch_limit)
        try:
            rows = [
                tuple(
                    cell if decode is None or cell is None else decode(cell)
                    for cell, decode in zip(row, decoders, strict=True)
                )
                for row in raw_rows
            ]
        except ValueError as exc:
            # A cell the session returned does not match its declared type: a
            # client-side defect, not the warehouse rejecting the statement.
            raise DbtChartsError(
                f"Could not decode a Snowpark result cell: {exc}"
            ) from exc
        return TypedRows(columns, rows, types)

    def interrupt(self) -> None:
        pass

    def close(self) -> None:
        pass
