"""Tests for parameterized query rendering.

Tests the dbt_charts.core.compile.template.parameterized module for secure SQL rendering
that prevents SQL injection attacks.
"""

from datetime import date

import pytest
from jinja2 import Environment

from dbt_charts.core.compile.errors import JinjaError
from dbt_charts.core.compile.template._helpers import _LenientUndefined
from dbt_charts.core.compile.template.jinja import expand_query_refs
from dbt_charts.core.compile.template.parameterized import (
    ParameterizedQuery,
    _compute_template_hash,
    render_parameterized,
    render_parameterized_with_queries,
)
from dbt_charts.core.dialects import DIALECTS, get_dialect
from dbt_charts.core.execute.sql_literals import inline_dialect_params, inline_params


class TestRenderParameterized:
    """Tests for render_parameterized function."""

    def test_simple_variable(self):
        """Test parameterizing simple variable."""
        template = "SELECT * FROM users WHERE status = '{{ status }}'"
        variables = {"status": "active"}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert result.sql == "SELECT * FROM users WHERE status = $1"
        assert result.params == ["active"]
        assert result.template_hash != ""

    def test_multiple_variables(self):
        """Test parameterizing multiple variables."""
        template = """
SELECT * FROM orders
WHERE region = '{{ region }}'
  AND status = '{{ status }}'
"""
        variables = {"region": "North", "status": "active"}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "$1" in result.sql
        assert "$2" in result.sql
        assert len(result.params) == 2
        assert "North" in result.params
        assert "active" in result.params

    def test_same_variable_reused(self):
        """Test that same variable reuses same parameter."""
        template = """
SELECT * FROM orders
WHERE region = '{{ region }}'
   OR secondary_region = '{{ region }}'
"""
        variables = {"region": "North"}

        result = render_parameterized(template, variables, profile_type="postgres")

        # Same variable should use same parameter placeholder
        assert result.sql.count("$1") == 2
        assert len(result.params) == 1
        assert result.params[0] == "North"

    def test_numeric_variable(self):
        """Test parameterizing numeric variable."""
        template = "SELECT * FROM orders WHERE amount > {{ min_amount }}"
        variables = {"min_amount": 100}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "$1" in result.sql
        assert result.params == [100]

    def test_no_jinja_returns_original(self):
        """Test that SQL without Jinja is returned as-is."""
        template = "SELECT * FROM users WHERE id = 1"

        result = render_parameterized(template, {}, profile_type="postgres")

        assert result.sql == template
        assert result.params == []

    def test_empty_template(self):
        """Test empty template."""
        result = render_parameterized("", {}, profile_type="postgres")

        assert result.sql == ""
        assert result.params == []

    def test_template_hash_consistent(self):
        """Test that template hash is consistent for same template."""
        template = "SELECT * FROM users WHERE id = {{ id }}"

        result1 = render_parameterized(template, {"id": 1}, profile_type="postgres")
        result2 = render_parameterized(template, {"id": 2}, profile_type="postgres")

        # Same template should produce same hash regardless of values
        assert result1.template_hash == result2.template_hash

    def test_template_hash_differs_for_different_templates(self):
        """Test that different templates have different hashes."""
        template1 = "SELECT * FROM users WHERE id = {{ id }}"
        template2 = "SELECT * FROM orders WHERE id = {{ id }}"

        result1 = render_parameterized(template1, {"id": 1}, profile_type="postgres")
        result2 = render_parameterized(template2, {"id": 1}, profile_type="postgres")

        assert result1.template_hash != result2.template_hash


