"""Regression tests: dict[str, Any] fields replaced with specific Pydantic types.

Covers:
- Variable.query: str | AuthoredQuery | None
- SqlQuery.source: str | None (inline dicts are rejected at compile, not
  coerced — see test_sources.py::TestInlineFileSourcePaths and
  test_compile_raise_sites.py::TestInlineSourceForbiddenCarriesCode)
- _SharedChartFields.query: str | AuthoredQuery | QueryRef | None
- TableChartStyle.columns: dict[str, TableColumnConfig] | None
- ResolvedChart.columns: dict[str, TableColumnConfig] | None
"""

from __future__ import annotations

from dbt_charts.core.compile.models.query.authored import (
    AuthoredSqlQuery,
    _BaseQueryFields,
)
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    SqlQuery,
)
from dbt_charts.core.compile.models.refs import QueryRef
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.normalize.variables import promote_inline_option_queries


class TestVariableQueryTyping:
    """Variable.query should coerce inline dict to AuthoredQuery, not keep it as dict."""

    def test_inline_sql_dict_becomes_authored_query(self) -> None:
        var = Variable(
            query={"sql": "SELECT DISTINCT region FROM orders", "source": "my_pg"}
        )
        assert isinstance(var.query, _BaseQueryFields)
        assert var.query.sql == "SELECT DISTINCT region FROM orders"

    def test_inline_dict_without_type_infers_sql(self) -> None:
        var = Variable(query={"sql": "SELECT 1"})
        assert isinstance(var.query, _BaseQueryFields)
        assert var.query.type == "sql"

    def test_string_query_name_stays_as_str(self) -> None:
        var = Variable(query="my_options_query")
        assert var.query == "my_options_query"

    def test_none_stays_none(self) -> None:
        var = Variable(query=None)
        assert var.query is None

    def test_already_authored_query_instance_passes_through(self) -> None:
        aq = AuthoredSqlQuery(sql="SELECT 1")
        var = Variable(query=aq)
        assert var.query is aq

    def test_inline_authored_query_promoted_to_named_query(self) -> None:
        var = Variable(
            query={"sql": "SELECT DISTINCT region FROM sales", "source": "db"}
        )
        assert isinstance(var.query, _BaseQueryFields)

        query_registry: dict[str, AnyQuery] = {}
        promote_inline_option_queries({"region": var}, query_registry, sources={})

        assert var.query == "_var_query_region"
        assert "_var_query_region" in query_registry
        promoted = query_registry["_var_query_region"]
        assert isinstance(promoted, SqlQuery)
        assert promoted.sql == "SELECT DISTINCT region FROM sales"

    def test_string_query_not_touched_by_promotion(self) -> None:
        var = Variable(query="existing_query")
        query_registry: dict[str, AnyQuery] = {}
        promote_inline_option_queries({"v": var}, query_registry, sources={})
        assert var.query == "existing_query"
        assert not query_registry


class TestQuerySourceTyping:
    """SqlQuery.source is a name reference — no inline dict/SourceConfig member.

    A *compiled* board query always names its source (normalize_query injects the
    inherited default and raises ERR-SOURCE-REQUIRED / -NOT-FOUND), but the
    field stays optional because the ad-hoc runtime boundary (execute_query with
    no source) constructs a sourceless query that runs
    against the scratch DuckDB locally and is rejected by the guarded resolver on
    hosted surfaces. An inline dict is rejected at compile time (see
    test_sources.py::TestInlineFileSourcePaths::test_inline_dict_source_still_forbidden_alongside_file_paths).
    """

    def test_sql_query_string_source_stays_str(self) -> None:
        q = SqlQuery(sql="SELECT 1", source="my_named_source")
        assert q.source == "my_named_source"

    def test_sql_query_none_source_stays_none(self) -> None:
        """Ad-hoc sourceless queries construct with source=None; the resolver
        (not this field) decides whether that is allowed at execute time."""
        q = SqlQuery(sql="SELECT 1", source=None)
        assert q.source is None


class TestChartQueryTyping:
    """Charts' query field should coerce inline dicts to AuthoredQuery or QueryRef."""

    def _make_bar(self, query_val: object) -> object:
        from dbt_charts.core.compile.models.chart.authored import BarChart

        return BarChart(type="bar", x="month", y="revenue", query=query_val)  # type: ignore[arg-type]

    def test_inline_sql_dict_becomes_authored_query(self) -> None:
        chart = self._make_bar({"sql": "SELECT month, revenue FROM orders"})
        assert isinstance(chart.query, _BaseQueryFields)  # type: ignore[union-attr]

    def test_inline_dict_without_type_infers_sql(self) -> None:
        chart = self._make_bar({"sql": "SELECT 1"})
        assert isinstance(chart.query, _BaseQueryFields)  # type: ignore[union-attr]
        assert chart.query.type == "sql"  # type: ignore[union-attr]

    def test_cross_file_ref_dict_becomes_query_ref(self) -> None:
        chart = self._make_bar({"ref": "other.yml.queries.my_q"})
        assert isinstance(chart.query, QueryRef)  # type: ignore[union-attr]

    def test_string_stays_as_str(self) -> None:
        chart = self._make_bar("my_named_query")
        assert chart.query == "my_named_query"  # type: ignore[union-attr]

    def test_none_stays_none(self) -> None:
        chart = self._make_bar(None)
        assert chart.query is None  # type: ignore[union-attr]


class TestTableChartStyleColumnsTyping:
    """TableChartStyle.columns values should be TableColumnConfig instances, not raw dicts."""

    def test_columns_dict_values_coerced_to_table_column_config(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.compile.models.style.authored import TableChartStylePatch

        patch = TableChartStylePatch(
            columns={"revenue": {"format": "$.2f", "width": 120}}
        )
        assert patch.columns is not None
        val = patch.columns["revenue"]
        assert isinstance(val, TableColumnConfig)
        assert val.width == 120

    def test_columns_already_typed_values_pass_through(self) -> None:
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.compile.models.style.authored import TableChartStylePatch

        cfg = TableColumnConfig(label="Revenue", width=100)
        patch = TableChartStylePatch(columns={"revenue": cfg})
        assert patch.columns is not None
        assert patch.columns["revenue"] is cfg
