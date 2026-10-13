"""Ordinary authoring mistakes surface as coded errors, never ERR-INTERNAL."""

from __future__ import annotations

import textwrap

import pytest

from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.diagnostics.diagnostic import Diagnostic


def _only_error(yaml_content: str) -> Diagnostic:
    result = compile(textwrap.dedent(yaml_content), file="board.yml")
    assert not result.success
    assert len(result.errors) == 1, [(e.code, e.message) for e in result.errors]
    return result.errors[0]


def test_theme_with_extends_is_a_coded_error() -> None:
    err = _only_error(
        """\
        theme: neon
        extends: base
        rows:
          - title: hi
        """
    )
    assert err.code == "ERR-THEME-AND-EXTENDS"
    assert "extends: [neon, base]" in err.message
    assert err.fields == {"theme": "neon", "combined": "[neon, base]"}


def test_theme_with_extends_on_a_nested_board_names_its_path() -> None:
    err = _only_error(
        """\
        rows:
          - theme: neon
            extends: [base, other]
            rows:
              - title: hi
        """
    )
    assert err.code == "ERR-THEME-AND-EXTENDS"
    assert "extends: [neon, base, other]" in err.message
    assert err.path == "rows.0"


def test_yaml_syntax_error_is_a_coded_error_with_line() -> None:
    err = _only_error(
        """\
        title: T
        style:
          color:
            categorical:
              palette: [category[2]]
        """
    )
    assert err.code == "ERR-YAML-SYNTAX"
    assert "did not find expected ',' or ']'" in err.message
    assert err.range is not None
    assert err.range.start_line == 5


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        ("dbt-grays.surface", "palette 'dbt-grays' has no alias 'surface'"),
        ("nosuch[2]", "theme has no palette assigned to role 'nosuch'"),
    ],
)
def test_unknown_color_token_is_not_a_traceback(token: str, reason: str) -> None:
    err = _only_error(
        f"""\
        style:
          charts:
            background: {token}
        rows:
          - title: hi
        """
    )
    assert err.code == "ERR-COLOR-TOKEN"
    assert "style.charts.background" in err.message
    assert reason in err.message


def test_marks_line_width_hint_does_not_mention_chart_root() -> None:
    err = _only_error(
        """\
        queries:
          q:
            type: values
            rows:
              - {a: 1, b: 2}
        charts:
          c:
            type: line
            query: q
            x: a
            y: b
            style:
              marks:
                line:
                  width: 4
        rows:
          - c
        """
    )
    assert err.code == "ERR-EXTRA-FIELD"
    assert err.hint is not None
    assert "chart root" not in err.hint
    assert "stroke" in err.hint


def test_root_allowed_keys_include_theme() -> None:
    err = _only_error(
        """\
        bogus_key: 1
        rows:
          - title: hi
        """
    )
    assert err.code == "ERR-EXTRA-FIELD"
    assert err.hint is not None
    assert ", theme," in err.hint


def test_board_wrapper_key_is_an_extra_field_with_a_move_up_hint() -> None:
    err = _only_error(
        """\
        board:
          title: T
        rows:
          - title: hi
        """
    )
    assert err.code == "ERR-EXTRA-FIELD"
    assert err.hint is not None
    assert "top level" in err.hint


def test_non_string_theme_is_a_field_error() -> None:
    err = _only_error(
        """\
        theme: 3
        rows:
          - title: hi
        """
    )
    assert err.code == "ERR-VALIDATION-FIELD"
    assert "'theme'" in err.message


def test_nested_board_color_token_claims_no_root_location() -> None:
    err = _only_error(
        """\
        style:
          charts:
            background: "#ffffff"
        rows:
          - style:
              charts:
                background: category.nope
            rows:
              - title: hi
        """
    )
    assert err.code == "ERR-COLOR-TOKEN"
    assert "category.nope" in err.message
    assert err.path is None
    assert err.range is None