class TestDialectPlaceholders:
    """Tests for different database dialect placeholders."""

    def test_postgres_placeholder(self):
        """Test PostgreSQL $1, $2 style."""
        template = "SELECT * FROM t WHERE a = '{{ a }}' AND b = '{{ b }}'"
        variables = {"a": "x", "b": "y"}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "$1" in result.sql
        assert "$2" in result.sql

    def test_duckdb_placeholder(self):
        """Test DuckDB $1, $2 style (same as PostgreSQL)."""
        template = "SELECT * FROM t WHERE a = '{{ a }}'"
        variables = {"a": "x"}

        result = render_parameterized(template, variables, profile_type="duckdb")

        assert "$1" in result.sql

    def test_mysql_placeholder(self):
        """Test MySQL %s style."""
        template = "SELECT * FROM t WHERE a = '{{ a }}'"
        variables = {"a": "x"}

        result = render_parameterized(template, variables, profile_type="mysql")

        assert "%s" in result.sql

    def test_snowflake_placeholder(self):
        """Test Snowflake ? style."""
        template = "SELECT * FROM t WHERE a = '{{ a }}'"
        variables = {"a": "x"}

        result = render_parameterized(template, variables, profile_type="snowflake")

        assert "?" in result.sql

    def test_sqlserver_placeholder(self):
        """Test SQL Server @p1 style."""
        template = "SELECT * FROM t WHERE a = '{{ a }}'"
        variables = {"a": "x"}

        result = render_parameterized(template, variables, profile_type="sqlserver")

        assert "@p1" in result.sql

    def test_dialect_instance(self):
        """Test passing dialect instance directly."""
        template = "SELECT * FROM t WHERE a = '{{ a }}'"
        variables = {"a": "x"}
        dialect = get_dialect("postgres")

        result = render_parameterized(template, variables, dialect=dialect)

        assert "$1" in result.sql

    def test_string_filter_on_parameterized_variable_raises(self):
        """A string transform on a variable corrupts the placeholder token —
        the parameter would silently never be substituted. Raise at render,
        naming the variable, like `| int` / `| float` already do."""
        with pytest.raises(JinjaError, match="region"):
            render_parameterized(
                "SELECT * FROM t WHERE upper(r) = '{{ region | upper }}'",
                {"region": "north"},
                profile_type="postgres",
            )

    def test_replace_filter_on_parameterized_variable_raises(self):
        with pytest.raises(JinjaError, match="region"):
            render_parameterized(
                "SELECT * FROM t WHERE r = '{{ region | replace('a', 'b') }}'",
                {"region": "north"},
                profile_type="postgres",
            )

    def test_filter_that_removes_the_placeholder_raises(self):
        """`| urlencode` escapes the placeholder's characters after the value
        was collected — the token vanishes and the value would never be bound.
        The post-render presence check catches it, naming the variable."""
        with pytest.raises(JinjaError, match="region"):
            render_parameterized(
                "SELECT * FROM t WHERE r = '{{ region | string | urlencode }}'",
                {"region": "north"},
                profile_type="postgres",
            )

    def test_trim_filter_stays_allowed(self):
        """`| trim` cannot change a placeholder token (no edge whitespace) —
        it keeps rendering as before rather than breaking working boards."""
        result = render_parameterized(
            "SELECT * FROM t WHERE r = '{{ region | trim }}'",
            {"region": "North"},
            profile_type="postgres",
        )
        assert result.params == ["North"]
        assert "$1" in result.sql

    def test_inline_placeholder_quotes_stripped(self):
        """The internal inline style strips authored quotes like real dialects.

        Without this, '{{ a }}' renders to a quoted token and the inline round
        trip splices an already-quoted literal inside the author's quotes.
        """
        from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS

        template = "SELECT * FROM t WHERE a = '{{ a }}'"

        result = render_parameterized(template, {"a": "x"}, dialect=INLINE_PLACEHOLDERS)

        placeholder = INLINE_PLACEHOLDERS.param(1)
        assert f"a = {placeholder}" in result.sql
        assert f"'{placeholder}'" not in result.sql
        assert result.params == ["x"]


class TestSqlInjectionPrevention:
    """Tests for SQL injection prevention."""

    def test_injection_attempt_in_string(self):
        """Test that SQL injection in string variable is prevented."""
        template = "SELECT * FROM users WHERE name = '{{ name }}'"
        # Malicious input that would cause injection with string interpolation
        variables = {"name": "'; DROP TABLE users; --"}

        result = render_parameterized(template, variables, profile_type="postgres")

        # The malicious string should be in params, not in SQL
        assert "DROP TABLE" not in result.sql
        assert result.sql == "SELECT * FROM users WHERE name = $1"
        assert result.params == ["'; DROP TABLE users; --"]

    def test_injection_attempt_in_number(self):
        """Test that SQL injection in numeric context is prevented."""
        template = "SELECT * FROM orders WHERE id = {{ id }}"
        # Attempting injection through numeric field
        variables = {"id": "1; DROP TABLE orders; --"}

        result = render_parameterized(template, variables, profile_type="postgres")

        # Malicious string should be parameter, not in SQL
        assert "DROP TABLE" not in result.sql
        assert result.params == ["1; DROP TABLE orders; --"]

    def test_injection_with_quotes(self):
        """Test that quote escaping attacks are prevented."""
        template = "SELECT * FROM users WHERE name = '{{ name }}'"
        # Trying to break out of quotes
        variables = {"name": "admin' OR '1'='1"}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "OR '1'='1" not in result.sql
        assert result.params == ["admin' OR '1'='1"]

    def test_injection_with_union(self):
        """Test that UNION injection is prevented."""
        template = "SELECT * FROM products WHERE category = '{{ category }}'"
        variables = {"category": "' UNION SELECT password FROM users --"}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "UNION" not in result.sql
        assert result.params == ["' UNION SELECT password FROM users --"]

    def test_injection_with_comment(self):
        """Test that comment-based injection is prevented."""
        template = "SELECT * FROM orders WHERE id = {{ id }}"
        variables = {"id": "1 /* malicious comment */"}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "malicious" not in result.sql
        assert result.params == ["1 /* malicious comment */"]


