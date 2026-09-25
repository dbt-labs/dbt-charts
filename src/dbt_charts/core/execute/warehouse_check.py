"""Per-adapter warehouse check — validity and column schema without full execution.

Every mechanism runs the query through ``AdapterRegistry.execute`` so it sees
exactly what execution would: dbt refs resolved, ``setup_sql`` on the same
connection, board variables merged and coerced. Checking a hand-rebuilt copy of
the SQL instead would report failures the real render never produces.

| Adapter          | Mechanism     | Result             |
|------------------|---------------|--------------------|
| duckdb           | ``DESCRIBE``  | validity + columns |
| clickhouse       | ``DESCRIBE``  | validity + columns |
| csv/json/parquet | ``DESCRIBE``  | columns only        |
| bigquery         | dry run       | validity + columns |
| postgres         | ``EXPLAIN``   | validity only      |
| redshift         | ``EXPLAIN``   | validity only      |
| snowflake        | ``EXPLAIN``   | validity only      |
| anything else    | none          | ``unchecked``      |

A file source (csv/json/parquet) resolves to the same ``DESCRIBE`` mechanism
as duckdb — it executes by materializing onto an in-process DuckDB either
way — but it is the one row in this table where DESCRIBE is not free: it must
read and parse the files first. Still far cheaper than executing (DESCRIBE
returns column metadata, not the result set), and it warms the cache for the
rest of the current process — not for a later, separate process, since the
cache is in-memory and dies with it. Its row also can't report ``invalid``:
materialization failures reach this module as a bare ``RuntimeError`` with no
``error_code`` (``execute_file_source_sql`` flattens whatever DuckDB raised),
so ``_is_query_defect`` can't classify them and they fall back to
``unchecked``.

The last row is the point: an adapter with no primitive that runs without
executing reports ``unchecked``, never ``valid``. "Unchecked" is not a flavor
of valid — nothing looked at that SQL, so the caller must say so. Databricks is
held in that row deliberately even though it has an ``EXPLAIN``: Spark returns
planner errors as plan *text* instead of failing the statement, so an EXPLAIN
branch there would report a broken query valid.

``EXPLAIN`` proves the query parses, binds and plans but returns a plan, not a
result schema — so those rows are ``columns_checked=False`` and the caller
reports the chart-column check as unavailable rather than pretending it ran.

A first EXPLAIN tier was cut from PR #7266: its guard inferred fault from
warehouse errors and kept producing false-invalids. The tier exists again only
because the guard was rebuilt the other way round — see the next paragraph.

``DESCRIBE`` and ``EXPLAIN`` are keyword prefixes on SQL this module did not
write, so before one is built the authored statement is parsed and checked
against what a check keyword can legally lead (:func:`_unwrappable_reason`). A
statement it cannot lead is reported ``unchecked`` with nothing sent. That gate
is what makes the classification honest in the other direction: every rejection
of a statement that *was* sent is a rejection of the author's SQL, so no error
has to be read for whose fault it was.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, NamedTuple

import sqlglot
import sqlglot.errors
import sqlglot.expressions as exp
from pydantic import BaseModel, ConfigDict

from dbt_charts.core.compile.models.query.normalized import SqlQuery, is_sql_query
from dbt_charts.core.compile.models.source import is_file_source
from dbt_charts.core.compile.sql_guard import (
    as_expressions,
    build_skeleton,
    sqlglot_dialect,
)
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_BINDER_TYPE_MISMATCH,
    ERR_BINDER_UNKNOWN_COLUMN,
    ERR_DBT_CALL_UNSUPPORTED,
    ERR_DBT_REF_UNKNOWN_NODE,
    ERR_DBT_SOURCE_UNKNOWN_TABLE,
    ERR_MUTATING_SQL,
    ERR_UNPARSEABLE_SQL,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import UnparseableSqlError
from dbt_charts.core.execute.source_resolver import AD_HOC_QUERY_NAME

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
    from dbt_charts.core.compile.models.query.normalized import AnyQuery
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.diagnostics.registry import ErrorCode
    from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry

CheckStatus = Literal["valid", "invalid", "unchecked"]

_BIGQUERY_DRY_RUN = "bigquery-dry-run"


class _PrefixCheck(NamedTuple):
    """One adapter's prefix-check mechanism: the keyword and what its rows are."""

    keyword: str
    # The result columns a row carries the column's name and type under, or
    # None when the rows are a plan and no schema can be read from them.
    # DuckDB spells them ``column_name``/``column_type``, ClickHouse ``name``
    # /``type``. One field so neither invalid pairing is constructable.
    columns: tuple[str, str] | None


