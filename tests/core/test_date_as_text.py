"""WARN-DATE-AS-TEXT: a charted x column that is a date turned into text."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.compiler import validate_compiled_queries
from dbt_charts.core.inspect.query_validator import QueryDiagnostic, validate_query

CODE = "WARN-DATE-AS-TEXT"


def _date_as_text(
    sql: str, x: str = "week", dialect: str | None = "duckdb"
) -> list[QueryDiagnostic]:
    diags = validate_query(sql, dialect=dialect, x_columns=frozenset({x}))
    return [d for d in diags if d.code == CODE]


class TestWarns:
    def test_strftime_over_date_trunc_names_the_replacement(self) -> None:
        found = _date_as_text(
            "SELECT strftime(date_trunc('week', sent_at), '%b %-d') AS week, "
            "count(*) AS sent FROM documents GROUP BY 1"
        )
        assert len(found) == 1
        assert found[0].severity == "warning"
        assert found[0].message == (
            "week is a date turned into text ('%b %-d'). Return the date itself, "
            "date_trunc('week', sent_at) AS week, and the axis labels it by week."
        )

    def test_strftime_over_bare_date_column(self) -> None:
        found = _date_as_text("SELECT strftime(sent_at, '%Y-%m-%d') AS week FROM t")
        assert len(found) == 1
        assert "sent_at AS week" in found[0].message
        assert "date_trunc" not in found[0].message

    def test_to_char_postgres(self) -> None:
        found = _date_as_text(
            "SELECT to_char(date_trunc('month', created_at), 'YYYY-MM-DD') AS week "
            "FROM t",
            dialect="postgres",
        )
        assert len(found) == 1
        assert "date_trunc('month', created_at) AS week" in found[0].message

    def test_format_date_bigquery(self) -> None:
        found = _date_as_text(
            "SELECT FORMAT_DATE('%Y-%m-%d', DATE_TRUNC(order_date, WEEK)) AS week "
            "FROM t",
            dialect="bigquery",
        )
        assert len(found) == 1

    @pytest.mark.parametrize(
        ("sql", "dialect"),
        [
            ("SELECT strftime('%Y-%m-%d', created_at) AS week FROM t", "sqlite"),
            (
                "SELECT to_char(created_at, 'YYYY-MM-DD') AS week FROM t",
                "snowflake",
            ),
            ("SELECT to_varchar(created_at, 'YYYY') AS week FROM t", "snowflake"),
            ("SELECT to_char(created_at, 'YYYY-MM-DD') AS week FROM t", "oracle"),
            ("SELECT to_char(created_at, 'YYYY-MM-DD') AS week FROM t", "redshift"),
            (
                "SELECT FORMAT_TIMESTAMP('%F', created_at) AS week FROM t",
                "bigquery",
            ),
            (
                "SELECT FORMAT_DATETIME('%F', created_at) AS week FROM t",
                "bigquery",
            ),
            (
                "SELECT date_format(created_at, 'yyyy-MM-dd') AS week FROM t",
                "spark",
            ),
            (
                "SELECT date_format(created_at, 'yyyy-MM-dd') AS week FROM t",
                "databricks",
            ),
            (
                "SELECT date_format(created_at, '%Y-%m-%d') AS week FROM t",
                "mysql",
            ),
            ("SELECT FORMAT(created_at, 'yyyy-MM-dd') AS week FROM t", "tsql"),
        ],
    )
    def test_formatting_function_per_dialect(self, sql: str, dialect: str) -> None:
        assert len(_date_as_text(sql, dialect=dialect)) == 1

    @pytest.mark.parametrize(
        ("sql", "dialect"),
        [
            ("SELECT CAST(created_at AS VARCHAR) AS week FROM t", "duckdb"),
            ("SELECT created_at::text AS week FROM t", "postgres"),
            ("SELECT CAST(order_date AS STRING) AS week FROM t", "bigquery"),
            ("SELECT CAST(created_at AS VARCHAR(10)) AS week FROM t", "tsql"),
            ("SELECT CAST(date_trunc('week', x) AS VARCHAR) AS week FROM t", "duckdb"),
            ("SELECT CAST(DATE '2026-01-01' AS VARCHAR) AS week FROM t", "duckdb"),
            ("SELECT CAST(CURRENT_DATE AS VARCHAR) AS week FROM t", "duckdb"),
        ],
    )
    def test_cast_to_text_of_evident_date(self, sql: str, dialect: str) -> None:
        assert len(_date_as_text(sql, dialect=dialect)) == 1

    def test_cast_over_date_trunc_names_the_replacement(self) -> None:
        found = _date_as_text("SELECT CAST(date_trunc('week', x) AS VARCHAR) AS week")
        assert "date_trunc('week', x) AS week" in found[0].message

    def test_x_match_is_case_insensitive(self) -> None:
        assert len(_date_as_text("SELECT strftime(d, '%Y') AS Week FROM t")) == 1

    def test_suppressed_by_inline_ignore(self) -> None:
        sql = "SELECT strftime(d, '%Y') AS week FROM t -- dct:ignore WARN-DATE-AS-TEXT"
        assert _date_as_text(sql) == []


class TestStaysQuiet:
    @pytest.mark.parametrize(
        ("sql", "dialect"),
        [
            ("SELECT strftime(d, '%b') AS week FROM t", "duckdb"),
            ("SELECT strftime(d, '%B') AS week FROM t", "duckdb"),
            ("SELECT strftime(d, '%a') AS week FROM t", "duckdb"),
            ("SELECT strftime(d, '%A') AS week FROM t", "duckdb"),
            ("SELECT to_char(created_at, 'Mon') AS week FROM t", "postgres"),
            ("SELECT to_char(created_at, 'Month') AS week FROM t", "postgres"),
            ("SELECT to_char(created_at, 'Dy') AS week FROM t", "postgres"),
            ("SELECT to_char(created_at, 'Day') AS week FROM t", "snowflake"),
            ("SELECT to_char(created_at, 'MON') AS week FROM t", "oracle"),
            ("SELECT FORMAT_DATE('%b', d) AS week FROM t", "bigquery"),
            ("SELECT FORMAT_DATE('%A', d) AS week FROM t", "bigquery"),
            ("SELECT date_format(created_at, 'MMM') AS week FROM t", "spark"),
            ("SELECT date_format(created_at, 'EEEE') AS week FROM t", "databricks"),
            ("SELECT FORMAT(created_at, 'MMMM') AS week FROM t", "tsql"),
            ("SELECT FORMAT(created_at, 'MMM') AS week FROM t", "tsql"),
            ("SELECT FORMAT(created_at, 'dddd') AS week FROM t", "tsql"),
            ("SELECT DATENAME(month, created_at) AS week FROM t", "tsql"),
            ("SELECT DATENAME(weekday, created_at) AS week FROM t", "tsql"),
            ("SELECT monthname(created_at) AS week FROM t", "mysql"),
            ("SELECT to_char(created_at, 'Day') AS week FROM t", "postgres"),
            ("SELECT to_char(created_at, 'FMDay') AS week FROM t", "postgres"),
            ("SELECT to_char(created_at, 'FMMonth') AS week FROM t", "postgres"),
            ("SELECT to_char(created_at, 'MMMM') AS week FROM t", "snowflake"),
            ("SELECT date_format(created_at, 'EEE') AS week FROM t", "spark"),
            ("SELECT FORMAT(created_at, 'ddd') AS week FROM t", "tsql"),
            ("SELECT date_format(created_at, 'LLL') AS week FROM t", "spark"),
            ("SELECT FORMAT_DATE('%h', d) AS week FROM t", "bigquery"),
        ],
    )
    def test_month_or_weekday_name_is_a_deliberate_category(
        self, sql: str, dialect: str
    ) -> None:
        assert _date_as_text(sql, dialect=dialect) == []

    def test_formatted_column_no_chart_uses_as_x(self) -> None:
        sql = "SELECT strftime(d, '%Y-%m-%d') AS label, count(*) AS n FROM t GROUP BY 1"
        assert _date_as_text(sql, x="n") == []

    def test_no_chart_x_given(self) -> None:
        sql = "SELECT strftime(d, '%Y-%m-%d') AS week FROM t"
        assert validate_query(sql, dialect="duckdb") == []

    def test_no_formatting(self) -> None:
        assert _date_as_text("SELECT date_trunc('week', d) AS week FROM t") == []

    def test_cast_of_unknown_column_is_not_guessed(self) -> None:
        assert _date_as_text("SELECT CAST(code AS VARCHAR) AS week FROM t") == []

    def test_cast_of_number_is_not_a_date(self) -> None:
        assert _date_as_text("SELECT CAST(1 + 2 AS VARCHAR) AS week") == []

    def test_to_char_of_a_number(self) -> None:
        sql = "SELECT to_char(revenue, 'FM999,999.00') AS week FROM t"
        assert _date_as_text(sql, dialect="postgres") == []
        assert _date_as_text(sql, dialect="snowflake") == []


class TestCompileWiring:
    def test_warns_only_for_the_charted_x(self) -> None:
        result = compile(
            """
