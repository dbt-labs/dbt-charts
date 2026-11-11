"""Typed validate verb — fast YAML schema + cross-reference validation.

No warehouse connection, no query execution (unless --warehouse is passed).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts import _engine
from dbt_charts._docs_site import DEFAULT_DOCS_SITE_URL, docs_site_url
from dbt_charts.agent_api._paths import (
    resolution_error_diagnostic,
    resolve_board_path,
)
from dbt_charts.core.board import board_warning_codes, partition_compile_warnings
from dbt_charts.core.compile.engine import engine_compile
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import missing_format_columns
from dbt_charts.core.compile.host_facts import parse_host_facts
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.resolve.chart.positional import (
    channel_error,
    is_unbound,
)
from dbt_charts.core.compile.validate.chart_color_tokens import (
    chart_color_token_errors,
)
from dbt_charts.core.diagnostics import (
    ERR_FILE_NOT_FOUND,
    ERR_INTERNAL,
    ERR_LOCKED_SCHEMA_VERSION,
    ERR_NO_BOARD_FILES,
    ERR_QUERY_FORMAT_COLUMN_MISSING,
    ERR_UNRESOLVED_REFERENCE,
    ERR_WAREHOUSE_QUERY_INVALID,
    REGISTRY,
    WARN_COLUMN_CHECK_UNAVAILABLE,
    WARN_WAREHOUSE_CHECK_UNAVAILABLE,
    Diagnostic,
    SourceRange,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.registry import ErrorCode
from dbt_charts.core.execute.chart_columns import (
    chart_column_refs,
    missing_column_error,
)
from dbt_charts.core.project import (
    CHARTS_SUBDIR,
    PROJECT_CONFIG_NAME,
    Project,
    ProjectPath,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.compiler import CompileResult
    from dbt_charts.core.execute.env import ExecEnv


class ValidateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    path: str = Field(description="Project-relative path to the validated board.")
    error: str | None = Field(
        default=None,
        description=(
            "Host refusal (e.g. access denied) reported in-band; compile "
            "findings go in `errors`, never here."
        ),
    )
    errors: list[Diagnostic] = Field(
        default_factory=list, description="Validation errors found in the board file."
    )
    warnings: list[Diagnostic] = Field(
        default_factory=list,
        description=(
            "Non-fatal warnings as Diagnostic objects with stable codes. "
            "Each warning carries code, message, and an optional fix hint."
        ),
    )


def validate_paths(
    paths: list[Path] | None,
    *,
    project: Project,
    env: ExecEnv | None = None,
) -> list[ValidateResult]:
    """Validate N board files / directories, or default ``charts/`` when empty.

    Each argv path expands like the single-path verb did: a file validates
    directly, a directory walks recursively (skipping ``_*.yml`` partials and
    ejected inspect-template directories). Empty / ``None`` defaults to
    ``<project_root>/charts``. Results are concatenated in argv order.

    ``env`` opts into the warehouse tier (``--warehouse``). It is
    additive by construction: the same schema and cross-reference validation
    runs first, and warehouse findings are appended to its result.
    """
    if not paths:
        return _validate_one_path(None, project, env)
    out: list[ValidateResult] = []
    for p in paths:
        out.extend(_validate_one_path(p, project, env))
    return out


def _validate_one_path(
    path: Path | None,
    project: Project,
    env: ExecEnv | None = None,
) -> list[ValidateResult]:
    """Per-argv expansion: ``None`` → charts/, file → [one], dir → walk."""
    from dbt_charts.agent_api._paths import iter_expanded_board_files

    raw_path = path if path is not None else Path(CHARTS_SUBDIR)

    try:
        resolved = (
            resolve_board_path(Path(CHARTS_SUBDIR), project)
            if path is None
            else resolve_board_path(path, project)
        )
    except ValueError as exc:
        # resolve_board_path failed — raw_path never became a ProjectPath, so
        # there is no root to relativize against via posix_relpath. Echo the
        # raw input POSIX-normalized (not a project identity, just consistent
        # display for an input that never resolved).
        return [
            ValidateResult(
                success=False,
                path=raw_path.as_posix(),
                errors=[resolution_error_diagnostic(exc)],
            )
        ]

    if resolved.is_yaml or resolved.is_markdown:
        return [_validate_resolved(resolved, project, env, True)]

    # No explicit path -> validate every board root (host + installed
    # packages), not just the host charts/ dir `resolved` degenerated to.
    under = None if path is None else resolved.relpath
    boards = iter_expanded_board_files(project, under, include_markdown=True)
    if not boards:
        return [
            ValidateResult(
                success=False,
                path=resolved.relpath,
                errors=[
                    DbtChartsError.from_code(
                        ERR_NO_BOARD_FILES, path=resolved.relpath
                    ).to_diagnostic()
                ],
            )
        ]
    return [_validate_resolved(pf, project, env, False) for pf in boards]


def validate(path: Path, *, project: Project) -> ValidateResult:
    """Fast YAML schema + cross-reference validation. No warehouse, no execute."""
    try:
        resolved = resolve_board_path(path, project)
    except ValueError as exc:
        # resolve_board_path failed — path never became a ProjectPath, so there
        # is no root to relativize against via posix_relpath. Echo the raw
        # input POSIX-normalized (not a project identity, just consistent
        # display for an input that never resolved).
        return ValidateResult(
            success=False,
            path=path.as_posix(),
            errors=[resolution_error_diagnostic(exc)],
        )

    return _validate_resolved(resolved, project, None, True)


def _refusal_diagnostics(
    refusal: _engine.CompileError, relpath: str
) -> list[Diagnostic]:
    """The engine's coded refusals as diagnostics."""
    out: list[Diagnostic] = []
    for found in refusal.diagnostics:
        code = REGISTRY.get(found["code"])
        if not isinstance(code, ErrorCode):
            raise TypeError(f"engine refused with non-error code {found['code']}")
        diagnostic = DbtChartsError.from_code(code, **found["params"]).to_diagnostic(
            file=relpath
        )
        placed: dict[str, object] = {}
        if (path := found.get("path")) is not None:
            placed["path"] = path
        if (hint := found.get("hint")) is not None:
            # The engine reads no environment, so its docs link is the shipped default.
            placed["hint"] = hint.replace(DEFAULT_DOCS_SITE_URL, docs_site_url())
        if (located := found.get("range")) is not None:
            placed["range"] = SourceRange.model_validate(located)
        if code is ERR_UNRESOLVED_REFERENCE and "context" in diagnostic.fields:
            # `fields` holds the bare place; only the message's `{context}` carries " in ".
            bare = diagnostic.fields["context"].removeprefix(" in ")
            placed["fields"] = {**diagnostic.fields, "context": bare}
        out.append(diagnostic.model_copy(update=placed))
    return out


