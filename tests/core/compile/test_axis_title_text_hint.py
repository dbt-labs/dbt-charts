"""Tests for the actionable error hint on ``style.axis_*.title``.

Both spellings of "set the axis title text" — a bare string, and a ``text:``
key — must name ``x_label:``/``y_label:``, on every axis slot, and must not
fire on the legend ``title:`` block that shares the key name.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.compiler import compile as compile_board

_QUERY = """
queries:
  q:
    source: db
    sql: SELECT 1 AS m, 2 AS v
"""


# Every authored slot whose `title:` resolves to AxisTitleStylePatch. A line
# chart carries all five; the redirect is anchored to the model, not the name.
_AXIS_SLOTS = ["axis", "axis_x", "axis_y", "axis_quantitative", "axis_band"]


def _board(style_block: str, board_style: str = "") -> str:
    return f"""
title: T
{_QUERY}
{board_style}charts:
  c:
    type: line
    query: q
    x: m
    y: v
    style:
{style_block}
rows:
  - c
"""


def _hints(yaml_text: str) -> str:
    result = compile_board(yaml_text)
    assert not result.success
    return " ".join(e.hint or "" for e in result.errors)


@pytest.mark.parametrize("axis", _AXIS_SLOTS)
def test_scalar_axis_title_names_the_authored_label_key(axis: str) -> None:
    """`style.axis_x.title: "Some text"` — the reported spelling."""
    hint = _hints(_board(f"      {axis}:\n        title: Weeks since registration"))

    assert "x_label" in hint
    assert "y_label" in hint


@pytest.mark.parametrize("axis", _AXIS_SLOTS)
def test_axis_title_text_key_names_the_authored_label_key(axis: str) -> None:
    """The other spelling: a `text:` key inside the typography block."""
    hint = _hints(
        _board(f"      {axis}:\n        title:\n          text: Weeks since reg")
    )

    assert "x_label" in hint
    assert "y_label" in hint


def test_board_level_axis_title_gets_the_hint() -> None:
    """`style.charts.<family>.axis_x.title` reaches the same model, and the
    answer is the same: the text is authored per chart."""
    hint = _hints(
        _board(
            "      axis_x:\n        visible: true",
            board_style="style:\n  charts:\n    line:\n      axis_x:\n        title: Hello\n",
        )
    )

    assert "x_label" in hint


def test_scalar_axis_title_keeps_its_allowed_keys_guidance() -> None:
    """The author may have meant the typography block, so the keys stay."""
    hint = _hints(_board("      axis_x:\n        title: Weeks"))

    assert "align" in hint
    assert "angle" in hint


# --- the hint must not misfire on the other `title` blocks ---


def test_legend_title_does_not_get_the_axis_hint() -> None:
    """`style.legend.title` is a different model that shares the key name;
    its text comes from the color column, not from x_label/y_label."""
    hint = _hints(_board("      key:\n        legend:\n          title: Region"))

    assert "x_label" not in hint
    assert "Available keys" in hint


def test_typo_under_axis_title_keeps_the_generic_suggestion() -> None:
    """`fonts:` is a plain typo for `font:`, not an attempt to set text."""
    hint = _hints(_board("      axis_x:\n        title:\n          fonts: {size: 9}"))

    assert "x_label" not in hint
    assert "Did you mean 'font'?" in hint
