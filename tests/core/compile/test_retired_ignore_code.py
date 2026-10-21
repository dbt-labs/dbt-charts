"""A board that suppresses a removed warning code still loads, with one notice."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.authored import AuthoredChart


@pytest.mark.parametrize(
    "code",
    [
        "WARN-LIKELY-CURRENCY-OR-PERCENT-MISSING-FORMATTER",
        "WARN-REDUNDANT-AUTHORED-DEFAULT",
        "WARN-REDUNDANT-AUTHORED-LABEL",
    ],
)
def test_ignoring_a_retired_warning_loads_and_notes_it(code: str) -> None:
    raw = {"type": "bar", "x": "a", "y": "b", "warnings_ignore": [code]}
    with pytest.warns(UserWarning, match="no longer exists"):
        TypeAdapter(AuthoredChart).validate_python(raw)
