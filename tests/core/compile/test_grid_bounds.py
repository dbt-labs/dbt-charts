"""Regression tests: GridLayout.columns and GridItem span fields reject bad grid sizes.

``grid: {columns: 0}`` passed ``dct validate`` and crashed ``dct render`` with
a float division by zero (``core/render/sizing.py`` divides
``available_width / columns``). A negative ``col_span``/``row_span``/``width``/
``height`` also passed ``dct validate``, but rendered broken (negative)
geometry instead of crashing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.board.authored import GridItem, GridLayout


@pytest.mark.parametrize("columns", [0, -1])
def test_grid_layout_columns_must_be_positive(columns: int) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        GridLayout.model_validate({"columns": columns, "items": [{"item": "c1"}]})


@pytest.mark.parametrize("field", ["col_span", "row_span", "width", "height"])
def test_grid_item_span_fields_reject_negative(field: str) -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        GridLayout.model_validate({"items": [{"item": "c1", field: -1}]})


@pytest.mark.parametrize("field", ["col_span", "row_span", "width", "height"])
def test_grid_item_span_fields_still_accept_zero(field: str) -> None:
    """0 predates this fix and must keep validating (see test below for normalization)."""
    item = GridItem.model_validate({"item": "c1", field: 0})
    assert getattr(item, field) == 0


_BOARD_YAML = """title: "grid columns 0"
queries:
  d: {{ columns: [cat, val], values: [[A, 10], [B, 30]] }}
charts:
  c: {{ type: bar, query: d, x: cat, y: val }}
grid:
  {grid_body}
"""


def test_grid_columns_zero_is_a_clean_compile_error_not_a_render_crash() -> None:
    result = compile(
        _BOARD_YAML.format(grid_body="columns: 0\n  items:\n    - item: c")
    )

    assert not result.success
    (error,) = result.errors
    assert error.code == "ERR-VALIDATION-FIELD"
    assert "Field 'grid.columns'" in error.message
    assert "greater than 0" in error.message


def test_grid_item_width_zero_still_normalizes_to_col_span_one() -> None:
    """Locks in the ``ge=0`` decision: 0 must still take the ``col_span or width or 1`` path."""
    result = compile(
        _BOARD_YAML.format(grid_body="items:\n    - item: c\n      width: 0")
    )

    assert result.success, result.errors
    assert result.board is not None
    (item,) = result.board.layout.items
    assert item.col_span == 1
