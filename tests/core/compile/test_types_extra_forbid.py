"""Regression tests: types.py models reject unknown keys via extra="forbid".

Each parametrized model raises ValidationError when constructed with a field
not declared on the class.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.board.authored import (
    GridItem,
    GridLayout,
)
from dbt_charts.core.compile.models.chart.authored import (
    ChartSort,
    ColumnScaleConfig,
    ScaleTargetConfig,
    SparkConfig,
)
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.query.authored import (
    AuthoredSqlQuery,
)
from dbt_charts.core.compile.models.variable.authored import VariableOptions


@pytest.mark.parametrize(
    ("cls", "kwargs"),
    [
        (FormatConfig, {}),
        (SparkConfig, {}),
        (ScaleTargetConfig, {"palette": ["#fff", "#000"]}),
        (ColumnScaleConfig, {}),
        (VariableOptions, {}),
        (AuthoredSqlQuery, {}),
        (ChartSort, {"by": "col"}),
        (GridItem, {"item": "my_chart"}),
        (GridLayout, {}),
    ],
)
def test_extra_field_rejected(cls: type, kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        cls(**kwargs, _bogus_unknown="oops")
