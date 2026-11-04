"""Tests for sql_authoring_lint.diagnose_literal_escaped_newlines.

The function under test:
  - returns a reason string when SQL has literal \\n (two chars: backslash + n)
    and sqlglot parse only succeeds after replacing \\n with a real newline
  - returns None (no error) when SQL with real newlines parses fine
  - returns None when SQL parses fine even before any replacement (e.g. \\n inside
    a string literal that sqlglot handles)
  - returns None when SQL is broken for unrelated reasons (no \\n present)
"""

import pytest

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.normalize.queries import normalize_query
from dbt_charts.core.compile.normalize.sql_authoring_lint import (
    find_date_literal_variable,
    has_literal_escaped_newlines,
)


class TestHasLiteralEscapedNewlines:
    """Unit tests for the parse-replace-parse heuristic function."""

    def test_literal_backslash_n_flagged(self):
        """SQL with literal \\n that only parses after replacement returns True.

        Simulates YAML author writing: sql: 'SELECT\\n  date,\\n  SUM(x)\\nFROM t'
        After YAML parsing the string contains backslash-n (two chars), not a real newline.
        sqlglot cannot parse that skeleton but CAN parse after replacing \\n with newline.
        """
        # In Python source "\\n" is the two-char sequence backslash + n —
        # exactly what a YAML single-quoted string produces.
        sql = "SELECT\\n  date,\\n  SUM(x)\\nFROM t"
        assert has_literal_escaped_newlines(sql, dialect=None) is True

    def test_real_newline_passes(self):
        """SQL with actual newlines returns False (fast path: no \\\\n present)."""
        # Python \\n here IS a real newline character — correct YAML block scalar output.
        sql = "SELECT\n  date\nFROM t"
        assert has_literal_escaped_newlines(sql, dialect=None) is False

    def test_string_literal_with_backslash_n_passes(self):
        """SQL containing \\n inside a SQL string literal returns False (step 2 passes).

        sqlglot parses SELECT 'hello\\nworld' as a valid string literal —
        the check must not flag it because the SQL parses fine as-is.
        """
        # The SQL string literal contains backslash-n — sqlglot handles it as an
        # escape sequence inside the quoted string.
        sql = "SELECT 'my sentence\\na new line' FROM t"
        assert has_literal_escaped_newlines(sql, dialect=None) is False

    def test_both_fail_defers(self):
        """Broken SQL with no \\n returns False (genuine syntax error, not our issue)."""
        sql = "SELEKT broken stuff without any backslash-n"
        assert has_literal_escaped_newlines(sql, dialect=None) is False

    def test_dialect_passed_through(self):
        """The dialect kwarg is accepted; basic duckdb SQL with \\n is flagged."""
        sql = "SELECT\\n  1\\nFROM range(10)"
        assert has_literal_escaped_newlines(sql, dialect="duckdb") is True

    def test_sqlserver_dialect_normalized(self):
        """'sqlserver' dialect is normalized to 'tsql' — must not raise ValueError.

        sqlglot does not know 'sqlserver'; the mapping translates it to 'tsql'.
        This test would crash before the fix: sqlglot.parse(..., read='sqlserver')
        raises ValueError("dialect: sqlserver").
        """
        sql = "SELECT\\n  revenue\\nFROM sales"
        assert has_literal_escaped_newlines(sql, dialect="sqlserver") is True

    def test_mssql_dialect_normalized(self):
        """'mssql' is also mapped to 'tsql' and must not raise ValueError."""
        sql = "SELECT\\n  1"
        # Must not raise; accept either True or False depending on sqlglot tsql parsing.
        result = has_literal_escaped_newlines(sql, dialect="mssql")
        assert isinstance(result, bool)

    def test_mariadb_dialect_normalized(self):
        """'mariadb' is mapped to 'mysql' and must not raise ValueError."""
        sql = "SELECT\\n  revenue\\nFROM t"
        assert has_literal_escaped_newlines(sql, dialect="mariadb") is True


class TestFindDateLiteralVariable:
    """Unit tests for the `date '{{ var }}'` authoring-mistake detector.

    Only the bound-parameter adapters (duckdb, sqlite) bind a variable through
    a driver placeholder that `_clean_parameter_quotes` strips the quotes
    from, so only there does `date '{{ var }}'` compile to invalid `date $1`.
    Every other adapter (the dbt backend for postgres/snowflake/bigquery/etc.,
    DbtAdapter for dbt_profile) renders variables as inline literal text, so
    `date '{{ var }}'` compiles to valid `date '2024-01-01'` there -- the
    detector must not flag it. `cast('{{ var }}' as date)` is the
    dialect-agnostic fix.
    """

    def test_date_literal_variable_flagged_on_duckdb(self):
        sql = "SELECT 1 WHERE d <= date '{{ latest_month }}'"
        match = find_date_literal_variable(sql, dialect="duckdb")
        assert match is not None
        assert match.group(1) == "date"
        assert match.group(2) == "latest_month"

    def test_date_literal_variable_flagged_on_sqlite(self):
        sql = "SELECT 1 WHERE d <= date '{{ latest_month }}'"
        match = find_date_literal_variable(sql, dialect="sqlite")
        assert match is not None

    def test_timestamp_literal_variable_flagged_case_insensitive(self):
        sql = "SELECT 1 WHERE ts <= TIMESTAMP '{{ cutoff }}'"
        match = find_date_literal_variable(sql, dialect="duckdb")
        assert match is not None
        assert match.group(1).lower() == "timestamp"
        assert match.group(2) == "cutoff"

    def test_ordinary_string_literal_variable_passes(self):
        """`status = '{{ status }}'` is the normal, working shape -- not flagged."""
        sql = "SELECT 1 WHERE status = '{{ status }}'"
        assert find_date_literal_variable(sql, dialect="duckdb") is None

    def test_already_cast_variable_passes(self):
        """The documented fix, `cast('{{ var }}' as date)`, is never flagged."""
        sql = "SELECT 1 WHERE d <= cast('{{ latest_month }}' as date)"
        assert find_date_literal_variable(sql, dialect="duckdb") is None

    def test_date_literal_with_static_value_passes(self):
        """A plain date literal with no Jinja variable is ordinary, valid SQL."""
        sql = "SELECT 1 WHERE d <= date '2024-01-01'"
        assert find_date_literal_variable(sql, dialect="duckdb") is None

    def test_date_literal_variable_on_inline_dialect_passes(self):
        """A warehouse adapter (the dbt backend) renders variables as inline literal
        text -- `date '{{ var }}'` compiles to valid `date '2024-01-01'` there,
        so the bound-param-only detector must not flag it."""
        sql = "SELECT 1 WHERE d <= date '{{ latest_month }}'"
        assert find_date_literal_variable(sql, dialect="snowflake") is None

    def test_date_literal_variable_on_unknown_dialect_passes(self):
        """dialect=None (e.g. dbt_profile, whose real dialect isn't known until
        execute) means the bound-param path can't be confirmed -- do not flag."""
        sql = "SELECT 1 WHERE d <= date '{{ latest_month }}'"
        assert find_date_literal_variable(sql, dialect=None) is None


