"""Tests for unified query interface types.

Tests the dbt_charts.core.compile.models.query.normalized module with type-specific
query classes and type guards.
"""

import pytest

from dbt_charts.core.compile.models.query.normalized import (
    VALID_QUERY_TYPES,
    SchemaQuery,
    SqlQuery,
    ValuesQuery,
    is_sql_query,
    is_values_query,
)


class TestSqlQuery:
    """Tests for SqlQuery class."""

    def test_create_sql_query(self):
        """Test creating a SQL query."""
        query = SqlQuery(sql="SELECT * FROM users", source="test_profile")
        assert query.sql == "SELECT * FROM users"
        assert query.source == "test_profile"
        assert query.query_type == "sql"

    def test_source_description(self):
        """Test source description for SQL query."""
        query = SqlQuery(sql="SELECT * FROM users", source="test_profile")
        assert "SQL:" in query.source_description
        assert "SELECT" in query.source_description

    def test_common_fields(self):
        """Test common fields inherited from Query."""
        query = SqlQuery(sql="SELECT 1", source="test_profile", limit=100)
        assert query.limit == 100

    def test_note_field(self):
        """Test optional query notes metadata field."""
        query = SqlQuery(
            sql="SELECT 1",
            source="test_profile",
            notes="Smoke test query",
        )
        assert query.notes == "Smoke test query"

    def test_source_optional_for_ad_hoc(self):
        """A *compiled* SqlQuery always names its source (normalize_query enforces
        ERR-SOURCE-REQUIRED), but the field is optional because the ad-hoc
        runtime boundary constructs a sourceless query that runs against the
        scratch DuckDB locally and is rejected by the guarded resolver on hosted
        surfaces."""
        assert SqlQuery(sql="SELECT 1").source is None

    def test_other_query_types_construct_without_source(self):
        """Sourceless query types construct without a source, same as SqlQuery."""
        ValuesQuery(rows=[{"a": 1}])
        assert SchemaQuery(source=None).source is None


class TestTypeGuards:
    """Tests for type guard functions."""

    @pytest.mark.parametrize(
        ("guard", "matching", "non_matching"),
        [
            (
                is_sql_query,
                SqlQuery(sql="SELECT 1", source="test_profile"),
                ValuesQuery(rows=[{"a": 1}]),
            ),
        ],
    )
    def test_type_guard(self, guard, matching, non_matching):
        """Each type guard returns True for its own type and False for others."""
        assert guard(matching)
        assert not guard(non_matching)


class TestApplyLimit:
    """Tests for Query.apply_limit method."""

    def test_apply_limit(self):
        """Test apply_limit with limit set."""
        query = SqlQuery(sql="SELECT 1", source="test_profile", limit=2)
        data = [{"a": 1}, {"a": 2}, {"a": 3}, {"a": 4}]

        result = query.apply_limit(data)
        assert len(result) == 2

    def test_apply_limit_no_limit(self):
        """Test apply_limit with no limit set."""
        query = SqlQuery(sql="SELECT 1", source="test_profile")
        data = [{"a": 1}, {"a": 2}, {"a": 3}]

        result = query.apply_limit(data)
        assert len(result) == 3

    def test_apply_limit_zero(self):
        """Test apply_limit with zero limit."""
        query = SqlQuery(sql="SELECT 1", source="test_profile", limit=0)
        data = [{"a": 1}, {"a": 2}]

        result = query.apply_limit(data)
        # Zero limit means no limit
        assert len(result) == 2


class TestValidQueryTypes:
    """Tests for VALID_QUERY_TYPES constant."""

    def test_contains_all_types(self):
        """Test VALID_QUERY_TYPES contains all expected types."""
        assert "sql" in VALID_QUERY_TYPES

    def test_contains_values_type(self):
        """Test VALID_QUERY_TYPES contains values."""
        assert "values" in VALID_QUERY_TYPES

    def test_query_types_match_classes(self):
        """Test query types match their class query_type properties."""
        assert (
            SqlQuery(sql="SELECT 1", source="test_profile").query_type
            in VALID_QUERY_TYPES
        )
        assert ValuesQuery(rows=[{"a": 1}]).query_type in VALID_QUERY_TYPES


