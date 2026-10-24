"""Tests for the actionable error hint on style.font / style.border for the
eight chart families with no per-chart card render surface.

bar, line, area, scatter, histogram, heatmap, pie, and donut structurally
reject style.font/style.border (extra_forbidden): they render via Vega-Lite
with no card surface distinct from the board frame, so neither field has
anywhere to paint. A 0.5.0 board's key is stripped by migration; where the
migration cannot finish (an unrelated unknown key strands the board), the
author meets the extra_forbidden instead. A bare one names the field but not
the reason; this hint says both.

The hint must be anchored to the slot, not merely to a path containing
``style``: ``font`` and ``border`` are among the most reused key names in the
style tree, and telling an author who typo'd ``style.axis_x.ticks.font`` to
delete the field would be worse than the generic error it replaces.
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

_CHART_BODY_BY_TYPE = {
    "bar": "x: m\n    y: v",
    "line": "x: m\n    y: v",
    "area": "x: m\n    y: v",
    "scatter": "x: m\n    y: v",
    "histogram": "x: m",
    "heatmap": "x: m\n    y: v",
    "pie": "theta: v",
    "donut": "theta: v",
}


# An unknown key no migration declares: it strands the whole board, so the
# card-style key is reported as authored rather than stripped.
_STRANDING_KEY = "not_a_chart_field: 1"


def _board(chart_type: str, style_key: str, stranded: bool = True) -> str:
    body = _CHART_BODY_BY_TYPE[chart_type]
    stranding = f"\n    {_STRANDING_KEY}" if stranded else ""
    return f"""
title: T
{_QUERY}
charts:
  c:
    type: {chart_type}
    query: q
    {body}{stranding}
    style:
      {style_key}:
        {"size" if style_key == "font" else "width"}: 2
rows:
  - c
"""


@pytest.mark.parametrize("chart_type", sorted(_CHART_BODY_BY_TYPE))
@pytest.mark.parametrize("style_key", ["font", "border"])
def test_card_style_field_alone_is_migrated_away(
    chart_type: str, style_key: str
) -> None:
    result = compile_board(_board(chart_type, style_key, stranded=False))

    assert result.success, [e.message for e in result.errors]


@pytest.mark.parametrize("chart_type", sorted(_CHART_BODY_BY_TYPE))
@pytest.mark.parametrize("style_key", ["font", "border"])
def test_card_style_field_raises_with_hint(chart_type: str, style_key: str) -> None:
    """style.font / style.border on the 8 card-less families names the field
    and says it is unsupported on that family — not a bare extra_forbidden dump."""
    result = compile_board(_board(chart_type, style_key))

    assert not result.success
    errors = [
        e
        for e in result.errors
        if (e.fields or {}).get("field_path", "").endswith(f"style.{style_key}")
    ]
    assert errors, [e.message for e in result.errors]
    all_text = " ".join((e.message or "") + " " + (e.hint or "") for e in errors)
    assert style_key in all_text
    assert chart_type in all_text
    expected = "delete `" if style_key == "border" else "not supported"
    assert expected in all_text.lower()


# --- the hint must not misfire on a nested field that shares the key name ---

_NESTED_SHARING_THE_KEY_NAME = {
    "axis_x.ticks.font": "axis_x:\n        ticks:\n          font: {size: 9}",
    "legend.font": "legend:\n        font: {size: 9}",
    "marks.border": "marks:\n        border: {width: 1}",
    "title.border": "title:\n        border: {width: 1}",
}


def _bar_board_with_nested_style(nested: str) -> str:
    return f"""
title: T
{_QUERY}
charts:
  c:
    type: bar
    query: q
    x: m
    y: v
    style:
      {nested}
rows:
  - c
"""


@pytest.mark.parametrize(
    "nested", sorted(_NESTED_SHARING_THE_KEY_NAME), ids=lambda k: k.replace(".", "_")
)
def test_nested_font_or_border_does_not_get_the_card_style_hint(nested: str) -> None:
    """A deeper font/border is a different, valid field that shares the name.

    Whether it compiles or errors is not this test's business — what matters is
    that it is never told the field has no effect and should be removed.
    """
    result = compile_board(
        _bar_board_with_nested_style(_NESTED_SHARING_THE_KEY_NAME[nested])
    )

    hints = " ".join((e.hint or "") for e in result.errors)
    assert "no per-chart card" not in hints


def test_wrong_nested_font_path_keeps_its_allowed_keys_guidance() -> None:
    """The regression this anchoring protects.

    ``style.axis_x.ticks.font`` is a real mistake — ticks has no font, the
    author wanted ``labels.font``. Before anchoring it was answered with "the
    field has no effect and should be removed", which sends them to delete
    working intent. It must keep the generic hint that lists the keys ticks
    actually accepts.
    """
    result = compile_board(
        _bar_board_with_nested_style(
            "axis_x:\n        ticks:\n          font: {size: 9}"
        )
    )

    assert not result.success
    hint = " ".join((e.hint or "") for e in result.errors)
    assert "no per-chart card" not in hint
    assert "Allowed keys" in hint


# --- board-level position: style.charts.<family>.<field> ---


def _board_level(family: str, field: str, stranded: bool = False) -> str:
    value = "size: 40" if field == "font" else "width: 2"
    body = _CHART_BODY_BY_TYPE[family]
    stranding = f"    {_STRANDING_KEY}\n" if stranded else ""
    return f"""
title: T
{_QUERY}
style:
  charts:
    {family}:
      {field}:
        {value}
charts:
  c:
    type: {family}
    query: q
    {body}
{stranding}rows:
  - c
"""


@pytest.mark.parametrize("family", sorted(set(_CHART_BODY_BY_TYPE) - {"donut"}))
@pytest.mark.parametrize("field", ["font", "border"])
def test_board_level_slot_is_migrated_away_not_failed(family: str, field: str) -> None:
    """style.charts.<family>.<field> ships a Deletion, so the better outcome
    applies: the board compiles and `dct migrate` strips the key. No hint is
    needed because there is no error."""
    result = compile_board(_board_level(family, field))

    assert result.success, [e.message for e in result.errors]


@pytest.mark.parametrize("field", ["font", "border"])
def test_board_level_slot_gets_the_hint_when_migration_cannot_finish(
    field: str,
) -> None:
    """A stranded board cannot reach the current grammar, so the original
    mapping is reported and the board-level position surfaces as an error after
    all — which is the position that needs the hint."""
    result = compile_board(_board_level("bar", field, stranded=True))

    assert not result.success
    board_level = [
        e
        for e in result.errors
        if (e.fields or {}).get("field_path", "").startswith("style.charts.")
    ]
    assert board_level, [e.message for e in result.errors]
    story = f"{board_level[0].message} {board_level[0].hint}"
    assert "no per-chart card" in story
    assert "bar" in story
