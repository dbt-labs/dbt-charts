"""Every variable input kind, through every way a query consumes it, on every dialect.

A variable's value takes one of a few routes into SQL — bare `{{ v }}`,
`filter()`, `filter_date_range()` — and what those routes emit has to be
accepted by every warehouse dbt charts ships a dialect for. The engines
disagree exactly where it hurts: Postgres and DuckDB coerce a DATE against a
TIMESTAMP column and a string against a number, BigQuery and Trino refuse both,
and SQLite has no DATE type at all. These tests pin the rendered SQL per
dialect and per input kind, so the next dialect-specific spelling is caught
here rather than on a warehouse.

Layer 1 — every registered dialect, no engine. Each case is rendered the way
`SqlAdapter` renders for a dbt warehouse (internal placeholders, then literals
inlined for the warehouse), parsed by sqlglot in that dialect's grammar, and
type-annotated against a declared column schema. Every comparison the query
makes must compare like with like: DATE with DATE, number with number. That is
the rule BigQuery enforces at the wire, and the strictest engine in the fleet
is the one the rendered SQL has to satisfy everywhere. DuckDB and SQLite bind
parameters rather than inlining, so for them this layer is a type analysis of
the same comparison shape; Layer 2 runs their real path.

Layer 2 — execution, with row-level expectations. DuckDB and SQLite (bound
parameters, the adapters' own path) and Postgres (the runner's PostgreSQL) run
on every PR, in test-dataface.yml's `test` job. BigQuery (a dry run through
WIF) and Databricks run nightly in warehouse-nightly.yml. Locally Postgres,
BigQuery and Databricks skip until their variables are set; BigQuery uses
Application Default Credentials:

    DCT_TEST_POSTGRES_URL=<url> DCT_TEST_BIGQUERY_PROJECT=<gcp project> \
    uv run pytest tests/core/test_variable_sql_cross_dialect.py

Postgres executes the inlined literals; BigQuery dry-runs them, which scans
nothing and is the one engine here that rejects a mistyped comparison.
Databricks executes them against the test SQL warehouse once
DCT_TEST_DATABRICKS_HOST, _HTTP_PATH and _TOKEN are set.

Each case starts from the value's runtime shape (the string a URL parameter
carries, or the typed value a YAML default carries) and goes through the same
coercion the executor applies, so a gap between what a control sends and what
the SQL layer types is visible here.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pytest
import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.qualify import qualify
from sqlglot.schema import MappingSchema

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import SQLiteSourceConfig
from dbt_charts.core.compile.models.variable.authored import (
    Variable,
    VariableOptions,
)
from dbt_charts.core.compile.template.parameterized import render_parameterized
from dbt_charts.core.compile.template.variables import (
    coerce_variable_values,
    parse_variable_json_strings,
)
from dbt_charts.core.dialects import DIALECTS, SQLDialect, get_dialect
from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter
from dbt_charts.core.execute.sql_literals import (
    INLINE_PLACEHOLDERS,
    inline_params_for_dialect,
    sql_string_literal,
)

# One entry per dialect class, not per alias ("postgresql", "mssql", "trino"
# resolve to the same objects and would only repeat rows).
CANONICAL_DIALECTS = sorted({d.name for d in DIALECTS.values()})

# Where our name and sqlglot's differ.
SQLGLOT_NAMES = {"sqlserver": "tsql", "lakecompute": "duckdb"}

DATE_KINDS = frozenset({"date", "datepicker", "daterange"})


def _compares_as_date(case: Case) -> bool:
    return case.kind in DATE_KINDS or case.data_type == "date"


# Fixture table shared by every engine. Row 2 carries a TIMESTAMP at 13:00 on
# the day a date variable names and a quote in the string; row 4 a backslash.
COLUMN_TYPES = {
    "id": "INT",
    "d": "DATE",
    "ts": "TIMESTAMP",
    "n": "INT",
    "f": "DOUBLE",
    "s": "VARCHAR",
    "b": "BOOLEAN",
}
ROWS = [
    (1, "2024-01-01", "2024-01-01 00:00:00", 1, 1.5, "gold", True),
    (2, "2024-01-15", "2024-01-15 13:00:00", 2, 2.5, "O'Brien", False),
    (3, "2024-02-01", "2024-02-01 08:00:00", 3, 3.5, "silver", True),
    (4, "2024-03-01", "2024-03-01 09:00:00", 4, 4.5, "x\\y", False),
]
ALL = frozenset({1, 2, 3, 4})
NONE: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Case:
    """One variable value, one way of consuming it, one column to compare."""

    id: str
    kind: str
    raw: object
    """The value in its runtime shape: a URL string, or a typed YAML default."""
    shape: str
    """WHERE-clause template; `{col}` is the fixture column."""
    columns: tuple[str, ...]
    expect: frozenset[int]
    """Row ids the predicate matches on an engine holding ROWS."""
    static_options: tuple[str | int, ...] | None = None
    data_type: str | None = None

    def variable(self) -> Variable:
        options = (
            VariableOptions(static=list(self.static_options))
            if self.static_options
            else VariableOptions(query="opts")
        )
        return Variable.model_validate(
            {"input": self.kind, "options": options, "data_type": self.data_type}
        )

    def bound_value(self) -> object:
        """The value as the executor hands it to the SQL layer."""
        registry = {"v": self.variable()}
        parsed = parse_variable_json_strings({"v": self.raw})
        return coerce_variable_values(parsed, registry)["v"]

    def template(self, column: str) -> str:
        return "SELECT id FROM t WHERE " + self.shape.format(col=column)


FILTER_EQ = "{{{{ filter('{col}', v) }}}}"
FILTER_GTE = "{{{{ filter('{col}', v, '>=') }}}}"
FILTER_LTE = "{{{{ filter('{col}', v, '<=') }}}}"
FILTER_NOT_IN = "{{{{ filter('{col}', v, 'not in') }}}}"
FILTER_DENY = "{{{{ filter('{col}', v, none='deny') }}}}"
DATE_RANGE = "{{{{ filter_date_range('{col}', v) }}}}"
BARE_EQ = "{col} = {{{{ v }}}}"
BARE_QUOTED = "{col} = '{{{{ v }}}}'"
BARE_GTE = "{col} >= {{{{ v }}}}"

CASES: list[Case] = [
    # --- text: the value is a string, and the only thing that can go wrong is
    # escaping. Row 2's quote and row 4's backslash are the cases.
    Case("text_eq", "text", "O'Brien", FILTER_EQ, ("s",), {2}),
    Case("text_bare_quoted", "text", "O'Brien", BARE_QUOTED, ("s",), {2}),
    Case("text_backslash", "text", "x\\y", FILTER_EQ, ("s",), {4}),
    Case("text_empty_allows", "text", "", FILTER_EQ, ("s",), ALL),
    Case("text_empty_denies", "text", "", FILTER_DENY, ("s",), NONE),
    Case("input_eq", "input", "gold", FILTER_EQ, ("s",), {1}),
    Case("textarea_eq", "textarea", "silver", FILTER_EQ, ("s",), {3}),
    # --- numbers: URL strings must reach SQL as numbers.
    Case("number_eq", "number", "2", FILTER_EQ, ("n",), {2}),
    Case("number_bare", "number", "2", BARE_EQ, ("n",), {2}),
    Case("slider_gte", "slider", "2.5", FILTER_GTE, ("f",), {2, 3, 4}),
    Case("range_gte", "range", "2", FILTER_GTE, ("n",), {2, 3, 4}),
    Case("number_empty", "number", "", FILTER_EQ, ("n",), ALL),
    # --- checkbox: a URL "true" must reach SQL as a boolean.
    Case("checkbox_eq", "checkbox", "true", FILTER_EQ, ("b",), {1, 3}),
    Case("checkbox_bare", "checkbox", "false", BARE_EQ, ("b",), {2, 4}),
    # --- dates: against a DATE column and against a TIMESTAMP column. The
    # helpers compare the column as a date on both, so a TIMESTAMP at 13:00 on
    # the named day is that day — not "after midnight", and not a type error.
    Case("date_gte", "date", "2024-01-15", FILTER_GTE, ("d", "ts"), {2, 3, 4}),
    Case("date_eq", "date", "2024-01-15", FILTER_EQ, ("d", "ts"), {2}),
    Case("datepicker_lte", "datepicker", "2024-01-15", FILTER_LTE, ("d", "ts"), {1, 2}),
    Case("date_bare_gte", "date", "2024-01-15", BARE_GTE, ("d",), {2, 3, 4}),
    Case("date_empty", "date", "", FILTER_GTE, ("d", "ts"), ALL),
    Case(
        "daterange",
        "daterange",
        '["2024-01-01", "2024-01-15"]',
        DATE_RANGE,
        ("d", "ts"),
        {1, 2},
    ),
    Case("daterange_empty", "daterange", "", DATE_RANGE, ("d", "ts"), ALL),
    # --- select/radio: the value comes back from the URL as a string; the
    # option source says what it is. Static numeric options imply number, a
    # query-driven source needs `data_type`, and a date-typed select compares
    # the column as a date like a date input does.
    Case("select_str", "select", "gold", FILTER_EQ, ("s",), {1}, ("gold", "silver")),
    Case("radio_str", "radio", "silver", FILTER_EQ, ("s",), {3}, ("gold", "silver")),
    Case("select_int_default", "select", 2, FILTER_EQ, ("n",), {2}, (1, 2, 3)),
    Case("select_int_url", "select", "2", FILTER_EQ, ("n",), {2}, (1, 2, 3)),
    Case("select_query_str", "select", "gold", FILTER_EQ, ("s",), {1}),
    Case(
        "select_query_number", "select", "2", FILTER_EQ, ("n",), {2}, data_type="number"
    ),
    Case(
        "select_query_float_gte",
        "radio",
        "2.5",
        FILTER_GTE,
        ("f",),
        {2, 3, 4},
        data_type="number",
    ),
    Case(
        "select_query_date",
        "select",
        "2024-01-15",
        FILTER_EQ,
        ("d", "ts"),
        {2},
        data_type="date",
    ),
    # --- multiselect: always a list by the time SQL sees it, whatever the
    # control sent; `not in` spelled in lowercase as an author would.
    Case(
        "multiselect_str",
        "multiselect",
        '["gold", "silver"]',
        FILTER_EQ,
        ("s",),
        {1, 3},
    ),
    Case("multiselect_single_str", "multiselect", "gold", FILTER_EQ, ("s",), {1}),
    Case(
        "multiselect_not_in",
        "multiselect",
        '["gold", "silver"]',
        FILTER_NOT_IN,
        ("s",),
        {2, 4},
    ),
    Case(
        "multiselect_int_default",
        "multiselect",
        [1, 3],
        FILTER_EQ,
        ("n",),
        {1, 3},
        (1, 2, 3),
    ),
    Case(
        "multiselect_int_url",
        "multiselect",
        '["1", "3"]',
        FILTER_EQ,
        ("n",),
        {1, 3},
        (1, 2, 3),
    ),
    Case(
        "multiselect_query_number",
        "multiselect",
        '["1", "3"]',
        FILTER_EQ,
        ("n",),
        {1, 3},
        data_type="number",
    ),
    Case(
        "multiselect_query_date",
        "multiselect",
        '["2024-01-01", "2024-01-15"]',
        FILTER_EQ,
        ("d", "ts"),
        {1, 2},
        data_type="date",
    ),
    Case("multiselect_empty_allows", "multiselect", "", FILTER_EQ, ("s",), ALL),
    Case("multiselect_empty_denies", "multiselect", "", FILTER_DENY, ("s",), NONE),
]


def _case_params() -> list[pytest.ParameterSet]:
    return [
        pytest.param(case, column, id=f"{case.id}-{column}")
        for case in CASES
        for column in case.columns
    ]


def _render_inlined(case: Case, column: str, warehouse: SQLDialect) -> str:
    """The wire SQL for a dbt warehouse: internal placeholders, then literals."""
    rendered = render_parameterized(
        case.template(column),
        {"v": case.bound_value()},
        dialect=INLINE_PLACEHOLDERS,
        warehouse=warehouse,
    )
    return inline_params_for_dialect(
        rendered.sql, rendered.params, INLINE_PLACEHOLDERS, escaping=warehouse
    )


# ---------------------------------------------------------------------------
# Layer 1: every dialect, parsed and type-checked with sqlglot.
# ---------------------------------------------------------------------------


def _family(dtype: exp.DataType) -> str:
    kind = dtype.this
    if kind == exp.DataType.Type.DATE:
        return "date"
    if kind in exp.DataType.TEMPORAL_TYPES:
        return "timestamp"
    if kind in exp.DataType.NUMERIC_TYPES:
        return "number"
    if kind in exp.DataType.TEXT_TYPES:
        return "string"
    if kind == exp.DataType.Type.BOOLEAN:
        return "boolean"
    return kind.name


def _comparisons(
    tree: exp.Expression,
) -> Iterator[tuple[exp.Expression, list[exp.Expression]]]:
    for node in tree.walk():
        if isinstance(node, exp.Between):
            yield node, [node.this, node.args["low"], node.args["high"]]
        elif isinstance(node, exp.In):
            yield node, [node.this, *node.expressions]
        elif isinstance(
            node,
            (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike),
        ):
            yield node, [node.this, node.expression]


def _operand_families(sql: str, dialect_name: str) -> list[tuple[str, list[str]]]:
    """Parse `sql` in the dialect's grammar and type every comparison's operands."""
    read = SQLGLOT_NAMES.get(dialect_name, dialect_name)
    schema = MappingSchema(
        {"t": {c: exp.DataType.build(t) for c, t in COLUMN_TYPES.items()}},
        dialect=read,
    )
    tree = sqlglot.parse_one(sql, read=read, error_level=ErrorLevel.RAISE)
    tree = annotate_types(
        qualify(tree, schema=schema, dialect=read), schema=schema, dialect=read
    )
    return [
        (node.sql(dialect=read), [_family(op.type) for op in operands])
        for node, operands in _comparisons(tree)
    ]


class TestEveryDialectTypesCoherently:
    """The rendered SQL parses in each dialect's grammar and never compares
    across type families — the strictest engine's rule, applied to all."""

    @pytest.mark.parametrize("dialect_name", CANONICAL_DIALECTS)
    @pytest.mark.parametrize(("case", "column"), _case_params())
    def test_comparison_operands_share_a_family(
        self, dialect_name: str, case: Case, column: str
    ) -> None:
        sql = _render_inlined(case, column, get_dialect(dialect_name))
        for text, families in _operand_families(sql, dialect_name):
            assert "UNKNOWN" not in families, (
                f"{dialect_name}: untyped operand in {text}"
            )
            assert len(set(families)) == 1, (
                f"{dialect_name}: {text} compares {families}"
            )

    @pytest.mark.parametrize("dialect_name", CANONICAL_DIALECTS)
    @pytest.mark.parametrize(
        ("case", "column"),
        [
            p
            for p in _case_params()
            if _compares_as_date(p.values[0])
            and p.values[1] == "ts"
            and p.values[0].expect != ALL
        ],
    )
    def test_date_helpers_compare_a_timestamp_column_as_a_date(
        self, dialect_name: str, case: Case, column: str
    ) -> None:
        warehouse = get_dialect(dialect_name)
        sql = _render_inlined(case, column, warehouse)
        assert warehouse.date_expr("ts") in sql