source: examples_db
queries:
  sent:
    dialect: duckdb
    sql: |
      SELECT strftime(date_trunc('week', sent_at), '%b %-d') AS week,
             strftime(date_trunc('week', sent_at), '%b %-d') AS label,
             count(*) AS sent
      FROM documents GROUP BY 1, 2
charts:
  weekly:
    query: sent
    type: bar
    x: week
    y: sent
rows:
  - weekly
"""
        )
        assert result.success
        validate_compiled_queries(result)
        hits = [w for w in result.warnings if w.code == CODE]
        assert len(hits) == 1
        assert hits[0].message.startswith("Query 'sent': week is a date turned")


class TestEdgeCases:
    @pytest.mark.parametrize("dialect", ["snowflake", "oracle"])
    def test_one_argument_to_char_does_not_crash(self, dialect: str) -> None:
        found = _date_as_text(
            "SELECT to_char(created_at) AS week FROM t", dialect=dialect
        )
        assert len(found) == 1
        assert "(" not in found[0].message.split(".")[0]
        assert (
            _date_as_text("SELECT to_char(amount) AS week FROM t", dialect=dialect)
            == []
        )

    @pytest.mark.parametrize(
        ("sql", "dialect"),
        [
            ("SELECT to_char(amount, '9999D99') AS week FROM t", "postgres"),
            ("SELECT to_char(amount, 'MI9999') AS week FROM t", "postgres"),
            (
                "SELECT to_char(amount, 'FM9,999.99 \"days\"') AS week FROM t",
                "postgres",
            ),
            ("SELECT to_char(amount, 'YYYY') AS week FROM t", "oracle"),
            ("SELECT to_char(amount, 'YYYY') AS week FROM t", "snowflake"),
            ("SELECT FORMAT(amount, 'dd') AS week FROM t", "tsql"),
        ],
    )
    def test_number_formats_are_not_dates(self, sql: str, dialect: str) -> None:
        assert _date_as_text(sql, dialect=dialect) == []

    def test_to_char_needs_a_date_value_not_just_a_date_format(self) -> None:
        assert (
            _date_as_text(
                "SELECT to_char(x, 'YYYY-MM-DD') AS week FROM t", dialect="postgres"
            )
            == []
        )

    @pytest.mark.parametrize(
        ("sql", "dialect"),
        [
            ("SELECT strftime(d, '%b %-d') AS week FROM t", "duckdb"),
            ("SELECT strftime(d, '%Y-%m') AS week FROM t", "duckdb"),
            ("SELECT strftime(d, '%Y') AS week FROM t", "duckdb"),
            ("SELECT to_char(created_at, 'Mon DD') AS week FROM t", "postgres"),
            ("SELECT strftime(d, '%m') AS week FROM t", "duckdb"),
            ("SELECT strftime(d, '%d') AS week FROM t", "duckdb"),
            ("SELECT strftime(d, '%H') AS week FROM t", "duckdb"),
            ("SELECT strftime(d, '%s') AS week FROM t", "duckdb"),
            ("SELECT strftime(d, '%j') AS week FROM t", "duckdb"),
            ("SELECT FORMAT_DATE('%m', d) AS week FROM t", "bigquery"),
            ("SELECT to_char(created_at, 'Q') AS week FROM t", "postgres"),
            ("SELECT to_char(created_at, 'MM') AS week FROM t", "postgres"),
            ("SELECT to_char(created_at, 'DDD') AS week FROM t", "postgres"),
            ("SELECT to_char(created_at, 'DDD') AS week FROM t", "snowflake"),
            ("SELECT FORMAT(created_at, 'MM') AS week FROM t", "tsql"),
            ("SELECT date_format(created_at, 'M') AS week FROM t", "spark"),
            ("SELECT date_format(created_at, 'D') AS week FROM t", "databricks"),
        ],
    )
    def test_anything_but_a_name_warns(self, sql: str, dialect: str) -> None:
        assert len(_date_as_text(sql, dialect=dialect)) == 1

    def test_non_literal_format_drops_the_format(self) -> None:
        found = _date_as_text(
            "SELECT to_char(created_at, fmtcol) AS week FROM t", dialect="postgres"
        )
        assert len(found) == 1
        assert "fmtcol" not in found[0].message
        found = _date_as_text("SELECT strftime(created_at, $1) AS week FROM t")
        assert len(found) == 1
        assert "'1'" not in found[0].message

    def test_non_literal_unit_drops_the_grain(self) -> None:
        found = _date_as_text("SELECT CAST(date_trunc(g, d) AS VARCHAR) AS week FROM t")
        assert len(found) == 1
        assert "'g'" not in found[0].message
        assert "date_trunc(" not in found[0].message


class TestLayerQueries:
    def test_layer_query_x_is_collected(self) -> None:
        result = compile(
            """
