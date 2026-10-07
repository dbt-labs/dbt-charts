"""Tests for the board-artifact emit/load-and-render verbs.

Covers emit_board_artifact (compile + resolve + execute a board, write the
artifact + recording sidecar) and render_board_from_files (load both back and
render with no compile, no warehouse).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.agent_api.board_artifact import (
    emit_board_artifact,
    render_board_from_files,
)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.adapters import build_adapter_registry

_CLIP_ID = re.compile(r"clip\d+")
_RENDER_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_DATA_AS_OF = re.compile(r"Data as of [^<]+")

_YAML = """
title: Emitted Board
queries:
  channel_mix:
    columns: [month, channel, signups]
    values:
      - ["2026-01-01", "Organic", 90]
      - ["2026-02-01", "Paid", 48]
charts:
  mix:
    query: channel_mix
    type: bar
    x: month
    y: signups
    color: channel
rows:
  - mix
"""


def _normalize(svg: str) -> str:
    out = _CLIP_ID.sub("clipN", svg)
    out = _RENDER_TIME.sub("RENDER_TIME", out)
    return _DATA_AS_OF.sub("DATA_AS_OF", out)


def test_emit_board_artifact_writes_both_files(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "board.yml").write_text(_YAML)
    project = local_project(tmp_path)

    result = emit_board_artifact(
        tmp_path / "board.yml",
        tmp_path / "out" / "board.artifact.json",
        tmp_path / "out" / "board.recording.json",
        project=project,
        adapter_registry=build_adapter_registry(project),
    )

    assert result.success, result.errors
    assert result.artifact_path is not None
    assert result.recording_path is not None
    assert result.artifact_path.exists()
    assert result.recording_path.exists()
    assert result.query_count == 1


def test_emitted_artifact_has_no_row_data(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "board.yml").write_text(_YAML)
    project = local_project(tmp_path)
    artifact_path = tmp_path / "out" / "board.artifact.json"

    emit_board_artifact(
        tmp_path / "board.yml",
        artifact_path,
        tmp_path / "out" / "board.recording.json",
        project=project,
        adapter_registry=build_adapter_registry(project),
    )

    artifact = json.loads(artifact_path.read_text())
    # The artifact is the published envelope — the board tree lives under
    # "board", with its styles hoisted into a shared "styles" table.
    board = artifact["board"]
    assert "rows" not in board
    assert "data" not in board
    assert "channel_mix" in board["queries"]
    # Queries are described; their results are not. Rows live in the sidecar.
    assert "rows_by_query" not in artifact


def test_emit_board_artifact_fails_on_compile_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "broken.yml").write_text("charts: {c: {type: bogus_chart_type}}")
    project = local_project(tmp_path)

    result = emit_board_artifact(
        tmp_path / "broken.yml",
        tmp_path / "out" / "board.artifact.json",
        tmp_path / "out" / "board.recording.json",
        project=project,
        adapter_registry=build_adapter_registry(project),
    )

    assert not result.success
    assert result.errors
    assert not (tmp_path / "out" / "board.artifact.json").exists()


def test_emit_board_artifact_fails_on_missing_board(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = local_project(tmp_path)

    result = emit_board_artifact(
        tmp_path / "nope.yml",
        tmp_path / "out" / "board.artifact.json",
        tmp_path / "out" / "board.recording.json",
        project=project,
        adapter_registry=build_adapter_registry(project),
    )

    assert not result.success
    assert "not found" in result.errors[0].message.lower()


def test_render_board_from_files_reproduces_the_live_render(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "board.yml").write_text(_YAML)
    project = local_project(tmp_path)
    adapter_registry = build_adapter_registry(project)
    artifact_path = tmp_path / "out" / "board.artifact.json"
    recording_path = tmp_path / "out" / "board.recording.json"

    emit_board_artifact(
        tmp_path / "board.yml",
        artifact_path,
        recording_path,
        project=project,
        adapter_registry=adapter_registry,
    )

    # Live render through the normal compile+execute+render path, for comparison.
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.compile.compiler import compile_file
    from dbt_charts.core.diagnostics import Diagnostic
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.board_resolve import build_resolved_board
    from dbt_charts.core.render.boards import render_board_svg

    located = resolve_board_or_error(tmp_path / "board.yml", project)
    assert not isinstance(located, Diagnostic)
    compile_result = compile_file(located)
    assert compile_result.board is not None
    live_executor = Executor(
        compile_result.board,
        adapter_registry,
        query_registry=compile_result.query_registry,
    )
    live_resolved, live_cache = build_resolved_board(
        compile_result.board, live_executor, {}
    )
    live_background = live_resolved.style.background
    live_svg = render_board_svg(
        live_resolved,
        live_executor,
        {},
        background=None if live_background == "transparent" else live_background,
        render_cache=live_cache,
    )

    result = render_board_from_files(artifact_path, recording_path)

    assert result.success, result.errors
    assert result.svg is not None
    assert _normalize(result.svg) == _normalize(live_svg)


def test_render_board_from_files_fails_loudly_on_missing_artifact(
    tmp_path: Path,
) -> None:
    result = render_board_from_files(
        tmp_path / "nope.artifact.json", tmp_path / "nope.recording.json"
    )

    assert not result.success
    assert result.svg is None
    assert result.errors


_MULTISELECT_YAML = """
title: Emitted Board
variables:
  regions:
    input: multiselect
    options:
      static: [North, South]
