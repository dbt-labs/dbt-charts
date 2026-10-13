"""SQL literal inlining for dbt-adapter execution paths.

dbt adapter.execute() accepts plain SQL only — no parameterized queries.
This module provides functions for inlining positional $N params or named
dialect-specific params as properly escaped SQL literals.

Escaping here is the last line of defense, not the only one. Values reaching
these functions include runtime variable values, which originate as user input;
that is what the quoting is for. Identifiers — column and table names — must be
validated before they arrive, since quoting a value cannot make an injected
identifier safe.

Which characters have to be escaped is a property of the engine that parses
the literal, not of the placeholder style it was rendered in, so every entry
point takes that engine and none of them defaults it. An omitted argument
would be indistinguishable from asking for no escaping — a call site could
then quietly opt out of the control, which is how a live injection once
survived the round that fixed it.
"""

import math
import re
from collections.abc import Callable
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from dbt_charts.core.dialects import SQLDialect

_DOLLAR_RE = re.compile(r"\$(\d+)")
_PERCENT_S_RE = re.compile(r"%s")
_QMARK_RE = re.compile(r"\?")


class InlinePlaceholderDialect(SQLDialect):
    """Placeholder style for SQL that is flattened to literals immediately.

    When rendering and inlining are two halves of one round trip that never
    reaches a driver, the placeholders only have to be unambiguous — and a
    warehouse's own syntax is not. `$1` and `?` occur in ordinary SQL (money
    strings, URLs with a query string), and once the renderer has emitted a
    placeholder of the same shape, nothing downstream can tell the two apart:
    the substitution either runs off the end of the parameter list or splices a
    value into the middle of somebody's string literal. NUL cannot appear in
    authored SQL, so this style collides with nothing.

    `uses_named_params` here means only "each index has its own placeholder, so
    match it whole rather than by pattern" — that is what routes flattening to
    inline_dialect_params. It does not promise the dict-shaped binding the base
    class describes, and must not be read that way if binding is ever added:
    this style never reaches a driver.
    """

    name = "dbt_charts_inline"
    uses_named_params = True

    def param(self, index: int) -> str:
        """Return the collision-free placeholder for a 1-based index."""
        return f"\x00dct_param_{index}\x00"

    def date_expr(self, column: str) -> str:
        """No engine parses this style, so it cannot spell warehouse SQL: a
        caller reaching a filter helper with it has not passed `warehouse=`."""
        raise NotImplementedError(
            "InlinePlaceholderDialect is a placeholder style, not a warehouse; "
            "pass warehouse= to render_parameterized so the filter helpers spell "
            "SQL for the engine that runs it"
        )


INLINE_PLACEHOLDERS = InlinePlaceholderDialect()


