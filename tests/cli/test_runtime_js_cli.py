"""`dct runtime-js` hands a static site the chart hover script."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from dbt_charts.agent_api import hover_runtime_source
from dbt_charts.cli.main import app

runner = CliRunner()


def test_prints_the_hover_runtime() -> None:
    result = runner.invoke(app, ["runtime-js"])
    assert result.exit_code == 0, result.output
    assert result.stdout == hover_runtime_source()


def test_writes_the_hover_runtime_to_a_file(tmp_path: Path) -> None:
    out = tmp_path / "site" / "charts.js"
    result = runner.invoke(app, ["runtime-js", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8") == hover_runtime_source()
