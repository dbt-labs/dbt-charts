"""TDD tests for dynamic endpoint label width (task: estimate-endpoint-label-width).

These tests must FAIL before the `label_width` field is dropped and replaced with
font-measurement-based width estimation.

Distinctive-value-plus-propagation pattern — no pixel value pinning.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.series_label_truncation import (
    SeriesLabelTruncation,
    collect_series_label_truncations,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


@pytest.fixture
def make_resolved_chart(make_chart, model_copy_at):
    """Build a resolved line chart with endpoint_labels enabled and given series names."""
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig

    def _build(series_names: list[str], chart_width: int = 400):
        chart = make_chart("line", x="date", y="value", color="series")

        data = []
        for name in series_names:
            data += [
                {"date": "2024-01-01", "value": 100, "series": name},
                {"date": "2024-02-01", "value": 120, "series": name},
                {"date": "2024-03-01", "value": 140, "series": name},
            ]

        seed = model_copy_at(
            get_theme_style(),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)

        resolved = resolve(chart, data, chart_style_context=board_style)
        return resolved, data

    return _build


def test_truncating_labels_records_the_full_series_names(make_resolved_chart):
    """The truncation sink names every series the cap cut, with full text."""
    long_name = "Enterprise Cloud Data Platform " + "x" * 69
    resolved, data = make_resolved_chart([long_name, "B"])

    with collect_series_label_truncations() as truncations:
        render_resolved_chart(resolved, data, _BOARD_STYLE, width=600)

    assert truncations[resolved.id] == [
        SeriesLabelTruncation(authored_field="color", series_name=long_name)
    ]


def test_labels_within_the_cap_record_nothing(make_resolved_chart):
    """Series names that fit are not reported as truncated."""
    resolved, data = make_resolved_chart(["Revenue", "Costs"])

    with collect_series_label_truncations() as truncations:
        render_resolved_chart(resolved, data, _BOARD_STYLE, width=600)

    assert truncations == {}
