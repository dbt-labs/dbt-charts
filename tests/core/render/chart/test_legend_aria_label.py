"""Regression: a layered chart's legend must not raw-enumerate every layer's
label in its Vega-generated aria-label. See emitters/_overlay.py's
label_scale_legend_encs gate.
"""

from __future__ import annotations

import json
import re

import vl_convert as vlc

from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.render.chart.emitters.bar import BarEmitter
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.translate import translate_to_vl

from ...test_layers_on_cartesian import (
    _bar_normalized,
    _default_board_style_no_endpoint_labels,
)

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)

_MANY_LAYER_DATA: list[dict] = [
    {"month": "Jan", "revenue": 100.0, "trend": 90.0, "changelog": 5.0},
    {"month": "Feb", "revenue": 200.0, "trend": 180.0, "changelog": 8.0},
]


def _layered_bar_svg(layers: list[LineLayer]) -> str:
    chart = _bar_normalized(y="revenue", layers=layers)
    resolved = resolve(
        chart, _MANY_LAYER_DATA, _default_board_style_no_endpoint_labels()
    )
    vl = translate_to_vl(
        BarEmitter().emit(
            resolved, _DEFAULT_BOX, regroup((), _MANY_LAYER_DATA), datasets=None
        )
    )
    return vlc.vegalite_to_svg(json.dumps(vl))


def _legend_aria_labels(svg: str) -> list[str]:
    return re.findall(r'aria-roledescription="legend" aria-label="([^"]*)"', svg)


def test_layered_chart_legend_aria_label_does_not_raw_enumerate_layer_labels() -> None:
    """The reported bug: Vega's auto legend aria-label joins every layer's
    label verbatim, commas and all -- including a comma already inside a
    label's own parenthetical. The fix must stop that raw join without
    leaving the legend's aria-label empty."""
    svg = _layered_bar_svg(
        [
            LineLayer(
                type="line",
                y="trend",
                label="pooled trend (scheduled releases, excl. 15.1)",
            ),
            LineLayer(
                type="line",
                y="changelog",
                label="trend excluding Aug '26, changelog items",
            ),
        ]
    )

    legend_labels = _legend_aria_labels(svg)
    assert legend_labels, "expected a legend group with its own aria-label"
    for legend_label in legend_labels:
        assert legend_label != ""
        assert "scheduled releases, excl. 15.1" not in legend_label
        assert "changelog items" not in legend_label


def test_short_comma_free_layered_legend_keeps_vegas_own_enumeration() -> None:
    """A short, comma-free layer-label set is not ambiguous when Vega joins
    it -- its own auto aria-label is accurate and more informative than a
    generic replacement, so the override must not fire here."""
    svg = _layered_bar_svg([LineLayer(type="line", y="trend", label="target")])

    legend_labels = _legend_aria_labels(svg)
    assert legend_labels, "expected a legend group with its own aria-label"
    for legend_label in legend_labels:
        assert "Revenue" in legend_label
        assert "target" in legend_label


_DUPLICATE_LABEL_DATA: list[dict] = [
    {"month": "Jan", "revenue": 100.0, "trend_a": 90.0, "trend_b": 80.0},
    {"month": "Feb", "revenue": 200.0, "trend_a": 180.0, "trend_b": 160.0},
]


def test_duplicate_layer_labels_deduplicate_the_stated_count() -> None:
    """Two layers authoring the same `label:` is unremarkable -- it's a
    legitimate palette-slot reuse, not an error -- and Vega's own categorical
    scale collapses the duplicate domain value to one legend entry (each
    series still paints its own mark). The stated count must match the
    rendered legend, not the raw (undeduped) domain list length."""
    layers = [
        LineLayer(type="line", y="trend_a", label="trend (scheduled, excl. 15.1)"),
        LineLayer(type="line", y="trend_b", label="trend (scheduled, excl. 15.1)"),
    ]
    chart = _bar_normalized(y="revenue", layers=layers)
    resolved = resolve(
        chart, _DUPLICATE_LABEL_DATA, _default_board_style_no_endpoint_labels()
    )
    vl = translate_to_vl(
        BarEmitter().emit(
            resolved, _DEFAULT_BOX, regroup((), _DUPLICATE_LABEL_DATA), datasets=None
        )
    )
    svg = vlc.vegalite_to_svg(json.dumps(vl))

    legend_labels = _legend_aria_labels(svg)
    assert legend_labels, "expected a legend group with its own aria-label"
    for legend_label in legend_labels:
        # revenue + the one distinct trend label (deduped), rendered as 2
        # swatches -- not 3, the raw (undeduped) domain list length.
        assert legend_label == "2 series"


_FIELD_COLOR_BASE_DATA: list[dict] = [
    {"month": "Jan", "revenue": 100.0, "trend": 90.0, "kind": "Enterprise"},
    {"month": "Feb", "revenue": 200.0, "trend": 180.0, "kind": "Mid-Market"},
]


def test_field_color_base_plus_layer_label_counts_the_full_rendered_domain() -> None:
    """A nominal/ordinal base `color:` (field_color_base) shares its scale
    with a layer's synthetic `label:` -- the merged legend's rendered domain
    is the base's own distinct field values PLUS the layer label, not just
    the synthetic subset a naive collection would see. The comma check (and
    the description's stated count) must key off the full domain Vega
    actually renders, or the count is a confidently wrong number."""
    layers = [
        LineLayer(
            type="line",
            y="trend",
            label="pooled trend (scheduled, excl. 15.1)",
        )
    ]
    chart = _bar_normalized(y="revenue", color="kind", layers=layers)
    resolved = resolve(
        chart, _FIELD_COLOR_BASE_DATA, _default_board_style_no_endpoint_labels()
    )
    vl = translate_to_vl(
        BarEmitter().emit(
            resolved,
            _DEFAULT_BOX,
            regroup((), _FIELD_COLOR_BASE_DATA),
            datasets=None,
        )
    )
    svg = vlc.vegalite_to_svg(json.dumps(vl))

    legend_labels = _legend_aria_labels(svg)
    assert legend_labels, "expected a legend group with its own aria-label"
    for legend_label in legend_labels:
        assert "scheduled, excl. 15.1" not in legend_label
        # Enterprise + Mid-Market (base's own distinct field values) + the
        # one layer label == 3 -- not 1 (the synthetic-only subset).
        assert legend_label == "3 series"


def test_field_bound_categorical_legend_keeps_vegas_own_enumeration() -> None:
    """A plain field-bound color legend (no `layers:`, so this never reaches
    the overlay code this task changes) is untouched -- a boundary guard
    against the change reappearing in the wrong place (apply_color_legend /
    legend_to_vl), not fix-path coverage."""
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    from .test_legend_layout import _BOARD_CTX, _BOARD_STYLE, _bar_chart

    data = [
        {"month": "Jan", "revenue": 100.0, "category": "A"},
        {"month": "Feb", "revenue": 200.0, "category": "B"},
    ]
    chart = _bar_chart()
    vl = generate_vega_lite_spec(
        chart, data, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    svg = vlc.vegalite_to_svg(json.dumps(vl))

    legend_labels = _legend_aria_labels(svg)
    assert legend_labels, "expected a legend group with its own aria-label"
    for legend_label in legend_labels:
        assert "A" in legend_label
        assert "B" in legend_label
