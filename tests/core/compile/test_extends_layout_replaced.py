"""A board's own rows, cols, grid, tabs or tags replace the inherited ones, and say so."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import CompileResult, compile_file

CODE = "WARN-INHERITED-LAYOUT-REPLACED"

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
    *,
    parent: str = PARENT,
    meta: str | None = None,
) -> CompileResult:
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "base.yml").write_text(parent)
    (tmp_path / "charts" / "variant.yml").write_text(child)
    if meta is not None:
        (tmp_path / "charts" / "meta.yml").write_text(meta)
    project = local_project(tmp_path)
    return compile_file(project.path("charts/variant.yml").read_board())


def _warnings(result: CompileResult) -> list:
    return [w for w in result.warnings if w.code == CODE]


def test_extends_with_restated_rows_replaces_the_parent_rows(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(
        tmp_path,
        local_project,
        "extends: ./base.yml\ntitle: Variant\nrows:\n  - text: four\n",
    )
    assert not result.errors
    (warning,) = _warnings(result)
    assert warning.message == (
        "`rows:` here replaces the 3 rows inherited from charts/base.yml."
    )
    assert warning.path == "rows"
    assert warning.range is not None
    assert result.board is not None
    assert len(result.board.layout.items) == 1


def test_cols_over_parent_rows_drops_the_rows_and_warns(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(
        tmp_path,
        local_project,
        "extends: ./base.yml\ntitle: V\ncols:\n  - text: four\n",
    )
    assert not result.errors
    (warning,) = _warnings(result)
    assert warning.message == (
        "`cols:` here replaces the 3 rows inherited from charts/base.yml."
    )
    assert result.board is not None
    assert len(result.board.layout.items) == 1


def test_board_without_layout_inherits_the_parent_layout(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(tmp_path, local_project, "extends: ./base.yml\ntitle: V\n")
    assert not result.errors
    assert not _warnings(result)
    assert result.board is not None
    assert len(result.board.layout.items) == 3


def test_meta_rows_are_replaced_by_board_rows_and_warn(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(
        tmp_path,
        local_project,
        "title: V\nrows:\n  - text: mine\n",
        meta="rows:\n  - text: header\n",
    )
    assert not result.errors
    (warning,) = _warnings(result)
    assert warning.message == (
        "`rows:` here replaces the 1 rows inherited from charts/meta.yml."
    )
    assert result.board is not None
    assert len(result.board.layout.items) == 1


def test_empty_inherited_rows_do_not_warn(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(
        tmp_path,
        local_project,
        "extends: ./base.yml\nrows:\n  - text: mine\n",
        parent="title: Base\nrows: []\n",
    )
    assert not result.errors
    assert not _warnings(result)


def test_board_tags_replace_meta_tags(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(
        tmp_path,
        local_project,
        "title: V\ntags:\n  - mine\nrows:\n  - text: x\n",
        meta="tags:\n  - folder\n",
    )
    assert not result.errors
    assert result.board is not None
    assert result.board.tags == ["mine"]
    (warning,) = _warnings(result)
    assert warning.path == "tags"


def test_board_without_tags_inherits_meta_tags(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(
        tmp_path,
        local_project,
        "title: V\nrows:\n  - text: x\n",
        meta="tags:\n  - folder\n",
    )
    assert not result.errors
    assert result.board is not None
    assert result.board.tags == ["folder"]
    assert not _warnings(result)


def test_nested_metas_warn_when_the_board_sets_nothing(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "charts" / "sub").mkdir(parents=True)
    (tmp_path / "charts" / "meta.yml").write_text("rows:\n  - text: root\n")
    (tmp_path / "charts" / "sub" / "meta.yml").write_text("rows:\n  - text: leaf\n")
    (tmp_path / "charts" / "sub" / "b.yml").write_text("title: B\n")
    (tmp_path / "dbt_charts.yml").write_text("")
    result = compile_file(local_project(tmp_path).path("charts/sub/b.yml").read_board())
    (warning,) = _warnings(result)
    assert warning.message == (
        "`rows:` in charts/sub/meta.yml replaces the 1 rows inherited from "
        "charts/meta.yml."
    )
    assert warning.path is None
    assert result.board is not None
    assert len(result.board.layout.items) == 1


def test_extends_chain_warns_at_each_replacement(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "c.yml").write_text("rows:\n  - text: c\n")
    (tmp_path / "charts" / "p.yml").write_text("extends: ./c.yml\nrows:\n  - text: p\n")
    (tmp_path / "charts" / "g.yml").write_text("extends: ./p.yml\ntitle: G\n")
    (tmp_path / "dbt_charts.yml").write_text("")
    result = compile_file(local_project(tmp_path).path("charts/g.yml").read_board())
    (warning,) = _warnings(result)
    assert warning.message == (
        "`rows:` in charts/p.yml replaces the 1 rows inherited from charts/c.yml."
    )


def test_list_extends_warns_for_the_later_entry(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "c.yml").write_text("rows:\n  - text: c\n")
    (tmp_path / "charts" / "p.yml").write_text("rows:\n  - text: p\n")
    (tmp_path / "charts" / "g.yml").write_text(
        "extends:\n  - ./c.yml\n  - ./p.yml\ntitle: G\n"
    )
    (tmp_path / "dbt_charts.yml").write_text("")
    result = compile_file(local_project(tmp_path).path("charts/g.yml").read_board())
    (warning,) = _warnings(result)
    assert warning.message == (
        "`rows:` in charts/p.yml replaces the 1 rows inherited from charts/c.yml."
    )


def test_a_parent_replacing_meta_rows_is_named_not_the_board(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    result = _compile(
        tmp_path,
        local_project,
        "extends: ./base.yml\ntitle: V\n",
        meta="rows:\n  - text: header\n",
    )
    (warning,) = _warnings(result)
    assert warning.message == (
        "`rows:` in charts/base.yml replaces the 1 rows inherited from charts/meta.yml."
    )
    assert warning.path is None


def test_same_kind_grid_replaces_the_parent_grid(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    grid = "grid:\n  columns: 12\n  items:\n    - item:\n        text: {}\n"
    result = _compile(
        tmp_path,
        local_project,
        f"extends: ./base.yml\n{grid.format('mine')}",
        parent=f"title: Base\n{grid.format('theirs')}",
    )
    assert not result.errors
    (warning,) = _warnings(result)
    assert (
        warning.message
        == "`grid:` here replaces the grid inherited from charts/base.yml."
    )


def test_same_kind_tabs_replace_the_parent_tabs(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    tabs = "tabs:\n  items:\n    - title: T\n      text: {}\n"
    result = _compile(
        tmp_path,
        local_project,
        f"extends: ./base.yml\n{tabs.format('mine')}",
        parent=f"title: Base\n{tabs.format('theirs')}",
    )
    assert not result.errors
    (warning,) = _warnings(result)
    assert (
        warning.message
        == "`tabs:` here replaces the tabs inherited from charts/base.yml."
    )


def test_a_fragment_reached_twice_does_not_warn(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "c.yml").write_text("rows:\n  - text: c\n")
    (tmp_path / "charts" / "a.yml").write_text("extends: ./c.yml\n")
    (tmp_path / "charts" / "b.yml").write_text("extends: ./c.yml\n")
    (tmp_path / "charts" / "g.yml").write_text(
        "extends:\n  - ./a.yml\n  - ./b.yml\ntitle: G\n"
    )
    (tmp_path / "dbt_charts.yml").write_text("")
    result = compile_file(local_project(tmp_path).path("charts/g.yml").read_board())
    assert _warnings(result) == []
