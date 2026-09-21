"""Render command implementation."""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from dbt_charts.agent_api import Diagnostic, RenderFormat
from dbt_charts.cli._error_format import emit_diagnostics_jsonl, print_diagnostics
from dbt_charts.cli._parsing import cwd_first
from dbt_charts.cli._project import with_project
from dbt_charts.cli.filesystem_project import FilesystemProject

if TYPE_CHECKING:
    from dbt_charts.agent_api.boards import BoardRenderResult

# Maps common file extensions to render formats for format inference.
_EXT_TO_FORMAT: dict[str, RenderFormat] = {
    "svg": "svg",
    "html": "html",
    "htm": "html",
    "png": "png",
    "pdf": "pdf",
    "json": "json",
    "txt": "text",
    "yaml": "yaml",
    "yml": "yaml",
}


def infer_format_from_extension(output: str) -> RenderFormat | None:
    """Infer render format from output file extension. Returns None for unknown extensions."""
    return _EXT_TO_FORMAT.get(Path(output).suffix.lstrip(".").lower())


def expand_output_template(template: str, board: Path) -> str:
    """Expand {stem} and {dir} placeholders in an output path template."""
    return template.format(stem=board.stem, dir=str(board.parent))


class RenderFailed(Exception):
    """A single dashboard failed to render; carries the diagnostics to report.

    Raised instead of exiting so the batch driver (render_commands) can report
    the failure and continue to the next board — the process-exit decision
    belongs to the driver, not to a per-board render.
    """

    def __init__(self, errors: list[Diagnostic]) -> None:
        super().__init__(f"{len(errors)} render error(s)")
        self.errors = errors


def _validate_ignore_codes(ignore_codes: set[str]) -> None:
    """Exit 1 for any --ignore-warning code that isn't a registered WARN-* code.

    Every valid suppression code is a registered warning code. Two distinct
    failure cases get distinguishable messages: a code that is registered but
    at error level (suppressing an error is never valid) versus a code that
    isn't registered at all (a typo or stale suppression stub).
    """
    from dbt_charts.agent_api.diagnostics import REGISTRY as _diag_registry

    warning_codes = _diag_registry.codes(level="warning")
    error_codes = _diag_registry.codes(level="error")
    for code in sorted(ignore_codes):
        if code in warning_codes:
            continue
        if code in error_codes:
            print(
                f"cannot ignore {code!r}: it is an error code, not a warning code",
                file=sys.stderr,
            )
        else:
            print(f"unknown warning code: {code!r}", file=sys.stderr)
        sys.exit(1)


def _print_warnings(warnings: list[Diagnostic]) -> None:
    """Print render warnings to stderr under a count header.

    The header is a render-command concern, not a diagnostic-printing one:
    `print_diagnostics` also renders errors, where "N warnings:" would be wrong.
    """
    if not warnings:
        return
    n = len(warnings)
    label = "warning" if n == 1 else "warnings"
    print(f"⚠ {n} {label}:", file=sys.stderr)
    print_diagnostics(warnings)


def _merge_warnings_into_json(
    data: dict[str, object],
    result: BoardRenderResult,
) -> str:
    """For JSON format, inject warnings and suppressed_warnings.

    Agents and consumers reading --format json must see warnings regardless of
    --no-warnings (that flag is a human-output convenience only).
    data is always a dict here (board.py JSON path returns json.loads output).
    """
    base = dict(data)
    base["warnings"] = [w.model_dump() for w in result.warnings]
    base["suppressed_warnings"] = [w.model_dump() for w in result.suppressed_warnings]
    return _json.dumps(base)


def _emit_result(
    result: BoardRenderResult,
    output: str | None,
    format: RenderFormat,
    output_dir: Path,
    default_output_stem: str,
    source_label: str,
    no_warnings: bool,
    fail_on_chart_errors: bool,
    print0: bool,
    diagnostics_json: bool = False,
) -> str | None:
    """Turn a completed render into terminal/file output, or raise RenderFailed.

    Shared tail of render_command and render_command_from_yaml: a failed render
    (validation, or per-chart runtime errors when fail_on_chart_errors) raises
    RenderFailed carrying the diagnostics; a successful one prints warnings and
    writes the output.
    """
    if result.status == "failed":
        errors = result.validation_errors or (
            [result.board_error] if result.board_error else []
        )
        raise RenderFailed(errors)

    if fail_on_chart_errors and result.chart_errors:
        raise RenderFailed(result.chart_errors)

    if diagnostics_json:
        all_diags = list(result.warnings) + list(result.chart_errors)
        if all_diags:
            emit_diagnostics_jsonl(all_diags)
    elif not no_warnings:
        _print_warnings(result.warnings)

    if format in ("json", "data"):
        assert isinstance(result.data, dict)
        rendered_content: str | bytes = _merge_warnings_into_json(result.data, result)
    else:
        rendered_content = (
            result.data
            if isinstance(result.data, (str, bytes))
            else _json.dumps(result.data)
        )
    return _write_output(
        rendered_content,
        output=output,
        format=format,
        output_dir=output_dir,
        default_output_stem=default_output_stem,
        source_label=source_label,
        print0=print0,
    )