def _to_sql_literal(value: Any, escaping: SQLDialect) -> str:
    """Convert a Python value to a SQL literal string.

    Args:
        value: The value to render.
        escaping: The engine that will parse the literal. Its
            `escapes_backslashes` decides whether a backslash in the value has
            to be doubled; a dialect that declares no answer raises here rather
            than defaulting to one.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            raise ValueError(
                "NaN cannot be inlined as a SQL literal — different warehouses use "
                "different syntax (FLOAT64 / DOUBLE / DOUBLE PRECISION / FLOAT). "
                "Filter the value out before passing to inline_params, or pass a "
                "string like 'NaN' if your warehouse supports it."
            )
        if math.isinf(value):
            raise ValueError(
                "Infinity cannot be inlined as a SQL literal — different warehouses "
                "use different syntax. Filter the value out before passing to "
                "inline_params, or pass a string."
            )
        return repr(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.isoformat()}'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, time):
        return f"TIME '{value.isoformat()}'"
    if isinstance(value, bytes):
        return f"X'{value.hex()}'"
    return escaping.string_literal(str(value))


def sql_string_literal(value: str, escaping: SQLDialect) -> str:
    """Render a string as an escaped SQL string literal.

    For call sites that inline an already-known string — a schema or table name
    from the inspector — directly into SQL, rather than substituting $N/%s
    placeholders. Same escaping contract as the placeholder inliners: the engine
    that parses the literal decides whether an embedded quote is doubled or
    backslash-escaped (see the module note).
    """
    return _to_sql_literal(value, escaping)


def inline_params(sql: str, params: list[Any], escaping: SQLDialect) -> str:
    """Inline $N-style positional params into a SQL string as SQL literals.

    Replaces $1, $2, ... placeholders with properly escaped SQL literals.
    Uses re.sub with a callback so each match position is visited exactly once —
    substituted text is never re-scanned, preventing re-substitution attacks.

    Args:
        sql: SQL string with $1, $2, ... positional placeholders.
        params: Ordered list of parameter values to inline.
        escaping: The engine that will parse the literals — its documented
            string-literal grammar decides the escaping (see the module note).

    Returns:
        SQL string with all $N placeholders replaced by SQL literals.

    Raises:
        IndexError: A $N placeholder references an index outside [1, len(params)].
    """

    def repl(m: re.Match[str]) -> str:
        idx = int(m.group(1)) - 1
        if not 0 <= idx < len(params):
            raise IndexError(
                f"Parameter index ${m.group(1)} out of range for {len(params)} param(s)"
            )
        return _to_sql_literal(params[idx], escaping)

    return _DOLLAR_RE.sub(repl, sql)


def inline_dialect_params(
    sql: str,
    params: list[Any],
    param_fn: Callable[[int], str],
    escaping: SQLDialect,
) -> str:
    """Inline dialect-specific named params into SQL as SQL literals.

    For dialects whose placeholder format is not `$N` (e.g., BigQuery's `@paramN`,
    Databricks' `:param1`, SQL Server's `@p1`), replaces each placeholder with a
    properly escaped SQL literal.

    Uses a single-pass regex so substituted text is never re-scanned — a param
    value that happens to contain another placeholder string is not re-substituted.

    The dbt adapter.execute() accepts only plain SQL, so this function is used
    when executing against named-param dialects via dbt adapters.

    Args:
        sql: SQL string with dialect-specific parameter placeholders.
        params: Ordered list of parameter values (1-indexed by placeholder number).
        param_fn: Callable(index: int) -> str — returns the placeholder string for
            the given 1-based index. Pass the dialect's ``param`` method.
        escaping: The engine that will parse the literals — its documented
            string-literal grammar decides the escaping (see the module note).

    Returns:
        SQL string with all dialect placeholders replaced by SQL literals.
    """
    if not params:
        return sql

    # Build a pattern that alternates all placeholders, sorted longest-first so
    # @param10 is matched before @param1 when they share a common prefix.
    placeholders = sorted(
        (re.escape(param_fn(i)), i) for i in range(1, len(params) + 1)
    )
    # Sort by length descending to avoid partial-match of @param1 inside @param10
    placeholders.sort(key=lambda t: len(t[0]), reverse=True)
    idx_by_placeholder = dict(placeholders)
    pattern = re.compile("|".join(ph for ph, _ in placeholders))

    def repl(m: re.Match[str]) -> str:
        ph = re.escape(m.group(0))
        return _to_sql_literal(params[idx_by_placeholder[ph] - 1], escaping)

    return pattern.sub(repl, sql)


def inline_qmark_params(sql: str, params: list[Any], escaping: SQLDialect) -> str:
    """Inline ?-style positional params into a SQL string as SQL literals.

    Snowflake uses ? for positional parameters. Each ? is replaced in order
    with the corresponding parameter value as a SQL literal.

    Args:
        sql: SQL string with ? positional placeholders.
        params: Ordered list of parameter values to inline.
        escaping: The engine that will parse the literals — its documented
            string-literal grammar decides the escaping (see the module note).

    Returns:
        SQL string with all ? placeholders replaced by SQL literals.

    Raises:
        ValueError: Param count does not match placeholder count.
    """
    n_placeholders = len(_QMARK_RE.findall(sql))
    if len(params) != n_placeholders:
        raise ValueError(
            f"inline_qmark_params: {n_placeholders} ? placeholder(s) in SQL "
            f"but {len(params)} param(s) provided"
        )

    it = iter(params)

    def repl(m: re.Match[str]) -> str:
        return _to_sql_literal(next(it), escaping)

    return _QMARK_RE.sub(repl, sql)


def inline_params_for_dialect(
    sql: str,
    params: list[Any],
    dialect: SQLDialect,
    escaping: SQLDialect,
) -> str:
    """Inline params written in `dialect`'s placeholder style as SQL literals.

    Every dbt-adapter execution path renders parameterized SQL and then has to
    flatten it, because dbt's adapter.execute() takes only a SQL string. Which
    placeholders to look for is a property of the dialect that produced them,
    so the choice belongs here rather than at each call site — but every
    current caller finds SQL rendered in the internal `InlinePlaceholderDialect`
    style (`uses_named_params = True`), never a warehouse's own placeholder
    syntax: the warehouse's own `$1`/`?`/`%s` can occur in authored string
    literals and would be corrupted by the inline step, so `dialect` here is
    always the collision-free internal one and `escaping` carries the
    warehouse instead. The `?`- and `$N`-positional branches below exist for a
    `dialect` that isn't `InlinePlaceholderDialect`, which no caller passes
    today.

    Args:
        sql: SQL string whose placeholders were emitted for `dialect`.
        params: Ordered parameter values, 1-indexed by placeholder number.
        dialect: The dialect the placeholders were rendered in.
        escaping: The warehouse the literals will execute on. Often the same
            object as `dialect`, but not where placeholders are an internal
            round trip: there `dialect` is a style no engine ever parses, while
            the literals still land on a real warehouse.

    Returns:
        SQL string with every placeholder replaced by an escaped SQL literal.

    Raises:
        ValueError: A recognizable fragment of an internal placeholder token
            survived inlining — the token was transformed before it could be
            substituted, the parameter was silently dropped, and the SQL must
            not reach a driver. Matched case-insensitively after the NUL so a
            case-mangled token (`| lower`, `| title`) is still caught; a NUL
            that arrives inside a *value* is not this failure and passes
            through to the driver's own handling.
    """
    if dialect.uses_named_params:
        inlined = inline_dialect_params(sql, params, dialect.param, escaping)
    elif dialect.param(1) == "?":
        inlined = inline_qmark_params(sql, params, escaping)
    else:
        inlined = inline_params(sql, params, escaping)
    if re.search(r"\x00dct_param_", inlined, flags=re.IGNORECASE):
        raise ValueError(
            "SQL still contains an internal parameter placeholder after "
            "inlining — a Jinja filter transformed it before it could be "
            "substituted, so the value would never be bound. Apply the "
            "transform in SQL instead."
        )
    return inlined


def inline_percent_params(sql: str, params: list[Any], escaping: SQLDialect) -> str:
    """Inline %s-style positional params into a SQL string as SQL literals.

    Used by InspectConnection where legacy code used %s placeholders for
    schema/table name parameters that now need literal inlining via dbt adapters.
    Uses re.sub with a callback so each match is visited exactly once —
    substituted text is never re-scanned, preventing re-substitution attacks.

    Args:
        sql: SQL string with %s positional placeholders.
        params: Ordered list of parameter values to inline. Count must match
            exactly the number of %s placeholders — too few or too many raises.
        escaping: The engine that will parse the literals — its documented
            string-literal grammar decides the escaping (see the module note).

    Returns:
        SQL string with all %s placeholders replaced by SQL literals.

    Raises:
        ValueError: Param count does not match placeholder count.
    """
    n_placeholders = len(_PERCENT_S_RE.findall(sql))
    if len(params) != n_placeholders:
        raise ValueError(
            f"inline_percent_params: {n_placeholders} %s placeholder(s) in SQL "
            f"but {len(params)} param(s) provided"
        )

    it = iter(params)

    def repl(m: re.Match[str]) -> str:
        return _to_sql_literal(next(it), escaping)

    return _PERCENT_S_RE.sub(repl, sql)
