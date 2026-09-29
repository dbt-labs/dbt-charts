"""ERR-LINK-SCHEME-UNSAFE / ERR-LINK-SCHEME-UNANCHORED: authored link schemes
are validated at compile(), not left to render as a live href.

A chart `link:`, table column `link:`/`header_link:`, or `style.footer.link:`
must resolve to a board path, http, https, or mailto scheme; a template-first
link (`{{ x }}` with no literal scheme-fixing prefix) must anchor to one
unless its render path re-checks the resolved value regardless of anchoring
(a table link).
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.compiler import compile as compile_board

_UNSAFE_SCHEMES = [
    "javascript:alert(1)",
    " JaVa\tScript:x",
    "data:text/html,x",
    "vbscript:x",
    "command:x",
    "vscode://x",
    "file:///etc/passwd",
]


def _yaml_dquote(value: str) -> str:
    """Double-quoted YAML scalar for *value* — preserves a literal tab
    (unlike Python's ``repr()``, which single-quotes and leaves YAML to read
    its backslash-t back as two literal characters, not a real tab)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _board_with_chart_link(link: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    link: {_yaml_dquote(link)}
rows:
  - revenue
"""


def _board_with_footer_link(link: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
style:
  footer:
    link: {_yaml_dquote(link)}
rows:
  - revenue
"""


def _board_with_header_link(link: str) -> str:
    return f"""
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  t:
    query: q
    type: table
    style:
      columns:
        month:
          header_link: {_yaml_dquote(link)}
rows:
  - t
"""


@pytest.mark.parametrize("link", _UNSAFE_SCHEMES)
def test_unsafe_scheme_rejected_on_chart_link(link: str) -> None:
    result = compile_board(_board_with_chart_link(link))
    assert not result.success, f"{link!r} must fail compile"
    assert result.errors[0].code == "ERR-LINK-SCHEME-UNSAFE"


@pytest.mark.parametrize("link", _UNSAFE_SCHEMES)
def test_unsafe_scheme_rejected_on_footer_link(link: str) -> None:
    result = compile_board(_board_with_footer_link(link))
    assert not result.success, f"{link!r} must fail compile"
    assert result.errors[0].code == "ERR-LINK-SCHEME-UNSAFE"


@pytest.mark.parametrize("link", _UNSAFE_SCHEMES)
def test_unsafe_scheme_rejected_on_header_link(link: str) -> None:
    result = compile_board(_board_with_header_link(link))
    assert not result.success, f"{link!r} must fail compile"
    assert result.errors[0].code == "ERR-LINK-SCHEME-UNSAFE"


def test_template_prefix_fixes_scheme() -> None:
    """`/x?id={{ id }}` is safe whatever the row holds — the literal prefix
    already fixes a board-path destination."""
    result = compile_board(_board_with_chart_link("/x?id={{ id }}"))
    assert result.success, f"Compile failed: {result.errors}"


def test_unanchored_template_rejected_on_chart() -> None:
    """A chart link template with no fixed prefix never gets a second render-
    time check (no sentinel path for template-first chart links), so it must
    anchor at compile."""
    result = compile_board(_board_with_chart_link("java{{ x }}"))
    assert not result.success
    assert result.errors[0].code == "ERR-LINK-SCHEME-UNANCHORED"


def test_anchor_character_anywhere_in_prefix_fixes_scheme() -> None:
    """A scheme can only appear as `scheme:` at the very start of a URL; once
    the literal prefix contains a `/`, `?`, or `#`, no template-filled suffix
    can turn the rest of the string into a scheme — the anchor character
    doesn't have to be the first one."""
    result = compile_board(_board_with_chart_link("sales?region={{ x }}"))
    assert result.success, f"Compile failed: {result.errors}"


def test_unanchored_template_allowed_on_table_chart_root_link() -> None:
    """A table chart's own chart-root `link:` feeds the row-link band, which
    re-checks the resolved value via checked_href regardless of anchoring —
    the same exemption a per-column link already gets."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  t:
    query: q
    type: table
    link: "{{ month }}"
rows:
  - t
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"


def test_unanchored_template_allowed_on_table() -> None:
    """A table column link resolves against row data at render, where
    checked_href runs on the final value — the compile-time anchoring rule
    doesn't apply to it."""
    board = """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  t:
    query: q
    type: table
    style:
      columns:
        month:
          link: "{{ month }}"
rows:
  - t
"""
    result = compile_board(board)
    assert result.success, f"Compile failed: {result.errors}"