def _validate_resolved(
    resolved: ProjectPath,
    project: Project,
    env: ExecEnv | None,
    named: bool,
) -> ValidateResult:
    """Validate an already-resolved board path — no re-resolution.

    *named* is true for a path the user typed, false for one a folder listed.
    """
    from dbt_charts.core.compile.compiler import compile_file, validate_compiled_queries
    from dbt_charts.core.compile.host_facts import sql_host_queries
    from dbt_charts.core.compile.validate.links import iter_board_links
    from dbt_charts.core.dbt_model_columns import check_model_columns
    from dbt_charts.core.dbt_ref_check import check_manifest_refs
    from dbt_charts.core.link_targets import check_link_targets
    from dbt_charts.core.session_time_zone_check import check_session_time_zones

    if not resolved.exists():
        return ValidateResult(
            success=False,
            path=resolved.relpath,
            errors=[
                DbtChartsError.from_code(
                    ERR_FILE_NOT_FOUND,
                    path=resolved.relpath,
                ).to_diagnostic()
            ],
        )

    if resolved.relpath == PROJECT_CONFIG_NAME:
        from dbt_charts.agent_api._paths import project_config_diagnostics

        errors = project_config_diagnostics(resolved)
        return ValidateResult(success=not errors, path=resolved.relpath, errors=errors)

    from dbt_charts.agent_api._paths import is_patch_fragment

    if is_patch_fragment(resolved):
        from dbt_charts.agent_api._paths import patch_file_diagnostics

        errors = patch_file_diagnostics(resolved)
        return ValidateResult(success=not errors, path=resolved.relpath, errors=errors)

    from dbt_charts.core.compile.migrations.locked_schema import locked_schema_errors

    try:
        warnings_ignore = project.warnings_ignore  # also reads the lock
    except CompilationError as exc:
        return ValidateResult(
            success=False,
            path=resolved.relpath,
            errors=[exc.to_diagnostic(file=PROJECT_CONFIG_NAME)],
        )

    # Phase 1: the locked release's grammar, before the compile migrates to DEV.
    try:
        lock_errors = [
            DbtChartsError.from_code(
                ERR_LOCKED_SCHEMA_VERSION, message=message
            ).to_diagnostic(file=resolved.relpath)
            for message in locked_schema_errors(resolved, resolved.read_text(), project)
        ]
    except OSError:
        lock_errors = []  # compile_file below reports the unreadable file

    try:
        result = compile_file(resolved.read_board(named), declared_sources=env is None)
    except OSError as exc:
        return ValidateResult(
            success=False,
            path=resolved.relpath,
            errors=[
                DbtChartsError.from_code(ERR_INTERNAL, message=str(exc)).to_diagnostic(
                    file=resolved.relpath
                )
            ],
        )
    except CompilationError as exc:
        return ValidateResult(
            success=False,
            path=resolved.relpath,
            errors=[*lock_errors, exc.to_diagnostic(file=resolved.relpath)],
        )

    validate_compiled_queries(result)
    board_sources = result.board.sources if result.board else {}
    refused: list[Diagnostic] = []
    if result.board is None:
        host_queries = {}
    elif resolved.is_markdown:
        # The engine compiles YAML boards; a markdown board's queries come from
        # the Python compile until it reads them.
        host_queries = sql_host_queries(result.query_registry)
    else:
        try:
            compiled = engine_compile(resolved)
        except _engine.CompileError as refusal:
            refused = _refusal_diagnostics(refusal, resolved.relpath)
            host_queries = {}
        else:
            host_queries = parse_host_facts(compiled.host_facts_json())["queries"]
    for diagnostic in check_manifest_refs(host_queries, project, board_sources):
        (result.errors if diagnostic.level == "error" else result.warnings).append(
            diagnostic
        )
    check_session_time_zones(result, project)
    for diagnostic in check_model_columns(host_queries, project, board_sources):
        if diagnostic.level == "warning":
            result.warnings.append(diagnostic)
        elif diagnostic not in result.errors:
            result.errors.append(diagnostic)
    if result.board is not None:
        result.warnings += check_link_targets(
            iter_board_links(result.board), project, resolved.relpath
        )
    check_channel_sets(result, resolved.relpath)

    errors = [*lock_errors, *result.errors]
    if result.board is not None:
        errors += [
            e.to_diagnostic(file=resolved.relpath)
            for e in chart_color_token_errors(result.board)
        ]
    # The Python checks may have reported the same defect the engine refused.
    reported = {e.code for e in errors}
    errors += [d for d in refused if d.code not in reported]
    warnings = list(result.warnings)

    # The warehouse tier runs last and only on a board that already compiled —
    # it appends to the stateless findings rather than standing in for them.
    if env is not None and not errors:
        wh_errors, wh_warnings = _warehouse_findings(result, resolved.relpath, env)
        errors += wh_errors
        warnings += wh_warnings

    chart_codes: dict[str, set[str]] = {}
    if resolved.is_markdown:
        # The engine compiles YAML boards only; a markdown board's charts are read off the
        # Python board until it reads markdown too.
        chart_codes = board_warning_codes(result.board)
    elif result.board is not None:
        try:
            facts = parse_host_facts(engine_compile(resolved).host_facts_json())
        except _engine.CompileError:
            # validate tolerates what the engine refuses (a source the project does not
            # declare, a palette stop that does not exist), and reports it itself.
            chart_codes = board_warning_codes(result.board)
        else:
            chart_codes = {c: set(v) for c, v in facts["warnings_ignore"].items()}
    warnings, _suppressed = partition_compile_warnings(
        warnings, chart_codes, None, warnings_ignore
    )
    return ValidateResult(
        success=len(errors) == 0,
        path=resolved.relpath,
        errors=errors,
        warnings=warnings,
    )


