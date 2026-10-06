"""Regression test: AuthoredChart passed programmatically to _resolve_single_item.

normalize/layout.py used to call item.model_dump() with no exclusions, which put
title=None and label=None in the dict even when the author omitted them.
Chart(**chart_dict) raises ValidationError because both compiled fields are
typed str.

Fix: use model_dump(exclude_none=True) so unset fields are absent from the
dict. Omitted display text remains empty after normalization.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.authored import BarChart
from dbt_charts.core.compile.normalize.layout import _resolve_single_item


def test_chartpatch_in_layout_omitted_title_stays_empty() -> None:
    """A BarChart with no title produces a Chart with no title."""
    patch = BarChart(type="bar", x="month", y="revenue")
    # No query, no title — both default to None on BarChart.
    result = _resolve_single_item(
        item=patch,
        charts={},
        query_registry={},
        board_id="test_board",
        depth=0,
        item_id="row0",
        sources={},
        html_policy="none",
    )
    assert result.chart is not None
    assert result.chart.title == ""


def test_chartpatch_in_layout_respects_explicit_title() -> None:
    """An explicit title on BarChart is honored."""
    patch = BarChart(type="bar", x="month", y="revenue", title="My Revenue Chart")
    result = _resolve_single_item(
        item=patch,
        charts={},
        query_registry={},
        board_id="test_board",
        depth=0,
        item_id="row0",
        sources={},
        html_policy="none",
    )
    assert result.chart is not None
    assert result.chart.title == "My Revenue Chart"
