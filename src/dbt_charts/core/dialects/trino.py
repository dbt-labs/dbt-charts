"""Trino dialect: Athena's SQL surface, plus Trino's own per-query cap.

Athena runs Trino's engine, so the SQL rendering is shared. What differs is the
connection: Trino is reached through its Python client on the driver seam, and
the cap is the ``query_max_run_time`` session property that driver sends at
connect.
"""

import math
import re
from collections.abc import Sequence
from typing import Any

from dbt_charts.core.dialects.athena import AthenaDialect

# https://trino.io/docs/current/admin/properties.html#duration
_DURATION = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(ns|us|ms|s|m|h|d)\s*")
_UNIT_SECONDS = {
    "ns": 1e-9,
    "us": 1e-6,
    "ms": 1e-3,
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


class TrinoDialect(AthenaDialect):
    """Trino dialect: Athena's SQL, Trino's own cap and metadata views."""

    name = "trino"
    # Trino lowercases unquoted identifiers and stores them that way.
    folds_identifier_case = True

    def resolve_timeout_seconds(
        self,
        creds: dict[str, Any],  # type-state: explicit_any — raw source_config
        seconds: int,
    ) -> int:
        """Narrow the ceiling to a source that caps itself harder.

        ``session_properties.query_max_run_time`` is the author's own cap, in
        Trino's duration spelling. A sub-second cap rounds up to one second, the
        finest the ceiling is expressed in.

        Raises:
            ValueError: the authored value is not a Trino duration.
        """
        authored = creds["session_properties"].get("query_max_run_time")
        if authored is None:
            return seconds
        match = _DURATION.fullmatch(str(authored))
        if match is None:
            raise ValueError(
                "session_properties.query_max_run_time must be a Trino duration "
                f"such as '90s' or '5m', got {authored!r}"
            )
        authored_seconds = math.ceil(float(match[1]) * _UNIT_SECONDS[match[2]])
        return min(seconds, max(1, authored_seconds))

    def bulk_schema_sql(self, scope: str = "") -> str:
        """Every column of the connection's catalog.

        Trino's ``information_schema`` is per catalog and an unqualified one is
        the session catalog's, so no scope is needed.
        """
        return (
            "SELECT table_schema, table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema <> 'information_schema' "
            "ORDER BY table_schema, table_name, ordinal_position"
        )

    def bulk_tables_sql(self, scope: str, schemas: Sequence[str]) -> str:
        """Every table of the connection's catalog in the given schemas."""
        return (
            "SELECT table_schema, table_name "
            "FROM information_schema.tables "
            "WHERE table_schema <> 'information_schema' "
            f"AND {self.schema_condition(schemas, 'table_schema')} "
            "ORDER BY table_schema, table_name"
        )

    def is_statement_timeout_error(self, exc: Exception) -> bool:
        """Return True when Trino stopped the query at ``query_max_run_time``."""
        return "EXCEEDED_TIME_LIMIT" in str(exc)
