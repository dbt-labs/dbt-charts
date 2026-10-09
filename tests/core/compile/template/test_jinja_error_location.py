"""A Jinja failure inside SQL names the offending template line.

Jinja does not know ``--`` opens a SQL comment, so brace syntax in a comment is
evaluated like any other. The failure must say which line carried it, not just
that something was undefined.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.errors import JinjaError
from dbt_charts.core.compile.template.jinja import resolve_jinja_template
from dbt_charts.core.compile.template.parameterized import (
    render_parameterized_with_queries,
)

QUERIES = {"base": {"sql": "SELECT 1 AS one"}}
SQL = "SELECT one\nFROM {{ queries.base }}\n-- refs look like {{ queries.nosuch }}\n"
SYNTAX_SQL = "SELECT one\nFROM {{ queries.base }}\n-- empty braces {{ }}\n"


def _render_jinja(sql: str) -> str:
    return resolve_jinja_template(sql, queries=QUERIES)


def _render_parameterized(sql: str) -> str:
    return render_parameterized_with_queries(sql, queries=QUERIES).sql


@pytest.mark.parametrize("render", [_render_jinja, _render_parameterized])
def test_undefined_in_a_sql_comment_names_the_line_and_token(render) -> None:
    with pytest.raises(JinjaError) as excinfo:
        render(SQL)

    message = str(excinfo.value)
    assert "line 3" in message
    assert "queries.nosuch" in message


@pytest.mark.parametrize("render", [_render_jinja, _render_parameterized])
def test_syntax_error_in_a_sql_comment_names_the_line(render) -> None:
    with pytest.raises(JinjaError) as excinfo:
        render(SYNTAX_SQL)

    message = str(excinfo.value)
    assert "line 3" in message
    assert "empty braces" in message


def test_line_numbers_ignore_form_feeds_that_jinja_does_not_count() -> None:
    with pytest.raises(JinjaError) as excinfo:
        resolve_jinja_template("{{ z }}\x0cfoo\n{{ y }}", {"z": 1})

    assert "template line 2: {{ y }}" in str(excinfo.value)
