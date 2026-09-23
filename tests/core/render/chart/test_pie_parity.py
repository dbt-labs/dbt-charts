"""V2 pie/donut emitter parity tests — pinning behavior V1 already produces.

Covers:
- Authored labels.template is used (not the default template).
- format() Jinja filter in label templates produces formatted strings.
- Center total layers (joinaggregate value + text label) emitted when chart.total is set.
- No leader-line label layer when style.slice_mark.labels is None (attached-table mode).
- __dbt_label / __dbt_label_lines absent from augmented data when labels is None.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.diagnostics import ERR_RESOLVED_PIE_DATA_MISMATCH
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)
from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
from dbt_charts.core.compile.resolve.chart.label_data import (
    pie_presentation_fingerprint,
)
from dbt_charts.core.render.chart.emitters.pie import PieEmitter
from dbt_charts.core.render.errors import RenderError

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_pie_style() -> Any:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _default_legend():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name())).legend


def _default_charts():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _base() -> dict[str, Any]:
    from dbt_charts.core.compile.models.style.theme import PaddingStyle

    charts = _default_charts()
    return {
        "variable_dependencies": frozenset(),
        # Pie charts always resolve a non-empty palette (_resolve_pie raises
        # ChartDataError otherwise); this file only builds pie fixtures.
        "palette": ("#3164a3",),
        "resolved_channels": {},
        "legend": _default_legend(),
        "background": charts.background,
        "title_style": charts.title,
        "layout_padding": PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0),
    }


@pytest.fixture
def pie_style() -> Any:
    board = _make_pie_style()
    return board.pie.marks.slice


# ── _augment_pie_data label-rendering ─────────────────────────────────────────


def test_augment_pie_data_attaches_finalized_label_lines() -> None:
    from dbt_charts.core.render.chart.emitters.pie import _augment_pie_data

    data = [
        {"segment": "A", "value": 76880},
        {"segment": "B", "value": 47120},
    ]
    rows = _augment_pie_data("value", data, (("62% A", "$77k"), ("38% B", "$47k")))
    assert rows[0]["__dbt_label"] == ["62% A", "$77k"], rows[0]["__dbt_label"]
    assert rows[1]["__dbt_label"] == ["38% B", "$47k"], rows[1]["__dbt_label"]


def test_augment_pie_data_attaches_suppressed_label_facts() -> None:
    from dbt_charts.core.render.chart.emitters.pie import _augment_pie_data

    data = [{"segment": "A", "value": 100}, {"segment": "B", "value": 50}]
    rows = _augment_pie_data("value", data, ((), ()))
    for row in rows:
        assert row["__dbt_label"] is None
        assert row["__dbt_label_lines"] == 0
    # Angle meta must still be present.
    assert "__dbt_pct" in rows[0]
    assert "__dbt_row_idx" in rows[0]


def test_augment_pie_data_rejects_stale_resolved_labels() -> None:
    from dbt_charts.core.diagnostics import ERR_INPUT_INVALID
    from dbt_charts.core.render.chart.emitters.pie import _augment_pie_data
    from dbt_charts.core.render.errors import RenderError

    with pytest.raises(RenderError, match="label rows") as excinfo:
        _augment_pie_data("value", [{"value": 100}], ())

    assert excinfo.value.code == ERR_INPUT_INVALID


# ── PieEmitter.emit — authored labels ─────────────────────────────────────────


def test_pie_emitter_uses_finalized_labels(pie_style: Any) -> None:
    from dbt_charts.core.compile.models.style.resolved import ResolvedPieStyle

    board = _make_pie_style()
    slice_mark = pie_style.model_copy(
        update={
            "labels": pie_style.labels.model_copy(
                update={
                    "template": "{{ percent | format('.0%') }}\n${{ (value / 1000) | format('.0f') }}k"
                }
            )
        }
    )
    rs = ResolvedPieStyle(
        inner_radius=0.0,
        slice_mark=slice_mark,
        tooltip_format="",
        total_style=board.pie.total,
    )
    data = [
        {"segment": "A", "value": 76880},
        {"segment": "B", "value": 47120},
    ]
    chart = ResolvedPieChart(
        id="p",
        chart_type="pie",
        resolution_width=600.0,
        outer_fraction=0.9,
        attached_table_gap=12.0,
        hybrid_heading_gap=6.0,
        presentation_fingerprint=pie_presentation_fingerprint(data),
        slice_label_indices=(0, 1),
        theta="value",
        style=rs,
        dark_companion_stops=("#0e4786",),
        **_base(),
    )
    spec = PieEmitter().emit(chart, _DEFAULT_BOX, regroup((), data))
    # Data rows in the spec must carry formatted labels, not raw numbers.
    assert spec.data is not None
    labels_in_data = [row.get("__dbt_label") for row in spec.data]
    assert [
        "62%",
        "$77k",
    ] in labels_in_data, f"Formatted label missing, got: {labels_in_data}"
    assert [
        "38%",
        "$47k",
    ] in labels_in_data, f"Formatted label missing, got: {labels_in_data}"


def test_pie_emitter_rejects_rows_different_from_resolution_snapshot() -> None:
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve

    board = _make_pie_style()
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        color="segment",
        style={
            "marks": {
                "slice": {
                    "labels": {
                        "template": "{{ segment }}={{ value }}",
                        "where": "{{ value > 5 }}",
                    }
                }
            }
        },
    )
    resolved = resolve(
        chart,
        [{"segment": "A", "value": 90}, {"segment": "B", "value": 10}],
        board,
        width=600.0,
    )

    with pytest.raises(RenderError) as exc_info:
        PieEmitter().emit(
            resolved,
            _DEFAULT_BOX,
            regroup((), [{"segment": "A", "value": 1}, {"segment": "B", "value": 99}]),
        )

    assert exc_info.value.code == ERR_RESOLVED_PIE_DATA_MISMATCH


# ── _resolve_pie — dark-companion label ink ─────────────────────────────────────


def test_resolve_pie_no_color_bakes_dark_companion_of_palette_zero() -> None:
    """No-color pie bakes a single dark-companion stop for palette[0], mirroring
    the per-distinct-value bake the multi-series (color-channel) branch already
    does — so single-color labels can anchor to the wedge instead of the theme's
    static label.font color."""
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.palette import label_ink

    board = _make_pie_style()
    chart = PieChart(id="p", type="pie", theta="value")
    resolved = resolve(
        chart,
        [{"value": 90}, {"value": 10}],
        board,
        width=600.0,
    )

    expected = (label_ink(board.palette[0], board.background),)
    assert resolved.dark_companion_stops == expected
    assert len(resolved.dark_companion_stops) == 1


def test_resolve_pie_rejects_empty_resolved_palette_on_direct_path() -> None:
    """An explicit empty categorical-palette override must raise loudly, not
    silently bake an empty dark_companion_stops (which would leave the arc
    fill / label fill with nothing to index) — same contract the
    attached-table path already enforces, now also checked on the direct
    (no attached table) path this change newly depends on."""
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    board = _make_pie_style()
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        style={"color": {"categorical": {"palette": []}}},
    )
    with pytest.raises(ChartDataError):
        resolve(chart, [{"value": 90}, {"value": 10}], board, width=600.0)


def _slice_label_layer_fill(spec: Any) -> Any:
    """Find the leader-line slice-label text layer and return its fill, if any.

    Distinguished from the other two "text" layers (center-total value/label)
    by its ``align`` mark prop, which is a per-datum VL expr dict only this
    layer sets.
    """
    for layer in spec.layers:
        if layer.mark == "text" and isinstance(layer.mark_props.get("align"), dict):
            return layer.mark_props.get("fill")
    raise AssertionError("slice-label text layer not found in spec.layers")


def test_resolve_pie_no_color_does_not_bake_authored_slice_label_color() -> None:
    """dark_companion_stops always reflects the plain dark companion of
    palette[0] — the authored override is applied later, in the emitter, now
    that the merged SliceLabelsStyle.font.color sentinel is real and doesn't
    need patch-peeking at resolve time."""
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.palette import label_ink

    board = _make_pie_style()
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        style={"marks": {"slice": {"labels": {"font": {"color": "#333333"}}}}},
    )
    resolved = resolve(chart, [{"value": 90}, {"value": 10}], board, width=600.0)

    expected = (label_ink(board.palette[0], board.background),)
    assert resolved.dark_companion_stops == expected


def test_pie_emitter_no_color_honors_chart_local_authored_slice_label_color() -> None:
    """An explicit style.marks.slice.labels.font.color on a no-color pie wins
    over the dark-companion default in the emitted label fill."""
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve

    board = _make_pie_style()
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        style={"marks": {"slice": {"labels": {"font": {"color": "#333333"}}}}},
    )
    resolved = resolve(chart, [{"value": 90}, {"value": 10}], board, width=600.0)
    spec = PieEmitter().emit(
        resolved, _DEFAULT_BOX, regroup((), [{"value": 90}, {"value": 10}])
    )

    assert _slice_label_layer_fill(spec) == "#333333"


def test_pie_emitter_no_color_honors_family_tier_authored_slice_label_color() -> None:
    """The same authored override wins when set at the pie family tier
    (`charts.pie.marks`), not just chart-local — the cascade-managed sentinel
    must resolve identically at every authoring tier."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    theme = get_theme_style(get_default_theme_name())
    labels = resolve_chart_style_context(theme).pie.marks.slice.labels
    new_labels = labels.model_copy(
        update={"font": labels.font.model_copy(update={"color": "#444444"})}
    )
    new_slice = theme.charts.pie.marks.slice.model_copy(update={"labels": new_labels})
    new_marks = theme.charts.pie.marks.model_copy(update={"slice": new_slice})
    new_pie = theme.charts.pie.model_copy(update={"marks": new_marks})
    new_charts = theme.charts.model_copy(update={"pie": new_pie})
    new_theme = theme.model_copy(update={"charts": new_charts})

    board = resolve_chart_style_context(new_theme)
    chart = PieChart(id="p", type="pie", theta="value")
    data = [{"value": 90}, {"value": 10}]
    resolved = resolve(chart, data, board, width=600.0)
    spec = PieEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data))

    assert _slice_label_layer_fill(spec) == "#444444"


