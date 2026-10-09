"""compile() surfaces SQL syntax errors, so editors can squiggle them.

Before this, a board with an unparseable query body compiled clean: the
compile-time guard caught the failure and dropped it into `logger.debug`, so
`dct validate` printed OK and VS Code's Problems panel stayed empty. The
author found out when the warehouse rejected the query.

The reason it stayed off is noise, so most of these tests are about what must
NOT warn. Two things gate the emission: the query's dialect has to be
resolvable from its source (BigQuery SQL parsed as generic SQL fails on ~40%
of real boards), and sqlglot has to hand back a position we can trust.
"""

from __future__ import annotations

from dbt_charts.core.compile.compiler import CompileResult, compile
from dbt_charts.core.compile.config import ProjectSourcesConfig

_DUCKDB = ProjectSourcesConfig(sources={"db": {"type": "duckdb"}})
_BIGQUERY = ProjectSourcesConfig(sources={"bq": {"type": "bigquery"}})


def _board(sql: str, source: str = "db") -> str:
    body = "\n".join(f"      {line}" for line in sql.splitlines())
    return f"""\
queries:
  q1:
    sql: |
{body}
    source: {source}
charts:
  c1:
    type: table
    query: q1
rows:
  - c1
"""


def _compile(
    sql: str,
    source: str = "db",
    project_sources: ProjectSourcesConfig | None = _DUCKDB,
) -> CompileResult:
    return compile(_board(sql, source), file="f.yml", project_sources=project_sources)


def _parse_warnings(result: CompileResult) -> list[str]:
    return [w.code for w in result.warnings if w.code == "WARN-PARSE-ERROR"]


def test_unparseable_sql_emits_one_parse_warning_ranged_at_the_query() -> None:
    result = _compile("SELEKT FROM WHERE ((")

    assert _parse_warnings(result) == ["WARN-PARSE-ERROR"]
    warning = next(w for w in result.warnings if w.code == "WARN-PARSE-ERROR")
    assert warning.path == "queries.q1.sql"
    # Ranged inside the sql: block — line 4 is the query's only content line.
    assert warning.range is not None
    assert warning.range.start_line == 4


def test_parse_warning_underlines_the_offending_token() -> None:
    """At compile time the authored text *is* what the parser saw — no
    variables substituted yet — so the SQL-local position translates into the
    board file exactly, and the mark lands on the token rather than the whole
    block."""
    result = _compile("SELECT 1 AS ok\nFROM t WHERE aa bb")
    warning = next(w for w in result.warnings if w.code == "WARN-PARSE-ERROR")

    assert warning.range is not None
    assert warning.range.start_line == warning.range.end_line == 5
    assert warning.range.columns is not None
    board_line = _board("SELECT 1 AS ok\nFROM t WHERE aa bb").splitlines()[4]
    token = board_line[
        warning.range.columns.start_col - 1 : warning.range.columns.end_col - 1
    ]
    assert token == "bb"


def test_parse_warning_does_not_fail_compile() -> None:
    """`WARN-PARSE-ERROR` is a warning, deliberately: compile has no dbt
    manifest to resolve {{ ref() }} against, so it cannot prove the SQL is
    broken — only that it could not check it. It must not fail the build or
    change `dct validate`'s exit code."""
    result = _compile("SELEKT FROM WHERE ((")

    assert result.success
    assert result.errors == []


def test_valid_sql_emits_no_parse_warning() -> None:
    assert _parse_warnings(_compile("SELECT 1 AS ok")) == []


def test_jinja_heavy_but_valid_sql_emits_no_parse_warning() -> None:
    """`{{ ref() }}` and `{% if %}` are normal authoring. The guard
    skeletonizes them before parsing; anything that still trips the parser
    here would be pure noise."""
    sql = (
        "SELECT {{ column }} AS c, SUM(revenue) AS r\n"
        "FROM {{ ref('fct_orders') }}\n"
        "WHERE {{ filter('region', region) }}\n"
        "GROUP BY 1"
    )
    assert _parse_warnings(_compile(sql)) == []


