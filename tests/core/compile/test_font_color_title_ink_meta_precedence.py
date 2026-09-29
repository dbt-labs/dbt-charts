"""Title ink across the charts/meta.yml boundary.

Nested boards are covered in
tests/core/render/test_font_color_reaches_heading_ink.py.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile_file
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

_HEADING_FILL_RE = re.compile(r"\.md-[0-9a-f]{8}-heading\s*\{[^}]*fill:\s*([^;\s}]+)")


def setup_function() -> None:
    reset_config()


def teardown_function() -> None:
    reset_config()


def _write(tmp_path: Path, relpath: str, content: str) -> Path:
    p = tmp_path / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _heading_fill(svg: str) -> str:
    match = _HEADING_FILL_RE.search(svg)
    assert match is not None, f"no .md-*-heading fill rule found in: {svg[:400]}"
    return match.group(1)


def _render_board_file(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> str:
    project = local_project(tmp_path)
    board_file = project.path("charts/board.yaml").read_board()
    result = compile_file(board_file, apply_meta=True)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format="svg").output
    assert isinstance(output, str)
    return output


def test_meta_font_color_reaches_the_boards_title_when_board_authors_neither(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _write(
        tmp_path,
        "charts/meta.yml",
        "style:\n  font:\n    color: '#0000ff'\n",
    )
    _write(
        tmp_path,
        "charts/board.yaml",
        "rows:\n  - text: |\n      # A heading\n\n      Body text.\n",
    )
    svg = _render_board_file(tmp_path, local_project)
    assert _heading_fill(svg) == "#0000ff"


def test_boards_own_title_font_color_wins_over_the_metas(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    _write(
        tmp_path,
        "charts/meta.yml",
        "style:\n  title:\n    font:\n      color: '#ff0000'\n",
    )
    _write(
        tmp_path,
        "charts/board.yaml",
        "style:\n  title:\n    font:\n      color: '#0000ff'\n"
        "rows:\n  - text: |\n      # A heading\n\n      Body text.\n",
    )
    svg = _render_board_file(tmp_path, local_project)
    assert _heading_fill(svg) == "#0000ff"
