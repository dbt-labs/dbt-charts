"""Typed describe verb — board structure inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.board.normalized import Layout
from dbt_charts.core.compile.models.chart.normalized import (
    Chart,
)
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    HttpQuery,
    SqlQuery,
    ValuesQuery,
)
from dbt_charts.core.diagnostics import (
    ERR_FILE_NOT_FOUND,
    ERR_INTERNAL,
    Diagnostic,
)
from dbt_charts.core.project import Project, ProjectPath

# Chart-encoding fields surfaced to agents. Covers the stable channel fields
# across chart families. `label` is excluded — it's a KPI-specific render hint,
# not an encoding channel that binds a column.
_ENCODING_FIELDS: tuple[str, ...] = (
    "x",
    "y",
    "color",
    "size",
    "shape",
    "theta",
    "value",
)


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class QueryDescription(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    summary: str
    source: str | None = None
    sql: str | None = None


class ChartDescription(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    query: str
    title: str | None = None
    encoding: dict[str, Any] = Field(
        default_factory=dict, description="Vega-Lite encoding channels keyed by role."
    )


class VariableDescription(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    default: Any | None = None
    options: list[str] | None = None


class LayoutDescription(BaseModel):
    model_config = ConfigDict(frozen=True)

    primitive: str
    items: list[str] = Field(
        default_factory=list,
        description="Layout item identifiers (chart IDs, text keys).",
    )


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class DescribeBoardArgs(BaseModel):
    """Describe the structure of a board file: queries (with type and source), charts (with query bindings and encoding), variables, and layout. Use this for orientation when picking up an unfamiliar dashboard — it reads the YAML and returns a curated narrative summary without executing any queries."""

    path: Path = Field(description="Path to the dashboard YAML file to describe")


class DescribeBoardResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    path: str = Field(description="Project-relative path to the described board.")
    error: str | None = Field(
        default=None,
        description=(
            "Host refusal (e.g. access denied) reported in-band; this model "
            "carries no compile-diagnostic channel."
        ),
    )
    title: str | None = None
    notes: str | None = None
    queries: list[QueryDescription] = Field(
        default_factory=list, description="Queries defined in the board."
    )
    charts: list[ChartDescription] = Field(
        default_factory=list, description="Charts defined in the board."
    )
    variables: list[VariableDescription] = Field(
        default_factory=list, description="Variables defined in the board."
    )
    layout: LayoutDescription | None = None
    errors: list[Diagnostic] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_query_description(name: str, query: AnyQuery) -> QueryDescription:
    """Map an AnyQuery union variant onto a typed description."""
    match query:
        case SqlQuery():
            sql = query.sql or None
            summary = f"SQL query against `{query.source}`"
            return QueryDescription(
                name=name, type="sql", summary=summary, source=query.source, sql=sql
            )
        case HttpQuery():
            return QueryDescription(
                name=name, type="http", summary=f"HTTP request: {query.url}"
            )
        case ValuesQuery():
            return QueryDescription(name=name, type="values", summary="Inline values")
        case _:
            return QueryDescription(
                name=name, type=query.query_type, summary=f"{query.query_type} query"
            )


def _collect_layout_items(layout: Layout, prefix: str = "") -> list[str]:
    """Walk a Layout, returning chart names in display order.

    Nested boards recurse with a prefix so the caller can tell layers apart.
    """
    out: list[str] = []
    for item in layout.items:
        if item.type == "chart" and item.chart is not None:
            out.append(f"{prefix}{item.chart.id}" if prefix else item.chart.id)
        elif item.type == "board" and item.board is not None:
            sub_prefix = f"{item.board.id or '<nested>'}."
            out.extend(_collect_layout_items(item.board.layout, sub_prefix))
    return out


def _encoding_for_chart(chart: Chart) -> dict[str, Any]:
    """Surface chart encoding bindings (column names) as JSON-friendly values.

    `y` is `str | list[str]` (multi-series), and `color` accepts
    dicts. Pass scalars and lists through unchanged so multi-series boards
    don't get their `y: [revenue, profit]` flattened into Python repr. The
    `value` is always a column reference string; pass through unchanged.
    """
    enc: dict[str, Any] = {}
    for field in _ENCODING_FIELDS:
        # Chart families only expose the fields relevant to their type.
        v = getattr(chart, field, None)
        if v is None or v == "":
            continue
        enc[field] = v
    return enc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def describe_board(path: Path, *, project: Project) -> DescribeBoardResult:
    """Describe the structure of a board: queries, charts, variables, layout."""
    from dbt_charts.agent_api._paths import resolve_board_path
    from dbt_charts.core.diagnostics.base import DbtChartsError

    try:
        resolved = resolve_board_path(path, project)
    except ValueError as exc:
        # resolve_board_path failed — path never became a ProjectPath, so there
        # is no root to relativize against via posix_relpath. Echo the raw
        # input POSIX-normalized (not a project identity, just consistent
        # display for an input that never resolved).
        return DescribeBoardResult(
            success=False,
            path=path.as_posix(),
            errors=[
                DbtChartsError.from_code(ERR_INTERNAL, message=str(exc)).to_diagnostic()
            ],
        )

    return _describe_resolved(resolved)


def _describe_resolved(resolved: ProjectPath) -> DescribeBoardResult:
    """Describe an already-resolved board path — no re-resolution."""
    from dbt_charts.core.compile.compiler import compile_file
    from dbt_charts.core.diagnostics.base import DbtChartsError

    if not resolved.exists():
        return DescribeBoardResult(
            success=False,
            path=resolved.relpath,
            errors=[
                DbtChartsError.from_code(
                    ERR_FILE_NOT_FOUND,
                    path=resolved.relpath,
                ).to_diagnostic()
            ],
        )

    try:
        result = compile_file(resolved.read_board())
    except OSError as exc:
        return DescribeBoardResult(
            success=False,
            path=resolved.relpath,
            errors=[
                DbtChartsError.from_code(ERR_INTERNAL, message=str(exc)).to_diagnostic(
                    file=resolved.relpath
                )
            ],
        )

    if result.errors:
        return DescribeBoardResult(
            success=False,
            path=resolved.relpath,
            errors=list(result.errors),
        )

    board = result.board
    if board is None:
        return DescribeBoardResult(
            success=False,
            path=resolved.relpath,
            errors=[
                DbtChartsError.from_code(
                    ERR_INTERNAL, message="Compilation produced no board"
                ).to_diagnostic(file=resolved.relpath)
            ],
        )

    query_descs = [
        _build_query_description(name, query) for name, query in board.queries.items()
    ]

    chart_descs = [
        ChartDescription(
            name=name,
            type=chart.type,
            query=chart.query_name or name,
            title=getattr(chart, "title", None)
            or getattr(chart, "label", None)
            or None,
            encoding=_encoding_for_chart(chart),
        )
        for name, chart in board.charts.items()
    ]

    var_descs: list[VariableDescription] = []
    for name, var in board.variables.items():
        static = var.options.static if var.options is not None else None
        var_descs.append(
            VariableDescription(
                name=name,
                type=str(var.input),
                default=var.default,
                options=[str(o) for o in static] if static else None,
            )
        )

    layout_type = board.layout.type
    layout_primitive = (
        layout_type.value if hasattr(layout_type, "value") else str(layout_type)
    )
    layout_desc = LayoutDescription(
        primitive=layout_primitive,
        items=_collect_layout_items(board.layout),
    )

    return DescribeBoardResult(
        success=True,
        path=resolved.relpath,
        title=board.title or None,
        notes=board.notes or None,
        queries=query_descs,
        charts=chart_descs,
        variables=var_descs,
        layout=layout_desc,
    )


def describe_paths(
    paths: list[Path],
    *,
    project: Project,
) -> list[DescribeBoardResult]:
    """Describe N board files / directories.

    Each argv path expands like the single-path verb: a file describes directly,
    a directory walks recursively (skipping ``_*.yml`` partials and ejected
    inspect-template directories). Results are concatenated in argv order.
    """
    out: list[DescribeBoardResult] = []
    for p in paths:
        out.extend(_describe_one_path(p, project))
    return out


def _describe_one_path(
    path: Path,
    project: Project,
) -> list[DescribeBoardResult]:
    """Per-argv expansion: file → [one], dir → walk."""
    from dbt_charts.agent_api._paths import (
        iter_expanded_board_files,
        resolve_board_path,
    )
    from dbt_charts.core.diagnostics.base import DbtChartsError

    try:
        resolved = resolve_board_path(path, project)
    except ValueError as exc:
        # resolve_board_path failed — path never became a ProjectPath, so there
        # is no root to relativize against via posix_relpath. Echo the raw
        # input POSIX-normalized (not a project identity, just consistent
        # display for an input that never resolved).
        return [
            DescribeBoardResult(
                success=False,
                path=path.as_posix(),
                errors=[
                    DbtChartsError.from_code(
                        ERR_INTERNAL, message=str(exc)
                    ).to_diagnostic()
                ],
            )
        ]

    if resolved.is_yaml:
        return [_describe_resolved(resolved)]

    boards = iter_expanded_board_files(project, resolved.relpath)
    if not boards:
        return [
            DescribeBoardResult(
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
    return [_describe_resolved(pf) for pf in boards]
