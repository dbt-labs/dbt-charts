"""Profile rendering module.

Stage: INSPECT
Purpose: Shared rendering logic for inspect dashboards.

This module provides the core rendering function used by both:
- CLI: `dct inspect <table> --format html`
- Server: `dct serve` (via core/serve/server.py)

The renderer substitutes string {{ }} vars in the template YAML, then
compiles and renders it as HTML. Schema-shaped panels query the
LayeredSchemaResolver in-process via the `schema` board query type.

M2 contract: templates contain only string {{ }} vars — no document-level
{% %} Jinja. Substitution uses simple string replacement for the declared
set of inspect vars; the schema compile validator enforces that no
{{ }} remain after substitution.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile import (
    CompileResult,
    compile,
)
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
from dbt_charts.core.inspect.cache_factory import make_cache_source_for_project
from dbt_charts.core.inspect.resolver import LayeredSchemaResolver
from dbt_charts.core.render import render

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject

# Valid identifier pattern for SQL safety
_VALID_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Preferred source name for inspect templates when a project configures more
# than one — an explicit project source named `warehouse` always wins.
_DEFAULT_SOURCE_NAME = "warehouse"

# The vars the renderer substitutes directly into the template YAML before
# compile(). schema fields reject {{ }} Jinja, so these must be
# literal strings in the compiled YAML. The set is fixed and declared here;
# adding a new schema field requires adding it here.
_SCHEMA_VARS = ("model", "column", "source_name", "schema_name")


class InspectProfileCompileError(Exception):
    """Inspect template failed compilation."""

    def __init__(self, result: CompileResult) -> None:
        self.result = result
        error_msg = (
            "; ".join(e.message for e in result.errors)
            if result.errors
            else "Unknown error"
        )
        super().__init__(f"Failed to compile profile template: {error_msg}")


def validate_inspect_variables(variables: dict[str, Any]) -> dict[str, Any]:
    """Validate and sanitize profile variables.

    Ensures variables are safe to use in SQL queries by:
    - Checking that model/column names are valid SQL identifiers
    - Converting all values to strings
    - Rejecting potentially dangerous inputs

    Args:
        variables: Raw variables from URL params or CLI

    Returns:
        Sanitized variables (all string values)

    Raises:
        ValueError: If variables contain invalid values
    """
    sanitized: dict[str, Any] = {}

    for key, value in variables.items():
        # Convert to string
        str_value = str(value) if value is not None else ""

        # Validate SQL identifiers (model, column names)
        if (
            key in ("model", "column")
            and str_value
            and not _VALID_IDENTIFIER.match(str_value)
        ):
            raise ValueError(
                f"Invalid {key} name: '{str_value}'. "
                "Must be a valid SQL identifier (letters, numbers, underscores)."
            )

        sanitized[key] = str_value

    # The renderer always overrides ``source_name`` / ``schema_name`` from
    # project-root below. Drop any caller-supplied values so a URL like
    # ``?schema_name=anything-goes`` can't smuggle a string into the rendered YAML.
    for stripped_key in ("source_name", "schema_name"):
        sanitized.pop(stripped_key, None)

    return sanitized


def _substitute_template_vars(template_yaml: str, vars: dict[str, str]) -> str:
    """Substitute declared inspect vars into the template YAML.

    Only substitutes the vars named in ``_SCHEMA_VARS``; the
    schema compile validator rejects any remaining ``{{ }}`` Jinja
    in its fields, so unknown vars cause a compile error rather than silent
    pass-through.

    Args:
        template_yaml: Raw template YAML with ``{{ var_name }}`` placeholders.
        vars: Resolved variable values (all string).

    Returns:
        Template YAML with placeholders replaced by literal values.
    """
    result = template_yaml
    for key in _SCHEMA_VARS:
        value = vars.get(key, "")
        result = result.replace("{{ " + key + " }}", value)
        result = result.replace("{{" + key + "}}", value)
    return result


def _resolve_table_schema(
    resolver: LayeredSchemaResolver,
    source_name: str,
    model_name: str,
) -> str:
    """Return the schema containing ``model_name``.

    Two-pass strategy that keeps the render path safe for every dialect:

    Pass 1 — cache only (via ``resolver.cache``): no adapter calls, works for
    every dialect including non-DuckDB warehouses where the adapter may refuse
    to connect. Returns immediately when the cache has the answer.

    Pass 2 — full resolver walk (via ``resolver.list_schemas`` /
    ``list_tables``): runs only when the cache pass found nothing. The resolver
    uses cache-first per-target logic, so cached tables are still served without
    a second adapter round-trip. Adapter calls happen here for cold-start DuckDB
    tables that were never profiled.

    First-match semantics: cache iteration order, then dbt-schema order.

    Args:
        resolver: LayeredSchemaResolver that owns both the cache and the adapter.
        source_name: Named source registered with the adapter registry.
        model_name: Bare table/model name to look up.

    Returns:
        Schema name string, or ``""`` when no match is found or the source is
        unreachable.
    """
    if not source_name:
        return ""

    def _safe_schema(name: str) -> str:
        """Return ``name`` only when it's a valid SQL identifier, else ``""``."""
        return name if name and _VALID_IDENTIFIER.match(name) else ""

    # Cache-first pass: when a warm SuperSchemaSource is available, look up the
    # schema without adapter calls. This path is only hit when the caller injects
    # a cache (e.g. Cloud/IDE with dbt-charts-super-schema). The OSS renderer builds
    # without a cache (cache=None), so this block is skipped in the OSS path.
    if resolver.cache is not None:
        cache_schemas = resolver.cache.list_schemas()
        if cache_schemas is not None:
            for schema in cache_schemas.get("schemas", {}):
                cache_tables = resolver.cache.list_tables(schema)
                if cache_tables is not None and model_name in cache_tables.get(
                    "tables", {}
                ):
                    return _safe_schema(schema)

    # Adapter walk: needed for cold start or when no cache is present.
    try:
        schemas_result = resolver.list_schemas(source_name)
    except Exception:  # noqa: BLE001 — renderer best-effort; fall back to ""
        return ""
    schema_names = list(
        schemas_result.get("sources", {}).get(source_name, {}).get("schemas", {})
    )
    for schema in schema_names:
        try:
            tables_result = resolver.list_tables(source_name, schema)
        except Exception:  # noqa: BLE001 — renderer best-effort; per-schema
            continue
        tables = (
            tables_result.get("sources", {})
            .get(source_name, {})
            .get("schemas", {})
            .get(schema, {})
            .get("tables", {})
        )
        if model_name in tables:
            return _safe_schema(schema)
    return ""


