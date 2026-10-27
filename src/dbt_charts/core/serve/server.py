"""Unified dbt charts server.

Stage: SERVE
Purpose: Single HTTP server for all dbt charts rendering.

Routes:
- /health - Health check
- /templates - List available inspect templates
- /{path}/?var=value - Any board file (path.yml → /path/)

For /inspect/* paths, the server checks charts/inspect/{template}.yml first,
then falls back to built-in templates with Jinja2 pre-processing.

Example URLs:
- /health
- /inspect/model/?model=fct_orders
- /inspect/numeric_column/?model=fct_orders&column=revenue
- /sales/?region=West  (renders charts/sales.yml)
"""

from __future__ import annotations

import asyncio
import functools
import importlib.util
import logging
import os
import threading
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.resources import files
from pathlib import Path, PurePosixPath  # noqa: TID251 — local dev server file serving
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

_SUPER_SCHEMA_AVAILABLE: bool = (
    importlib.util.find_spec("dbt_charts_super_schema") is not None
)

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, Template, select_autoescape

from dbt_charts._docs_site import docs_site_url
from dbt_charts.core.board import (
    BoardRenderResult,
    prefetch_dashboard,
    render_dashboard,
)
from dbt_charts.core.compile.config import (
    get_project_markdown_metadata_table,
    get_project_server_config,
    resolve_cache_boot,
)
from dbt_charts.core.compile.template.variables import variables_from_query_pairs
from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.registry import ERROR_GUIDE_PATH, REGISTRY
from dbt_charts.core.execute.adapters.adapter_registry import (
    LOCAL_AUTHORING_REGISTRY_KWARGS,
    AdapterRegistry,
    build_adapter_registry,
)
from dbt_charts.core.execute.cache_backend import QueryResultCache
from dbt_charts.core.execute.executor import utc_now
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache
from dbt_charts.core.fonts import get_fonts_dir
from dbt_charts.core.inspect import INSPECT_TEMPLATES
from dbt_charts.core.inspect.renderer import (
    InspectProfileCompileError,
    render_inspect_dashboard,
    resolve_source_name,
    validate_inspect_variables,
)
from dbt_charts.core.project import (
    BOARD_CANDIDATE_SUFFIXES,
    CHARTS_SUBDIR,
    PROJECT_CONFIG_NAME,
    Project,
    ProjectDirectory,
    ProjectPath,
    board_root_for_relpath,
    board_roots,
    board_slug,
    posix_relpath,
    resolve_directory_url,
)

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.registered_views.loader import load_builtin_registry
from dbt_charts.core.registered_views.router import RouteRouter
from dbt_charts.core.render.dir_context import directory_listing
from dbt_charts.core.render.nav import nav_content_left, render_nav_html
from dbt_charts.core.serve.alias_index import AliasIndex, board_file_candidates
from dbt_charts.core.serve.watcher import FileWatcher

logger = logging.getLogger(__name__)

# Built-in registered-view router — initialized once at module load from the
# shipped registry.yaml. Route matching is fast for the small built-in set.
_BUILTIN_ROUTER: RouteRouter = RouteRouter(load_builtin_registry())

# A URL carrying a board-file suffix (BOARD_CANDIDATE_SUFFIXES) is redirected to its
# clean (suffix-less) canonical form so a page has one URL, not three (/a, /a/, /a.md).

# /foo.svg, /foo.png, /foo.pdf → serve board in that render format.
# /foo.yaml → serve raw YAML source as text/plain.
# These are checked before the canonical-form redirect so .yaml isn't swallowed
# by it (BOARD_CANDIDATE_SUFFIXES contains .yaml).
_URL_FORMAT_SUFFIXES: dict[str, str] = {".svg": "svg", ".png": "png", ".pdf": "pdf"}

# Paths that skip the per-request change check (registry / alias-index rebuild) —
# they serve static data and never need a live project state.
_SKIP_REFRESH_PATHS = frozenset({"/health", "/templates", "/__livereload"})

# Shared empty request-variables default (never mutated) — keeps the
# registered-view wrapper's request_variables parameter non-optional.
_NO_REQUEST_VARS: dict[str, str] = {}


