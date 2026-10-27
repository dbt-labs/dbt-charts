"""BoardRecording captures a resolved board's rows for later replay."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import cycle
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
from dbt_charts.core.compile.resolve.chart.label_data import (
    pie_presentation_fingerprint,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.recording import (
    BoardRecording,
    load_board_recording,
    record_board,
)
from dbt_charts.core.render.board_resolve import build_resolved_board

_YAML = """
title: Recording Board
queries:
  channel_mix:
    columns: [month, signups]
    values:
      - ["2026-01-01", 90]
      - ["2026-02-01", 105]
charts:
  mix:
    query: channel_mix
    type: bar
    x: month
    y: signups
rows:
  - mix
"""


def _build_executor(tmp_path: Path) -> tuple[object, Executor, dict[str, object]]:
    result = compile_board(_YAML)
    assert result.success, result.errors
    assert result.board is not None
    project = FilesystemProject(tmp_path)
    executor = Executor(
        result.board,
        build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    variables: dict[str, object] = {}
    resolved, _ = build_resolved_board(result.board, executor, variables)
    return resolved, executor, variables


def test_record_board_captures_rows_by_query(tmp_path: Path) -> None:
    resolved, executor, variables = _build_executor(tmp_path)

    recording = record_board(resolved, executor, variables)

    assert recording.rows_by_query["channel_mix"] == [
        {"month": "2026-01-01", "signups": 90},
        {"month": "2026-02-01", "signups": 105},
    ]


def test_record_board_captures_variables_and_timestamp(tmp_path: Path) -> None:
    resolved, executor, variables = _build_executor(tmp_path)

    recording = record_board(resolved, executor, variables)

    assert recording.variables == variables
    assert recording.recorded_at == executor.rendered_at


def test_record_board_provenance_empty_for_non_dbt_queries(tmp_path: Path) -> None:
    resolved, executor, variables = _build_executor(tmp_path)

    recording = record_board(resolved, executor, variables)

    assert recording.provenance == {}


def test_board_recording_round_trips_through_json(tmp_path: Path) -> None:
    resolved, executor, variables = _build_executor(tmp_path)
    recording = record_board(resolved, executor, variables)

    reloaded = load_board_recording(recording.model_dump_json().encode())

    assert reloaded == recording


def test_load_board_recording_raises_on_malformed_json() -> None:
    with pytest.raises(DbtChartsError) as exc_info:
        load_board_recording(b"{not json")

    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-BOARD-RECORDING-INVALID"


def test_load_board_recording_raises_on_schema_mismatch() -> None:
    with pytest.raises(DbtChartsError):
        load_board_recording(b'{"unexpected_field": true}')


_TIED_PIE_YAML = """
title: Tied Pie
queries:
  industry_counts:
    sql: SELECT industry, count(*) AS n FROM accounts GROUP BY 1 ORDER BY 2 DESC
    source: test_profile
charts:
  industry_pie:
    query: industry_counts
    type: pie
    theta: n
    color: industry
rows:
  - industry_pie
"""

# Two valid results for the same ORDER BY n DESC query: the three n=2 rows
# (and the two n=1 rows) are ties, so a warehouse is free to return either
# order on any given execution.
_TIE_ORDER_A = [
    {"industry": "Tech", "n": 2},
    {"industry": "Retail", "n": 2},
    {"industry": "Media", "n": 2},
    {"industry": "Energy", "n": 1},
    {"industry": "Bio", "n": 1},
]
_TIE_ORDER_B = [
    {"industry": "Media", "n": 2},
    {"industry": "Tech", "n": 2},
    {"industry": "Retail", "n": 2},
    {"industry": "Bio", "n": 1},
    {"industry": "Energy", "n": 1},
]


def _tie_flipping_registry() -> Mock:
    """Adapter registry returning a different tie order on every execution."""
    orders = cycle([_TIE_ORDER_A, _TIE_ORDER_B])

    def _execute(*args: object, **kwargs: object) -> Mock:
        ok = Mock()
        ok.is_success = True
        ok.data = [dict(row) for row in next(orders)]
        ok.resolved_relations = None
        ok.truncated_reason = None
        return ok

    registry = Mock()
    registry.execute.side_effect = _execute
    return registry


def test_record_board_records_the_rows_resolve_saw_even_with_cache_off() -> None:
    """Regression: resolve and record must come from one execution.

    A query with tied ORDER BY values may return a different row order on each
    execution. With the persistent cache off, ``record_board`` must still get
    the exact rows ``build_resolved_board`` baked the pie's
    ``presentation_fingerprint`` from — a second execution would flake replay
    with ERR-RESOLVED-PIE-DATA-MISMATCH whenever the tie order shifted.
    """
    result = compile_board(_TIED_PIE_YAML)
    assert result.success, result.errors
    assert result.board is not None
    registry = _tie_flipping_registry()
    executor = Executor(
        result.board,
        adapter_registry=registry,
        query_registry=result.query_registry,
        use_cache=False,
    )
    variables: dict[str, object] = {}

    resolved, _ = build_resolved_board(
        result.board, executor, variables, render_first=False
    )
    recording = record_board(resolved, executor, variables)

    pie = resolved.charts["industry_pie"]
    assert isinstance(pie, ResolvedPieChart)
    assert (
        pie_presentation_fingerprint(recording.rows_by_query["industry_counts"])
        == pie.presentation_fingerprint
    )
    assert registry.execute.call_count == 1


def test_board_recording_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        BoardRecording(
            recorded_at=datetime.now(UTC),
            variables={},
            rows_by_query={},
            provenance={},
            extra_field="nope",  # type: ignore[call-arg]
        )
