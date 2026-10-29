"""Compile and render a board's YAML to its SVG text, for baseline measurements."""

from __future__ import annotations

import tempfile
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.board_resolve import build_resolved_board
from dbt_charts.core.render.boards import render_board_svg


def body_line() -> float:
    rs = resolve_style(get_theme_style())
    size, line_height = rs.text.font.size, rs.text.font.line_height
    assert size is not None and line_height is not None
    return float(size) * float(line_height)


def render_board_svg_text(yaml_text: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_board(yaml_text)
        assert result.success, result.errors
        assert result.board is not None
        executor = Executor(
            result.board,
            build_adapter_registry(FilesystemProject(Path(tmp))),
            query_registry=result.query_registry,
        )
        variables: dict[str, object] = {}
        resolved, render_cache = build_resolved_board(result.board, executor, variables)
        background = resolved.style.background
        return render_board_svg(
            resolved,
            executor,
            variables,
            background=None if background == "transparent" else background,
            render_cache=render_cache,
        )
