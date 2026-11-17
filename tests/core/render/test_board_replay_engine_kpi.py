"""A replayed board draws its KPI through the engine's cell, as a live render does."""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.agent_api.board_artifact import (
    emit_board_artifact,
    render_board_from_files,
)
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.engine import engine_compile_recorded
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_render import ERR_BOARD_ARTIFACT_INVALID
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.execute.recording import record_board
from dbt_charts.core.render import render
from dbt_charts.core.render.board_replay import (
    dump_board_artifact,
    load_board_artifact,
    render_board_from_artifact,
)
from dbt_charts.core.render.board_resolve import build_resolved_board

from ..._run import Run
from ..._svg_normalize import normalize_same_run_svg

_YAML = """
title: KPI Replay
queries:
  totals:
    columns: [revenue]
    values:
      - [4820]
charts:
  revenue:
    query: totals
    type: kpi
    value: revenue
rows:
  - revenue
"""


def _kpi(svg: str) -> str:
    return normalize_same_run_svg(svg[svg.index('data-chart-id="revenue"') :])


def test_a_replayed_kpi_is_the_one_a_live_render_draws(tmp_path: Path) -> None:
    project = FilesystemProject(tmp_path)
    result = compile_board(_YAML)
    assert result.success and result.board is not None, result.errors
    live = Run(result.board, build_exec_env(project))
    rendered = render(result.board, live.ctx, live.ledger, format="svg")
    assert isinstance(rendered.output, str)

    replay_run = Run(result.board, build_exec_env(project))
    resolved, _ = build_resolved_board(result.board, replay_run, {})
    recording = record_board(
        resolved, replay_run.results(list(resolved.queries), {}), {}
    )
    record = engine_compile_recorded(None, content=_YAML, project=project)[1]
    replayed = render_board_from_artifact(
        load_board_artifact(dump_board_artifact(resolved, record)),
        recording,
        recording.variables,
    )

    assert _kpi(replayed) == _kpi(rendered.output)


def test_an_artifact_without_the_engine_compile_names_the_fix(
    tmp_path: Path,
) -> None:
    project = FilesystemProject(tmp_path)
    result = compile_board(_YAML)
    assert result.success and result.board is not None, result.errors
    resolved, _ = build_resolved_board(
        result.board, Run(result.board, build_exec_env(project)), {}
    )
    record = engine_compile_recorded(None, content=_YAML, project=project)[1]
    old = dump_board_artifact(resolved, record).replace(
        b'"engine_compile"', b'"unrelated"'
    )

    with pytest.raises(DbtChartsError) as excinfo:
        load_board_artifact(old)

    assert excinfo.value.code is ERR_BOARD_ARTIFACT_INVALID
    assert "dct artifact emit" in str(excinfo.value)


def test_a_stored_source_keeps_its_env_var_template_and_never_the_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DCT_REPLAY_TEST_VALUE", "resolved-sentinel-value")
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n"
        "  wh:\n"
        "    type: snowflake\n"
        "    user: user\n"
        "    database: DB\n"
        "    warehouse: WH\n"
        "    account: acct\n"
        "    password: \"{{ env_var('DCT_REPLAY_TEST_VALUE') }}\"\n"
    )
    record = engine_compile_recorded(
        None, content=_YAML, project=FilesystemProject(tmp_path)
    )[1]

    stored = record.model_dump_json()

    assert "DCT_REPLAY_TEST_VALUE" in stored
    assert "resolved-sentinel-value" not in stored


_CALLOUT_YAML = """
title: Callout Replay
charts:
  note:
    type: callout
    message: A replayed callout
rows:
  - note
"""


def test_a_replayed_callout_is_the_one_a_live_render_draws(tmp_path: Path) -> None:
    (tmp_path / "board.yml").write_text(_CALLOUT_YAML)
    project = FilesystemProject(tmp_path)
    emitted = emit_board_artifact(
        tmp_path / "board.yml",
        tmp_path / "board.artifact.json",
        tmp_path / "board.recording.json",
        project=project,
        env=build_exec_env(project),
    )
    assert emitted.success, emitted.errors
    replayed = render_board_from_files(
        tmp_path / "board.artifact.json", tmp_path / "board.recording.json"
    )
    assert replayed.success and replayed.svg is not None, replayed.errors

    result = compile_board(_CALLOUT_YAML)
    assert result.success and result.board is not None, result.errors
    live = Run(result.board, build_exec_env(project))
    rendered = render(result.board, live.ctx, live.ledger, format="svg")
    assert isinstance(rendered.output, str)

    def callout(svg: str) -> str:
        return normalize_same_run_svg(svg[svg.index('data-chart-id="note"') :])

    assert callout(replayed.svg) == callout(rendered.output)


def test_a_replay_with_an_imported_chart_keeps_its_callout_and_kpi(
    tmp_path: Path,
) -> None:
    """The recording names an imported chart's query by its own name, and the engine
    takes its datasets by plan key."""
    (tmp_path / "src.yml").write_text(_YAML)
    (tmp_path / "board.yml").write_text(
        "title: Main\n"
        "charts:\n"
        "  revenue: src.yml.charts.revenue\n"
        "  note:\n"
        "    type: callout\n"
        "    message: Hello\n"
        "rows:\n"
        "  - revenue\n"
        "  - note\n"
    )
    project = FilesystemProject(tmp_path)
    emitted = emit_board_artifact(
        tmp_path / "board.yml",
        tmp_path / "board.artifact.json",
        tmp_path / "board.recording.json",
        project=project,
        env=build_exec_env(project),
    )
    assert emitted.success, emitted.errors

    replayed = render_board_from_files(
        tmp_path / "board.artifact.json", tmp_path / "board.recording.json"
    )

    assert replayed.success and replayed.svg is not None, replayed.errors
    assert "ERR-INTERNAL" not in replayed.svg
    note = replayed.svg[replayed.svg.index('data-chart-id="note"') :]
    assert "Hello" in note


def test_a_replayed_imported_callout_reads_a_source_query_no_chart_uses(
    tmp_path: Path,
) -> None:
    (tmp_path / "src.yml").write_text(
        "title: Src\n"
        "queries:\n"
        "  totals:\n"
        "    columns: [revenue]\n"
        "    values:\n"
        "      - [4820]\n"
        "  months:\n"
        "    columns: [month]\n"
        "    values:\n"
        "      - [jan]\n"
        "      - [feb]\n"
        "charts:\n"
        "  revenue:\n"
        "    query: totals\n"
        "    type: kpi\n"
        "    value: revenue\n"
        "  note:\n"
        "    type: callout\n"
        '    message: "Total {{ queries.months.rows | length }} months"\n'
        "rows:\n"
        "  - revenue\n"
        "  - note\n"
    )
    (tmp_path / "board.yml").write_text(
        "title: Main\n"
        "charts:\n"
        "  revenue: src.yml.charts.revenue\n"
        "  note: src.yml.charts.note\n"
        "rows:\n"
        "  - revenue\n"
        "  - note\n"
    )
    project = FilesystemProject(tmp_path)
    emitted = emit_board_artifact(
        tmp_path / "board.yml",
        tmp_path / "board.artifact.json",
        tmp_path / "board.recording.json",
        project=project,
        env=build_exec_env(project),
    )
    assert emitted.success, emitted.errors

    replayed = render_board_from_files(
        tmp_path / "board.artifact.json", tmp_path / "board.recording.json"
    )

    assert replayed.success and replayed.svg is not None, replayed.errors
    assert "ERR-" not in replayed.svg
    assert "Total 2 months" in replayed.svg
