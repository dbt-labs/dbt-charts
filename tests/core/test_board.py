"""Regression tests: render_dashboard must not mutate a caller-supplied
``CompileResult``.

A host that authorizes a ``yaml_content`` render once (e.g. Cloud's
``authorize_yaml_content_render``) and then renders the SAME ``compile_result``
twice -- once for the semantic payload, once for the widget SVG -- must get
the same answer both times. ``validate_compiled_queries``/``check_manifest_refs``
append to ``result.errors``/``result.warnings`` in place; if ``render_dashboard``
works directly on the caller's object, the second call inherits the first
call's appended diagnostics and can flip from ``success`` to a spurious
``failed``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import render_dashboard
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.project import InMemoryBoard
from dbt_charts.core.render.render_result import RenderResult

_BOARD_WITH_REF = """\
queries:
  orders:
    sql: "SELECT * FROM {{ ref('fct_orders') }}"
    source: dbt_duckdb
rows: []
"""


def test_render_dashboard_does_not_mutate_a_shared_compile_result(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Two render_dashboard calls sharing one compile_result must leave that
    object's errors/warnings unchanged and return the same status both times.

    Without the fix, board.py:367 binds ``result = compile_result`` directly,
    so ``check_manifest_refs``'s WARN-DBT-MANIFEST-MISSING append on call 1
    lands on the caller's own object -- call 2 then sees a warnings list
    already carrying call 1's entry, doubled by its own append.
    """
    project = local_project(tmp_path)
    board = InMemoryBoard(_BOARD_WITH_REF, path=project.path("charts/_t.yml"))
    compile_result = compile_file(board)
    assert compile_result.success
    assert compile_result.warnings == []

    mock_registry = MagicMock(spec=AdapterRegistry)
    render_result = RenderResult(output='{"id": "test", "title": "Board", "items": []}')

    with (
        patch("dbt_charts.core.board.render", return_value=render_result),
        patch("dbt_charts.core.board.Executor"),
    ):
        first = render_dashboard(
            board=board,
            adapter_registry=mock_registry,
            format="json",
            project=project,
            result_cache=None,
            compile_result=compile_result,
        )
        second = render_dashboard(
            board=board,
            adapter_registry=mock_registry,
            format="json",
            project=project,
            result_cache=None,
            compile_result=compile_result,
        )

    assert compile_result.warnings == [], (
        "render_dashboard must not append to the caller's own CompileResult; "
        f"got {[w.code for w in compile_result.warnings]}"
    )
    assert first.status == second.status
    assert [w.code for w in first.warnings] == [w.code for w in second.warnings]