class TestLiteralNewlinesWiredIntoNormalizeQuery:
    """Integration: normalize_query raises CompilationError for literal \\n SQL."""

    def test_normalize_query_raises_for_literal_backslash_n(self):
        """normalize_query raises CompilationError when sql has literal \\n authoring error."""
        sql_with_literal_n = "SELECT\\n  revenue\\nFROM sales"
        with pytest.raises(CompilationError) as exc_info:
            normalize_query(
                "my_query",
                {"sql": sql_with_literal_n, "source": "mydb"},
                sources={},
            )
        err_msg = str(exc_info.value)
        assert "my_query" in err_msg
        assert "sql" in err_msg

    def test_normalize_query_real_newlines_ok(self):
        """normalize_query accepts SQL with real newlines (correct YAML block scalar)."""
        sql_real_newlines = "SELECT\n  revenue\nFROM sales"
        result = normalize_query(
            "my_query",
            {"sql": sql_real_newlines, "source": "mydb"},
            sources={},
        )
        assert isinstance(result, SqlQuery)

    def test_normalize_query_raises_for_literal_backslash_n_in_setup_sql(self):
        """normalize_query raises CompilationError when setup_sql has literal \\n.

        The error message names the query and mentions setup_sql (via the field_label
        field in the error code template), not via string-concatenation in query_name.
        """
        setup_sql_with_literal_n = (
            "CREATE TEMP TABLE tmp AS SELECT\\n  1 AS x\\nFROM range(1)"
        )
        with pytest.raises(CompilationError) as exc_info:
            normalize_query(
                "my_query",
                {
                    "sql": "SELECT x FROM tmp",
                    "setup_sql": setup_sql_with_literal_n,
                    "source": "mydb",
                },
                sources={},
            )
        err_msg = str(exc_info.value)
        assert "my_query" in err_msg
        assert "setup_sql" in err_msg


class TestDateLiteralVariableWiredIntoNormalizeQuery:
    """Integration: normalize_query raises a typed, authoring-tier CompilationError
    for `date '{{ var }}'` on a bound-param source (duckdb/sqlite) -- not left
    to surface as a confusing warehouse or internal error at execution time
    (FR-74). On every other source, the variable renders as inline literal
    text and the same SQL is valid, so normalize_query must not raise."""

    def test_normalize_query_raises_for_date_literal_variable_on_duckdb(self):
        with pytest.raises(CompilationError) as exc_info:
            normalize_query(
                "my_query",
                {
                    "sql": "SELECT 1 WHERE d <= date '{{ latest_month }}'",
                    "source": "mydb",
                },
                sources={"mydb": {"type": "duckdb"}},
            )
        error = exc_info.value
        assert error.code is not None
        assert error.code.code != "ERR-INTERNAL"
        err_msg = str(error)
        assert "my_query" in err_msg
        assert "cast('{{ latest_month }}' as date)" in err_msg

    def test_normalize_query_date_literal_variable_on_warehouse_source_passes(self):
        """Snowflake goes through the dbt backend, which renders variables as inline
        literal text -- `date '{{ var }}'` compiles to valid SQL there."""
        result = normalize_query(
            "my_query",
            {
                "sql": "SELECT 1 WHERE d <= date '{{ latest_month }}'",
                "source": "mydb",
            },
            sources={"mydb": {"type": "snowflake"}},
        )
        assert isinstance(result, SqlQuery)

    def test_normalize_query_date_literal_variable_on_dbt_profile_passes(self):
        """dbt_profile sources go through DbtAdapter, which also renders
        variables as inline literal text. dialect_for_source cannot resolve a
        dbt_profile's real dialect at compile time (it lives in the profile),
        so this also exercises the dialect=None path."""
        result = normalize_query(
            "my_query",
            {
                "sql": "SELECT 1 WHERE d <= date '{{ latest_month }}'",
                "source": "mydb",
            },
            sources={"mydb": {"type": "dbt_profile"}},
        )
        assert isinstance(result, SqlQuery)

    def test_normalize_query_cast_form_is_accepted(self):
        """The documented fix must compile without raising, on any source."""
        result = normalize_query(
            "my_query",
            {
                "sql": "SELECT 1 WHERE d <= cast('{{ latest_month }}' as date)",
                "source": "mydb",
            },
            sources={"mydb": {"type": "duckdb"}},
        )
        assert isinstance(result, SqlQuery)
