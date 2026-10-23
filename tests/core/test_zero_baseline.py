"""Zero-baseline rule mark for bar / area / line charts.

Vega renders axis guides (grid lines) BELOW marks, so area/bar fills cover the
bold zero baseline produced by the conditional ``gridColor`` encoding. A rule
mark layer at the top of the layer stack renders above all fills and restores
the baseline.

Constraints:
- Scope: bar (vertical and horizontal), area, line, and scatter. Other chart
  families (pie, heatmap, ...) don't emit the rule.
- Only emit when 0 is in the measure-axis domain (no rule for line charts
  whose y-domain is e.g. [75000, 183000], or for charts with explicit
  ``scale.domain`` excluding 0).
- Encoding explicitly overrides shared x/x2/color so the rule renders as one
  full-width horizontal line, not a per-series fragment.
- The rule layer ships its own single-row data so VL emits the rule once
  rather than iterating it per parent data row at identical pixel coords.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


# ── Behavioral: when does the rule appear? ────────────────────────────────────


# ── Horizontal bar: measure guards read axis_y (the cascade slot), never
#    axis_x -- the VL channel (x) and the cascade slot (axis_y) are different
#    things for a horizontal bar. See BaselineFeature._insert_zero_rule /
#    _apply_unity docstrings. ──────────────────────────────────────────────


# ── the domain and the baseline must agree ────────────────────────────────────
# A chart that draws the `datum: 0` rule needs 0 in its domain; a chart whose
# domain excludes 0 must draw no rule. Resolve owns the first half (`_pick_scale`
# mirrors all-negative data onto the same branches as all-positive), and
# `build_zero_rule_if_applicable` owns the second for the cases resolve cannot
# reconcile: bar's rule is unconditional per family, so only the guard stops it
# against an authored `zero: false` or a layer-pinned scale.

# Two series so area emits its endpoint-label hconcat — the shape that renders
# under autosize:pad, where an off-plot mark grows the SVG instead of being
# clipped.
# Two panels whose spans differ by ~9x: a shared floor squashes the narrow one.
_ALL_NEGATIVE_PANELS = [
    {"x": "p", "s": "a", "y": -90},
    {"x": "q", "s": "a", "y": -103},
    {"x": "p", "s": "b", "y": -20},
    {"x": "q", "s": "b", "y": -8},
]


def test_effective_measure_domain_agrees_with_the_emitted_floor():
    """`effective_measure_domain` reports where the axis really renders, and
    its own contract is that every value is one the emitter actually pins.

    It kept an inline copy of the floor decision, so when the emitter stopped
    pinning a literal 0.0 this went on reporting it: on an all-negative
    zero-anchored axis with nothing baked and no rung, it answered
    `lo=0.0, hi=-8.0` — an inverted range, feeding a negative denominator to
    `_series_value_span` in the area-reads-as-stacked detector.
    """
    from dbt_charts.core.render.chart.emitters._cartesian import (
        effective_measure_domain,
    )

    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "line",
            "x": "x",
            "y": "y",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "style": {"axis_y": {"scale": {"continuous": {"zero": True}}}},
            "multiples": {"columns": "s", "scale": "independent"},
        }
    )
    resolved = resolve(chart, _ALL_NEGATIVE_PANELS, chart_style_context=_BOARD_CTX)
    ay = resolved.style.axis_y
    data_lo, data_hi = -103.0, -8.0

    assert ay.domain_min is None, "precondition: nothing baked"
    assert not ay.tick_values, "precondition: no ladder"

    lo, hi = effective_measure_domain(ay, (data_lo, data_hi))

    assert lo <= hi, f"inverted range ({lo}, {hi})"
    assert lo == data_lo, "with no rung the floor is where zero:true auto-fits"


# ── 0-not-in-domain: rule must NOT be emitted ─────────────────────────────────


# ── Encoding correctness ──────────────────────────────────────────────────────


# ── End-to-end: the rule actually renders as a horizontal line ────────────────


# ── Dual-axis (independent y-scale) zero baselines ────────────────────────
#
# A dual-axis overlay resolves `resolve.scale.y: independent`, under which
# BaselineFeature bails out entirely (see its `apply()`). These rules are
# instead inserted by `render_cartesian_overlay` itself, nested one level
# inside the specific `vl_layers[i]` entry that owns the scale they bind to
# — never as a floating top-level sibling, which would get its own
# degenerate [0, 0] scale under VL's independent-scale resolution.


def _dual_axis_bar_line_chart(layer_axis_y: dict) -> dict:
    return {
        "id": "t",
        "type": "bar",
        "x": "x",
        "y": "y",
        "query": SqlQuery(sql="SELECT 1", source="src"),
        "query_name": "q",
        "variable_dependencies": set(),
        "layers": [{"type": "line", "y": "y2", "axis_y": layer_axis_y}],
    }


def test_dual_axis_overlay_empty_domain_list_is_rejected():
    """`domain` must be a `[min, max]` pair, so `domain: []` fails validation
    instead of reaching the emitter."""
    with pytest.raises(ValidationError, match="domain"):
        TypeAdapter(Chart).validate_python(
            _dual_axis_bar_line_chart({"position": "right", "scale": {"domain": []}})
        )
