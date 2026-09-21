"""Compile + execute + render orchestrator that returns a typed envelope.

`render_dashboard` packages compile errors, chart errors, and warnings into
a `BoardRenderResult` Pydantic model — the shape the CLI render verb, the
MCP tool, and the embedded HTTP server all consume. Callers that want a
raise-on-failure contract instead call `raise_on_dashboard_failure` on the
returned envelope (see `ProjectSession.render_board`).

The agent_api boards module re-exports the public names so callers
reach them via `dbt_charts.agent_api.boards` (the typed-verb surface).
The implementation lives here — top-level under `core` rather than under
`core/render/` — so `core.serve.server` can call it without inverting the
layer stack, and so the `dbt_charts.core.render` function-vs-submodule name
collision doesn't break `mock.patch("dbt_charts.core.X.board.Y")` on
Python 3.10 (a real CI failure we hit when this module lived at
`core/render/dashboard.py`).
"""

from __future__ import annotations

import dataclasses
import functools
import importlib.util as _importlib_util
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, ParamSpec, get_args

_logger = logging.getLogger(__name__)

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.compile import (
    CompileResult,
    compile,
    compile_file,
    focus_on_chart,
)
from dbt_charts.core.compile.config import resolve_max_template_output_bytes
from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.cache import CachePolicy
from dbt_charts.core.compile.parse.source_map import stamp_diagnostics
from dbt_charts.core.compile.template.output_budget import template_output_budget
from dbt_charts.core.diagnostics import (
    ERR_FORMAT_UNSUPPORTED,
    ERR_INPUT_INVALID,
    ERR_INTERNAL,
    Diagnostic,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.registry import REGISTRY
from dbt_charts.core.diagnostics.suppression import partition as _partition_warnings
from dbt_charts.core.execute import ExecutionError, Executor
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.execute.cache_backend import QueryResultCache
from dbt_charts.core.execute.file_source_materializer import (
    FileSourceMaterializer,
    resolve_local_file_materializer_factory,
)
from dbt_charts.core.project import (
    CHARTS_SUBDIR,
    BoardFile,
    Project,
    ProjectDirectory,
    ProjectPath,
)

# Probe once at import time: avoids repeated find_spec calls and avoids
# the `try/except ImportError` antipattern on our own modules.
_SUPER_SCHEMA_AVAILABLE = (
    _importlib_util.find_spec("dbt_charts_super_schema") is not None
)

if TYPE_CHECKING:
    from dbt_charts.core.inspect.query_validator import RelationshipContext


def _relationship_context(project: Project) -> RelationshipContext | None:
    if not _SUPER_SCHEMA_AVAILABLE:
        return None
    from dbt_charts_super_schema.inspect.relationship_context import (  # noqa: PLC0415
        load_relationship_context,
    )

    return load_relationship_context(project)


from dbt_charts.core.compile.compiler import validate_compiled_queries
from dbt_charts.core.dbt_ref_check import check_manifest_refs
from dbt_charts.core.render import RenderError, render
from dbt_charts.core.render.board_links import LinkContext
from dbt_charts.core.render.board_to_dict import NO_ROW_CAP
from dbt_charts.core.render.dir_context import lazy_dir_context
from dbt_charts.core.render_format import RenderFormat

# "failed" — no output produced (board_error, or validation_errors from a failed
#   compile).
# "partial" — output produced, but chart_errors is non-empty.
# "ok" — output produced, no chart errors.
# Warnings never affect status: they're advisory, and suppressed_warnings are
# project-accepted by definition.
RenderStatus = Literal["ok", "partial", "failed"]


class BoardRenderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RenderStatus
    # bytes for binary formats (png/pdf); str for text-based; dict for json.
    # MCP callers should not request binary formats — `model_dump_json()` /
    # `json.dumps()` in the MCP dispatcher will fail on bytes at the wire
    # boundary, which is correct behavior.
    data: dict[str, Any] | str | bytes | None = None
    validation_errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []
    suppressed_warnings: list[Diagnostic] = []
    chart_errors: list[Diagnostic] = []
    board_error: Diagnostic | None = None
    # The `dct serve` / MCP preview URL for this render. Populated only when
    # `server_port` is passed to `render_dashboard` (i.e. `dct serve` and
    # `dct mcp serve`) — its precondition. Cloud never passes `server_port`,
    # so this stays `None` there by design: core builds a bare
    # `http://localhost:<port>/...` URL, and Cloud's dashboard addresses are
    # org/project/branch-scoped and access-controlled, which core has no
    # concept of. Cloud resolves agent-facing dashboard URLs through its own
    # router instead (`search_boards`).
    url: str | None = None
    # Per-query (name, data_as_of, policy) records populated on success. Consumed
    # by Cloud's snapshot writer to compute expires_at anchored on actual data age.
    board_query_data_ages: list[tuple[str, datetime, CachePolicy]] = []


def raise_on_dashboard_failure(result: BoardRenderResult) -> None:
    """Raise ValueError from a failed BoardRenderResult's error fields; no-op on success.

    Shared translation for callers that want render_dashboard's typed envelope
    surfaced as a plain raise-on-failure contract.
    """
    if result.status != "failed":
        return
    if result.validation_errors:
        errors_str = "\n  ".join(e.message for e in result.validation_errors)
        raise ValueError(f"Compilation errors:\n  {errors_str}")
    if result.board_error is not None:
        # Preserve the render domain as RenderError so callers that classify by
        # exception type keep working; execute/other stay ValueError (the
        # markdown integration catches RenderError but not ExecutionError).
        if REGISTRY.get(result.board_error.code).domain == "render":
            raise RenderError(result.board_error.message)
        raise ValueError(result.board_error.message)
    raise ValueError("Render failed")


def _view_url(
    board_path: ProjectPath,
    variables: dict[str, Any] | None = None,
    *,
    port: int | None,
) -> str | None:
    if port is None:
        return None
    from urllib.parse import urlencode

    rel = PurePosixPath(board_path.relpath).with_suffix("")
    if rel.is_relative_to(CHARTS_SUBDIR):
        rel = rel.relative_to(CHARTS_SUBDIR)
    url_path = rel.as_posix()
    qs = "?" + urlencode(variables) if variables else ""
    return f"http://localhost:{port}/{url_path}{qs}"


def _charts_meta_default_source(charts_dir: ProjectDirectory) -> str | None:
    """Return the project-level default source declared in ``charts/meta.yml``.

    A pathless in-memory board (``InMemoryBoard(content, path=None)``) compiles
    in isolation, so the on-disk meta.yml cascade never runs. A board that
    inherits its source from ``charts/meta.yml`` (``source: <name>``) would then
    compile as sourceless and fail ``ERR-SOURCE-REQUIRED``. Read that
    project-level default through the ``ProjectDirectory`` abstraction — so it
    works for both the filesystem and a host-injected file plugin (Cloud's git
    backend) — and feed it to the compiler as the host default source.
    """
    from dbt_charts.core.compile.parse.parser import load_yaml_mapping

    for name in ("meta.yml", "meta.yaml"):
        meta_path = charts_dir / name
        if meta_path.exists():
            source = load_yaml_mapping(meta_path.read_text()).get("source")
            return source if isinstance(source, str) else None
    return None


def _input_error(message: str) -> BoardRenderResult:
    """Build a BoardRenderResult failure from a plain input-validation message.

    Stamped with ERR-INPUT-INVALID so consumers can distinguish user-supplied
    bad arguments from internal failures.
    """
    return BoardRenderResult(
        status="failed",
        validation_errors=[
            DbtChartsError.from_code(ERR_INPUT_INVALID, message=message).to_diagnostic()
        ],
    )


def _preserved_walk_data(
    output: str | bytes | None, format: str
) -> dict[str, Any] | str | bytes | None:  # type-state: explicit_any — walked payload
    """Convert a data-format walk's raw output into BoardRenderResult.data's shape.

    For a board-level draw failure whose walk still ran (renderer.py's
    board_error branch runs the walk regardless of the draw), ``output`` may
    still hold a payload. ``json.loads`` can fail if that payload was
    truncated by the same fault that set ``board_error``; return ``None``
    rather than raising on top of an already-failed render.
    """
    if output is None:
        return None
    if format in ("json", "data"):
        import json

        try:
            return json.loads(output)
        except ValueError:
            return None
    return output


_RenderDashboardP = ParamSpec("_RenderDashboardP")


def _with_template_output_budget(
    fn: Callable[_RenderDashboardP, BoardRenderResult],
) -> Callable[_RenderDashboardP, BoardRenderResult]:
    """Open template_output_budget() around one call to `fn`.

    A decorator so the ~300-line function body below stays flat: nesting it
    inside an inlined `with` would reindent every line in its span.
    """

    @functools.wraps(fn)
    def wrapper(
        *args: _RenderDashboardP.args, **kwargs: _RenderDashboardP.kwargs
    ) -> BoardRenderResult:
        with template_output_budget(resolve_max_template_output_bytes()):
            return fn(*args, **kwargs)

    return wrapper


@_with_template_output_budget
def render_dashboard(
    board: BoardFile | None = None,
    variables: dict[str, Any] | None = None,
    *,
    chart: str | None = None,
    project: Project,
    adapter_registry: AdapterRegistry | None = None,
    format: str = "json",
    server_port: int | None = None,
    use_cache: bool = True,
    scale: float | None = None,
    as_link: bool = False,
    link_context: LinkContext | None = None,
    result_cache: QueryResultCache | None,
    ignore_codes: set[str] | None = None,
    url_mount_dir: str = "",
    max_workers: int | None = None,
    compile_result: CompileResult | None = None,
    file_materializer: FileSourceMaterializer | None = None,
    builtin_variables: VariableValues | None = None,
    max_rows_per_query: int = NO_ROW_CAP,
    **render_options: Any,
) -> BoardRenderResult:
    """Validate, compile, and render a dbt charts dashboard.

    Pass ``compile_result`` to skip the compile step when the caller has already
    compiled (e.g. the serve layer compiles once to read the board's theme for
    the nav strip, then renders without recompiling). ``board`` is still used for
    error-file stamping, dir-navigation variables, and the preview URL even when
    ``compile_result`` is supplied — pass both when the caller has a real board
    location alongside a pre-compiled result.

    ``board`` binds the content to compile to its project location (the
    ``charts/meta.yml`` cascade anchor, relative-ref base, and error/link path);
    it does not need to exist on the store (an unsaved buffer compiles fine). A
    ``board.path`` of ``None`` compiles with no cascade and contributes no file
    identity (no preview URL, unstamped errors, no dir-navigation variables).
    ``builtin_variables`` overrides the dir-navigation variables ``render_dashboard``
    otherwise derives from ``board.path`` — pass it when the caller's anchor has its
    own directory-listing semantics.
    """
    _warnings_ignore = project.warnings_ignore

    # Only a located board contributes a file identity to error stamps and
    # preview URLs; a pathless in-memory board stays unstamped here so its
    # buffer never leaks a fabricated leaf — the cascade still uses board.path.
    file_path: ProjectPath | None = board.path if board is not None else None

    # as_link=True: compile-only path that returns a preview URL without
    # executing queries — the cheap counterpart to a full render. Requires
    # `board` (the URL points at the saved board on disk).
    if as_link:
        if chart is not None:
            # Preview URLs address whole saved boards; a link that silently
            # ignored the focus would be worse than refusing.
            return _input_error("'chart' cannot be combined with as_link=True")
        if board is None or board.path is None:
            # The preview URL points at a saved board on the store; pathless
            # in-memory content has no addressable URL.
            return _input_error("as_link=True requires a located board")
        result = compile_file(board)
        if not result.success:
            return BoardRenderResult(
                status="failed",
                validation_errors=list(result.errors),
                warnings=list(result.warnings),
                suppressed_warnings=list(result.suppressed_warnings),
            )
        validate_compiled_queries(
            result, relationship_context=_relationship_context(project)
        )
        check_manifest_refs(result, project)
        if result.errors:
            # Ref/source errors land here: this arm has no execution layer to
            # catch them per chart, and a board that cannot execute must not
            # come back "ok" with a preview URL. (Checked on `errors` rather
            # than `success` — mypy narrows the property from the guard above
            # and calls the branch unreachable.)
            return BoardRenderResult(
                status="failed",
                validation_errors=list(result.errors),
                warnings=list(result.warnings),
                suppressed_warnings=list(result.suppressed_warnings),
            )
        return BoardRenderResult(
            status="ok",
            warnings=list(result.warnings),
            suppressed_warnings=list(result.suppressed_warnings),
            url=_view_url(board.path, variables, port=server_port),
        )

    if adapter_registry is None:
        return _input_error(
            "adapter_registry is required for full render "
            "(or pass as_link=True with a board for link-only)"
        )

    allowed_formats = frozenset(get_args(RenderFormat))
    if format not in allowed_formats:
        return BoardRenderResult(
            status="failed",
            validation_errors=[
                DbtChartsError.from_code(
                    ERR_FORMAT_UNSUPPORTED, format=format
                ).to_diagnostic()
            ],
        )
    if compile_result is None and board is None:
        return _input_error("Must provide one of 'board' or 'compile_result'")

    _t_start = time.perf_counter()
    if compile_result is not None:
        # validate_compiled_queries/check_manifest_refs below append to
        # errors/warnings/suppressed_warnings/diagnostics in place. A caller
        # that renders the same compile_result more than once (one compile
        # authorizing several renders off it) must get the same answer every
        # time, so this copies the four mutable lists rather than working on
        # the caller's object directly -- board/query_registry/source_map
        # etc. are frozen data every call reads, never appends to.
        result = dataclasses.replace(
            compile_result,
            errors=list(compile_result.errors),
            warnings=list(compile_result.warnings),
            suppressed_warnings=list(compile_result.suppressed_warnings),
            diagnostics=list(compile_result.diagnostics),
        )
    else:
        assert board is not None  # guaranteed by the check above
        if board.path is not None:
            result = compile_file(board)
        else:
            # Pathless in-memory content: no meta.yml cascade to run, so
            # compile the string directly — the historical yaml_content arm.
            result = compile(
                board.content,
                base_dir=project.directory(CHARTS_SUBDIR),
                project_sources=project.sources,
                project_cache=project.cache,
                host_default_source=_charts_meta_default_source(
                    project.directory(CHARTS_SUBDIR)
                ),
            )
    _t_compile = time.perf_counter()

    if not result.success:
        return BoardRenderResult(
            status="failed",
            validation_errors=list(result.errors),
            warnings=list(result.warnings),
            suppressed_warnings=list(result.suppressed_warnings),
        )

    validate_compiled_queries(
        result, relationship_context=_relationship_context(project)
    )
    # Errors this appends are re-derived per chart by DbtRefResolver during
    # execution, which contains them to the failing chart instead of failing the
    # whole board; only WARN-DBT-MANIFEST-MISSING needs the board-level call.
    check_manifest_refs(result, project)
    compiled_board = result.board
    if compiled_board is not None and chart is not None:
        # Reuse-by-reference: render one existing chart (with its dependent
        # variables) instead of the whole board. Unknown ids fail loudly with
        # the available chart ids — never fall back to the full dashboard.
        try:
            compiled_board = focus_on_chart(compiled_board, chart)
        except ValueError as exc:
            return _input_error(str(exc))
    if compiled_board is None:
        return BoardRenderResult(
            status="failed",
            board_error=DbtChartsError.from_code(
                ERR_INTERNAL,
                message="Compilation did not produce a board",
            ).to_diagnostic(file=file_path.relpath if file_path is not None else None),
        )

    from dbt_charts.core.render.render_result import RenderResult as _RenderResult

    render_result: _RenderResult
    try:
        # Use the caller-supplied materializer when provided (e.g. Cloud with Postgres
        # cache). For local `dct serve` / CLI, hand the executor a lazy factory so the
        # in-process DuckDB-backed materializer is built only when a file-source query
        # actually misses the cache — a fully-cached render never opens one.
        executor = Executor(
            compiled_board,
            adapter_registry=adapter_registry,
            query_registry=result.query_registry,
            use_cache=use_cache,
            result_cache=result_cache,
            file_materializer=file_materializer,
            file_materializer_factory=resolve_local_file_materializer_factory(
                project, file_materializer
            ),
        )
        if scale is not None:
            render_options["scale"] = scale
        if link_context is not None:
            render_options["link_context"] = link_context
        _t_execute_start = time.perf_counter()
        if builtin_variables is not None:
            _builtin_variables: VariableValues | None = builtin_variables
        else:
            _board_dir = file_path.parent if file_path is not None else None
            _builtin_variables = (
                lazy_dir_context(_board_dir, url_mount_dir=url_mount_dir)
                if _board_dir is not None
                else None
            )
        render_result = render(
            compiled_board,
            executor,
            format=format,
            variables=variables or {},
            ignore_codes=ignore_codes,
            builtin_variables=_builtin_variables,
            max_workers=max_workers,
            warnings_ignore=_warnings_ignore,
            max_rows_per_query=max_rows_per_query,
            **render_options,
        )
        _t_done = time.perf_counter()
        # Render-side diagnostics (a chart runtime error's authored_path, a
        # from-query warning's queries.<name> path, ...) are stamped against
        # the compile-time source map here: render never re-parses the
        # authored text, it reuses the map compile already built.
        stamp_diagnostics(
            render_result.chart_errors,
            result.source_map,
            result.sql_blocks,
            result.container_paths,
        )
        stamp_diagnostics(
            render_result.warnings,
            result.source_map,
            result.sql_blocks,
            result.container_paths,
        )
        stamp_diagnostics(
            render_result.suppressed_warnings,
            result.source_map,
            result.sql_blocks,
            result.container_paths,
        )
        if render_result.board_error is not None:
            stamp_diagnostics(
                [render_result.board_error],
                result.source_map,
                result.sql_blocks,
                result.container_paths,
            )
        _logger.debug(
            "render_dashboard %s compile=%.0fms execute+render=%.0fms total=%.0fms cache=%s",
            format,
            (_t_compile - _t_start) * 1000,
            (_t_done - _t_execute_start) * 1000,
            (_t_done - _t_start) * 1000,
            "on" if result_cache is not None else "off",
        )
        # Compile-side warnings haven't passed through the render-time partition,
        # so --ignore-warning / dbt_charts.yml / per-chart codes have no effect on
        # them yet. Apply the same three-layer partition here for symmetry.
        #
        # This yields two suppressed sets that both belong in the result and
        # must not be collapsed. They differ by which layer silenced them:
        # `result.suppressed_warnings` matched a compile-time layer (query-level
        # `ignore:` or meta.yml lint config), while `compile_suppressed`
        # matched the render-time layers applied just above (--ignore-warning,
        # dbt_charts.yml, per-chart codes).
        compile_active, compile_suppressed = _partition_warnings(
            list(result.warnings),
            cli_codes=ignore_codes or set(),
            project_codes=set(_warnings_ignore),
            per_chart_codes={
                chart_id: set(chart.warnings_ignore)
                for chart_id, chart in compiled_board.charts.items()
                if chart.warnings_ignore
            },
        )
        if render_result.board_error is not None:
            return BoardRenderResult(
                status="failed",
                board_error=render_result.board_error,
                data=_preserved_walk_data(render_result.output, format),
                chart_errors=render_result.chart_errors,
                warnings=[*compile_active, *render_result.warnings],
                suppressed_warnings=[
                    *result.suppressed_warnings,
                    *compile_suppressed,
                    *render_result.suppressed_warnings,
                ],
            )
        rendered_output = render_result.output
        if rendered_output is None:
            # board_error guard above returns early; this is unreachable in practice.
            return BoardRenderResult(
                status="failed",
                board_error=DbtChartsError.from_code(
                    ERR_INTERNAL, message="Render produced no output"
                ).to_diagnostic(),
            )
        if format in ("json", "data"):
            import json

            data: dict[str, Any] | str | bytes = json.loads(rendered_output)
        elif isinstance(rendered_output, bytes):
            # Binary formats (png, pdf) — pass bytes through unchanged.
            data = rendered_output
        else:
            data = rendered_output
    except (ExecutionError, RenderError, ChartDataError) as e:
        # What reaches here is what never entered the sizing pass: executor
        # construction, and MissingRequiredVariablesError (a RenderError
        # subclass) raised before build_resolved_board is called. Both are
        # board-level fatals with nothing to isolate. A chart's own resolve
        # failure does not reach here — _require_resolved records it and the
        # tile renders as an error.
        fatal_diagnostic = e.to_diagnostic(
            file=file_path.relpath if file_path is not None else None
        )
        stamp_diagnostics(
            [fatal_diagnostic],
            result.source_map,
            result.sql_blocks,
            result.container_paths,
        )
        return BoardRenderResult(
            status="failed",
            board_error=fatal_diagnostic,
        )

    url = (
        _view_url(file_path, variables, port=server_port)
        if file_path is not None
        else None
    )
    return BoardRenderResult(
        status="partial" if render_result.chart_errors else "ok",
        data=data,
        chart_errors=render_result.chart_errors,
        warnings=[*compile_active, *render_result.warnings],
        suppressed_warnings=[
            *result.suppressed_warnings,
            *compile_suppressed,
            *render_result.suppressed_warnings,
        ],
        url=url,
        board_query_data_ages=executor.query_data_ages,
    )
