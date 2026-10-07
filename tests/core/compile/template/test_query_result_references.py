"""extract_query_result_references: literal ``queries.<name>`` use in board text."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.template.jinja import extract_query_result_references


def test_collects_attribute_and_literal_subscript_names() -> None:
    text = "{{ queries.a.one.x }} {% for r in queries['b'].rows %}{{ r.y }}{% endfor %}"
    assert extract_query_result_references(text) == {"a", "b"}


def test_text_without_jinja_has_no_references() -> None:
    assert extract_query_result_references("plain **markdown**") == set()


def test_fenced_code_is_not_scanned() -> None:
    text = "```\n{{ queries[name] }}\n```\n{{ queries.a.one.x }}"
    assert extract_query_result_references(text) == {"a"}


@pytest.mark.parametrize(
    "template",
    [
        "{{ queries[name].one.x }}",
        "{% set x = queries %}{{ x }}",
        "{{ queries | list }}",
        "{{ queries }}",
        "{{ queries[1] }}",
        "{% macro m() %}{{ queries }}{% endmacro %}",
    ],
)
def test_non_literal_use_raises(template: str) -> None:
    with pytest.raises(CompilationError) as exc:
        extract_query_result_references(template)
    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-TEXT-QUERIES-NONLITERAL"
    assert "literal name" in str(exc.value)
