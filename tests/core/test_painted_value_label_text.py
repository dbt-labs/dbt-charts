"""Pins the text the value-label crowding warning measures.

`painted_span_label_text` restates `_span_label_text_expr`'s ternary in Python;
these cases are the branches that expression takes.
"""

import pytest

from dbt_charts.core.render.chart.features.value_labels import (
    painted_label_text,
    painted_span_label_text,
)


@pytest.mark.parametrize(
    ("value", "format_spec", "is_house", "expected"),
    [
        (4_230_000_000, "$.3s", True, "$4.23bn"),
        (4_230_000_000, "$.3s", False, "$4.23G"),
        (28, "$,.0f", False, "$28"),
        (0.67, ".3~s", True, "0.67"),
        (-0.25, ".3~s", True, "−0.25"),
        (0, ".3~s", True, "0"),
        (1.5e-13, ".3~s", True, "1.5e-13"),
        (0.67, ".3~s", False, "670m"),
    ],
)
def test_painted_label_text(
    value: float, format_spec: str, is_house: bool, expected: str
) -> None:
    assert painted_label_text(value, format_spec, is_house) == expected


@pytest.mark.parametrize(
    ("end", "start", "expected"),
    [
        (128, 100, "+$28"),
        (72, 100, "−$28"),
        (100, 100, "+$0"),
        (128, 0, "$128"),
    ],
)
def test_painted_span_label_text(end: float, start: float, expected: str) -> None:
    assert painted_span_label_text(end, start, "$,.0f", False) == expected
