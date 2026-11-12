"""A rendered board draws its nested band's controls compact and its own full-size."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject

_BOARD = """
title: T
variables:
  root_pick: {input: select, options: {static: [x, y]}, default: x}
queries:
  q: {type: values, rows: [{k: a, v: 1}]}
charts:
  c1: {query: q, type: bar, x: k, y: v}
rows:
  - title: Nested
    text: Subtitle.
    style:
      variables:
        position: top
    variables:
      nested_pick: {input: select, options: {static: [x, y]}, default: x}
      nested_buttons: {input: radio, options: {static: [p, q]}}
    rows: [c1]
"""


def _field_heights(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> dict[str, set[float]]:
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.env import build_exec_env
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    board = InMemoryBoard(_BOARD, path=project.path("charts/_t.yml"))
    result = render_dashboard(
        board=board,
        env=build_exec_env(project, read_only=False),
        format="svg",
        project=project,
        result_cache=None,
    )
    assert isinstance(result.data, str)
    root = ET.fromstring(result.data)
    heights: dict[str, set[float]] = {}
    for group in root.iter():
        name = group.get("data-dbt-variable")
        if name is None:
            continue
        heights[name] = {
            float(e.attrib["height"])
            for e in group.iter()
            if e.get("data-dbt-field") in ("input", "buttons")
        }
    return heights


def test_nested_controls_are_compact_and_root_controls_are_not(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    variables = resolve_style(get_theme_style()).variables

    heights = _field_heights(tmp_path, local_project)

    assert heights["root_pick"] == {float(variables.input.height)}
    assert heights["nested_pick"] == {float(variables.compact_height)}
    assert heights["nested_buttons"] == {float(variables.compact_height)}