def annotate_with_data_lint(
    results: list[ValidateResult],
    *,
    project: Project,
) -> list[ValidateResult]:
    """Augment validate results with data alias typo lint.

    For each result that compiled successfully, reads its aliases and checks
    any /data/ prefixed ones against the configured source names (config only,
    no DB connection). Appends Diagnostics to results in place and updates
    success=False when errors are found.
    """
    from dbt_charts.agent_api.data_paths import data_alias_errors_for_file

    try:
        source_names = frozenset(project.declared_sources.sources.keys())
    except CompilationError as exc:
        # The registry is project-level: report it once against the config
        # file instead of letting the lazy load crash the verb.
        diagnostic = exc.to_diagnostic(file=PROJECT_CONFIG_NAME)
        if any(
            r.path == PROJECT_CONFIG_NAME
            and any(e.code == diagnostic.code for e in r.errors)
            for r in results
        ):
            return results
        config_result = ValidateResult(
            success=False,
            path=PROJECT_CONFIG_NAME,
            errors=[diagnostic],
        )
        return [*results, config_result]

    annotated: list[ValidateResult] = []
    for result in results:
        project_path = project.path(result.path)
        try:
            path_exists = project_path.exists()
        except ValueError:
            # Fires for a genuine root-escape (an earlier resolve_board_path
            # failure) or a not-found result echoing a raw caller-supplied
            # path that isn't project-relative. Either way there's nothing to
            # lint through the seam; keep the result unchanged.
            annotated.append(result)
            continue
        if not path_exists:
            annotated.append(result)
            continue
        alias_msgs = data_alias_errors_for_file(
            result.path, source_names=source_names, project=project
        )
        if not alias_msgs:
            annotated.append(result)
            continue
        new_errors = list(result.errors) + [
            DbtChartsError.from_code(ERR_INTERNAL, message=msg).to_diagnostic(
                file=result.path
            )
            for msg in alias_msgs
        ]
        annotated.append(
            ValidateResult(
                success=False,
                path=result.path,
                errors=new_errors,
                warnings=result.warnings,
                error=result.error,
            )
        )
    return annotated


