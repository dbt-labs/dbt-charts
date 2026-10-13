"""A geoshape shading input (`value:` or a data-driven `color:`) without `lookup:`
must fail at resolve; otherwise the base map renders unshaded and looks deliberate.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
from dbt_charts.core.compile.resolve.chart.geo import _resolve_geoshape
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_DATA = [{"state_id": "CA", "population": 39.0}]


def _resolve(**authored: str) -> None:
    chart = GeoshapeChart(
        id="geo1", type="geoshape", geo_source="us-states", **authored
    )
    style = resolve_chart_style_context(get_theme_style())
    _resolve_geoshape(chart, _DATA, style, 800.0, None)


@pytest.mark.parametrize("shading", [{"value": "population"}, {"color": "population"}])
def test_shading_input_without_lookup_rejected(shading: dict[str, str]) -> None:
    with pytest.raises(CompilationError) as exc_info:
        _resolve(**shading)
    assert exc_info.value.code.code == "ERR-GEOSHAPE-SHADING-WITHOUT-LOOKUP"
    assert "lookup" in str(exc_info.value)


def test_shading_input_with_lookup_compiles() -> None:
    _resolve(value="population", lookup="state_id")


def test_base_map_without_shading_or_lookup_compiles() -> None:
    _resolve()
