"""Registered view expansion pipeline.

Purpose: Given a matched registered view and pre-executed query results,
         render the view's YAML template and return a normal AuthoredBoard.

Public API:
- ``load_template(rel_path)`` — read a template file from the package.
- ``render_template(text, path_params, query_results, resolve_dialect=...)`` —
  render using non-conflicting delimiters so board-SQL Jinja passes through
  unchanged.
- ``expand_registered_view(match, query_results, resolve_dialect=...)`` — full
  pipeline: load template, render, parse into AuthoredBoard.

Template delimiters use non-conflicting tokens so board-SQL Jinja passes
through unchanged:

- ``[[`` / ``]]`` — variable expressions (instead of ``{{`` / ``}}``)
- ``[%`` / ``%]`` — block statements (instead of ``{%`` / ``%}``)
- ``[#`` / ``#]`` — comments (instead of ``{#`` / ``#}``)

Standard Jinja ``{{`` / ``}}`` and ``{%`` / ``%}`` tokens are left untouched
so board-SQL query references survive to the compile stage.

Template context:
- ``path`` — dict of route path params; ``[[ path.source ]]`` resolves via
  Jinja's normal attribute/key lookup. ``StrictUndefined`` raises on missing keys.
- ``queries`` — dict keyed by query name; each value is a QueryResult
  with ``.rows``, ``.one``, ``.columns``. Rows are QueryRow: cells by column
  name (``row.name`` / ``row['name']``), raising on an unknown column.

Template globals:
- ``plan_variables(rows)`` — takes a list of column-query rows (each with ``name``
  and ``actual_type`` keys) and returns a list of ``{"name", "input"}`` dicts for
  the default-selected variable set (boolean/string/temporal; not numeric or complex).
  Wraps ``plan_entity_variables`` so templates avoid hand-coding the type-filter logic.
- ``plan_key_variables(rows)`` — like ``plan_variables`` but for row *identity*:
  identifier-named columns whose type round-trips exactly through a URL equality
  param (integers, exact decimals, strings). The detail view and the
  index→detail link use this so a numeric ``id`` primary key actually selects one
  row, where the browse-filter set would have dropped it.
- ``sql_identifier(name)`` — rejects ``name`` if it contains a quote,
  backslash, bracket, or control character (untrusted URL path params reach
  this — see core/AGENTS.md "Two validation boundaries"), then returns it safely quoted
  in the matched source's dialect (backticks on BigQuery/MySQL, brackets on
  SQL Server, double quotes elsewhere). Bound per-render from a dialect
  resolver passed into ``render_template`` — use for any path param that
  appears inside a SQL ``FROM`` or column reference.
- ``pivot_column_profiles(cols, characteristics)`` /
  ``column_header_styles(names, source, schema, table)`` — the inspector
  table page's describe-style transpose (one row per characteristic, one
  column per data column) and its per-column header-link styling, done in
  Python rather than a template-side ``list.append`` loop: the sandboxed
  environment below blocks mutating a known-mutable container.
"""

from __future__ import annotations

import functools
import pathlib
from collections.abc import Callable
from importlib.resources import files
from typing import TYPE_CHECKING

from jinja2 import (
    StrictUndefined,
    TemplateSyntaxError,
    UndefinedError,
)

from dbt_charts.core.compile.sql_guard import sqlglot_dialect
from dbt_charts.core.compile.template.environment import BoardTemplateEnvironment
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.dialects.identifier import sql_identifier

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard
    from dbt_charts.core.compile.template.query_result import QueryResult, QueryRow
    from dbt_charts.core.registered_views.router import RouteMatch


class TemplateLoadError(Exception):
    """Raised when a template file cannot be found or loaded."""


class ExpansionError(Exception):
    """Raised when view-template rendering or board parsing fails.

    Always includes the template path and the underlying cause so authors
    can locate both the template source and the generated output location.
    """


# ---------------------------------------------------------------------------
# Template rendering environment
# ---------------------------------------------------------------------------

# Use [[ / ]] and [% / %] to avoid colliding with board-SQL {{ / }} and {% / %}.
# Board-SQL Jinja is evaluated AFTER expansion, at the compile/execute boundary.
_TEMPLATE_ENV = BoardTemplateEnvironment(
    undefined=StrictUndefined,
    variable_start_string="[[",
    variable_end_string="]]",
    block_start_string="[%",
    block_end_string="%]",
    comment_start_string="[#",
    comment_end_string="#]",
    keep_trailing_newline=True,
)


