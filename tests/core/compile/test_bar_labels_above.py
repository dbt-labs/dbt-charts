"""`style.charts.bar.labels_above`: a theme's switch that moves long horizontal bar names above their bars."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
from dbt_charts.core.compile.models.style.theme.bar import BarLabelsAboveStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

ROWS = [{"cat": "a", "v": 1}, {"cat": "b", "v": 2}]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _resolved(
    labels_above: Any = None, theme_labels_above: Any = None
) -> ResolvedBarChart:
    style: dict[str, Any] = {}
    if labels_above is not None:
        style["labels_above"] = labels_above
    chart = BarChart.model_validate(
        {
            "id": "t",
            "type": "bar",
            "query_name": "q",
            "x": "cat",
            "y": "v",
            "style": style,
        }
    )
    theme = get_theme_style("clarity")
    if theme_labels_above is not None:
        bar = theme.charts.bar.model_copy(update={"labels_above": theme_labels_above})
        charts = theme.charts.model_copy(update={"bar": bar})
        theme = theme.model_copy(update={"charts": charts})
    _, ctx = resolve_style_and_context(theme)
    out = resolve(
        chart,
        ROWS,
        chart_style_context=ctx,
        rendered_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert isinstance(out, ResolvedBarChart)
    return out


def test_no_shipped_theme_moves_names_above_bars() -> None:
    assert _resolved().style.labels_above is None


def test_an_authored_share_reaches_the_resolved_style() -> None:
    labels_above = _resolved({"max_share": 0.45}).style.labels_above
    assert labels_above is not None
    assert labels_above.max_share == 0.45


def test_a_theme_share_reaches_the_resolved_style() -> None:
    labels_above = _resolved(
        theme_labels_above=BarLabelsAboveStyle(max_share=0.3)
    ).style.labels_above
    assert labels_above is not None
    assert labels_above.max_share == 0.3


@pytest.mark.parametrize(
    ("labels_above", "needle"),
    [
        ({"max_share": 0}, "greater than 0"),
        ({"max_share": -0.2}, "greater than 0"),
        ({"max_share": 1.5}, "less than or equal to 1"),
        ({"max_share": "wide"}, "labels_above.max_share"),
        ({}, "labels_above.max_share\n  Field required"),
        ({"max_share": 0.45, "min_share": 0.1}, "Extra inputs are not permitted"),
    ],
)
def test_a_malformed_share_is_rejected(
    labels_above: dict[str, Any], needle: str
) -> None:
    with pytest.raises(ValidationError, match=needle):
        _resolved(labels_above)