class TestFilterHelper:
    """Tests for parameterized filter() helper."""

    def test_filter_basic(self):
        """Test basic filter with parameter."""
        template = "SELECT * FROM t WHERE {{ filter('status', status) }}"
        variables = {"status": "active"}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "status = $1" in result.sql
        assert result.params == ["active"]

    def test_filter_with_operator(self):
        """Test filter with custom operator."""
        template = "SELECT * FROM t WHERE {{ filter('amount', min_amount, '>') }}"
        variables = {"min_amount": 100}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "amount > $1" in result.sql
        assert result.params == [100]

    def test_filter_with_none(self):
        """Test filter with None value returns 1=1."""
        template = "SELECT * FROM t WHERE {{ filter('status', status) }}"
        variables = {"status": None}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "1=1" in result.sql
        assert result.params == []

    def test_filter_with_empty_string(self):
        """Test filter with empty string returns 1=1."""
        template = "SELECT * FROM t WHERE {{ filter('status', status) }}"
        variables = {"status": ""}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "1=1" in result.sql

    def test_filter_with_list(self):
        """Test filter with list generates IN clause."""
        template = "SELECT * FROM t WHERE {{ filter('category', categories) }}"
        variables = {"categories": ["A", "B", "C"]}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "category IN" in result.sql
        assert "$1" in result.sql
        assert "$2" in result.sql
        assert "$3" in result.sql
        assert result.params == ["A", "B", "C"]


class TestFilterDateRangeHelper:
    """Tests for parameterized filter_date_range() helper."""

    def test_date_range_basic(self):
        """Test basic date range filter."""
        template = (
            "SELECT * FROM t WHERE {{ filter_date_range('created_at', date_range) }}"
        )
        variables = {"date_range": ["2024-01-01", "2024-12-31"]}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "CAST(created_at AS DATE) BETWEEN $1 AND $2" in result.sql
        assert result.params == ["2024-01-01", "2024-12-31"]

    def test_date_range_inlined_for_bigquery_casts_timestamp_column(self):
        """A TIMESTAMP column compared against DATE bounds fails on BigQuery
        ("No matching signature for operator BETWEEN"). Casting the column to
        DATE is portable: Postgres/DuckDB/Snowflake coerce silently, and a DATE
        column casts to itself."""
        template = (
            "SELECT * FROM t WHERE {{ filter_date_range('created_at', date_range) }}"
        )
        variables = {"date_range": [date(2026, 9, 8), date(2026, 9, 30)]}

        result = render_parameterized(template, variables, profile_type="postgres")
        sql = inline_params(result.sql, result.params, get_dialect("bigquery"))

        assert (
            "CAST(created_at AS DATE) BETWEEN DATE '2026-09-08' AND DATE '2026-09-30'"
            in sql
        )

    def test_date_range_none(self):
        """Test date range with None returns 1=1."""
        template = (
            "SELECT * FROM t WHERE {{ filter_date_range('created_at', date_range) }}"
        )
        variables = {"date_range": None}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "1=1" in result.sql
        assert result.params == []

    @pytest.mark.parametrize(
        ("date_range", "expected_sql", "expected_params"),
        [
            (["2025-10-01", None], "CAST(created_at AS DATE) >= $1", ["2025-10-01"]),
            (["2025-10-01", ""], "CAST(created_at AS DATE) >= $1", ["2025-10-01"]),
            ([None, "2026-10-04"], "CAST(created_at AS DATE) <= $1", ["2026-10-04"]),
            ([None, None], "1=1", []),
        ],
    )
    def test_date_range_open_bound(
        self, date_range: list[str | None], expected_sql: str, expected_params: list
    ) -> None:
        """An open end leaves that side unbounded; the other bound still applies."""
        template = (
            "SELECT * FROM t WHERE {{ filter_date_range('created_at', date_range) }}"
        )

        result = render_parameterized(
            template, {"date_range": date_range}, profile_type="postgres"
        )

        assert expected_sql in result.sql
        assert result.params == expected_params

    def test_date_range_json_string(self):
        """Test date range from JSON string (URL parameters)."""
        template = (
            "SELECT * FROM t WHERE {{ filter_date_range('created_at', date_range) }}"
        )
        variables = {"date_range": '["2024-01-01", "2024-12-31"]'}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "BETWEEN" in result.sql
        assert len(result.params) == 2


class TestConditionalTemplates:
    """Tests for conditional Jinja templates with parameterization."""

    def test_if_condition(self):
        """Test if condition with parameterized variable."""
        template = """
SELECT * FROM orders
WHERE 1=1
{% if region %}
  AND region = '{{ region }}'
{% endif %}
"""
        result_with = render_parameterized(
            template, {"region": "North"}, profile_type="postgres"
        )
        result_without = render_parameterized(
            template, {"region": None}, profile_type="postgres"
        )

        assert "AND region = $1" in result_with.sql
        assert result_with.params == ["North"]
        # When region is None, it's falsy so the condition is skipped
        assert "$" not in result_without.sql

    def test_for_loop(self):
        """Test for loop still works - use filter() helper for parameterized IN.

        Note: For-loops with raw values don't get parameterized automatically.
        This is expected behavior. For parameterized IN clauses, use the
        filter() helper with a list instead.
        """
        # For-loops with raw iteration don't parameterize (expected behavior)
        template = """
SELECT * FROM orders
WHERE status IN ({% for s in statuses %}'{{ s }}'{% if not loop.last %}, {% endif %}{% endfor %})
"""
        variables = {"statuses": ["pending", "active", "completed"]}

        result = render_parameterized(template, variables, profile_type="postgres")

        # Loop iterates over raw values - they appear directly in SQL
        # This is expected behavior for Jinja loops
        assert "pending" in result.sql
        assert "active" in result.sql
        assert "completed" in result.sql

    def test_for_loop_use_filter_instead(self):
        """Test that filter() helper with list is the correct way to parameterize IN."""
        # Recommended approach: use filter() helper for parameterized IN clause
        template = "SELECT * FROM orders WHERE {{ filter('status', statuses) }}"
        variables = {"statuses": ["pending", "active", "completed"]}

        result = render_parameterized(template, variables, profile_type="postgres")

        # Filter helper properly parameterizes the list
        assert "status IN" in result.sql
        assert "$1" in result.sql
        assert "$2" in result.sql
        assert "$3" in result.sql
        assert result.params == ["pending", "active", "completed"]


