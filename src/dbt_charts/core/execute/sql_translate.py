"""Translate board SQL from the dialect it was authored in to the warehouse's.

Runs only on SQL rendered with `INLINE_PLACEHOLDERS` and with relations swapped
for `\\x00dct_rel_N\\x00` sentinels: sqlglot rewrites driver placeholders and
cannot parse a target-quoted relation. Every failure is fatal.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import sqlglot
import sqlglot.expressions as exp
from sqlglot.dialects.dialect import Dialect as SqlglotDialect

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.compile.models.source import (
    ResolvedSourceConfig,
    is_file_source,
)
from dbt_charts.core.compile.sql_guard import sqlglot_dialect
from dbt_charts.core.compile.template.parameterized import (
    render_parameterized_with_queries,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_SQL_DIALECT_UNSUPPORTED
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.dialects import SQLDialect, get_dialect
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS

if TYPE_CHECKING:
    # The adapters package imports this module; a runtime import is a cycle.
    from dbt_charts.core.execute.adapters.dbt_utils import DbtRefResolver
    from dbt_charts.core.execute.metricflow import SemanticLayer

_SENTINEL_RE = re.compile(r"\x00dct_(?:param|rel)_\d+\x00")
_PARAM_RE = re.compile(r"\x00dct_param_(\d+)\x00")

BoundParams = list[Any]  # type-state: explicit_any — runtime-typed values


def dialect_unsupported(read: str, write: str, reason: str) -> ExecutionError:
    return ExecutionError.from_code(
        ERR_SQL_DIALECT_UNSUPPORTED, read=read, write=write, reason=reason
    )


def _emit(tree: exp.Expr, write: str | None) -> str:
    return tree.sql(dialect=write, unsupported_level=sqlglot.ErrorLevel.RAISE)


def _first_unknown_function(tree: exp.Expr) -> str | None:
    return next((node.name for node in tree.find_all(exp.Anonymous)), None)


# Warehouses whose DATE_TRUNC('week') starts on Monday. Others differ (BigQuery
# Sunday) or depend on a session setting (Snowflake WEEK_START).
_MONDAY_WEEK_DIALECTS = frozenset(
    {
        "duckdb",
        "postgres",
        "redshift",
        "databricks",
        "spark",
        "trino",
        "presto",
        "athena",
    }
)


def _keep_monday_weeks(
    tree: exp.Expr, read: str, write: str
) -> tuple[exp.Expr, Counter[str]]:
    """Rewrite `date_trunc('week', x)` so the target buckets weeks as the source does.

    Only Monday weeks are translatable: a source whose `WEEK` starts elsewhere
    (BigQuery's Sunday) or on a session setting is refused. BigQuery takes `WEEK(MONDAY)`; Snowflake's own `WEEK` follows a
    session parameter, so Monday is built from `DAYOFWEEKISO`, which repeats the
    operand; the second return value counts the placeholders that adds.

    Raises:
        ExecutionError: ERR-SQL-DIALECT-UNSUPPORTED when a week does not start on
            Monday or the target has no Monday spelling.
    """

    def week_start(node: exp.Expr) -> str | None:
        unit = node.args["unit"]
        if isinstance(unit, exp.WeekStart):
            return unit.name.upper()
        if isinstance(unit, exp.Var) and unit.name.upper() == "WEEK":
            return "MONDAY" if read in _MONDAY_WEEK_DIALECTS else "UNKNOWN"
        return None

    weeks = [
        (n, start)
        for n in tree.find_all(exp.TimestampTrunc, exp.DateTrunc)
        if (start := week_start(n)) is not None
    ]
    duplicated: Counter[str] = Counter()
    if not weeks or write in _MONDAY_WEEK_DIALECTS and read in _MONDAY_WEEK_DIALECTS:
        return tree, duplicated
    if any(start != "MONDAY" for _, start in weeks):
        raise dialect_unsupported(
            read, write, "only weeks starting on Monday can be translated"
        )
    nodes = [node for node, _ in weeks]
    if write == "bigquery":
        for node in nodes:
            node.set("unit", exp.WeekStart(this=exp.var("MONDAY")))
    elif write == "snowflake":
        for node in nodes:
            operand = node.this
            duplicated.update(_SENTINEL_RE.findall(operand.sql()))
            node.replace(
                exp.DateAdd(
                    this=exp.TimestampTrunc(this=operand.copy(), unit=exp.var("DAY")),
                    expression=exp.Sub(
                        this=exp.Literal.number(1),
                        expression=exp.DayOfWeekIso(this=operand.copy()),
                    ),
                    unit=exp.var("DAY"),
                )
            )
    elif write not in _MONDAY_WEEK_DIALECTS:
        raise dialect_unsupported(
            read, write, "the target has no deterministic Monday-week truncation"
        )
    return tree, duplicated


def translate_sql(sql: str, read: str, write: str) -> str:
    """Return `sql` (written in `read`) as `write` SQL.

    Args:
        sql: One statement, with params and relations already sentinel-encoded.
        read: dbt Charts name of the dialect `sql` is written in.
        write: dbt Charts name of the warehouse dialect to emit.

    Raises:
        ExecutionError: ERR-SQL-DIALECT-UNSUPPORTED when the SQL does not parse,
            holds several statements, calls a function sqlglot does not know,
            uses a construct the target cannot express, or loses or duplicates
            a placeholder.
    """
    read_name, write_name = sqlglot_dialect(read), sqlglot_dialect(write)
    if read_name == write_name:
        return sql
    # sqlglot raises ValueError for a dialect name it does not know.
    try:
        trees = sqlglot.parse(sql, read=read_name)
    except Exception as exc:  # noqa: BLE001 — sqlglot raises arbitrary internal errors on constructs it cannot model
        raise dialect_unsupported(
            read, write, f"the SQL does not parse ({exc})"
        ) from exc
    if len(trees) != 1 or trees[0] is None:
        raise dialect_unsupported(read, write, "exactly one statement is supported")
    tree = trees[0]
    # sqlglot passes a function it does not know through under its source name
    # and its RAISE level does not object, so the name may mean something else
    # (or nothing) on the target. A Command is raw text it could not model.
    unknown = _first_unknown_function(tree)
    if unknown is not None:
        raise dialect_unsupported(
            read, write, f"function {unknown}() has no known translation"
        )
    if tree.find(exp.Command) is not None:
        raise dialect_unsupported(read, write, "this statement kind is not translated")
    tree, duplicated = _keep_monday_weeks(tree, read, write)
    try:
        out = _emit(tree, write_name)
        emitted = sqlglot.parse_one(out, read=write_name)
    except Exception as exc:  # noqa: BLE001 — sqlglot raises arbitrary internal errors on constructs it cannot model
        raise dialect_unsupported(read, write, str(exc)) from exc
    # Every sentinel must come out as a bare identifier: sqlglot can move one
    # into a string literal (`INTERVAL {{ n }} DAY` becomes `INTERVAL '<n> DAY'`),
    # where the value inlined later would no longer be data.
    in_text = Counter(_SENTINEL_RE.findall(sql)) + duplicated
    as_identifiers = Counter(
        node.name
        for node in emitted.find_all(exp.Identifier)
        if not node.quoted and _SENTINEL_RE.fullmatch(node.name)
    )
    if in_text != Counter(_SENTINEL_RE.findall(out)) or in_text != as_identifiers:
        raise dialect_unsupported(
            read, write, "a variable or ref() would end up inside a string or comment"
        )
    return out


_DIALECT_ALIASES = {"postgresql": "postgres", "mssql": "sqlserver", "mariadb": "mysql"}


def engine_dialect(authored: str, source_config: ResolvedSourceConfig) -> str:
    """The canonical dialect of the engine that runs a source's SQL.

    File sources run on an in-process DuckDB. Every translate-or-refuse decision
    goes through this, so no path compares raw source types.

    Raises:
        ExecutionError: ERR-SQL-DIALECT-UNSUPPORTED when the type has no sqlglot
            dialect.
    """
    if is_file_source(source_config):
        return "duckdb"
    engine = source_config.type
    if engine in _DIALECT_ALIASES:
        engine = _DIALECT_ALIASES[engine]
    if sqlglot_dialect(engine) not in SqlglotDialect.classes:
        raise dialect_unsupported(
            authored, source_config.type, "this source type has no known SQL dialect"
        )
    return engine


def dialect_differs(authored: str | None, source_config: ResolvedSourceConfig) -> bool:
    """Whether SQL authored in `authored` needs translating for this source."""
    return authored is not None and authored != engine_dialect(authored, source_config)


def render_in_dialect(
    sql: str,
    read: str,
    write: str,
    variables: VariableValues,
    queries: Mapping[str, AnyQuery] | None,
    strict: bool,
    resolver: DbtRefResolver,
    target_path: str | None,
    placeholders: SQLDialect,
    semantic: SemanticLayer
    | None = None,  # type-state: optional — None is a source with no MetricFlow bundle
) -> tuple[str, BoundParams]:
    """Render board SQL written in `read` into `write` SQL plus its parameters.

    dbt relations become sentinels while the statement is translated, since
    their text is quoted for `write`, which `read` may not parse. Values stay
    out of the SQL: they come back as `placeholders`' own placeholders, one per
    occurrence in order, with the matching parameter list.

    With `semantic`, the source's MetricFlow bundle, a statement that reads one
    of its datasets or constellations is compiled by MetricFlow instead of
    translated; it emits `write` SQL itself.

    Raises:
        ExecutionError: ERR-SQL-DIALECT-UNSUPPORTED, a MetricFlow compile
            failure, or the resolver's error for a ref() the manifest cannot
            resolve.
    """
    relations: dict[str, str] = {}

    def encode(relation: str) -> str:
        sentinel = f"\x00dct_rel_{len(relations) + 1}\x00"
        relations[sentinel] = relation
        return sentinel

    own_sql, _ = resolver.resolve(sql, target_path, encode)
    rendered = render_parameterized_with_queries(
        own_sql,
        dict(variables),
        queries=dict(queries) if queries else None,
        dialect=INLINE_PLACEHOLDERS,
        strict=strict,
        warehouse=get_dialect(read),
    )
    # A composed `{{ queries.X }}` body is inlined during the render above, so
    # its refs only appear now.
    composed_sql, _ = resolver.resolve(rendered.sql, target_path, encode)
    if semantic is not None and semantic.references(composed_sql, read):
        translated = semantic.compile(composed_sql, read, write)
    else:
        translated = translate_sql(composed_sql, read, write)
    for sentinel, relation in relations.items():
        translated = translated.replace(sentinel, relation)
    if placeholders is INLINE_PLACEHOLDERS:
        return translated, list(rendered.params)
    params: BoundParams = []

    def bind(match: re.Match[str]) -> str:
        params.append(rendered.params[int(match.group(1)) - 1])
        return placeholders.param(len(params))

    return _PARAM_RE.sub(bind, translated), params
