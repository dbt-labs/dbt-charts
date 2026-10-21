from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts import compile
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project import CHARTS_SUBDIR

_BOARD = """\
title: Inline rows
queries:
  q:
{query}
charts:
  c:
    query: q
    type: bar
    x: region
    y: amount
rows:
  - c
"""

_RECORD_ROWS = """\
    rows:
      - region: East
        amount: 10
      - region: West
        amount: 20"""


@pytest.mark.parametrize("query", [_RECORD_ROWS, "    type: values\n" + _RECORD_ROWS])
def test_record_shape_rows_compile(
    query: str,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    (tmp_path / CHARTS_SUBDIR).mkdir()
    result = compile(
        _BOARD.format(query=query),
        base_dir=local_project(tmp_path).directory(CHARTS_SUBDIR),
    )

    assert result.success, result.errors