def _plan_variables(
    rows: list[QueryRow],
) -> list[dict[str, str]]:
    """Convert column-query rows into a list of variable dicts for template iteration.

    Wraps ``plan_entity_variables`` so templates can call this global instead of
    hand-looping with fragile per-type conditionals.  Returns a list of
    ``{"name": <col_name>, "input": <input_type>}`` dicts — one per column that
    passes the default filter selection (boolean/string/temporal; not numeric or
    complex).

    Args:
        rows: Rows from a ``type: schema`` registry query (each row has ``name``
            and ``actual_type`` keys).

    Returns:
        List of ``{"name": col_name, "input": input_type}`` for selected columns.
        Empty list when no columns pass the selection or when ``rows`` is empty.
    """
    from dbt_charts.core.registered_views.variable_planner import (
        PlannerColumn,
        plan_entity_variables,
    )

    all_cols = [
        PlannerColumn(name=r["name"], actual_type=r["actual_type"]) for r in rows
    ]
    # Auto-browse path: skip columns whose names aren't valid variable ids.
    # Non-identifier names (spaces, dashes, leading digits) are common in real
    # warehouses; raising here would 500 the entity table page.
    identifier_cols = [c for c in all_cols if c.is_valid_variable_id]
    variables = plan_entity_variables(identifier_cols)
    return [{"name": name, "input": var.input} for name, var in variables.items()]


_TEMPLATE_ENV.globals["plan_variables"] = _plan_variables


def _plan_key_variables(
    rows: list[QueryRow],
) -> list[dict[str, str]]:
    """Like ``plan_variables`` but selects row-identity *key* columns, not filters.

    The detail view and the index→detail link need the columns that identify a
    single row — a different selection from the browse-filter set. A typical
    primary key is a numeric ``id`` (dropped from the filter set as filter-control
    noise) yet is exactly what selects one row. Selection is restricted to types
    that round-trip exactly through a URL equality param (see
    ``PlannerColumn.is_identity_keyable``): integers/exact-decimals/strings in,
    floats/temporals/booleans/complex out. This is not true PK detection — it is
    "every column that can safely be an equality key"; on a table with no such
    column the detail SQL is just ``WHERE 1=1 ... LIMIT 2``, so the page shows up
    to two arbitrary rows.
    """
    from dbt_charts.core.registered_views.variable_planner import (
        PlannerColumn,
        plan_entity_variables,
    )

    all_cols = [
        PlannerColumn(name=r["name"], actual_type=r["actual_type"]) for r in rows
    ]
    # Pre-filter to valid-id + keyable here, so plan_entity_variables' own
    # InvalidColumnNameError branch can't fire from this caller.
    key_names = {
        c.name for c in all_cols if c.is_valid_variable_id and c.is_identity_keyable
    }
    variables = plan_entity_variables(all_cols, include=key_names)
    return [{"name": name, "input": var.input} for name, var in variables.items()]


_TEMPLATE_ENV.globals["plan_key_variables"] = _plan_key_variables


def _pivot_column_profiles(
    cols: list[QueryRow],
    characteristics: list[list[str]],
) -> list[list[str | None]]:
    """Transpose column-profile rows into a describe-style table: one row per
    characteristic (from ``characteristics``, each a ``[label, field]`` pair),
    one column per entry in ``cols``. A column holds a type name, a percentage
    and a count, so cells are text formatted here: a values column declares one
    kind, so no column format can apply.
    """
    # QueryRow has no .get, and profile rows carry only the fields a source knows.
    return [
        [label]
        + [
            None if field not in c or c[field] is None else _profile_text(c[field])
            for c in cols
        ]
        for label, field in characteristics
    ]


def _profile_text(value: str | int | float) -> str:
    if isinstance(value, (bool, str)):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


_TEMPLATE_ENV.globals["pivot_column_profiles"] = _pivot_column_profiles


def _column_header_styles(
    names: list[str], source: str, schema: str, table: str
) -> dict[str, dict[str, str]]:
    """Per-column table style: the label column right-aligned, every data
    column's header linked to its own inspector column page.
    """
    styles: dict[str, dict[str, str]] = {"Attribute": {"align": "right"}}
    for name in names:
        styles[name] = {"header_link": f"/inspector/{source}/{schema}/{table}/{name}/"}
    return styles


_TEMPLATE_ENV.globals["column_header_styles"] = _column_header_styles


# ---------------------------------------------------------------------------
# load_template
# ---------------------------------------------------------------------------