def test_inline_if_in_expression_position_emits_no_parse_warning() -> None:
    """An `{% if %}` used as a *value* is a statement node in jinja's AST, so
    the skeleton walk joins its branches with `;` and the parser sees a
    dangling second statement. The SQL is fine; the skeleton is not. This is
    the shape that fires on a shipped example board, so it must stay silent."""
    sql = (
        "SELECT region, SUM(revenue) AS r\n"
        "FROM orders\n"
        "GROUP BY 1\n"
        "HAVING SUM(revenue) >= "
        "{% if min_revenue is defined %}{{ min_revenue }}{% else %}0{% endif %}"
    )
    assert _parse_warnings(_compile(sql)) == []


def test_dialect_specific_sql_parses_under_its_own_source_dialect() -> None:
    """BigQuery's `EXCEPT (col)` is a syntax error in generic SQL. Resolving
    the dialect from the query's source is what keeps this quiet — parsing it
    dialect-less is how a parse warning ends up on most real boards."""
    sql = "SELECT * EXCEPT (internal_id) FROM `proj.ds.orders`"
    assert _parse_warnings(_compile(sql, source="bq", project_sources=_BIGQUERY)) == []


def test_unresolvable_dialect_stays_silent() -> None:
    """No source registry (a pure in-memory compile, or a dbt-profile source
    whose real dialect is not knowable until execute) must degrade to no
    diagnostic — never to a wrong-dialect false positive."""
    assert _parse_warnings(_compile("SELEKT FROM WHERE ((", project_sources=None)) == []


def test_parse_warning_does_not_drag_in_the_relationship_gated_warnings() -> None:
    """`validate_compiled_queries` also emits WARN-MISSING-JOIN-PREDICATE and
    WARN-REAGGREGATION. Turning them on at compile time is a separate product
    call with its own noise budget."""
    sql = (
        "SELECT o.id, SUM(li.amount) AS total\nFROM orders o, line_items li\nGROUP BY 1"
    )
    codes = {w.code for w in _compile(sql).warnings}

    assert codes.isdisjoint({"WARN-MISSING-JOIN-PREDICATE", "WARN-REAGGREGATION"})


def test_parse_warning_message_has_no_terminal_escapes() -> None:
    """sqlglot underlines the offending token with ANSI escapes. They read
    fine in a shell and render as literal "ESC[4m" garbage in the Cloud editor
    tooltip and VS Code's Problems panel — which is exactly where this warning
    is meant to go."""
    result = _compile("SELECT 1 FROM t WHERE aa bb")
    warning = next(w for w in result.warnings if w.code == "WARN-PARSE-ERROR")

    assert "\x1b" not in warning.message
    assert "bb" in warning.message


def test_inline_chart_query_does_not_warn_under_a_synthetic_name() -> None:
    """`normalize_board` adds its own entries to the same registry the harvest
    walks — an inline `charts.<id>.query: {sql: |}` lands as
    `_inline_query_<chart>`. That name has no `queries.<name>` node, so
    warning on it would quote an identifier the author never typed and carry
    no position. Execute time still catches the SQL.
    """
    board = """\
charts:
  c1:
    type: table
    query:
      sql: |
        SELECT 1 FROM t WHERE aa bb
      source: db
rows:
  - c1
"""
    result = compile(board, file="f.yml", project_sources=_DUCKDB)

    assert _parse_warnings(result) == []
    assert not any("_inline_query" in w.message for w in result.warnings)


def test_variable_option_query_does_not_warn_under_a_synthetic_name() -> None:
    """Same boundary for promoted variable-option queries
    (`_var_options_<var>`)."""
    board = """\
variables:
  region:
    input: select
    query:
      sql: |
        SELECT region FROM t WHERE aa bb
      source: db
    column: region
queries:
  ok:
    sql: |
      SELECT 1 AS n
    source: db
charts:
  c1:
    type: table
    query: ok
rows:
  - c1
"""
    result = compile(board, file="f.yml", project_sources=_DUCKDB)

    assert not any("_var_options" in w.message for w in result.warnings)