class TestLenientUndefined:
    """Tests for non-strict undefined behavior during parameterized rendering."""

    def test_bracket_access_remains_lenient(self):
        """Undefined bracket access should stay chainable in non-strict mode."""
        env = Environment(undefined=_LenientUndefined)
        template = env.from_string("{{ missing['nested'].value }}")

        assert template.render() == ""


class TestNullHandling:
    """Tests for NULL value handling."""

    def test_null_renders_as_null(self):
        """Test that None variables render as NULL."""
        template = "SELECT * FROM t WHERE deleted_at = {{ deleted_at }}"
        variables = {"deleted_at": None}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "NULL" in result.sql
        # None values should not be parameterized (they render as SQL NULL)
        assert result.params == []


class TestQueryReferences:
    """Tests for {{ queries.* }} reference handling."""

    def test_query_reference(self):
        """Test that query references are resolved."""
        template = """
WITH base AS (
  {{ queries.base_query }}
)
SELECT * FROM base WHERE region = '{{ region }}'
"""
        queries = {"base_query": {"sql": "SELECT * FROM orders"}}
        variables = {"region": "North"}

        result = render_parameterized_with_queries(
            template,
            variables=variables,
            queries=queries,
            profile_type="postgres",
        )

        assert "SELECT * FROM orders" in result.sql
        assert "region = $1" in result.sql
        assert result.params == ["North"]


class TestParameterizedQueryDataclass:
    """Tests for ParameterizedQuery dataclass."""

    def test_default_values(self):
        """Test default values for ParameterizedQuery."""
        query = ParameterizedQuery(sql="SELECT 1")

        assert query.sql == "SELECT 1"
        assert query.params == []
        assert query.template_hash == ""

    def test_with_all_fields(self):
        """Test ParameterizedQuery with all fields."""
        query = ParameterizedQuery(
            sql="SELECT * FROM t WHERE id = $1",
            params=[42],
            template_hash="abc123",
        )

        assert query.sql == "SELECT * FROM t WHERE id = $1"
        assert query.params == [42]
        assert query.template_hash == "abc123"


class TestComputeTemplateHash:
    """Tests for template hash computation."""

    def test_hash_is_deterministic(self):
        """Test that hash is deterministic."""
        template = "SELECT * FROM users"

        hash1 = _compute_template_hash(template)
        hash2 = _compute_template_hash(template)

        assert hash1 == hash2

    def test_hash_differs_for_different_templates(self):
        """Test that different templates produce different hashes."""
        hash1 = _compute_template_hash("SELECT * FROM users")
        hash2 = _compute_template_hash("SELECT * FROM orders")

        assert hash1 != hash2


class TestOperatorValidation:
    """Tests for operator validation in filter helpers."""

    def test_valid_operators_accepted(self):
        """Test that valid operators are accepted."""
        valid_ops = ["=", "!=", "<>", ">", "<", ">=", "<=", "LIKE", "IN"]

        for op in valid_ops:
            template = f"SELECT * FROM t WHERE {{{{ filter('col', val, '{op}') }}}}"
            variables = {"val": "test"}

            result = render_parameterized(template, variables, profile_type="postgres")
            # Should not raise, should contain the operator
            assert op in result.sql or "IN" in result.sql or "$1" in result.sql

    def test_invalid_operator_raises_error(self):
        """Test that invalid operators raise an error."""
        import pytest

        from dbt_charts.core.compile.errors import JinjaError

        template = "SELECT * FROM t WHERE {{ filter('col', val, 'INVALID_OP') }}"
        variables = {"val": "test"}

        with pytest.raises(JinjaError) as exc_info:
            render_parameterized(template, variables, profile_type="postgres")

        assert "Invalid SQL operator" in str(exc_info.value)

    def test_sql_injection_via_operator_prevented(self):
        """Test that SQL injection via operator is prevented."""
        import pytest

        from dbt_charts.core.compile.errors import JinjaError

        # Attempt to inject SQL via operator
        template = "SELECT * FROM t WHERE {{ filter('col', val, '= 1; DROP TABLE users; --') }}"
        variables = {"val": "test"}

        with pytest.raises(JinjaError) as exc_info:
            render_parameterized(template, variables, profile_type="postgres")

        assert "Invalid SQL operator" in str(exc_info.value)


