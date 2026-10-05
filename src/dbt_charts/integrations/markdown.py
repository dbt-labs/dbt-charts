"""Superfences handlers for embedding dbt charts boards in markdown.

Two handlers for use with ``pymdownx.superfences`` custom fences:

- ``fence_dbt_charts`` — render-only: YAML in, SVG/HTML out.
- ``fence_dbt_charts_example`` — code + render: syntax-highlighted YAML
  alongside rendered output, with playground links.

Register in ``mkdocs.yml``::

    markdown_extensions:
      - pymdownx.superfences:
          custom_fences:
            - name: dbt-charts
              class: dbt-charts
              format: !!python/name:dbt_charts.integrations.markdown.fence_dbt_charts
            - name: dbt-charts-example
              class: dbt-charts-example
              format: !!python/name:dbt_charts.integrations.markdown.fence_dbt_charts_example

Then in any ``.md`` page::

    ```dbt-charts
    charts:
      revenue:
        type: bar
        x: product
        y: revenue
    ```

    ```dbt-charts-example {file=charts/examples/bar-charts/minimum.yml}
    ```
"""

from __future__ import annotations

import base64
import hashlib
import html
import logging
import re
import zlib
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import (
    BoardRenderResult,
    raise_on_dashboard_failure,
    render_dashboard,
)
from dbt_charts.core.compile.parse.markdown import (
    MARKDOWN_NOT_BOARD_MESSAGE,
    is_markdown_board_content,
    markdown_to_yaml,
)
from dbt_charts.core.diagnostics.codes_compile import ERR_UNRESOLVED_REFERENCE
from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry
from dbt_charts.core.project import (
    CHARTS_SUBDIR,
    InMemoryBoard,
    ProjectDirectory,
    ProjectPath,
)
from dbt_charts.core.project_roots import find_dct_root, find_repo_root
from dbt_charts.core.render.dir_context import lazy_dir_context
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.utils import YAML_LOADER
from dbt_charts.integrations.highlighting import highlight_board_yaml

logger = logging.getLogger("dbt_charts.integrations.markdown")

_PLAYGROUND_URL = "https://play.dbtcharts.com"
_VALID_LAYOUTS = {"side-by-side", "stacked", "render-only", "yaml-only"}

_PROJECT_DIR_OVERRIDE: ContextVar[Path | None] = ContextVar(
    "dbt_charts_markdown_project_dir", default=None
)


@contextmanager
def project_dir_override(path: Path) -> Generator[None, None, None]:
    """Pin fence rendering to *path* instead of walking up from ``cwd``."""
    token = _PROJECT_DIR_OVERRIDE.set(path.resolve())
    try:
        yield
    finally:
        _PROJECT_DIR_OVERRIDE.reset(token)


def set_project_dir_override(path: Path) -> None:
    """Permanently pin fence rendering to *path* for the rest of the process.

    Unlike ``project_dir_override``, there is no matching unset. For
    long-lived single-purpose processes (an mkdocs build) there is no natural
    point to unwind the pin, so entering the context manager and never
    exiting it left a suspended generator for the GC to finalize at
    interpreter shutdown instead.
    """
    _PROJECT_DIR_OVERRIDE.set(path.resolve())


def _resolve_project_dir() -> Path:
    """Resolve the project root for markdown fence rendering.

    Override pins (tasks-site renders task files from arbitrary cwds); otherwise
    walk from cwd to a dbt charts project root, then a git root, then cwd.
    """
    override = _PROJECT_DIR_OVERRIDE.get()
    if override is not None:
        return override
    cwd = Path.cwd().resolve()
    return find_dct_root(cwd) or find_repo_root(cwd) or cwd


