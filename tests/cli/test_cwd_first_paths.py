"""A relative board argument means what a shell means: relative to cwd."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli._parsing import cwd_first
from dbt_charts.cli.main import app

_BOARD = """\
queries:
  q:
    columns: [month, sends]
    values:
      - ["2026-06", 1000]
charts:
  c:
    query: q
    type: bar
    x: month
    y: sends
rows:
  - c
"""


def test_cwd_first_makes_a_path_that_exists_from_cwd_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "board.yml").write_text(_BOARD)
    monkeypatch.chdir(tmp_path)

    assert cwd_first(Path("proj/board.yml")) == Path.cwd() / "proj" / "board.yml"


def test_cwd_first_leaves_project_relative_and_absolute_paths_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for path in (Path("charts/not-under-cwd.yml"), Path("-"), tmp_path / "abs.yml"):
        assert cwd_first(path) == path


@pytest.mark.parametrize("verb", ["render", "validate", "describe"])
def test_relative_board_path_from_the_parent_of_project_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "dbt_charts.yml").write_text("")
    (project / "board.yml").write_text(_BOARD)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, [verb, "proj/board.yml", "--project-dir", "proj"])

    assert result.exit_code == 0, result.output


def test_output_template_dir_is_the_directory_as_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    (project / "charts").mkdir(parents=True)
    (project / "dbt_charts.yml").write_text("")
    (project / "charts" / "a.yml").write_text(_BOARD)
    (project / "charts" / "b.yml").write_text(_BOARD)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(
        app, ["render", "charts/a.yml", "charts/b.yml", "-o", "out/{dir}/{stem}.svg"]
    )

    assert result.exit_code == 0, result.output
    assert (project / "out" / "charts" / "a.svg").is_file()