class TestDateExprSpelling:
    """`date_expr` is the one per-dialect spelling the helpers depend on."""

    @pytest.mark.parametrize("dialect_name", CANONICAL_DIALECTS)
    def test_every_dialect_spells_compare_as_date(self, dialect_name: str) -> None:
        expected = "DATE(ts)" if dialect_name == "sqlite" else "CAST(ts AS DATE)"
        assert get_dialect(dialect_name).date_expr("ts") == expected

    def test_render_with_placeholder_style_needs_a_warehouse(self) -> None:
        template = "SELECT 1 WHERE {{ filter_date_range('ts', v) }}"
        value = {"v": ["2024-01-01", "2024-01-31"]}
        with pytest.raises(NotImplementedError, match="warehouse"):
            render_parameterized(template, value, dialect=INLINE_PLACEHOLDERS)
        rendered = render_parameterized(
            template,
            value,
            dialect=INLINE_PLACEHOLDERS,
            warehouse=get_dialect("sqlite"),
        )
        assert "DATE(ts) BETWEEN" in rendered.sql


# ---------------------------------------------------------------------------
# Layer 2: execution, with row expectations.
# ---------------------------------------------------------------------------

_DDL = (
    "CREATE {kind} TABLE t (id INT, d DATE, ts TIMESTAMP, n INT, "
    "f DOUBLE PRECISION, s VARCHAR, b BOOLEAN)"
)


