"""A gradient's range is ``domain: {min, max}`` (``[min, max]`` is its shorthand),
through ``compile()``: the board of dbt-labs/dbt-charts#109 compiles as written,
and 0.12.0's ``min``/``max`` and ``domain: data`` migrate (the ``v0_13_0`` moves).
"""

from __future__ import annotations

from dbt_charts.core.compile.compiler import compile

BOARD = """\
title: t
queries:
  q:
    type: values
    columns: [a, b, v]
    values:
      - [x, y, 3]
charts:
  h:
    type: heatmap
    query: q
    x: a
    y: b
    color: v
    style:
      color:
        gradient:
          palette: blues
{gradient}rows:
  - h
"""


def _gradient(gradient: str):
    result = compile(BOARD.format(gradient=gradient))
    assert result.board is not None, result.errors
    assert not result.errors
    return result, result.board.charts["h"].style.color.gradient


def test_the_issues_domain_shorthand_compiles_as_written() -> None:
    result, gradient = _gradient("          domain: [0, 100]\n")
    assert (gradient.domain.min, gradient.domain.max) == (0, 100)
    assert not result.warnings


def test_a_one_sided_shorthand_pins_one_end() -> None:
    _, gradient = _gradient("          domain: [null, 1]\n")
    assert (gradient.domain.min, gradient.domain.max) == (None, 1)


def test_released_min_and_max_migrate_under_domain() -> None:
    result, gradient = _gradient("          min: 0\n          max: 100\n")
    assert (gradient.domain.min, gradient.domain.max) == (0, 100)
    assert any("migrated" in w.message for w in result.warnings)


def test_released_domain_data_migrates_away() -> None:
    result, gradient = _gradient("          domain: data\n")
    assert gradient.domain is None
    assert any("domain: data` is gone" in w.message for w in result.warnings)