@with_project
def render_command(
    board_path: Path,
    output: str | None = None,
    format: RenderFormat = "svg",
    variables: dict[str, str] | None = None,
    use_cache: bool = True,
    cache_path: Path | None = None,
    no_warnings: bool = False,
    ignore_codes: set[str] | None = None,
    fail_on_chart_errors: bool = True,
    max_workers: int | None = None,
    print0: bool = False,
    chart: str | None = None,
    diagnostics_json: bool = False,
    *,
    project: FilesystemProject,
) -> str | None:
    """Render a dashboard to SVG, HTML, PNG, PDF, or terminal.

    Raises RenderFailed on a validation/chart failure or missing board; the
    batch driver (render_commands) reports it and decides the exit code.

    Args:
        board_path: Path to board YAML file
        output: Output file path. For binary formats (png, pdf) and svg/html,
               default is renders/ folder with board name. For json/text/yaml/data,
               default is stdout. Use "-" to force stdout. Ignored for terminal
               format (always prints to stdout).
        format: Output format (svg, html, png, pdf, terminal, json, text,
                yaml, data)
        project: The resolved dbt charts project (injected by @with_project)
        variables: Variable values to pass to the render (key=value pairs)
        use_cache: Whether to use cached query results. False (--no-cache) skips
            opening a cache backend entirely, overriding project config.
        cache_path: Persist the query-result cache to this DuckDB file (created
            if absent), overriding project config. None (the default) falls
            through to the project's dbt_charts.yml cache: block (path: null
            there means the zero-config in-memory default).
        no_warnings: Suppress stderr warning output (warnings still appear in JSON)
        ignore_codes: Warning codes to suppress via the partition seam
        fail_on_chart_errors: Exit 1 when any per-chart runtime error exists
            (CI-safe default). Pass False for live-preview / agent iteration.

    Returns:
        Output file path on success, None for terminal/stdout output
    """
    from dbt_charts.agent_api import ProjectSession
    from dbt_charts.agent_api._paths import (
        build_board_render_context,
        resolve_board_or_error,
    )
    from dbt_charts.agent_api.cache import project_cache_ctx

    if ignore_codes:
        _validate_ignore_codes(ignore_codes)

    # Resolved here rather than on the argument: `--output` templates and the
    # "Rendered …" line both read the board path as typed.
    ctx = build_board_render_context(cwd_first(board_path), project.root)
    output_dir = ctx.output_dir

    with (
        # use_cache=False (--no-cache) skips opening a cache backend entirely
        # rather than opening one and having every read/write no-op — the two
        # are behaviorally identical but this avoids the wasted I/O.
        project_cache_ctx(
            project, no_cache=not use_cache, cache_path=cache_path
        ) as cache,
        # from_project, not open(): the cache resolution above already needs the
        # project, and open() would build a second one from the same path.
        ProjectSession.from_project(project, cache=cache) as project_session,
    ):
        # Boards-first path resolution lives at the agent_api boundary
        # (resolve_board_or_error) so the command stays thin; a missing board
        # exits with the same structured ERR-FILE-NOT-FOUND envelope
        # --json callers already expect.
        resolved = resolve_board_or_error(ctx.scoped_path, project_session.project)
        if isinstance(resolved, Diagnostic):
            raise RenderFailed([resolved])
        result = project_session.render_board(
            board=resolved,
            chart=chart,
            format=format,
            variables=variables,
            use_cache=use_cache,
            # Match pre-PR behavior: 2x retina PNG by default for the CLI.
            scale=2.0,
            ignore_codes=ignore_codes,
            max_workers=max_workers,
            # `dct render` writes files. Nothing serves them, so an HTML export
            # carries its fonts inline rather than naming /static/fonts/.
            standalone=True,
        )

    return _emit_result(
        result,
        output,
        format,
        output_dir,
        default_output_stem=ctx.board_file.stem,
        source_label=str(board_path),
        no_warnings=no_warnings,
        fail_on_chart_errors=fail_on_chart_errors,
        print0=print0,
        diagnostics_json=diagnostics_json,
    )


