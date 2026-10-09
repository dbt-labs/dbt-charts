"""BigQuery dialect implementation.

BigQuery uses:
- @param1, @param2 for named parameter placeholders
"""

from dbt_charts.core.dialects.base import SQLDialect


class BigQueryDialect(SQLDialect):
    """BigQuery dialect with @paramN parameter style.

    BigQuery uses named parameters with @ prefix.

    max_query_duration_seconds is enforced here via the job's job_timeout_ms
    (set on QueryJobConfig at connection setup in sql_adapter.py), not SQL —
    BigQuery has no session-level statement_timeout syntax. This dialect does
    not override statement_timeout_sql(); the base no-op is correct.

    Example:
        >>> dialect = BigQueryDialect()
        >>> dialect.param(1)
        '@param1'
    """

    name = "bigquery"
    # GoogleSQL quoted literals: backslashes introduce escape sequences (\', \\,
    # \n). Only the raw r'...' form exempts them, and nothing here emits one.
    # https://cloud.google.com/bigquery/docs/reference/standard-sql/lexical
    escapes_backslashes = True
    uses_named_params = True

    def param(self, index: int) -> str:
        """Generate BigQuery parameter placeholder.

        BigQuery uses @paramN named style.

        Args:
            index: 1-based parameter index

        Returns:
            Parameter placeholder in @paramN format
        """
        return f"@param{index}"

    def bulk_schema_sql(self, scope: str = "") -> str:
        """Whole-warehouse columns from the region-wide INFORMATION_SCHEMA.

        BigQuery's INFORMATION_SCHEMA is scoped: a *region* view
        (``region-us.INFORMATION_SCHEMA.COLUMNS``) spans every dataset in one
        query, while an unqualified reference is per-dataset (the N+1 we avoid).
        ``scope`` must be the region qualifier (e.g. ``region-us``); without it we
        cannot address the whole warehouse, so raise and let the caller fall back
        to the on-demand schema tool.
        """
        if not scope:
            raise NotImplementedError("bigquery bulk_schema_sql requires a region")
        return (
            "SELECT table_schema, table_name, column_name, data_type "
            f"FROM `{scope}`.INFORMATION_SCHEMA.COLUMNS "
            "ORDER BY table_schema, table_name, ordinal_position"
        )

    def quote(self, identifier: str) -> str:
        """Backtick-quote an identifier; GoogleSQL has no double-quoted form.

        A backtick inside one is escaped with a backslash, not by doubling —
        doubling is MySQL's rule, and GoogleSQL reads two backticks as the end
        of one quoted identifier and the start of another.

        https://cloud.google.com/bigquery/docs/reference/standard-sql/lexical
        """
        return "`" + identifier.replace("\\", "\\\\").replace("`", "\\`") + "`"
