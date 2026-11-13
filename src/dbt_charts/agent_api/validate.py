"""Typed validate verb — fast YAML schema + cross-reference validation.

No warehouse connection, no query execution (unless --warehouse is passed).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts import _engine
from dbt_charts.agent_api._paths import (
    resolution_error_diagnostic,
    resolve_board_path,
)
from dbt_charts.core.board import partition_compile_warnings
from dbt_charts.core.compile.column_match import match_column
from dbt_charts.core.compile.engine import (
    engine_compile,
    refusal_diagnostics,
    wire_diagnostic,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import missing_format_columns
from dbt_charts.core.compile.host_facts import UnboundChart, parse_host_facts
from dbt_charts.core.compile.resolve.chart.positional import (
    unbound_columns_error,
)
from dbt_charts.core.diagnostics import (
    ERR_FILE_NOT_FOUND,
    ERR_INTERNAL,
    ERR_LOCKED_SCHEMA_VERSION,
    ERR_NO_BOARD_FILES,
    ERR_QUERY_FORMAT_COLUMN_MISSING,
    ERR_WAREHOUSE_QUERY_INVALID,
    WARN_COLUMN_CHECK_UNAVAILABLE,
    WARN_WAREHOUSE_CHECK_UNAVAILABLE,
    Diagnostic,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.chart_columns import (
    missing_column_error,
)
from dbt_charts.core.execute.plan_reader import env_sources
from dbt_charts.core.execute.planned import plan_board
from dbt_charts.core.execute.query_plan import QueryPlan
from dbt_charts.core.project import (
    CHARTS_SUBDIR,
    PROJECT_CONFIG_NAME,
    Project,
    ProjectPath,
)

if TYPE_CHECKING:
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


def _validate_resolved(
    resolved: ProjectPath,
    project: Project,
    env: ExecEnv | None,
    named: bool,
) -> ValidateResult:
    """Validate an already-resolved board path — no re-resolution.

    *named* is true for a path the user typed, false for one a folder listed.
    """
    from dbt_charts.core.dbt_model_columns import check_model_columns
    from dbt_charts.core.dbt_ref_check import check_manifest_refs
    from dbt_charts.core.link_targets import check_link_targets

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

    try:
        compiled = engine_compile(resolved, named)
    except _engine.CompileError as refusal:
        return ValidateResult(
            success=False,
            path=resolved.relpath,
            errors=[*lock_errors, *refusal_diagnostics(refusal)],
            warnings=partition_compile_warnings(
                [wire_diagnostic(found) for found in refusal.warnings],
                {},
                None,
                warnings_ignore,
            )[0],
        )
    except CompilationError as exc:  # the project's `sources:` do not load
        return ValidateResult(
            success=False,
            path=resolved.relpath,
            errors=[*lock_errors, exc.to_diagnostic(file=resolved.relpath)],
        )

    facts = parse_host_facts(compiled.host_facts_json())
    found = [
        wire_diagnostic(wire)
        for wire in (*compiled.diagnostics(), *compiled.validate_findings())
    ]
    errors = [*lock_errors, *(d for d in found if d.level == "error")]
    warnings = [d for d in found if d.level == "warning"]
    # A board declares no sources of its own; the project's are the registry.
    for diagnostic in check_manifest_refs(facts["queries"], project):
        (errors if diagnostic.level == "error" else warnings).append(diagnostic)
    for diagnostic in check_model_columns(facts["queries"], project):
        if diagnostic.level == "warning":
            warnings.append(diagnostic)
        elif diagnostic not in errors:
            errors.append(diagnostic)
    warnings += check_link_targets(facts["links"], project, resolved.relpath)

    # The warehouse tier runs last and only on a board that already compiled —
    # it appends to the stateless findings rather than standing in for them.
    if env is not None and not errors:
        try:
            # The tier connects, so the plan carries the env's sources with their values
            # rendered, over the project's declarations; the compile above has only those. A
            # declaration the env lacks (a test registry's) stays as declared.
            compiled = engine_compile(resolved, named, sources=env_sources(env))
            plan = plan_board(
                compiled,
                None,
                builtins=None,
                now=datetime.now(UTC),
                strict=False,
                active_tab_only=False,
                only_charts=None,
                all_queries=True,
            )
        except _engine.CompileError as refusal:
            errors += refusal_diagnostics(refusal)
        except ExecutionError as refusal:
            errors.append(refusal.to_diagnostic(file=resolved.relpath))
        else:
            wh_errors, wh_warnings = _warehouse_findings(
                compiled, plan, resolved.relpath, env
            )
            errors += wh_errors
            warnings += wh_warnings

    warnings, _suppressed = partition_compile_warnings(
        warnings,
        {chart: set(codes) for chart, codes in facts["warnings_ignore"].items()},
        None,
        warnings_ignore,
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


def _warehouse_findings(
    compiled: _engine.CompiledBoard,
    plan: QueryPlan,
    relpath: str,
    env: ExecEnv,
) -> tuple[list[Diagnostic], list[Diagnostic]]:
    """Check every SQL query of a compiled board against its warehouse.

    Returns (errors, warnings) to append to the stateless findings — this tier
    only ever adds. A query the adapter cannot check without running it comes
    back as a warning saying so, never as a silent pass. It reads the engine's
    host facts and its query plan, which holds every SQL query, placed or not.
    """
    from dbt_charts.core.execute.warehouse_check import warehouse_check

    facts = parse_host_facts(compiled.host_facts_json())

    # query key -> [(chart id, channel label, column name)] it must contain.
    refs_by_query: dict[str, list[tuple[str, str, str]]] = {}
    for chart_id, refs in facts["column_refs"].items():
        for query_key, label, column in refs:
            refs_by_query.setdefault(query_key, []).append((chart_id, label, column))
    # query key -> the charts that bind its columns by position.
    unbound_by_query: dict[str, list[UnboundChart]] = {}
    for chart in facts["unbound_charts"]:
        if chart["query"] is not None:
            unbound_by_query.setdefault(chart["query"], []).append(chart)

    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []

    for query_key, query in facts["queries"].items():
        query_name = query["label"]
        if query["type"] != "sql":
            warnings.append(
                Diagnostic.from_code(
                    WARN_WAREHOUSE_CHECK_UNAVAILABLE,
                    message=WARN_WAREHOUSE_CHECK_UNAVAILABLE.message_template.format(
                        name=query_name,
                        reason=(
                            f"a {query['type']} query carries no SQL for the "
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
            check = warehouse_check(plan, query_key, env=env)
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
        for chart in unbound_by_query.get(query_key, []):
            columns = [c.name for c in check.columns]
            try:
                side = {
                    field: [
                        match_column(c, columns, f"chart {chart['chart']!r} {field}")
                        or c
                        for c in named
                    ]
                    for field, named in chart["side_columns"].items()
                }
                error = unbound_columns_error(
                    chart["chart"], chart["type"], columns, side
                )
            except CompilationError as exc:
                error = exc
            if error is not None:
                errors.append(error.to_diagnostic(file=relpath))
        try:
            missing_formats = missing_format_columns(
                query_name, plan["queries"][query_key]["formats"], result_columns
            )
        except CompilationError as exc:
            errors.append(exc.to_diagnostic(file=relpath))
            missing_formats = []
        for column_name in missing_formats:
            errors.append(
                DbtChartsError.from_code(
                    ERR_QUERY_FORMAT_COLUMN_MISSING,
                    query=query_name,
                    column=column_name,
                    columns=", ".join(sorted(result_columns)),
                ).to_diagnostic(file=relpath)
            )
        for chart_name, channel_label, column_name in refs_by_query.get(query_key, []):
            try:
                found = match_column(
                    column_name, result_columns, f"chart {chart_name!r} {channel_label}"
                )
            except CompilationError as exc:
                errors.append(exc.to_diagnostic(file=relpath))
                continue
            if found is None:
                errors.append(
                    missing_column_error(
                        chart_name,
                        (query_name, channel_label, column_name),
                        result_columns,
                    ).to_diagnostic(file=relpath)
                )

    return errors, warnings
