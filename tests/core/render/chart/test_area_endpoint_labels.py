"""TDD tests for area chart endpoint labels and halo render order.

These tests must FAIL until:
- AreaStyle gains endpoint_labels: EndpointLabelsConfig
- theme YAML gains area.endpoint_labels section
- _maybe_wrap_endpoint_label_pane handles chart_type == "area"
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


@pytest.fixture
def make_area_resolved_chart(make_chart, model_copy_at):
    """Build a resolved multi-series area chart with endpoint_labels configured."""
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig

    def _build(enabled: bool, chart_width: int = 400):
        chart = make_chart("area", x="date", y="value", color="series")
        data = [
            {"date": "2024-01-01", "value": 100, "series": "Revenue"},
            {"date": "2024-02-01", "value": 120, "series": "Revenue"},
            {"date": "2024-01-01", "value": 80, "series": "Costs"},
            {"date": "2024-02-01", "value": 90, "series": "Costs"},
        ]

        seed = model_copy_at(
            get_theme_style(),
            "charts.area.endpoint_labels",
            EndpointLabelsConfig(visible=enabled, label_offset=6.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)

        resolved = resolve(chart, data, chart_style_context=board_style)
        return resolved, data

    return _build


# ---------------------------------------------------------------------------
# Stack-aware anchors (mirrors test_bar_endpoint_labels.py) — regression for
# stacked-area-endpoint-labels-place-at-raw-y-not-band-midpoints +
# normalize-stacked-top-rule-shipped-but-is-not-visible-root-cause.
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_with_area_endpoint_labels(model_copy_at):
    """Resolved style with area.endpoint_labels override applied, seeded from stark."""

    def _build(enabled: bool, label_offset: float = 5.0):
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig

        seed = model_copy_at(
            get_theme_style("stark"),
            "charts.area.endpoint_labels",
            EndpointLabelsConfig(
                visible=enabled, label_offset=label_offset, height=20.0
            ),
        )
        return resolve_chart_style_context(seed)

    return _build


@pytest.fixture
def resolve_area_chart(make_chart, seed_with_area_endpoint_labels):
    """Resolved area chart with endpoint_labels enabled and optional stack."""

    def _build(
        data: list[dict[str, Any]],
        enabled: bool,
        color: str | None = "series",
        stack: Any = None,
    ):
        kwargs: dict[str, Any] = {}
        if color is not None:
            kwargs["color"] = color
        if stack is not None:
            kwargs["stack"] = stack
        chart = make_chart("area", x="date", y="value", **kwargs)
        board_style = seed_with_area_endpoint_labels(enabled)
        return resolve(chart, data, chart_style_context=board_style)

    return _build


def _render_area(rc, data: list[dict[str, Any]]) -> dict[str, Any]:
    artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
    assert artifact.kind == "vega_spec"
    return artifact.payload


def test_area_negative_values_raise_for_stacked(resolve_area_chart):
    """Negative values in a stacked area would put the cumulative domain across
    both signs and break the midpoint computation — must raise."""
    data = [
        {"date": "2024-01-01", "value": 10, "series": "A"},
        {"date": "2024-01-01", "value": -20, "series": "B"},
    ]
    rc = resolve_area_chart(data=data, enabled=True, stack="zero")
    with pytest.raises(ChartDataError, match="negative"):
        _render_area(rc, data)


def test_wide_area_truncation_names_the_y_key_not_the_fold_column(make_chart):
    """A wide-form area has no `color:` — its rail labels are the `y:` measures.

    Regression: the truncation record carried the fold-generated column name,
    so the warning read "from column '__df_wide_measure_label'" and anchored on
    a `color` key the author never wrote.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
    from dbt_charts.core.render.chart.series_label_truncation import (
        collect_series_label_truncations,
    )
    from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    long_measure = "enterprise_cloud_data_integration_platform_north_america_west"
    rows = [
        {"date": "2024-01-01", long_measure: 100, "costs": 80},
        {"date": "2024-02-01", long_measure: 120, "costs": 90},
    ]
    chart = make_chart("area", x="date", y=[long_measure, "costs"])
    resolved = resolve(chart, rows, chart_style_context=board_ctx)

    with collect_series_label_truncations() as truncations:
        render_resolved_chart(resolved, rows, board_style, width=400)

    records = [r for r in truncations[resolved.id] if r.fate == "truncated"]
    assert [r.authored_field for r in records] == ["y"]
    # Humanized (field_title), matching the rail's own rendered text
    # and the wide chart's y-axis title for the same measure -- not the raw
    # column name.
    assert (
        records[0].series_name
        == "Enterprise cloud data integration platform north america west"
    )