source: examples_db
queries:
  main:
    dialect: duckdb
    sql: SELECT 1 AS week, 2 AS n
  extra:
    dialect: duckdb
    sql: |
      SELECT strftime(date_trunc('week', sent_at), '%b %-d') AS wk, count(*) AS n
      FROM documents GROUP BY 1
charts:
  c:
    query: main
    type: line
    x: week
    y: n
    layers:
      - type: line
        query: extra
        x: wk
        y: n
rows:
  - c
"""
        )
        assert result.success
        validate_compiled_queries(result)
        hits = [w for w in result.warnings if w.code == CODE]
        assert len(hits) == 1
        assert hits[0].message.startswith("Query 'extra': wk is a date turned")


def test_dialect_aliases_resolve() -> None:
    sql = "SELECT to_char(amount, 'YYYY') AS week FROM t"
    assert _date_as_text(sql, dialect="postgres") == []
    sql = "SELECT FORMAT(amount, 'dd') AS week FROM t"
    assert _date_as_text(sql, dialect="sqlserver") == []


class TestDateEvidence:
    @pytest.mark.parametrize(
        "col", ["OrderDate", "createdAt", "ShippedOn", "CREATED_AT"]
    )
    def test_camel_and_pascal_date_names(self, col: str) -> None:
        sql = f"SELECT CAST({col} AS VARCHAR) AS week FROM t"
        assert len(_date_as_text(sql)) == 1

    def test_non_date_camel_name_is_not_guessed(self) -> None:
        assert _date_as_text("SELECT CAST(UpdatedBy AS VARCHAR) AS week FROM t") == []

    def test_other_trunc_units_name_the_grain(self) -> None:
        sql = "SELECT CAST(date_trunc('decade', d) AS VARCHAR) AS week FROM t"
        found = _date_as_text(sql)
        assert "date_trunc('decade', d) AS week" in found[0].message


_LAYER_BOARD = """
source: examples_db
queries:
  main:
    dialect: duckdb
    sql: SELECT strftime(sent_at, '%Y-%m-%d') AS wk, 2 AS n FROM documents
  extra:
    dialect: duckdb
    sql: SELECT strftime(sent_at, '%Y-%m-%d') AS wk, 2 AS n FROM documents
charts:
  c:
    query: main
    type: line
{chart_x}    y: n
    layers:
      - type: line
{layer_query}        x: wk
        y: n
rows:
  - c
"""


class TestLayerX:
    def _hit_queries(self, chart_x: str, layer_query: str) -> set[str]:
        result = compile(_LAYER_BOARD.format(chart_x=chart_x, layer_query=layer_query))
        assert result.success
        validate_compiled_queries(result)
        return {w.message.split("'")[1] for w in result.warnings if w.code == CODE}

    def test_layer_without_query_uses_the_chart_query(self) -> None:
        assert self._hit_queries("", "") == {"main"}

    def test_layer_own_x_counts_when_chart_has_no_x(self) -> None:
        assert self._hit_queries("", "        query: extra\n") == {"extra"}

    def test_chart_x_still_counts_with_layer_query(self) -> None:
        assert self._hit_queries("    x: wk\n", "        query: extra\n") == {
            "main",
            "extra",
        }
