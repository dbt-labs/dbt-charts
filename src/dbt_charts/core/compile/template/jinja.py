"""Jinja template resolution module.

Stage: COMPILE (Part of Step 3: Normalization)
Purpose: Resolve Jinja templates in queries and SQL strings.

Entry Points:
    - resolve_jinja_template(template: str, variables: Dict) -> str
    - extract_variable_dependencies(template: str) -> Set[str]

Inputs:
    - Template strings with {{ variable }} syntax
    - Variable values for substitution

Outputs:
    - Resolved strings with variables substituted
    - Set of variable names referenced in templates

Dependencies:
    - jinja2 (Environment, Template, meta)

Errors:
    - JinjaError: Template syntax or resolution errors

See also:
    - compile/normalize/dispatch.py: Uses this module

Security Note:
    When using strict=False (lenient mode), undefined variables are silently
    converted to empty strings. This mode should ONLY be used in interactive
    contexts (chart editor) where the user is actively editing and expects
    partial results. Production rendering should always use strict=True.
"""

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from jinja2 import (
    Environment,
    StrictUndefined,
    TemplateError,
    TemplateSyntaxError,
    Undefined,
    UndefinedError,
    meta,
    nodes as jinja_nodes,
)
from jinja2.exceptions import TemplateRuntimeError
from jinja2.utils import missing

