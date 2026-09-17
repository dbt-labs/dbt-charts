"""Tests for dbt_charts.core.execute.sql_literals — SQL literal inlining.

These tests are a regression guard against SQL injection through inlined values.
Schema names containing single quotes must be properly escaped before
being inlined into SQL strings passed to dbt adapters.
"""

import pytest

from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.execute.sql_literals import (
    INLINE_PLACEHOLDERS,
    inline_dialect_params,
    inline_params,
    inline_params_for_dialect,
    inline_percent_params,
    inline_qmark_params,
    sql_string_literal,
)

# Cases outside TestLiteralEscaping are not about the per-engine classification;
# postgres stands in for "escapes nothing but the quote".
PG = get_dialect("postgres")


class TestInlineParams:
    """$N-style positional param inlining."""

    def test_string_escapes_single_quotes(self) -> None:
        # Schema named O'Brien must not produce malformed SQL
        result = inline_params("WHERE schema = $1", ["O'Brien"], PG)
        assert result == "WHERE schema = 'O''Brien'"

    def test_injection_attempt_neutralized(self) -> None:
        # Attacker tries to close the string and inject; must be harmless
        malicious = "x'; DROP TABLE users; --"
        result = inline_params("WHERE tbl = $1", [malicious], PG)
        assert "DROP TABLE" in result  # literal text preserved
        assert result == "WHERE tbl = 'x''; DROP TABLE users; --'"

    def test_none_becomes_null(self) -> None:
        assert inline_params("WHERE x = $1", [None], PG) == "WHERE x = NULL"

    def test_int_not_quoted(self) -> None:
        assert inline_params("LIMIT $1", [100], PG) == "LIMIT 100"

    def test_bool_becomes_true_false(self) -> None:
        assert inline_params("WHERE flag = $1", [True], PG) == "WHERE flag = TRUE"
        assert inline_params("WHERE flag = $1", [False], PG) == "WHERE flag = FALSE"

    def test_reverse_order_prevents_partial_match(self) -> None:
        # $1 must not accidentally replace inside $10
        result = inline_params(
            "$1 $10 $11", ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"], PG
        )
        assert result == "'a' 'j' 'k'"

    def test_multiple_params(self) -> None:
        result = inline_params("$1 AND $2", ["foo", "bar"], PG)
        assert result == "'foo' AND 'bar'"

    def test_no_resubstitution_placeholder_in_value(self) -> None:
        # Regression: if a user value contains $1, it must not be re-substituted.
        # Old str.replace loop would substitute '$1' in position-2 value after
        # position-1 was already placed — yielding a boolean-bypass attack.
        result = inline_params("WHERE id = $1 AND email = $2", [" OR 1=1 --", "$1"], PG)
        assert result == "WHERE id = ' OR 1=1 --' AND email = '$1'"

    def test_no_resubstitution_placeholder_embedded(self) -> None:
        # $1 embedded inside a user value must be preserved as literal.
        result = inline_params("col = $1", ["X$1Y"], PG)
        assert result == "col = 'X$1Y'"


class TestInlinePercentParams:
    """%s-style positional param inlining used by InspectConnection."""

    def test_string_escapes_single_quotes(self) -> None:
        result = inline_percent_params("WHERE schema = %s", ["O'Brien"], PG)
        assert result == "WHERE schema = 'O''Brien'"

    def test_injection_neutralized(self) -> None:
        malicious = "x'; DROP TABLE users; --"
        result = inline_percent_params("WHERE name = %s", [malicious], PG)
        assert result.startswith("WHERE name = '")
        assert "''" in result  # escaped quote

    def test_multiple_params_in_order(self) -> None:
        result = inline_percent_params("WHERE a = %s AND b = %s", ["foo", "bar"], PG)
        assert result == "WHERE a = 'foo' AND b = 'bar'"

    def test_int_not_quoted(self) -> None:
        assert inline_percent_params("LIMIT %s", [100], PG) == "LIMIT 100"

    def test_no_resubstitution_percent_s_in_value(self) -> None:
        # Regression: if a user value contains %s, it must not be re-substituted
        # on the next iteration. Old str.replace loop would do exactly that.
        result = inline_percent_params("a = %s AND b = %s", ["%s", " OR 1=1 --"], PG)
        assert result == "a = '%s' AND b = ' OR 1=1 --'"


