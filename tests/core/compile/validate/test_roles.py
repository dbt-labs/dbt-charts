"""ERR-COLOR-TOKEN: a `style.roles` address that resolves to no color fails
compile() instead of surfacing later as a failed swatch or render error."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.compiler import CompileResult, compile as compile_board

_BOARD = """
title: T
{style}
queries:
  q:
    columns: [a, b]
    values: [[x, 1]]
charts:
  c:
    query: q
    type: bar
    x: a
    y: b
rows:
  - c
"""


def _compile(style: str) -> CompileResult:
    return compile_board(_BOARD.format(style=style))


def test_a_role_address_that_resolves_compiles() -> None:
    result = _compile("style:\n  roles:\n    accent: chrome[3]")

    assert result.success, result.errors


@pytest.mark.parametrize("address", ["chrome.nonsense", "nopalette[2]", "chrome[99]"])
def test_a_role_address_that_resolves_to_nothing_is_an_error(address: str) -> None:
    result = _compile(f"style:\n  roles:\n    ink: {address}")

    assert not result.success
    assert result.errors[0].code == "ERR-COLOR-TOKEN"
    assert "style.roles.ink" in result.errors[0].message
    assert address in result.errors[0].message


def test_a_bad_role_on_a_nested_board_is_an_error() -> None:
    inner = "style:\n  roles:\n    ink: chrome.nonsense"
    result = compile_board(
        f"""
title: T
queries:
  q:
    columns: [a, b]
    values: [[x, 1]]
rows:
  - title: inner
    {inner.replace(chr(10), chr(10) + "    ")}
    charts:
      c:
        query: q
        type: bar
        x: a
        y: b
    rows:
      - c
"""
    )

    assert not result.success
    assert result.errors[0].code == "ERR-COLOR-TOKEN"
