"""The Python host's answer to the SQL seam: what `normalize_query` learns from sqlglot,
as the plain data Rust's `board_model::sql_wire` decodes. Positions are SQL-local.
`Skeleton.placeholders`, `OutputColumn.sources`/`format` and `Analysis.scope` are not computed (no
Python function returns them) and go empty."""

import re
from collections.abc import Callable, Sequence
from typing import Any

import sqlglot
import sqlglot.errors

from dbt_charts.core.compile.normalize.sql_authoring_lint import (
    find_date_literal_variable,
    has_literal_escaped_newlines,
)
from dbt_charts.core.compile.sql_guard import (
    SKELETON_PLACEHOLDER_PREFIX,
    build_skeleton,
    dialect_for_source,
    validate_select_only,
    validate_setup_sql,
)
from dbt_charts.core.diagnostics.ansi import strip_ansi
from dbt_charts.core.diagnostics.codes_query import (
    WARN_DATE_AS_TEXT,
    WARN_MISSING_JOIN_PREDICATE,
    WARN_REAGGREGATION,
)
from dbt_charts.core.diagnostics.execution import MutatingSqlError, UnparseableSqlError
from dbt_charts.core.inspect.query_validator import (
    query_findings,  # tach-ignore(pre-existing compile->inspect coupling — accepted debt)
)

Wire = dict[str, Any]  # type-state: explicit_any — a JSON object on the host wire
Registry = dict[str, Any]  # type-state: explicit_any — raw or validated source registry
Request = Wire
GuardError = Any  # type-state: explicit_any — a guard's exception, or None when allowed

# What `build_skeleton` turns a `{{ expr }}` into. A placeholder that survives
# into the projection is a column whose name only a render would know.
_JINJA_PLACEHOLDER_RE = re.compile(rf"^{SKELETON_PLACEHOLDER_PREFIX}\d+__$")


def sql_output_columns(sql: str) -> tuple[tuple[str, ...], bool]:
    """The statically-knowable output columns of one SQL string.

    Jinja is flattened to the same skeleton the SQL guard validates, so
    `{{ filter(...) }}` in a WHERE clause costs nothing; a projection the
    skeleton cannot name (`*`, an unaliased aggregate, a Jinja expression)
    is dropped rather than guessed at. The second return says whether the
    columns name *every* projection — counted against ``parsed.selects``,
    which is projection-aligned where ``named_selects`` silently omits
    unnamed expressions. Dialect-agnostic parse: a dialect-specific string
    that fails to parse degrades to no offer.
    """
    try:
        parsed = sqlglot.parse_one(build_skeleton(sql, all_branches=False))
    except (UnparseableSqlError, sqlglot.errors.SqlglotError):
        return (), False
    # Not every sqlglot expression has projections.
    names = getattr(
        parsed, "named_selects", None
    )  # type-state: silent_fallback — expression without projections
    names = names or []  # type-state: silent_fallback — expression without projections
    columns = tuple(
        name
        for name in names
        if name and name != "*" and not _JINJA_PLACEHOLDER_RE.match(name)
    )
    selects = getattr(
        parsed, "selects", None
    )  # type-state: silent_fallback — expression without projections
    complete = (
        selects is not None
        and len(columns) == len(selects)
        # The skeleton is not projection-faithful under Jinja control blocks:
        # an `{% if %}` emits only its primary branch and a `{% for %}` body
        # runs once, so the count proves nothing about the other branches —
        # and a `{{ }}` embedded inside an alias survives as a fabricated
        # `__dct_jN__` name that counts as named.
        and "{%" not in sql
        and not any(SKELETON_PLACEHOLDER_PREFIX in c for c in columns)
    )
    return columns, complete


def guard(
    fn: Callable[..., None], sql: str, dialect: str | None
) -> tuple[str, GuardError]:
    """('allowed' | 'mutating' | 'unparseable', the error)."""
    try:
        fn(sql, dialect=dialect)
    except MutatingSqlError as e:
        return "mutating", e
    except UnparseableSqlError as e:
        return "unparseable", e
    return "allowed", None


def lints(sql: str, dialect: str | None) -> list[Wire]:
    out: list[Wire] = []
    if has_literal_escaped_newlines(sql, dialect=dialect):
        out.append({"kind": "literal_newlines"})
    match = find_date_literal_variable(sql, dialect=dialect)
    if match:
        out.append(
            {
                "kind": "date_literal_variable",
                "keyword": match.group(1).lower(),
                "variable": match.group(2).strip(),
            }
        )
    return out


def verdict(kind: str, error: GuardError) -> Wire:
    if kind == "mutating":
        return {
            "kind": "mutating",
            "node_kind": error.rejected_node_kind,
            "fragment_preview": error.fragment_preview,
        }
    return {"kind": "allowed" if kind == "allowed" else "undetermined"}


def findings(sql: str, dialect: str | None) -> list[Wire]:
    """`query_validator`'s structural findings, tagged by the lint that found them."""
    kinds = {
        WARN_MISSING_JOIN_PREDICATE.code: "missing_join_predicate",
        WARN_REAGGREGATION.code: "reaggregation",
        WARN_DATE_AS_TEXT.code: "date_as_text",
    }
    return [
        {
            "kind": kinds[d.code],
            "column": column,
            "severity": d.severity,
            "message": d.message,
            "detail": d.detail,
            "recommendation": d.recommendation,
            "confidence": d.confidence,
            "evidence": list(d.evidence),
        }
        for d, column in query_findings(sql, dialect)
    ]


def outcome(request: Request, sources: Registry) -> Wire:
    """What a Python host answers for `request`, by the functions `normalize_query` calls."""
    sql, setup = request["sql"], request.get("setup_sql")
    source_dialect = dialect_for_source(request.get("source"), sources)
    dialect = request.get("dialect") or source_dialect
    sql_kind, sql_error = guard(validate_select_only, sql, dialect)
    sql_lints = lints(sql, dialect)
    setup_analysis = None
    setup_kind = "allowed"
    if setup:
        setup_kind, setup_error = guard(validate_setup_sql, setup, source_dialect)
        setup_analysis = {
            "guard": verdict(setup_kind, setup_error),
            "lints": lints(setup, source_dialect),
        }
    fatal = (
        sql_lints
        or (setup_analysis and setup_analysis["lints"])
        or sql_kind == "mutating"
        or setup_kind == "mutating"
    )
    if (
        not fatal
        and sql_kind == "unparseable"
        and dialect is not None
        and sql_error.sql_position is not None
    ):
        pos = sql_error.sql_position
        return {
            "kind": "parse_error",
            "message": strip_ansi(str(sql_error.cause)),
            "position": {
                "line": pos.line,
                "start_col": pos.start_col,
                "end_col": pos.end_col,
                "line_text": pos.line_text,
            },
        }
    try:
        skeleton = build_skeleton(sql, all_branches=True)
    except UnparseableSqlError:
        skeleton = ""
    names, complete = sql_output_columns(sql)
    return {
        "kind": "analyzed",
        "analysis": {
            "skeleton": skeleton,
            "select_only": verdict(sql_kind, sql_error),
            "lints": sql_lints,
            "findings": findings(sql, dialect),
            "setup": setup_analysis,
            "outputs": list(names),
            "outputs_complete": complete,
        },
    }


def analyze_all(requests: Sequence[Request], sources: Registry) -> list[Wire]:
    return [outcome(r, sources) for r in requests]