class TestColumnNameValidation:
    """Tests for column name validation in filter helpers."""

    def test_valid_column_names_accepted(self):
        """Test that valid column names are accepted."""
        valid_columns = [
            "status",
            "user_id",
            "created_at",
            "orders.status",
            "t.column_name",
            # Qualification depth is not capped: a table outside the default
            # schema must be nameable here too, or `column: schema.table.col`
            # has no working spelling in the query that consumes it.
            "gis.fact_sales.property_type",
            "warehouse.gis.fact_sales.property_type",
        ]

        for col in valid_columns:
            template = f"SELECT * FROM t WHERE {{{{ filter('{col}', val) }}}}"
            variables = {"val": "test"}

            result = render_parameterized(template, variables, profile_type="postgres")
            assert col in result.sql

    def test_invalid_column_name_raises_error(self):
        """Test that invalid column names raise an error."""
        import pytest

        from dbt_charts.core.compile.errors import JinjaError

        # Column name with invalid characters
        template = "SELECT * FROM t WHERE {{ filter('col; DROP TABLE users', val) }}"
        variables = {"val": "test"}

        with pytest.raises(JinjaError) as exc_info:
            render_parameterized(template, variables, profile_type="postgres")

        assert "Invalid column name" in str(exc_info.value)

    def test_sql_injection_via_column_name_prevented(self):
        """Test that SQL injection via column name is prevented."""
        import pytest

        from dbt_charts.core.compile.errors import JinjaError

        # Attempt to inject SQL via column name
        # These should all raise JinjaError (either column validation or template syntax error)
        malicious_columns = [
            "col = 1; DROP TABLE users; --",
            "col); DELETE FROM users; --",
            "col--",
            "col/*comment*/",
            # Dots join bare identifiers and nothing else — an empty part is as
            # invalid as an illegal character.
            "gis..fact_sales",
            ".fact_sales.col",
            "gis.fact_sales.",
            "gis.fact sales.col",
            'gis."fact_sales".col',
        ]

        for col in malicious_columns:
            template = f"SELECT * FROM t WHERE {{{{ filter('{col}', val) }}}}"
            variables = {"val": "test"}

            with pytest.raises(JinjaError) as exc_info:
                render_parameterized(template, variables, profile_type="postgres")

            # Should fail with column validation error
            assert "Invalid column name" in str(exc_info.value)

    def test_sql_injection_with_quotes_causes_template_error(self):
        """Test that injection attempts with quotes break template syntax."""
        import pytest

        from dbt_charts.core.compile.errors import JinjaError

        # Column names with quotes break Jinja template parsing itself
        # This is also a valid security protection - template fails before execution
        template = "SELECT * FROM t WHERE {{ filter('col\\' OR \\'1\\'=\\'1', val) }}"
        variables = {"val": "test"}

        # Should raise some kind of JinjaError (syntax or validation)
        with pytest.raises(JinjaError):
            render_parameterized(template, variables, profile_type="postgres")

    def test_table_qualified_column_name(self):
        """Test that table.column format is allowed."""
        template = "SELECT * FROM t WHERE {{ filter('orders.status', val) }}"
        variables = {"val": "active"}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "orders.status = $1" in result.sql
        assert result.params == ["active"]

    def test_qualified_column_survives_to_the_sql(self):
        """schema.table.column reaches the emitted SQL intact, dots and all."""
        template = "SELECT * FROM t WHERE {{ filter('gis.fact_sales.status', val) }}"

        result = render_parameterized(
            template, {"val": "test"}, profile_type="postgres"
        )

        assert "gis.fact_sales.status" in result.sql


class TestDateRangeColumnValidation:
    """Tests for column validation in filter_date_range helper."""

    def test_valid_column_in_date_range(self):
        """Test valid column name in date range filter."""
        template = (
            "SELECT * FROM t WHERE {{ filter_date_range('created_at', date_range) }}"
        )
        variables = {"date_range": ["2024-01-01", "2024-12-31"]}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "CAST(created_at AS DATE) BETWEEN" in result.sql

    def test_invalid_column_in_date_range_raises_error(self):
        """Test invalid column name in date range filter raises error."""
        import pytest

        from dbt_charts.core.compile.errors import JinjaError

        template = "SELECT * FROM t WHERE {{ filter_date_range('col; DROP TABLE', date_range) }}"
        variables = {"date_range": ["2024-01-01", "2024-12-31"]}

        with pytest.raises(JinjaError) as exc_info:
            render_parameterized(template, variables, profile_type="postgres")

        assert "Invalid column name" in str(exc_info.value)


