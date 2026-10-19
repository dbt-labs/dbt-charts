"""Emit a resolved-board artifact + recording, and render one back.

`emit_board_artifact` compiles, resolves, and executes a board, then writes the
board's published `ResolvedBoard` contract and a data sidecar (the rows it was
resolved against). `render_board_from_files` reads both back and renders
without a compile step or a warehouse connection — the "skip the
compilation" win the resolved-board artifact exists for. `board_artifact_schema`
returns the JSON Schema an artifact conforms to.

Business logic (the resolve/record/dump/load primitives) lives in
`dbt_charts.core.execute.recording` and `dbt_charts.core.render.board_replay`;
this module wires them to a `Project` + `AdapterRegistry` and to plain
filesystem paths for the artifact/recording files, which are generated
build outputs rather than project (board YAML) content.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict
from pydantic.json_schema import JsonSchemaValue

from dbt_charts.agent_api._paths import resolve_board_or_error
from dbt_charts.core.compile import compile_file
from dbt_charts.core.compile.models.board.resolved import ChartResolveFailure
from dbt_charts.core.compile.schema.renderers.board_resolved import (
    render_board_resolved_schema,
)
from dbt_charts.core.compile.template.variables import normalize_multiselect_values
from dbt_charts.core.diagnostics import ERR_FILE_NOT_FOUND, Diagnostic
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.file_source_materializer import (
    default_local_materializer_factory,
)
from dbt_charts.core.execute.recording import load_board_recording, record_board
from dbt_charts.core.render.board_replay import (
    dump_board_artifact,
    load_board_artifact,
    render_board_from_artifact,
)
from dbt_charts.core.render.board_resolve import build_resolved_board
from dbt_charts.core.render.chart.automatic_link import (
    set_filter_variables_context,
)

if TYPE_CHECKING:
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.execute.cache_backend import QueryResultCache
    from dbt_charts.core.project import Project


class EmitBoardArtifactResult(BaseModel):
    """Result of writing a board artifact + recording."""

    model_config = ConfigDict(frozen=True)

    success: bool
    artifact_path: Path | None = None
    recording_path: Path | None = None
    query_count: int = 0
    errors: list[Diagnostic] = []


class RenderBoardArtifactResult(BaseModel):
    """Result of rendering a board from an artifact + recording."""

    model_config = ConfigDict(frozen=True)

    success: bool
    svg: str | None = None
    errors: list[Diagnostic] = []


def _read_or_error(path: Path) -> bytes:
    if not path.exists():
        raise DbtChartsError.from_code(ERR_FILE_NOT_FOUND, path=str(path))
    return path.read_bytes()


def emit_board_artifact(
    path: Path,
    artifact_path: Path,
    recording_path: Path,
    *,
    project: Project,
    adapter_registry: AdapterRegistry,
    variables: dict[str, Any] | None = None,
    use_cache: bool = True,
    result_cache: QueryResultCache | None = None,
) -> EmitBoardArtifactResult:
    """Compile, resolve, and execute a board, then write its artifact + recording.

    Writes `artifact_path` (the published, data-free `ResolvedBoard` contract)
    and `recording_path` (the rows it was resolved against, the variable
    values, and a `recorded_at` stamp) — kept separate so the board contract
    carries no data payload.
    """
    located = resolve_board_or_error(path, project)
    if isinstance(located, Diagnostic):
        return EmitBoardArtifactResult(success=False, errors=[located])

    compile_result = compile_file(located)
    if not compile_result.success:
        return EmitBoardArtifactResult(
            success=False, errors=list(compile_result.errors)
        )

    board = compile_result.board
    assert board is not None  # compile_result.success guarantees this

    # `emit-board` merges its own variables — it never goes through `render()`,
    # whose prologue does this — and this dict is both rendered from and stored
    # verbatim in the recording that `render_board_from_files` replays. `--var`
    # values are always strings, so a scalar is the only shape a caller can
    # supply for a multiselect here; unnarrowed, a bare `| join` would iterate
    # it character by character.
    merged_variables: dict[str, Any] = normalize_multiselect_values(
        {**board.variable_defaults, **(variables or {})},
        board.variable_registry or {},
    )

    executor = Executor(
        board,
        adapter_registry=adapter_registry,
        query_registry=compile_result.query_registry,
        use_cache=use_cache,
        result_cache=result_cache,
        # emit_board_artifact takes no file_materializer of its own — no host
        # calls it with an injected one — so this is always the local
        # DuckDB-backed factory, not a routing decision.
        file_materializer_factory=default_local_materializer_factory(project),
    )

    # Matches render()'s prologue: the declared variable names are set for the
    # sizing pass, as a live render sets them.
    set_filter_variables_context(
        frozenset(board.variable_registry) if board.variable_registry else frozenset()
    )
    resolve_errors: dict[str, ChartResolveFailure] = {}
    try:
        resolved, _render_cache = build_resolved_board(
            board, executor, merged_variables, resolve_errors=resolve_errors
        )
    except DbtChartsError as exc:
        return EmitBoardArtifactResult(success=False, errors=[exc.to_diagnostic()])
    finally:
        set_filter_variables_context(frozenset())

    # A chart-level resolve failure no longer raises — it leaves a hole in the
    # board instead. Publishing that hole silently would be worse than the fatal
    # it replaced, since a live render at least draws an error tile where an
    # artifact just has nothing. Artifacts stay all-or-nothing.
    if resolve_errors:
        return EmitBoardArtifactResult(
            success=False,
            errors=[failure.diagnostic for failure in resolve_errors.values()],
        )

    recording = record_board(resolved, executor, merged_variables)

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(dump_board_artifact(resolved))
    recording_path.parent.mkdir(parents=True, exist_ok=True)
    recording_path.write_bytes(recording.model_dump_json(indent=2).encode())

    return EmitBoardArtifactResult(
        success=True,
        artifact_path=artifact_path,
        recording_path=recording_path,
        query_count=len(recording.rows_by_query),
    )


def render_board_from_files(
    artifact_path: Path,
    recording_path: Path,
    standalone: bool = False,
) -> RenderBoardArtifactResult:
    """Load a board artifact + recording and render it — no compile, no warehouse.

    ``standalone`` carries the font bytes inline, for a caller writing the SVG to a
    file that nothing will serve. Left to the caller because a replay has to match
    whatever it is being compared against: `dct artifact render` reproduces
    `dct render`, which carries its fonts, while a replay checked against a plain
    `render_board_svg` must not.
    """
    try:
        resolved = load_board_artifact(_read_or_error(artifact_path))
        recording = load_board_recording(_read_or_error(recording_path))
        svg = render_board_from_artifact(
            resolved, recording, recording.variables, embed_fonts=standalone
        )
    except DbtChartsError as exc:
        return RenderBoardArtifactResult(success=False, errors=[exc.to_diagnostic()])
    return RenderBoardArtifactResult(success=True, svg=svg)


def board_artifact_schema() -> JsonSchemaValue:
    """Return the JSON Schema (draft 2020-12) every emitted board artifact conforms to.

    The artifact's ``$id`` names this schema. It is derived from the
    ``ResolvedBoard`` models on each call, so it always matches the installed
    release. The return is a JSON Schema document rather than a result model:
    its shape is the JSON Schema standard's, not one a caller has to guess.
    """
    return render_board_resolved_schema()
