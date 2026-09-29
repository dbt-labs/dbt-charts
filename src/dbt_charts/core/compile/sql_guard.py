"""SQL statement-type guard for dbt_charts.core.

Two public validators (plus `build_skeleton`, the Jinja skeletonizer they
share with `core/inspect/relations.py`):
  validate_select_only(sql, dialect=None)  — read-only queries only
  validate_setup_sql(sql, dialect=None)    — TEMP CREATE + DuckDB MACRO only

Both raise MutatingSqlError on policy violation, UnparseableSqlError when
the SQL skeleton cannot be statically determined (Jinja shapes we don't model,
or sqlglot parse failure). Callers decide what to do with each error type.

The approach:
1. Build a SQL skeleton from the Jinja AST: TemplateData verbatim, {{ expr }}
   as placeholder identifiers, {% if %}/{% for %}/{% set %} walked structurally
   so both branches of if are included (catching malicious-branch injection).
   Unknown node types raise UnparseableSqlError (default-deny).
2. Strip a leading EXPLAIN / EXPLAIN ANALYZE keyword — sqlglot opaquely wraps
   `EXPLAIN <anything>` as a Command node, so the inner statement is invisible
   to AST allowlist checks. Strip-and-revalidate forces the inner body through
   the same gate, so `EXPLAIN DROP TABLE x` fails like the bare DROP would.
3. sqlglot.parse(skeleton, read=dialect) to get top-level statements.
4. Walk statements against the function's allowlist, then scan descendants for
   mutating-statement types so CTE-laundered DML (`WITH x AS (DELETE …) SELECT
   * FROM x`) is rejected.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

import jinja2
import jinja2.nodes
import sqlglot
import sqlglot.errors
import sqlglot.expressions as exp

from dbt_charts.core.compile.config import get_execution_config
from dbt_charts.core.diagnostics.execution import (
    MutatingSqlError,
    SqlErrorPosition,
    UnparseableSqlError,
)

# The identifier prefix build_skeleton splices in place of `{{ expr }}`.
# Consumers detecting templated identifiers must match on this constant — a
# hand-copied spelling silently disables their guards when this changes.
SKELETON_PLACEHOLDER_PREFIX = "__dct_j"


def sqlglot_dialect(dialect: str | None) -> str | None:
    """Translate a dbt charts dialect name to the sqlglot equivalent.

    sqlglot knows 'tsql' (not 'sqlserver'/'mssql') and 'mysql' (not 'mariadb').
    Returns None unchanged (sqlglot default dialect).
    """
    if dialect is None:
        return None
    aliases = get_execution_config().dialect_aliases
    return aliases.get(dialect, dialect)


# Top-level statements allowed by validate_select_only. exp.Select is further
# narrowed by an INTO-anywhere descendant scan below.
_ALLOWED_QUERY_NODES: frozenset[type[exp.Expression]] = frozenset(
    {
        exp.Select,
        exp.Subquery,
        exp.Union,
        exp.Intersect,
        exp.Except,
        exp.Describe,
        exp.Show,
    }
)

# Mutating statement types that must not appear anywhere in a SELECT-family
# tree — including inside CTEs, subqueries, and UNION arms. The CTE-laundered
# bypass shape `WITH x AS (DELETE … RETURNING *) SELECT * FROM x` parses as a
# top-level Select with the DML buried as a descendant; descendant-scan closes
# the gap. Top-level type-allowlist would otherwise admit the outer Select.
_DISALLOWED_DESCENDANT_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Create,
    exp.Pragma,
    exp.Grant,
    exp.Attach,
    exp.Detach,
    # sqlglot has no exp.Revoke as of 26.x; REVOKE parses as a Command at the
    # top level and is caught there. If a future sqlglot adds Revoke as a
    # dedicated node, add it here so a buried REVOKE inside a CTE is caught.
)

_ALLOWED_SETUP_NODES: frozenset[type[exp.Expression]] = frozenset({exp.Create})


# sqlglot models `EXPLAIN <stmt>` as an opaque Command — the inner statement
# is invisible to the AST. Strip the EXPLAIN keyword (and common modifiers)
# from the skeleton so the body re-parses through the normal allowlist and
# `EXPLAIN DROP TABLE x` fails like the bare DROP would. Only the leading
# EXPLAIN is stripped — a mid-stream `…; EXPLAIN …` parses as Command and
# is rejected by the allowlist (acceptable fail-closed for an uncommon shape).
_EXPLAIN_PREFIX_RE = re.compile(
    r"^\s*EXPLAIN"
    r"(?:\s+(?:ANALYZE|VERBOSE|QUERY\s+PLAN))*"
    r"(?:\s*\([^)]*\))?"
    r"\s+",
    re.IGNORECASE,
)


def _strip_explain_prefix(skeleton: str) -> str:
    return _EXPLAIN_PREFIX_RE.sub("", skeleton, count=1)


# Databricks Delta Lake adds DETAIL / HISTORY variants to DESCRIBE that
# sqlglot does not model — `DESCRIBE DETAIL my_table` fails to parse. Both
# variants are read-only metadata reads (mirrors of Delta-log inspection).
# Strip the variant keyword so the body re-parses as a standard exp.Describe.
_DESCRIBE_VARIANT_PREFIX_RE = re.compile(
    r"^\s*DESCRIBE\s+(?:DETAIL|HISTORY)\s+",
    re.IGNORECASE,
)


def _strip_describe_variant_prefix(skeleton: str) -> str:
    return _DESCRIBE_VARIANT_PREFIX_RE.sub("DESCRIBE ", skeleton, count=1)


# Read-only commands sqlglot falls back to `Command` for on dialects that
# lack a dedicated node (postgres / bigquery / redshift / databricks / default
# all parse `SHOW TABLES` as `Command(name="SHOW")`). Terminal commands —
# no inner statement to revalidate.
_ALLOWED_TOP_LEVEL_COMMAND_KEYWORDS: frozenset[str] = frozenset({"SHOW"})


def _is_allowed_top_level_command(stmt: exp.Expression) -> bool:
    return (
        isinstance(stmt, exp.Command)
        and (stmt.name or "").upper() in _ALLOWED_TOP_LEVEL_COMMAND_KEYWORDS
    )


def _walk_jinja(
    node: jinja2.nodes.Node,
    out: list[str],
    counter: list[int],
    all_branches: bool = True,
) -> None:
    """Walk a jinja2 AST, appending SQL fragments to *out*.

    counter is a one-element list used as a mutable int for placeholder naming.
    Raises UnparseableSqlError for any node type outside the explicit table —
    default-deny so AssignBlock / Macro / Include / extension nodes never
    silently swallow embedded SQL.

    ``all_branches`` is the mutation guard's posture: every branch of an
    ``{% if %}`` reaches the parser, joined by ``;`` so each reads as its own
    statement. Pass False for the primary branch alone — see
    ``_branch_join_is_the_only_problem``.
    """
    if isinstance(node, jinja2.nodes.Template):
        for child in node.body:
            _walk_jinja(child, out, counter, all_branches)

    elif isinstance(node, jinja2.nodes.TemplateData):
        out.append(node.data)

    elif isinstance(node, jinja2.nodes.Output):
        # Output interleaves TemplateData (literal SQL) and expression nodes
        # ({{ ref('orders') }}, {{ var }}, …). Literals pass through; each
        # expression becomes a placeholder identifier.
        for child in node.nodes:
            if isinstance(child, jinja2.nodes.TemplateData):
                out.append(child.data)
            else:
                out.append(f"{SKELETON_PLACEHOLDER_PREFIX}{counter[0]}__")
                counter[0] += 1

    elif isinstance(node, jinja2.nodes.If):
        # jinja2 represents elif_ as a flat list of If nodes (each with empty
        # elif_), not a linked-list chain. Iterate every element so DROPs
        # buried in the 2nd / 3rd elif slot are walked, not just the first.
        for child in node.body:
            _walk_jinja(child, out, counter, all_branches)
        if not all_branches:
            return
        for elif_node in node.elif_:
            out.append(" ; ")
            for child in elif_node.body:
                _walk_jinja(child, out, counter, all_branches)
        if node.else_:
            out.append(" ; ")
            for child in node.else_:
                _walk_jinja(child, out, counter, all_branches)

    elif isinstance(node, jinja2.nodes.For):
        # Walk body once and the `else_` clause if present — jinja2 runs the
        # else clause when the iterable is empty. Same branch-coverage posture
        # as If: SQL hiding in the else branch must reach the skeleton.
        for child in node.body:
            _walk_jinja(child, out, counter, all_branches)
        if node.else_ and all_branches:
            out.append(" ; ")
            for child in node.else_:
                _walk_jinja(child, out, counter, all_branches)

    elif isinstance(node, jinja2.nodes.Assign):
        # {% set x = ... %} — emits nothing.
        pass

    else:
        # AssignBlock ({% set sql %}...{% endset %}), Macro, Include, Import,
        # FromImport, Extends, Block, FilterBlock, CallBlock, future jinja2
        # additions — all default-deny. If AssignBlock fell through, the body's
        # SQL would never reach the parser.
        raise UnparseableSqlError(f"unsupported_jinja_node:{type(node).__name__}")


def build_skeleton(template_str: str, all_branches: bool = True) -> str:
    """Return a SQL skeleton by walking the jinja2 AST.

    TemplateData becomes literal SQL; `{{ expr }}` becomes a placeholder
    identifier; `{% if %}` recurses all branches. Unknown jinja nodes raise
    UnparseableSqlError so no SQL can hide inside them.
    """
    if "{{" not in template_str and "{%" not in template_str:
        return template_str

    env = jinja2.Environment()
    try:
        ast = env.parse(template_str)
    except jinja2.TemplateSyntaxError as exc:
        raise UnparseableSqlError(exc) from exc

    out: list[str] = []
    counter = [0]
    _walk_jinja(ast, out, counter, all_branches)
    return "".join(out)


def _sqlglot_error_position(
    exc: sqlglot.errors.ParseError, sql: str
) -> SqlErrorPosition | None:
    """SQL-local position for the first entry in exc.errors, or None.

    sqlglot's own `col` is the 1-based position of the LAST character of the
    offending token (`highlight`) — confirmed empirically against
    sqlglot.parser.Parser.raise_error, which builds `col`/`highlight` from the
    same failing token. Walking back by the token's length recovers its
    start; a missing/empty highlight still marks one character at `col`
    rather than reporting no position at all. Returns None when sqlglot
    supplies no line/col, or when the line it names isn't in `sql` (never
    fabricates one).

    `sql` is the exact string sqlglot parsed, so the captured `line_text` is
    the text those columns were measured against — see SqlErrorPosition.
    """
    if not exc.errors:
        return None
    err = exc.errors[0]
    line, col = err.get("line"), err.get("col")
    if line is None or col is None:
        return None
    sql_lines = sql.splitlines()
    if not 1 <= line <= len(sql_lines):
        return None
    highlight = err.get("highlight")
    token_len = len(highlight) if highlight else 1
    return SqlErrorPosition(
        line=line,
        start_col=col - token_len + 1,
        end_col=col + 1,
        line_text=sql_lines[line - 1],
    )


def _select_skeleton(sql: str, all_branches: bool = True) -> str:
    return _strip_describe_variant_prefix(
        _strip_explain_prefix(build_skeleton(sql, all_branches))
    )


def _branch_join_is_the_artifact(
    rebuild_single_branch: Callable[[], str], dialect: str | None
) -> bool:
    """True when the skeleton only failed to parse because branches were joined.

    An ``{% if %}`` used in *expression* position — ``HAVING x >= {% if a %}1{%
    else %}0{% endif %}`` — is a statement node in jinja's AST, so the walk
    joins its branches with ``;`` and the parser sees a dangling second
    statement. The SQL is fine; the skeleton is not. Rebuilding with the
    primary branch alone tells the two apart: if that parses, the failure is
    ours, and the caller must not report a position (and so must not warn).

    Deliberately not fixed by making the walk smarter — jinja gives no way to
    tell statement position from expression position, and narrowing the walk
    would blind the mutation guard to SQL hiding in an else branch. Detecting
    the artifact after the fact keeps that coverage intact.
    """
    try:
        sqlglot.parse(rebuild_single_branch(), read=sqlglot_dialect(dialect))
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        return False
    return True


def as_expression(node: exp.Expr) -> exp.Expression | None:
    """Narrow a sqlglot node to an ``Expression``, or ``None`` if it is not one.

    sqlglot's generic tree-walk methods (``walk``, ``find_all``, ...) and the
    trait bases some node types share (``Query``, ``DDL``, ``AggFunc``) are
    typed against ``Expr``, the abstract base. Every concrete node the parser
    can actually produce multiply-inherits ``Expression``, so this narrows
    back to what the rest of dbt_charts assumes.
    """
    return node if isinstance(node, exp.Expression) else None


def as_expressions(nodes: Iterable[exp.Expr | None]) -> list[exp.Expression]:
    """Narrow every node in *nodes* to ``Expression``, dropping bare ``None``\\s
    (sqlglot's own empty-statement/separator artifact).

    Every concrete statement sqlglot's parser can produce multiply-inherits
    ``Expression`` — an invariant of sqlglot's own class hierarchy, not a
    shape the caller's SQL can trigger. Raises (not ``assert``, so this
    default-deny guard survives ``python -O``) rather than silently dropping
    a node from a mutation-guard's allowlist scan.
    """
    result: list[exp.Expression] = []
    for node in nodes:
        if node is None:
            continue
        expr = as_expression(node)
        if expr is None:
            raise UnparseableSqlError(f"non_expression_node:{type(node).__name__}")
        result.append(expr)
    return result


def _parse(
    skeleton: str, dialect: str | None, rebuild_single_branch: Callable[[], str]
) -> list[exp.Expression]:
    try:
        parsed = sqlglot.parse(skeleton, read=sqlglot_dialect(dialect))
    except sqlglot.errors.TokenError as exc:
        # Sibling of ParseError, not a subclass, so it needs its own clause or it
        # escapes uncoded. It carries no `.errors`: the tokenizer failed before a
        # token existed, so there is nothing to locate and no position to report.
        raise UnparseableSqlError(exc, sql_position=None) from exc
    except sqlglot.errors.ParseError as exc:
        position = (
            None
            if _branch_join_is_the_artifact(rebuild_single_branch, dialect)
            else _sqlglot_error_position(exc, skeleton)
        )
        raise UnparseableSqlError(exc, sql_position=position) from exc
    return as_expressions(parsed)


def _reject_mutating_descendants(
    stmt: exp.Expression, allowlist_label: str = "query"
) -> None:
    """Scan stmt for mutating-statement descendants and raise on first hit.

    Catches CTE-laundered DML (`WITH x AS (DELETE …) SELECT *`), DML inside
    subqueries, DML inside UNION arms, and `SELECT … INTO` at any depth —
    all of which would otherwise pass a top-level type-only check. Used by
    both validate_select_only and validate_setup_sql; the latter passes
    allowlist_label="setup" so the error message names the right policy.

    sqlglot's find_all includes the calling node, so the outer Create on the
    setup path would re-match itself — skip self explicitly. Nested CREATEs
    inside `CREATE TEMP TABLE t AS (CREATE …)` still get rejected.
    """
    for desc in stmt.find_all(*_DISALLOWED_DESCENDANT_NODES):
        if desc is stmt:
            continue
        raise MutatingSqlError(
            rejected_node_kind=type(desc).__name__,
            fragment_preview=desc.sql()[:60],
            allowlist_label=allowlist_label,
        )
    for select in stmt.find_all(exp.Select):
        if select.args.get("into") is not None:
            raise MutatingSqlError(
                rejected_node_kind="Select(into)",
                fragment_preview=stmt.sql()[:60],
                allowlist_label=allowlist_label,
            )


def _is_bq_temp_function_create(stmt: exp.Expression) -> bool:
    """Return True iff stmt is a BigQuery-style CREATE TEMP FUNCTION.

    Used by validate_select_only to permit the BigQuery scripting pattern
    `CREATE TEMP FUNCTION ... ; SELECT ...`. Only FUNCTION kind is accepted;
    CREATE TEMP TABLE and CREATE TEMP VIEW belong in validate_setup_sql, not
    inline query scripts.
    """
    if not isinstance(stmt, exp.Create):
        return False
    kind = (stmt.args.get("kind") or "").upper()
    if kind != "FUNCTION":
        return False
    props = stmt.args.get("properties")
    return props is not None and any(
        isinstance(p, exp.TemporaryProperty) for p in props.expressions
    )


def validate_select_only(sql: str, dialect: str | None = None) -> None:
    """Raise MutatingSqlError on non-read-only SQL; UnparseableSqlError when undetermined.

    Accepts: SELECT (no INTO), WITH, UNION, INTERSECT, EXCEPT, DESCRIBE, SHOW,
    EXPLAIN of an allowed inner statement.
    On BigQuery only: also accepts a script of one or more CREATE TEMP FUNCTION
    statements followed by a final SELECT-family statement (inline scalar UDF
    pattern from Looker-migrated dashboards).
    Rejects: DROP, DELETE, INSERT, UPDATE, ALTER, GRANT, REVOKE, TRUNCATE,
             ATTACH, LOAD, CALL, PRAGMA, CREATE, and any other mutating
             statement — including DML hidden inside a CTE or subquery.

    Args:
        sql: Raw SQL string, possibly containing Jinja expressions.
        dialect: sqlglot dialect name (e.g. "duckdb", "postgres"). None = default.
    """
    skeleton = _select_skeleton(sql)
    statements = [
        s
        for s in _parse(skeleton, dialect, lambda: _select_skeleton(sql, False))
        if not isinstance(s, exp.Semicolon)
    ]

    # BigQuery inline-UDF scripting pattern: one or more CREATE TEMP FUNCTION
    # statements followed by a SELECT-family statement. This is a read-only
    # pattern — the TEMP FUNCTIONs are ephemeral UDFs, not persistent DDL.
    # Only allowed on the bigquery dialect; other dialects keep the full ban.
    if dialect == "bigquery" and statements:
        leading_temp_fns = [
            s for s in statements[:-1] if _is_bq_temp_function_create(s)
        ]
        if leading_temp_fns and len(leading_temp_fns) == len(statements) - 1:
            final = statements[-1]
            # Validate the TEMP FUNCTION creates for buried DML.
            for fn_stmt in leading_temp_fns:
                _reject_mutating_descendants(fn_stmt)
            # Validate the trailing statement as a normal query.
            if type(final) in _ALLOWED_QUERY_NODES:
                _reject_mutating_descendants(final)
                return
            if _is_allowed_top_level_command(final):
                return
            # Trailing statement is not a SELECT-family — fall through to
            # per-statement validation which will reject it.

    for stmt in statements:
        if type(stmt) in _ALLOWED_QUERY_NODES:
            _reject_mutating_descendants(stmt)
            continue
        if _is_allowed_top_level_command(stmt):
            continue
        raise MutatingSqlError(
            rejected_node_kind=type(stmt).__name__,
            fragment_preview=stmt.sql()[:60],
        )


def validate_setup_sql(sql: str, dialect: str | None = None) -> None:
    """Raise MutatingSqlError on non-setup SQL; UnparseableSqlError when undetermined.

    Accepts:
      - CREATE TEMP FUNCTION/TABLE/VIEW (any dialect)
      - CREATE [OR REPLACE] MACRO (DuckDB; no TEMP form exists)

    Rejects everything else, including non-TEMP CREATE, DROP, INSERT, etc.
    """
    skeleton = build_skeleton(sql)
    for stmt in _parse(skeleton, dialect, lambda: build_skeleton(sql, False)):
        if isinstance(stmt, exp.Semicolon):
            continue
        if type(stmt) not in _ALLOWED_SETUP_NODES:
            raise MutatingSqlError(
                rejected_node_kind=type(stmt).__name__,
                fragment_preview=stmt.sql()[:60],
                allowlist_label="setup",
            )
        kind = (stmt.args.get("kind") or "").upper()
        # sqlglot represents TEMP via TemporaryProperty in the properties list,
        # not as args["temporary"] = True (that field is always None in practice).
        props = stmt.args.get("properties")
        is_temp = props is not None and any(
            isinstance(p, exp.TemporaryProperty) for p in props.expressions
        )
        is_macro = kind == "MACRO"
        is_allowed_temp_kind = kind in {"FUNCTION", "TABLE", "VIEW"}

        if not (is_macro or (is_temp and is_allowed_temp_kind)):
            raise MutatingSqlError(
                rejected_node_kind=f"Create(kind={kind}, temporary={is_temp})",
                fragment_preview=stmt.sql()[:60],
                allowlist_label="setup",
            )
        # CTAS bodies can smuggle DML through CTEs or SELECT … INTO. Scan
        # descendants the same way validate_select_only does.
        _reject_mutating_descendants(stmt, allowlist_label="setup")