def _insert_rows(execute: Callable[[str], object]) -> None:
    for id_, d, ts, n, f, s, b in ROWS:
        quoted = s.replace("'", "''")
        execute(
            f"INSERT INTO t VALUES ({id_}, DATE '{d}', TIMESTAMP '{ts}', {n}, {f}, "
            f"'{quoted}', {'TRUE' if b else 'FALSE'})"
        )


@pytest.fixture(scope="module")
def duck() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    con.execute(_DDL.format(kind=""))
    _insert_rows(con.execute)
    yield con
    con.close()


class TestDuckDB:
    """Bound parameters in DuckDB's own placeholder style, as the adapter binds them."""

    @pytest.mark.parametrize(("case", "column"), _case_params())
    def test_rows(
        self, duck: duckdb.DuckDBPyConnection, case: Case, column: str
    ) -> None:
        rendered = render_parameterized(
            case.template(column),
            {"v": case.bound_value()},
            dialect=get_dialect("duckdb"),
        )
        rows = duck.execute(rendered.sql, rendered.params).fetchall()
        assert {r[0] for r in rows} == case.expect


@pytest.fixture(scope="module")
def sqlite_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """SQLite has no DATE/TIMESTAMP/BOOLEAN types: ISO text and 0/1 stand in,
    which is what any real SQLite source holds."""
    path = tmp_path_factory.mktemp("sqlite") / "t.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE t (id INTEGER, d TEXT, ts TEXT, n INTEGER, f REAL, s TEXT, b INTEGER)"
    )
    con.executemany(
        "INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(*r[:6], int(r[6])) for r in ROWS],
    )
    con.commit()
    con.close()
    return path


