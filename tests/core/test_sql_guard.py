"""Unit tests for dbt_charts.core.compile.sql_guard.

TDD-first: this file is written before the implementation. Each test group
corresponds to one red→green cycle from the approved implementation plan.
"""

from __future__ import annotations

import pytest
import sqlglot

from dbt_charts.core.compile.sql_guard import (
    validate_select_only,
    validate_setup_sql,
)
from dbt_charts.core.diagnostics.execution import MutatingSqlError, UnparseableSqlError

# All dialects the validator must handle; None means sqlglot default.
DIALECTS: list[str | None] = [
    "duckdb",
    "bigquery",
    "postgres",
    "snowflake",
    "redshift",
    "databricks",
    "mysql",
    "sqlserver",
    "fabric",
    None,
]


# ---------------------------------------------------------------------------
# Group 1: Skeleton-builder unit tests
# ---------------------------------------------------------------------------


class TestBuildSkeleton:
    """White-box walk of the build_skeleton helper via black-box
    validate_select_only — we only observe the functional outcome (raises or
    not), not the exact skeleton string (that would pin implementation).
    """

    def test_template_data_passthrough(self) -> None:
        # Pure SQL with no Jinja — must be treated as literal SQL.
        validate_select_only("SELECT 1")  # should not raise

    def test_output_expression_becomes_placeholder(self) -> None:
        # {{ ref('orders') }} is an Output node; becomes __dct_j0__ identifier.
        # That placeholder is a valid SQL identifier so SELECT stays valid.
        validate_select_only(
            "SELECT * FROM {{ ref('orders') }} WHERE id = {{ user_id }}"
        )

    def test_if_body_and_else_both_walked(self) -> None:
        # Both branches of {% if %} are included in the skeleton so the Drop
        # in the else branch is discovered even when the variable is unbound.
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "{% if some_var %}DROP TABLE x{% else %}SELECT 1{% endif %}"
            )

    def test_if_body_only_valid(self) -> None:
        # Both branches valid → no raise.
        validate_select_only("SELECT a FROM t {% if filter_on %}WHERE x = 1{% endif %}")

    def test_for_loop_body_walked_once(self) -> None:
        # {% for c in cols %} body is walked once; target becomes a placeholder.
        validate_select_only("SELECT a {% for c in cols %}, {{ c }}{% endfor %} FROM t")

    def test_for_else_body_walked(self) -> None:
        # jinja2 {% for %}{% else %} runs the else clause when the iterable is
        # empty. The else body must be included in the skeleton so a DROP
        # hidden there is rejected — same property as {% if %}{% else %}.
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "{% for x in [] %}SELECT 1{% else %}DROP TABLE users{% endfor %}"
            )

    def test_for_else_body_walked_after_main_statement(self) -> None:
        # A valid statement before the for-else must not mask the DROP in else.
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "SELECT 1; {% for x in [] %}body{% else %}DROP TABLE users{% endfor %}"
            )

    def test_assign_emits_nothing(self) -> None:
        # {% set x = 1 %} produces nothing — Assign node.  SELECT stays valid.
        validate_select_only("{% set x = 1 %}SELECT {{ x }}")

    def test_macro_node_raises_unparseable(self) -> None:
        with pytest.raises(UnparseableSqlError):
            validate_select_only("{% macro foo() %}SELECT 1{% endmacro %}{{ foo() }}")

    def test_include_node_raises_unparseable(self) -> None:
        with pytest.raises(UnparseableSqlError):
            validate_select_only("{% include 'helper.sql' %}")

    def test_import_node_raises_unparseable(self) -> None:
        with pytest.raises(UnparseableSqlError):
            validate_select_only("{% import 'lib.sql' as lib %}")

    def test_extends_node_raises_unparseable(self) -> None:
        with pytest.raises(UnparseableSqlError):
            validate_select_only("{% extends 'base.sql' %}")

    def test_block_node_raises_unparseable(self) -> None:
        with pytest.raises(UnparseableSqlError):
            validate_select_only("{% block content %}SELECT 1{% endblock %}")

    def test_assign_block_default_deny_raises_unparseable(self) -> None:
        # AssignBlock is NOT in the explicit allow-table — default-deny must fire.
        # If it were silently dropped the body's DROP never reaches the parser.
        # Regression test: the walker is NOT a permissive fall-through.
        with pytest.raises(UnparseableSqlError):
            validate_select_only(
                "{% set sql %}DROP TABLE x{% endset %}{{ sql | safe }}"
            )

    def test_template_syntax_error_raises_unparseable(self) -> None:
        # Jinja parse failure → UnparseableSqlError, not TemplateSyntaxError.
        with pytest.raises(UnparseableSqlError):
            validate_select_only("SELECT {% unclosed_tag")

    def test_quick_check_no_jinja(self) -> None:
        # No {{ or {% → treated as raw SQL, no Jinja parse overhead.
        validate_select_only("SELECT a, b FROM t WHERE x = 1")


