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
from dbt_charts.core.constants.marks import ENDPOINT_LABEL_MAX_WIDTH_FRACTION
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


def _render_and_get_label_pane_width(resolved_chart, data, chart_width=400):
    """Render and return the label pane width from the hconcat spec."""
    artifact = render_resolved_chart(
        resolved_chart, data, _BOARD_STYLE, width=chart_width
    )
    assert artifact.kind == "vega_spec"
    spec = artifact.payload
    assert "hconcat" in spec, "Expected hconcat — endpoint labels enabled"
    label_pane = spec["hconcat"][1]
    return label_pane["width"]


def test_short_labels_yield_narrower_pane_than_long_labels(make_resolved_chart):
    """Shorter series names → smaller label pane than longer series names.

    Distinctive-value pattern: compare two calls with different label lengths,
    assert ordering — no pinned pixel value.
    Uses a wide chart (800px) so both stay under the max-width-fraction cap and
    the comparison reads natural measured widths.
    """
    short_resolved, short_data = make_resolved_chart(["A", "B"])
    long_resolved, long_data = make_resolved_chart(
        ["Operating Income Q3", "Cost of Goods Sold Q3"]
    )

    wide_chart = 800
    short_width = _render_and_get_label_pane_width(
        short_resolved, short_data, wide_chart
    )
    long_width = _render_and_get_label_pane_width(long_resolved, long_data, wide_chart)

    assert short_width < long_width, (
        f"Short labels ({short_width}px) should produce narrower pane than "
        f"long labels ({long_width}px)"
    )


def test_label_width_within_bounds_does_not_raise(make_resolved_chart):
    """Reasonable series names + normal chart width → no ChartDataError."""
    resolved, data = make_resolved_chart(["Revenue", "Costs"])
    # Should not raise — short labels, wide chart
    _render_and_get_label_pane_width(resolved, data, chart_width=400)


def test_label_width_capped_at_configured_fraction_of_chart_width(make_resolved_chart):
    """Very long series names are capped at the configured fraction, not an error.

    Labels that exceed the cap are truncated by Vega via the mark's limit
    property — no ChartDataError is raised.
    """
    very_long_name = "A" * 40  # extremely wide label
    resolved, data = make_resolved_chart([very_long_name, "B"])

    cap = 400 * ENDPOINT_LABEL_MAX_WIDTH_FRACTION
    # Should not raise
    pane_width = _render_and_get_label_pane_width(resolved, data, chart_width=400)
    assert pane_width <= cap + 1, (
        f"label pane ({pane_width}px) should be capped at {cap:.1f}px "
        "for very long series names"
    )


def test_hundred_char_series_name_renders_instead_of_raising(make_resolved_chart):
    """A ~100-char series name ellipsizes rather than overshooting the canvas.

    Regression: the natural rail width for such a name exceeds the whole slot, so
    the uncapped pane drove _correct_concat_overshoot to a negative chart pane and
    raised ERR-CONCAT-OVERSHOOT-NONPOSITIVE.
    """
    resolved, data = make_resolved_chart(["Enterprise Cloud Data Platform " + "x" * 69])

    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE, width=600)
    label_pane = artifact.payload["hconcat"][1]

    cap = 600 * ENDPOINT_LABEL_MAX_WIDTH_FRACTION
    assert label_pane["width"] == pytest.approx(cap)
    # VL ellipsizes at the mark limit, so the limit must match the capped pane.
    assert label_pane["mark"]["limit"] == label_pane["width"]


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
