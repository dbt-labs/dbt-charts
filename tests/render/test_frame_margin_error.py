"""`style.frame.margin` is one number; the error says so and names the per-side keys."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app


@pytest.fixture(autouse=True)
def in_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "dbt_charts.yml").write_text("{}\n")
    monkeypatch.chdir(tmp_path)


def _render(tmp_path: Path, style: str) -> tuple[int, str]:
    board = tmp_path / "m.yml"
    board.write_text("title: M\nstyle:\n" + style + "text: hello\n")
    result = CliRunner().invoke(app, ["render", str(board), "--format", "text"])
    return result.exit_code, result.output


def test_frame_margin_mapping_names_style_margin(tmp_path: Path) -> None:
    code, output = _render(
        tmp_path, "  frame:\n    margin:\n      left: 16\n      right: 16\n"
    )

    assert code != 0
    assert "one number" in output
    assert "style.margin" in output


def test_style_margin_takes_per_side_mapping(tmp_path: Path) -> None:
    code, output = _render(tmp_path, "  margin:\n    left: 16\n    right: 16\n")

    assert code == 0, output
