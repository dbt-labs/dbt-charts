"""Lake compute dialect: DuckDB's SQL, reached through dbt's ADBC driver.

Lake compute's engine is DuckDB and its SQL is rendered as DuckDB, so this
inherits everything. It exists as its own registry entry because
``get_dialect`` falls back to Postgres for a name it does not know.

The per-query cap is the driver's own ``adbc.dbt.timeout_seconds`` option, sent
at connect, so ``statement_timeout_sql`` stays DuckDB's ``None`` and no
credential rewrite is needed.
"""

from dbt_charts.core.dialects.duckdb import DuckDBDialect


class LakeComputeDialect(DuckDBDialect):
    """Lake compute dialect with DuckDB's $1, $2 parameter style."""

    name = "lakecompute"
