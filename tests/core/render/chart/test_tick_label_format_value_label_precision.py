"""Regression: baking a non-compacting axis's ticks to plain digits must not
leak a tick-derived precision onto value labels, which render arbitrary data
values, not ticks.

``ResolvedAxisStyle.tick_label`` is read only by this axis's own tick
emission, never by anything that inherits the resolved measure format
(``label.format``, shared with value labels, tooltips, and the
currency-warning detector) or that treats a resolved ``label.expr`` as an
authored opt-out (the mirror ghost, case injection, align resolution). A
coarse, non-compacting ladder with fractional data proves the boundary: the
axis's own ticks write out in full while a value label on the same chart
still reads its datum at full precision -- ``0.5`` must never render as
``"1"``.
"""

from __future__ import annotations

import pytest

from d3_format import format as d3_format_apply
from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_COARSE_FRACTIONAL_DATA = [
    {"month": "Jan", "revenue": 0.5},
    {"month": "Feb", "revenue": 9.18},
    {"month": "Mar", "revenue": 47.3},
    {"month": "Apr", "revenue": 200},
]


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _board_with_bar_labels():
    compiled = get_theme_style("clarity")
    new_labels = compiled.charts.marks.bar.labels.model_copy(update={"visible": True})
    new_bar = compiled.charts.marks.bar.model_copy(update={"labels": new_labels})
    new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _text_layer_format(spec):
    for lyr in spec.get("layer", []):
        enc = lyr.get("encoding", {})
        if "text" in enc:
            return enc["text"].get("format")
    return None


def test_coarse_ladder_value_labels_keep_precision(make_chart):
    """0-200 never compacts; the theme's SI format is unauthored, so the axis
    bakes tick_label for its own ticks. Value labels must still read
    the fractional data at full SI precision -- 0.5 must never render as 1.

    label.format itself is SI-shaped, so value labels take the narrative
    house register (a calculate transform) instead of a literal text.format —
    the precision guarantee below is checked directly against the resolved
    spec string (still the theme's bounded 3-sig-fig default, not the
    ladder's tick-derived precision), not against the emitted VL spec.
    """
    board_style, chart_style_context = _board_with_bar_labels()
    chart = make_chart("bar", x="month", y="revenue")
    resolved = resolve(chart, _COARSE_FRACTIONAL_DATA, chart_style_context)
    spec = generate_vega_lite_spec(
        chart,
        _COARSE_FRACTIONAL_DATA,
        board_style=board_style,
        chart_style_context=chart_style_context,
    )

    # The axis mechanism engaged -- proves this isn't a no-op due to some
    # other guard silently skipping the bake.
    assert resolved.style.axis_y.tick_label is not None
    assert resolved.style.axis_y.ruler is None

    # label.format itself is untouched -- value labels inherit the theme's
    # bounded (3-sig-fig) SI default, not the ladder's tick-derived precision.
    label_fmt = resolved.style.mark.labels.format
    assert label_fmt.spec == ".3~s"
    assert resolved.style.axis_y.labels.format.spec == ".3~s"
    assert _text_layer_format(spec) is None, (
        "an SI-shaped format must not be handed to Vega verbatim"
    )

    rendered = {
        v["revenue"]: d3_format_apply(label_fmt.spec, v["revenue"])
        for v in _COARSE_FRACTIONAL_DATA
    }
    assert rendered[0.5] == "500m"
    assert rendered[0.5] != "1"
    assert rendered[9.18] == "9.18"
    assert rendered[47.3] == "47.3"
    assert rendered[200] == "200"
