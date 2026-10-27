"""Render-time capture + detectors for `align: left` on a numeric table column.

The two warning codes are mutually exclusive; their conditions are documented
on the codes themselves (core/diagnostics/codes_render.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render
from dbt_charts.core.render.chart.table_align import (
    collect_table_numeric_align_lefts,
    record_table_numeric_align_left,
    reset_table_align,
)

_NUMERIC_ALIGN_LEFT = "WARN-TABLE-NUMERIC-ALIGN-LEFT"
_ALIGN_NO_VISIBLE_EFFECT = "WARN-TABLE-ALIGN-NO-VISIBLE-EFFECT"


def _scalar(value: Any) -> str:
    return repr(value) if isinstance(value, str) else str(value)


def _board(columns_yaml: str, rows: list[dict[str, Any]]) -> str:
    body = "\n".join(
        "      - {" + ", ".join(f"{k}: {_scalar(v)}" for k, v in row.items()) + "}"
        for row in rows
    )
    return (
        "title: T\n"
        "queries:\n  q:\n    type: values\n    rows:\n" + body + "\n"
        "charts:\n  t:\n    query: q\n    type: table\n    style:\n"
        "      columns:\n" + columns_yaml + "rows:\n  - t\n"
    )


def _warnings(yaml_text: str) -> list[str]:
    result = compile_board(yaml_text)
    assert result.success, result.diagnostics
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor, format="svg")
    return [w.code for w in output.warnings]


def _warning_codes(yaml_text: str) -> set[str]:
    return set(_warnings(yaml_text))


_RAGGED_ROWS = [
    {"month": "Oct", "docs": 7},
    {"month": "Nov", "docs": 1234567},
]
_EQUAL_WIDTH_ROWS = [
    {"month": "Oct", "docs": 10},
    {"month": "Nov", "docs": 20},
]


def test_ragged_width_numeric_column_warns_numeric_align_left() -> None:
    """Different-width rows (1.04K vs 5.8K) never stack their digits, no
    matter how much slack the column has."""
    columns_yaml = "        month: {}\n        docs:\n          align: left\n"
    codes = _warning_codes(_board(columns_yaml, _RAGGED_ROWS))
    assert _NUMERIC_ALIGN_LEFT in codes
    assert _ALIGN_NO_VISIBLE_EFFECT not in codes


def test_equal_width_numeric_column_with_slack_is_silent() -> None:
    """Same-width rows with room to shift left: the digits still stack, just
    shifted — nothing wrong, nothing warns."""
    columns_yaml = "        month: {}\n        docs:\n          align: left\n"
    codes = _warning_codes(_board(columns_yaml, _EQUAL_WIDTH_ROWS))
    assert _NUMERIC_ALIGN_LEFT not in codes
    assert _ALIGN_NO_VISIBLE_EFFECT not in codes


def test_equal_width_numeric_column_pinned_below_its_digits_still_moves_them() -> None:
    """A pinned-narrow column overflows both sides of the decimal lane, so left
    anchoring moves the digits: the key has a visible effect and nothing
    warns. (The no-op case needs the exact digit width; the engine's own
    tests pin it.)"""
    columns_yaml = (
        "        month: {}\n        docs:\n          align: left\n          width: 20\n"
    )
    codes = _warning_codes(_board(columns_yaml, _EQUAL_WIDTH_ROWS))
    assert _ALIGN_NO_VISIBLE_EFFECT not in codes
    assert _NUMERIC_ALIGN_LEFT not in codes


def test_numeric_column_with_no_align_key_is_silent() -> None:
    """No `align:` authored at all: the decimal-aligned default applies, and
    neither alignment warning has anything to say."""
    columns_yaml = "        month: {}\n        docs: {}\n"
    codes = _warning_codes(_board(columns_yaml, _RAGGED_ROWS))
    assert _NUMERIC_ALIGN_LEFT not in codes
    assert _ALIGN_NO_VISIBLE_EFFECT not in codes


def test_text_column_with_align_left_is_silent() -> None:
    """`align: left` on a TEXT column is not this ladder's concern: the
    three-lane numeric paint path never runs for it."""
    columns_yaml = "        month:\n          align: left\n        docs: {}\n"
    rows = [
        {"month": "October", "docs": 1037},
        {"month": "Nov", "docs": 5795},
    ]
    codes = _warning_codes(_board(columns_yaml, rows))
    assert _NUMERIC_ALIGN_LEFT not in codes
    assert _ALIGN_NO_VISIBLE_EFFECT not in codes


def test_one_ragged_column_yields_exactly_one_warning() -> None:
    """Sizing probes render the table too; the latest render's verdict wins, so
    the column is reported once, not once per render."""
    columns_yaml = "        month: {}\n        docs:\n          align: left\n"
    codes = _warnings(_board(columns_yaml, _RAGGED_ROWS))
    assert codes.count(_NUMERIC_ALIGN_LEFT) == 1


def test_a_later_render_replaces_an_earlier_verdict_for_the_same_chart() -> None:
    with collect_table_numeric_align_lefts() as sink:
        record_table_numeric_align_left("t", "docs")
        reset_table_align("t")
        record_table_numeric_align_left("t", "docs")
        record_table_numeric_align_left("t", "other")
    assert sink == {"t": ["docs", "other"]}
