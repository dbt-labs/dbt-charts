"""Donut charts share the pie's fixed natural height in aligned rows."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dbt_charts.core.render.layout_sizing import _effective_alignment_target_for_chart


@pytest.mark.parametrize(
    ("chart_type", "expected"),
    [("pie", 200.0), ("donut", 200.0), ("bar", 320.0)],
)
def test_alignment_target_caps_fixed_aspect_pie_types(
    chart_type: str, expected: float
) -> None:
    chart = SimpleNamespace(id="c", type=chart_type)
    render_ctx = SimpleNamespace(natural_heights={("c", 400.0): 200.0})

    target = _effective_alignment_target_for_chart(chart, 400.0, 320.0, render_ctx)

    assert target == expected
