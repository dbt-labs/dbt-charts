"""ERR-VARIABLE-NAME-INVALID: an authored `variables:` key must be a valid
Jinja identifier — a name with a space, hyphen, or leading digit fails
compile() with a clear message.

Only authored keys are checked, before compile generates its own hidden
tabs/details variables (`_tab_<id>`, `_details_<id>`, derived from a
chart/tab `id:`), which are legal even when hyphenated.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.compiler import compile as compile_board


def _board_with_variable(name: str) -> str:
    return f"""
title: T
variables:
  {name}:
    input: text
    default: hi
queries:
  q:
    source: db
    sql: "SELECT 1 AS n"
charts:
  c:
    query: q
    type: bar
    x: n
    y: n
rows:
  - c
"""


@pytest.mark.parametrize("name", ["a b", "a-b", "1a", "a onmouseover=x"])
def test_non_identifier_rejected(name: str) -> None:
    result = compile_board(_board_with_variable(name))
    assert not result.success, f"{name!r} must fail compile"
    assert result.errors[0].code == "ERR-VARIABLE-NAME-INVALID"


def test_unicode_identifier_accepted() -> None:
    """Jinja accepts Unicode identifiers; a variable named "région" is legal."""
    result = compile_board(_board_with_variable("région"))
    assert result.success, f"Compile failed: {result.errors}"


def test_hyphenated_board_id_with_tabs_compiles() -> None:
    """A hyphenated board `id:` feeds the generated tabs variable's default
    name (`_tab_<id>`) when `tabs:` has no `id:` of its own — that name is
    never authored, so it must not be checked for identifier validity."""
    board = """
title: T
id: sales-overview
queries:
  q: {type: values, rows: [{n: 1}]}
tabs:
  items:
    - title: A
      rows: [c1]
    - title: B
      rows: [c1]
charts:
  c1: {query: q, type: kpi, value: n}
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"


def test_hyphenated_tabs_id_compiles() -> None:
    """An authored, hyphenated `tabs.id:` names the generated tabs variable
    directly — still never checked, since it's never a Jinja name."""
    board = """
title: T
queries:
  q: {type: values, rows: [{n: 1}]}
tabs:
  id: region-tabs
  items:
    - title: A
      rows: [c1]
    - title: B
      rows: [c1]
charts:
  c1: {query: q, type: kpi, value: n}
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"


def test_hyphenated_details_id_compiles() -> None:
    """A hyphenated details-section `id:` feeds the generated details
    variable's name directly — same exemption as a tabs variable."""
    board = """
title: T
queries:
  q: {type: values, rows: [{n: 1}]}
rows:
  - id: my-details
    details:
      summary: Notes
    rows:
      - c1
charts:
  c1: {query: q, type: kpi, value: n}
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"