class TestSQLite:
    """Through the SQLite adapter, which binds through sqlite3."""

    @pytest.mark.parametrize(("case", "column"), _case_params())
    def test_rows(self, sqlite_db: Path, case: Case, column: str) -> None:
        result = SqliteAdapter().execute(
            SqlQuery(sql=case.template(column), source="db"),
            variables={"v": case.bound_value()},
            source_config=SQLiteSourceConfig(type="sqlite", path=str(sqlite_db)),
        )
        assert result.error is None, result.error
        assert {r["id"] for r in result.data} == case.expect


@pytest.fixture(scope="module")
def postgres() -> Iterator[Any]:
    url = os.environ.get("DCT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("DCT_TEST_POSTGRES_URL not set")
    psycopg = pytest.importorskip("psycopg")
    con = psycopg.connect(url, autocommit=True)
    cur = con.cursor()
    cur.execute(_DDL.format(kind="TEMP"))
    _insert_rows(cur.execute)
    yield cur
    con.close()


class TestPostgres:
    """Inlined literals, as `SqlAdapter` sends them to a dbt warehouse."""

    @pytest.mark.parametrize(("case", "column"), _case_params())
    def test_rows(self, postgres: Any, case: Case, column: str) -> None:
        postgres.execute(_render_inlined(case, column, get_dialect("postgres")))
        assert {r[0] for r in postgres.fetchall()} == case.expect


def _bigquery_fixture_cte() -> str:
    def literal(s: str) -> str:
        return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"

    rows = ", ".join(
        f"STRUCT({id_} AS id, DATE '{d}' AS d, TIMESTAMP '{ts}' AS ts, {n} AS n, "
        f"{f} AS f, {literal(s)} AS s, {'TRUE' if b else 'FALSE'} AS b)"
        for id_, d, ts, n, f, s, b in ROWS
    )
    return f"WITH t AS (SELECT * FROM UNNEST([{rows}])) "


@pytest.fixture(scope="module")
def bigquery() -> tuple[Any, Any]:
    project = os.environ.get("DCT_TEST_BIGQUERY_PROJECT")
    if not project:
        pytest.skip("DCT_TEST_BIGQUERY_PROJECT not set")
    bq = pytest.importorskip("google.cloud.bigquery")
    return bq.Client(project=project), bq.QueryJobConfig(
        dry_run=True, use_query_cache=False
    )


class TestBigQueryDryRun:
    @pytest.mark.parametrize(("case", "column"), _case_params())
    def test_accepted(self, bigquery: tuple[Any, Any], case: Case, column: str) -> None:
        client, config = bigquery
        sql = _bigquery_fixture_cte() + _render_inlined(
            case, column, get_dialect("bigquery")
        )
        client.query(sql, job_config=config)


def _databricks_fixture_cte() -> str:
    databricks = get_dialect("databricks")
    rows = ", ".join(
        f"(CAST({id_} AS INT), DATE '{d}', TIMESTAMP '{ts}', CAST({n} AS INT), "
        f"CAST({f} AS DOUBLE), {sql_string_literal(s, databricks)}, "
        f"{'TRUE' if b else 'FALSE'})"
        for id_, d, ts, n, f, s, b in ROWS
    )
    return f"WITH t AS (SELECT * FROM VALUES {rows} AS t(id, d, ts, n, f, s, b)) "


@pytest.fixture(scope="module")
def databricks() -> Iterator[Any]:
    host = os.environ.get("DCT_TEST_DATABRICKS_HOST")
    if not host:
        pytest.skip("DCT_TEST_DATABRICKS_HOST not set")
    sql = pytest.importorskip("databricks.sql")
    con = sql.connect(
        server_hostname=host,
        http_path=os.environ["DCT_TEST_DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DCT_TEST_DATABRICKS_TOKEN"],
    )
    yield con.cursor()
    con.close()


class TestDatabricks:
    @pytest.mark.parametrize(("case", "column"), _case_params())
    def test_rows(self, databricks: Any, case: Case, column: str) -> None:
        databricks.execute(
            _databricks_fixture_cte()
            + _render_inlined(case, column, get_dialect("databricks"))
        )
        assert {r[0] for r in databricks.fetchall()} == case.expect
