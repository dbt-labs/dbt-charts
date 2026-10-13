"""Re-render a board from an artifact plus a recording, with no warehouse.

This is the end-to-end claim of the resolved-board artifact: a serialized
``ResolvedBoard`` and the rows it was rendered against are together sufficient to
reproduce the board, with no YAML compile and no database connection.

The comparison normalizes Vega's clip-path ids. Those come from a
process-global counter, so the same chart rendered twice in one process gets
``clip1`` the first time and ``clip7`` the second — a pre-existing render
nondeterminism unrelated to artifact fidelity. Everything else must match
exactly; the normalization is deliberately narrow so a real difference cannot
hide behind it.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.replay_provider import ReplayDataProvider
from dbt_charts.core.render.board_resolve import build_resolved_board
from dbt_charts.core.render.boards import render_board_svg

_ADAPTER: TypeAdapter[ResolvedBoard] = TypeAdapter(ResolvedBoard)
_CLIP_ID = re.compile(r"clip\d+")
# Wall-clock render time, e.g. `2026-08-02T01:11:13Z`. Two renders a second apart
# differ here for reasons that have nothing to do with the artifact.
_RENDER_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
# The "data as of" stamp. Replay deliberately reports the *recording* time rather
# than render time, so it legitimately differs — that behavior is pinned by
# test_replayed_board_stamps_the_recording_time rather than normalized blindly.
_DATA_AS_OF = re.compile(r"Data as of [^<]+")

# `values:` queries execute with no database, so the "live" side of this test is
# a real Executor and a real render, not a stand-in.
_YAML = """
title: Replay Board
queries:
  channel_mix:
    columns: [month, channel, signups]
    values:
      - ["2026-01-01", "Organic", 90]
      - ["2026-02-01", "Organic", 105]
      - ["2026-01-01", "Paid", 40]
      - ["2026-02-01", "Paid", 48]
charts:
  mix:
    query: channel_mix
    type: bar
    x: month
    y: signups
    color: channel
    title: Signups by channel
  headline:
    query: channel_mix
    type: kpi
    value: signups
    label: Total signups
rows:
  - headline
  - mix
"""


def _normalize_time_and_ids(svg: str) -> str:
    """Blank the three things that cannot match across two separate renders.

    Deliberately narrow: clip ids, the wall-clock render stamp, and the
    "data as of" stamp. Everything else — geometry, marks, text, styling — must
    match exactly, so a real regression cannot hide behind this.
    """
    out = _CLIP_ID.sub("clipN", svg)
    out = _RENDER_TIME.sub("RENDER_TIME", out)
    return _DATA_AS_OF.sub("DATA_AS_OF", out)


def test_board_renders_from_an_artifact_with_no_warehouse(tmp_path: Path) -> None:
    result = compile_board(_YAML)
    assert result.success, result.errors
    assert result.board is not None

    # ── Live render, through a real Executor ────────────────────────────────
    project = FilesystemProject(tmp_path)
    executor = Executor(
        result.board,
        build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    variables: dict[str, Any] = {}
    resolved, render_cache = build_resolved_board(result.board, executor, variables)
    live_svg = render_board_svg(
        resolved,
        executor,
        variables,
        background="#ffffff",
        render_cache=render_cache,
    )

    # ── Record: the artifact, plus the rows it was rendered against ─────────
    artifact = _ADAPTER.dump_json(resolved, warnings="error")
    recording = {
        name: executor.execute_query(name, variables) for name in resolved.queries
    }

    # ── Replay: nothing but the artifact and the recording ─────────────────
    replayed_board = _ADAPTER.validate_json(artifact)
    replay = ReplayDataProvider(
        rows_by_query=recording,
        recorded_at=datetime(2026, 8, 1, tzinfo=UTC),
        variables=variables,
    )
    replay_svg = render_board_svg(
        replayed_board,
        replay,
        variables,
        background="#ffffff",
        # A fresh cache: the replay must stand on its own, not inherit
        # pre-rendered chart SVGs from the live render.
        render_cache={},
    )

    assert _normalize_time_and_ids(replay_svg) == _normalize_time_and_ids(live_svg), (
        "A board replayed from its artifact did not reproduce the live render. "
        "The artifact is missing something the renderer depends on."
    )


def test_replayed_board_stamps_the_recording_time(tmp_path: Path) -> None:
    """A replayed board says when its data was captured, not when it rendered.

    This is why ``recorded_at`` is required rather than optional. Without it the
    provider would report no cache hits, render would fall back to render time,
    and a board built from a week-old recording would claim to be current — a
    plausible-looking lie, which is the failure mode this whole artifact exists
    to avoid.
    """
    result = compile_board(_YAML)
    assert result.success, result.errors
    assert result.board is not None

    project = FilesystemProject(tmp_path)
    executor = Executor(
        result.board,
        build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    variables: dict[str, Any] = {}
    resolved, _ = build_resolved_board(result.board, executor, variables)
    recording = {
        name: executor.execute_query(name, variables) for name in resolved.queries
    }

    replay_svg = render_board_svg(
        _ADAPTER.validate_json(_ADAPTER.dump_json(resolved, warnings="error")),
        ReplayDataProvider(
            rows_by_query=recording,
            recorded_at=datetime(2019, 3, 4, 5, 6, tzinfo=UTC),
            variables=variables,
        ),
        variables,
        background="#ffffff",
        render_cache={},
    )

    assert "Data as of 05:06 UTC on 4 Mar 2019" in replay_svg


def test_replay_needs_no_adapter_registry(tmp_path: Path) -> None:
    """Rendering this board reaches for no warehouse connection.

    ``ReplayDataProvider`` has no ``adapter_registry`` attribute, so any reach
    for one while rendering raises AttributeError instead of quietly opening a
    connection. Scope honestly: this covers only the paths *this board*
    exercises, so it is a backstop, not a proof. The comprehensive guard is
    static — ``ChartDataProvider`` does not declare the member, so the type
    checkers reject any access to it on a retyped signature, across the whole
    post-resolve path rather than just the paths one fixture happens to hit.
    """
    result = compile_board(_YAML)
    assert result.success, result.errors
    assert result.board is not None

    project = FilesystemProject(tmp_path)
    executor = Executor(
        result.board,
        build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    variables: dict[str, Any] = {}
    resolved, _ = build_resolved_board(result.board, executor, variables)
    recording = {
        name: executor.execute_query(name, variables) for name in resolved.queries
    }

    replay = ReplayDataProvider(
        rows_by_query=recording,
        recorded_at=datetime(2026, 8, 1, tzinfo=UTC),
        variables=variables,
    )
    svg = render_board_svg(
        _ADAPTER.validate_json(_ADAPTER.dump_json(resolved, warnings="error")),
        replay,
        variables,
        background="#ffffff",
        render_cache={},
    )
    assert svg.startswith("<svg") or "<svg" in svg[:200]


def test_the_recording_is_a_sidecar_not_part_of_the_artifact() -> None:
    """Rows live beside the artifact, not inside it.

    Keeps the board contract free of a data payload, so the same resolved board
    can be re-rendered against fresh data or shipped with none.
    """
    result = compile_board(_YAML)
    assert result.success, result.errors
    assert result.board is not None

    from dbt_charts.core.render.board_resolve import build_resolved_board_static

    artifact = json.loads(
        _ADAPTER.dump_json(build_resolved_board_static(result.board), warnings="error")
    )
    assert "rows" not in artifact
    assert "data" not in artifact
    # The queries are described in the artifact; their results are not.
    assert "channel_mix" in artifact["queries"]
