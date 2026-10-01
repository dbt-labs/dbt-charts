"""Many irregular-cadence date points on a narrow line chart overprint their
tick labels with no signal today; WARN-AXIS-LABEL-COLLISION is the seam that
makes it visible.

Mirrors ``test_table_cramping_capture.py``'s shape: compile a real board,
render it, and read the warning codes off the output — the fact must survive
the actual render pipeline, not just a hand-built WarningContext (that half
is covered by ``tests/render/warnings/test_axis_label_collision.py``).

Uses irregular (non-month-aligned) dates rather than clean first-of-month
values: a clean monthly/yearly cadence is exactly what
``resolve_axis_x_overlap``'s existing coarsen-to-fit ladder (skip to a
coarser calendar unit, then tilt) already handles well — it can almost
always coarsen a short calendar span down to a handful of yearly labels that
trivially fit. The residual gap this warning targets is the case that ladder
can't resolve: a calendar span whose month openers are too dense for even the
steepest tilt (-90°, footprint == line height) to clear every adjacent pair.
The ladder measures the span, not the rows present, so the card is narrow
enough that the openers themselves collide.
"""

from __future__ import annotations

from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render


def _irregular_daily_rows(n: int) -> str:
    # Deterministic, distinct, non-month-aligned day spread across ~2.5
    # years so the temporal cadence detector can't coarsen these to a clean
    # calendar unit. A step coprime with the 900-day span keeps every
    # offset distinct for n up to 900.
    rows = []
    for i in range(n):
        day_offset = (i * 37) % 900
        year = 2023 + day_offset // 365
        day_of_year = day_offset % 365
        month = (day_of_year // 30) % 12 + 1
        day = (day_of_year % 27) + 1
        rows.append(f"      - {{d: '{year}-{month:02d}-{day:02d}', value: {i}}}")
    return "\n".join(rows)


def _board(n_points: int, width: int) -> str:
    return (
        "title: T\n"
        "queries:\n  q:\n    type: values\n    rows:\n"
        + _irregular_daily_rows(n_points)
        + "\n"
        "charts:\n"
        f"  c: {{query: q, type: line, x: d, y: value, width: {width}}}\n"
        "rows:\n  - c\n"
    )


def _render_warnings(yaml_text: str) -> list[Diagnostic]:
    result = compile_board(yaml_text)
    assert result.success, result.diagnostics
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format="svg")
    return output.warnings


def _warning_codes(yaml_text: str) -> set[str]:
    return {w.code for w in _render_warnings(yaml_text)}


def test_many_irregular_dates_on_a_narrow_chart_warn_on_collision() -> None:
    codes = _warning_codes(_board(n_points=60, width=120))
    assert "WARN-AXIS-LABEL-COLLISION" in codes


def test_collision_message_reports_the_measured_label_count_not_the_row_count() -> None:
    """Pin the real render's message text, not just the warning code: the 60
    raw x values step to the year openers of their calendar span, halved by
    parity skipping to 11, before the fit-check measures them. The message
    must report that 11, not a recount of the 60 raw values."""
    warnings = _render_warnings(_board(n_points=60, width=120))
    collision = next(w for w in warnings if w.code == "WARN-AXIS-LABEL-COLLISION")
    assert "60 tick labels" not in collision.message
    assert "11 tick labels" in collision.message


def test_few_points_on_a_wide_chart_stay_silent() -> None:
    codes = _warning_codes(_board(n_points=4, width=900))
    assert "WARN-AXIS-LABEL-COLLISION" not in codes