# ---------------------------------------------------------------------------
# Group 2: validate_select_only — allowlist passes, parametrized over dialects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dialect", DIALECTS)
class TestValidateSelectOnlyPasses:
    def test_select_one(self, dialect: str | None) -> None:
        validate_select_only("SELECT 1", dialect=dialect)

    def test_cte_select(self, dialect: str | None) -> None:
        validate_select_only("WITH x AS (SELECT 1) SELECT * FROM x", dialect=dialect)

    def test_union_all(self, dialect: str | None) -> None:
        validate_select_only("(SELECT 1) UNION ALL (SELECT 2)", dialect=dialect)

    def test_describe(self, dialect: str | None) -> None:
        if dialect in {"mysql", "sqlserver"}:
            pytest.skip(f"DESCRIBE not standard for {dialect}")
        validate_select_only("DESCRIBE my_table", dialect=dialect)

    def test_explain_select(self, dialect: str | None) -> None:
        # Spec contract: EXPLAIN of an allowed query passes. The validator
        # strips the EXPLAIN keyword and revalidates the body.
        validate_select_only("EXPLAIN SELECT 1", dialect=dialect)

    def test_explain_analyze_select(self, dialect: str | None) -> None:
        # `EXPLAIN ANALYZE SELECT 1` — variant form on a passing inner.
        if dialect == "bigquery":
            pytest.skip("BigQuery has no EXPLAIN ANALYZE syntax")
        validate_select_only("EXPLAIN ANALYZE SELECT 1", dialect=dialect)

    def test_trailing_line_comment(self, dialect: str | None) -> None:
        # sqlglot emits a standalone exp.Semicolon node when a comment trails
        # a semicolon. Must not be mis-rejected as a mutating statement.
        validate_select_only("SELECT 1; -- trailing note", dialect=dialect)

    def test_block_comment_between_statements(self, dialect: str | None) -> None:
        validate_select_only("SELECT 1; /* mid */ SELECT 2", dialect=dialect)

    def test_show_tables(self, dialect: str | None) -> None:
        # Spec/docstring promise: SHOW is allowed. sqlglot parses SHOW as
        # `exp.Show` on duckdb/mysql/snowflake but falls back to `exp.Command`
        # with name="SHOW" on postgres/bigquery/redshift/databricks/default —
        # both shapes must pass the validator.
        if dialect == "sqlserver":
            pytest.skip("sqlglot cannot parse SHOW for sqlserver/tsql")
        validate_select_only("SHOW TABLES", dialect=dialect)

    def test_jinja_ref(self, dialect: str | None) -> None:
        validate_select_only(
            "SELECT * FROM {{ ref('orders') }} WHERE id = {{ user_id }}",
            dialect=dialect,
        )

    def test_if_filter_clause(self, dialect: str | None) -> None:
        validate_select_only(
            "SELECT a FROM t {% if filter_on %}WHERE x = 1{% endif %}",
            dialect=dialect,
        )

    def test_for_loop_columns(self, dialect: str | None) -> None:
        validate_select_only(
            "SELECT a {% for c in cols %}, {{ c }}{% endfor %} FROM t",
            dialect=dialect,
        )


def test_fabric_accepts_tsql_top_n() -> None:
    validate_select_only("SELECT TOP 5 * FROM t", dialect="fabric")