queries:
  channel_mix:
    columns: [month, channel, signups]
    values:
      - ["2026-01-01", "Organic", 90]
charts:
  mix:
    query: channel_mix
    type: bar
    x: month
    y: signups
    color: channel
text: |
  ## {{ regions | join(', ') }}
rows:
  - mix
"""


def _emit_and_replay(
    tmp_path: Path,
    project: FilesystemProject,
    variables: dict[str, str] | None,
) -> str:
    """Emit a board with the given variables, then replay it from disk."""
    (tmp_path / "board.yml").write_text(_MULTISELECT_YAML)
    artifact_path = tmp_path / "out" / "board.artifact.json"
    recording_path = tmp_path / "out" / "board.recording.json"

    emitted = emit_board_artifact(
        tmp_path / "board.yml",
        artifact_path,
        recording_path,
        project=project,
        adapter_registry=build_adapter_registry(project),
        variables=variables,
    )
    assert emitted.success, emitted.errors

    replayed = render_board_from_files(artifact_path, recording_path)
    assert replayed.success, replayed.errors
    assert replayed.svg is not None
    return replayed.svg


@pytest.mark.windows
def test_replayed_board_renders_a_scalar_multiselect_whole(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """`--var` supplies strings, so a scalar is the only shape this verb can get.

    `emit_board_artifact` merges its own variables and never goes through
    `render()`, and the merged dict is stored verbatim in the recording that
    `render_board_from_files` replays. Unnarrowed, `{{ v | join(', ') }}`
    iterates the string and renders "N, o, r, t, h" — a wrong result that looks
    right, which is worse than a crash.
    """
    svg = _emit_and_replay(tmp_path, local_project(tmp_path), {"regions": "North"})

    assert "North" in svg
    assert "N, o, r, t, h" not in svg


def test_replayed_board_renders_an_unset_multiselect(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """No value supplied: the heading is blank rather than taking the board down."""
    svg = _emit_and_replay(tmp_path, local_project(tmp_path), None)

    assert "<svg" in svg


_BROKEN_PIE_YAML = """
title: Board With A Broken Pie
queries:
  q_good:
    columns: [value]
    values:
      - [42]
  q_pie:
    columns: [label, amount]
    values:
      - ["a", null]
      - ["b", 3]
charts:
  good:
    query: q_good
    type: kpi
    value: value
  broken:
    query: q_pie
    type: pie
    theta: amount
    color: label
cols:
  - good
  - broken
"""


def test_emit_board_artifact_fails_on_a_chart_that_cannot_resolve(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A resolve failure keeps artifacts all-or-nothing.

    Once ``build_resolved_board`` stopped raising for a chart-level failure, the
    handler that used to report it here stopped firing — leaving this verb one
    line away from publishing a board with a silent hole in it and telling the
    caller everything was fine.
    """
    (tmp_path / "board.yml").write_text(_BROKEN_PIE_YAML)
    project = local_project(tmp_path)

    result = emit_board_artifact(
        tmp_path / "board.yml",
        tmp_path / "out" / "board.json",
        tmp_path / "out" / "board.rows.json",
        project=project,
        adapter_registry=build_adapter_registry(project),
    )

    assert result.success is False
    assert [d.code for d in result.errors] == ["ERR-PIE-NULL-THETA"]
    assert result.errors[0].fields["chart_id"] == "broken"
    # Nothing published: record_board and the replay path are unreachable for
    # this board, so there is no artifact for them to consume.
    assert not (tmp_path / "out" / "board.json").exists()
    assert not (tmp_path / "out" / "board.rows.json").exists()


def test_a_board_whose_text_reads_a_query_emits_and_replays(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    yaml = (
        "queries:\n  summary:\n    columns: [x]\n    values:\n      - [4242]\n"
        "text: 'Answer {{ queries.summary.one.x }}'\n"
    )
    (tmp_path / "board.yml").write_text(yaml)
    project = local_project(tmp_path)
    artifact_path = tmp_path / "out" / "board.artifact.json"
    recording_path = tmp_path / "out" / "board.recording.json"

    emitted = emit_board_artifact(
        tmp_path / "board.yml",
        artifact_path,
        recording_path,
        project=project,
        adapter_registry=build_adapter_registry(project),
    )
    assert emitted.success, emitted.errors

    replayed = render_board_from_files(artifact_path, recording_path)
    assert replayed.success, replayed.errors
    assert replayed.svg is not None
    assert "4242" in re.sub(r"<[^>]+>", "", replayed.svg)