class RegistryLeases:
    """Keeps a replaced registry open until the requests using it finish.

    Renders run on worker threads, so a config reload can swap the registry
    while a render still queries the old one.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[AdapterRegistry] = Counter()
        self._retired: set[AdapterRegistry] = set()
        # None until shutdown begins; then the registries already interrupted.
        self._interrupted: set[AdapterRegistry] | None = None

    def acquire(self, registry: AdapterRegistry) -> None:
        late = False
        with self._lock:
            self._counts[registry] += 1
            interrupted = self._interrupted
            if interrupted is not None and registry not in interrupted:
                interrupted.add(registry)
                late = True
        if late:
            # Built by a reload after shutdown began.
            registry.interrupt()

    def release(self, registry: AdapterRegistry) -> None:
        with self._lock:
            self._counts[registry] -= 1
            if self._counts[registry]:
                return
            del self._counts[registry]
            if registry not in self._retired:
                return
            self._retired.remove(registry)
        registry.close()

    def retire(self, registry: AdapterRegistry) -> None:
        """Close `registry` now, or once its last lease is released."""
        with self._lock:
            leased = registry in self._counts
            if leased:
                self._retired.add(registry)
        if not leased:
            registry.close()

    def interrupt_all(self, current: AdapterRegistry) -> None:
        """Interrupt `current`, every leased registry, and any leased later."""
        with self._lock:
            registries = dict.fromkeys([current, *self._counts])
            self._interrupted = set(registries)
        for registry in registries:
            registry.interrupt()

    def close_retired(self) -> None:
        """Close retired registries a render still holds; for shutdown."""
        with self._lock:
            retired, self._retired = self._retired, set()
        for registry in retired:
            registry.close()


def interrupt_queries(state: Any) -> None:  # type-state: explicit_any — starlette State
    state.registry_leases.interrupt_all(state.adapter_registry)


def _board_dir_mtime(scan_root: Path) -> float:
    """Max mtime of board files under scan_root; returns 0.0 when scan_root doesn't exist.

    TODO: this is a filesystem-only mtime poll for hot-reload. A git-blob
    backend has no mtimes; Cloud must drive reload off commit identity instead
    of this scan rather than route it through the Project file-access seam.
    """
    if not scan_root.exists():
        return 0.0
    return max(
        (
            f.stat().st_mtime
            for f in scan_root.rglob("*")
            if f.suffix in BOARD_CANDIDATE_SUFFIXES
        ),
        default=0.0,
    )


def _all_board_roots_mtime(project: FilesystemProject) -> tuple[float, frozenset[str]]:
    """Invalidation signature for the alias index / adapter registry watch:
    max board mtime across every board root (the host charts/ plus every
    installed package's charts/), paired with the SET of package charts/
    directories currently present.

    The max-mtime alone misses a package add or remove whose own files don't
    hold the newest mtime -- e.g. a `dbt deps` reinstall that preserves a
    package's original file mtimes, or removing a package that never held
    the max. Pairing the mtime with the root set catches those cases too.
    """
    package_dirs = project.package_charts_dirs()
    mtime = max(
        (_board_dir_mtime(d) for d in (project.charts_dir, *package_dirs)),
        default=0.0,
    )
    return mtime, frozenset(str(d) for d in package_dirs)


def _config_mtime(root: Path) -> float:
    """Mtime of the project config file (dbt_charts.yml); 0.0 when absent.

    Probes the filesystem directly rather than a cached ``config_file`` so a
    freshly-created config is detected. A change here rebuilds the adapter
    registry so edited sources take effect without a restart.
    """
    candidate = root / PROJECT_CONFIG_NAME
    return candidate.stat().st_mtime if candidate.exists() else 0.0


def _build_link_context(
    board: ProjectPath,
    serve_storage_prefix: str | None = None,
) -> Any:
    """Build board-link rewrite context for a served board."""
    from dbt_charts.core.render.board_links import LinkContext

    prefix = serve_storage_prefix.strip("/") if serve_storage_prefix is not None else ""
    root = f"/{prefix}" if prefix else ""
    try:
        slug = board_slug(board.relpath)
    except ValueError:
        # A board outside every board root (an ad-hoc `dct render some.yml`)
        # has no root-relative slug — fall back to its own stem-path so
        # link rewriting still has a stable, if non-canonical, identity.
        slug = str(PurePosixPath(board.relpath).with_suffix(""))
    return LinkContext(root=root, current_board_slug=slug)


def _resolve_board_file_path(project: Project, clean_path: str) -> ProjectPath:
    """Resolve the request path to a board file handle.

    Uses the canonical board_file_candidates list so that candidate order is
    shared with the alias-index collision checker and never drifts. A ``..``
    segment is rejected outright (403): a board URL never legitimately contains
    one, and allowing it is unsafe when charts/ holds an intentional symlink.
    """
    if ".." in clean_path.split("/"):
        raise HTTPException(status_code=403, detail="Path outside project directory")
    file_path: ProjectPath | None = None
    first_yml_candidate: ProjectPath | None = None
    for candidate in board_file_candidates(project, clean_path):
        is_board = candidate.relpath.endswith(BOARD_CANDIDATE_SUFFIXES)
        if candidate.exists():
            if is_board:
                file_path = candidate
                break
        elif candidate.relpath.endswith(".yml") and first_yml_candidate is None:
            first_yml_candidate = candidate

    if file_path is not None:
        return file_path
    return first_yml_candidate or (
        project.directory(CHARTS_SUBDIR) / f"{clean_path}.yml"
    )


def _doc_url_for_code(code: str) -> str:
    return REGISTRY.get(code).doc_url


@functools.cache
def _serve_template(name: str) -> Template:
    """Load a Jinja2 page template shipped under ``serve/templates`` (cached)."""
    raw = (
        files("dbt_charts.core.serve.templates")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )
    env = Environment(autoescape=select_autoescape(["html", "j2"]))
    env.globals["doc_url"] = _doc_url_for_code
    return env.from_string(raw)


def _error_palette_for_theme(theme_name: str | None = None) -> dict[str, str]:
    """Return structured-error page CSS values for a compiled theme."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    rstyle = resolve_style(get_theme_style(theme_name))
    return {
        "panel_bg": rstyle.background,
        "text": rstyle.font.color,
        "secondary": rstyle.secondary_color,
        "border": rstyle.border.color,
        "accent": rstyle.accent,
        "font_family": rstyle.font.family,
    }


def _board_theme_name(board: ProjectPath) -> str | None:
    """Return a board file's resolved ``theme:``, for callers with no compile result.

    Both the error page's palette and its nav chrome need this, so it returns the
    theme rather than a finished palette — a nav themed differently from the page
    it sits on is worse than an unthemed one.

    Intentionally compiles without the markdown_metadata_table flag: only the
    resolved theme is read here, and the metadata header table does not affect
    a board's theme.
    """
    from dbt_charts.core.compile import compile_file

    result = compile_file(board.read_board())
    if result.success and result.board is not None:
        return result.board.theme
    return None


def _render_structured_errors_html(
    result: BoardRenderResult,
    request_path: str,
    status_override: int | None = None,
    error_palette: Mapping[str, str] | None = None,
    chrome: str = "",
    chart_errors_status: int = 200,
) -> HTMLResponse:
    """Render a BoardRenderResult error result as a structured HTML page.

    ``chart_errors_status`` is the status of a page whose board rendered but
    some charts failed. Board routes pass 500, so a status check does not read
    the board as healthy; the inspector's pages keep 200 and their partial view.

    ``chrome`` is the nav fragment from ``_error_nav`` — empty for the error
    sites with no directory to anchor a nav on (``/inspect/*``, the app-wide
    handler), so those pages render exactly as before.
    """

    if status_override is not None:
        status = status_override
    elif result.board_error is not None:
        status = (
            500 if REGISTRY.get(result.board_error.code).domain == "unknown" else 422
        )
    elif result.validation_errors:
        status = 422
    elif result.chart_errors:
        status = chart_errors_status
    else:
        status = 200

    board_html = ""
    if result.chart_errors and result.data:
        data = result.data
        board_html = data.decode("utf-8") if isinstance(data, bytes) else str(data)

    html = _serve_template("error.html.j2").render(
        result=result,
        request_path=request_path,
        board_html=board_html,
        palette=error_palette or _error_palette_for_theme(),
        error_guide_url=f"{docs_site_url()}/{ERROR_GUIDE_PATH}/",
        chrome=chrome,
    )
    return HTMLResponse(content=html, status_code=status)


def _synthetic_error(message: str) -> BoardRenderResult:
    """Build a BoardRenderResult with a synthetic ERR-INTERNAL board_error."""
    return BoardRenderResult(
        status="failed",
        board_error=Diagnostic.from_code(ERR_INTERNAL, message=message),
    )


def _url_mount_dir(path: Path, project: FilesystemProject) -> str:
    """The board root's own relpath, so lazy_dir_context yields serve-router-
    relative URLs.

    The host charts/ is always mounted at /; a package root
    (dbt_packages/<pkg>/charts) mounts at /<pkg>/ — ``_strip_mount_prefix``
    resolves the namespace rewrite from this value via
    ``board_namespace_for_root``. A path outside every board root mounts at
    the project root (empty string, a no-op for ``_strip_mount_prefix``).
    """
    relpath = posix_relpath(path, project.root)
    root = board_root_for_relpath(relpath, board_roots(project))
    return root.relpath if root is not None else ""


def _error_nav(
    nav_dir: ProjectDirectory,
    url_mount_dir: str,
    current_label: str,
    include_nav: bool,
    board_theme: str | None = None,
) -> str:
    """Nav chrome for an error page, so a failed board is never a dead end.

    No download menu — a board that did not render has nothing to export, and
    every format would land back on this same page — which is why a caller holding
    a success-path fragment builds a second one here rather than reusing it. The
    directory listing's ``except`` branch has no fragment to reuse at all: it is
    reachable before one is ever built.
    """
    if not include_nav:
        return ""
    return render_nav_html(
        nav_dir=nav_dir,
        url_mount_dir=url_mount_dir,
        current_label=current_label,
        board_theme=board_theme,
        content_left="",
        show_download=False,
        show_actions=True,
    )


def _render_board_file(
    file_path: ProjectPath,
    variables: dict[str, Any],
    project: FilesystemProject,
    adapter_registry: AdapterRegistry,
    clock: Callable[[], datetime],
    serve_storage_prefix: str | None = None,
    max_workers: int | None = None,
    result_cache: QueryResultCache | None = None,
    download_format: str | None = None,
    include_nav: bool = True,
) -> Response:
    """Render a board file to HTML (or, when download_format is set, as a download)."""
    if not file_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Board file not found: {file_path.relpath}"
        )

    # Any board surface (root index, folder index, or a direct board) supports
    # ?format=<svg|png|pdf> downloads — the nav's download menu hits this.
    if download_format is not None and download_format in _DOWNLOAD_FORMATS:
        return _render_board_download(
            file_path,
            fmt=download_format,
            variables=variables,
            project=project,
            adapter_registry=adapter_registry,
            max_workers=max_workers,
            result_cache=result_cache,
            include_nav=include_nav,
            clock=clock,
        )

    link_context = _build_link_context(
        file_path,
        serve_storage_prefix=serve_storage_prefix,
    )

    file_fspath = project.root / file_path.relpath
    url_mount_dir = _url_mount_dir(file_fspath, project)

    # Compile once, reuse for the nav's theme read and the render — render_dashboard
    # otherwise re-opens the same YAML to recompile it.
    from dbt_charts.core.compile import compile_file as _compile_file

    board = file_path.read_board()
    compile_result = _compile_file(
        board,
        markdown_metadata_table=get_project_markdown_metadata_table(project),
    )
    board_theme = (
        compile_result.board.theme if compile_result.board is not None else None
    )

    nav_fragment = (
        render_nav_html(
            nav_dir=file_path.parent,
            url_mount_dir=url_mount_dir,
            current_label=file_fspath.stem,
            board_theme=board_theme,
            content_left=nav_content_left(compile_result.board),
            show_download=True,
            show_actions=True,
        )
        if include_nav
        else ""
    )

    result = render_dashboard(
        board=board,
        variables=variables,
        strict_variables=False,
        restated_check=True,
        project=project,
        adapter_registry=adapter_registry,
        format="html",
        link_context=link_context,
        url_mount_dir=url_mount_dir,
        result_cache=result_cache,
        max_workers=max_workers,
        compile_result=compile_result,
        chrome=nav_fragment,
        livereload=True,
        # A served board can re-run its queries, so it gets live controls.
        controls=True,
        clock=clock,
    )

    if result.status == "ok":
        data = result.data
        html = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        # Runs after the response is sent.
        prefetch = BackgroundTasks()
        if result.prefetch_query_names and result_cache is not None:
            prefetch.add_task(
                prefetch_dashboard,
                board,
                variables,
                project=project,
                adapter_registry=adapter_registry,
                result_cache=result_cache,
                max_workers=max_workers,
                compile_result=compile_result,
                clock=clock,
            )
        return HTMLResponse(content=html, background=prefetch)

    # Structured board errors and validation/chart errors all share the same
    # served error page.
    return _render_structured_errors_html(
        result,
        request_path=file_fspath.name,
        error_palette=_error_palette_for_theme(board_theme),
        chrome=_error_nav(
            file_path.parent,
            url_mount_dir,
            file_fspath.stem,
            include_nav,
            board_theme,
        ),
        chart_errors_status=500,
    )