class TestValuesQuery:
    """Tests for ValuesQuery class."""

    def test_create_values_query(self):
        """Test creating a values query with inline rows."""
        rows = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 140}]
        query = ValuesQuery(rows=rows)
        assert query.rows == rows
        assert query.query_type == "values"

    def test_source_description(self):
        """Test source description for values query."""
        query = ValuesQuery(rows=[{"a": 1}, {"a": 2}, {"a": 3}])
        assert "Values" in query.source_description
        assert "3 rows" in query.source_description

    def test_source_description_single_row(self):
        """Test source description for single-row values query."""
        query = ValuesQuery(rows=[{"a": 1}])
        assert "1 row" in query.source_description

    def test_source_description_unique_per_content(self):
        """Distinct ValuesQuery instances with same row count produce different cache keys."""
        q1 = ValuesQuery(rows=[{"metric": "Top ZASI", "value": 85}])
        q2 = ValuesQuery(rows=[{"metric": "Median ZASI", "value": 52}])
        q3 = ValuesQuery(rows=[{"metric": "Top ZASI", "value": 85}])
        assert q1.source_description != q2.source_description
        assert q1.source_description == q3.source_description

    def test_source_description_cache_not_in_model_dump(self):
        """Cached _cached_source_description should not leak into serialization."""
        q = ValuesQuery(rows=[{"a": 1}])
        _ = q.source_description  # trigger caching
        dumped = q.model_dump()
        assert "_cached_source_description" not in dumped

    def test_empty_rows(self):
        """Test values query with empty rows list."""
        query = ValuesQuery(rows=[])
        assert query.rows == []
        assert query.query_type == "values"

    def test_with_limit(self):
        """Test values query respects limit."""
        rows = [{"a": i} for i in range(10)]
        query = ValuesQuery(rows=rows, limit=3)
        assert query.apply_limit(rows) == [{"a": 0}, {"a": 1}, {"a": 2}]

    def test_columns_and_values_syntax(self):
        """Test SQL-style columns + values compact syntax."""
        query = ValuesQuery(
            columns=["month", "revenue"],
            values=[["Jan", 100], ["Feb", 140], ["Mar", 180]],
        )
        assert query.rows == [
            {"month": "Jan", "revenue": 100},
            {"month": "Feb", "revenue": 140},
            {"month": "Mar", "revenue": 180},
        ]

    def test_columns_and_values_single_row(self):
        """Test columns + values with a single row."""
        query = ValuesQuery(
            columns=["x", "y"],
            values=[[1, 2]],
        )
        assert query.rows == [{"x": 1, "y": 2}]

    def test_columns_and_values_empty(self):
        """Test columns + values with no value rows."""
        query = ValuesQuery(
            columns=["x", "y"],
            values=[],
        )
        assert query.rows == []

    def test_columns_values_length_mismatch_raises(self):
        """Test that mismatched columns/values lengths raise an error."""
        import pytest

        with pytest.raises(ValueError, match="row 0 has 2 items, expected 3"):
            ValuesQuery(
                columns=["a", "b", "c"],
                values=[[1, 2]],
            )

    def test_type_guard(self):
        """Test is_values_query type guard."""
        values_query = ValuesQuery(rows=[{"a": 1}])
        sql_query = SqlQuery(sql="SELECT 1", source="test_profile")

        assert is_values_query(values_query)
        assert not is_values_query(sql_query)


class TestValuesQueryNormalization:
    """Test that values queries are correctly inferred and normalized from dicts."""

    def test_infer_from_rows_key(self):
        """Test type inference from 'rows' key in dict."""
        from dbt_charts.core.compile.normalize.dispatch import normalize_query

        query = normalize_query("demo", {"rows": [{"a": 1}, {"a": 2}]}, sources={})
        assert isinstance(query, ValuesQuery)
        assert query.query_type == "values"
        assert len(query.rows) == 2

    def test_infer_from_values_key(self):
        """Test type inference from 'values' key (columns+values syntax)."""
        from dbt_charts.core.compile.normalize.dispatch import normalize_query

        query = normalize_query(
            "demo",
            {"columns": ["x", "y"], "values": [[1, 2], [3, 4]]},
            sources={},
        )
        assert isinstance(query, ValuesQuery)
        assert query.rows == [{"x": 1, "y": 2}, {"x": 3, "y": 4}]

    def test_explicit_type(self):
        """Test explicit type: values with rows."""
        from dbt_charts.core.compile.normalize.dispatch import normalize_query

        query = normalize_query(
            "demo",
            {"type": "values", "rows": [{"month": "Jan", "rev": 100}]},
            sources={},
        )
        assert isinstance(query, ValuesQuery)
        assert query.rows[0]["month"] == "Jan"