def _highlight_yaml(source: str) -> str:
    """Syntax-highlight dbt charts board YAML using DbtChartsYamlLexer.

    SQL inside ``sql: |`` and ``query: |`` block scalars is delegated to
    SqlLexer so SQL keywords receive keyword styling.

    Returns HTML wrapped in ``<div class="yaml"><pre><code>...</code></pre></div>``.
    """
    highlighted = highlight_board_yaml(source)
    return (
        f'<div class="yaml"><pre><code class="language-yaml">'
        f"{highlighted}</code></pre></div>"
    )


def _playground_url(yaml_source: str) -> str:
    """Generate a hosted-playground URL with compressed YAML payload."""
    compressed = zlib.compress(yaml_source.encode("utf-8"), level=9)
    encoded = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
    return f"{_PLAYGROUND_URL}/?y={encoded}"


def _resolve_file(file_path: str, project_dir: Path) -> Path:
    """Resolve a file= path against project_dir with traversal guard."""
    resolved = (project_dir / file_path).resolve()
    if not resolved.is_relative_to(project_dir.resolve()):
        raise ValueError(f"Path escapes project directory: {file_path}")
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")
    return resolved


def _fence_options(
    options: dict[str, Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Merge legacy ``options`` with SuperFences attr-list kwargs."""
    merged = dict(options)
    attrs = kwargs.get("attrs") or {}
    for key, value in attrs.items():
        merged.setdefault(key, value)
    return merged


def _shared_examples_dir(project_dir: Path) -> Path | None:
    """Return ``examples/playground/`` when it ships a ``dbt_charts.yml`` project root.

    Docs ``dbt-charts-example`` fences declare playground sources — ``examples_db``
    over ``ecommerce_orders``, ``db`` over the ``dundersign.*`` schemas — which live
    in the shipped playground gallery project
    (``examples/playground/dbt_charts.yml`` + ``examples.duckdb``), not at the
    ``examples/`` container root (which holds no ``dbt_charts.yml``; see
    ``examples/AGENTS.md``).
    """
    playground_dir = project_dir / "examples" / "playground"
    if playground_dir.is_dir() and (playground_dir / "dbt_charts.yml").is_file():
        return playground_dir
    return None


def _inline_render_contexts(project_dir: Path) -> list[tuple[Path, Path]]:
    """Return candidate ``(project_root, base_dir)`` contexts for inline YAML."""
    examples_dir = _shared_examples_dir(project_dir)
    if examples_dir is not None:
        # Docs inline examples resolve shared queries and named sources from here.
        return [(examples_dir, examples_dir), (project_dir, project_dir)]
    candidates: list[tuple[Path, Path]] = [(project_dir, project_dir)]
    fallback = project_dir / "examples"
    if fallback.is_dir():
        candidates.append((fallback, fallback))
    return candidates


def _file_render_contexts(board_dir: ProjectDirectory) -> list[tuple[Path, Path]]:
    """Return candidate ``(project_root, base_dir)`` contexts for file-backed YAML.

    Materializes filesystem paths here: each candidate builds its own
    ``FilesystemProject`` and adapter registry (the project root and the shared
    ``examples/`` fallback are different projects), so the fallback loop is
    filesystem currency, not domain.
    """
    project = board_dir.project
    if not isinstance(project, FilesystemProject):
        raise ValueError(
            "File-backed markdown fences require a local filesystem project; "
            f"got {type(project).__name__}."
        )
    project_dir = project.root
    file_ctx = (project_dir, project_dir / board_dir.relpath)
    examples_dir = _shared_examples_dir(project_dir)
    if examples_dir is not None:
        return [(examples_dir, examples_dir), file_ctx]
    candidates: list[tuple[Path, Path]] = [file_ctx]
    fallback = project_dir / "examples"
    if fallback.is_dir():
        candidates.append((fallback, fallback))
    return candidates


_SOURCE_NOT_FOUND_RE = re.compile(
    r"Source .+ not found|No source profiles are configured",
    re.I,
)


def _names_missing_query_file(result: BoardRenderResult | None) -> bool:
    """A ``file.yml#q`` ref whose file this project lacks (the resolver's code)."""
    return result is not None and any(
        e.code == ERR_UNRESOLVED_REFERENCE.code and "#" in str(e.fields.get("ref"))
        for e in result.validation_errors
    )


def _should_retry_with_examples(
    exc: Exception, result: BoardRenderResult | None, current_project_dir: Path
) -> bool:
    """Retry against ``examples/`` for shared docs fixtures the repo root lacks."""
    if current_project_dir.name == "examples":
        return False
    return _names_missing_query_file(result) or bool(
        _SOURCE_NOT_FOUND_RE.search(str(exc))
    )


def _render_with_fallback(
    yaml_content: str,
    contexts: list[tuple[Path, Path]],
) -> str:
    """Render YAML against candidate (project_root, base_dir) contexts, retrying on retry-able errors."""
    last_exc: Exception | None = None
    for candidate_project_dir, candidate_base_dir in contexts:
        project = FilesystemProject(candidate_project_dir.resolve())
        registry = build_adapter_registry(project, read_only=True)
        result: BoardRenderResult | None = None
        try:
            # Fence-rendered YAML has no on-disk leaf — a pathless in-memory
            # board (no charts/meta.yml cascade). Relative extends/import refs
            # in a fenced board therefore anchor at charts/, not the doc's own
            # directory; docs fences use named sources / charts-root refs.
            board = InMemoryBoard(yaml_content, path=None)
            result = render_dashboard(
                board=board,
                project=project,
                adapter_registry=registry,
                result_cache=None,
                builtin_variables=lazy_dir_context(
                    project.directory_for_fspath(candidate_base_dir)
                ),
                format="svg",
            )
            raise_on_dashboard_failure(result)
            assert isinstance(result.data, str)
            return result.data
        except (FileNotFoundError, ValueError, OSError, RenderError) as exc:
            last_exc = exc
            if not _should_retry_with_examples(exc, result, candidate_project_dir):
                raise
        finally:
            registry.close()
    assert last_exc is not None
    raise last_exc


def _render_file_with_fallback(board_path: ProjectPath) -> str:
    """Render a board file inline via render_dashboard."""
    yaml_content = board_path.read_text()
    if board_path.is_markdown:
        # "In charts" is a charts/ dir within the project (the file's project-relative
        # path), not the project's own on-disk location.
        in_boards = CHARTS_SUBDIR in PurePosixPath(board_path.relpath).parts
        if not is_markdown_board_content(yaml_content, in_boards=in_boards):
            raise ValueError(MARKDOWN_NOT_BOARD_MESSAGE)
        yaml_content = markdown_to_yaml(yaml_content)
    return _render_with_fallback(yaml_content, _file_render_contexts(board_path.parent))


def _candidate_external_bases(
    project_dir: Path,
    source_base_dir: Path | None,
) -> list[Path]:
    """Return candidate base dirs for resolving external query references."""
    candidates: list[Path] = []
    for candidate in (
        source_base_dir,
        project_dir,
        project_dir / "examples",
    ):
        if candidate is None:
            continue
        resolved = candidate.resolve()
        if resolved not in candidates:
            candidates.append(resolved)
    return candidates


def _load_external_query_definition(
    query_ref: str,
    project_dir: Path,
    source_base_dir: Path | None,
) -> tuple[str, Any]:
    """Load a raw external query definition for playground-link inlining."""
    file_part, query_name = query_ref.split("#", 1)

    for candidate_base in _candidate_external_bases(project_dir, source_base_dir):
        candidate_file = (candidate_base / file_part).resolve()
        if not candidate_file.exists():
            continue

        raw = (
            yaml.load(candidate_file.read_text(encoding="utf-8"), Loader=YAML_LOADER)
            or {}
        )
        queries = raw.get("queries", {}) if isinstance(raw, dict) else {}
        if query_name in queries:
            return query_name, queries[query_name]
        raise ValueError(
            f"Query '{query_name}' not found in '{candidate_file}'. "
            f"Available queries: {', '.join(sorted(queries)) if queries else 'none'}"
        )

    raise FileNotFoundError(
        f"External query file not found for playground link: {file_part}"
    )


def _inline_playground_queries(
    yaml_source: str,
    project_dir: Path,
    source_base_dir: Path | None,
) -> str:
    """Rewrite external query refs into local inline queries for playground URLs."""
    doc = yaml.load(yaml_source, Loader=YAML_LOADER)
    if not isinstance(doc, dict):
        return yaml_source

    query_defs = doc.get("queries")
    if query_defs is None:
        query_defs = {}
        doc["queries"] = query_defs
    if not isinstance(query_defs, dict):
        return yaml_source

    inlined_any = False

    def visit(node: Any) -> None:
        nonlocal inlined_any
        if isinstance(node, dict):
            query_ref = node.get("query")
            if isinstance(query_ref, str) and "#" in query_ref:
                query_name, query_def = _load_external_query_definition(
                    query_ref,
                    project_dir,
                    source_base_dir,
                )
                existing = query_defs.get(query_name)
                if existing is None:
                    query_defs[query_name] = query_def
                elif existing != query_def:
                    suffix = 2
                    rewritten_name = f"{query_name}_{suffix}"
                    while (
                        rewritten_name in query_defs
                        and query_defs[rewritten_name] != query_def
                    ):
                        suffix += 1
                        rewritten_name = f"{query_name}_{suffix}"
                    query_defs.setdefault(rewritten_name, query_def)
                    query_name = rewritten_name
                node["query"] = query_name
                inlined_any = True

            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(doc)
    if not inlined_any:
        return yaml_source
    return yaml.safe_dump(doc, sort_keys=False)


def _pg_link(pg_url: str, extra_class: str = "") -> str:
    """Generate a playground link anchor tag."""
    cls = f"playground-link {extra_class}".strip()
    return (
        f'<a href="{pg_url}" target="_blank" class="{cls}" '
        f'rel="noopener noreferrer" title="Try in Playground">'
        f'<span class="playground-icon">\u25b6</span></a>'
    )


def _error_div(message: str) -> str:
    escaped = html.escape(message)
    return (
        f'<div class="dbt-charts-error">'
        f"<strong>dbt charts render error:</strong> {escaped}</div>"
    )


# ---------------------------------------------------------------------------
# fence_dbt_charts — render-only
# ---------------------------------------------------------------------------


def fence_dbt_charts(
    source: str,
    _language: str,
    _class_name: str,
    options: dict[str, Any],
    _md: Any,
    **kwargs: Any,
) -> str:
    """Superfences handler: render YAML to embedded SVG/HTML.

    Usage::

        ```dbt-charts
        charts:
          c1:
            type: bar
            x: product
            y: revenue
        ```

        ```dbt-charts {file=path/to/board.yml}
        ```
    """
    project = FilesystemProject(_resolve_project_dir())

    resolved_options = _fence_options(options, kwargs)
    file_opt = resolved_options.get("file")
    try:
        if file_opt:
            resolved = _resolve_file(file_opt, project.root)
            rendered = _render_file_with_fallback(project.path_for_fspath(resolved))
        else:
            yaml_source = source.strip()
            if not yaml_source:
                return _error_div(
                    "no YAML source provided (use inline content or file= option)"
                )
            rendered = _render_with_fallback(
                yaml_source, _inline_render_contexts(project.root)
            )
        return f'<div class="dbt-charts-embed">{rendered}</div>'
    except (FileNotFoundError, ValueError, OSError, RenderError) as exc:
        logger.warning("dbt charts render failed: %s", exc)
        return _error_div(str(exc))


# ---------------------------------------------------------------------------
# fence_dbt_charts_example — code + render
# ---------------------------------------------------------------------------


def fence_dbt_charts_example(
    source: str,
    _language: str,
    _class_name: str,
    options: dict[str, Any],
    _md: Any,
    **kwargs: Any,
) -> str:
    """Superfences handler: code-plus-render example layout.

    Options (via ``{key=value}`` in the info string):

    - ``file=path`` — read YAML from a file instead of inline
    - ``format=side-by-side`` (default), ``stacked``, ``render-only``, ``yaml-only``

    Usage::

        ```dbt-charts-example {file=charts/examples/bar-charts/minimum.yml}
        ```

        ```dbt-charts-example {format=stacked}
        charts:
          c1:
            type: bar
        ```
    """
    project = FilesystemProject(_resolve_project_dir())

    resolved_options = _fence_options(options, kwargs)
    layout = resolved_options.get("format", "side-by-side")
    if layout not in _VALID_LAYOUTS:
        return _error_div(
            f"unknown format {layout!r}, expected one of: "
            f"{', '.join(sorted(_VALID_LAYOUTS))}"
        )

    # Resolve source: file or inline
    file_opt = resolved_options.get("file")
    resolved_path: Path | None = None
    if file_opt:
        try:
            resolved_path = _resolve_file(file_opt, project.root)
            yaml_source = resolved_path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, ValueError, OSError) as exc:
            return _error_div(str(exc))
    else:
        yaml_source = source.strip()

    if not yaml_source:
        return _error_div("no YAML source provided")

    example_id = hashlib.md5(yaml_source.encode(), usedforsecurity=False).hexdigest()[
        :12
    ]
    playground_yaml = yaml_source
    try:
        playground_yaml = _inline_playground_queries(
            yaml_source,
            project.root,
            resolved_path.parent if resolved_path else project.root,
        )
    except (FileNotFoundError, ValueError, OSError, yaml.YAMLError) as exc:
        logger.warning("Playground payload rewrite failed: %s", exc)
    pg_url = _playground_url(playground_yaml)

    # yaml-only: no rendering
    if layout == "yaml-only":
        highlighted = _highlight_yaml(yaml_source)
        return (
            f'<div class="example-container example-yaml-only" '
            f'id="example-container-{example_id}">\n'
            f'<div class="example-code">\n{highlighted}\n'
            f"{_pg_link(pg_url, 'playground-link-inline')}\n"
            f"</div>\n</div>"
        )

    # Render the board
    rendered_html: str
    try:
        if resolved_path:
            rendered = _render_file_with_fallback(
                project.path_for_fspath(resolved_path)
            )
        else:
            rendered = _render_with_fallback(
                yaml_source, _inline_render_contexts(project.root)
            )
        rendered_html = f'<div class="rendered-dashboard">{rendered}</div>'
    except (FileNotFoundError, ValueError, OSError, RenderError) as exc:
        logger.warning("dbt charts render failed: %s", exc)
        rendered_html = _error_div(str(exc))

    # render-only: no code panel
    if layout == "render-only":
        return (
            f'<div class="example-container example-render-only" '
            f'id="example-container-{example_id}">\n'
            f'<div class="example-render" id="example-render-{example_id}" '
            f'data-example-modal-trigger="true">\n'
            f"{rendered_html}\n{_pg_link(pg_url)}\n</div>\n</div>"
        )

    # side-by-side (default) or stacked
    highlighted = _highlight_yaml(yaml_source)
    layout_class = " example-stacked" if layout == "stacked" else ""
    return (
        f'<div class="example-container{layout_class}" '
        f'id="example-container-{example_id}">\n'
        f'<div class="example-code">\n{highlighted}\n</div>\n'
        f'<div class="example-render" id="example-render-{example_id}" '
        f'data-example-modal-trigger="true">\n'
        f"{rendered_html}\n{_pg_link(pg_url)}\n</div>\n</div>"
    )
