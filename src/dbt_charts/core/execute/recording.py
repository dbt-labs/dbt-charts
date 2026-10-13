"""BoardRecording: the sidecar written beside a resolved-board artifact.

A resolved board's published contract (`ResolvedBoard`) deliberately carries no
data — see `execute/replay_provider.py`. This module is the other half: the
typed shape of the sidecar file that carries the rows, the variable values
they were captured under, and when they were captured, plus the loader that
turns a registered `ERR-*` code loose on a malformed one instead of a bare
`pydantic.ValidationError` reaching a user-facing CLI/MCP call.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_render import ERR_BOARD_RECORDING_INVALID
from dbt_charts.core.execute.adapters.base import ResolvedRelation
from dbt_charts.core.execute.cache_backend import CacheRows

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
    from dbt_charts.core.execute.executor import Executor


class BoardRecording(BaseModel):
    """Rows a resolved board was rendered against, captured separately from it.

    Kept out of the board artifact by design: the artifact is a data-free
    contract and can be shipped with no rows. A resolved board may contain
    presentation facts derived from its resolution rows, so replay requires the
    recording captured by the same emission; fresh data requires a new resolve.
    `recorded_at` and `variables` are not incidental — `ReplayDataProvider`
    needs the former to stamp an honest "data as of" and the latter to refuse a
    mismatched replay.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    recorded_at: datetime = Field(
        description=(
            "When the rows were captured. Becomes the replayed board's "
            "'data as of' stamp — never the time it is replayed."
        )
    )
    variables: VariableValues = Field(
        default_factory=dict,
        description=(
            "Variable values the rows were captured under. Replay refuses to "
            "serve them under any other values."
        ),
    )
    rows_by_query: dict[str, CacheRows] = Field(
        default_factory=dict,
        description="Recorded rows, keyed by query name (no `queries.` prefix).",
    )
    provenance: dict[str, list[ResolvedRelation]] = Field(
        default_factory=dict,
        description=(
            "Recorded dbt relation provenance, keyed by query name. Empty for "
            "queries with no provenance (e.g. non-dbt sources)."
        ),
    )


def record_board(
    resolved: ResolvedBoard, executor: Executor, variables: VariableValues
) -> BoardRecording:
    """Capture the rows and provenance a resolved board was rendered against.

    Call this after `build_resolved_board` has already run every query once
    for data-aware sizing — every lookup here is a cache hit, not a new
    execution.

    ``recorded_at`` is the executor's own ``rendered_at``, so a replay stamps
    exactly what the live render it records stamped.
    """
    rows_by_query = {
        name: executor.execute_query(name, variables) for name in resolved.queries
    }
    provenance = {
        name: relations
        for name in resolved.queries
        if (relations := executor.get_query_provenance(name))
    }
    return BoardRecording(
        recorded_at=executor.rendered_at,
        variables=dict(variables),
        rows_by_query=rows_by_query,
        provenance=provenance,
    )


def load_board_recording(data: bytes) -> BoardRecording:
    """Parse a recording sidecar, raising a registered ERR-* on malformed input."""
    try:
        return BoardRecording.model_validate_json(data)
    except ValidationError as exc:
        raise DbtChartsError.from_code(
            ERR_BOARD_RECORDING_INVALID, detail=str(exc)
        ) from exc
