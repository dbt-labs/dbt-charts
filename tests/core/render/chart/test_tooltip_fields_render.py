"""Chart-level `tooltip:` end to end: the rows reach each mark's hover card, and
a column the query lacks fails the chart."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from ...._run import Run

EXTRA = "‍"
_ROWS = [
    {"month": "2024-01-01", "dept": "A", "cost": 10, "share": 0.25, "ok": "yes"},
    {"month": "2024-01-01", "dept": "B", "cost": 20, "share": 0.5, "ok": None},
    {"month": "2024-02-01", "dept": "A", "cost": 30, "share": 0.75, "ok": "no"},
]


def _svg(chart: dict[str, Any], rows: list[dict[str, Any]] = _ROWS) -> str:
    board = yaml.safe_dump(
        {
            "title": "T",
            "queries": {"q": {"type": "values", "rows": rows}},
            "charts": {"c": {"query": "q", **chart}},
            "rows": ["c"],
        },
        sort_keys=False,
    )
    result = compile_board(board)
    assert result.board is not None, result.errors
    executor = Run(
        result.board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
    )
    return render(result.board, executor.ctx, executor.ledger, format="svg").output


def test_authored_rows_follow_the_value_row_and_use_the_resolved_label_and_format() -> (
    None
):
    svg = _svg(
        {
            "type": "bar",
            "x": "month",
            "y": "cost",
            "color": "dept",
            "tooltip": [
                "ok",
                {"column": "share", "label": "% of budget", "format": ".1%"},
            ],
        }
    )
    assert f"{EXTRA}Ok: yes; {EXTRA}% of budget: 25.0%" in svg
    assert f"{EXTRA}Ok: —" in svg


@pytest.mark.parametrize("family", ["bar", "line", "area", "scatter"])
def test_with_layers_the_rows_reach_the_base_marks_only(family: str) -> None:
    rows = [
        {"month": "2024-01-01", "cost": 10, "plan": 12, "ok": "yes"},
        {"month": "2024-02-01", "cost": 30, "plan": 25, "ok": "no"},
    ]
    svg = _svg(
        {
            "type": family,
            "x": "month",
            "y": "cost",
            "tooltip": ["ok"],
            "layers": [{"type": "line", "y": "plan", "label": "Plan"}],
        },
        rows,
    )
    labels = re.findall(r'aria-label="([^"]*)"', svg)
    assert any(f"{EXTRA}Ok: yes" in label for label in labels), labels
    plan = [label for label in labels if "Plan" in label]
    assert plan, labels
    assert not any(EXTRA in label for label in plan), plan


def test_a_missing_tooltip_column_fails_the_chart_naming_it() -> None:
    svg = _svg({"type": "bar", "x": "month", "y": "cost", "tooltip": ["nope"]})
    assert "ERR-CHART-COLUMN-NOT-IN-RESULT" in svg
    assert "tooltip[0]" in svg and "nope" in svg


def test_a_semicolon_in_a_value_is_escaped_for_the_runtime() -> None:
    rows = [{"month": "Jan", "cost": 1, "note": "Q1; Q2"}]
    board = yaml.safe_dump(
        {
            "title": "T",
            "queries": {"q": {"type": "values", "rows": rows}},
            "charts": {
                "c": {
                    "query": "q",
                    "type": "bar",
                    "x": "month",
                    "y": "cost",
                    "tooltip": ["note"],
                }
            },
            "rows": ["c"],
        },
        sort_keys=False,
    )
    result = compile_board(board)
    assert result.board is not None, result.errors
    executor = Run(
        result.board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
    )
    svg = render(result.board, executor.ctx, executor.ledger, format="svg").output
    assert f"{EXTRA}Note: Q1\ue000 Q2" in svg
    assert "Q1;" not in svg


def test_a_url_entry_reaches_the_card_as_a_link_row() -> None:
    rows = [{"month": "Jan", "cost": 1, "ticket": "https://t.io/1"}]
    svg = _svg(
        {
            "type": "bar",
            "x": "month",
            "y": "cost",
            "tooltip": [{"column": "ticket", "format": "url"}],
        },
        rows,
    )
    assert f"{EXTRA}⁯Ticket: https://t.io/1" in svg