from dbt_charts.core.compile.errors import (
    CompilationError,
    JinjaError,
    TemplateOutputTooLargeError,
)
from dbt_charts.core.compile.models.query.normalized import AnyQuery, is_sql_query
from dbt_charts.core.compile.template._helpers import (
    _LenientUndefined,
    _QueryNamespace,
    template_error_location,
)
from dbt_charts.core.compile.template.environment import BoardTemplateEnvironment
from dbt_charts.core.compile.template.listings import (
    LISTING_FUNCTIONS,
    TextListings,
    listing_helpers,
)
from dbt_charts.core.compile.template.markdown_fence import mask_fenced_code
from dbt_charts.core.compile.template.output_budget import (
    TemplateOutputBudgetExceeded,
    render_with_budget,
)
from dbt_charts.core.compile.template.query_result import (
    QueryResult,
    QueryResults,
    underscore_access_error,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_QUERY_DIALECT_MIXED,
    ERR_TEXT_QUERIES_NONLITERAL,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_SQL_DIALECT_UNSUPPORTED
from dbt_charts.core.diagnostics.codes_render import (
    ERR_TEXT_EMPTY_SEQUENCE,
    ERR_TEXT_NULL_COMPARISON,
    ERR_TEXT_TYPE_ERROR,
)

logger = logging.getLogger(__name__)


# Jinja environment with strict undefined (errors on missing variables)
_jinja_env = BoardTemplateEnvironment(undefined=StrictUndefined)

# Lenient Jinja environment (undefined variables become empty/None)
_jinja_env_lenient = BoardTemplateEnvironment(undefined=_LenientUndefined)


class _BoardTextEnvironment(BoardTemplateEnvironment):
    """Board text: an underscore name on a query result raises, never renders empty."""

    def unsafe_undefined(
        self,
        obj: Any,  # type-state: explicit_any — any template value
        attribute: str,
    ) -> Undefined:
        if not attribute.startswith("__"):
            error = underscore_access_error(obj, attribute)
            if error is not None:
                raise error
        return super().unsafe_undefined(obj, attribute)


class _TextUndefined(_LenientUndefined):
    """Board text: an item taken from an empty list raises instead of rendering empty.

    Jinja's ``first``/``last``/``min``/``max``/``random`` return an undefined
    whose hint ends with "sequence was empty."
    """

    def __init__(
        self,
        hint: str | None = None,
        obj: Any = missing,  # type-state: explicit_any — Jinja's undefined contract
        name: str | None = None,
        exc: type[TemplateRuntimeError] = UndefinedError,
    ) -> None:
        if hint is not None and hint.endswith("sequence was empty."):
            raise ChartDataError.from_code(ERR_TEXT_EMPTY_SEQUENCE, detail=hint)
        super().__init__(hint, obj, name, exc)


_jinja_env_text = _BoardTextEnvironment(undefined=_TextUndefined)

# {{ queries.X }} inline ref and {{ queries.X.cache }} cache-read ref patterns.
# Shared by dependency detection and inline substitution so the two stay in sync.
_QUERY_REF_RE = re.compile(r"\{\{\s*queries\.(\w+)\s*\}\}")
_CACHE_REF_RE = re.compile(r"\{\{\s*queries\.(\w+)\s*\.\s*cache\s*\}\}")
# A referenced query name becomes a SQL table alias (`(<sql>) AS <name>`), so it
# must be a usable identifier on every engine. Reject names that collide with a
# SQL reserved word at compile time — otherwise the alias emits a cryptic parse
# error at execution. (ANSI + common Postgres/DuckDB/BigQuery reserved words.)
_SQL_RESERVED_WORDS: frozenset[str] = frozenset(
    {
        "all",
        "and",
        "any",
        "array",
        "as",
        "asc",
        "between",
        "by",
        "case",
        "cast",
        "check",
        "collate",
        "column",
        "constraint",
        "create",
        "cross",
        "current",
        "default",
        "delete",
        "desc",
        "distinct",
        "drop",
        "else",
        "end",
        "except",
        "exists",
        "false",
        "fetch",
        "for",
        "foreign",
        "from",
        "full",
        "group",
        "having",
        "in",
        "inner",
        "insert",
        "intersect",
        "into",
        "is",
        "join",
        "left",
        "like",
        "limit",
        "natural",
        "not",
        "null",
        "offset",
        "on",
        "or",
        "order",
        "outer",
        "primary",
        "qualify",
        "references",
        "right",
        "select",
        "set",
        "some",
        "table",
        "then",
        "to",
        "true",
        "union",
        "unique",
        "update",
        "using",
        "values",
        "when",
        "where",
        "window",
        "with",
    }
)


def _validate_query_ref_name(name: str) -> None:
    """Reject a referenced query name that can't be a SQL table alias.

    A ``{{ queries.X }}`` / ``{{ queries.X.cache }}`` reference renders as
    ``(<sql>) AS X``, so X has to be a plain, non-reserved SQL identifier.
    """
    if name.lower() in _SQL_RESERVED_WORDS:
        raise CompilationError(
            f"Query '{name}' is referenced via {{{{ queries.{name} }}}}, but "
            f"'{name}' is a SQL reserved word and cannot be used as a table "
            f"alias. Rename the query to a non-reserved identifier."
        )


# dbt charts runtime namespaces/callables — always stripped from dependency analysis.
# These names conflict with dbt charts helper functions injected into the Jinja context
# at render time. Users must not declare variables with these names.
RESERVED_VARIABLE_NAMES: frozenset[str] = frozenset(
    {
        "filter",
        "filter_date_range",
        "queries",
        *LISTING_FUNCTIONS,
    }
)

# The only names resolve_jinja_template's filter_helpers= may bind. It exists
# to swap the raising stub below for a real closure over a known warehouse —
# not a general mechanism for adding a new callable to the render context.
RESERVED_FILTER_HELPER_NAMES: frozenset[str] = frozenset(
    {"filter", "filter_date_range"}
)

# dbt SQL Jinja builtins that appear as CALLS in embedded dbt model SQL.
# Stripped from dependency analysis only when the name appears as a function call
# (e.g. {{ source('schema', 'table') }}), NOT when referenced bare ({{ source }}).
# A bare ref means the user declared a variable with that name (e.g. a "source"
# dropdown filter) — that is valid and should flow through to validation.
_DBT_BUILTIN_CALLS: frozenset[str] = frozenset(
    {
        "ref",
        "source",
        "var",
        "env_var",
        "config",
        "is_incremental",
        # Note: 'this' is omitted — in dbt SQL it is always a bare token, never a call.
        # {{ this }} in a dbt charts board query is an undefined user variable reference.
    }
)


def extract_variable_dependencies(template_str: str) -> set[str]:
    """Extract variable names referenced in a Jinja template.

    Uses Jinja's AST parser to find all undeclared variables in the template.
    Filters out known helper functions and namespaces.

    This is used during normalization to track which variables a query,
    chart title, or other template depends on.

    Args:
        template_str: String potentially containing Jinja expressions

    Returns:
        Set of variable names referenced in the template

    Example:
        >>> extract_variable_dependencies(
        ...     "SELECT * FROM orders WHERE region = '{{ region }}'"
        ... )
        {'region'}

        >>> extract_variable_dependencies(
        ...     "SELECT * WHERE {{ filter('region', region) }}"
        ... )
        {'region'}
    """
    if not template_str:
        return set()

    # Quick check - no Jinja syntax
    if "{{" not in template_str and "{%" not in template_str:
        return set()

    try:
        env = Environment()
        ast = env.parse(template_str)
        undeclared = meta.find_undeclared_variables(ast)

        # Always strip dbt charts runtime helpers — never user variables.
        result = undeclared - RESERVED_VARIABLE_NAMES

        # Strip dbt builtins only when they appear as FUNCTION CALLS, not bare refs.
        # {{ source('schema','table') }} → call → strip.
        # {{ source }} → bare Name node → keep (user declared variable named 'source').
        #
        # Strategy: collect the object IDs of Name nodes used as call targets, then
        # find all Name nodes — those whose id is NOT in call_target_ids are bare refs.
        # Names that appear bare are NOT subtracted even if they are dbt builtins.
        call_target_ids = {
            id(node.node)
            for node in ast.find_all(jinja_nodes.Call)
            if isinstance(node.node, jinja_nodes.Name)
        }
        bare_names = {
            node.name
            for node in ast.find_all(jinja_nodes.Name)
            if id(node) not in call_target_ids
        }
        # Subtract only the dbt builtins that are NOT referenced bare.
        result -= _DBT_BUILTIN_CALLS - bare_names

        return result
    except TemplateSyntaxError:
        # Jinja syntax errors are expected here for invalid templates.
        # Return empty set - the actual render will catch and report the syntax error
        # with proper context. This is intentionally silent because this function
        # is only used for dependency detection, not validation.
        return set()


CellFormatter = Callable[[Any, str], str]  # type-state: explicit_any — DB cell values


def extract_query_result_references(template_str: str) -> set[str]:
    """Query names board text reads through ``queries.<name>`` / ``queries['<name>']``.

    Fenced code blocks are not Jinja and are skipped. A template that does not
    parse yields no references; rendering reports the syntax error.

    Raises:
        CompilationError: ``ERR-TEXT-QUERIES-NONLITERAL`` if ``queries`` appears
            in any form other than a literal-name access, so the set of queries
            a board reads is always known before it runs.
    """
    masked, _ = mask_fenced_code(template_str)
    if "queries" not in masked:
        return set()
    try:
        ast = Environment().parse(masked)
    except TemplateSyntaxError:
        return set()

    names: set[str] = set()
    literal_uses: set[int] = set()
    for attr_access in ast.find_all(jinja_nodes.Getattr):
        target = attr_access.node
        if isinstance(target, jinja_nodes.Name) and target.name == "queries":
            names.add(attr_access.attr)
            literal_uses.add(id(target))
    for item_access in ast.find_all(jinja_nodes.Getitem):
        target, key = item_access.node, item_access.arg
        if (
            isinstance(target, jinja_nodes.Name)
            and target.name == "queries"
            and isinstance(key, jinja_nodes.Const)
            and isinstance(key.value, str)
        ):
            names.add(key.value)
            literal_uses.add(id(target))
    for node in ast.find_all(jinja_nodes.Name):
        if node.name == "queries" and id(node) not in literal_uses:
            raise CompilationError.from_code(ERR_TEXT_QUERIES_NONLITERAL)
    return names


def text_query_references(texts: Iterable[str | None]) -> set[str]:
    """Query names read by ``queries.<name>`` in any of the given display texts."""
    names: set[str] = set()
    for text in texts:
        if text:
            names |= extract_query_result_references(text)
    return names


def template_literal_text(template_str: str) -> str:
    """The text a template draws regardless of data: its literal ``TemplateData``.

    Both branches of an ``if``/``else`` are kept, an upper bound since data picks
    the branch. A template that does not parse is returned as written; rendering
    reports the error.
    """
    try:
        ast = Environment().parse(template_str)
    except TemplateSyntaxError:
        return template_str
    return "".join(node.data for node in ast.find_all(jinja_nodes.TemplateData))


def text_needs_render_data(template_str: str) -> bool:
    """Whether board text reads query results, lists boards, or uses the
    render-only ``format``.

    Counts ``format`` passed by name (``map('format', ...)``) too. Text that does
    not parse counts as needing data.
    """
    masked, _ = mask_fenced_code(template_str)
    if "queries" not in masked and "format" not in masked and "glob_" not in masked:
        return False
    try:
        ast = Environment().parse(masked)
    except TemplateSyntaxError:
        return True
    return (
        any(
            node.name == "queries" or node.name in LISTING_FUNCTIONS
            for node in ast.find_all(jinja_nodes.Name)
        )
        or any(node.name == "format" for node in ast.find_all(jinja_nodes.Filter))
        or any(node.value == "format" for node in ast.find_all(jinja_nodes.Const))
    )


def resolve_jinja_template(
    template: str,
    variables: Mapping[str, Any] | None = None,
    queries: dict[str, Any] | None = None,
    strict: bool = True,
    filter_helpers: Mapping[str, Callable[..., str]]
    | None = None,  # type-state: optional — None is the ordinary case: only a caller that knows the target warehouse can bind these
    *,
    query_results: Mapping[str, QueryResult]
    | None = None,  # type-state: optional — only board text reads query results
    format_cell: CellFormatter
    | None = None,  # type-state: optional — travels with query_results
    listings: TextListings
    | None = None,  # type-state: optional — only board text lists boards
) -> str:
    """Resolve a Jinja template string.

    Stage: COMPILE (Step 3: Normalization - Jinja Resolution)

    Resolves {{ variable }} expressions in template strings.
    Also handles {{ queries.query_name }} references for SQL composition.

    Args:
        template: String potentially containing Jinja expressions
        variables: Variable values for substitution
        queries: Query registry for {{ queries.* }} resolution
        strict: If True (default), raises error on undefined variables.
                If False, undefined variables become None/empty (useful for
                chart editor where variables may not all be set).
        filter_helpers: If given, replaces the raising compile-time filter() /
                filter_date_range() stubs in the render context, for callers
                that can bind them. Jinja invokes them, so a span in a false
                {% if %} branch is never called, a span in a {% for %} is
                called once per iteration with that iteration's scope, and
                {{- -}} whitespace control applies natively.
        query_results: Board text only. The results ``queries.<name>`` reads, as
                ``queries`` (``QueryResults``) in the context. Requires
                ``strict=False``; never combined with ``queries=``.
        format_cell: Required with ``query_results``; replaces the ``format``
                filter.
        listings: Board text only, with ``query_results``: what
                ``glob_boards``/``glob_folders`` return, resolved by the data provider.

    Returns:
        Resolved string with variables substituted

    Raises:
        JinjaError: If template syntax is invalid or (if strict) variable not found
        CompilationError: If filter_helpers binds a name other than
                RESERVED_FILTER_HELPER_NAMES
        ChartDataError: A ``query_results`` access error, or an ordering
                comparison on a NULL value (``ERR-TEXT-NULL-COMPARISON``)
        ValueError: If only one of ``query_results``/``format_cell`` is given, or
                ``query_results`` is combined with ``queries`` or ``strict=True``

    Example:
        >>> resolve_jinja_template(
        ...     "SELECT * FROM users WHERE status = '{{ status }}'",
        ...     variables={"status": "active"}
        ... )
        "SELECT * FROM users WHERE status = 'active'"
    """
    if (query_results is None) != (format_cell is None):
        raise ValueError("query_results and format_cell must be given together")
    if query_results is not None and queries:
        raise ValueError("query_results cannot be combined with queries")
    if query_results is not None and strict:
        raise ValueError("query_results requires strict=False: board text is lenient")
    if listings is not None and query_results is None:
        raise ValueError("listings requires query_results: only board text lists")

    if not template:
        return template

    # Quick check for Jinja syntax
    if "{{" not in template and "{%" not in template:
        return template

    variables = variables or {}
    queries = queries or {}

    # Build context with variables and query helper
    context = {**variables}

    # Add queries namespace for {{ queries.query_name }} resolution
    if queries:
        context["queries"] = _QueryNamespace(queries)
    if query_results is not None:
        context["queries"] = QueryResults(query_results)
    if listings is not None:
        context.update(listing_helpers(listings))

    # Add helper functions. The stubs raise; a caller able to bind them — one
    # that knows the warehouse the SQL will run on — supplies real ones.
    context["filter"] = _filter_helper
    context["filter_date_range"] = _filter_date_range_helper
    if filter_helpers:
        unreserved = set(filter_helpers) - RESERVED_FILTER_HELPER_NAMES
        if unreserved:
            raise CompilationError(
                f"filter_helpers may only bind {sorted(RESERVED_FILTER_HELPER_NAMES)}, "
                f"got unexpected name(s): {sorted(unreserved)}"
            )
        context.update(filter_helpers)

    # Use strict or lenient environment
    jinja_env = _jinja_env if strict else _jinja_env_lenient
    if format_cell is not None:
        jinja_env = _jinja_env_text.overlay()
        jinja_env.filters = {**jinja_env.filters, "format": format_cell}

    try:
        jinja_template = jinja_env.from_string(template)
        result = render_with_budget(jinja_template, context)
    except TemplateOutputBudgetExceeded as e:
        raise TemplateOutputTooLargeError(
            emitted_bytes=e.emitted_bytes, ceiling=e.ceiling
        ) from e
    except UndefinedError as e:
        raise JinjaError(
            f"Undefined variable: {e}{template_error_location(template, e)}", template
        ) from e
    except TemplateSyntaxError as e:
        raise JinjaError(
            f"Template syntax error: {e}{template_error_location(template, e)}",
            template,
        ) from e
    except TemplateError as e:
        raise JinjaError(f"Template error: {e}", template) from e
    except TypeError as e:
        if query_results is None:
            raise
        if _is_none_ordering_error(e):
            raise ChartDataError.from_code(ERR_TEXT_NULL_COMPARISON) from e
        raise ChartDataError.from_code(ERR_TEXT_TYPE_ERROR, detail=str(e)) from e
    except ValueError as e:
        # The filter helpers validate their own arguments (identifier, operator
        # allowlist, none=, date_range shape) and raise ValueError from inside
        # the render. Unwrapped it reaches the executor uncoded and stamps
        # ERR-INTERNAL, which this repo treats as a bug rather than an
        # author-facing error.
        raise JinjaError(str(e), template) from e

    return result


def _is_none_ordering_error(error: TypeError) -> bool:
    message = str(error)
    return "not supported between instances of" in message and "'NoneType'" in message


def extract_sql_variable_dependencies(sql: str, setup_sql: str | None) -> set[str]:
    """Variables named by a SQL query's own `sql` and `setup_sql`."""
    deps = extract_variable_dependencies(sql)
    if setup_sql:
        deps |= extract_variable_dependencies(setup_sql)
    return deps


def detect_query_dependencies(queries: dict[str, Any]) -> dict[str, list[str]]:
    """Detect dependencies between queries for circular reference detection.

    Scans query SQL for {{ queries.* }} references and builds a dependency graph.
    Raises an error if circular dependencies are detected.

    Args:
        queries: Dictionary of query definitions

    Returns:
        Dictionary mapping query name to list of dependencies

    Raises:
        JinjaError: If circular dependencies detected
        CompilationError: If a referenced query name is a reserved SQL word
    """
    dependencies: dict[str, list[str]] = {}

    for name, query in queries.items():
        deps: list[str] = []

        # Get SQL content
        sql = None
        if hasattr(query, "sql"):
            sql = query.sql
        elif isinstance(query, dict):
            sql = query.get("sql")

        if sql:
            # Find {{ queries.NAME }} inline refs
            matches = _QUERY_REF_RE.findall(sql)
            deps.extend(matches)
            # Find {{ queries.NAME.cache }} cache-read refs — same dependency
            # semantics: NAME must exist and must be executed before the composer.
            cache_matches = _CACHE_REF_RE.findall(sql)
            deps.extend(cache_matches)
            # Every referenced name becomes a SQL table alias — fail fast on
            # reserved-word names before they reach the database.
            for ref_name in deps:
                _validate_query_ref_name(ref_name)

        dependencies[name] = deps

    # Check for circular dependencies
    _detect_circular(dependencies)

    return dependencies


def check_dialect_composition(queries: Mapping[str, AnyQuery]) -> None:
    """Enforce the compile-time rules for queries that set `dialect:`.

    Every `{{ queries.X }}` embedding must share its host's dialect, an unset
    `dialect:` counting as a value. A `{{ queries.X.cache }}` reference runs the
    composing SQL on DuckDB, so its `dialect:` must be duckdb or unset.

    Raises:
        CompilationError: ERR-QUERY-DIALECT-MIXED or ERR-SQL-DIALECT-UNSUPPORTED
            on the first violation.
    """
    for name, query in queries.items():
        if not is_sql_query(query):
            continue
        if query.dialect not in (None, "duckdb") and _CACHE_REF_RE.search(query.sql):
            raise CompilationError.from_code(
                ERR_SQL_DIALECT_UNSUPPORTED,
                read=query.dialect,
                write="duckdb",
                reason=(
                    f"query {name!r} reads {{{{ queries.X.cache }}}}, which "
                    "composes over cached rows in duckdb, so write it in duckdb"
                ),
            )
        for dep_name in _QUERY_REF_RE.findall(query.sql):
            dep = queries.get(dep_name)
            if dep is not None and is_sql_query(dep) and dep.dialect != query.dialect:
                raise CompilationError.from_code(
                    ERR_QUERY_DIALECT_MIXED,
                    query_name=name,
                    dialect=query.dialect
                    or "unset",  # type-state: silent_fallback — display label in the error, not a value used downstream
                    dep_name=dep_name,
                    dep_dialect=dep.dialect
                    or "unset",  # type-state: silent_fallback — display label in the error, not a value used downstream
                )


def _detect_circular(dependencies: dict[str, list[str]]) -> None:
    """Detect circular dependencies using DFS.

    Args:
        dependencies: Dependency graph

    Raises:
        JinjaError: If circular dependency found
    """
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str, path: list[str]) -> None:
        if node in rec_stack:
            cycle = path[path.index(node) :] + [node]
            raise JinjaError(
                f"Circular query dependency detected: {' -> '.join(cycle)}"
            )

        if node in visited:
            return

        visited.add(node)
        rec_stack.add(node)

        for dep in dependencies.get(node, []):
            if dep in dependencies:  # Only follow known queries
                dfs(dep, path + [node])

        rec_stack.remove(node)

    for query_name in dependencies:
        dfs(query_name, [])


