"""TDD tests for endpoint_labels.visible on layered single-series charts.

Regression coverage for: endpoint labels cannot be authored when there is no
color encoding. Before this fix, `EndpointLabelFeature.applies_to()` required
a base-level color channel
in "series" mode — a layered chart with no `color:` (e.g. a bar base +
a line overlay, each single-series) could never reach the endpoint-label
rail, even with `endpoint_labels.visible: true` authored.

The fix treats each layer (the base series plus every overlay layer) as its
own labeled endpoint on the SAME right_pane rail multi-series charts already
use — anchored on the base chart's own y-domain (single shared scale only;
a layer that pins its own axis_y.position is a genuine dual-axis chart and
is refused, see test_dual_axis_layer_raises below).
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored._layer import (
    LineLayer,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _bar_with_layers_data():
    return [
        {"month": "Jan", "annual": 10.0, "cumulative": 10.0},
        {"month": "Feb", "annual": 20.0, "cumulative": 30.0},
        {"month": "Mar", "annual": 5.0, "cumulative": 35.0},
    ]


def _resolve_and_render(chart, data, *, width=400.0, chart_style_context=None):
    resolved = resolve(
        chart, data, chart_style_context=chart_style_context or _BOARD_CTX, width=width
    )
    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE, width=width)
    assert artifact.kind == "vega_spec"
    return artifact.payload


# --------------------------------------------------------------------------
# One layer, no color encoding — the worksheet's motivating case.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Several layers, no color encoding.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Default (not opted in): layers keep the legend, never the rail.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# With a genuine base color-series channel, the existing rail path still
# fires (base series only) — regression guard, unaffected by this fix.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# A layer pinning its own axis_y.position is a genuine dual-axis chart —
# the rail anchors on one shared y-scale, so this must raise, not mislabel.
# --------------------------------------------------------------------------


def test_dual_axis_layer_raises(make_chart):
    from dbt_charts.core.diagnostics.codes_render import (
        ERR_LAYER_AXIS_POSITION_ENDPOINT_LABELS,
    )

    chart = make_chart(
        "bar",
        x="month",
        y="annual",
        layers=[
            LineLayer(
                type="line",
                y="cumulative",
                label="Cumulative",
                axis_y={"position": "right"},
            )
        ],
        style={"endpoint_labels": {"visible": True}},
    )
    with pytest.raises(ChartDataError) as exc_info:
        _resolve_and_render(chart, _bar_with_layers_data())
    err = exc_info.value
    assert err.code is ERR_LAYER_AXIS_POSITION_ENDPOINT_LABELS
    assert "style.endpoint_labels.visible" in str(err)


# --------------------------------------------------------------------------
# Review round 1 regressions: legend suppression, rail ink, collision domain.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Review round 2 regressions: color-channel crash, over-suppressed legend,
# duplicate-label collapse.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Round-3 CRITICAL regression: the collision raise must be scoped to the
# layered endpoint-label rail (where a colliding label silently collapses
# the label-keyed anchors dict) — not fire on every layered chart with no
# rail at all, which is ordinary authoring (bars + a same-measure trend
# line; two layers sharing one label with no endpoint_labels opt-in).
# --------------------------------------------------------------------------