def check_channel_sets(compile_result: CompileResult, relpath: str) -> None:
    """Fail each chart that names part of its channel set and not the rest.

    Needs no data: a `y` with no `x` is wrong whatever the query returns. The
    other half of the rule, an unnamed chart whose column count fits no shape,
    needs the warehouse's columns (`_warehouse_findings`) or the rows at
    render. Mutates ``compile_result.errors``.
    """
    if compile_result.board is None:
        return
    for chart in compile_result.board.charts.values():
        error = channel_error(chart, ())
        if error is not None:
            compile_result.errors.append(error.to_diagnostic(file=relpath))


def _warehouse_findings(
    compile_result: CompileResult,
    relpath: str,
    env: ExecEnv,
) -> tuple[list[Diagnostic], list[Diagnostic]]:
    """Check every SQL query of a compiled board against its warehouse.

    Returns (errors, warnings) to append to the stateless findings — this tier
    only ever adds. A query the adapter cannot check without running it comes
    back as a warning saying so, never as a silent pass.
    """
    from dbt_charts.core.compile.models.query.normalized import is_sql_query
    from dbt_charts.core.execute.warehouse_check import warehouse_check

    # This tier runs only when compilation reported no errors, and every
    # CompileResult that carries no errors carries a board.
    board = compile_result.board
    assert board is not None

    # query name -> [(chart name, channel label, column name)] it must contain.
    refs_by_query: dict[str, list[tuple[str, str, str]]] = {}
    # query name -> the charts that bind its columns by position.
    unbound_by_query: dict[str, list[Chart]] = {}
    for chart_name, chart in board.charts.items():
        for query_name, label, column in chart_column_refs(chart):
            refs_by_query.setdefault(query_name, []).append((chart_name, label, column))
        if is_unbound(chart) and chart.query_name is not None:
            unbound_by_query.setdefault(chart.query_name, []).append(chart)

    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []

    for query_name, query in compile_result.query_registry.items():
        if not is_sql_query(query):
            warnings.append(
                Diagnostic.from_code(
                    WARN_WAREHOUSE_CHECK_UNAVAILABLE,
                    message=WARN_WAREHOUSE_CHECK_UNAVAILABLE.message_template.format(
                        name=query_name,
                        reason=(
                            f"a {query.query_type} query carries no SQL for the "
                            "warehouse to check"
                        ),
                    ),
                    fix=WARN_WAREHOUSE_CHECK_UNAVAILABLE.fix_template,
                    path=relpath,
                    query=query_name,
                )
            )
            continue

        try:
            check = warehouse_check(
                query,
                board=board,
                env=env,
                query_name=query_name,
                query_registry=compile_result.query_registry,
            )
        except DbtChartsError as exc:
            # An unresolvable source is a project-level fault, not a verdict on
            # this query — stop rather than repeat it once per remaining query.
            # What the earlier queries already found still stands; dropping it
            # would hide a broken query behind an unrelated config fault.
            return errors + [exc.to_diagnostic(file=relpath)], warnings

        if check.status == "invalid":
            errors.append(
                DbtChartsError.from_code(
                    ERR_WAREHOUSE_QUERY_INVALID,
                    name=query_name,
                    mechanism=check.mechanism,
                    warehouse_message=check.error,
                ).to_diagnostic(file=relpath)
            )
            continue

        if check.status == "unchecked":
            warnings.append(
                Diagnostic.from_code(
                    WARN_WAREHOUSE_CHECK_UNAVAILABLE,
                    message=WARN_WAREHOUSE_CHECK_UNAVAILABLE.message_template.format(
                        name=query_name, reason=check.reason
                    ),
                    fix=WARN_WAREHOUSE_CHECK_UNAVAILABLE.fix_template,
                    path=relpath,
                    query=query_name,
                )
            )
            continue

        if not check.columns_checked:
            warnings.append(
                Diagnostic.from_code(
                    WARN_COLUMN_CHECK_UNAVAILABLE,
                    message=WARN_COLUMN_CHECK_UNAVAILABLE.message_template.format(
                        adapter_type=check.adapter_type,
                        mechanism=check.mechanism,
                    ),
                    fix=WARN_COLUMN_CHECK_UNAVAILABLE.fix_template,
                    path=relpath,
                    query=query_name,
                )
            )
            continue

        result_columns = {c.name for c in check.columns}
        for chart in unbound_by_query.get(query_name, []):
            error = channel_error(chart, [c.name for c in check.columns])
            if error is not None:
                errors.append(error.to_diagnostic(file=relpath))
        for column_name in missing_format_columns(query.formats, result_columns):
            errors.append(
                DbtChartsError.from_code(
                    ERR_QUERY_FORMAT_COLUMN_MISSING,
                    query=query_name,
                    column=column_name,
                    columns=", ".join(sorted(result_columns)),
                ).to_diagnostic(file=relpath)
            )
        for chart_name, channel_label, column_name in refs_by_query.get(query_name, []):
            if column_name not in result_columns:
                errors.append(
                    missing_column_error(
                        chart_name,
                        (query_name, channel_label, column_name),
                        result_columns,
                    ).to_diagnostic(file=relpath)
                )

    return errors, warnings