def _topological_sort(dependencies: dict[str, list[str]]) -> list[str]:
    """Return query names in topological order (dependencies first).

    Args:
        dependencies: Dependency graph from detect_query_dependencies()

    Returns:
        List of query names with leaf nodes first
    """
    visited: set[str] = set()
    order: list[str] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        for dep in dependencies.get(name, []):
            if dep in dependencies:
                visit(dep)
        order.append(name)

    for name in dependencies:
        visit(name)

    return order


def _get_query_sql(query: Any) -> str:
    """Extract a query's raw SQL body (before any subquery wrapping)."""
    if hasattr(query, "sql"):
        sql = query.sql
        return sql if isinstance(sql, str) else ""
    elif isinstance(query, dict):
        sql = query.get("sql", "")
        return sql if isinstance(sql, str) else ""
    return str(query)


def _substitute_query_refs(sql: str, resolved: dict[str, str]) -> str:
    """Replace {{ queries.X }} tokens with X's resolved SQL as an aliased subquery.

    Each reference expands to ``(<resolved sql>) AS X`` — parenthesized so it is a
    valid subquery and aliased to the query name so the same authored SQL runs on
    both the DuckDB cache engine and Cloud Postgres (which requires the alias).
    This matches _QueryProxy.__str__. Because the expansion carries an alias, a
    reference is only valid in **FROM/JOIN (table) position** — using it as a CTE
    body (``WITH t AS ({{ queries.X }})``) or a scalar subquery emits invalid SQL.
    Uses regex substitution so that only query references are expanded; variable
    expressions ({{ var }}) and other Jinja constructs are left untouched for the
    single parameterized render on the way to the warehouse.
    """

    def _replacer(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in resolved:
            return f"({resolved[name]}) AS {name}"
        return m.group(0)  # leave unresolved if not in map

    return _QUERY_REF_RE.sub(_replacer, sql)


def expand_query_refs(query_name: str, queries: dict[str, Any]) -> str:
    """Expand a query's {{ queries.* }} references, recursively, as text.

    Each reference is replaced by its target's own already-expanded SQL, in
    topological order, so a query composed of a query composed of a query
    resolves all the way down. Nothing else is touched: ``{{ variable }}``,
    ``{{ filter(...) }}`` and every other Jinja construct come out exactly as the
    author wrote them.

    Leaving them is the point. The SQL returned here still goes through the
    parameterized render on its way to the warehouse, and that render must be the
    only one — a variable value substituted here would be sitting in template
    position when the next pass read it, and values come from URL query
    parameters. Expanding references is the one thing that render cannot do for
    itself, because the query namespace it resolves against inlines a query's raw
    SQL without recursing.

    Args:
        query_name: The target query to expand
        queries: Full query registry (name -> query object or dict)

    Returns:
        The target query's SQL with every query reference expanded

    Raises:
        JinjaError: If circular dependencies are detected.
        CompilationError: If a referenced query name is a SQL reserved word
            (both via detect_query_dependencies).
    """
    # Build dependency graph (validates no cycles), then walk leaves-first so
    # each query expands against dependencies that are themselves expanded.
    deps = detect_query_dependencies(queries)

    expanded: dict[str, str] = {}
    for name in _topological_sort(deps):
        if name not in queries:
            continue
        raw_sql = _get_query_sql(queries[name])
        if raw_sql and ("{{ queries." in raw_sql or "{{queries." in raw_sql):
            expanded[name] = _substitute_query_refs(raw_sql, expanded)
        else:
            expanded[name] = raw_sql

    return expanded.get(query_name, _get_query_sql(queries.get(query_name, "")))


def _filter_helper(*args: Any, **kwargs: Any) -> str:
    """Raise CompilationError — the string-interpolation filter() is no longer supported.

    The parameterized {{ filter(...) }} helper (parameterized.py) replaces it.
    Most templates that reach this stub are not queries at all — chart titles,
    layout and sizing expressions — so the actionable message is where filter()
    does work, not a claim about this particular caller.
    """
    raise CompilationError(
        "The 'filter' Jinja helper uses string interpolation and is no longer supported. "
        "{{ filter(...) }} works only in query SQL; it cannot be used in this "
        "template."
    )


def _filter_date_range_helper(*args: Any, **kwargs: Any) -> str:
    """Raise CompilationError — the string-interpolation filter_date_range() is no longer supported.

    Migrate to {{ filter_date_range(...) }} from the parameterized helper in parameterized.py.
    Same as _filter_helper: name where the helper works rather than describe the
    caller, since most templates reaching this stub are not queries.
    """
    raise CompilationError(
        "The 'filter_date_range' Jinja helper uses string interpolation and is no longer "
        "supported. {{ filter_date_range(...) }} works only in query SQL; it cannot "
        "be used in this template."
    )
