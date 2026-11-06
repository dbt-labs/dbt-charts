"""A Python-Markdown extension that draws ``charts`` fences as charts.

One line turns it on. MkDocs::

    markdown_extensions:
      - dbt_charts.integrations.markdown

Pelican, keeping its default extensions::

    MARKDOWN = {
        "extension_configs": {
            "markdown.extensions.codehilite": {"css_class": "highlight"},
            "markdown.extensions.extra": {},
            "markdown.extensions.meta": {},
            "dbt_charts.integrations.markdown": {},
        },
        "output_format": "html5",
    }

A ``charts`` fence means what it means to ``dct``: one ``rows:`` item, with
query and chart names shared across the page. A ``charts-example`` fence
draws a whole board beside its highlighted YAML, with a playground link; a
page with one carries the stylesheet for its layouts, once.

Options:

- ``project_dir``: the dbt Charts project the charts read sources and files
  from. Default: the nearest ``dbt_charts.yml`` above the working directory,
  else the git root, else the working directory itself. Inline data needs no
  project at all.
- ``embed_fonts``: carry the fonts in the page, each face once (default). Turn
  it off only for a site that serves the font files at ``/static/fonts/``.

A fence that fails raises ``ChartFenceError``, so a broken chart fails the build.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import zlib
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from markdown import Markdown
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor

from dbt_charts.agent_api import ProjectSession
from dbt_charts.agent_api.cache import project_cache_ctx
from dbt_charts.agent_api.markdown import render_chart_fences, splice_lines
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import (
    BoardRenderResult,
    raise_on_dashboard_failure,
    render_dashboard,
)
from dbt_charts.core.compile.parse.markdown import (
    has_chart_fence,
    is_markdown_board_content,
    markdown_to_yaml,
)
from dbt_charts.core.compile.template.markdown_fence import (
    MASKED_FENCE_RE,
    mask_fenced_code,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_MARKDOWN_NOT_BOARD,
    ERR_UNRESOLVED_REFERENCE,
)
from dbt_charts.core.execute.env import build_exec_env, close_env
from dbt_charts.core.project import (
    CHARTS_SUBDIR,
    InMemoryBoard,
    ProjectDirectory,
    ProjectPath,
)
from dbt_charts.core.project_roots import find_dct_root, find_repo_root
from dbt_charts.core.render.dir_context import lazy_dir_context
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.font_selection import hoist_embedded_font_faces
from dbt_charts.core.utils import YAML_LOADER
from dbt_charts.integrations.highlighting import highlight_board_yaml

logger = logging.getLogger("dbt_charts.integrations.markdown")

_PLAYGROUND_URL = "https://play.dbtcharts.com"
_EXAMPLE_CSS = (files("dbt_charts.integrations") / "markdown.css").read_text(
    encoding="utf-8"
)
_VALID_LAYOUTS = {"side-by-side", "stacked", "render-only", "yaml-only", "tab"}
_EXAMPLE_OPTIONS = {"file", "format", "allow-chart-errors"}
# Any indent: an example may sit in a tab or an admonition, as with superfences.
_EXAMPLE_FENCE_RE = re.compile(
    r"([ \t]*)(?:`{3,}|~{3,})[ \t]*charts-example(?![\w-])[ \t]*(?:\{(.*)\})?[ \t]*$"
)
_OPTION_RE = re.compile(r'([\w-]+)=("[^"]*"|\S+)')

# A page has no location of its own here: it is read as a note in the
# project's charts/ folder, so file paths resolve from there and charts/meta.yml
# (a default source, the theme, the footer) applies to it.
_PAGE_RELPATH = f"{CHARTS_SUBDIR}/page.md"


class ChartFenceError(ValueError):
    """A fence on the page failed to render."""


def _resolve_project_dir(configured: str) -> Path:
    """The configured project, else the nearest dbt Charts project or git root."""
    if configured:
        return Path(configured).resolve()
    cwd = Path.cwd().resolve()
    return find_dct_root(cwd) or find_repo_root(cwd) or cwd


def _highlight_yaml(source: str) -> str:
    """Syntax-highlight dbt charts board YAML using DbtChartsYamlLexer.

    SQL inside ``sql: |`` and ``query: |`` block scalars is delegated to
    SqlLexer so SQL keywords receive keyword styling.

    Returns HTML wrapped in
    ``<div class="dbt-charts-example-yaml"><pre><code>...</code></pre></div>``.
    The ``nohighlight`` class, and no ``language-`` class, keeps a theme's
    client-side highlighter (highlight.js in MkDocs' own theme, Prism) from
    recoloring a block that is already highlighted.
    """
    highlighted = highlight_board_yaml(source)
    return (
        f'<div class="dbt-charts-example-yaml"><pre><code class="nohighlight">'
        f"{highlighted}</code></pre></div>"
    )


def _playground_url(yaml_source: str) -> str:
    """Generate a hosted-playground URL with compressed YAML payload."""
    compressed = zlib.compress(yaml_source.encode("utf-8"), level=9)
    encoded = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
    return f"{_PLAYGROUND_URL}/?y={encoded}"


def _resolve_file(file_path: str, project_dir: Path) -> Path:
    """Resolve a file= path against project_dir with traversal guard.

    Errors name the path as written: an absolute path would print the build
    machine's layout into the page's build log.
    """
    resolved = (project_dir / file_path).resolve()
    if not resolved.is_relative_to(project_dir.resolve()):
        raise ValueError(f"Path escapes project directory: {file_path}")
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    return resolved


def _shared_examples_dir(project_dir: Path) -> Path | None:
    """Return ``examples/playground/`` when it ships a ``dbt_charts.yml`` project root.

    Docs ``charts-example`` fences declare playground sources — ``examples_db``
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
    ``FilesystemProject`` and exec env (the project root and the shared
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


def _env_cache_path() -> Path | None:
    """``DCT_CACHE_PATH``, which places the query cache for ``dct`` as well."""
    value = os.environ.get("DCT_CACHE_PATH")
    return Path(value) if value else None


def _render_with_fallback(
    yaml_content: str,
    contexts: list[tuple[Path, Path]],
    standalone: bool,
    allow_chart_errors: bool,
) -> str:
    """Render YAML against candidate (project_root, base_dir) contexts, retrying on retry-able errors."""
    last_exc: Exception | None = None
    for candidate_project_dir, candidate_base_dir in contexts:
        project = FilesystemProject(candidate_project_dir.resolve())
        env = build_exec_env(project, read_only=True)
        result: BoardRenderResult | None = None
        try:
            # Fence-rendered YAML has no on-disk leaf — a pathless in-memory
            # board (no charts/meta.yml cascade). Relative extends/import refs
            # in a fenced board therefore anchor at charts/, not the doc's own
            # directory; docs fences use named sources / charts-root refs.
            board = InMemoryBoard(yaml_content, path=None)
            with project_cache_ctx(project, cache_path=_env_cache_path()) as cache:
                result = render_dashboard(
                    board=board,
                    project=project,
                    env=env,
                    result_cache=cache,
                    builtin_variables=lazy_dir_context(
                        project.directory_for_fspath(candidate_base_dir)
                    ),
                    format="svg",
                    standalone=standalone,
                )
            raise_on_dashboard_failure(result)
            if result.chart_errors and not allow_chart_errors:
                raise ChartFenceError(_describe(result.chart_errors))
            assert isinstance(result.data, str)
            return result.data
        except (FileNotFoundError, ValueError, OSError, RenderError) as exc:
            last_exc = exc
            if not _should_retry_with_examples(exc, result, candidate_project_dir):
                raise
        finally:
            close_env(env)
    assert last_exc is not None
    raise last_exc


def _render_file_with_fallback(
    board_path: ProjectPath, standalone: bool, allow_chart_errors: bool
) -> str:
    """Render a board file inline via render_dashboard."""
    yaml_content = board_path.read_text()
    if board_path.is_markdown:
        # "In charts" is a charts/ dir within the project (the file's project-relative
        # path), not the project's own on-disk location.
        in_boards = CHARTS_SUBDIR in PurePosixPath(board_path.relpath).parts
        if not is_markdown_board_content(yaml_content, in_boards=in_boards, named=True):
            raise ValueError(ERR_MARKDOWN_NOT_BOARD.message_template)
        yaml_content = markdown_to_yaml(yaml_content)
    return _render_with_fallback(
        yaml_content,
        _file_render_contexts(board_path.parent),
        standalone,
        allow_chart_errors,
    )


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


def _pg_link(pg_url: str) -> str:
    """Generate a playground link anchor tag."""
    return (
        f'<a href="{pg_url}" target="_blank" class="dbt-charts-playground-link" '
        f'rel="noopener noreferrer" title="Try in Playground">'
        f'<span class="dbt-charts-playground-icon">\u25b6</span></a>'
    )


def _example_html(
    source: str, options: dict[str, str], project_dir: Path, standalone: bool
) -> str:
    """A ``charts-example`` fence: its YAML beside the board it draws.

    Options: ``file=path`` reads the YAML from a file instead;
    ``format=side-by-side`` (default), ``stacked``, ``render-only``,
    ``yaml-only`` or ``tab``, the board with its YAML in a drawer under it;
    ``allow-chart-errors=true`` draws a chart whose query fails as its error
    card instead of failing the build, for a page that shows one.
    """
    unknown = set(options) - _EXAMPLE_OPTIONS
    if unknown:
        raise ChartFenceError(
            f"unknown charts-example option {sorted(unknown)[0]!r}, expected "
            f"one of: {', '.join(sorted(_EXAMPLE_OPTIONS))}"
        )
    allow = options.get("allow-chart-errors", "false")
    if allow not in ("true", "false"):
        raise ChartFenceError(f"allow-chart-errors takes true or false, got {allow!r}")
    allow_chart_errors = allow == "true"
    project = FilesystemProject(project_dir)
    layout = options.get("format", "side-by-side")
    if layout not in _VALID_LAYOUTS:
        raise ChartFenceError(
            f"unknown format {layout!r}, expected one of: "
            f"{', '.join(sorted(_VALID_LAYOUTS))}"
        )

    file_opt = options.get("file")
    resolved_path = _resolve_file(file_opt, project.root) if file_opt else None
    yaml_source = (
        resolved_path.read_text(encoding="utf-8").strip()
        if resolved_path
        else source.strip()
    )
    if not yaml_source:
        raise ChartFenceError("a charts-example fence holds no YAML")

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

    container = (
        f'<div class="dbt-charts-example dbt-charts-example--{layout}" '
        f'id="dbt-charts-example-{example_id}">\n'
    )
    if layout == "yaml-only":
        return (
            f'{container}<div class="dbt-charts-example-code">\n'
            f"{_highlight_yaml(yaml_source)}\n"
            f"{_pg_link(pg_url)}\n"
            f"</div>\n</div>"
        )

    if resolved_path:
        rendered = _render_file_with_fallback(
            project.path_for_fspath(resolved_path), standalone, allow_chart_errors
        )
    else:
        rendered = _render_with_fallback(
            yaml_source,
            _inline_render_contexts(project.root),
            standalone,
            allow_chart_errors,
        )
    render = (
        f'<div class="dbt-charts-example-render" '
        f'id="dbt-charts-example-render-{example_id}">\n'
        f'<div class="dbt-charts-example-board">{rendered}</div>\n'
        f"{_pg_link(pg_url)}\n</div>\n"
    )
    if layout == "render-only":
        return f"{container}{render}</div>"
    code = (
        f'<div class="dbt-charts-example-code">\n{_highlight_yaml(yaml_source)}\n'
        "</div>\n"
    )
    if layout == "tab":
        return (
            f'{container}{render}<details class="dbt-charts-example-drawer">\n'
            '<summary><span class="dbt-charts-example-drawer-tab">'
            "See the YAML</span></summary>\n"
            f"{code}</details>\n</div>"
        )
    return f"{container}{code}{render}</div>"


def _example_fences(text: str) -> list[tuple[int, int, str, dict[str, str]]]:
    """Each ``charts-example`` fence: its lines, its dedented body, its options.

    Fences are found by the same rules as chart fences, so an example shown
    inside a longer fence stays code.
    """
    masked, fences = mask_fenced_code(text)
    found: list[tuple[int, int, str, dict[str, str]]] = []
    line = 0
    for piece in MASKED_FENCE_RE.split(masked):
        source = fences.get(piece, piece)  # type-state: silent_fallback — not a fence
        opener, *body = source.rstrip("\n").split("\n")
        match = _EXAMPLE_FENCE_RE.match(opener) if piece in fences else None
        if match is not None:
            indent = match.group(1)
            dedented = [
                b[len(indent) :] if b.startswith(indent) else b.lstrip()
                for b in body[:-1]
            ]
            options = {
                key: value.strip('"')
                for key, value in _OPTION_RE.findall(match.group(2) or "")
            }
            found.append((line, line + len(body), "\n".join(dedented), options))
        line += source.count("\n")
    return found


def _describe(diagnostics: list[Any]) -> str:
    return "\n".join(f"{d.code}: {d.message}" for d in diagnostics)


class _ChartFencePreprocessor(Preprocessor):
    """Swap each fence on the page for its rendered chart.

    Runs on the whole page, before the code-fence preprocessors, because a
    ``charts`` fence can name what another fence declares.
    """

    def __init__(
        self,
        md: Markdown,
        project_dir: str,
        embed_fonts: bool,
        variables: dict[str, str],
    ) -> None:
        super().__init__(md)
        self.project_dir = project_dir
        self.embed_fonts = embed_fonts
        self.variables = variables

    def run(self, lines: list[str]) -> list[str]:
        text = "\n".join(lines)
        examples = _example_fences(text)
        if not examples and not has_chart_fence(text):
            return lines
        project_dir = _resolve_project_dir(self.project_dir)
        spans: list[tuple[int, int, str]] = []
        if has_chart_fence(text):
            project = FilesystemProject(project_dir)
            with (
                project_cache_ctx(project, cache_path=_env_cache_path()) as cache,
                ProjectSession.from_project(project, cache=cache) as session,
            ):
                page = render_chart_fences(
                    session,
                    InMemoryBoard(text, path=project.path(_PAGE_RELPATH)),
                    self.embed_fonts,
                    variables=self.variables,
                )
            if page.errors:
                raise ChartFenceError(_describe(page.errors))
            for warning in page.warnings:
                logger.warning("%s: %s", warning.code, warning.message)
            spans = [
                (
                    f.first_line,
                    f.last_line,
                    f'<div class="dbt-charts-embed">{f.svg}</div>',
                )
                for f in page.fences
            ]
            if lines[0].strip() == "---":
                # Frontmatter is the page's metadata, never its prose; the
                # compile above read it and checked its closing delimiter.
                closing = next(
                    i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"
                )
                spans.append((0, closing, ""))
        for first, last, body, options in examples:
            try:
                spans.append(
                    (
                        first,
                        last,
                        _example_html(body, options, project_dir, self.embed_fonts),
                    )
                )
            except (FileNotFoundError, ValueError, OSError, RenderError) as exc:
                raise ChartFenceError(
                    f"charts-example fence on line {first + 1}: {exc}"
                ) from exc
        spans.sort()
        fragments = [fragment for _, _, fragment in spans]
        css = _EXAMPLE_CSS if examples else ""
        if self.embed_fonts:
            font_css, fragments = hoist_embedded_font_faces(fragments)
            css = font_css + css
        if css:
            fragments[0] = f"<style>\n{css}</style>\n{fragments[0]}"
        stashed = [
            (
                first,
                last,
                f"\n{self.md.htmlStash.store(fragment)}\n" if fragment else "",
            )
            for (first, last, _), fragment in zip(spans, fragments, strict=True)
        ]
        return splice_lines(text, stashed).split("\n")


class DbtChartsExtension(Extension):
    """Draw ``charts`` and ``charts-example`` fences as charts."""

    def __init__(self, **kwargs: Any) -> None:
        self.config = {
            "project_dir": [
                "",
                "dbt Charts project directory. Default: the nearest dbt_charts.yml "
                "or git root above the working directory",
            ],
            "embed_fonts": [
                True,
                "Carry the fonts in the page. Off only for a site that serves "
                "them at /static/fonts/",
            ],
            "variables": [{}, "Values for the page's variables, by name"],
        }
        super().__init__(**kwargs)

    def extendMarkdown(self, md: Markdown) -> None:
        # After `meta` (27) strips Pelican's metadata, before the fenced-code
        # preprocessors (25) claim the fences.
        md.preprocessors.register(
            _ChartFencePreprocessor(
                md,
                self.getConfig("project_dir"),
                self.getConfig("embed_fonts"),
                self.getConfig("variables"),
            ),
            "dbt_charts",
            26,
        )


def makeExtension(**kwargs: Any) -> DbtChartsExtension:
    """The entry point Python-Markdown loads this module by name through."""
    return DbtChartsExtension(**kwargs)
