"""An `extends:` path resolves through the one resolver and fails as a diagnostic."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile_file


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        ("../../../x.yml", "escape above the project root"),
        ("_missing.yml", "'charts/_missing.yml' does not exist"),
    ],
)
def test_unresolvable_extends_path_is_a_reference_diagnostic(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    entry: str,
    why: str,
) -> None:
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "a.yml").write_text(f"title: A\nextends: {entry}\n")
    project = local_project(tmp_path)
    result = compile_file(project.path("charts/a.yml").read_board())
    (error,) = result.errors
    assert error.code == "ERR-UNRESOLVED-REFERENCE"
    assert why in error.message