class TestValidationHelperFunctions:
    """Tests for the validation helper functions."""

    def test_validate_identifier_valid(self):
        """Test _validate_identifier with valid identifiers."""
        from dbt_charts.core.compile.template.parameterized import _validate_identifier

        # These should not raise
        _validate_identifier("column")
        _validate_identifier("user_id")
        _validate_identifier("_private")
        _validate_identifier("Column123")
        _validate_identifier("orders.status")
        _validate_identifier("t.col")

    def test_validate_identifier_invalid(self):
        """Test _validate_identifier with invalid identifiers."""
        import pytest

        from dbt_charts.core.compile.template.parameterized import _validate_identifier

        invalid = [
            "",
            "123column",  # starts with number
            "col-name",  # hyphen
            "col name",  # space
            "col;drop",  # semicolon
            "col'quote",  # quote
            "a..c",  # empty part between dots
            ".a.c",  # empty leading part
            "a.c.",  # empty trailing part
        ]

        for ident in invalid:
            with pytest.raises(ValueError, match="Invalid column name"):
                _validate_identifier(ident)

    def test_validate_operator_valid(self):
        """Test _validate_operator with valid operators."""
        from dbt_charts.core.compile.template.parameterized import _validate_operator

        valid_ops = [
            "=",
            "!=",
            "<>",
            ">",
            "<",
            ">=",
            "<=",
            "LIKE",
            "like",
            "IN",
            "NOT IN",
        ]

        for op in valid_ops:
            result = _validate_operator(op)
            assert result == op  # Returns the original operator

    def test_validate_operator_invalid(self):
        """Test _validate_operator with invalid operators."""
        import pytest

        from dbt_charts.core.compile.template.parameterized import _validate_operator

        invalid_ops = ["INVALID", "DROP", "=;", "= OR 1=1", ""]

        for op in invalid_ops:
            with pytest.raises(ValueError, match="Invalid SQL operator"):
                _validate_operator(op)


class TestParameterReuse:
    """Tests for parameter reuse behavior with same variable/different values."""

    def test_same_variable_same_value_reuses_param(self):
        """Test that same variable with same value reuses the same parameter."""
        template = """
SELECT * FROM orders
WHERE region = '{{ region }}'
   OR backup_region = '{{ region }}'
"""
        variables = {"region": "North"}

        result = render_parameterized(template, variables, profile_type="postgres")

        # Same variable, same value should use same parameter
        assert result.sql.count("$1") == 2
        assert len(result.params) == 1
        assert result.params[0] == "North"

    def test_different_variables_same_value_different_params(self):
        """Test that different variables with same value get different parameters."""
        template = """
SELECT * FROM orders
WHERE region = '{{ region }}'
  AND country = '{{ country }}'
"""
        variables = {"region": "North", "country": "North"}

        result = render_parameterized(template, variables, profile_type="postgres")

        # Different variables should get 2 distinct parameter slots
        assert len(result.params) == 2
        # Both parameters carry "North" as the value
        assert all(p == "North" for p in result.params)

    def test_jinja_set_creates_new_context(self):
        """Test behavior when Jinja {% set %} is used.

        Note: {% set %} in Jinja creates a new variable in the template context,
        it doesn't modify our wrapped _ParameterizedValue. The set value is
        a raw string, not parameterized (which is safe for static template content).
        """
        # When {% set %} is used, it overwrites our wrapped variable
        # The new value is a raw string in the template
        template = """
SELECT '{{ status }}' as before,
{% set status = 'modified' %}
'{{ status }}' as after
"""
        variables = {"status": "original"}

        result = render_parameterized(template, variables, profile_type="postgres")

        # First use of status uses the parameterized value
        assert "$1" in result.sql
        assert "original" in result.params or result.params == ["original"]

        # The {% set %} creates a new raw string variable (appears directly in SQL)
        # This is expected Jinja behavior - template authors control this
        assert "modified" in result.sql

    def test_same_variable_repeated_snowflake_union(self):
        """Regression: same variable used N times in a UNION ALL produces N params for ?-style."""
        template = (
            "SELECT a FROM t WHERE d >= DATEADD(day, -{{ days_back }}, CURRENT_DATE()) "
            "UNION ALL "
            "SELECT b FROM t WHERE d >= DATEADD(day, -{{ days_back }}, CURRENT_DATE()) "
            "UNION ALL "
            "SELECT c FROM t WHERE d >= DATEADD(day, -{{ days_back }}, CURRENT_DATE())"
        )
        result = render_parameterized(
            template, {"days_back": 90}, profile_type="snowflake"
        )
        assert result.sql.count("?") == 3
        assert result.params == [90, 90, 90]

    def test_same_variable_repeated_mysql_union(self):
        """Same regression for %s-style (MySQL)."""
        template = (
            "SELECT a FROM t WHERE x = '{{ val }}' "
            "UNION ALL "
            "SELECT b FROM t WHERE x = '{{ val }}'"
        )
        result = render_parameterized(template, {"val": "foo"}, profile_type="mysql")
        assert result.sql.count("%s") == 2
        assert result.params == ["foo", "foo"]

    def test_filter_helper_different_values_different_params(self):
        """Test that filter helper creates separate params for different values."""
        template = """
SELECT * FROM orders
WHERE {{ filter('status', status1) }}
  AND {{ filter('type', status2) }}
"""
        variables = {"status1": "active", "status2": "pending"}

        result = render_parameterized(template, variables, profile_type="postgres")

        assert "$1" in result.sql
        assert "$2" in result.sql
        assert len(result.params) == 2
        assert "active" in result.params
        assert "pending" in result.params


