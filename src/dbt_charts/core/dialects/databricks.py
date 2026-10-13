"""Databricks dialect implementation.

Databricks uses:
- :param1, :param2 for named parameter placeholders
"""

from collections.abc import Sequence
from typing import Any

from dbt_charts.core.dialects.base import SQLDialect
from dbt_charts.core.dialects.identifier import sql_identifier


class DatabricksDialect(SQLDialect):
    """Databricks dialect with :paramN parameter style.

    Databricks (Spark SQL) uses named parameters with : prefix.

    max_query_duration_seconds is not enforced for Databricks — SET STATEMENT_TIMEOUT
    requires DBSQL warehouses 14.1+ and would fail on interactive clusters; pending a
    verified mechanism across all Databricks compute types.

    Example:
        >>> dialect = DatabricksDialect()
        >>> dialect.param(1)
        ':param1'
    """

    name = "databricks"
    folds_identifier_case = True
    # Spark SQL string literals: use \ to escape special characters (' or \).
    # https://spark.apache.org/docs/latest/sql-ref-literals.html
    escapes_backslashes = True
    uses_named_params = True

    def param(self, index: int) -> str:
        """Generate Databricks parameter placeholder.

        Databricks uses :paramN named style.

        Args:
            index: 1-based parameter index

        Returns:
            Parameter placeholder in :paramN format
        """
        return f":param{index}"

    def namespace_probe_sql(
        self,
        creds: dict[str, Any],  # type-state: explicit_any — raw source_config
    ) -> str | None:
        catalog = creds.get("catalog")
        if catalog is None:
            return None
        return f"USE CATALOG {sql_identifier(catalog, 'databricks')}"

    def bulk_schema_sql(self, scope: str = "") -> str:
        """Whole-catalog columns from Unity Catalog's per-catalog information_schema.

        ``hive_metastore`` has no information_schema. Plain ``data_type`` drops
        parameters (``DECIMAL`` for ``decimal(10,2)``).
        """
        if scope.lower() == "hive_metastore":
            raise NotImplementedError(
                "bulk_schema_sql is not available for the 'hive_metastore' catalog"
            )
        catalog = f"{sql_identifier(scope, 'databricks')}." if scope else ""
        return (
            "SELECT table_schema, table_name, column_name, full_data_type AS data_type "
            f"FROM {catalog}information_schema.columns "
            "WHERE table_schema <> 'information_schema' "
            "ORDER BY table_schema, table_name, ordinal_position"
        )

    def bulk_tables_sql(self, scope: str, schemas: Sequence[str]) -> str:
        """Whole-catalog tables from Unity Catalog's information_schema."""
        if scope.lower() == "hive_metastore":
            raise NotImplementedError(
                "bulk_tables_sql is not available for the 'hive_metastore' catalog"
            )
        catalog = f"{sql_identifier(scope, 'databricks')}." if scope else ""
        condition = self.schema_condition(schemas, "table_schema")
        extra = f"AND {condition} "
        return (
            "SELECT table_schema, table_name "
            f"FROM {catalog}information_schema.tables "
            "WHERE table_schema <> 'information_schema' "
            f"{extra}"
            "ORDER BY table_schema, table_name"
        )

    def quote(self, identifier: str) -> str:
        """Backtick-quote an identifier — Spark SQL's only quoted form.

        https://spark.apache.org/docs/latest/sql-ref-identifier.html
        """
        return "`" + identifier.replace("`", "``") + "`"


class SparkDialect(DatabricksDialect):
    """Spark dialect — same SQL grammar as Databricks, but the PySpark cursors
    (PyhiveConnectionWrapper, PyodbcConnectionWrapper, SparkConnectionWrapper)
    implement fetchall but not fetchmany.  cursor_supports_driver_limit = False
    causes the adapters to fall back to fetchall + post-fetch slice instead of
    using the limit= kwarg.
    """

    name = "spark"
    cursor_supports_driver_limit = False
    bulk_schema_sql = SQLDialect.bulk_schema_sql
    bulk_tables_sql = SQLDialect.bulk_tables_sql

    def namespace_probe_sql(
        self,
        creds: dict[str, Any],  # type-state: explicit_any — raw source_config
    ) -> str | None:
        return None
