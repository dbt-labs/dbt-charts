"""validate_yaml reports a board only when compilation succeeded."""

from __future__ import annotations

from dbt_charts.core.validate import validate_yaml


def test_a_board_that_only_compiles_on_a_quoted_copy_has_no_board() -> None:
    result = validate_yaml("title: Largest in 1950: rank\nrows:\n  - text: hi\n")
    assert result["success"] is False
    assert result["has_board"] is False


def test_a_valid_board_has_a_board() -> None:
    result = validate_yaml("title: Fine\nrows:\n  - text: hi\n")
    assert result["success"] is True
    assert result["has_board"] is True
