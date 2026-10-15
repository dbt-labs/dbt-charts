"""Shared Jinja helpers used by compile/template/parameterized.py and compile/template/jinja.py.

Both modules need identical lenient-undefined and query-namespace behavior;
this module is the single canonical home. Consumers import from here.
"""

from __future__ import annotations

from typing import Any

from jinja2 import TemplateSyntaxError, Undefined
from jinja2.exceptions import TemplateError


def template_error_location(template: str, exc: TemplateError) -> str:
    """The template line a Jinja failure came from; Jinja's own message names no line."""
    lineno = exc.lineno if isinstance(exc, TemplateSyntaxError) else None
    tb = exc.__traceback__
    while lineno is None and tb is not None:
        if tb.tb_frame.f_code.co_filename == "<template>":
            lineno = tb.tb_lineno
        tb = tb.tb_next
    if lineno is None:
        return ""
    # Jinja counts only \n, \r\n and \r; str.splitlines() also splits on \x0c etc.
    lines = template.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return f" (template line {lineno}: {lines[lineno - 1].strip()})"


class _LenientUndefined(Undefined):
    """Lenient undefined for non-strict Jinja rendering.

    Undefined variables become empty strings rather than raising errors.
    Used in interactive editing contexts where variables may not yet be defined.
    """

    def __str__(self) -> str:
        return ""

    def __repr__(self) -> str:
        return ""

    def __bool__(self) -> bool:
        return False

    def __iter__(self) -> Any:
        return iter([])

    def __len__(self) -> int:
        return 0

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Undefined) or other is None or other == ""

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)

    def __getitem__(
        self,
        key: Any,  # type-state: explicit_any — Jinja subscripts with any key
    ) -> _LenientUndefined:
        return self

    def __getattr__(self, name: str) -> _LenientUndefined:
        # Return self for chaining like {{ foo.bar.baz }}; guard dunder lookups.
        if name.startswith("_"):
            raise AttributeError(name)
        return self


_CACHE_REF_PREFIX = "__dct_cache_ref__"
_CACHE_REF_SUFFIX = "__"


class _QueryProxy:
    """Proxy for a single named query in the {{ queries.NAME }} namespace.

    __str__ renders ``{{ queries.X }}`` as ``(<sql>) AS X`` — a parenthesized
    subquery aliased to the query name (valid on DuckDB and Postgres alike).
    .cache returns a sentinel string that marks the reference for cache-path
    execution: ``__dct_cache_ref__<name>__``. The executor detects the sentinel,
    swaps it for the bare upstream name, and runs the composing SQL over the
    registered upstream rows in an isolated in-process DuckDB.
    """

    def __init__(self, name: str, sql: str) -> None:
        self._name = name
        self._sql = sql

    def __str__(self) -> str:
        # {{ queries.X }} path — render as a parenthesized subquery aliased to the
        # query name. Parens + alias make the same authored SQL valid on both the
        # DuckDB cache engine and Cloud Postgres (Postgres requires the alias;
        # DuckDB tolerates it). Columns are referenced as `X.col`. Because of the
        # alias, a ref is only valid in FROM/JOIN position — not a CTE body or a
        # scalar subquery.
        return f"({self._sql}) AS {self._name}"

    def __eq__(self, other: object) -> bool:
        # Identity by the underlying SQL body (NOT the rendered `(sql) AS name`
        # form that __str__ produces). Both proxy-vs-proxy and proxy-vs-str
        # compare on _sql only so __hash__ (also sql-only) satisfies
        # equal => same hash. Used for dedup, not for string substitution.
        if isinstance(other, _QueryProxy):
            return self._sql == other._sql
        if isinstance(other, str):
            return self._sql == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._sql)

    @property
    def cache(self) -> str:
        """Sentinel for {{ queries.X.cache }} cache-read composition.

        The executor detects this sentinel in the composing query's SQL,
        demand-executes X, swaps the sentinel for the bare upstream name, and
        runs the composing SQL over the registered upstream rows in an isolated
        in-process DuckDB (see cache_composition.compose_over_named_rows).
        """
        return f"{_CACHE_REF_PREFIX}{self._name}{_CACHE_REF_SUFFIX}"


class _QueryNamespace:
    """Proxy for {{ queries.query_name }} template resolution.

    Returns a _QueryProxy whose __str__ is the inline aliased subquery
    (`(<sql>) AS X` for {{ queries.X }}) and whose .cache property is a sentinel
    for cache-read composition ({{ queries.X.cache }}). Raises AttributeError
    when the query is not found.
    """

    def __init__(
        self,
        queries: dict[str, Any],  # type-state: explicit_any — per-caller query objects
    ) -> None:
        self._queries = queries

    def __getattr__(self, name: str) -> _QueryProxy:
        if name.startswith("_"):
            raise AttributeError(name)

        if name not in self._queries:
            raise AttributeError(f"Query '{name}' not found")

        query = self._queries[name]

        if hasattr(query, "sql"):
            sql = query.sql
            sql_str = sql if isinstance(sql, str) else ""
        elif isinstance(query, dict):
            sql_raw = query.get("sql", "")
            sql_str = sql_raw if isinstance(sql_raw, str) else ""
        else:
            sql_str = str(query)

        return _QueryProxy(name, sql_str)