_DOWNLOAD_FORMATS: frozenset[str] = frozenset({"svg", "png", "pdf"})

_DOWNLOAD_MEDIA_TYPE: dict[str, str] = {
    "svg": "image/svg+xml",
    "png": "image/png",
    "pdf": "application/pdf",
}


def _render_board_download(
    file_path: ProjectPath,
    fmt: str,
    variables: dict[str, Any],
    project: FilesystemProject,
    adapter_registry: AdapterRegistry,
    clock: Callable[[], datetime],
    max_workers: int | None = None,
    result_cache: QueryResultCache | None = None,
    include_nav: bool = True,
) -> Response:
    """Render a board in the requested binary/text format and return as attachment.

    ``fmt`` must be in ``_DOWNLOAD_FORMATS``; this is checked by the caller.
    Returns a Response with Content-Disposition: attachment and the correct
    media type for the format. Caller guarantees ``file_path`` exists via the
    project existence check in ``_render_board_file``.
    """
    result = render_dashboard(
        board=file_path.read_board(),
        variables=variables,
        strict_variables=False,
        project=project,
        adapter_registry=adapter_registry,
        format=fmt,
        result_cache=result_cache,
        max_workers=max_workers,
        clock=clock,
    )

    file_fspath = project.root / file_path.relpath
    url_mount_dir = _url_mount_dir(file_fspath, project)
    if result.status == "failed" or not result.data:
        board_theme = _board_theme_name(file_path)
        return _render_structured_errors_html(
            result,
            request_path=file_fspath.name,
            error_palette=_error_palette_for_theme(board_theme),
            chrome=_error_nav(
                file_path.parent,
                url_mount_dir,
                file_fspath.stem,
                include_nav,
                board_theme,
            ),
        )

    filename = f"{file_fspath.stem}.{fmt}"
    raw = result.data
    if isinstance(raw, bytes):
        body = raw
    elif isinstance(raw, str):
        body = raw.encode("utf-8")
    else:
        # dict output (json format) is not a download format — shouldn't happen,
        # but raise rather than silently return garbage.
        raise ValueError(
            f"Unexpected data type for download format {fmt!r}: {type(raw)}"
        )
    return Response(
        content=body,
        media_type=_DOWNLOAD_MEDIA_TYPE[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _build_inspect_registry(project: FilesystemProject) -> AdapterRegistry:
    """Build the read-only adapter registry shared by both /inspect/* routes.

    The inspect HTML server is always DuckDB — dialect is not a parameter.
    Resolves against the project's configured `sources:` only; a project
    with none renders with an empty source_name and the schema/SQL queries
    raise ERR-SOURCE-NOT-FOUND-EMPTY at execute time.
    """
    return build_adapter_registry(
        project,
        read_only=True,
        profile_type="duckdb",
    )


def _resolve_inspect_source_config(adapter_registry: AdapterRegistry) -> dict[str, Any]:
    """Resolve the source_config the /inspect/profile/ and /inspect/exists/
    routes should target. Raises when the project has no configured source —
    no connection-URL fallback, no ``:memory:`` default.
    """
    return adapter_registry.resolve_source_config(
        resolve_source_name(adapter_registry) or None
    )


def _handle_inspect_route(
    template_name: str,
    variables: dict[str, Any],
    project: FilesystemProject,
) -> Response:
    """Handle /inspect/* routes with custom template fallback.

    The inspect HTML server is DuckDB-only and builds its own fixed-config
    (read-only DuckDB) registry, so the render-path knobs (target/read_only)
    do not apply here. Project templates under ``charts/inspect/`` use the same
    Jinja-then-compile pipeline as the built-in copies (``dct init`` ejects
    Jinja-heavy YAML).

    Args:
        template_name: Name of the inspect template (e.g., "model" or "model.yml")
        variables: Variables from query params
        project: The local filesystem project for custom template lookup

    Returns:
        Rendered HTML response
    """
    template_name = template_name.strip().strip("/")
    if template_name.endswith((".yml", ".yaml")):
        template_name = template_name[: template_name.rindex(".")]

    custom_relpath = f"{CHARTS_SUBDIR}/inspect/{template_name}.yml"
    try:
        has_custom = project.exists(custom_relpath)
    except ValueError:
        # template_name escapes the project root — treat as not-found, not 500.
        has_custom = False

    if not has_custom and template_name not in INSPECT_TEMPLATES:
        return _render_structured_errors_html(
            _synthetic_error(
                f"Inspect template '{template_name}' not found. "
                f"Available: {INSPECT_TEMPLATES}"
            ),
            request_path=f"/inspect/{template_name}/",
            status_override=404,
        )

    try:
        variables = validate_inspect_variables(variables)
    except ValueError as e:
        return _render_structured_errors_html(
            BoardRenderResult(
                status="failed",
                validation_errors=[Diagnostic.from_code(ERR_INTERNAL, message=str(e))],
            ),
            request_path=f"/inspect/{template_name}/",
        )

    template_file = f"{template_name}.yml"
    if has_custom:
        try:
            template_yaml = project.read_text(custom_relpath)
        except FileNotFoundError:
            return _render_structured_errors_html(
                _synthetic_error(f"Inspect template file not found: {template_file}"),
                request_path=f"/inspect/{template_name}/",
                status_override=404,
            )
    else:
        template_path = files("dbt_charts.core.inspect.templates").joinpath(
            template_file
        )
        try:
            template_yaml = template_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _render_structured_errors_html(
                _synthetic_error(f"Template file '{template_file}' not found"),
                request_path=f"/inspect/{template_name}/",
                status_override=404,
            )

    adapter_registry = _build_inspect_registry(project)

    try:
        return HTMLResponse(
            content=render_inspect_dashboard(
                template_yaml,
                variables,
                project=project,
                adapter_registry=adapter_registry,
            )
        )
    except InspectProfileCompileError as e:
        return _render_structured_errors_html(
            BoardRenderResult(
                status="failed",
                validation_errors=list(e.result.errors),
                warnings=list(e.result.warnings),
                suppressed_warnings=list(e.result.suppressed_warnings),
            ),
            request_path=f"/inspect/{template_name}/",
        )
    except (DbtChartsError, ValueError) as e:
        error_msg = str(e)
        # Surface table-not-found errors as 404 for better UX
        if "does not exist" in error_msg:
            return _render_structured_errors_html(
                _synthetic_error(error_msg),
                request_path=f"/inspect/{template_name}/",
                status_override=404,
            )
        log_label = "custom" if has_custom else "built-in"
        logger.exception("Error rendering %s inspect template", log_label)
        err: BoardRenderResult
        if isinstance(e, DbtChartsError):
            err = BoardRenderResult(status="failed", board_error=e.to_diagnostic())
        else:
            err = _synthetic_error(error_msg)
        return _render_structured_errors_html(
            err, request_path=f"/inspect/{template_name}/"
        )


def _render_directory_listing(
    dir_handles: list[ProjectDirectory],
    url_path: str,
    project: Project,
    url_mount_dir: str,
    clock: Callable[[], datetime],
    result_cache: QueryResultCache | None = None,
    include_nav: bool = True,
) -> HTMLResponse:
    """Render a directory listing through a built-in board.

    *dir_handles* are every directory the URL names (host first); entries merge
    per URL, the first directory's entry winning, so a host board shadows the
    package board with the same slug.
    """
    from dbt_charts.core.compile import compile

    dir_handle = dir_handles[0]

    if not url_path and all(pf.is_meta for pf in project.iter_boards()):
        return HTMLResponse(
            _serve_template("intro.html.j2").render(
                palette=_error_palette_for_theme(),
                docs_url=docs_site_url(),
            )
        )

    # The root directory has an empty name; "/" is how the nav trigger labels it.
    nav_label = dir_handle.name or "/"  # type-state: silent_fallback — root's label

    board_text, listing_variables = directory_listing(dir_handles, url_path, project)

    try:
        compile_result = compile(board_text)
        if compile_result.errors or compile_result.board is None:
            raise RuntimeError(
                f"Directory board failed: {[e.message for e in compile_result.errors]}"
            )

        nav_fragment = (
            render_nav_html(
                nav_dir=dir_handle,
                url_mount_dir=url_mount_dir,
                current_label=nav_label,
                board_theme=None,
                content_left=nav_content_left(compile_result.board),
                show_download=False,
                show_actions=True,
            )
            if include_nav
            else ""
        )
        from dbt_charts.core.execute.adapters import (  # noqa: PLC0415
            AdapterRegistry,
        )

        result = render_dashboard(
            project=project,
            adapter_registry=AdapterRegistry(project=project),
            format="html",
            result_cache=result_cache,
            compile_result=compile_result,
            chrome=nav_fragment,
            controls=True,
            builtin_variables=listing_variables,
            clock=clock,
        )
        if result.status == "ok":
            data = result.data
            html = data.decode("utf-8") if isinstance(data, bytes) else str(data)
            return HTMLResponse(content=html)
        return _render_structured_errors_html(
            result, request_path=url_path, chrome=nav_fragment
        )
    except (DbtChartsError, ValueError, FileNotFoundError) as e:
        if logger.isEnabledFor(logging.DEBUG):
            raise
        logger.exception("Error rendering directory listing for %s", dir_handle.relpath)
        if isinstance(e, DbtChartsError):
            err = BoardRenderResult(status="failed", board_error=e.to_diagnostic())
        else:
            err = _synthetic_error(str(e))
        return _render_structured_errors_html(
            err,
            request_path=url_path,
            chrome=_error_nav(
                dir_handle,
                url_mount_dir,
                nav_label,
                include_nav,
            ),
        )


def _render_registered_view_response(
    request_path: str,
    project: Project,
    adapter_registry: Any,
    result_cache: QueryResultCache | None,
    max_workers: int | None = None,
    request_variables: dict[str, str] = _NO_REQUEST_VARS,
) -> Response | None:
    """Try to serve request_path as a built-in registered view.

    Thin HTTP wrapper around the core ``render_registered_view`` pipeline.
    Returns ``None`` when no registered view matches (caller falls through to
    regular file-based routing). Returns ``HTMLResponse`` when a route matches.

    ``request_variables`` carries the URL query params so interactive views
    (details/expander toggles, tabs) can read their state from the URL.
    """
    from dbt_charts.core.registered_views.render_pipeline import (  # noqa: PLC0415
        RenderError as PipelineRenderError,
        RenderSuccess,
        render_registered_view,
    )

    result = render_registered_view(
        request_path=request_path,
        project=project,
        adapter_registry=adapter_registry,
        result_cache=result_cache,
        max_workers=max_workers,
        request_variables=request_variables,
    )
    if result is None:
        return None
    if isinstance(result, RenderSuccess):
        return HTMLResponse(content=result.output)
    # PipelineRenderError: map BoardRenderResult to an error page response.
    assert isinstance(result, PipelineRenderError)
    return _render_structured_errors_html(result.dashboard, request_path=request_path)


_DuckDBConfig = dict[str, Any]  # type-state: explicit_any — passthrough


def build_serve_registry(
    project: FilesystemProject,
    *,
    dialect: str,
    target: str | None,
    max_workers: int | None,
    read_only: bool = True,
    allow_mutating_methods: bool = False,
    allow_external_access_in_readonly: bool = False,
    duckdb_config: _DuckDBConfig | None = None,
) -> AdapterRegistry:
    """The adapter registry a served board renders with.

    The one construction both ``create_server`` and the ``--detach`` check use,
    so the check cannot run under different settings than the server.
    """
    return build_adapter_registry(
        project,
        read_only=read_only,
        allow_mutating_methods=allow_mutating_methods,
        allow_external_access_in_readonly=allow_external_access_in_readonly,
        duckdb_config=duckdb_config,
        profile_type=dialect,
        target=target if target is not None else "dev",
        max_workers=max_workers,
    )


def create_server(
    project: FilesystemProject,
    dialect: str = "duckdb",
    target: str | None = None,
    serve_storage_prefix: str | None = None,
    read_only: bool = True,
    allow_external_access_in_readonly: bool = False,
    duckdb_config: dict[str, Any] | None = None,
    max_workers: int | None = None,
    no_cache: bool = False,
    cache_path: Path | None = None,
    allow_mutating_methods: bool = False,
    clock: Callable[[], datetime] = utc_now,
) -> FastAPI:
    """Create unified dbt charts server.

    Args:
        project: The dbt charts project — resolved before this call by the CLI edge.
        dialect: SQL dialect for board rendering (duckdb, postgres, etc.).
            The /inspect/* HTML path is always DuckDB — dialect is not forwarded there.
        target: dbt target name override for DbtAdapter
        serve_storage_prefix: Override for rendered board-link URLs in serve mode
        read_only: DuckDB read-only driver flag for board rendering (default True).
        allow_external_access_in_readonly: Allow author SQL to reach the filesystem
            or network through DuckDB (read_csv/read_json_auto, httpfs, ATTACH).
            Default False: serve is strict read-only, matching Cloud. File sources
            (csv/json/parquet) do not need this: the file source materializer
            reads them on a private connection, out of band from author SQL.
        duckdb_config: DuckDB config dict; defaults to enable_external_access when
            allow_external_access_in_readonly is True.
        allow_mutating_methods: Let http sources use POST/PUT/PATCH/DELETE.
            Only the human-run ``dct serve`` passes True; the embedded
            preview behind agent sessions keeps GET only.
        no_cache: Skip the query-result cache entirely (Executor runs uncached).
            Explicit override — wins over the project's dbt_charts.yml cache: block.
        cache_path: Persist the query-result cache to this DuckDB file (created
            if absent). Explicit override — wins over dbt_charts.yml. None (the
            default) falls through to the project's cache: block (path: null
            there means the per-user file). Ignored when
            no_cache=True.
        clock: Every render's time source, handed to its ``Executor``.

    Returns:
        Configured FastAPI application
    """
    if duckdb_config is None and allow_external_access_in_readonly:
        duckdb_config = LOCAL_AUTHORING_REGISTRY_KWARGS["duckdb_config"]

    # Permanent handle to the served project: file reads hit disk fresh on every
    # call, so board and data-file edits are picked up with no rebuild.
    core_project = project
    server_config = get_project_server_config(core_project)
    # Resolved once, up front: FileWatcher (below) needs boot.path to exclude
    # the on-disk cache file from the live-reload watch, and _lifespan reuses
    # this same value rather than resolving it a second time at startup.
    boot = resolve_cache_boot(core_project, no_cache=no_cache, cache_path=cache_path)

    def _build_registry(project: FilesystemProject) -> AdapterRegistry:
        return build_serve_registry(
            project,
            dialect=dialect,
            target=target,
            max_workers=max_workers,
            read_only=read_only,
            allow_mutating_methods=allow_mutating_methods,
            allow_external_access_in_readonly=allow_external_access_in_readonly,
            duckdb_config=duckdb_config,
        )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        # Boot owns the result-cache lifecycle: resolve_cache_boot (resolved
        # once, above) applies flag (no_cache/cache_path) > project
        # dbt_charts.yml cache: block precedence; core/ cannot import
        # agent_api (layering), so the cache is constructed directly here.
        # The cache is process-lifetime: it survives the per-change registry
        # rebuilds below (file_version keys invalidate stale entries). The
        # registry is built eagerly here (not lazily on first request), so a
        # malformed dbt_charts.yml surfaces at startup rather than on the
        # first page load.
        cache = (
            TrivialDuckDBCache(db_path=boot.path, max_age=boot.max_age)
            if boot.enabled
            else None
        )
        try:
            _app.state.result_cache = cache
            _app.state.adapter_registry = _build_registry(core_project)
            _app.state.config_mtime = _config_mtime(core_project.root)
            _app.state.alias_index_mtime = _all_board_roots_mtime(core_project)
            _app.state.alias_index = AliasIndex.build(core_project)
            await _app.state.watcher.start()
            yield
        finally:
            # The registry closes must not strand the cache close.
            registry = getattr(_app.state, "adapter_registry", None)
            try:
                _app.state.registry_leases.close_retired()
                if registry is not None:
                    registry.close()
            finally:
                if cache is not None:
                    cache.close()
            # Last: must stay below the warehouse closes, so a raise here
            # cannot skip them.
            await _app.state.watcher.stop()

    app = FastAPI(
        title="dbt charts Server",
        description="Unified server for dbt charts rendering",
        version="0.1.0",
        debug=server_config.debug,
        lifespan=_lifespan,
    )

    app.state.project = core_project
    app.state.max_workers = max_workers
    app.state.serve_storage_prefix = serve_storage_prefix
    app.state.read_only = read_only
    app.state.server_nav = server_config.nav
    app.state.allow_external_access_in_readonly = allow_external_access_in_readonly
    app.state.duckdb_config = duckdb_config
    # Fallbacks until the lifespan sets the real signatures on startup.
    app.state.alias_index_mtime = (0.0, frozenset())
    app.state.config_mtime = 0.0
    # Built here so shutdown.py can take it before the app runs. Watches the
    # root, not charts_dir, which lives under it and need not exist yet.
    app.state.watcher = FileWatcher(str(core_project.root), cache_path=boot.path)
    app.state.registry_leases = RegistryLeases()
    # Serializes concurrent registry / alias-index rebuilds.
    _refresh_lock = asyncio.Lock()

    @app.exception_handler(DbtChartsError)
    async def _dbt_charts_error_handler(
        request: Request, exc: DbtChartsError
    ) -> Response:
        logger.error(
            "Unhandled DbtChartsError serving %s",
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return _render_structured_errors_html(
            BoardRenderResult(status="failed", board_error=exc.to_diagnostic()),
            request_path=request.url.path,
        )

    @app.middleware("http")
    async def _reload_on_change(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path.startswith("/static/fonts/") or path in _SKIP_REFRESH_PATHS:
            return await call_next(request)
        state = request.app.state
        project: FilesystemProject = state.project
        leases: RegistryLeases = state.registry_leases
        async with _refresh_lock:
            # dbt_charts.yml edit → rebuild the registry from fresh sources and close
            # the old one (releasing its DuckDB handles). The project is a permanent
            # handle; only its cached config needs busting so the rebuild sees the edit.
            config_mtime = _config_mtime(project.root)
            if config_mtime != state.config_mtime:
                for key in (
                    "config_file",
                    "sources",
                    "warnings_ignore",
                    "locked_schema_version",
                ):
                    vars(project).pop(key, None)
                old_registry = state.adapter_registry
                state.adapter_registry = _build_registry(project)
                leases.retire(old_registry)
                state.config_mtime = config_mtime
            # Board add/edit/remove → rebuild the alias index.
            charts_mtime = _all_board_roots_mtime(project)
            if charts_mtime != state.alias_index_mtime:
                state.alias_index = AliasIndex.build(project)
                state.alias_index_mtime = charts_mtime
            # Leased under the lock, so a reload cannot close it before the route
            # reads it from request.state.
            registry: AdapterRegistry = state.adapter_registry
            leases.acquire(registry)
        request.state.adapter_registry = registry
        try:
            return await call_next(request)
        finally:
            leases.release(registry)

    # Mount the bundled webfonts at /static/fonts so the @font-face URLs
    # injected into rendered HTML/SVG (Inter Variable, dbt Sans Tabular,
    # Source Serif 4, Noto Emoji) resolve in the browser. Register before the
    # `/{path:path}` catch-all — Starlette matches routes in insertion order.
    app.mount(
        "/static/fonts",
        StaticFiles(directory=get_fonts_dir()),
        name="fonts_static",
    )

    @app.get("/health")
    async def health() -> dict[str, str | int]:
        """Health check; names the process and project so `dct serve --detach`
        can tell its own server from any other listener on the port."""
        return {
            "status": "ok",
            "service": "dbt-charts-server",
            "pid": os.getpid(),
            "project": str(project.root),
        }

    @app.get("/templates")
    async def list_templates() -> dict[str, list[str]]:
        """List available inspect templates."""
        return {"inspect_templates": INSPECT_TEMPLATES}

    @app.get("/__livereload")
    async def livereload(request: Request) -> StreamingResponse:
        """SSE endpoint: emits a reload event when watched files change.

        Browser tabs opened via ``dct serve`` connect here via EventSource.
        When a board file or dbt_charts.yml changes, the server pushes
        ``data: reload`` and the tab calls ``location.reload()``. The stream
        ends when the server closes the watch, which is how CTRL-C reaches it.
        """

        watcher: FileWatcher = request.app.state.watcher

        async def _event_stream() -> AsyncGenerator[str, None]:
            async for _ in watcher.changes():
                yield "data: reload\n\n"

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/inspect/profile/", response_class=HTMLResponse)
    def profile_table(request: Request) -> str:
        """Run table profiling, save results, and render the dashboard.

        Requires ``dbt-charts-super-schema`` to be installed. Without the private
        package, this endpoint returns 503 with an install hint.

        Uses POST to avoid accidental invocation by prefetchers/crawlers.
        """
        if not _SUPER_SCHEMA_AVAILABLE:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Table profiling requires the dbt-charts-super-schema package. "
                    "Install it from the monorepo or your private registry."
                ),
            )
        from dbt_charts_super_schema.inspect.inspector import (  # noqa: PLC0415
            TableInspector,
        )
        from dbt_charts_super_schema.inspect.storage import (  # noqa: PLC0415
            InspectionStorage,
        )

        variables = dict(request.query_params)
        model_name = variables.get("model", "")
        if not model_name:
            raise HTTPException(status_code=400, detail="model parameter required")

        try:
            variables = validate_inspect_variables(variables)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        inspect_project: FilesystemProject = app.state.project
        adapter_registry = _build_inspect_registry(inspect_project)
        super_schema_path = inspect_project.root / InspectionStorage.DEFAULT_PATH
        storage = InspectionStorage(output_path=super_schema_path)

        try:
            source_config = _resolve_inspect_source_config(adapter_registry)
            with TableInspector(source_config) as inspector:
                profile = inspector.inspect_table(model_name)
            storage.save_inspection(profile)
        except Exception as e:
            logger.exception("Profile failed for %s", model_name)
            raise HTTPException(
                status_code=500,
                detail="Profiling failed. Check server logs for details.",
            ) from e

        template_path = files("dbt_charts.core.inspect.templates").joinpath("model.yml")
        return render_inspect_dashboard(
            template_path.read_text(encoding="utf-8"),
            variables,
            project=inspect_project,
            adapter_registry=adapter_registry,
        )

    @app.get("/inspect/exists/")
    def table_exists(model: str) -> Response:
        """Check if a table exists in the warehouse without reading profile data.

        Requires ``dbt-charts-super-schema`` to be installed.
        Returns 200 {"exists": true} when found, 404 when not found.
        Returns 500 if the check itself fails (caller falls through to generic error UI).
        """
        if not _SUPER_SCHEMA_AVAILABLE:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Table existence check requires the dbt-charts-super-schema package."
                ),
            )
        from dbt_charts_super_schema.inspect.connection import (  # noqa: PLC0415
            InspectConnection,
        )

        try:
            validate_inspect_variables({"model": model})
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

        adapter_registry = _build_inspect_registry(app.state.project)
        try:
            source_config = _resolve_inspect_source_config(adapter_registry)
            with InspectConnection(source_config, read_only=True) as conn:
                rows = conn.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
                    [model],
                ).fetchall()
        except Exception as e:  # noqa: BLE001 — HTTP boundary: any DB error must surface as 500, not crash server
            logger.warning("inspect/exists check failed for %r: %s", model, e)
            raise HTTPException(
                status_code=500, detail="Warehouse existence check failed"
            ) from e
        if rows:
            return JSONResponse({"exists": True})
        raise HTTPException(
            status_code=404, detail=f"Table '{model}' not found in warehouse"
        )

    @app.get("/{path:path}", response_class=HTMLResponse)
    def get_board(path: str, request: Request) -> Response:
        """Serve any board file with query params as variables.

        URL path maps to file path:
            /sales/ → charts/sales.yml
            /reports/q1/ → charts/reports/q1.yml

        /charts/* always returns 404 — charts/ is served at /.

        Directories show a listing of renderable files and subdirectories.

        Special case - /inspect/* paths:
            1. Check charts/inspect/{template}.yml for custom templates
            2. Fall back to built-in templates with Jinja2 pre-processing

        Query params become variables:
            /sales/?region=West → variables["region"] = "West"
        """
        clean_path = path.strip("/")

        if clean_path == CHARTS_SUBDIR or clean_path.startswith(f"{CHARTS_SUBDIR}/"):
            raise HTTPException(status_code=404, detail="Not found")
        if ".." in clean_path.split("/"):
            raise HTTPException(
                status_code=403, detail="Path outside project directory"
            )

        # /foo.svg, /foo.png, /foo.pdf — render and serve in that format.
        for url_suffix, fmt in _URL_FORMAT_SUFFIXES.items():
            if clean_path.endswith(url_suffix):
                base = clean_path[: -len(url_suffix)]
                resolved = resolve_directory_url(app.state.project, base)
                if isinstance(resolved, list) and resolved:
                    raise HTTPException(
                        status_code=404,
                        detail=f"No board at /{base}/" if base else "No board at /",
                    )
                file_path = (
                    resolved
                    if isinstance(resolved, ProjectPath)
                    else _resolve_board_file_path(app.state.project, base)
                )
                raw_variables = variables_from_query_pairs(
                    request.query_params.multi_items()
                )
                return _render_board_file(
                    file_path,
                    raw_variables,
                    app.state.project,
                    adapter_registry=request.state.adapter_registry,
                    serve_storage_prefix=app.state.serve_storage_prefix,
                    max_workers=app.state.max_workers,
                    clock=clock,
                    result_cache=app.state.result_cache,
                    download_format=fmt,
                    include_nav=app.state.server_nav,
                )

        # /foo.yaml — serve raw YAML source as plain text.
        # Must come before the canonical-form redirect (BOARD_CANDIDATE_SUFFIXES
        # contains .yaml) so it isn't redirected to the HTML render.
        if clean_path.endswith(".yaml"):
            base = clean_path[: -len(".yaml")]
            raw_file = _resolve_board_file_path(app.state.project, base)
            if not raw_file.exists():
                raise HTTPException(
                    status_code=404, detail=f"Board file not found: {raw_file.relpath}"
                )
            return Response(
                content=raw_file.read_text(),
                media_type="text/plain; charset=utf-8",
            )

        # Canonicalize board-file suffixes to the clean URL. The #fragment is
        # client-side, so /a.md#section redirects to /a and the browser re-applies
        # #section against it — fixing anchored markdown links for free.
        for suffix in BOARD_CANDIDATE_SUFFIXES:
            if clean_path.endswith(suffix):
                target = "/" + clean_path[: -len(suffix)]
                qs = request.url.query
                return RedirectResponse(
                    url=f"{target}?{qs}" if qs else target, status_code=301
                )

        raw_params = variables_from_query_pairs(request.query_params.multi_items())
        download_format = raw_params.pop("format", None)
        # ?_embed renders the board without nav chrome, for a host's modal iframe.
        embed = raw_params.pop("_embed", None) is not None
        variables = raw_params

        # Alias resolution: before any board lookup, check whether this URL is an
        # alias that should redirect to a board's canonical file-path URL.
        # Runs after the /charts/ prefix guard above (infra routes like /health and
        # /static are matched by their dedicated routes before this catch-all fires).
        alias_index: AliasIndex = (
            getattr(app.state, "alias_index", None) or AliasIndex()
        )
        request_url = "/" + clean_path + ("/" if clean_path else "")
        canonical_url = alias_index.lookup(request_url)
        if canonical_url is not None:
            # Preserve the query string (without the internal `format` pop — restore it)
            qs = request.url.query
            redirect_url = canonical_url
            if qs:
                redirect_url = canonical_url + "?" + qs
            # 302 (temporary) is the correct v1 default — browsers don't cache it,
            # so renaming a board later is safe.  Per-alias `redirect: 301` for
            # permanent SEO-safe moves is a documented follow-up; it requires a
            # richer alias shape (object with `url` + `redirect` keys) rather than
            # the current plain list[str].
            return RedirectResponse(url=redirect_url, status_code=302)

        project: FilesystemProject = app.state.project
        resolved = resolve_directory_url(project, clean_path)
        if isinstance(resolved, ProjectPath):
            return _render_board_file(
                resolved,
                variables,
                project,
                adapter_registry=request.state.adapter_registry,
                serve_storage_prefix=app.state.serve_storage_prefix,
                max_workers=app.state.max_workers,
                clock=clock,
                result_cache=app.state.result_cache,
                download_format=download_format,
                include_nav=app.state.server_nav and not embed,
            )
        if resolved:
            return _render_directory_listing(
                resolved,
                clean_path,
                project=project,
                url_mount_dir=_url_mount_dir(
                    project.root / resolved[0].relpath, project
                ),
                clock=clock,
                result_cache=app.state.result_cache,
                include_nav=app.state.server_nav,
            )

        # Try the built-in registered-view router (data/inspector routes).
        # charts/ is always mounted at /; strip any serve_storage_prefix override
        # before matching the router so /data/... routes resolve correctly.
        _prefix = (
            app.state.serve_storage_prefix.strip("/")
            if app.state.serve_storage_prefix is not None
            else ""
        )
        _route_path = clean_path.removeprefix(f"{_prefix}/") if _prefix else clean_path
        request_abs_path = f"/{_route_path}/" if _route_path else "/"
        if _BUILTIN_ROUTER.match(request_abs_path) is not None:
            registered_response = _render_registered_view_response(
                request_path=request_abs_path,
                project=app.state.project,
                adapter_registry=request.state.adapter_registry,
                result_cache=app.state.result_cache,
                max_workers=app.state.max_workers,
                request_variables=variables_from_query_pairs(
                    request.query_params.multi_items()
                ),
            )
            if registered_response is not None:
                return registered_response

        # Special case: /inspect/* paths use built-in template fallback (DuckDB-only).
        if _route_path.startswith("inspect/"):
            template_name = _route_path.removeprefix("inspect/")
            return _handle_inspect_route(
                template_name,
                variables,
                app.state.project,
            )

        file_path = _resolve_board_file_path(app.state.project, clean_path)

        # Parameterized alias fallback: only when no real board file resolves, so
        # real files / dirs / system views always win. A pattern like
        # /milestones/<name> redirects to the board's canonical URL with the
        # captured segment as a query param (?name=...), which the board reads as
        # its variable.
        if not file_path.exists():
            for canonical, captured in alias_index.match_patterns(request_url):
                # The capture wins over a colliding query param, as it always has:
                # the alias names the board's variable, and a stray `?name=` in the
                # link must not ride along beside it as a second value.
                merged = [
                    (key, value)
                    for key, value in request.query_params.multi_items()
                    if key not in captured
                ] + list(captured.items())
                redirect_url = canonical + ("?" + urlencode(merged) if merged else "")
                return RedirectResponse(url=redirect_url, status_code=302)

        return _render_board_file(
            file_path,
            variables,
            app.state.project,
            adapter_registry=request.state.adapter_registry,
            serve_storage_prefix=app.state.serve_storage_prefix,
            max_workers=app.state.max_workers,
            clock=clock,
            result_cache=app.state.result_cache,
            download_format=download_format,
            include_nav=app.state.server_nav and not embed,
        )

    return app
