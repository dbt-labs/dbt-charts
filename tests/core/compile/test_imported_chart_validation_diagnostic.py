"""Validation of charts imported via `<file>.charts.<name>`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile

_SOURCE = """\
title: Other
queries:
  revenue:
    sql: "SELECT 1 AS month, 2 AS revenue"
    source: db
charts:
  rev_chart:
    type: bar
    query: revenue
    x: month
    y: revenue
    style:
      legend:
        position: 42
rows:
  - rev_chart
"""


def test_invalid_field_in_imported_chart_is_a_coded_diagnostic(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "other.yaml").write_text(_SOURCE)
    main_yaml = """\
title: Importer
charts:
  rev_chart: other.yaml.charts.rev_chart
rows:
  - rev_chart
"""
    result = compile(
        main_yaml,
        base_dir=local_project(root=tmp_path).directory(),
        file="main.yaml",
    )
    [inline] = compile(_SOURCE, file="other.yaml").errors

    assert not result.success
    [err] = result.errors
    assert err.code == inline.code == "ERR-WRONG-SHAPE"
    assert err.path == inline.path
    assert err.message == inline.message
    assert err.hint == inline.hint
    assert err.range == inline.range
    assert err.range is not None
    assert err.range.file == "other.yaml"
    assert err.range.start_line == 14
