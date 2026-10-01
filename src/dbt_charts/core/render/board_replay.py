"""Dump a resolved-board artifact and render it back from a recording.

The two halves of the artifact round trip that make a resolved board a
first-class, replayable output: `dump_board_artifact`/`load_board_artifact`
(de)serialize the published `ResolvedBoard` contract, and
`render_board_from_artifact` renders one with a `ReplayDataProvider` instead
of a live `Executor` — no compile, no warehouse.

Both loaders stamp a registered `ERR-*` code on failure rather than letting a
bare `pydantic.ValidationError` or `ReplayDataProvider` exception reach a
user-facing CLI/MCP call — this is the first user-facing entry point for
loading an artifact, so an unmigrated raise here would surface as
`ERR-INTERNAL` and signal a bug instead of a real, actionable failure.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from dbt_charts.core.compile.board_artifact import (
    DanglingStyleRefError,
    dump_resolved_board_artifact,
    load_resolved_board_artifact,
)
from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
from dbt_charts.core.compile.resolve.chart.label_data import (
    pie_presentation_fingerprint,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_BOARD_ARTIFACT_INVALID,
    ERR_BOARD_RECORDING_MISMATCH,
    ERR_RESOLVED_PIE_DATA_MISMATCH,
)
from dbt_charts.core.execute.replay_provider import ReplayDataProvider
from dbt_charts.core.render.boards import render_board_svg
from dbt_charts.core.render.font_selection import collect_painted_italic_families

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import VariableValues
    from dbt_charts.core.execute.recording import BoardRecording


def dump_board_artifact(resolved: ResolvedBoard) -> bytes:
    """Serialize a resolved board to its published JSON contract.

    Delegates the shape to ``compile.board_artifact``, which owns the envelope
    and the schema generated from it. Emitting a bare ``ResolvedBoard`` dump here
    instead would write documents that do not match the ``board-resolved``
    schema this repo publishes, and would carry one full copy of the ~81 KB
    style tree per nested board.
    """
    return json.dumps(dump_resolved_board_artifact(resolved), indent=2).encode()


def load_board_artifact(data: bytes) -> ResolvedBoard:
    """Parse a board artifact, raising a registered ERR-* on malformed input.

    ``DanglingStyleRefError`` is caught alongside the parse errors: a board whose
    style table is missing an entry is an incomplete artifact, which is the same
    class of failure to a caller as malformed JSON, and this loader is the
    user-facing boundary where that becomes a registered code rather than a bare
    exception.
    """
    try:
        resolved = load_resolved_board_artifact(json.loads(data))
    except (
        ValidationError,
        DanglingStyleRefError,
        json.JSONDecodeError,
        TypeError,
    ) as exc:
        raise DbtChartsError.from_code(
            ERR_BOARD_ARTIFACT_INVALID, detail=str(exc)
        ) from exc
    _require_prose_plans(resolved)
    return resolved


def _require_prose_plans(resolved: ResolvedBoard) -> None:
    """Reject an artifact whose text carries no prose plan.

    The plan is decided over the whole board before sizing, so it cannot be
    recomputed from a resolved tree, and an artifact emitted before text columns
    sat on the card grid does not hold one. Drawing it without a plan would
    either guess a grid or crash mid-render; naming the cause is the loader's
    job.
    """
    boards = [resolved]
    for board in boards:
        if board.text and board.prose_plan is None:
            raise DbtChartsError.from_code(
                ERR_BOARD_ARTIFACT_INVALID,
                detail=(
                    f"board {board.id!r} has text but no prose plan; the artifact "
                    "was emitted before board text columns sat on the card grid"
                ),
            )
        boards.extend(  # noqa: B909 — queue growth: BFS over the board tree
            item.board for item in board.layout.items if item.board is not None
        )


def render_board_from_artifact(
    resolved: ResolvedBoard,
    recording: BoardRecording,
    variables: VariableValues,
    embed_fonts: bool = False,
) -> str:
    """Render a resolved board from a recording — no compile, no warehouse.

    `variables` is the state to render with; it is checked against
    `recording.variables` (the state the rows were captured under) rather than
    assumed to match, so a caller asking for a different value gets a loud,
    named failure instead of stale-looking data. A static replay passes
    `recording.variables` itself.

    The mismatch check runs before any rendering, not inside
    `ReplayDataProvider.execute_query`: render isolates a per-chart failure
    into an inline error callout rather than raising past it (so one broken
    chart doesn't take down the whole board), which would turn a broken
    artifact into a quietly-degraded render instead of the loud failure this
    function promises.

    The canvas background is not a parameter here: it is baked into
    `resolved.style.background` at resolve time (the same value a live
    render would use), the same "transparent" string sentinel `render()`
    itself translates before calling `render_board_svg`.

    Raises:
        DbtChartsError: ERR-BOARD-RECORDING-MISMATCH when the recording lacks a
            query the artifact needs, or was captured under different
            variable values.
    """
    missing = sorted(set(resolved.queries) - set(recording.rows_by_query))
    if missing:
        raise DbtChartsError.from_code(
            ERR_BOARD_RECORDING_MISMATCH,
            detail=f"the recording has no rows for {', '.join(missing)}.",
        )
    if dict(variables) != recording.variables:
        raise DbtChartsError.from_code(
            ERR_BOARD_RECORDING_MISMATCH,
            detail=(
                f"the recording was captured under variables "
                f"{recording.variables!r}, but this render asked for "
                f"{dict(variables)!r}."
            ),
        )
    boards = [resolved]
    for board in boards:
        boards.extend(  # noqa: B909 — queue growth: BFS over the board tree
            item.board for item in board.layout.items if item.board is not None
        )
        for chart in board.charts.values():
            if not isinstance(chart, ResolvedPieChart) or chart.query_name is None:
                continue
            rows = recording.rows_by_query[chart.query_name]
            if pie_presentation_fingerprint(rows) != chart.presentation_fingerprint:
                raise DbtChartsError.from_code(
                    ERR_RESOLVED_PIE_DATA_MISMATCH,
                    chart_id=chart.id,
                )
    provider = ReplayDataProvider(
        rows_by_query=recording.rows_by_query,
        recorded_at=recording.recorded_at,
        variables=recording.variables,
    )
    resolved_background = resolved.style.background
    # `embed_fonts` follows the caller, because a replay's contract is reproducing
    # whatever it is being compared against: an artifact rendered for export must
    # match `dct render`, which carries its fonts, while a replay checked against a
    # plain `render_board_svg` must match that instead. The sink italic selection
    # writes into is opened either way — branching on it would buy nothing.
    with collect_painted_italic_families():
        return render_board_svg(
            resolved,
            provider,
            variables,
            background=(
                None if resolved_background == "transparent" else resolved_background
            ),
            render_cache={},
            embed_fonts=embed_fonts,
        )
