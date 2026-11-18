"""A board that redeclares an inherited variable merges with it, key by key."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import CompileResult, compile_file

PARTIAL = """\
title: Shared filters
variables:
  department:
    input: multiselect
    options:
      static: [Engineering, Sales]
    label: Team
    default: [Engineering]
"""


def _compile(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    child: str,
) -> CompileResult:
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir(exist_ok=True)
    (tmp_path / "charts" / "_filters.yml").write_text(PARTIAL)
    (tmp_path / "charts" / "board.yml").write_text(child)
    project = local_project(tmp_path)
    return compile_file(project.path("charts/board.yml").read_board())


def test_overriding_one_key_keeps_the_inherited_input_options_and_default(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(
        tmp_path,
        local_project,
        "extends: ./_filters.yml\n"
        "title: Board\n"
        "variables:\n"
        "  department:\n"
        "    visible: false\n"
        "rows:\n"
        "  - text: hi\n",
    )
    assert not result.errors
    assert result.board is not None
    var = result.board.variables["department"]
    assert var.visible is False
    assert var.input == "multiselect"
    assert var.options is not None
    assert var.options.static == ["Engineering", "Sales"]
    assert var.default == ["Engineering"]
    assert var.label == "Team"


def test_an_explicit_null_clears_only_that_inherited_key(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(
        tmp_path,
        local_project,
        "extends: ./_filters.yml\n"
        "title: Board\n"
        "variables:\n"
        "  department:\n"
        "    default: null\n"
        "rows:\n"
        "  - text: hi\n",
    )
    assert not result.errors
    assert result.board is not None
    var = result.board.variables["department"]
    assert var.default is None
    assert var.input == "multiselect"
    assert var.label == "Team"


def test_a_ref_over_an_inherited_inline_variable_replaces_it(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "_lib.yml").write_text(
        "title: Lib\n"
        "variables:\n"
        "  region:\n"
        "    input: select\n"
        "    options:\n"
        "      static: [East, West]\n"
    )
    result = _compile(
        tmp_path,
        local_project,
        "extends: ./_filters.yml\n"
        "title: Board\n"
        "variables:\n"
        "  department: _lib.variables.region\n"
        "rows:\n"
        "  - text: hi\n",
    )
    assert not result.errors
    assert result.board is not None
    var = result.board.variables["department"]
    assert var.input == "select"
    assert var.default is None
    assert var.label != "Team"


def test_an_overriding_options_source_replaces_the_inherited_one(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(
        tmp_path,
        local_project,
        "extends: ./_filters.yml\n"
        "title: Board\n"
        "queries:\n"
        "  depts:\n"
        "    columns: [name]\n"
        "    values:\n"
        "      - [Ops]\n"
        "variables:\n"
        "  department:\n"
        "    options:\n"
        "      query: depts\n"
        "      column: name\n"
        "rows:\n"
        "  - text: hi\n",
    )
    assert not result.errors
    assert result.board is not None
    var = result.board.variables["department"]
    assert var.options is not None
    assert var.options.static is None
    assert var.options.query == "depts"
    assert var.label == "Team"


def test_an_overriding_options_query_drops_the_inherited_column(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "_cols.yml").write_text(
        "title: Cols\nvariables:\n  department:\n    column: tickets.department\n"
    )
    result = _compile(
        tmp_path,
        local_project,
        "extends: ./_cols.yml\n"
        "title: Board\n"
        "queries:\n"
        "  depts:\n"
        "    columns: [name]\n"
        "    values:\n"
        "      - [Ops]\n"
        "variables:\n"
        "  department:\n"
        "    options:\n"
        "      query: depts\n"
        "      column: name\n"
        "rows:\n"
        "  - text: hi\n",
    )
    assert not result.errors
    assert result.board is not None
    var = result.board.variables["department"]
    assert var.column is None
    assert var.options is not None
    assert var.options.query == "depts"