@with_project
def render_command_from_yaml(
    yaml_content: str,
    output: str | None = None,
    format: RenderFormat = "svg",
    variables: dict[str, str] | None = None,
    use_cache: bool = True,
    cache_path: Path | None = None,
    diagnostics_json: bool = False,
    no_warnings: bool = False,
    ignore_codes: set[str] | None = None,
    fail_on_chart_errors: bool = True,
    max_workers: int | None = None,
    print0: bool = False,
    chart: str | None = None,
    *,
    project: FilesystemProject,
) -> str | None:
    """Render a dashboard from YAML content (e.g. stdin).

    Args:
        yaml_content: Raw YAML string
        output: Output file path. Binary formats and svg/html default to a
               renders/ file under project_dir; json/text/yaml/data go to stdout.
               Use "-" to force stdout. Ignored for terminal format.
        format: Output format (svg, html, png, pdf, terminal, json, text,
                yaml, data)
        project: The resolved dbt charts project (injected by @with_project)
        variables: Variable values to pass to the render
        use_cache: Whether to use cached query results. False (--no-cache) skips
            opening a cache backend entirely, overriding project config.
        cache_path: Persist the query-result cache to this DuckDB file (created
            if absent), overriding project config. None (the default) falls
            through to the project's dbt_charts.yml cache: block (path: null
            there means the zero-config in-memory default).
        diagnostics_json: Emit all diagnostics (errors and warnings) as JSON Lines
            to stderr. Stdout stays the render payload on success.
        no_warnings: Suppress stderr warning output (warnings still appear in JSON)
        ignore_codes: Warning codes to suppress via the partition seam
        fail_on_chart_errors: Exit 1 when any per-chart runtime error exists
            (CI-safe default). Pass False for live-preview / agent iteration.

    Returns:
        Output file path on success, None for terminal/stdout output. Exits 1
        (typer.Exit) after reporting on a validation/chart failure.
    """
    from dbt_charts.agent_api import InMemoryBoard, ProjectSession
    from dbt_charts.agent_api._paths import build_yaml_render_context
    from dbt_charts.agent_api.cache import project_cache_ctx

    if ignore_codes:
        _validate_ignore_codes(ignore_codes)

    ctx = build_yaml_render_context(project.root)
    output_dir = ctx.output_dir

    with (
        # use_cache=False (--no-cache) skips opening a cache backend entirely
        # rather than opening one and having every read/write no-op — the two
        # are behaviorally identical but this avoids the wasted I/O.
        project_cache_ctx(
            project, no_cache=not use_cache, cache_path=cache_path
        ) as cache,
        # from_project, not open(): the cache resolution above already needs the
        # project, and open() would build a second one from the same path.
        ProjectSession.from_project(project, cache=cache) as project_session,
    ):
        # stdin content has no on-disk location — a pathless in-memory board,
        # no meta.yml cascade.
        board = InMemoryBoard(yaml_content, path=None)
        result = project_session.render_board(
            board=board,
            chart=chart,
            format=format,
            variables=variables,
            use_cache=use_cache,
            scale=2.0,
            ignore_codes=ignore_codes,
            max_workers=max_workers,
            standalone=True,
        )

    # stdin is a standalone top-level command (not a batch member), so it owns
    # its own reporting and exit code rather than propagating RenderFailed.
    try:
        return _emit_result(
            result,
            output,
            format,
            output_dir,
            default_output_stem="stdin",
            source_label="<stdin>",
            no_warnings=no_warnings,
            fail_on_chart_errors=fail_on_chart_errors,
            print0=print0,
            diagnostics_json=diagnostics_json,
        )
    except RenderFailed as exc:
        if exc.errors:
            if diagnostics_json:
                emit_diagnostics_jsonl(exc.errors)
            else:
                print_diagnostics(exc.errors)
        raise typer.Exit(1) from exc


