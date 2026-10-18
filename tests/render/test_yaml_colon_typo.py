"""An unquoted `: ` in a Jinja subtitle is ERR-YAML-SYNTAX with the quoting fix."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

_BOARD = """\
title: T
subtitle: Median: {{ queries.q.one.v }} days
queries:
  q:
    type: values
    rows:
      - v: 1
charts:
  c:
    query: q
    type: bar
    x: v
    y: v
    subtitle: Overall median: {{ queries.q.one.v }} days across {{ queries.q.one.v }} items
rows:
  - c
"""


def test_colon_typos_report_every_line_and_the_quoting_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "dbt_charts.yml").write_text("{}\n")
    (tmp_path / "t.yml").write_text(_BOARD)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["render", "t.yml", "--format", "text"])

    assert result.exit_code == 1
    assert "ERR-INTERNAL" not in result.output
    assert result.output.count("ERR-YAML-SYNTAX") == 2
    assert "wrap the whole value in quotes" in result.output
