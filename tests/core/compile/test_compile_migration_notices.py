"""In-memory migration notices surface as WARN-SCHEMA-MIGRATED diagnostics.

A notice that escapes as a Python warning reaches stderr as raw text and
corrupts `--diagnostics-json` streams.
"""

from __future__ import annotations

import threading
import warnings
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile, compile_file
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    collect_migration_notices,
    prepare_board_mapping,
)
from dbt_charts.core.compile.migrations.migrations import warn_migration
from dbt_charts.core.compile.parse.parser import load_yaml_mapping
from dbt_charts.core.diagnostics import Diagnostic

# `style.axis_y.format` is the 0.3.1 spelling of `style.axis_y.labels.format`.
_RETIRED_BOARD = """\
title: t
queries:
  q:
    type: values
    columns: [n]
    values:
      - [1]
charts:
  revenue:
    type: bar
    query: q
    x: n
    y: n
    style:
      axis_y:
        format: currency_whole
rows:
  - revenue
"""


def _migration_warnings(result_warnings: list[Diagnostic]) -> list[Diagnostic]:
    return [d for d in result_warnings if d.code == "WARN-SCHEMA-MIGRATED"]


def test_compile_reports_in_memory_migration_as_diagnostic() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = compile(_RETIRED_BOARD)

    assert result.success, result.errors
    assert not [w for w in caught if issubclass(w.category, SchemaMigrationWarning)]
    [notice] = _migration_warnings(result.warnings)
    assert "migrated this YAML in memory" in notice.message
    assert notice.level == "warning"


def test_compile_file_reports_in_memory_migration_as_diagnostic(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    board = tmp_path / "charts" / "old.yml"
    board.parent.mkdir(parents=True)
    board.write_text(_RETIRED_BOARD)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = compile_file(
            local_project(tmp_path).path("charts/old.yml").read_board()
        )

    assert result.success, result.errors
    assert not [w for w in caught if issubclass(w.category, SchemaMigrationWarning)]
    [notice] = _migration_warnings(result.warnings)
    assert notice.message.startswith("charts/old.yml: dbt charts migrated")


def test_current_board_reports_no_migration() -> None:
    result = compile(_RETIRED_BOARD.replace("format:", "labels:\n          format:"))
    assert result.success, result.errors
    assert not _migration_warnings(result.warnings)


def test_migration_outside_compile_still_warns() -> None:
    with pytest.warns(SchemaMigrationWarning, match="migrated this YAML in memory"):
        prepare_board_mapping(load_yaml_mapping(_RETIRED_BOARD))


@pytest.mark.parametrize(
    ("board_extra", "stale_file", "stale_yaml"),
    [
        pytest.param("", "charts/meta.yml", "theme: solid\n", id="meta"),
        pytest.param("", "charts/meta.yml", "extends: cream\n", id="meta-extends"),
        pytest.param(
            "extends: ./_frag.yml\n", "charts/_frag.yml", "theme: solid\n", id="extends"
        ),
    ],
)
def test_notice_points_at_the_stale_file(
    board_extra: str,
    stale_file: str,
    stale_yaml: str,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    charts = tmp_path / "charts"
    charts.mkdir()
    (tmp_path / "dbt_charts.yml").write_text("name: t\n")
    (tmp_path / stale_file).write_text(stale_yaml)
    current = _RETIRED_BOARD.replace("format:", "labels:\n          format:")
    (charts / "board.yml").write_text(board_extra + current)

    result = compile_file(local_project(tmp_path).path("charts/board.yml").read_board())

    assert result.success, result.errors
    assert [d.message.split(":")[0] for d in _migration_warnings(result.warnings)] == [
        stale_file
    ]


def test_collectors_are_isolated_across_threads() -> None:
    barrier = threading.Barrier(2)
    collected: dict[str, list[str]] = {}

    def run(name: str) -> None:
        with collect_migration_notices() as notices:
            barrier.wait()
            warn_migration(name, stacklevel=1)
            barrier.wait()
        collected[name] = [n.message for n in notices]

    threads = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert collected == {"a": ["a"], "b": ["b"]}