def test_pie_emitter_no_color_honors_global_tier_authored_slice_label_color() -> None:
    """The same authored override wins when set at the global `charts.marks`
    tier — the third and outermost cascade tier the field is documented at."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    theme = get_theme_style(get_default_theme_name())
    global_labels = theme.charts.marks.slice.labels
    new_font = global_labels.font.model_copy(update={"color": "#555555"})
    new_labels = global_labels.model_copy(update={"font": new_font})
    new_slice = theme.charts.marks.slice.model_copy(update={"labels": new_labels})
    new_marks = theme.charts.marks.model_copy(update={"slice": new_slice})
    new_charts = theme.charts.model_copy(update={"marks": new_marks})
    new_theme = theme.model_copy(update={"charts": new_charts})

    board = resolve_chart_style_context(new_theme)
    chart = PieChart(id="p", type="pie", theta="value")
    data = [{"value": 90}, {"value": 10}]
    resolved = resolve(chart, data, board, width=600.0)
    spec = PieEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data))

    assert _slice_label_layer_fill(spec) == "#555555"


def test_pie_emitter_no_color_unauthored_still_uses_dark_companion() -> None:
    """No font.color authored at any tier: the label fill still falls back to
    the dark-companion default (regression guard — the sentinel exclude must
    not accidentally suppress the fallback)."""
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve

    board = _make_pie_style()
    chart = PieChart(id="p", type="pie", theta="value")
    data = [{"value": 90}, {"value": 10}]
    resolved = resolve(chart, data, board, width=600.0)
    spec = PieEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data))

    assert _slice_label_layer_fill(spec) == resolved.dark_companion_stops[0]


def test_pie_emitter_color_channel_ignores_authored_slice_label_color() -> None:
    """Multi-series (color-channel) pies never honor font.color — only path
    that ever did was the no-color path, matching pre-existing behavior."""
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve

    board = _make_pie_style()
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        color="segment",
        style={"marks": {"slice": {"labels": {"font": {"color": "#333333"}}}}},
    )
    data = [{"segment": "A", "value": 90}, {"segment": "B", "value": 10}]
    resolved = resolve(chart, data, board, width=600.0)
    spec = PieEmitter().emit(resolved, _DEFAULT_BOX, regroup((), data))

    assert _slice_label_layer_fill(spec) is None


def test_resolved_slice_labels_font_color_stays_none_when_unauthored() -> None:
    """Cascade-managed sentinel regression guard: family/size/weight still
    inherit from charts.font when unauthored, while color does not."""
    board = _make_pie_style()
    labels = board.pie.marks.slice.labels

    assert labels.font.color is None
    assert labels.font.family is not None
    assert labels.font.size is not None
    assert labels.font.weight is not None


# ── PieEmitter.emit — center total layers ─────────────────────────────────────


def test_pie_emitter_total_emits_joinaggregate_layer(pie_style: Any) -> None:
    """chart.total.visible → emitter adds a center-text layer with joinaggregate transform."""
    from dbt_charts.core.compile.models.style.resolved import ResolvedPieStyle

    board = _make_pie_style()
    slice_mark = board.pie.marks.slice
    slice_mark = slice_mark.model_copy(
        update={
            "labels": slice_mark.labels.model_copy(update={"template": "{{ value }}"})
        }
    )
    total_style = board.pie.total.model_copy(
        update={"value": board.pie.total.value.model_copy(update={"format": "$,.0f"})}
    )
    rs = ResolvedPieStyle(
        inner_radius=0.62,
        slice_mark=slice_mark,
        tooltip_format="",
        total_style=total_style,
    )
    data = [{"segment": "A", "value": 100}, {"segment": "B", "value": 50}]
    chart = ResolvedPieChart(
        id="p",
        chart_type="pie",
        resolution_width=600.0,
        outer_fraction=0.9,
        attached_table_gap=12.0,
        hybrid_heading_gap=6.0,
        presentation_fingerprint=pie_presentation_fingerprint(data),
        slice_label_indices=(0, 1),
        theta="value",
        total=ChartTotal(visible=True, label="Total Revenue"),
        style=rs,
        dark_companion_stops=("#0e4786",),
        **_base(),
    )
    spec = PieEmitter().emit(chart, _DEFAULT_BOX, regroup((), data))
    assert spec.mark == "layered"
    # Layer 0 = arc, Layer 1 = total value text, Layer 2 = total label text
    assert len(spec.layers) >= 3, (
        f"Expected arc + 2 total layers, got {len(spec.layers)}"
    )
    # Find the layer with joinaggregate transform
    total_value_layer = None
    for layer in spec.layers[1:]:
        transforms = layer.transforms or []
        if any("joinaggregate" in str(t) for t in transforms):
            total_value_layer = layer
            break
    assert total_value_layer is not None, "No layer with joinaggregate transform found"
    # Must encode __dbt_arc_total as quantitative text
    text_enc = total_value_layer.encoding.get("text", {})
    assert text_enc.get("field") == "__dbt_arc_total"
    assert text_enc.get("type") == "quantitative"
    # Format must be propagated
    assert text_enc.get("format") == "$,.0f"


def test_pie_emitter_total_label_layer_emitted(pie_style: Any) -> None:
    """When total.label is set, a text-value layer is emitted centered at width/2, height/2."""
    from dbt_charts.core.compile.models.style.resolved import ResolvedPieStyle

    board = _make_pie_style()
    slice_mark = board.pie.marks.slice
    slice_mark = slice_mark.model_copy(
        update={
            "labels": slice_mark.labels.model_copy(update={"template": "{{ value }}"})
        }
    )
    rs = ResolvedPieStyle(
        inner_radius=0.62,
        slice_mark=slice_mark,
        tooltip_format="",
        total_style=board.pie.total,
    )
    data = [{"segment": "A", "value": 100}]
    chart = ResolvedPieChart(
        id="p",
        chart_type="pie",
        resolution_width=600.0,
        outer_fraction=0.9,
        attached_table_gap=12.0,
        hybrid_heading_gap=6.0,
        presentation_fingerprint=pie_presentation_fingerprint(data),
        slice_label_indices=(0,),
        theta="value",
        total=ChartTotal(visible=True, label="Total Revenue"),
        style=rs,
        dark_companion_stops=("#0e4786",),
        **_base(),
    )
    spec = PieEmitter().emit(chart, _DEFAULT_BOX, regroup((), data))
    # Find the static text label layer (the "Total Revenue" caption)
    static_label_layer = None
    for layer in spec.layers[1:]:
        text_enc = layer.encoding.get("text", {})
        if text_enc.get("value") == "Total Revenue":
            static_label_layer = layer
            break
    assert static_label_layer is not None, (
        "No layer with text value='Total Revenue' found"
    )
    # Must be centered
    x_enc = static_label_layer.encoding.get("x", {})
    y_enc = static_label_layer.encoding.get("y", {})
    assert "expr" in x_enc.get("value", {}), f"x must be a VL expr, got {x_enc}"
    assert "expr" in y_enc.get("value", {}), f"y must be a VL expr, got {y_enc}"


# ── PieEmitter.emit — no leader labels when style.slice_mark.labels is None ────


def test_pie_emitter_no_leader_labels_when_labels_none() -> None:
    """When style.slice_mark.labels is None, no leader-line label layer is emitted.

    This is the attached-table suppression mechanism used by
    _render_arc_attached_table to hide direct callout labels for the donut
    sub-render.
    """
    from dbt_charts.core.compile.models.style.resolved import ResolvedPieStyle

    board = _make_pie_style()
    slice_mark = board.pie.marks.slice.model_copy(update={"labels": None})
    rs = ResolvedPieStyle(
        inner_radius=0.62,
        slice_mark=slice_mark,
        tooltip_format="",
        total_style=board.pie.total,
    )
    data = [{"segment": "A", "value": 100}, {"segment": "B", "value": 50}]
    chart = ResolvedPieChart(
        id="p",
        chart_type="pie",
        resolution_width=600.0,
        outer_fraction=0.9,
        attached_table_gap=12.0,
        hybrid_heading_gap=6.0,
        presentation_fingerprint=pie_presentation_fingerprint(data),
        slice_label_indices=(),
        theta="value",
        total=ChartTotal(visible=True, label="Total Value"),
        style=rs,
        dark_companion_stops=("#0e4786",),
        **_base(),
    )
    spec = PieEmitter().emit(chart, _DEFAULT_BOX, regroup((), data))
    # Should have arc + total value + total label = 3 layers (NO leader label layer)
    assert len(spec.layers) == 3, (
        f"Expected 3 layers (arc + 2 total), got {len(spec.layers)}: {[layer.mark for layer in spec.layers]}"
    )
    # Suppression is a resolved row fact; no label layer consumes it.
    for row in spec.data or []:
        assert row["__dbt_label"] is None
    # No resolve: scale: color: independent when no label color encoding
    assert spec.resolve is None or "color" not in str(spec.resolve)
