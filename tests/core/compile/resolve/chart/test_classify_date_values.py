"""classify_column_type reads types from values once the name gate passes."""

from __future__ import annotations

from datetime import date

import pytest

from dbt_charts.core.compile.resolve.chart.enrich import classify_column_type


@pytest.mark.parametrize("name", ["started", "finished", "start_date"])
def test_date_objects_classify_temporal_regardless_of_name(name: str) -> None:
    values = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    assert classify_column_type(name, values) == "temporal"