class TestNumericCoercionFilters:
    """Regression tests for | int and | float on parameterized variables.

    Jinja's built-in | int filter calls int(value) and catches TypeError,
    returning 0 on failure — so {{ n | int }} on a _ParameterizedValue silently
    emits 0 into SQL. These tests assert that the coercion dunders raise instead.
    """

    def test_int_filter_raises_on_parameterized_variable(self):
        """{{ n | int }} must raise, not emit 0."""
        with pytest.raises(JinjaError, match="'n'"):
            render_parameterized(
                "SELECT {{ n | int }} AS via_int",
                {"n": 3},
                profile_type="duckdb",
            )

    def test_int_filter_arithmetic_raises(self):
        """{{ n | int - 1 }} must raise, not emit -1."""
        with pytest.raises(JinjaError, match="'n'"):
            render_parameterized(
                "SELECT {{ n | int - 1 }} AS arith",
                {"n": 3},
                profile_type="duckdb",
            )

    def test_float_filter_raises_on_parameterized_variable(self):
        """{{ n | float }} must raise, not emit 0.0."""
        with pytest.raises(JinjaError, match="'n'"):
            render_parameterized(
                "SELECT {{ n | float }} AS via_float",
                {"n": 3},
                profile_type="duckdb",
            )

    def test_plain_variable_still_parameterizes(self):
        """{{ n }} without a coercion filter still emits a bound parameter."""
        result = render_parameterized(
            "SELECT {{ n }} AS val",
            {"n": 3},
            profile_type="duckdb",
        )
        assert "$1" in result.sql
        assert result.params == [3]

    def test_interval_int_filter_raises_no_double_dash(self):
        """Regression for INTERVAL -{{ var | int - 1 }} MONTH.

        Before the fix, {{ var | int - 1 }} emitted -1, so -(-1) = --1
        which starts a SQL comment and silently drops the rest of the clause.
        After the fix, this raises rather than producing broken SQL.
        """
        with pytest.raises(JinjaError, match="'months'"):
            render_parameterized(
                "WHERE dt >= DATE_SUB(CURRENT_DATE(), INTERVAL -{{ months | int - 1 }} MONTH)",
                {"months": 3},
                profile_type="duckdb",
            )

    def test_sql_arithmetic_on_variable_is_the_supported_form(self):
        """{{ n }} - 1 in SQL produces $1 - 1, not 0 - 1."""
        result = render_parameterized(
            "INTERVAL -({{ n }} - 1) MONTH",
            {"n": 3},
            profile_type="duckdb",
        )
        assert "$1 - 1" in result.sql
        assert result.params == [3]

    def test_int_filter_error_not_double_prefixed(self):
        """{{ n | int }} error message must not carry 'Jinja template error:' twice.

        JinjaError.__init__ already formats the message through ERR_JINJA_ERROR
        ('Jinja template error: {message}').  If the except-JinjaError handler
        re-raises via JinjaError(str(e), ...), str(e) already carries the prefix
        and it is applied a second time.  The handler must be absent so the
        dunder's error propagates unchanged.
        """
        with pytest.raises(JinjaError) as exc_info:
            render_parameterized(
                "SELECT {{ n | int }} AS via_int",
                {"n": 3},
                profile_type="duckdb",
            )
        assert not str(exc_info.value).startswith(
            "Jinja template error: Jinja template error:"
        )


def _render_composed(name, queries, variables):
    """Render a query the way execution does: expand references, then render once.

    Expansion is textual and variable-blind, so the render below is the only one
    that ever sees a variable — which is why the composed and direct paths cannot
    drift apart. These cases pin that they do not.
    """
    return render_parameterized(
        expand_query_refs(name, queries), variables, profile_type="postgres"
    )


