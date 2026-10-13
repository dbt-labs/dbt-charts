"""Characterization: scatter's emitted y-scale zero/domain across the shapes
that matter for the resolve/render zero-anchor split.

Pins the exact VL ``encoding.y.scale`` dict scatter emits for six data/style
shapes. This is the equivalence proof for collapsing the resolver's
zero-anchor decision (``resolve_y_zero``) and the emitter's independent
``ratio > 0.25`` recomputation into a single resolve-time decision: these
values must not change across that collapse.
"""

from __future__ import annotations

from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

# ratio = 6000/33000 ~= 0.18 <= 0.25 -> abstain, anchored at zero (all-positive).
_ABSTAIN_DATA = [
    {"x": "2024-01", "y": 6000.0},
    {"x": "2024-02", "y": 20000.0},
    {"x": "2024-03", "y": 33000.0},
]
# ratio = 88/96 ~= 0.917 > 0.25 -> smart-zero fires zero:false (zoomed axis).
_FAR_RATIO_DATA = [
    {"x": "a", "y": 88},
    {"x": "b", "y": 91},
    {"x": "c", "y": 96},
]
# min < 0 -> resolve_y_zero abstains regardless of ratio; ticks span data
# symmetrically (not zero-anchored, since min < 0).
_CROSSES_ZERO_DATA = [
    {"x": "a", "y": -3},
    {"x": "b", "y": 5},
    {"x": "c", "y": 8},
]


def _spec(
    data: list[dict],
    style: ScatterChartStylePatch | None = None,
    layers: list[dict] | None = None,
) -> dict:
    reset_config()
    payload: dict = {"id": "t", "type": "scatter", "x": "x", "y": "y", "style": style}
    if layers is not None:
        payload["layers"] = layers
    chart = TypeAdapter(Chart).validate_python(payload)
    return generate_vega_lite_spec(
        chart, data, width=400, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )


def test_abstain_all_positive_close_to_zero_anchors_with_no_explicit_zero_key():
    scale = _spec(_ABSTAIN_DATA)["encoding"]["y"]["scale"]
    assert "zero" not in scale
    assert "domainMin" not in scale
    assert scale["domainMax"] > max(row["y"] for row in _ABSTAIN_DATA)


def test_ratio_far_from_zero_sets_explicit_zero_false():
    scale = _spec(_FAR_RATIO_DATA)["encoding"]["y"]["scale"]
    assert scale["zero"] is False
    assert scale["domainMax"] > max(row["y"] for row in _FAR_RATIO_DATA)
    assert scale["domainMin"] < min(row["y"] for row in _FAR_RATIO_DATA)


def test_headroom_zero_sets_zero_false_and_pins_only_the_top_at_the_data_max():
    style = ScatterChartStylePatch.model_validate(
        {"axis_y": {"scale": {"headroom": 0}}}
    )
    spec = _spec(_FAR_RATIO_DATA, style=style)
    assert spec["encoding"]["y"]["scale"] == {
        "zero": False,
        "domainMax": max(row["y"] for row in _FAR_RATIO_DATA),
    }


def test_authored_domain_still_sets_zero_false_alongside_domain():
    style = ScatterChartStylePatch.model_validate(
        {"axis_y": {"scale": {"continuous": {"domain": [50, 150]}}}}
    )
    spec = _spec(_FAR_RATIO_DATA, style=style)
    assert spec["encoding"]["y"]["scale"] == {"domain": [50, 150], "zero": False}


def test_shared_scale_layer_zero_decision_uses_base_series_only():
    """The zero decision reads the base chart's y only; the shared-scale
    domain bounds still widen to include the layer's y2 values."""
    data = [
        {"x": "a", "y": 88, "y2": 50},
        {"x": "b", "y": 91, "y2": 60},
        {"x": "c", "y": 96, "y2": 70},
    ]
    spec = _spec(data, layers=[{"type": "scatter", "y": "y2"}])
    scale = spec["layer"][0]["encoding"]["y"]["scale"]
    assert scale["zero"] is False
    # Widened past the base series' own [88, 96] range to cover y2's [50, 70].
    assert scale["domainMax"] > 96
    assert scale["domainMin"] < 50


def test_y_crossing_zero_leaves_zero_key_absent():
    scale = _spec(_CROSSES_ZERO_DATA)["encoding"]["y"]["scale"]
    assert "zero" not in scale
    assert scale["domainMax"] > max(row["y"] for row in _CROSSES_ZERO_DATA)
    assert scale["domainMin"] < min(row["y"] for row in _CROSSES_ZERO_DATA)


def test_authored_zero_true_with_far_from_zero_data_is_honored_not_clobbered():
    """Regression: an authored zero:true pin must win over the data-only
    ratio heuristic, even when the data itself sits far from zero.

    Before the resolve/render collapse, the emitter recomputed the ratio>0.25
    suppression straight from data, ignoring any authored pin, and clobbered
    this to zero:False -- while domainMax was still baked assuming a
    zero-anchored axis (resolve_y_zero already honored the pin for ticks).
    That mismatch is exactly the bug: the chart emitted zero:False next to a
    domainMax computed for zero:True.
    """
    style = ScatterChartStylePatch.model_validate(
        {"axis_y": {"scale": {"continuous": {"zero": True}}}}
    )
    spec = _spec(_FAR_RATIO_DATA, style=style)
    scale = spec["encoding"]["y"]["scale"]
    assert scale["zero"] is True
    assert scale["domainMax"] > 96
    assert "domainMin" not in scale


def test_numeric_string_y_never_gets_a_zero_key():
    """Regression: a numeric-string y column classifies as nominal
    (``_classify_to_channel_type``/``infer_vega_type_from_data`` both treat
    numeric strings as nominal, matching the oracle), so the zero-anchor bake
    must never fire for it -- ``zero`` is not a valid property of a nominal
    VL scale. Same data as ``_FAR_RATIO_DATA`` but as strings.
    """
    data = [
        {"x": "a", "y": "88"},
        {"x": "b", "y": "91"},
        {"x": "c", "y": "96"},
    ]
    spec = _spec(data)
    assert spec["encoding"]["y"]["type"] == "nominal"
    assert "zero" not in spec["encoding"]["y"].get("scale", {})


def test_authored_zero_false_with_close_to_zero_data_is_honored():
    """Mirror shape: an authored zero:false pin on data close to zero.

    Unlike the zero:true mirror above, this was never broken -- the ratio
    heuristic only overrides when it fires (ratio>0.25), so a close-to-zero
    ratio never touched an explicit False pin either before or after the
    collapse. Pinned here as a regression guard now that the collapse routes
    the whole decision through resolve_y_zero.
    """
    style = ScatterChartStylePatch.model_validate(
        {"axis_y": {"scale": {"continuous": {"zero": False}}}}
    )
    scale = _spec(_ABSTAIN_DATA, style=style)["encoding"]["y"]["scale"]
    assert scale["zero"] is False
    assert scale["domainMax"] > max(row["y"] for row in _ABSTAIN_DATA)
    assert scale["domainMin"] < min(row["y"] for row in _ABSTAIN_DATA)