class TestSpecialFloats:
    """NaN and Inf must raise — CAST syntax is not portable across warehouses.

    BigQuery requires FLOAT64, Redshift requires DOUBLE PRECISION, SQL Server requires FLOAT.
    Emitting CAST(... AS DOUBLE) would syntax-error on BigQuery, an explicit target warehouse.
    """

    import pytest

    def test_nan_raises_value_error(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="NaN cannot be inlined"):
            inline_params("WHERE x = $1", [float("nan")], PG)

    def test_positive_inf_raises_value_error(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Infinity cannot be inlined"):
            inline_params("WHERE x = $1", [float("inf")], PG)

    def test_negative_inf_raises_value_error(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Infinity cannot be inlined"):
            inline_params("WHERE x = $1", [float("-inf")], PG)

    def test_normal_float_still_works(self) -> None:
        result = inline_params("WHERE x = $1", [3.14], PG)
        assert result == "WHERE x = 3.14"


class TestInlineParamsErrors:
    """Regression: out-of-range $N must raise, not silently pass through."""

    def test_out_of_range_raises_index_error(self) -> None:
        import pytest

        with pytest.raises(IndexError, match=r"\$5 out of range"):
            inline_params("SELECT $5", ["x"], PG)

    def test_empty_params_raises(self) -> None:
        import pytest

        with pytest.raises(IndexError, match=r"\$1 out of range"):
            inline_params("SELECT $1", [], PG)

    def test_in_range_does_not_raise(self) -> None:
        result = inline_params("SELECT $1, $2", ["a", "b"], PG)
        assert result == "SELECT 'a', 'b'"


class TestInlinePercentParamsErrors:
    """Param-count mismatch must raise, not silently pass through or drop params."""

    def test_too_few_params_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="placeholder"):
            inline_percent_params("a=%s", [], PG)

    def test_too_many_params_raises(self) -> None:
        """Extra params are a callsite drift bug — raise, don't silently drop."""
        import pytest

        with pytest.raises(ValueError, match="placeholder"):
            inline_percent_params("a=%s", ["x", "y", "z"], PG)

    def test_exactly_right_count_works(self) -> None:
        result = inline_percent_params("a=%s AND b=%s", ["x", "y"], PG)
        assert result == "a='x' AND b='y'"


@pytest.fixture
def bq_param():
    """BigQuery dialect param function — @paramN."""
    return get_dialect("bigquery").param


class TestInlineDialectParams:
    """inline_dialect_params covers named-param dialects in the dbt-adapter path.

    Regression test for: named-param placeholders (BigQuery @paramN) passing
    through unresolved because the dbt-adapter path only called inline_params,
    which handles $N.

    Uses BigQuery's param() as the representative named-param dialect.
    """

    def test_basic_replacement(self, bq_param) -> None:
        result = inline_dialect_params(
            "WHERE region = @param1", ["North"], bq_param, PG
        )
        assert result == "WHERE region = 'North'"

    def test_multiple_params_in_order(self, bq_param) -> None:
        result = inline_dialect_params(
            "WHERE region = @param1 AND month = @param2",
            ["North", "2024-01"],
            bq_param,
            PG,
        )
        assert result == "WHERE region = 'North' AND month = '2024-01'"

    def test_string_escapes_single_quotes(self, bq_param) -> None:
        result = inline_dialect_params(
            "WHERE name = @param1", ["O'Brien"], bq_param, PG
        )
        assert result == "WHERE name = 'O''Brien'"

    def test_none_becomes_null(self, bq_param) -> None:
        assert (
            inline_dialect_params("WHERE x = @param1", [None], bq_param, PG)
            == "WHERE x = NULL"
        )

    def test_int_not_quoted(self, bq_param) -> None:
        assert (
            inline_dialect_params("LIMIT @param1", [100], bq_param, PG) == "LIMIT 100"
        )

    def test_no_params_returns_sql_unchanged(self, bq_param) -> None:
        sql = "SELECT * FROM orders"
        assert inline_dialect_params(sql, [], bq_param, PG) == sql

    def test_reverse_order_prevents_partial_match(self, bq_param) -> None:
        # @param1 must not partially match inside @param10, @param11, etc.
        params = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"]
        result = inline_dialect_params(
            "@param1 @param10 @param11", params, bq_param, PG
        )
        assert result == "'a' 'j' 'k'"

    def test_no_resubstitution_placeholder_in_value(self, bq_param) -> None:
        # A param value that contains @param1 must not be re-substituted.
        result = inline_dialect_params(
            "WHERE id = @param1 AND code = @param2",
            [" OR 1=1 --", "@param1"],
            bq_param,
            PG,
        )
        assert result == "WHERE id = ' OR 1=1 --' AND code = '@param1'"

    def test_databricks_colon_params(self) -> None:
        # Databricks uses :param1 style — same function, different param_fn.
        db_param = get_dialect("databricks").param
        result = inline_dialect_params(
            "WHERE region = :param1 AND month = :param2",
            ["North", "2024-01"],
            db_param,
            PG,
        )
        assert result == "WHERE region = 'North' AND month = '2024-01'"


class TestInlineQmarkParams:
    """?-style positional param inlining used by Snowflake in the dbt-adapter path."""

    def test_string_escapes_single_quotes(self) -> None:
        result = inline_qmark_params("WHERE schema = ?", ["O'Brien"], PG)
        assert result == "WHERE schema = 'O''Brien'"

    def test_injection_neutralized(self) -> None:
        malicious = "x'; DROP TABLE users; --"
        result = inline_qmark_params("WHERE name = ?", [malicious], PG)
        assert "''" in result

    def test_multiple_params_in_order(self) -> None:
        result = inline_qmark_params("WHERE a = ? AND b = ?", ["foo", "bar"], PG)
        assert result == "WHERE a = 'foo' AND b = 'bar'"

    def test_int_not_quoted(self) -> None:
        assert inline_qmark_params("LIMIT ?", [100], PG) == "LIMIT 100"

    def test_none_becomes_null(self) -> None:
        assert inline_qmark_params("WHERE x = ?", [None], PG) == "WHERE x = NULL"

    def test_no_resubstitution_qmark_in_value(self) -> None:
        # A param value containing ? must not be re-substituted on the next iteration.
        result = inline_qmark_params("a = ? AND b = ?", ["?", " OR 1=1 --"], PG)
        assert result == "a = '?' AND b = ' OR 1=1 --'"

    def test_too_few_params_raises(self) -> None:
        with pytest.raises(ValueError, match="placeholder"):
            inline_qmark_params("a=? AND b=?", ["only_one"], PG)

    def test_too_many_params_raises(self) -> None:
        with pytest.raises(ValueError, match="placeholder"):
            inline_qmark_params("a=?", ["x", "y", "z"], PG)


# Every registry key, classified from each engine's documented string-literal
# grammar — each dialect class cites the documentation it was read from.
EXPECTED_CLASSIFICATION = {
    "postgres": False,
    "postgresql": False,
    "duckdb": False,
    "sqlite": False,
    "mysql": True,
    "mariadb": True,
    "snowflake": True,
    "bigquery": True,
    "redshift": True,
    "sqlserver": False,
    "mssql": False,
    "databricks": True,
    "spark": True,
    "athena": False,
    "presto": False,
    "trino": False,
    "clickhouse": True,
}
BACKSLASH_ESCAPING = sorted(k for k, v in EXPECTED_CLASSIFICATION.items() if v)
ANSI_LITERALS = sorted(k for k, v in EXPECTED_CLASSIFICATION.items() if not v)

# `\' UNION ...` — on an engine where backslash escapes, doubling only the
# quote leaves `'\''`, whose literal closes at the escaped quote and hands the
# UNION to the parser as a top-level set operation.
INJECTION = "\\' UNION ALL SELECT api_key, 2 FROM secrets --"


class TestLiteralEscaping:
    """A value must arrive as itself, and must not escape its own literal.

    Both halves are per-engine. Wherever `\\` is the escape character, doubling
    only `\'` lets the value close its literal and put the rest in code position,
    and `\'\'` is not an escaped quote at all — GoogleSQL and Spark SQL
    concatenate adjacent literals, so `\'O\'\'Brien\'` compares against `OBrien`.
    Wherever it is not, doubling a backslash corrupts the value instead.
    """

    @pytest.mark.parametrize("profile_type", BACKSLASH_ESCAPING)
    def test_the_payload_is_escaped_with_backslashes(self, profile_type: str) -> None:
        out = inline_params("SELECT $1", [INJECTION], get_dialect(profile_type))

        assert out == r"SELECT '\\\' UNION ALL SELECT api_key, 2 FROM secrets --'"

    @pytest.mark.parametrize("profile_type", ANSI_LITERALS)
    def test_the_payload_is_escaped_by_doubling_the_quote(
        self, profile_type: str
    ) -> None:
        """Doubling the backslash here would corrupt the value instead."""
        out = inline_params("SELECT $1", [INJECTION], get_dialect(profile_type))

        assert out == r"SELECT '\'' UNION ALL SELECT api_key, 2 FROM secrets --'"

    @pytest.mark.parametrize("profile_type", ANSI_LITERALS)
    def test_a_windows_path_arrives_byte_identical(self, profile_type: str) -> None:
        """The value is data, and on these engines a backslash is just a byte.

        Trino and Athena escape only the quote, by doubling it; doubling their
        backslashes turns `C:\\Users` into a ten-character string that matches
        no row, with no error anywhere to say so.
        """
        out = inline_params("SELECT $1", ["C:\\Users"], get_dialect(profile_type))

        assert out == r"SELECT 'C:\Users'"

    @pytest.mark.parametrize("profile_type", BACKSLASH_ESCAPING)
    def test_a_windows_path_is_doubled_back_to_itself(self, profile_type: str) -> None:
        """Here the doubled pair is what the engine reads back as one backslash."""
        out = inline_params("SELECT $1", ["C:\\Users"], get_dialect(profile_type))

        assert out == r"SELECT 'C:\\Users'"

    @pytest.mark.parametrize("profile_type", BACKSLASH_ESCAPING)
    def test_a_quote_is_escaped_with_a_backslash(self, profile_type: str) -> None:
        """`''` on these engines ends one literal and starts the next."""
        out = inline_params("SELECT $1", ["O'Brien"], get_dialect(profile_type))

        assert out == r"SELECT 'O\'Brien'"

    @pytest.mark.parametrize("profile_type", ANSI_LITERALS)
    def test_a_quote_is_escaped_by_doubling(self, profile_type: str) -> None:
        out = inline_params("SELECT $1", ["O'Brien"], get_dialect(profile_type))

        assert out == "SELECT 'O''Brien'"

    # sqlglot's athena reader treats `\` as an escape (it accepts Athena's
    # Hive-flavored DDL), so it cannot tokenize the correct Trino spelling of a
    # value ending `\'`. That is a reader limitation, not an engine one — and it
    # fails closed: our own guard rejects the statement rather than running it.
    ROUND_TRIP_EXCEPTIONS = {
        ("athena", "a\\'b"),
        ("presto", "a\\'b"),
        ("trino", "a\\'b"),
    }

    @pytest.mark.parametrize("profile_type", sorted(EXPECTED_CLASSIFICATION))
    @pytest.mark.parametrize(
        "value", [INJECTION, "C:\\Users", "O'Brien", "a\\'b", "Wendy's ' OR 1=1 --"]
    )
    def test_a_value_reparses_to_itself_as_one_inert_literal(
        self, profile_type: str, value: str
    ) -> None:
        """The decisive check, in both directions.

        Reading the emitted SQL back with the engine's own grammar must yield one
        statement, no set operation the value smuggled in, and the value byte for
        byte — which fails for a wrong escape either way round.
        """
        if (profile_type, value) in self.ROUND_TRIP_EXCEPTIONS:
            pytest.skip("sqlglot's athena reader escapes backslashes; Trino does not")

        import sqlglot

        from dbt_charts.core.compile.sql_guard import sqlglot_dialect

        dialect = get_dialect(profile_type)
        sql = inline_params("SELECT * FROM t WHERE c = $1", [value], dialect)

        parsed = sqlglot.parse(sql, read=sqlglot_dialect(dialect.name))

        assert len(parsed) == 1
        assert not list(parsed[0].find_all(sqlglot.expressions.Union))
        literal = parsed[0].find(sqlglot.expressions.Literal)
        assert literal is not None
        assert literal.this == value

    def test_every_registry_dialect_matches_its_documented_grammar(self) -> None:
        """The classification itself, pinned per registry key.

        Redshift is the one that cannot be inherited: it subclasses the Postgres
        dialect and disagrees with it here, being a Postgres 8.0.2 fork from
        before standard-conforming strings.
        """
        from dbt_charts.core.dialects import DIALECTS

        classified = {
            name: dialect.escapes_backslashes for name, dialect in DIALECTS.items()
        }

        assert classified == EXPECTED_CLASSIFICATION

    def test_an_undeclared_dialect_raises_rather_than_skipping_escaping(self) -> None:
        """A new dialect that forgets the answer must not get a quiet one."""
        from dbt_charts.core.dialects import SQLDialect

        class UndeclaredDialect(SQLDialect):
            name = "undeclared"

            def param(self, index: int) -> str:
                return f"${index}"

        with pytest.raises(AttributeError, match="escapes_backslashes"):
            inline_params("c = $1", ["\\' OR 1=1 -- "], UndeclaredDialect())


class TestNulGuard:
    """No NUL byte may survive inlining. NUL cannot appear in authored SQL, so
    a survivor is an internal placeholder that was transformed before it could
    be substituted — a Jinja string filter the render-time guard missed."""

    def test_surviving_mangled_token_raises(self) -> None:
        # A case-mangled token (e.g. a filter that bypassed _TokenText and
        # uppercased the lowercase token) does not match the substitution pattern
        # but is caught by the case-insensitive NUL guard.
        mangled = "SELECT * FROM t WHERE r = '\x00DCT_PARAM_1\x00'"
        with pytest.raises(ValueError, match="placeholder"):
            inline_params_for_dialect(mangled, ["north"], INLINE_PLACEHOLDERS, PG)

    def test_nul_inside_a_value_is_not_misdiagnosed(self) -> None:
        # A NUL that arrives inside a *value* is not a mangled placeholder —
        # it passes through to the driver's own handling rather than being
        # blamed on a Jinja filter.
        sql = f"SELECT * FROM t WHERE r = {INLINE_PLACEHOLDERS.param(1)}"
        result = inline_params_for_dialect(
            sql, ["nul\x00value"], INLINE_PLACEHOLDERS, PG
        )
        assert "nul\x00value" in result

    def test_clean_round_trip_passes(self) -> None:
        sql = f"SELECT * FROM t WHERE r = {INLINE_PLACEHOLDERS.param(1)}"
        result = inline_params_for_dialect(sql, ["north"], INLINE_PLACEHOLDERS, PG)
        assert result == "SELECT * FROM t WHERE r = 'north'"


class TestSqlStringLiteral:
    """`sql_string_literal` inlines a single already-known string (a schema or
    table name from the inspector) directly into SQL, escaped for the engine
    that parses the literal — the direct-inlining sibling of the placeholder
    inliners above."""

    def test_backslash_dialect_escapes_quote_with_backslash(self) -> None:
        assert sql_string_literal("O'Brien", get_dialect("bigquery")) == r"'O\'Brien'"

    def test_ansi_dialect_doubles_the_quote(self) -> None:
        assert sql_string_literal("O'Brien", get_dialect("postgres")) == "'O''Brien'"

    def test_backslash_dialect_doubles_backslash(self) -> None:
        out = sql_string_literal("C:\\Users", get_dialect("snowflake"))
        assert out == r"'C:\\Users'"

    def test_injection_stays_inside_its_literal_on_backslash_dialect(self) -> None:
        out = sql_string_literal("\\' UNION ALL SELECT 1 --", get_dialect("bigquery"))
        assert out == r"'\\\' UNION ALL SELECT 1 --'"