class TestComposedAndDirectPathsAgree:
    """Adding or removing a {{ queries.X }} reference must not change what a
    query means. Both paths run the same single render, so this is structural —
    these cases keep it that way."""

    def test_int_filter_raises_on_the_composed_path_too(self):
        """{{ n | int }} raises whether or not the query composes another.

        Silently coercing would emit different SQL for the same authored query
        depending on an unrelated reference.
        """
        queries = {
            "base": {"sql": "SELECT 1 AS one"},
            "viaref": {
                "sql": "SELECT {{ n | int }} AS via_int FROM {{ queries.base }}"
            },
        }
        with pytest.raises(JinjaError, match="'n'"):
            _render_composed("viaref", queries, {"n": 3})

    def test_numeric_comparison_raises_jinja_error(self):
        """{% if threshold > 0 %} raises JinjaError, not a bare TypeError.

        The parameterized wrapper has no ordering dunders — a value that reaches
        the warehouse as a bound parameter cannot also be compared at render
        time. The render converts the TypeError into a JinjaError so the author
        gets a message naming the variable.
        """
        queries = {
            "base": {"sql": "SELECT one FROM t"},
            "filtered": {
                "sql": (
                    "SELECT * FROM {{ queries.base }} "
                    "{% if threshold > 0 %}WHERE one >= {{ threshold }}{% endif %}"
                )
            },
        }
        with pytest.raises(JinjaError):
            _render_composed("filtered", queries, {"threshold": 5})

    def test_eq_comparison_holds_on_both_paths(self):
        """{% if v == 'x' %} must emit the same SQL composed or not.

        If the wrapper's __eq__ fell back to identity the condition would be
        silently False and the WHERE clause would vanish.
        """
        direct = {"q": {"sql": "{% if country == 'US' %}WHERE 1=1{% endif %}"}}
        composed = {
            "base": {"sql": "SELECT 1"},
            "q": {
                "sql": (
                    "{% if country == 'US' %}WHERE 1=1{% endif %} "
                    "FROM {{ queries.base }}"
                )
            },
        }

        assert "WHERE 1=1" in _render_composed("q", direct, {"country": "US"}).sql
        assert "WHERE 1=1" in _render_composed("q", composed, {"country": "US"}).sql

    def test_in_membership_holds_on_both_paths(self):
        """{% if v in [...] %} must emit the same SQL composed or not."""
        direct = {
            "q": {"sql": "{% if status in ['active', 'pending'] %}WHERE 1=1{% endif %}"}
        }
        composed = {
            "base": {"sql": "SELECT 1"},
            "q": {
                "sql": (
                    "{% if status in ['active', 'pending'] %}WHERE 1=1{% endif %}"
                    " FROM {{ queries.base }}"
                )
            },
        }

        assert "WHERE 1=1" in _render_composed("q", direct, {"status": "active"}).sql
        assert "WHERE 1=1" in _render_composed("q", composed, {"status": "active"}).sql

    def test_none_variable_renders_as_null(self):
        """A None variable inlines as NULL, not the string 'None'."""
        queries = {
            "base": {"sql": "SELECT 1"},
            "q": {"sql": "SELECT {{ n }} FROM {{ queries.base }}"},
        }

        result = _render_composed("q", queries, {"n": None})

        assert "NULL" in result.sql
        assert "None" not in result.sql

    def test_a_value_is_bound_not_inlined(self):
        """The whole point: a composed query's variable becomes a parameter.

        A value inlined as SQL text would carry both of the defects this suite
        exists for — it could close the surrounding literal, and it would be
        sitting in template position for anything that rendered the result again.
        """
        queries = {
            "base": {"sql": "SELECT id, status FROM t"},
            "q": {"sql": "SELECT * FROM {{ queries.base }} WHERE status = '{{ s }}'"},
        }

        result = _render_composed("q", queries, {"s": "active' OR '1'='1"})

        assert result.params == ["active' OR '1'='1"]
        assert "OR" not in result.sql


class TestQuoteStrippingAcrossDialects:
    """Quote stripping must hold for every registered dialect, not a hardcoded few.

    `_clean_parameter_quotes` used to branch on `dialect.name`, so any dialect
    missing from its chain inlined a quoted literal inside the author's quotes.
    Parametrizing over the registry itself is what makes a newly added dialect
    fail here until it is handled — the alias keys are deliberately included,
    since four of the originally broken eight were aliases.
    """

    # Two variables, so the 1..param_count loop is exercised: a stripper narrowed
    # to param(1) leaves the second placeholder quoted on named-param dialects.
    QUOTED = "SELECT * FROM t WHERE name = '{{ v }}' AND team = '{{ w }}'"
    BARE = "SELECT * FROM t WHERE name = {{ v }} AND team = {{ w }}"
    VALUES = {"v": "abc", "w": "xyz"}

    @pytest.mark.parametrize("profile_type", sorted(DIALECTS))
    def test_authored_quotes_are_stripped_from_placeholder(self, profile_type):
        """`'{{ v }}'` and `{{ v }}` must render the same placeholder-bearing SQL."""
        quoted = render_parameterized(
            self.QUOTED, self.VALUES, profile_type=profile_type
        )
        bare = render_parameterized(self.BARE, self.VALUES, profile_type=profile_type)

        assert quoted.sql == bare.sql, (
            f"{profile_type}: authored quotes survived around the placeholder"
        )

    @pytest.mark.parametrize("profile_type", sorted(DIALECTS))
    def test_no_quote_adjacent_to_placeholder(self, profile_type):
        """No quote may remain on either side of any emitted placeholder."""
        dialect = get_dialect(profile_type)
        result = render_parameterized(
            self.QUOTED, self.VALUES, profile_type=profile_type
        )

        for placeholder in dialect.params(len(self.VALUES)):
            assert placeholder in result.sql
            for quote in ("'", '"'):
                assert f"{quote}{placeholder}" not in result.sql
                assert f"{placeholder}{quote}" not in result.sql

    @pytest.mark.parametrize("profile_type", sorted(DIALECTS))
    def test_empty_value_does_not_emit_quadruple_quote(self, profile_type):
        """An empty value is what escalates wrong SQL into a tokenizer crash.

        The `''''` is only produced once the placeholder is inlined as a literal,
        so surviving quotes plus an empty value give `''''`, which opens a
        triple-quoted string on BigQuery — sqlglot then scans to EOF and raises
        TokenError instead of returning a wrong result.

        `inline_dialect_params` is the inliner production dispatches to for the
        named-param dialects only (`sql_adapter` routes `?` and `$N`/`%s`
        elsewhere); it is applied to all of them here as a uniform stand-in for
        "the placeholder became a literal", which is what the assertion turns on.
        """
        dialect = get_dialect(profile_type)
        result = render_parameterized(
            "SELECT * FROM t WHERE name = '{{ v }}'",
            {"v": ""},
            profile_type=profile_type,
        )

        inlined = inline_dialect_params(
            result.sql, result.params, dialect.param, dialect
        )

        assert "''''" not in inlined