def _write_output(
    rendered_content: str | bytes,
    output: str | None,
    format: RenderFormat,
    output_dir: Path,
    default_output_stem: str,
    source_label: str,
    print0: bool = False,
) -> str | None:
    """Write rendered content to the appropriate destination."""
    # `terminal` is stdout-only by design — --output is meaningless for the
    # ANSI-formatted preview. Every other text format honors --output when set
    # and defaults to stdout otherwise (no implicit `renders/` write).
    if format == "terminal" or (
        format in ("json", "text", "yaml", "data") and output is None
    ):
        text_output = (
            rendered_content.decode()
            if isinstance(rendered_content, bytes)
            else rendered_content
        )
        print(text_output)
        return None

    if output == "-":
        if isinstance(rendered_content, bytes):
            sys.stdout.buffer.write(rendered_content)
        else:
            print(rendered_content)
        return None

    # Resolve output paths against output_dir (CWD / --project-dir), not project_root
    is_binary = format in ("png", "pdf")
    output_extension = f".{format}"

    if output:
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = output_dir / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        renders_dir = output_dir / "renders"
        renders_dir.mkdir(exist_ok=True)
        output_path = renders_dir / f"{default_output_stem}{output_extension}"

    if is_binary:
        content_bytes = (
            rendered_content.encode()
            if isinstance(rendered_content, str)
            else rendered_content
        )
        output_path.write_bytes(content_bytes)
    else:
        content_str = (
            rendered_content.decode()
            if isinstance(rendered_content, bytes)
            else rendered_content
        )
        output_path.write_text(content_str, encoding="utf-8")

    print(
        f"Rendered {source_label} to {output_path} ({format} format)",
        file=sys.stderr,
    )
    sys.stdout.write(str(output_path) + ("\0" if print0 else "\n"))
    sys.stdout.flush()
    return str(output_path)


def render_commands(
    board_paths: list[Path],
    output: str | None = None,
    format: RenderFormat = "svg",
    project_dir: Path | None = None,
    dbt_project_dir: Path | None = None,
    variables: dict[str, str] | None = None,
    use_cache: bool = True,
    cache_path: Path | None = None,
    diagnostics_json: bool = False,
    no_warnings: bool = False,
    ignore_codes: set[str] | None = None,
    fail_on_chart_errors: bool = True,
    max_workers: int | None = None,
    print0: bool = False,
    fail_fast: bool = False,
    chart: str | None = None,
) -> None:
    """Render one or more dashboards, printing output paths to stdout.

    For multiple boards, `output` must contain {stem} or {dir} placeholders
    (e.g. "renders/{stem}.svg"). With a single board, any output path is valid.

    Per-board errors are reported to stderr; processing continues unless
    `fail_fast=True`. Exits 1 if any board failed.
    """
    # ignore_codes is the same for every board — a driver-level precondition, not
    # a per-board outcome, so validate it once here before the loop.
    if ignore_codes:
        _validate_ignore_codes(ignore_codes)

    # Fail early when two inputs expand to the same output path — later renders
    # would silently overwrite earlier ones.
    if output and len(board_paths) > 1:
        planned: list[str] = [expand_output_template(output, f) for f in board_paths]
        seen: dict[str, Path] = {}
        for board_path, dest in zip(board_paths, planned, strict=True):
            if dest in seen:
                print(
                    f"Error: {board_path} and {seen[dest]} both expand to {dest!r}; "
                    "use a more specific template (add {{dir}} or rename the files)",
                    file=sys.stderr,
                )
                raise typer.Exit(1)
            seen[dest] = board_path

    has_failure = False
    for board_path in board_paths:
        board_output = expand_output_template(output, board_path) if output else None
        try:
            render_command(
                board_path=board_path,
                output=board_output,
                format=format,
                project_dir=project_dir,
                dbt_project_dir=dbt_project_dir,
                variables=variables,
                use_cache=use_cache,
                cache_path=cache_path,
                no_warnings=no_warnings,
                ignore_codes=ignore_codes,
                fail_on_chart_errors=fail_on_chart_errors,
                max_workers=max_workers,
                print0=print0,
                chart=chart,
                diagnostics_json=diagnostics_json,
            )
        except RenderFailed as exc:
            if exc.errors:
                if diagnostics_json:
                    emit_diagnostics_jsonl(exc.errors)
                else:
                    print_diagnostics(exc.errors)
            has_failure = True
            if fail_fast:
                raise typer.Exit(1) from exc
        except typer.Exit:
            # Project discovery failed (no project found, or a bad
            # --project-dir): @with_project already printed the clean message
            # and this is batch-fatal, not a per-board render error. Propagate
            # so it isn't mis-reported as "Error rendering <board>: 1" per board.
            raise
        except Exception as exc:  # noqa: BLE001 — unexpected per-board crash
            print(f"Error rendering {board_path}: {exc}", file=sys.stderr)
            has_failure = True
            if fail_fast:
                raise typer.Exit(1) from exc

    if has_failure:
        raise typer.Exit(1)
