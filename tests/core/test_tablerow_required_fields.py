"""Behavior tests: TableRow* fields are required (no hardcoded defaults).

Verifies that bare construction raises ValidationError and that values propagate
from the theme pipeline through to the render layer.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.primitives import DividerStyle
from dbt_charts.core.compile.models.style.theme import (
    TableDividerStyle,
    TableRowRolesStyle,
    TableRowRoleStyle,
    TableRowStyle,
)

_DIVIDER = {"visible": True, "color": "#000000", "width": 1.0}


@pytest.mark.parametrize("missing", sorted(_DIVIDER))
def test_divider_style_requires_every_field(missing: str) -> None:
    with pytest.raises(ValidationError):
        DividerStyle.model_validate({k: v for k, v in _DIVIDER.items() if k != missing})


@pytest.mark.parametrize("dasharray", ["", "dashed", "6 -3", "6;3"])
def test_divider_style_rejects_a_malformed_dasharray(dasharray: str) -> None:
    with pytest.raises(ValidationError):
        DividerStyle.model_validate({**_DIVIDER, "dasharray": dasharray})


def test_divider_style_normalizes_a_comma_dasharray() -> None:
    assert (
        DividerStyle.model_validate({**_DIVIDER, "dasharray": "6, 3"}).dasharray
        == "6 3"
    )


def test_divider_style_rejects_zero_width() -> None:
    with pytest.raises(ValidationError):
        DividerStyle.model_validate({**_DIVIDER, "width": 0})


def test_table_divider_style_requires_continuous() -> None:
    with pytest.raises(ValidationError):
        TableDividerStyle.model_validate(_DIVIDER)


def test_compiled_table_row_role_style_requires_divider():
    with pytest.raises(ValidationError):
        TableRowRoleStyle()


def test_compiled_table_row_roles_style_requires_summary():
    with pytest.raises(ValidationError):
        TableRowRolesStyle(total=TableRowRoleStyle(divider=DividerStyle(**_DIVIDER)))


def test_compiled_table_row_roles_style_requires_total():
    with pytest.raises(ValidationError):
        TableRowRolesStyle(summary=TableRowRoleStyle(divider=DividerStyle(**_DIVIDER)))


def test_compiled_table_row_style_requires_height():
    role = TableRowRoleStyle(divider=DividerStyle(**_DIVIDER))
    with pytest.raises(ValidationError):
        TableRowStyle(
            divider=TableDividerStyle(**_DIVIDER, continuous=False),
            roles=TableRowRolesStyle(summary=role, total=role),
        )


def test_theme_pipeline_populates_row_fields():
    """Theme pipeline produces populated TableRowStyle with required fields."""
    reset_config()  # guard against config-cache corruption from other tests
    row = get_theme_style().charts.table.row
    assert isinstance(row, TableRowStyle)
    assert isinstance(row.height, float)
    assert isinstance(row.divider, TableDividerStyle)
    assert isinstance(row.divider.width, float)
    assert isinstance(row.divider.continuous, bool)
    assert isinstance(row.roles, TableRowRolesStyle)
    assert isinstance(row.roles.summary.divider.width, float)
    assert isinstance(row.roles.total.divider.width, float)
