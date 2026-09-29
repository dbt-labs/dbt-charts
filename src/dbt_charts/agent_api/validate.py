"""Typed validate verb — fast YAML schema + cross-reference validation.

No warehouse connection, no query execution (unless --warehouse is passed).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.agent_api._paths import resolve_board_path
from dbt_charts.core.compile.normalize.charts import _FIELD_CHANNEL_KEYS
from dbt_charts.core.diagnostics import (
    ERR_CHART_COLUMN_NOT_IN_RESULT,
    ERR_FILE_NOT_FOUND,
    ERR_INTERNAL,
    ERR_WAREHOUSE_QUERY_INVALID,
    WARN_COLUMN_CHECK_UNAVAILABLE,
    WARN_WAREHOUSE_CHECK_UNAVAILABLE,
    Diagnostic,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.project import (
    CHARTS_SUBDIR,
    PROJECT_CONFIG_NAME,
    Project,
    ProjectPath,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.compiler import CompileResult
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry


class ValidateBoardArgs(BaseModel):
    """Validate a board YAML file without executing queries.

    Runs YAML parse, Pydantic schema validation, and cross-reference checks
    (chart → query resolution, variable resolution, partial-include expansion).
    Fast — no warehouse connection, no query execution. Use this as the inner
    edit-loop verb: edit, validate, fix, repeat. Prefer render_board for a
    full compile + execute validation.
    """

    path: Path | None = Field(
        None, description="Path to board YAML file (use this OR yaml_content)"
    )
    yaml_content: str | None = Field(
        None, description="YAML content to validate directly (use this OR path)"
    )


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


class ContentValidateResult(BaseModel):
    """Result of validating in-memory YAML content — no file on disk."""

    model_config = ConfigDict(frozen=True)

    success: bool
    errors: list[Diagnostic] = Field(
        default_factory=list, description="Validation errors found in the YAML."
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
    adapter_registry: AdapterRegistry | None = None,
) -> list[ValidateResult]:
    """Validate N board files / directories, or default ``charts/`` when empty.

    Each argv path expands like the single-path verb did: a file validates
    directly, a directory walks recursively (skipping ``_*.yml`` partials and
    ejected inspect-template directories). Empty / ``None`` defaults to
    ``<project_root>/charts``. Results are concatenated in argv order.

    ``adapter_registry`` opts into the warehouse tier (``--warehouse``). It is
    additive by construction: the same schema and cross-reference validation
    runs first, and warehouse findings are appended to its result.
    """
    if not paths:
        return _validate_one_path(None, project, adapter_registry)
    out: list[ValidateResult] = []
    for p in paths:
        out.extend(_validate_one_path(p, project, adapter_registry))
    return out


def _validate_one_path(
    path: Path | None,
    project: Project,
    adapter_registry: AdapterRegistry | None = None,
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
                errors=[
                    DbtChartsError.from_code(
                        ERR_INTERNAL, message=str(exc)
                    ).to_diagnostic()
                ],
            )
        ]

    if resolved.is_yaml:
        return [_validate_resolved(resolved, project, adapter_registry)]

    boards = iter_expanded_board_files(project, resolved.relpath)
    if not boards:
        return [
            ValidateResult(
                success=False,
                path=resolved.relpath,
                errors=[
                    DbtChartsError.from_code(
                        ERR_INTERNAL,
                        message=f"No board files found in {resolved.relpath}",
                    ).to_diagnostic()
                ],
            )
        ]
    return [_validate_resolved(pf, project, adapter_registry) for pf in boards]


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
            errors=[
                DbtChartsError.from_code(ERR_INTERNAL, message=str(exc)).to_diagnostic()
            ],
        )

    return _validate_resolved(resolved, project)


def _validate_resolved(
    resolved: ProjectPath,
    project: Project,
    adapter_registry: AdapterRegistry | None = None,
) -> ValidateResult:
    """Validate an already-resolved board path — no re-resolution."""
    from dbt_charts.core.compile.compiler import compile_file, validate_compiled_queries
    from dbt_charts.core.dbt_model_columns import check_model_columns
    from dbt_charts.core.dbt_ref_check import check_manifest_refs

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

    from dbt_charts.core.compile.errors import CompilationError

    try:
        result = compile_file(resolved.read_board())
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
            errors=[exc.to_diagnostic(file=resolved.relpath)],
        )

    validate_compiled_queries(result)
    check_manifest_refs(result, project)
    check_model_columns(result, project)

    errors = list(result.errors)
    warnings = list(result.warnings)

    # The warehouse tier runs last and only on a board that already compiled —
    # it appends to the stateless findings rather than standing in for them.
    if adapter_registry is not None and not errors:
        wh_errors, wh_warnings = _warehouse_findings(
            result, resolved.relpath, adapter_registry
        )
        errors += wh_errors
        warnings += wh_warnings

    return ValidateResult(
        success=len(errors) == 0,
        path=resolved.relpath,
        errors=errors,
        warnings=warnings,
    )


def validate_content(yaml_content: str, *, project: Project) -> ContentValidateResult:
    """Fast schema + cross-reference validation of in-memory YAML.

    No warehouse, no query execution, no file on disk — for surfaces (e.g. the
    Playground) that hold unsaved scratch YAML in memory. Mirrors ``validate()``
    but reuses ``compile()`` directly instead of ``compile_file()``, since there
    is no path to resolve or read. Cross-file refs resolve against the project's
    charts/ directory, matching the in-memory render path (``core.board.
    render_dashboard``'s ``yaml_content`` branch).

    Lint findings from ``validate_compiled_queries`` carry ``range=None`` by
    design: in-memory content has no file identity, so ``_stamp_compile_result``
    leaves ``source_map`` empty and stamping is a no-op.
    """
    from dbt_charts.core.compile.compiler import (
        compile as _compile,
        validate_compiled_queries,
    )
    from dbt_charts.core.dbt_model_columns import check_model_columns
    from dbt_charts.core.dbt_ref_check import check_manifest_refs

    result = _compile(
        yaml_content,
        base_dir=project.directory(CHARTS_SUBDIR),
        project_sources=project.sources,
        project_cache=project.cache,
    )
    validate_compiled_queries(result)
    check_manifest_refs(result, project)
    check_model_columns(result, project)
    return ContentValidateResult(
        success=result.success,
        errors=list(result.errors),
        warnings=list(result.warnings),
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

    source_names = frozenset(project.sources.sources.keys())

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


# Channel fields that may reference query result columns. Every one is authored
# as a bare column name (str or list[str]), so a miss is unambiguously an error
# and not a value the renderer would interpret some other way.
#
# `geo` is a named boundary source ("us-states"), not a column. `sort` is a
# sort-order field reference, not a display channel. Both are excluded so a new
# channel added to _FIELD_CHANNEL_KEYS is automatically checked here too.
_CHANNEL_FIELDS: frozenset[str] = _FIELD_CHANNEL_KEYS - {"geo", "sort"}


def _chart_column_refs(chart: Chart) -> list[tuple[str, str, str]]:
    """Return (query_name, channel_label, column_name) for every column channel.

    Walks the chart's own channels plus each cartesian ``layer``, which carries
    its own ``x``/``y``/``color`` and is where a mistyped column hides most
    easily — the chart-level channels look fine and the layer silently plots
    nothing. A layer's ``query:`` overrides the chart's, so each ref carries the
    query whose result actually has to contain it: checking a layer's columns
    against the chart-level query rejects a board that renders fine.
    """
    refs: list[tuple[str, str, str]] = []

    def collect(source: object, prefix: str, query_name: str | None) -> None:
        if query_name is None:
            return
        for field_name in _CHANNEL_FIELDS:
            val = getattr(source, field_name, None)
            if isinstance(val, str):
                refs.append((query_name, f"{prefix}{field_name}", val))
            elif isinstance(val, list):
                for i, item in enumerate(val):
                    if isinstance(item, str):
                        refs.append((query_name, f"{prefix}{field_name}[{i}]", item))
        # TableChart.columns: list[str] | None
        table_cols = getattr(source, "columns", None)
        if isinstance(table_cols, list):
            for i, col in enumerate(table_cols):
                if isinstance(col, str):
                    refs.append((query_name, f"{prefix}columns[{i}]", col))

    collect(chart, "", chart.query_name)
    layers = getattr(chart, "layers", None)
    if isinstance(layers, list):
        for i, layer in enumerate(layers):
            collect(layer, f"layers[{i}].", layer.query or chart.query_name)
    return refs


def _warehouse_findings(
    compile_result: CompileResult,
    relpath: str,
    adapter_registry: AdapterRegistry,
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
    for chart_name, chart in board.charts.items():
        for query_name, label, column in _chart_column_refs(chart):
            refs_by_query.setdefault(query_name, []).append((chart_name, label, column))

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
                adapter_registry=adapter_registry,
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
        for chart_name, channel_label, column_name in refs_by_query.get(query_name, []):
            if column_name not in result_columns:
                errors.append(
                    DbtChartsError.from_code(
                        ERR_CHART_COLUMN_NOT_IN_RESULT,
                        chart=chart_name,
                        channel=channel_label,
                        column=column_name,
                        name=query_name,
                        columns=sorted(result_columns),
                    ).to_diagnostic(file=relpath)
                )

    return errors, warnings


def validate_board(
    *,
    path: Path | None,
    yaml_content: str | None,
    project: Project,
) -> ValidateResult | ContentValidateResult:
    """Unified validate_board dispatch for the AI tool layer.

    Routes to ``validate()`` (path) or ``validate_content()`` (yaml_content).
    Raises ``ValueError`` when neither or both are provided — the caller
    (``_handle_validate`` shim) lets this propagate to ``dispatch_tool_call``'s
    central exception handler. Returns a typed model; the shim serializes.
    """
    if path is None and yaml_content is None:
        raise ValueError("Provide one of 'path' or 'yaml_content'")
    if path is not None and yaml_content is not None:
        raise ValueError("Provide only one of 'path' or 'yaml_content', not both")
    if yaml_content is not None:
        return validate_content(yaml_content, project=project)
    assert path is not None  # guaranteed by the checks above
    annotated = annotate_with_data_lint(
        [validate(path=path, project=project)], project=project
    )
    return annotated[0]
