"""A layout item that is no chart id, container or typed inline chart names its
path, what it holds, and what the slot accepts; schema failures carry paths.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.errors import ParseError
from dbt_charts.core.compile.parse.parser import parse_yaml
from dbt_charts.core.diagnostics import Diagnostic

_BOARD = """queries:
  q:
    sql: "SELECT 1 AS a, 2 AS b"
{body}
"""


def _errors(body: str) -> list[Diagnostic]:
    return compile(_BOARD.format(body=body)).errors


def _by_path(errors: list[Diagnostic], path: str) -> Diagnostic:
    return next(e for e in errors if e.path == path)


class TestLayoutItemWithoutAType:
    def test_untyped_inline_chart_names_its_path_keys_and_accepted_forms(self) -> None:
        errors = _errors("rows:\n  - cols:\n      - query: q\n        x: a\n")

        error = _by_path(errors, "rows.0.cols.0")
        assert error.code == "ERR-VALIDATION-FIELD"
        assert "Unable to extract tag" not in error.message
        assert "has no `type:` (keys present: query, x)" in error.message
        assert "a chart id" in error.message
        assert "a layout container (rows, cols, grid or tabs)" in error.message
        assert "inline chart whose `type:` is one of: bar," in error.message

    def test_unknown_type_in_a_layout_item_suggests_the_close_type(self) -> None:
        errors = _errors("rows:\n  - cols:\n      - type: barr\n        query: q\n")

        message = _by_path(errors, "rows.0.cols.0").message
        assert "`type: barr`" in message
        assert "Did you mean 'bar'?" in message

    def test_no_error_is_reported_at_the_list_itself(self) -> None:
        errors = _errors("rows:\n  - cols:\n      - query: q\n        x: a\n")

        assert not [e for e in errors if e.path == "rows.0.cols"]

    def test_an_empty_sibling_item_is_skipped_for_the_bad_one(self) -> None:
        errors = _errors(
            "rows:\n  - cols:\n      - {}\n      - query: q\n        x: a\n"
        )

        error = _by_path(errors, "rows.0.cols.1")
        assert "keys present: query, x" in error.message
        assert not [e for e in errors if e.path == "rows.0.cols.0"]

    def test_a_bad_tag_outside_layout_does_not_get_layout_advice(self) -> None:
        errors = compile("queries:\n  q1:\n    type: 5\n    sql: SELECT 1\n").errors

        assert errors
        assert not [e for e in errors if "a layout container" in e.message]


class TestEveryProblemCarriesItsFieldPath:
    def test_board_schema_failure_summary_names_each_field(self) -> None:
        with pytest.raises(ParseError) as excinfo:
            parse_yaml("title: T\nrows: nope\nzzz: 1\n")

        message = str(excinfo.value)
        assert "Field 'rows'" in message
        assert "Unknown field 'zzz'" in message
