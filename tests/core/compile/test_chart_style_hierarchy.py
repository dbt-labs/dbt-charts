"""Tests for the chart-family style base-class hierarchy.

Verifies that the extra="forbid" config on chart style classes rejects unknown fields.
Structural field presence/absence is covered by the theme corpus smoke test
(test_marks_namespace.py::test_theme_corpus_smoke) and validated at model load time.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_extra_forbid_on_base_classes():
    """_CartesianChartStyle (via BarChartStyle) rejects unknown fields."""
    from dbt_charts.core.compile.models.style.theme import BarChartStyle

    with pytest.raises(ValidationError):
        BarChartStyle.model_validate({"unknown_field_xyz": 42})