# Adapter type → the keyword whose prefix checks a query without executing it,
# paired with whether its result rows are the column schema (DESCRIBE) or a
# plan read by nobody (EXPLAIN — validity-only). Absent means unchecked:
# notably databricks, whose EXPLAIN embeds planner errors in the plan text
# instead of failing (see the module docstring and the dispatch below).
_PREFIX_CHECKS: dict[str, _PrefixCheck] = {
    "duckdb": _PrefixCheck("DESCRIBE", ("column_name", "column_type")),
    # A bare DESCRIBE prefix leads a SELECT in ClickHouse too, and it binds the
    # query (an unknown column is a rejection, not a plan note), so the row is
    # the result schema exactly as on DuckDB.
    "clickhouse": _PrefixCheck("DESCRIBE", ("name", "type")),
    "postgres": _PrefixCheck("EXPLAIN", None),
    "redshift": _PrefixCheck("EXPLAIN", None),
    "snowflake": _PrefixCheck("EXPLAIN", None),
}


class WarehouseCheckColumn(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    type: str


class WarehouseCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CheckStatus
    adapter_type: str
    mechanism: str
    columns_checked: bool
    columns: list[WarehouseCheckColumn] = []
    error: str = ""
    # Why the check came back incomplete — nothing looked at the SQL, or
    # something did but read no columns. Phrased to be read by an author: it is
    # the whole content of the warning they get, and "unchecked" without a
    # reason is not actionable.
    reason: str = ""


def warehouse_check(
    query: SqlQuery,
    *,
    board: Board,
    adapter_registry: AdapterRegistry,
    query_name: str,
    query_registry: dict[str, AnyQuery],
) -> WarehouseCheck:
    """Check one compiled query against its warehouse without executing it.

    Args:
        query_registry: Every compiled query of the board, so a ``{{ queries.X }}``
            chain resolves the same way the executor resolves it.

    Raises:
        DbtChartsError: the query's source cannot be resolved.
    """
    from dbt_charts.core.execute.executor import (
        CACHE_REF_JINJA_RE,
        merge_board_variables,
        resolve_query_references,
    )

    # {{ queries.X.cache }} composes another query's *cached result*, which the
    # Executor materializes at render time and no warehouse has ever heard of.
    # Sending this SQL anywhere would report a failure about a table the author
    # never wrote.
    if CACHE_REF_JINJA_RE.search(query.sql):
        return WarehouseCheck(
            status="unchecked",
            adapter_type="none",
            mechanism="cache-ref",
            columns_checked=False,
            reason=(
                "it composes another query's cached result, which exists only "
                "at render time"
            ),
        )

    source_config = adapter_registry.resolve_query_source(
        query, board=board, query_name=query_name
    )
    variables = merge_board_variables(board, {})

    # Multi-hop {{ queries.X }} composition and the setup_sql of everything in
    # the chain, resolved exactly as the executor resolves it. AdapterRegistry's
    # own composition is a single non-recursive pass, so a two-hop chain would
    # otherwise reach the warehouse with `{{ queries.X }}` still in the SQL.
    resolved = resolve_query_references(
        query,
        all_queries={**board.queries, **query_registry},
        query_name=query_name,
    )
    # Resolution only ever rewrites `sql`/`setup_sql` on a copy of what it got.
    assert is_sql_query(resolved)
    query = resolved

    # None usually means a source-less *non*-SQL query (source-less SQL raises
    # ERR-NO-DEFAULT-SOURCE) — but a SqlQuery can land here too: an authored
    # `source:` that names no board/project source falls through to
    # DefaultSourceResolver's dbt fallback whenever a dbt project is in scope,
    # with no ResolvedSourceConfig to read a type off. Must run against the
    # *composed* `query` above, never the authored one: Executor.execute_query
    # composes `{{ queries.X }}` before ever calling AdapterRegistry.execute,
    # so a ref() reachable only through a composed `{{ queries.X }}` still has
    # to route to DbtAdapter.
    if source_config is None:
        fallback_type, fallback_detail = _resolve_dbt_fallback_type(
            query, adapter_registry
        )
        if fallback_type is None:
            return WarehouseCheck(
                status="unchecked",
                adapter_type="none",
                mechanism="no-source",
                columns_checked=False,
                reason=(
                    fallback_detail  # type-state: silent_fallback — empty string means no adapter claimed it, not a suppressed error
                    or (
                        "its source names no board or project source, and dbt "
                        "charts could not resolve a warehouse type for it "
                        "ahead of execution"
                    )
                ),
            )
        adapter_type = fallback_type
    else:
        adapter_type = source_config.type

    if adapter_type == "bigquery":
        if source_config is None:
            # The dbt-fallback branch above only learns the target's `type`
            # from profiles.yml — a BigQuery dry run needs the full resolved
            # config (project, keyfile, …), which nothing built here.
            return WarehouseCheck(
                status="unchecked",
                adapter_type="bigquery",
                mechanism=_BIGQUERY_DRY_RUN,
                columns_checked=False,
                reason=(
                    "its source falls through to dbt's own profile "
                    "resolution, which this tier cannot dry-run without a "
                    "resolved BigQuery source config"
                ),
            )
        return _check_bigquery(
            query,
            board=board,
            adapter_registry=adapter_registry,
            variables=variables,
            source_config=source_config,
        )
    prefix_check = _PREFIX_CHECKS.get(adapter_type)
    parse_dialect = adapter_type
    if (
        prefix_check is None
        and source_config is not None
        and is_file_source(source_config)
    ):
        # A csv/json/parquet source executes by materializing its files onto
        # an in-process DuckDB (file_source_materializer.py), so DuckDB's own
        # DESCRIBE mechanism answers it too — reusing the duckdb entry rather
        # than adding three near-duplicate rows to _PREFIX_CHECKS. Cost of
        # this path: see the module docstring.
        prefix_check = _PREFIX_CHECKS["duckdb"]
        # "csv"/"json"/"parquet" aren't sqlglot dialects — the SQL is parsed
        # as DuckDB's own, since DuckDB is what actually runs it.
        parse_dialect = "duckdb"
    if prefix_check is not None:
        keyword = prefix_check.keyword
        # A bare keyword prefix rather than a parenthesized wrapper, so a
        # pasted trailing `;`, comment, or `;;` rides along harmlessly —
        # unlike `DESCRIBE (\n{sql}\n)`, which needed a semicolon-strip helper
        # that broke again on each new trailing shape (see the git history of
        # this line for the round-2 regressions). What a bare prefix cannot do
        # is lead every statement, which is what the gate below rules on — and
        # for EXPLAIN the gate's leading-parenthesized-arm refusal also covers
        # Postgres parsing `EXPLAIN (SELECT …)` as EXPLAIN's options list.
        unwrappable = _unwrappable_reason(
            query.sql, dialect=parse_dialect, keyword=keyword
        )
        if unwrappable is not None:
            return WarehouseCheck(
                status="unchecked",
                adapter_type=adapter_type,
                mechanism=keyword,
                columns_checked=False,
                reason=unwrappable,
            )
        return _check_via_wrap(
            query,
            f"{keyword} {query.sql}",
            adapter_type=adapter_type,
            check=prefix_check,
            board=board,
            adapter_registry=adapter_registry,
            variables=variables,
        )
    # The remaining adapters are unchecked because dbt charts implements no
    # non-executing check for them — a claim about us, not the warehouse
    # (mysql and trino have EXPLAIN too, just no branch here yet). Databricks
    # is the exception with a reason of its own: adding it to the table above
    # would silently shadow this refusal, and the refusal is the point.
    if adapter_type == "databricks":
        reason = (
            "databricks EXPLAIN returns planner errors as plan text "
            "instead of failing the statement, so a broken query would "
            "read as valid — dbt charts implements no check for it"
        )
    else:
        reason = f"dbt charts implements no non-executing check for {adapter_type}"
    return WarehouseCheck(
        status="unchecked",
        adapter_type=adapter_type,
        mechanism="no-validity-primitive",
        columns_checked=False,
        reason=reason,
    )


def _resolve_dbt_fallback_type(
    query: SqlQuery, adapter_registry: AdapterRegistry
) -> tuple[str | None, str]:
    """The warehouse type this query's *composed* SQL routes to, read off disk.

    Caller must pass the post-composition query — see the call site.

    Returns ``(None, "")`` when no adapter would actually claim the query, and
    ``(None, <detail>)`` when a claiming ``DbtAdapter``'s profile could not be
    resolved — the failure detail carries into the caller's ``reason``.
    """
    from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter
    from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

    adapter = adapter_registry.get_adapter(query, None)
    if isinstance(adapter, DuckDBAdapter):
        return "duckdb", ""
    if not isinstance(adapter, DbtAdapter):
        return None, ""
    try:
        return adapter.resolve_target_type(), ""
    except Exception as exc:  # noqa: BLE001 — any profile fault means unresolvable, not a crash
        return None, str(exc)


def check_ad_hoc_query(
    sql: str,
    *,
    source: str | None,
    adapter_registry: AdapterRegistry,
) -> WarehouseCheck:
    """Warehouse-check a standalone SQL string that was never authored on a board.

    ``warehouse_check`` composes ``{{ queries.X }}`` refs and board variables
    against a board's own registry — an ad hoc query (e.g. ``describe_query``'s
    column-schema lookup) has neither, so this synthesizes a minimal empty
    board for it to resolve against and delegates to the same per-adapter
    dispatch. Callers checking a query that *is* part of a board should call
    ``warehouse_check`` directly with the real board instead of this.
    """
    from dbt_charts.core.compile.models.board.normalized import (
        Board,
        Layout,
        LayoutType,
    )
    from dbt_charts.core.compile.normalize.dispatch import compile_board_resolved_style

    resolved_style, chart_style_context, _ = compile_board_resolved_style(
        None, None, None
    )
    board = Board(
        id=AD_HOC_QUERY_NAME,
        layout=Layout(type=LayoutType.ROWS, items=[]),
        resolved_style=resolved_style,
        chart_style_context=chart_style_context,
        level=0,
    )
    return warehouse_check(
        SqlQuery(sql=sql, source=source),
        board=board,
        adapter_registry=adapter_registry,
        query_name=AD_HOC_QUERY_NAME,
        query_registry={},
    )


# The statement types a ``DESCRIBE`` prefix can lead. Everything else —
# an authored DESCRIBE/SHOW/EXPLAIN, DDL, an opaque Command — turns into a
# doubled or nonsensical statement under the prefix, which says nothing about
# the query the author wrote.
_WRAPPABLE_STATEMENTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


def _statement_label(stmt: exp.Expression) -> str:
    """The keyword an author would recognize this parsed statement by.

    A ``Command`` is sqlglot's opaque node for a statement it does not model
    (an authored ``DESCRIBE`` or ``EXPLAIN``), and it holds the leading
    keyword — which is exactly the word the author typed and the one this
    tier has to name.
    """
    if isinstance(stmt, exp.Command):
        return stmt.name.upper()
    return type(stmt).__name__.upper()


def _unwrappable_reason(sql: str, *, dialect: str, keyword: str) -> str | None:
    """Why prefixing this statement with *keyword* would not check it, or None.

    Decided from the author's own parsed statement, before anything is built or
    sent. Deciding it here rather than reading it back out of a warehouse error
    is the whole design: an error code carries what went wrong, never whose
    fault it was, and three rounds of inferring the latter from the former each
    mis-sorted a different quadrant.

    A statement that parses clean and is wrappable stays wrappable once the
    keyword is prepended — ``DESCRIBE`` or ``EXPLAIN`` of a single
    select-family statement parses for the guard and for the engine alike — so
    every rejection of what does get sent is a rejection of the author's SQL.
    """
    try:
        # Primary branch only. An `{% if %}` in *expression* position is a
        # statement node in jinja's AST, so the mutation guard's all-branches
        # walk joins its branches with `;` and the skeleton fails to parse for
        # SQL that renders fine (sql_guard's `_branch_join_is_the_artifact`
        # documents the same shape). Shape is all this needs, so it takes the
        # branch that parses.
        parsed = sqlglot.parse(
            build_skeleton(sql, all_branches=False), read=sqlglot_dialect(dialect)
        )
    except (UnparseableSqlError, sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        # SQL with no determinable shape has no *collision* to rule out either,
        # and the wrapped form fails the execute path's own guard on the same
        # SQL — which is the author's defect, and is reported as one.
        return None

    # as_expressions raises the same UnparseableSqlError sql_guard._parse does
    # on a non-Expression node (an invariant of sqlglot's own class
    # hierarchy, not something this SQL can trigger) — stamped, so it also
    # passes core/execute's raise ratchet against bare RuntimeError.
    statements = [s for s in as_expressions(parsed) if not isinstance(s, exp.Semicolon)]

    if len(statements) != 1:
        return (
            f"this tier checks a query by prefixing it with {keyword}, which "
            "reaches only the first statement of a multi-statement script"
        )
    stmt = statements[0]
    if not isinstance(stmt, _WRAPPABLE_STATEMENTS):
        return (
            f"this tier checks a query by prefixing it with {keyword}, and a "
            f"{_statement_label(stmt)} statement cannot be led by it"
        )
    # A leading parenthesized arm — `(SELECT …) UNION ALL (SELECT …)` — binds
    # to the prefix as its whole target, leaving the remaining arms dangling.
    leftmost: exp.Expression = stmt
    while isinstance(leftmost, (exp.Union, exp.Intersect, exp.Except)):
        leftmost = leftmost.this
    if isinstance(leftmost, exp.Subquery):
        return (
            f"this tier checks a query by prefixing it with {keyword}, which "
            "would bind to this query's own leading parenthesized statement "
            "rather than to the whole compound"
        )
    return None


def _check_via_wrap(
    query: SqlQuery,
    wrapped_sql: str,
    *,
    adapter_type: str,
    check: _PrefixCheck,
    board: Board,
    adapter_registry: AdapterRegistry,
    variables: VariableValues,
) -> WarehouseCheck:
    """Run a keyword-wrapped statement through the real execute path.

    The wrap replaces ``sql``; source, ``setup_sql`` and lenient_variables ride
    along, so the adapter resolves refs and variables exactly as it would for
    the real run. ``limit`` is dropped: it bounds the *rows* of the authored
    query, and the wrapped statement's rows are a schema or a plan, never the
    authored result.

    ``check.columns`` says what those rows are: DESCRIBE's are the result
    column schema and are read into ``columns``; EXPLAIN's are a plan nobody
    reads, so the check is validity-only, ``columns_checked`` stays False, and
    ``reason`` carries why — it is the whole detail a caller that wanted
    columns (``describe_query``'s refusal) has to show.

    Callers must have cleared :func:`_unwrappable_reason` first — that is what
    entitles this to read a failure of the wrapped statement as a verdict on
    the authored one.
    """
    result = adapter_registry.execute(
        query.model_copy(update={"sql": wrapped_sql, "limit": None}),
        variables=variables,
        board=board,
    )
    if result.error:
        return _failure(
            str(result.error),
            rejected=_is_query_defect(result.error.code),
            adapter_type=adapter_type,
            mechanism=check.keyword,
        )
    if check.columns is None:
        return WarehouseCheck(
            status="valid",
            adapter_type=adapter_type,
            mechanism=check.keyword,
            columns_checked=False,
            reason=(
                f"{check.keyword} validated the query but returns no result schema"
            ),
        )
    name_column, type_column = check.columns
    return WarehouseCheck(
        status="valid",
        adapter_type=adapter_type,
        mechanism=check.keyword,
        columns_checked=True,
        columns=[
            WarehouseCheckColumn(name=row[name_column], type=row[type_column])
            for row in result.data
        ],
    )


def _failure(
    error: str,
    *,
    rejected: bool,
    adapter_type: str,
    mechanism: str,
) -> WarehouseCheck:
    """Build the verdict for a failed check.

    ``rejected`` is the whole decision: True means a warehouse looked at this
    SQL and refused it, False means nothing ever ruled on it. Calling the second
    case "your query is invalid" asserts a cause that is false — one typo'd
    hostname would condemn every query in the project.
    """
    return WarehouseCheck(
        status="invalid" if rejected else "unchecked",
        adapter_type=adapter_type,
        mechanism=mechanism,
        columns_checked=False,
        error=error,
        reason="" if rejected else f"the warehouse gave no verdict on it ({error})",
    )


# The codes that mean a warehouse read this SQL and refused it. Held as the
# registered objects, not string literals: a literal that drifts (or never
# existed) is a silent no-match, and here that means a broken query passes.
#
# A connect-time or credentials fault (which reaches ``handle_adapter_error``
# / ``connection_failure`` before a warehouse has read any SQL), a duration
# cap, or a source that would not resolve says nothing about the SQL, so
# those report "unchecked". The test is the code and only the code: warehouse
# errors quote the offending identifier, so scanning the message for words
# like "credentials" or "timed out" reads a column name as a connection
# failure and passes a broken query.
#
# ERR_WAREHOUSE_RUNTIME is a deliberate exception to "the code means a
# defect": ``_classify_duckdb_error`` has no finer code for a raw
# duckdb.ParserException (what a malformed ORDER BY or an empty ``IN ()``
# raises), and on the dbt-adapter path it is the catch-all for every mid-query
# fault *and* the only code a genuine binding rejection carries — so excluding
# it would report real defects as "unchecked". The cost, wider since the
# EXPLAIN tier: an *unrecognized mid-flight* fault — a dropped connection while
# DESCRIBE runs, a Snowflake warehouse suspended or a grant revoked mid-EXPLAIN,
# a quota refusal — also falls through to this code and is reported "invalid"
# rather than "unchecked", now across three managed warehouses rather than a
# local DuckDB file. Pinned as a decision, not an accident.
_QUERY_DEFECT_CODES = frozenset(
    {
        ERR_BINDER_TYPE_MISMATCH.code,
        ERR_BINDER_UNKNOWN_COLUMN.code,
        ERR_DBT_CALL_UNSUPPORTED.code,
        ERR_DBT_REF_UNKNOWN_NODE.code,
        ERR_DBT_SOURCE_UNKNOWN_TABLE.code,
        ERR_MUTATING_SQL.code,
        ERR_UNPARSEABLE_SQL.code,
        ERR_WAREHOUSE_RUNTIME.code,
    }
)


def _is_query_defect(error_code: ErrorCode | None) -> bool:
    return error_code is not None and error_code.code in _QUERY_DEFECT_CODES


# BigQuery's dry run gets no session to hold setup_sql across two statements
# (see _check_bigquery below), so the two are joined into one script with an
# explicit `;\n` separator. If setup_sql itself already ends with a `;` — or a
# comment that would swallow the appended `;` — the join produces `;;` or eats
# the separator; this is the only remaining place in the module that needs a
# semicolon strip, since DESCRIBE wraps in nothing a trailing artifact
# could land inside of. Repeated (`+`) because a pasted `;;`
# is as common as a single one and leaving the second one produces the same
# rejected script.
_TRAILING_SEMICOLON_RE = re.compile(r"(?:;[ \t]*(?:--[^\n]*)?\s*)+\Z")


def _strip_trailing_semicolon_for_script_join(sql: str) -> str:
    return _TRAILING_SEMICOLON_RE.sub("", sql.rstrip())


def _check_bigquery(
    query: SqlQuery,
    *,
    board: Board,
    adapter_registry: AdapterRegistry,
    variables: VariableValues,
    source_config: ResolvedSourceConfig,
) -> WarehouseCheck:
    """Dry-run through the native BigQuery client — validity + schema, unbilled.

    BigQuery has no SQL-level check (no EXPLAIN, and an outer ``WHERE FALSE``
    still scans), so this is the one mechanism that cannot ride the execute
    path. It takes its SQL from ``prepare_sql`` — the same preparation execute
    runs — rather than re-rendering, so refs, variables, and cross-query
    composition match the real run exactly.

    ``source_config`` is typed as the whole resolved union, not
    ``BigQuerySourceConfig``: a source reachable only through a dbt profile
    resolves to ``DbtTargetSourceConfig`` (``extra="allow"``, dbt owns the
    schema), which carries no BigQuery attributes to access directly — hence
    ``model_dump()`` below rather than attribute access, mirroring
    ``bigquery_default_dataset``'s own dict-based read.
    """
    from dbt_charts.core.connections import build_bigquery_client, import_bigquery
    from dbt_charts.core.execute.adapters.base import (
        BIGQUERY_UNKNOWN_REF_SUBSTRINGS,
        QueryResult,
    )
    from dbt_charts.core.execute.adapters.sql_adapter import bigquery_default_dataset

    # Through the same helper the rest of the BigQuery surface uses, so a
    # missing extra names itself instead of surfacing a bare ModuleNotFoundError.
    QueryJobConfig = import_bigquery("google.cloud.bigquery").QueryJobConfig
    BadRequest = import_bigquery("google.api_core.exceptions").BadRequest

    prepared = adapter_registry.prepare_sql(
        query, board=board, variables=variables, source_config=source_config
    )
    if isinstance(prepared, QueryResult):
        if prepared.error:
            return _failure(
                str(prepared.error),
                rejected=_is_query_defect(prepared.error.code),
                adapter_type="bigquery",
                mechanism=_BIGQUERY_DRY_RUN,
            )
        # A result with neither wire SQL nor an error leaves nothing to send.
        return WarehouseCheck(
            status="unchecked",
            adapter_type="bigquery",
            mechanism=_BIGQUERY_DRY_RUN,
            columns_checked=False,
            reason="the query could not be prepared for a dry run",
        )

    # A dry run gets no session to hold `setup_sql` across two statements, but
    # it does validate a whole multi-statement script in one call — so send both
    # together rather than dropping the definitions the query calls into.
    wire_sql = (
        f"{_strip_trailing_semicolon_for_script_join(prepared.setup_sql)};\n{prepared.sql}"
        if prepared.setup_sql
        else prepared.sql
    )
    config = source_config.model_dump()
    # Read the same key `bigquery_default_dataset` reads, and read it outside
    # the SDK boundary below. A target reached through a dbt profile can
    # legitimately carry no project — `method: oauth` with a dataset alone is a
    # valid credential set, and dbt takes the project from ADC at connect time.
    # Execution gets that resolution for free; a dry run has to name the
    # project it is sent to, so an absent one is reported as the missing
    # setting it is rather than as a KeyError swallowed into a bare "'project'".
    project = config.get("project")
    if not project:
        return WarehouseCheck(
            status="unchecked",
            adapter_type="bigquery",
            mechanism=_BIGQUERY_DRY_RUN,
            columns_checked=False,
            reason=(
                "its BigQuery source names no `project`, and a dry run has to "
                "be sent to one"
            ),
        )
    try:
        # Construction is inside the try along with the query itself: with no
        # keyfile/keyfile_json (the documented default), the client calls
        # google.auth.default() and raises DefaultCredentialsError on a machine
        # without ADC — a credentials fault exactly like one raised mid-query,
        # and one that says nothing about this SQL either.
        client = build_bigquery_client(
            project=project,
            keyfile_json=config.get("keyfile_json"),
            keyfile=config.get("keyfile"),
        )
        query_job = client.query(
            wire_sql,
            job_config=QueryJobConfig(
                dry_run=True,
                use_query_cache=False,
                # Execution binds this to the connection, so without it a bare
                # `FROM orders` — the normal spelling once the source names the
                # dataset — dry-runs as "missing dataset" on a healthy board.
                default_dataset=bigquery_default_dataset(config),
            ),
            # A dataset outside the default region is invisible without it.
            location=config.get("location"),
        )
    except Exception as exc:  # noqa: BLE001 — BigQuery SDK boundary
        # BadRequest is the SDK's "this SQL is wrong". An unresolvable relation
        # arrives as NotFound instead — a sibling of BadRequest, not a subclass
        # — so it is matched on the same substrings the execute-path classifier
        # uses (`base.BIGQUERY_UNKNOWN_REF_SUBSTRINGS`), and a dropped table
        # gets one verdict whether it is executed or checked. Everything else
        # (Forbidden, transport, credentials — including a client that never
        # constructed) never ruled on the query.
        message = str(exc)
        return _failure(
            message,
            rejected=isinstance(exc, BadRequest)
            or any(sub in message for sub in BIGQUERY_UNKNOWN_REF_SUBSTRINGS),
            adapter_type="bigquery",
            mechanism=_BIGQUERY_DRY_RUN,
        )
    schema = query_job.schema
    if schema is None:
        # BigQuery reports no schema for a script — validity, without columns.
        return WarehouseCheck(
            status="valid",
            adapter_type="bigquery",
            mechanism=_BIGQUERY_DRY_RUN,
            columns_checked=False,
            reason="BigQuery reports no result schema for a multi-statement dry run",
        )
    return WarehouseCheck(
        status="valid",
        adapter_type="bigquery",
        mechanism=_BIGQUERY_DRY_RUN,
        columns_checked=True,
        # Lowercased to match `SqlAdapter`, which lowercases every result column
        # before a chart ever sees it — so lowercase is the only spelling that
        # renders, and reporting the dry run's verbatim casing would reject the
        # working board and pass the broken one.
        columns=[
            WarehouseCheckColumn(name=field.name.lower(), type=str(field.field_type))
            for field in schema
        ],
    )
