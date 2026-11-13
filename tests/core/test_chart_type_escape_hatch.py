"""Tests for AuthoredChart and Chart.type enforcement.

With the discriminated union design:
- AuthoredChart is a union alias, not a BaseModel.
- type: is mandatory — missing or unknown type raises ValidationError.
- Known types dispatch to their specific family patch class.
- Chart.type still has strict validation at the compiled level.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.normalized import Chart


def test_chartpatch_accepts_known_type():
    """AuthoredChart dispatches known types to their family patch."""
    from dbt_charts.core.compile.models.chart.authored import (
        AuthoredChart,
        BarChart,
        LineChart,
    )

    adapter = TypeAdapter(AuthoredChart)
    assert isinstance(adapter.validate_python({"type": "bar"}), BarChart)
    assert isinstance(adapter.validate_python({"type": "line"}), LineChart)


def test_compiledchart_rejects_invalid_type_string():
    """Chart.type must reject strings that are neither known types
    nor valid custom type names (uppercase, hyphens, etc.)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TypeAdapter(Chart).validate_python(
            {"id": "x", "type": "My-Chart"}
        )  # uppercase + hyphen

    with pytest.raises(ValidationError):
        TypeAdapter(Chart).validate_python(
            {"id": "x", "type": "custom:funnel"}
        )  # colon not allowed

    with pytest.raises(ValidationError):
        TypeAdapter(Chart).validate_python({"id": "x", "type": "UPPERCASE"})


def test_compiledchart_rejects_unknown_type_name():
    """Chart.type rejects names that are not known built-in chart types."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TypeAdapter(Chart).validate_python({"id": "x", "type": "gauge"})

    with pytest.raises(ValidationError):
        TypeAdapter(Chart).validate_python({"id": "x", "type": "my_custom_chart"})


def test_compiledchart_accepts_known_types():
    """Chart.type accepts known built-in chart types."""
    c = TypeAdapter(Chart).validate_python({"id": "x", "type": "bar"})
    assert c.type == "bar"
    c = TypeAdapter(Chart).validate_python({"id": "x", "type": "line"})
    assert c.type == "line"
    c = TypeAdapter(Chart).validate_python({"id": "x", "type": "table"})
    assert c.type == "table"
