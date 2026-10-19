"""A failing board reports each failure once, as a diagnostic, not as log lines too.

A fresh interpreter, because pytest installs its own root logging handler and
would mask how the CLI configures logging for itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_BOARD = """\
title: Failing
queries:
  broken:
    source: mem
    sql: SELECT nonexistent_col FROM (SELECT 1 AS a) t
charts:
  broken_table:
    type: table
    query: broken
rows:
  - cols:
      - broken_table
"""


def _render(tmp_path: Path, *root_args: str) -> str:
    (tmp_path / "dbt_charts.yml").write_text("sources:\n  mem:\n    type: duckdb\n")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "board.yml").write_text(_BOARD)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "dbt_charts.cli.main",
            *root_args,
            "render",
            "charts/board.yml",
            "--format",
            "svg",
            "--no-cache",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "DCT_NO_WORKSPACE_GUARD": "1", "COLUMNS": "200"},
    )
    return proc.stdout + proc.stderr


def test_failing_query_prints_its_diagnostic_once_and_no_log_lines(
    tmp_path: Path,
) -> None:
    out = _render(tmp_path)
    assert out.count("ERR-BINDER-UNKNOWN-COLUMN") == 1, out
    assert "dbt_charts." not in out, out


def test_verbose_shows_library_log_records(tmp_path: Path) -> None:
    out = _render(tmp_path, "--verbose")
    assert "dbt_charts.core.render.layout_sizing - DEBUG" in out, out
