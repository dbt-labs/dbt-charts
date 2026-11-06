# dbt-charts/tests/agent_api/test_required_project_dir_contract.py
"""FR-001 contract: every agent_api verb with project-scoped inputs requires
project_dir at call time — invoking without it raises TypeError. The CLI
shim keeps `dct validate <path>` working without --project-dir by resolving
cwd at the Typer boundary.

Deletion of setup_render_for_board / setup_render_for_yaml is enforced via
`grep` in the Verify block (per dbt-charts/AGENTS.md: don't assert absence of
Python symbols in tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.agent_api import (
    boards as _boards,
    describe as _describe,
    inspect as _inspect_api,  # noqa: F401 (alias used below)
    query as _query,
    validate as _validate,
)
from dbt_charts.agent_api.pack import apply_proposal
from dbt_charts.cli.main import app
from dbt_charts.core.pack.models import PackProposal

_VALID_BOARD = """
queries:
  revenue:
    sql: SELECT month, SUM(revenue) AS revenue FROM orders GROUP BY 1
    source: analytics
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: month
    y: revenue
rows:
  - revenue_trend
"""


# --- Behavioral: calling without project-scoped kwarg raises TypeError ------


def test_validate_without_project_dir_raises_type_error(tmp_path: Path) -> None:
    board = tmp_path / "f.yml"
    board.write_text(_VALID_BOARD)
    with pytest.raises(TypeError):
        _validate.validate(board)  # type: ignore[call-arg]


def test_validate_paths_without_project_dir_raises_type_error(tmp_path: Path) -> None:
    board = tmp_path / "f.yml"
    board.write_text(_VALID_BOARD)
    with pytest.raises(TypeError):
        _validate.validate_paths([board])  # type: ignore[call-arg]


def test_describe_board_without_project_dir_raises_type_error(tmp_path: Path) -> None:
    board = tmp_path / "f.yml"
    board.write_text(_VALID_BOARD)
    with pytest.raises(TypeError):
        _describe.describe_board(board)  # type: ignore[call-arg]


def test_describe_paths_without_project_dir_raises_type_error(tmp_path: Path) -> None:
    board = tmp_path / "f.yml"
    board.write_text(_VALID_BOARD)
    with pytest.raises(TypeError):
        _describe.describe_paths([board])  # type: ignore[call-arg]


def test_lookup_board_query_sql_without_project_dir_raises_type_error(
    tmp_path: Path,
) -> None:
    board = tmp_path / "f.yml"
    board.write_text(_VALID_BOARD)
    with pytest.raises(TypeError):
        _query.lookup_board_query_sql("revenue", board)  # type: ignore[call-arg]


def test_query_board_without_project_dir_raises_type_error(tmp_path: Path) -> None:
    from dbt_charts.core.execute.env import make_exec_env

    board = tmp_path / "f.yml"
    board.write_text(_VALID_BOARD)
    with pytest.raises(TypeError):
        _query.query_board("revenue", board, env=make_exec_env({}))  # type: ignore[call-arg]


def test_get_board_without_project_dir_raises_type_error(tmp_path: Path) -> None:
    board = tmp_path / "f.yml"
    board.write_text(_VALID_BOARD)
    with pytest.raises(TypeError):
        _boards.get_board(board)  # type: ignore[call-arg]


def test_list_boards_without_directory_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _boards.list_boards()  # type: ignore[call-arg]


def test_validate_ejected_templates_without_target_dir_raises_type_error() -> None:
    with pytest.raises(TypeError):
        _inspect_api.validate_ejected_templates()  # type: ignore[call-arg]


def _empty_proposal() -> PackProposal:
    return PackProposal(
        organization_mode="connector-first",
        detected_sources=[],
        folders=[],
        partials=[],
        uncertainty_notes=[],
        planned_actions=[],
    )


def test_apply_proposal_requires_project() -> None:
    proposal = _empty_proposal()
    with pytest.raises(TypeError):
        apply_proposal(proposal)  # type: ignore[call-arg]


# --- CLI shim: dct validate <path> still works without --project-dir --------


def test_cli_validate_resolves_project_dir_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI command must resolve cwd at the Typer boundary so users don't need
    to pass --project-dir on every invocation."""
    (tmp_path / "dbt_charts.yml").write_text("name: testproj\n")
    boards = tmp_path / "charts"
    boards.mkdir()
    board = boards / "f.yml"
    board.write_text(_VALID_BOARD)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["validate", str(board)])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
