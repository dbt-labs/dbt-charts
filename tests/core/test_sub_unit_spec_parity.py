"""A KPI value with no format prints at most two real decimals, never padded."""

import pytest

from dbt_charts.core.compile.format_value import format_kpi_parts


@pytest.mark.parametrize(
    ("value", "expected"),
    [(35.6, "35.6"), (35.67, "35.67"), (0.5, "0.5"), (35.678, "35.68"), (35, "35")],
)
def test_unformatted_value_prints_at_most_two_real_decimals(
    value: float, expected: str
) -> None:
    _, number, suffix = format_kpi_parts(value, None)
    assert number + suffix == expected
