"""Regression: a missing `type: csv` source file must not crash the render pipeline.

Before this fix, `Executor.is_cached()` (called synchronously in the render loop,
outside any per-chart error boundary) let a bare `FileNotFoundError` escape from
`FilesystemProject.file_version`'s unguarded `.stat()` call — a `dct serve` request
for such a board 500ed with a raw Python traceback in the response body instead of
degrading to the usual per-chart `tone: negative` callout.

See tasks/workstreams/dft-core/tasks/dct-serve-500s-with-a-leaked-traceback-on-a-missing-source-file.md
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import render_dashboard
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.project import InMemoryBoard

_BOARD_YAML = """
title: Missing File Source Board
queries:
  q_good:
    type: values
    rows:
      - {value: 1}
  q_bad:
    sql: "SELECT * FROM sales"
    source: sales
charts:
  good:
    type: kpi
    query: q_good
    value: value
  bad:
    type: table
    query: q_bad
cols:
  - good
  - bad
"""


def _project_with_missing_csv(tmp_path: Path) -> FilesystemProject:
    """A project whose 'sales' CSV source names a `files:` path that never exists."""
    (tmp_path / "dbt_charts.yml").write_text(
        yaml.dump(
            {
                "sources": {
                    "sales": {"type": "csv", "files": {"sales": "data/sales.csv"}}
                }
            }
        )
    )
    return FilesystemProject(tmp_path)


def _project_with_file_blocking_nested_path(tmp_path: Path) -> FilesystemProject:
    """A project whose `files:` path traverses through an existing plain file.

    `data/sales.csv` is a real file, not a directory; `files:` names
    `data/sales.csv/rows.csv` underneath it. Compiles clean — only absolute
    paths are rejected at compile time — but `Path.stat()` on that path
    raises `NotADirectoryError`, not `FileNotFoundError`.
    """
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "sales.csv").write_text("region,amount\nNorth,100\n")
    (tmp_path / "dbt_charts.yml").write_text(
        yaml.dump(
            {
                "sources": {
                    "sales": {
                        "type": "csv",
                        "files": {"sales": "data/sales.csv/rows.csv"},
                    }
                }
            }
        )
    )
    return FilesystemProject(tmp_path)


class TestMissingCsvSourceFile:
    def test_is_cached_does_not_raise_on_missing_file(self, tmp_path: Path) -> None:
        """is_cached must report a miss, not crash, when the backing file is absent."""
        project = _project_with_missing_csv(tmp_path)
        result = compile_board(_BOARD_YAML)
        assert result.success, result.errors
        board = result.board
        assert board is not None
        executor = Executor(
            board,
            adapter_registry=build_adapter_registry(project),
            query_registry=dict(board.queries),
        )

        assert executor.is_cached("q_bad") is False

    def test_render_dashboard_degrades_to_chart_error_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        """render_dashboard() — the function `dct serve` calls per request — must
        not propagate FileNotFoundError; the missing file becomes a per-chart
        diagnostic and the sibling chart still renders."""
        project = _project_with_missing_csv(tmp_path)

        result = render_dashboard(
            board=InMemoryBoard(_BOARD_YAML, path=project.path("charts/_t.yml")),
            adapter_registry=build_adapter_registry(project),
            format="svg",
            project=project,
            result_cache=None,
        )

        assert result.status == "partial"
        assert result.board_error is None
        assert len(result.chart_errors) == 1
        assert result.chart_errors[0].code == "ERR-FILE-SOURCE-NOT-FOUND"
        assert result.data is not None
        assert 'data-chart-id="good"' in result.data
        assert "Traceback" not in result.data

    def test_render_dashboard_degrades_for_an_inline_ref(self, tmp_path: Path) -> None:
        """An inline `source: <path>` that exists at neither anchor compiles and
        fails at execution like the registry form: one tile, not the board."""
        project = FilesystemProject(tmp_path)
        board_yaml = _BOARD_YAML.replace("source: sales", "source: ./data/sales.csv")

        result = render_dashboard(
            board=InMemoryBoard(board_yaml, path=project.path("charts/_t.yml")),
            adapter_registry=build_adapter_registry(project),
            format="svg",
            project=project,
            result_cache=None,
        )

        assert result.status == "partial"
        assert result.board_error is None
        assert [e.code for e in result.chart_errors] == ["ERR-FILE-SOURCE-NOT-FOUND"]
        assert result.data is not None
        assert 'data-chart-id="good"' in result.data

    def test_render_dashboard_degrades_on_nested_path_through_a_file(
        self, tmp_path: Path
    ) -> None:
        """A `files:` path traversing through an existing file raises
        `NotADirectoryError`, not `FileNotFoundError` — a sibling OSError that
        must degrade the same way, not escape `is_cached` as an unhandled 500."""
        project = _project_with_file_blocking_nested_path(tmp_path)

        result = render_dashboard(
            board=InMemoryBoard(_BOARD_YAML, path=project.path("charts/_t.yml")),
            adapter_registry=build_adapter_registry(project),
            format="svg",
            project=project,
            result_cache=None,
        )

        assert result.status == "partial"
        assert result.board_error is None
        assert len(result.chart_errors) == 1
        assert result.chart_errors[0].code == "ERR-FILE-SOURCE-NOT-FOUND"
        # The OS reason threads through — a NotADirectoryError must not be
        # reported as "does not exist" (that's the FileNotFoundError case).
        assert "not a directory" in result.chart_errors[0].message.lower()
        assert result.data is not None
        assert 'data-chart-id="good"' in result.data
        assert "Traceback" not in result.data
