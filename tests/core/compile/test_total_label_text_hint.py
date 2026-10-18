"""Tests for the actionable error hint on ``style.total.label``.

Both spellings of "set the donut center caption text" — a bare string, and a
``text:`` key — must name ``total.label:``, at chart-local and board-tier
positions, and must not fire on ``style.total.value`` (a different model:
the value slot carries a real ``format`` field, no text redirect).
"""

from __future__ import annotations

from dbt_charts.core.compile.compiler import compile as compile_board

_QUERY = """
queries:
  q:
    source: db
    sql: SELECT 1 AS val, 'A' AS cat
"""


def _board(style_block: str, board_style: str = "") -> str:
    return f"""
title: T
{_QUERY}
{board_style}charts:
  c:
    type: pie
    query: q
    theta: val
    color: cat
    style:
{style_block}
rows:
  - c
"""


def _hints(yaml_text: str) -> str:
    result = compile_board(yaml_text)
    assert not result.success
    return " ".join(e.hint or "" for e in result.errors)


def test_scalar_total_label_names_the_authored_label_key() -> None:
    """`style.total.label: "Some text"` — the reported spelling."""
    hint = _hints(_board("      total:\n        label: Sessions"))

    assert "authored on the chart" in hint
    assert "total.label" in hint


def test_total_label_text_key_names_the_authored_label_key() -> None:
    """The other spelling: a `text:` key inside the typography block."""
    hint = _hints(_board("      total:\n        label:\n          text: Sessions"))

    assert "authored on the chart" in hint
    assert "total.label" in hint


def test_board_level_total_label_gets_the_hint() -> None:
    """`style.charts.pie.total.label` reaches the same model, and the answer
    is the same: the text is authored per chart."""
    hint = _hints(
        _board(
            "      inner_radius: 0.6",
            board_style="style:\n  charts:\n    pie:\n      total:\n        label: Sessions\n",
        )
    )

    assert "authored on the chart" in hint
    assert "total.label" in hint


def test_scalar_total_label_keeps_its_allowed_keys_guidance() -> None:
    """The author may have meant the typography block, so the keys stay."""
    hint = _hints(_board("      total:\n        label: Sessions"))

    assert "font" in hint


# --- the hint must not misfire on the value slot, a different model ---


def test_total_value_typo_does_not_get_the_label_hint() -> None:
    """A typo under `value:` (a different model, with its own `font`/`format`
    keys) must not be redirected to the label's text hint."""
    hint = _hints(_board("      total:\n        value:\n          formats: currency"))

    assert "authored on the chart" not in hint
    assert "Did you mean 'format'?" in hint


def test_typo_under_total_label_explains_the_font_mapping() -> None:
    """`fonts:` is a plain typo for `font:`, not an attempt to set text."""
    hint = _hints(_board("      total:\n        label:\n          fonts: {size: 9}"))

    assert "authored on the chart" not in hint
    assert "`font` is a mapping" in hint
