"""TDD tests for line endpoint rich label pane (D-053).

Tests are written BEFORE implementation. They should FAIL until the feature lands.

Feature: when `style.line.endpoint_labels.visible: true` is authored on a
multi-series line chart, `render_standard_vega_spec` emits an hconcat spec
with the main chart as pane[0] and a label pane as pane[1].

Label format: `{value_formatted} {series_name}`.
Dark companion palette for label text.
`resolve.scale.color: independent` on the outer hconcat.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE = resolve_style(get_theme_style())


def _extract_y_domain(spec: dict, data: list[dict], field: str) -> tuple[float, float]:
    """Read y domain bounds from a VL spec dict; fall back to data range."""
    scale = spec.get("encoding", {}).get("y", {}).get("scale", {})
    if scale.get("domainMin") is not None or scale.get("domainMax") is not None:
        return float(scale["domainMin"]), float(scale["domainMax"])
    if "domain" in scale:
        dom = scale["domain"]
        return float(min(dom)), float(max(dom))
    vals = [float(r[field]) for r in data if field in r]
    return float(min(vals)), float(max(vals))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _line_chart(make_chart, **kwargs):
    return make_chart("line", x="date", y="value", **kwargs)


def _multi_series_data():
    return [
        {"date": "2024-01-01", "value": 100, "series": "Core"},
        {"date": "2024-02-01", "value": 120, "series": "Core"},
        {"date": "2024-03-01", "value": 140, "series": "Core"},
        {"date": "2024-01-01", "value": 200, "series": "Growth"},
        {"date": "2024-02-01", "value": 220, "series": "Growth"},
        {"date": "2024-03-01", "value": 180, "series": "Growth"},
    ]


# ---------------------------------------------------------------------------
# 1. hconcat emitted for multi-series line with endpoint_labels.visible=true
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 2. Label pane structure
# ---------------------------------------------------------------------------


@pytest.fixture
def label_pane(make_chart, model_copy_at):
    """The right-hand label pane of a multi-series line spec with endpoint_labels on."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig

    chart = _line_chart(make_chart, color="series")
    data = _multi_series_data()

    seed = model_copy_at(
        get_theme_style(),
        "charts.line.endpoint_labels",
        EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
    )
    board_style = resolve_chart_style_context(seed)
    resolved_chart = resolve(chart, data, chart_style_context=board_style)
    artifact = render_resolved_chart(resolved_chart, data, _BOARD_STYLE)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload

    return spec["hconcat"][1]


# ---------------------------------------------------------------------------
# 3. Disabled by default — no hconcat
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. Single-series line — no hconcat even if enabled
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5. Main chart pane keeps its original layers — no endpoint-dot injection
# ---------------------------------------------------------------------------


def test_label_pane_min_pixel_gap_at_least_font_size():
    """Min pixel gap between adjacent labels must be >= font size.

    Regression: ``_LABEL_LINE_HEIGHT_MULTIPLIER`` was once 0.7, which let
    14pt labels nudge to 9.8px apart — narrower than the labels themselves,
    so the collision-avoidance algorithm produced overlapping bounding
    rects. The contract is that the multiplier is >= 1.0 so labels'
    bounding rects can never overlap on the y-axis. We assert the *rule*,
    not the literal value, so tuning the multiplier upward stays free.

    A unit test of the pixel<->data conversion formula in isolation cannot
    see whether the *delivered* pixel gap actually meets this floor — see
    ``test_endpoint_label_gap.py``, which measures it from rendered SVG.
    This test only pins the typographic rule the engine constant must satisfy.
    """
    from dbt_charts.core.constants.marks import ENDPOINT_LABEL_LINE_HEIGHT_MULTIPLIER

    multiplier = ENDPOINT_LABEL_LINE_HEIGHT_MULTIPLIER
    assert multiplier >= 1.0, (
        f"line_height_multiplier ({multiplier}) must be >= 1.0 "
        f"or adjacent label bounding rects can overlap"
    )


# ---------------------------------------------------------------------------
# 6b. Wrapper stamps $df_target_height for two-pass height correction
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 7. Theme corpus smoke test
# ---------------------------------------------------------------------------


def test_all_themes_compile_with_endpoint_labels_field(compiled_themes):
    """Every production theme must compile and resolve with the endpoint_labels field."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    for _, style in compiled_themes.items():
        resolve_style(style)  # must not raise


# ---------------------------------------------------------------------------
# 8. Label cascade domain invariant — kept labels must never exceed y_domain bounds
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 9. _extract_y_domain reads domainMin/domainMax (individual keys)
# ---------------------------------------------------------------------------


def test_extract_y_domain_reads_domain_min_max():
    """_extract_y_domain must read domainMin/domainMax when scale.domain is absent.

    When tick values are pinned via nice_tick_values, the cartesian emitter
    writes scale.domainMin and scale.domainMax (not scale.domain as a
    list). If _extract_y_domain ignores them it falls back to raw data
    range, making _label_pane_min_data_gap underestimate y_range and
    space labels too tightly.
    """

    spec = {
        "encoding": {
            "y": {
                "field": "revenue",
                "scale": {"zero": False, "domainMin": 50000.0, "domainMax": 200000.0},
            }
        }
    }
    data = [{"revenue": 97000}, {"revenue": 168000}]
    lo, hi = _extract_y_domain(spec, data, "revenue")
    assert lo == 50000.0, f"expected domainMin=50000.0, got {lo}"
    assert hi == 200000.0, f"expected domainMax=200000.0, got {hi}"


def test_extract_y_domain_falls_back_to_data_when_no_scale():
    """_extract_y_domain falls back to data range when scale carries no bounds."""

    spec = {"encoding": {"y": {"field": "revenue"}}}
    data = [{"revenue": 97000}, {"revenue": 168000}]
    lo, hi = _extract_y_domain(spec, data, "revenue")
    assert lo == 97000.0
    assert hi == 168000.0
