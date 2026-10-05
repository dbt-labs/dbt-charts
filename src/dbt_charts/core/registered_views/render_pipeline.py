"""Registered-view render pipeline — core API.

Purpose: Execute the full registered-view pipeline (match → query → expand →
         compile → execute → render) and return a typed success/error result.

This is a pure-core function. It knows nothing about HTTP: no Response, no
FastAPI, no starlette. The server handler in serve/server.py calls this and
maps the typed result to HTTP responses.

Public API:
- ``RenderSuccess`` — successful render; carries the rendered output string.
- ``RenderError`` — failed render; carries a ``BoardRenderResult`` ready for
  the server to pass to ``_render_structured_errors_html``.
- ``render_registered_view(request_path, project, adapter_registry, ...)``
  — full pipeline. Returns ``None`` when no route matches (caller falls
  through to regular file routing). Returns ``RenderSuccess | RenderError``
  when a route matches, whether or not rendering succeeds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dbt_charts.core.registered_views.expander import (
    ExpansionError,
    TemplateLoadError,
    expand_registered_view,
)
from dbt_charts.core.registered_views.loader import load_builtin_registry
from dbt_charts.core.registered_views.query_runner import (
    RegistryQueryError,
    run_registry_queries,
)
from dbt_charts.core.registered_views.router import RouteRouter

if TYPE_CHECKING:
    from dbt_charts.core.board import BoardRenderResult
    from dbt_charts.core.compile.models.board.normalized import Board
    from dbt_charts.core.compile.models.query.normalized import AnyQuery
    from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
    from dbt_charts.core.execute.cache_backend import QueryResultCache
    from dbt_charts.core.execute.file_source_materializer import (
        FileSourceMaterializer,
    )
    from dbt_charts.core.project import Project
    from dbt_charts.core.render.board_links import LinkContext

# Module-level router — compiled once from the built-in registry at import time.
_BUILTIN_ROUTER: RouteRouter = RouteRouter(load_builtin_registry())

# Shared empty request-variables default. Callers (serve, Cloud) always pass the
# request's query params; this is the "no query params" case. A named constant
# (never mutated) keeps the parameter non-optional — see render_registered_view.
_NO_REQUEST_VARS: dict[str, str] = {}


@dataclass
class RenderSuccess:
    """Successful registered-view render.

    Attributes:
        output: The rendered output, in whatever ``format`` the caller
            requested (a standalone HTML document by default; a bare SVG
            fragment when the caller passes ``format="svg"``).
        title: The compiled board's own title, so a caller composing its own
            page chrome (Cloud) doesn't have to re-derive it by parsing a
            ``data-dbt-page-title`` attribute out of the rendered output.
    """

    output: str
    title: str = ""


@dataclass
class RenderError:
    """Failed registered-view render.

    The ``dashboard`` field is a ``BoardRenderResult`` with ``status="failed"``
    and the relevant error payload set. The server passes it to
    ``_render_structured_errors_html`` to produce the error page.

    Attributes:
        dashboard: BoardRenderResult describing the failure.
    """

    dashboard: BoardRenderResult


# Union result type for callers that want a single type annotation.
RenderResult = RenderSuccess | RenderError


@dataclass
class _CompiledView:
    """The board a registered view compiled to, ready for an Executor.

    Holds the output of steps 1-3 of the pipeline (match, pre-template
    queries, expand, compile) so step 4 (execute + render) can build the
    ``Executor`` from it.
    """

    board: Board
    query_registry: dict[str, AnyQuery]


def _compile_registered_view(
    request_path: str,
    adapter_registry: AdapterRegistry,
    result_cache: QueryResultCache | None,
) -> _CompiledView | RenderError | None:
    """Match, query, expand, and compile a registered view. Steps 1-3.

    Returns ``None`` when no registered route matches (caller falls through to
    regular file-based routing), a ``RenderError`` when any step fails, or the
    compiled view ready for an ``Executor``.
    """
    from sqlglot.dialects.dialect import Dialect as SqlglotDialect

    from dbt_charts.core.board import BoardRenderResult
    from dbt_charts.core.compile import compile_authored_board
    from dbt_charts.core.compile.sql_guard import sqlglot_dialect
    from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic
    from dbt_charts.core.diagnostics.base import DbtChartsError

    match = _BUILTIN_ROUTER.match(request_path)
    if match is None:
        return None

    # --- Step 1: run pre-template queries ---
    try:
        query_results = run_registry_queries(
            match.view, match.path_params, adapter_registry, cache=result_cache
        )
    except RegistryQueryError as exc:
        return RenderError(
            dashboard=BoardRenderResult(
                status="failed",
                board_error=Diagnostic.from_code(ERR_INTERNAL, message=str(exc)),
            )
        )

    # --- Step 1.5: build a lazy dialect resolver for the matched source ---
    # Routes below /data/<source>/ and /inspector/<source>/ have a `source`
    # path param, but only the leaf table/detail/column templates actually
    # call sql_identifier() — resolving the dialect eagerly here would touch
    # the adapter registry (and can fail loudly) even for index-level routes
    # that never quote an identifier at all, 200->422ing a source-less
    # project's `/data/` and `/inspector/<source>/` pages. Passing a thunk
    # instead defers the resolve_source_config() call to the first
    # sql_identifier() call inside the template, if any.
    source_param = match.path_params.get("source")

    def resolve_dialect() -> str | None:
        if source_param is None:
            return None
        try:
            source_config = adapter_registry.resolve_source_config(source_param)
        except DbtChartsError as exc:
            raise ExpansionError(str(exc)) from exc
        source_type = source_config["type"]
        # A source `type` is a connector kind, not necessarily a sqlglot
        # dialect — csv/json/parquet are file sources and dbt_profile's real
        # dialect lives in the profile, unknowable here. sqlglot raises
        # rather than degrading on an unknown name, so guard before quoting.
        if sqlglot_dialect(source_type) not in SqlglotDialect.classes:
            raise ExpansionError(
                f"Source {source_param!r} has type {source_type!r}, which has "
                "no SQL dialect dbt charts can use to quote identifiers (file "
                "sources and dbt_profile sources have no fixed SQL dialect)."
            )
        return source_type

    # --- Step 2: expand template into an AuthoredBoard ---
    try:
        authored = expand_registered_view(match, query_results, resolve_dialect)
    except (TemplateLoadError, ExpansionError) as exc:
        return RenderError(
            dashboard=BoardRenderResult(
                status="failed",
                board_error=Diagnostic.from_code(ERR_INTERNAL, message=str(exc)),
            )
        )

    # --- Step 3: compile ---
    # Registered-view boards are programmatically generated and ref-free, so no
    # base_dir context is needed (compilation raises if a ref is ever hit).
    compile_result = compile_authored_board(authored)
    if not compile_result.success:
        return RenderError(
            dashboard=BoardRenderResult(
                status="failed",
                validation_errors=compile_result.errors,
            )
        )

    board = compile_result.board
    if board is None:
        return RenderError(
            dashboard=BoardRenderResult(
                status="failed",
                board_error=Diagnostic.from_code(
                    ERR_INTERNAL, message="Compilation produced no board"
                ),
            )
        )

    return _CompiledView(board=board, query_registry=compile_result.query_registry)


def render_registered_view(
    request_path: str,
    project: Project,
    adapter_registry: AdapterRegistry,
    result_cache: QueryResultCache | None,
    max_workers: int | None = None,
    link_context: LinkContext | None = None,
    file_materializer: FileSourceMaterializer | None = None,
    request_variables: dict[str, str] = _NO_REQUEST_VARS,
    format: str = "html",
    controls: bool = False,
) -> RenderResult | None:
    """Run the full registered-view pipeline for a request path.

    Matches ``request_path`` against the built-in router, runs any pre-template
    queries, expands the template, compiles and executes the resulting board,
    and renders it in the requested format.

    Args:
        request_path: Absolute URL path, e.g. ``"/data/snowflake/analytics/"``.
        project: The project handle, used by the file-source materializer to
            read project-local data files.
        adapter_registry: Configured adapter registry with adapters for the
            sources declared in the project.
        result_cache: Optional persistent query-result cache for the project.
        max_workers: Maximum parallel query workers. ``None`` uses the project
            default.
        request_variables: URL query params for the request, threaded through as
            render variables so interactive views (details/expander toggles, tabs)
            can read their state from the URL. Empty (the default) renders with
            template defaults.
        link_context: Optional link-rewriting context. Cloud callers pass a
            ``LinkContext`` with ``root="/{org}/{project}/d"`` so that
            internal links (sibling tables, schema/source navigation) are emitted
            as ``/{org}/{project}/d/data/...`` rather than root-relative ``/data/...``
            which would 404 under the Cloud project scope. Leave ``None`` for
            ``dct serve`` (links stay root-relative).
        format: Output format passed straight through to ``render()``.
            Defaults to ``"html"`` -- ``dct serve``'s standalone document, the
            same artifact this pipeline has always produced. A caller that
            composes its own page chrome (Cloud's ``board_view.html``) passes
            ``"svg"`` to get the bare fragment shape a served board render
            already uses, instead of a second, nested HTML document.
            ``"yaml"`` is Cloud's "make this a board" clone: the compiled
            board dumped as re-compilable authored YAML (queries, charts, and
            layout, with each query's result rows inlined as ``values:``).
            It is a plain data format like ``"json"``/``"text"`` -- ``render()``
            short-circuits data formats before any SVG/link/controls work, so
            ``link_context`` and ``controls`` are ignored for it.
        controls: Whether the render carries the live control runtime's
            drawn payload (a select's full option list). ``dct serve``'s
            registered-view route has never set this, so the default
            (``False``) preserves that behavior; Cloud passes ``True``, same
            as its board renders.

    Returns:
        ``None`` when no registered route matches (caller falls through to
        regular file-based routing).
        ``RenderSuccess`` when a route matches and the board renders without errors.
        ``RenderError`` when a route matches but any step fails.
    """
    from dbt_charts.core.board import BoardRenderResult
    from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic
    from dbt_charts.core.diagnostics.execution import ExecutionError
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.execute.file_source_materializer import (
        resolve_local_file_materializer_factory,
    )
    from dbt_charts.core.render import render
    from dbt_charts.core.render.errors import RenderError as RenderExecError
    from dbt_charts.core.render.image_assets import image_scope

    compiled = _compile_registered_view(request_path, adapter_registry, result_cache)
    if compiled is None:
        return None
    if isinstance(compiled, RenderError):
        return compiled

    # --- Step 4: execute + render ---
    try:
        # Injected materializer (Cloud) wins; otherwise a lazy local factory so
        # a fully-cached registered view never builds a DuckDB (see Executor).
        executor = Executor(
            compiled.board,
            adapter_registry=adapter_registry,
            query_registry=compiled.query_registry,
            result_cache=result_cache,
            file_materializer=file_materializer,
            file_materializer_factory=resolve_local_file_materializer_factory(
                project, file_materializer
            ),
        )
        with image_scope(project):
            render_result = render(
                compiled.board,
                executor,
                format=format,
                variables=request_variables,
                max_workers=max_workers,
                link_context=link_context,
                controls=controls,
            )
    except (ExecutionError, RenderExecError) as exc:
        return RenderError(
            dashboard=BoardRenderResult(
                status="failed",
                board_error=Diagnostic.from_code(ERR_INTERNAL, message=str(exc)),
            )
        )

    if render_result.board_error is None and not render_result.payload_errors:
        raw_output = render_result.output
        output = (
            raw_output.decode("utf-8")
            if isinstance(raw_output, bytes)
            else str(raw_output)
        )
        return RenderSuccess(output=output, title=compiled.board.title)

    return RenderError(
        dashboard=BoardRenderResult(
            status="failed",
            board_error=render_result.board_error,
            chart_errors=render_result.chart_errors,
        )
    )
