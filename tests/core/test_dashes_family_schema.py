"""Tests that `dashes` is not a per-family chart style field.

After the restructure:
- _ChartStyleBase no longer carries dashes → no per-family Patch has a `dashes`
  field; bar/pie/line/area/etc. all reject it via extra_forbidden.
- ChartsStyle.dashes (global cascade source and sole authoring surface) is unchanged.
- ResolvedChartsStyle.dashes and the render gate (chart_has_active_dashes) are
  unchanged — dashes behavior is driven by the board-level field only.

Structural rejection here mirrors the pie-axis-cut precedent: the type
system is honest — if a field has no behavioral effect on a surface, that surface
must not carry it.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.chart.authored import AuthoredChart

_chart_adapter = TypeAdapter(AuthoredChart)

_LINE_BARE: dict = {"type": "line", "x": "month", "y": "revenue", "query": "q"}
_AREA_BARE: dict = {"type": "area", "x": "month", "y": "revenue", "query": "q"}
_BAR_BARE: dict = {"type": "bar", "x": "month", "y": "revenue", "query": "q"}
_PIE_BARE: dict = {"type": "pie", "theta": "value", "color": "cat", "query": "q"}

SAMPLE_DASHES: list[list[int]] = [[4, 4], [8, 8]]


class TestDashesRejectedOnAllFamilies:
    """No chart-local style accepts dashes — all families reject it via
    extra_forbidden.  Dashes are authored only at style.charts.dashes (global).

    The render layer reads only ResolvedChartsStyle.dashes (the board-level
    field); per-chart dashes would be silently ignored, so the schema is honest
    and rejects it structurally.
    """

    def test_bar_style_rejects_dashes(self) -> None:
        with pytest.raises(ValidationError, match="dashes"):
            _chart_adapter.validate_python(
                {**_BAR_BARE, "style": {"dashes": SAMPLE_DASHES}}
            )

    def test_pie_style_rejects_dashes(self) -> None:
        with pytest.raises(ValidationError, match="dashes"):
            _chart_adapter.validate_python(
                {**_PIE_BARE, "style": {"dashes": SAMPLE_DASHES}}
            )

    def test_line_style_rejects_dashes(self) -> None:
        # Line charts do not carry per-chart dashes either — global only.
        with pytest.raises(ValidationError, match="dashes"):
            _chart_adapter.validate_python(
                {**_LINE_BARE, "style": {"dashes": SAMPLE_DASHES}}
            )

    def test_area_style_rejects_dashes(self) -> None:
        # Area charts do not carry per-chart dashes either — global only.
        with pytest.raises(ValidationError, match="dashes"):
            _chart_adapter.validate_python(
                {**_AREA_BARE, "style": {"dashes": SAMPLE_DASHES}}
            )