def load_template(rel_path: str) -> str:
    """Load a YAML template file from the registered_views package.

    Args:
        rel_path: Relative path within the templates directory, e.g.
            ``'data/table-index.yaml'``. Must be relative (no leading
            ``/``) and must not contain ``..`` components.

    Returns:
        Raw template text (not yet rendered).

    Raises:
        TemplateLoadError: If the path is absolute, contains traversal
            components, or the file does not exist in the package templates
            directory.
    """
    # Validate containment via the relpath's own segments — no filesystem
    # resolution needed. `files()` returns a Traversable, and `str(Traversable)`
    # is not a guaranteed-stable path (breaks under zip installs), so
    # containment must be checked before joinpath, on the string itself.
    path = pathlib.PurePosixPath(rel_path)
    if path.is_absolute():
        raise TemplateLoadError(
            f"Template path must be relative, not absolute: {rel_path!r}. "
            "Template paths must be relative within the templates directory."
        )
    if not path.parts or ".." in path.parts:
        raise TemplateLoadError(
            f"Template path is empty or contains '..' traversal components: {rel_path!r}. "
            "Template paths must be relative within the templates directory."
        )

    templates_pkg = files("dbt_charts.core.registered_views").joinpath("templates")
    template_file = templates_pkg.joinpath(rel_path)

    try:
        return template_file.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TemplateLoadError(
            f"Template not found: {rel_path!r}. "
            "Check the template path in the view registry."
        ) from exc
    except OSError as exc:
        raise TemplateLoadError(f"Failed to read template {rel_path!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------


def render_template(
    text: str,
    path_params: dict[str, str],
    query_results: dict[str, QueryResult],
    resolve_dialect: Callable[[], str | None] = lambda: None,
) -> str:
    """Render a view template with path params and query results in context.

    Uses non-standard delimiters (``[[``/``]]``, ``[%``/``%]``) so that
    board-SQL Jinja tokens (``{{``/``}}``, ``{%``/``%}``) pass through
    unchanged to the later compile stage.

    Args:
        text: Raw template text (loaded via ``load_template``).
        path_params: Route path params, e.g. ``{"source": "snowflake"}``.
        query_results: Pre-executed query results keyed by name.
        resolve_dialect: Zero-arg callable returning the matched source's
            dialect for ``sql_identifier()`` quoting, or ``None`` for ANSI
            double-quoting. Called at most once, lazily, the first time the
            template invokes ``sql_identifier()`` — a template that never
            calls it never triggers source resolution (``render_pipeline.py``
            builds a resolver that only touches the adapter registry on
            first call, so index-level routes with no identifier quoting to
            do stay free). Defaults to a resolver that always returns
            ``None``, for templates and callers that don't quote identifiers.

    Returns:
        Rendered YAML string ready to parse as an ``AuthoredBoard``.

    Raises:
        ExpansionError: If the template has a syntax error, references an
            absent path param, or reads a query result wrongly (unknown
            column, ``.one`` without exactly one row), an identifier fails
            validation, or ``resolve_dialect`` itself fails to resolve a
            usable dialect.
    """
    try:
        template = _TEMPLATE_ENV.from_string(text)
    except TemplateSyntaxError as exc:
        raise ExpansionError(f"Template syntax error: {exc}") from exc

    # Memoized, so the "called at most once" contract above holds however many
    # sql_identifier() calls a template makes — inspector/column.yaml has 37.
    resolve_once = functools.lru_cache(maxsize=1)(resolve_dialect)

    def _sql_identifier_bound(name: str) -> str:
        try:
            return sql_identifier(name, sqlglot_dialect(resolve_once()))
        except ValueError as exc:
            raise ExpansionError(str(exc)) from exc

    try:
        return template.render(
            path=path_params,
            queries=query_results,
            sql_identifier=_sql_identifier_bound,
        )
    except (UndefinedError, ChartDataError) as exc:
        raise ExpansionError(f"Template rendering failed: {exc}") from exc


# ---------------------------------------------------------------------------
# expand_registered_view
# ---------------------------------------------------------------------------


def expand_registered_view(
    match: RouteMatch,
    query_results: dict[str, QueryResult],
    resolve_dialect: Callable[[], str | None] = lambda: None,
) -> AuthoredBoard:
    """Expand a matched registered view into a normal authored board.

    Pipeline:
    1. Load the template text from the package.
    2. Render it with ``path.*`` and ``queries.<name>`` in context.
    3. Parse the rendered YAML into an ``AuthoredBoard`` using the normal
       board parser (same path as authored YAML files).

    After this function returns, the generated board is indistinguishable
    from an authored board and passes through the normal compile/validate/
    query/render pipeline unchanged.

    Args:
        match: A successful route match containing the view definition and
            extracted path params.
        query_results: Pre-executed registry query results keyed by name
            (typically from ``run_registry_queries``). Pass ``{}`` when the
            view declares no ``queries:``.
        resolve_dialect: Zero-arg callable resolving the matched source's
            dialect, threaded through to ``render_template`` — see its
            docstring for the laziness contract. Defaults to a resolver that
            always returns ``None`` (no dialect-correct quoting).

    Returns:
        Parsed ``AuthoredBoard`` ready for validation and compilation.

    Raises:
        TemplateLoadError: If the template file cannot be found.
        ExpansionError: If template rendering fails (bad template syntax,
            missing path param, missing query result, an identifier that
            fails validation, or a source dialect that can't be resolved) or
            if the rendered output is not valid board YAML. The template path
            is always included in the error message.
    """
    template_path = match.view.template

    # Step 1: Load the template text.
    template_text = load_template(template_path)

    # Step 2: Render the template.
    # Re-raise with the template path so the error is actionable; use a
    # distinct prefix ("Failed to render") to avoid repeating the inner
    # "Template rendering failed" text from render_template.
    try:
        rendered_yaml = render_template(
            template_text,
            path_params=match.path_params,
            query_results=query_results,
            resolve_dialect=resolve_dialect,
        )
    except ExpansionError as exc:
        raise ExpansionError(
            f"Failed to render template {template_path!r}: {exc}"
        ) from exc

    # Step 3: Parse rendered YAML into an AuthoredBoard.
    from dbt_charts.core.compile.parse.parser import ParseError, parse_yaml

    try:
        return parse_yaml(rendered_yaml)
    except ParseError as exc:
        raise ExpansionError(
            f"Generated board from template {template_path!r} is not valid board YAML: {exc}"
        ) from exc
