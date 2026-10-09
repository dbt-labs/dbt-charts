"""A child that restates a same-axis layout appends to the parent's, and says so."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import CompileResult, compile_file

PARENT = """\
title: Base
rows:
  - text: one
  - text: two
  - text: three
"""


def _compile(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    child: str,
) -> CompileResult:
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "base.yml").write_text(PARENT)
    (tmp_path / "charts" / "variant.yml").write_text(child)
    project = local_project(tmp_path)
    return compile_file(project.path("charts/variant.yml").read_board())


def test_extends_with_restated_rows_warns_that_parent_rows_are_appended(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(
        tmp_path,
        local_project,
        "extends: ./base.yml\ntitle: Variant\nrows:\n  - text: four\n",
    )
    assert not result.errors
    (warning,) = [
        w for w in result.warnings if w.code == "WARN-EXTENDS-LAYOUT-APPENDED"
    ]
    assert warning.message == (
        "`rows:` here is appended to the 3 rows inherited from ./base.yml, "
        "not substituted for them."
    )
    assert warning.path == "rows"
    assert warning.range is not None
    assert result.board is not None
    assert len(result.board.layout.items) == 4


def test_restated_cols_warns_too(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "base.yml").write_text(
        "title: Base\ncols:\n  - text: one\n  - text: two\n"
    )
    (tmp_path / "charts" / "variant.yml").write_text(
        "extends: ./base.yml\ncols:\n  - text: three\n"
    )
    result = compile_file(
        local_project(tmp_path).path("charts/variant.yml").read_board()
    )
    (warning,) = [
        w for w in result.warnings if w.code == "WARN-EXTENDS-LAYOUT-APPENDED"
    ]
    assert "2 cols" in warning.message


@pytest.mark.parametrize(
    "child",
    [
        "extends: ./base.yml\ntitle: V\n",
        "extends: ./base.yml\ntitle: V\ncols:\n  - text: four\n",
    ],
    ids=["no-layout", "cross-axis"],
)
def test_no_warning_without_a_same_axis_restate(
    tmp_path: Path, local_project: Callable[..., FilesystemProject], child: str
) -> None:
    result = _compile(tmp_path, local_project, child)
    assert not result.errors
    assert not [w for w in result.warnings if w.code == "WARN-EXTENDS-LAYOUT-APPENDED"]
