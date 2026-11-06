"""End-to-end: render_dashboard() opens the render-scoped template-output
budget around compile + execute + render, so an amplifying templated field
fails the whole board render with ERR-TEMPLATE-OUTPUT-TOO-LARGE instead of
exhausting memory.

Proves the real per-board-render entry point (not a hand-opened
template_output_budget() scope) — the seam Cloud's renders worker
(apps/cloud/apps/renders/tasks.py) and the CLI both go through. The
cumulative-across-different-call-sites property itself (many fields, each
individually under the ceiling, tripping it combined) is proven
deterministically at the unit level in test_output_budget.py, which drives
resolve_jinja_template()/render_parameterized() directly under one manually
opened scope — not repeated here through a chart query, whose execution
happens on the query executor's thread pool with its own caching and
per-chart error-isolation behavior, orthogonal to this bound.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_CODE = "ERR-TEMPLATE-OUTPUT-TOO-LARGE"


def _render(tmp_path: Path, board_yaml: str, *, format: str = "svg"):
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.env import (
        build_exec_env,
    )

    project = FilesystemProject(tmp_path)
    (tmp_path / "charts").mkdir(exist_ok=True)
    board_path = tmp_path / "charts" / "board.yml"
    board_path.write_text(board_yaml)
    (tmp_path / "dbt_charts.yml").write_text("sources:\n  db:\n    type: duckdb\n")

    registry = build_exec_env(project)
    return render_dashboard(
        board=project.path("charts/board.yml").read_board(),
        project=project,
        env=registry,
        result_cache=None,
        format=format,
    )


class TestAmplificationPayloadFailsTheWholeRender:
    """A nested-loop title template must fail the render with a coded
    error, quickly — not after materializing 10**10 characters."""

    def test_title_amplification_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DCT_MAX_TEMPLATE_OUTPUT_BYTES_CEILING", "1000")
        # range(40) x range(40) = 1,600 chars: over the 1000-byte ceiling
        # either way, but small enough that a disengaged budget still fails
        # the assert below in milliseconds rather than hanging — the title
        # also feeds render/sizing.py's mdsvg.measure(), which is quadratic
        # in string length, so a much larger payload here would hang on a
        # disengaged budget the same way the unit-level amplification
        # payload would (that one, and its bounded-peak assertion, lives in
        # test_output_budget.py, which never reaches sizing).
        board_yaml = (
            "title: >-\n"
            "  {% for i in range(40) %}{% for j in range(40) %}x"
            "{% endfor %}{% endfor %}\n"
            "text: Marker\n"
        )

        result = _render(tmp_path, board_yaml)

        assert result.status == "failed"
        assert result.board_error is not None
        assert result.board_error.code == _CODE, result.board_error