def resolve_source_name(adapter_registry: AdapterRegistry) -> str:
    """Pick the source the inspect templates query for schema-shaped data."""
    sources = adapter_registry.list_sql_sources()
    if not sources:
        return ""
    for entry in sources:
        if entry["name"] == _DEFAULT_SOURCE_NAME:
            return _DEFAULT_SOURCE_NAME
    return str(sources[0]["name"])


def render_inspect_dashboard(
    template_yaml: str,
    variables: dict[str, Any],
    *,
    project: FilesystemProject,
    adapter_registry: AdapterRegistry,
    standalone: bool = False,
    source_name: str | None = None,
) -> str:
    """Render a profile template to HTML.

    Substitutes string {{ }} vars into the template YAML (M2: no block-tag
    Jinja), compiles it, and renders it as HTML. ``source_name`` and
    ``schema_name`` are resolved from the LayeredSchemaResolver (which uses
    the cache when present, falls through to dbt/adapter when absent);
    caller-supplied values for those keys are silently dropped to prevent URL
    parameter injection.

    A pure function of a *configured* registry — the caller builds the
    ``AdapterRegistry`` from the project's ``dbt_charts.yml`` sources before
    calling this function. This function only resolves the source name (via
    ``resolve_source_name``) and renders; a project with no configured
    sources renders with an empty ``source_name``, and the schema/SQL
    queries raise ``ERR-SOURCE-NOT-FOUND-EMPTY`` at execute time.

    Local-only surface: callers are ``dct serve`` and the super-schema CLI.
    Cloud renders inspector views through its own path and must not call this
    with a non-filesystem project.

    Args:
        template_yaml: YAML template content with string {{ }} placeholders
        variables: Variables to substitute in the template
        project: Project for resolving sources and relative paths
        adapter_registry: Pre-configured registry to resolve/execute against
        source_name: The source to query, when the caller names one. None
            picks the registry's default via ``resolve_source_name``.
        standalone: Output will be opened with no host, so carry font bytes
            inline rather than naming ``/static/fonts/...`` (the super-schema
            CLI's case). ``dct serve`` leaves this at the default — it already
            mounts the prefix, and embedding would add ~0.5 MiB of base64 to
            every served page.

    Returns:
        Rendered HTML string

    Raises:
        InspectProfileCompileError: If compilation fails
    """
    safe_vars = validate_inspect_variables(variables)
    safe_vars["source_name"] = (
        source_name
        if source_name is not None
        else resolve_source_name(adapter_registry)
    )

    # Use warm cache when dbt-charts-super-schema is installed; fall back to
    # dbt-only resolver (cache=None) when it is not. The cache enriches
    # schema panels for non-DuckDB warehouses where the adapter
    # can't connect; without the private package, those panels degrade.
    cache = make_cache_source_for_project(project)
    resolver = LayeredSchemaResolver(
        cache=cache,
        adapter_registry=adapter_registry,
        project=project,
    )
    model_name = safe_vars.get("model", "")
    safe_vars["schema_name"] = (
        _resolve_table_schema(resolver, safe_vars["source_name"], model_name)
        if model_name
        else ""
    )

    # Substitute the schema vars into the YAML before compile().
    # schema fields reject {{ }} Jinja — they must be literal strings.
    substituted_yaml = _substitute_template_vars(template_yaml, safe_vars)

    result = compile(
        substituted_yaml,
        project_sources=adapter_registry.project_sources,
        project_cache=project.cache,
    )
    if result.errors or not result.board:
        raise InspectProfileCompileError(result=result)

    executor = Executor(
        result.board,
        adapter_registry=adapter_registry,
        query_registry=result.query_registry,
    )

    html_output = render(
        result.board,
        executor,
        format="html",
        variables=safe_vars,
        standalone=standalone,
    ).output

    if html_output is None:
        return ""
    if isinstance(html_output, bytes):
        return html_output.decode("utf-8")
    return html_output