class TestDescribeDetailDatabricks:
    """Databricks Delta Lake DESCRIBE DETAIL / HISTORY are read-only Delta-log
    reads sqlglot doesn't model. The guard strips the variant keyword so the
    body re-parses as a standard DESCRIBE — needed for the inspector's
    `_get_databricks_enrichment` path that dispatches `DESCRIBE DETAIL`.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "DESCRIBE DETAIL my_table",
            "DESCRIBE DETAIL my_schema.my_table",
            "DESCRIBE HISTORY my_table",
            "describe detail my_table",
        ],
    )
    def test_databricks_describe_variants_allowed(self, sql: str) -> None:
        validate_select_only(sql, dialect="databricks")


# ---------------------------------------------------------------------------
# Group 3: validate_select_only — must raise MutatingSqlError
# ---------------------------------------------------------------------------


class TestValidateSelectOnlyRejections:
    def test_drop_table(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_select_only("DROP TABLE users")

    def test_delete(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_select_only("DELETE FROM users WHERE 1=1")

    def test_insert(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_select_only("INSERT INTO logs VALUES (1)")

    def test_update(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_select_only("UPDATE users SET role='admin'")

    def test_truncate(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_select_only("TRUNCATE TABLE x")

    def test_alter_table(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_select_only("ALTER TABLE x ADD COLUMN y INT")

    def test_grant(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_select_only("GRANT SELECT ON x TO public")

    def test_attach(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_select_only("ATTACH DATABASE 'file.duckdb' AS d", dialect="duckdb")

    def test_call(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_select_only("CALL my_proc()")

    def test_multi_statement_injection(self) -> None:
        # Both statements are top-level — the DROP must be caught.
        with pytest.raises(MutatingSqlError):
            validate_select_only("SELECT 1; DROP TABLE users")

    def test_select_into_persistent(self) -> None:
        # Postgres/SQL Server CTAS-equivalent — SELECT INTO creates a table.
        with pytest.raises(MutatingSqlError):
            validate_select_only("SELECT * INTO exfil FROM users", dialect="postgres")

    def test_select_into_temp(self) -> None:
        # Even TEMP SELECT INTO is rejected here — it's a setup_sql concern.
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "SELECT * INTO #temp_exfil FROM users", dialect="sqlserver"
            )

    def test_branch_coverage_drop_in_else(self) -> None:
        # The DROP lives in the else branch; skeleton includes BOTH branches.
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "{% if some_var %}DROP TABLE x{% else %}SELECT 1{% endif %}"
            )

    @pytest.mark.parametrize(
        "sql",
        [
            # DROP in 1st, 2nd, and 3rd elif slot — every position must be walked.
            "{% if a %}{% elif b %}DROP TABLE x{% elif c %}SELECT 1{% endif %}",
            "{% if a %}{% elif b %}SELECT 1{% elif c %}DROP TABLE x{% endif %}",
            "{% if a %}{% elif b %}{% elif c %}DROP TABLE x{% else %}SELECT 1{% endif %}",
        ],
    )
    def test_branch_coverage_drop_in_any_elif(self, sql: str) -> None:
        # Regression for the elif-list iteration bug: every elif body must be
        # walked, not just the first. The reviewer's reproducer.
        with pytest.raises(MutatingSqlError):
            validate_select_only(sql)

    def test_pragma_rejected(self) -> None:
        # PRAGMA enable_external_access=true would re-enable what the
        # read-only DuckDB default disables. Must be rejected.
        with pytest.raises(MutatingSqlError):
            validate_select_only("PRAGMA enable_external_access=true", dialect="duckdb")

    def test_load_rejected(self) -> None:
        # LOAD installs/activates DuckDB extensions — side-effecting.
        with pytest.raises(MutatingSqlError):
            validate_select_only("LOAD 'http_extension'", dialect="duckdb")

    def test_revoke_rejected(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_select_only("REVOKE SELECT ON x FROM public")

    def test_select_into_in_subquery_rejected(self) -> None:
        # Parenthesized SELECT ... INTO parses as Subquery(Select(into=...)).
        # The into-check must recurse into descendants, not just top-level Select.
        with pytest.raises(MutatingSqlError):
            validate_select_only("(SELECT * INTO foo FROM bar)", dialect="postgres")

    @pytest.mark.parametrize(
        "inner",
        [
            "DELETE FROM users RETURNING *",
            "INSERT INTO logs VALUES (1) RETURNING *",
            "UPDATE users SET role='admin' RETURNING *",
        ],
    )
    @pytest.mark.parametrize("dialect", ["postgres", "duckdb", "snowflake"])
    def test_data_modifying_cte_rejected(self, inner: str, dialect: str) -> None:
        # `WITH x AS (DELETE/INSERT/UPDATE ... RETURNING *) SELECT * FROM x`
        # parses as a top-level Select with the DML hidden in a CTE.
        # find_all over the parsed tree must catch the DML descendant.
        sql = f"WITH x AS ({inner}) SELECT * FROM x"
        with pytest.raises(MutatingSqlError):
            validate_select_only(sql, dialect=dialect)

    def test_data_modifying_cte_in_subquery_rejected(self) -> None:
        # Even when the DML-bearing CTE is wrapped in parens (a Subquery).
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "(WITH x AS (DELETE FROM users RETURNING id) SELECT * FROM x)",
                dialect="postgres",
            )

    def test_data_modifying_cte_in_union_rejected(self) -> None:
        # And when the DML-bearing CTE is one arm of a UNION.
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "(WITH a AS (DELETE FROM users RETURNING id) SELECT * FROM a) "
                "UNION ALL (SELECT 1)",
                dialect="postgres",
            )

    def test_explain_drop_rejected(self) -> None:
        # EXPLAIN parses opaquely as a Command in sqlglot — the inner DROP
        # is invisible to the AST. The validator strips the leading EXPLAIN
        # keyword and re-validates the body so `EXPLAIN DROP TABLE x` fails.
        with pytest.raises(MutatingSqlError):
            validate_select_only("EXPLAIN DROP TABLE x", dialect="postgres")

    def test_explain_analyze_drop_rejected(self) -> None:
        # `EXPLAIN ANALYZE` in Postgres actually executes the inner statement;
        # the strip-and-revalidate path catches the inner DROP either way.
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "EXPLAIN ANALYZE DELETE FROM users", dialect="postgres"
            )

    def test_error_message_lists_allowed_set_accurately(self) -> None:
        # The user-facing error message must match what the implementation
        # actually permits. Pin a few canonical tokens so docstring/message
        # drift cannot reintroduce the historical EXPLAIN-lie.
        with pytest.raises(MutatingSqlError) as exc_info:
            validate_select_only("DROP TABLE users")
        msg = str(exc_info.value)
        for token in ("SELECT", "WITH", "UNION", "DESCRIBE", "EXPLAIN"):
            assert token in msg, f"{token!r} missing from error message: {msg}"

    def test_error_carries_rejected_node_kind(self) -> None:
        with pytest.raises(MutatingSqlError) as exc_info:
            validate_select_only("DROP TABLE users")
        err = exc_info.value
        assert err.rejected_node_kind is not None
        assert (
            "Drop" in err.rejected_node_kind or "drop" in err.rejected_node_kind.lower()
        )

    def test_error_carries_fragment_preview(self) -> None:
        with pytest.raises(MutatingSqlError) as exc_info:
            validate_select_only("DROP TABLE users")
        err = exc_info.value
        assert err.fragment_preview is not None
        assert len(err.fragment_preview) <= 60


# ---------------------------------------------------------------------------
# Group 4: validate_select_only — must raise UnparseableSqlError
# ---------------------------------------------------------------------------


class TestValidateSelectOnlyDefer:
    def test_include_directive(self) -> None:
        with pytest.raises(UnparseableSqlError):
            validate_select_only("{% include 'helper.sql' %}")

    def test_macro_definition(self) -> None:
        with pytest.raises(UnparseableSqlError):
            validate_select_only("{% macro foo() %}SELECT 1{% endmacro %}{{ foo() }}")

    def test_assign_block_default_deny(self) -> None:
        # {% set sql %}...{% endset %} produces AssignBlock — not in allow-table.
        with pytest.raises(UnparseableSqlError):
            validate_select_only(
                "{% set sql %}DROP TABLE x{% endset %}{{ sql | safe }}"
            )

    def test_sqlglot_parse_failure(self) -> None:
        # Gibberish that sqlglot cannot parse after skeleton built.
        with pytest.raises(UnparseableSqlError):
            validate_select_only("SELECT FROM WHERE")

    def test_error_carries_cause(self) -> None:
        with pytest.raises(UnparseableSqlError) as exc_info:
            validate_select_only("{% include 'helper.sql' %}")
        assert exc_info.value.cause is not None

    def test_error_code_is_unparseable_not_unknown(self) -> None:
        # "Could not validate" is a well-defined error case — not an unknown
        # internal failure. Stamping ERR_INTERNAL would set off the
        # wrong alerting in structured-error pipelines.
        with pytest.raises(UnparseableSqlError) as exc_info:
            validate_select_only("{% include 'helper.sql' %}")
        assert exc_info.value.code is not None
        assert exc_info.value.code.code == "ERR-UNPARSEABLE-SQL"

    def test_sqlglot_parse_error_carries_sql_local_position(self) -> None:
        # sqlglot reports a structured {'line', 'col', 'highlight'} for a real
        # ParseError — capture it as a typed position instead of letting it
        # dissolve into the message string only.
        with pytest.raises(UnparseableSqlError) as exc_info:
            validate_select_only("WITHasdf x AS (SELECT 1) SELECT * FROM x")
        position = exc_info.value.sql_position
        assert position is not None
        assert position.line == 1
        # "AS" is the offending token — start/end are its exact span, not a
        # guessed single column.
        sql = "WITHasdf x AS (SELECT 1) SELECT * FROM x"
        assert sql[position.start_col - 1 : position.end_col - 1] == "AS"
        # The position travels on .fields too, so it survives a
        # QueryResult -> QueryError.from_code(**fields) round trip.
        assert exc_info.value.fields["sql_line"] == position.line
        assert exc_info.value.fields["sql_start_col"] == position.start_col
        assert exc_info.value.fields["sql_end_col"] == position.end_col

    def test_unclosed_cte_paren_is_named(self) -> None:
        # The author's CTE `s AS (` is never closed, so the parser reports
        # "Expecting )" far from the cause. DuckDB rejects this SQL too.
        sql = (
            "WITH s AS (SELECT * FROM (SELECT a, b FROM t) u "
            "UNPIVOT (v FOR k IN (a, b)), n AS (SELECT 1 AS m) "
            "SELECT * FROM s JOIN n ON TRUE"
        )
        with pytest.raises(UnparseableSqlError) as exc_info:
            validate_select_only(sql, dialect="duckdb")
        assert "has 5 '(' but 4 ')'" in str(exc_info.value)
        assert "missing its closing ')'" in str(exc_info.value)

    def test_extra_closing_paren_is_named(self) -> None:
        with pytest.raises(UnparseableSqlError) as exc_info:
            validate_select_only("SELECT (1 + 2)) AS x", dialect="duckdb")
        assert "has 1 '(' but 2 ')'" in str(exc_info.value)
        assert "extra ')'" in str(exc_info.value)

    def test_missing_comma_between_ctes_is_named(self) -> None:
        sql = "WITH a AS (SELECT 1 AS x) b AS (SELECT 2 AS y) SELECT * FROM a, b"
        with pytest.raises(UnparseableSqlError) as exc_info:
            validate_select_only(sql, dialect="duckdb")
        assert "needs a comma between CTEs" in str(exc_info.value)

    def test_a_pivot_statement_cannot_launder_a_write(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "CREATE TABLE p AS PIVOT (SELECT 1 AS a, 2 AS b) ON b USING sum(a)",
                dialect="duckdb",
            )

    def test_balanced_parse_failure_adds_no_paren_advice(self) -> None:
        with pytest.raises(UnparseableSqlError) as exc_info:
            validate_select_only("WITHasdf x AS (SELECT 1) SELECT * FROM x")
        assert "'(' but" not in str(exc_info.value)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM (SELECT 1 AS a, 2 AS b) u UNPIVOT (v FOR k IN (a, b))",
            "UNPIVOT (SELECT 1 AS a, 2 AS b) ON a, b INTO NAME k VALUE v",
            "SELECT * FROM (SELECT 1 AS a, 2 AS b) PIVOT (sum(a) FOR b IN (2))",
            "PIVOT (SELECT 1 AS a, 2 AS b) ON b USING sum(a)",
            "WITH x AS (SELECT 1 AS a, 2 AS b) UNPIVOT x ON a, b",
        ],
    )
    def test_duckdb_pivot_forms_the_warehouse_runs_parse(self, sql: str) -> None:
        validate_select_only(sql, dialect="duckdb")

    def test_non_sqlglot_unparseable_error_has_no_position(self) -> None:
        # The jinja-node bail-out path (a plain str cause) has no sqlglot
        # position to capture — must not fabricate one.
        with pytest.raises(UnparseableSqlError) as exc_info:
            validate_select_only("{% macro foo() %}SELECT 1{% endmacro %}{{ foo() }}")
        assert exc_info.value.sql_position is None
        assert "sql_line" not in exc_info.value.fields


# ---------------------------------------------------------------------------
# Group 5 + 6: validate_setup_sql
# ---------------------------------------------------------------------------


class TestValidateSetupSqlPasses:
    def test_create_temp_function_bigquery_style(self) -> None:
        validate_setup_sql(
            "CREATE TEMP FUNCTION add_one(x INT64) RETURNS INT64 AS (x + 1)",
            dialect="bigquery",
        )

    def test_create_macro_duckdb(self) -> None:
        # DuckDB: no CREATE TEMP MACRO syntax; kind=MACRO is allowed without TEMP.
        validate_setup_sql(
            "CREATE OR REPLACE MACRO add_ten(x) AS x + 10",
            dialect="duckdb",
        )

    def test_create_temp_table(self) -> None:
        validate_setup_sql("CREATE TEMP TABLE staging AS SELECT 1")

    def test_create_temp_view(self) -> None:
        validate_setup_sql("CREATE TEMP VIEW v AS SELECT 1")


class TestValidateSetupSqlRejections:
    def test_create_permanent_table(self) -> None:
        # Non-TEMP create is not in setup_sql allowlist.
        with pytest.raises(MutatingSqlError):
            validate_setup_sql("CREATE TABLE permanent_log (x INT)")

    def test_drop_function(self) -> None:
        with pytest.raises(MutatingSqlError):
            validate_setup_sql("DROP FUNCTION my_fn")

    def test_mixed_statements_second_violates(self) -> None:
        # First statement is valid, second (DROP) must be caught.
        with pytest.raises(MutatingSqlError):
            validate_setup_sql("CREATE TEMP FUNCTION f() AS (1); DROP TABLE x")

    @pytest.mark.parametrize(
        "inner",
        [
            "DELETE FROM users RETURNING id",
            "INSERT INTO secrets VALUES (1) RETURNING *",
            "UPDATE users SET role='admin' RETURNING *",
        ],
    )
    def test_ctas_with_dml_cte_rejected(self, inner: str) -> None:
        # CREATE TEMP TABLE AS … with a DML-bearing CTE is a hostile-input
        # bypass: the outer CREATE TEMP TABLE passes the setup allowlist, but
        # the CTE body smuggles DELETE/INSERT/UPDATE past the gate. Same
        # descendant-scan posture as validate_select_only must apply here.
        sql = f"CREATE TEMP TABLE t AS WITH x AS ({inner}) SELECT * FROM x"
        with pytest.raises(MutatingSqlError):
            validate_setup_sql(sql, dialect="postgres")

    def test_ctas_with_select_into_rejected(self) -> None:
        # SELECT … INTO buried inside a CTAS body is the same hostile shape.
        with pytest.raises(MutatingSqlError):
            validate_setup_sql(
                "CREATE TEMP TABLE t AS (SELECT * INTO perm FROM users)",
                dialect="postgres",
            )

    def test_error_message_names_setup_context(self) -> None:
        # The MutatingSqlError raised from validate_setup_sql should not
        # claim "non-read-only SQL" — the policy here is the setup-allowlist,
        # not the read-only allowlist. The message must name its own policy.
        with pytest.raises(MutatingSqlError) as exc_info:
            validate_setup_sql("DROP FUNCTION my_fn")
        msg = str(exc_info.value)
        # Either the message says "setup" or it names the allowed setup forms.
        assert "setup" in msg.lower() or "CREATE TEMP" in msg, (
            f"setup_sql error message has no setup-context cue: {msg}"
        )


# ---------------------------------------------------------------------------
# Group 7: validate_select_only — BigQuery CREATE TEMPORARY FUNCTION scripts
# ---------------------------------------------------------------------------


class TestBigQueryTempFunctionScript:
    """BigQuery allows CREATE TEMP FUNCTION ... ; SELECT ... as a read-only
    scripting pattern. validate_select_only must allow these scripts on the
    bigquery dialect only.
    """

    def test_single_temp_function_plus_select_allowed(self) -> None:
        # Core use-case: inline scalar UDF followed by a SELECT.
        validate_select_only(
            "CREATE TEMP FUNCTION add_one(x INT64) RETURNS INT64 AS (x + 1);\n"
            "SELECT add_one(col) FROM my_table",
            dialect="bigquery",
        )

    def test_multiple_temp_functions_plus_select_allowed(self) -> None:
        # Multiple UDF definitions before a single trailing SELECT.
        validate_select_only(
            "CREATE TEMP FUNCTION f1(x INT64) RETURNS INT64 AS (x + 1);\n"
            "CREATE TEMP FUNCTION f2(x INT64) RETURNS INT64 AS (x + 2);\n"
            "SELECT f1(col), f2(col) FROM my_table",
            dialect="bigquery",
        )

    def test_temp_function_only_no_select_rejected(self) -> None:
        # A lone TEMP FUNCTION with no trailing SELECT is setup SQL, not a query.
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "CREATE TEMP FUNCTION add_one(x INT64) RETURNS INT64 AS (x + 1)",
                dialect="bigquery",
            )

    def test_temp_function_plus_non_select_final_rejected(self) -> None:
        # TEMP FUNCTION followed by a non-SELECT statement must be rejected.
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "CREATE TEMP FUNCTION f(x INT64) RETURNS INT64 AS (x + 1);\n"
                "DROP TABLE users",
                dialect="bigquery",
            )

    def test_create_table_not_allowed_bigquery(self) -> None:
        # Non-TEMP CREATE (e.g. CREATE TABLE) must still be rejected even in
        # BigQuery multi-statement context.
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "CREATE TABLE permanent_log (x INT64);\nSELECT * FROM permanent_log",
                dialect="bigquery",
            )

    def test_non_temp_function_create_rejected_bigquery(self) -> None:
        # CREATE FUNCTION without TEMP must be rejected (not a read-only pattern).
        with pytest.raises(MutatingSqlError):
            validate_select_only(
                "CREATE FUNCTION add_one(x INT64) RETURNS INT64 AS (x + 1);\n"
                "SELECT add_one(1)",
                dialect="bigquery",
            )

    def test_other_dialect_temp_function_still_rejected(self) -> None:
        # The BigQuery-specific allowance must not bleed into other dialects.
        for dialect in ("duckdb", "postgres", "snowflake"):
            with pytest.raises(MutatingSqlError):
                validate_select_only(
                    "CREATE TEMP FUNCTION f(x INT) RETURNS INT AS (x + 1);\n"
                    "SELECT f(1)",
                    dialect=dialect,
                )

    def test_temp_function_with_cte_laundered_dml_rejected(self) -> None:
        # Even if the function body smuggles DML via a CTE it should not escape
        # the _reject_mutating_descendants scan. BigQuery inline functions are
        # pure expressions so sqlglot will parse valid ones cleanly — use a
        # shape that actually parses (table subquery in function body) rather
        # than an unparseable one to avoid UnparseableSqlError masking the test.
        # The trailing SELECT must still be present so the TEMP-FUNCTION allowance
        # triggers; the descendant-scan must then catch the buried Delete.
        #
        # In practice BigQuery inline functions cannot contain DML, so the
        # sqlglot parser rejects those shapes and raises UnparseableSqlError
        # rather than MutatingSqlError. Both are non-pass outcomes — assert that
        # a DML-containing body is never silently allowed.
        with pytest.raises((MutatingSqlError, UnparseableSqlError)):
            validate_select_only(
                "CREATE TEMP FUNCTION f(x INT64) RETURNS INT64 AS (\n"
                "  (WITH bad AS (DELETE FROM users WHERE 1=1 RETURNING id)\n"
                "   SELECT MAX(id) FROM bad)\n"
                ");\n"
                "SELECT f(1)",
                dialect="bigquery",
            )


class TestUnparseableSqlMessageIsTerminalFree:
    def test_sqlglot_ansi_underline_is_stripped_from_the_message(self) -> None:
        """sqlglot underlines the offending token with terminal escapes. They
        read fine in a shell and render as literal "ESC[4m" garbage in the
        Cloud editor tooltip and VS Code's Problems panel, which is where
        these messages now go."""
        with pytest.raises(UnparseableSqlError) as exc_info:
            validate_select_only("SELECT 1 FROM t WHERE aa bb", dialect="duckdb")

        assert "\x1b" not in str(exc_info.value)
        assert "\x1b" not in exc_info.value.fields["cause"]
        # The token itself survives — only the escapes go.
        assert "bb" in exc_info.value.fields["cause"]


class TestTokenizerFailures:
    """A tokenizer failure must convert like a parser failure.

    `TokenError` is a sibling of `ParseError` under `SqlglotError`, not a
    subclass, so catching only `ParseError` let it escape the
    `UnparseableSqlError` conversion and surface uncoded as ERR-INTERNAL.
    """

    def test_unterminated_string_raises_unparseable(self) -> None:
        """`''''` opens a triple-quoted string on BigQuery — sqlglot scans to EOF."""
        with pytest.raises(UnparseableSqlError):
            validate_select_only("SELECT length('''') AS n", dialect="bigquery")

    def test_tokenizer_failure_carries_no_position(self) -> None:
        """A tokenizer failure has no `.errors`, so there is no position to report."""
        with pytest.raises(UnparseableSqlError) as exc_info:
            validate_select_only("SELECT length('''') AS n", dialect="bigquery")

        assert exc_info.value.sql_position is None

    def test_validate_select_only_is_not_bypassed_by_tokenizer_failure(self) -> None:
        """An unterminated literal must not be mistaken for allowed read-only SQL."""
        with pytest.raises(UnparseableSqlError):
            validate_select_only("SELECT 'abc", dialect="duckdb")


class TestNonExpressionNodeDefaultDenies:
    """A sqlglot node with no Expression base must reject, not pass silently.

    Every concrete statement sqlglot's parser can actually produce multiply-
    inherits Expression today, so this path is unreachable under the locked
    sqlglot release — but it is exactly the guard that closes the hole a
    stripped `assert` would leave open: a non-Expression node reaching the
    allowlist loop as the query's only statement would make that loop run
    zero times and `validate_select_only`/`validate_setup_sql` return
    without raising, letting a mutating statement through unchecked. A fake
    node exercises the arm without depending on any real sqlglot version.
    """

    class _NotAnExpression:
        """Stands in for a hypothetical future sqlglot node with no Expression base."""

    def test_validate_select_only_rejects_a_non_expression_node(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sqlglot, "parse", lambda *args, **kwargs: [self._NotAnExpression()]
        )
        with pytest.raises(UnparseableSqlError):
            validate_select_only("SELECT 1", dialect="duckdb")

    def test_validate_setup_sql_rejects_a_non_expression_node(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sqlglot, "parse", lambda *args, **kwargs: [self._NotAnExpression()]
        )
        with pytest.raises(UnparseableSqlError):
            validate_setup_sql("CREATE TEMP TABLE t AS SELECT 1", dialect="duckdb")


def test_lakecompute_parses_as_duckdb() -> None:
    """Lake compute's SQL is rendered as DuckDB; sqlglot has no `lakecompute`
    dialect, so the engine's alias map names it."""
    from dbt_charts.core.compile.sql_guard import sqlglot_dialect

    assert sqlglot_dialect("lakecompute") == "duckdb"


def test_unparseable_sql_fix_names_reserved_word_aliases_first() -> None:
    from dbt_charts.core.diagnostics.codes_execute import ERR_UNPARSEABLE_SQL

    fix = ERR_UNPARSEABLE_SQL.fix_template
    assert fix is not None
    assert fix.index("reserved word") < fix.index("unclosed `(`")
