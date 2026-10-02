"""TDD tests: style.marks.<mark>.labels value-label layer injection."""

from __future__ import annotations

import re
from typing import Any

import pytest

from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.models.style.theme import FontStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.features.value_labels import _house_register_expr
from dbt_charts.core.render.chart.vega_lite import (
    generate_vega_lite_spec,
    render_chart,
)
from dbt_charts.core.render.numeral_expr import numeral_vega_expr

from ...conftest import chart_pane

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())

SAMPLE_DATA = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _has_text_layer(spec):
    """Return True if spec has a text mark layer anywhere in its layer list."""
    for lyr in spec.get("layer", []):
        m = lyr.get("mark", {})
        if isinstance(m, dict) and m.get("type") == "text":
            return True
        if isinstance(m, str) and m == "text":
            return True
    return False


def _board_with_bar_labels_visible(visible: bool, position=None):
    compiled = get_theme_style("clarity")
    new_labels = compiled.charts.marks.bar.labels.model_copy(
        update={"visible": visible}
    )
    if position is not None:
        new_labels = new_labels.model_copy(update={"position": position})
    new_bar = compiled.charts.marks.bar.model_copy(update={"labels": new_labels})
    new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _board_with_line_labels_visible(visible: bool):
    compiled = get_theme_style("clarity")
    new_labels = compiled.charts.marks.line.labels.model_copy(
        update={"visible": visible}
    )
    new_line = compiled.charts.marks.line.model_copy(update={"labels": new_labels})
    new_marks = compiled.charts.marks.model_copy(update={"line": new_line})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _board_with_point_labels_visible(visible: bool):
    compiled = get_theme_style("clarity")
    new_labels = compiled.charts.marks.point.labels.model_copy(
        update={"visible": visible}
    )
    new_point = compiled.charts.marks.point.model_copy(update={"labels": new_labels})
    new_marks = compiled.charts.marks.model_copy(update={"point": new_point})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


class TestBarValueLabels:
    def test_labels_visible_true_emits_text_layer(self, make_chart):
        board_rs, board_ctx = _board_with_bar_labels_visible(True)
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert _has_text_layer(spec), (
            f"Expected text layer in spec layers, got: {spec.get('layer')}"
        )

    def test_labels_visible_false_no_text_layer(self, make_chart):
        board_rs, board_ctx = _board_with_bar_labels_visible(False)
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert not _has_text_layer(spec), "Expected no text layer when visible=False"

    def test_default_no_text_layer(self, make_chart):
        """Default theme has labels.visible=False — no text layer."""
        compiled = get_theme_style("clarity")
        board_rs, board_ctx = resolve_style_and_context(compiled)
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert not _has_text_layer(spec), "Default theme must not emit text layer"

    def test_bar_labels_field_override_sources_named_column(self, make_chart):
        """labels.field draws text from a named column instead of the y-field, so
        the axis keeps the real y unit while the label shows a pre-scaled value.

        The values (hundreds of millions) and the unauthored theme format
        (.3~s) route the label through the narrative house register, so the
        text channel reads a computed field — the underlying calculate
        transform, not the text channel's own field, is what must reference
        revenue_millions.
        """
        compiled = get_theme_style("clarity")
        new_labels = compiled.charts.marks.bar.labels.model_copy(
            update={"visible": True, "field": "revenue_millions"}
        )
        new_bar = compiled.charts.marks.bar.model_copy(update={"labels": new_labels})
        new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
        charts = compiled.charts.model_copy(update={"marks": new_marks})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        data = [
            {"month": "Jan", "revenue": 100_000_000, "revenue_millions": 100},
            {"month": "Feb", "revenue": 200_000_000, "revenue_millions": 200},
        ]
        chart = make_chart("bar", x="month", y="revenue")
        spec = generate_vega_lite_spec(
            chart, data, board_style=board_rs, chart_style_context=board_ctx
        )
        text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers, "expected a text layer"
        label_enc = text_layers[0]["encoding"]
        text_field = label_enc["text"]["field"]
        assert text_field == "__value_label_text", f"got {label_enc['text']}"
        calc = next(
            (t for t in spec.get("transform", []) if t.get("as") == text_field), None
        )
        assert calc is not None, (
            f"expected a calculate transform producing {text_field}: "
            f"{spec.get('transform')}"
        )
        assert "revenue_millions" in calc["calculate"], (
            f"label text should read the override column, got {calc['calculate']}"
        )
        # Core invariant: labels.field changes ONLY the text channel. The label's
        # measure position channel still binds the y-field (lands on x or y
        # depending on resolved orientation — horizontal bars use x, vertical use y).
        position_fields = {
            enc.get("field")
            for enc in [label_enc.get("x"), label_enc.get("y")]
            if isinstance(enc, dict)
        }
        assert "revenue" in position_fields, (
            f"label position must stay on the y-field, got {label_enc}"
        )

    def test_bar_labels_field_override_v2_renderer(self):
        """The BoardRenderSession emission path honors labels.field too: the
        narrative-register calculate transform reads the override column, so
        the y-field value isn't silently shown instead."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.session import BoardRenderSession

        compiled = get_theme_style("clarity")
        new_labels = compiled.charts.marks.bar.labels.model_copy(
            update={"visible": True, "field": "revenue_millions"}
        )
        new_bar = compiled.charts.marks.bar.model_copy(update={"labels": new_labels})
        new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(
                update={
                    "charts": compiled.charts.model_copy(update={"marks": new_marks})
                }
            )
        )
        data = [
            {"month": "Jan", "revenue": 100_000_000, "revenue_millions": 100},
            {"month": "Feb", "revenue": 200_000_000, "revenue_millions": 200},
        ]
        registry: dict[str, Any] = {"q": SqlQuery(sql="SELECT 1", source="test")}
        compiled_chart = normalize_chart(
            "v2chart",
            {"type": "bar", "x": "month", "y": "revenue", "query": "q"},
            registry,
            sources={},
        )
        resolved = resolve(compiled_chart, data, chart_style_context=board_ctx)
        session = BoardRenderSession.create(board_rs)
        spec = session.finalize_vl(
            session.emit_chart(resolved, _DEFAULT_BOX, {resolved.query_name: data})
        )
        text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers, "expected a v2 text layer"
        text_field = text_layers[0]["encoding"]["text"]["field"]
        assert text_field == "__value_label_text", (
            f"got {text_layers[0]['encoding']['text']}"
        )
        calc = next(
            (t for t in spec.get("transform", []) if t.get("as") == text_field), None
        )
        assert calc is not None, (
            f"expected a calculate transform producing {text_field}"
        )
        assert "revenue_millions" in calc["calculate"], (
            f"v2 label text should read the override column, got {calc['calculate']}"
        )

    def test_bar_labels_field_unknown_column_raises(self, make_chart):
        """A labels.field naming a column absent from the data fails fast (no
        silent blank labels) — same contract as channel-column validation."""
        compiled = get_theme_style("clarity")
        new_labels = compiled.charts.marks.bar.labels.model_copy(
            update={"visible": True, "field": "does_not_exist"}
        )
        new_bar = compiled.charts.marks.bar.model_copy(update={"labels": new_labels})
        new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
        charts = compiled.charts.model_copy(update={"marks": new_marks})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = make_chart("bar", x="month", y="revenue")
        with pytest.raises(
            ChartDataError,
            match=r"labels\.field 'does_not_exist' on the chart names a column not present",
        ):
            generate_vega_lite_spec(
                chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
            )

    def test_area_labels_field_unknown_column_raises(self, make_chart):
        """v2 area value-labels route through the line text layer and honor
        labels.field, so a bad area labels.field must fail fast too — validation
        is scoped to include area, not just bar/line/scatter.

        Area's line_mark carries value-label style ONLY (ResolvedAreaLineStyle —
        area's own top-edge stroke lives on area_mark) and is scoped under
        charts.area.marks.line, not the standalone line chart's charts.line.
        """
        compiled = get_theme_style("clarity")
        area_chart = compiled.charts.area
        new_labels = area_chart.marks.line.labels.model_copy(
            update={"visible": True, "field": "does_not_exist"}
        )
        new_line = area_chart.marks.line.model_copy(update={"labels": new_labels})
        new_marks = area_chart.marks.model_copy(update={"line": new_line})
        new_area_chart = area_chart.model_copy(update={"marks": new_marks})
        charts = compiled.charts.model_copy(update={"area": new_area_chart})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = make_chart("area", x="month", y="revenue")
        with pytest.raises(
            ChartDataError,
            match=r"labels\.field 'does_not_exist' on the chart names a column not present",
        ):
            generate_vega_lite_spec(
                chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
            )

    def test_bar_above_position_vertical(self, make_chart):
        # Explicit vertical orientation: "above" means outside above the bar.
        board_rs, board_ctx = _board_with_bar_labels_visible(True, position="above")
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers, "Expected a text layer"
        mark = text_layers[0]["mark"]
        assert mark.get("baseline") == "bottom", (
            f"Expected baseline=bottom for vertical above, got: {mark}"
        )
        assert mark.get("dy") == -4, f"Expected dy=-4 for vertical above, got: {mark}"

    def test_bar_middle_position_horizontal(self, make_chart):
        # x="month" is categorical → horizontal bar; middle midpoint goes on x channel.
        board_rs, board_ctx = _board_with_bar_labels_visible(True, position="middle")
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers
        lyr = text_layers[0]
        mark = lyr["mark"]
        assert mark.get("baseline") == "middle", (
            f"Expected baseline=middle, got: {mark}"
        )
        # Midpoint transform must put the calculated value on x (not y) for horizontal bars.
        assert "x" in lyr.get("encoding", {}), (
            f"Expected x channel in middle text layer, got: {lyr}"
        )
        assert "y" not in lyr.get("encoding", {}), (
            f"y channel must not be overridden for horizontal middle, got: {lyr}"
        )

    def test_bar_middle_position_vertical(self, make_chart):
        # Explicit vertical orientation: middle midpoint goes on y channel.
        board_rs, board_ctx = _board_with_bar_labels_visible(True, position="middle")
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers
        lyr = text_layers[0]
        assert "y" in lyr.get("encoding", {}), (
            f"Expected y channel in vertical middle text layer, got: {lyr}"
        )
        assert "x" not in lyr.get("encoding", {}), (
            f"x channel must not be overridden for vertical middle, got: {lyr}"
        )

    def _text_layers(self, spec):
        return [
            lyr
            for lyr in chart_pane(spec).get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]

    def test_bar_bottom_position_has_background_color(self, make_chart):
        """position=bottom sits inside the bar fill at the baseline — must use background color."""
        board_rs, board_ctx = _board_with_bar_labels_visible(True, position="bottom")
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        layers = self._text_layers(spec)
        assert layers, "Expected a text layer"
        lyr = layers[0]
        expected_bg = resolved.canvas
        assert lyr["encoding"].get("color") == {"value": expected_bg}, (
            f"bottom must pin the canvas color {expected_bg!r}: {lyr['encoding']}"
        )
        # V2 hoists sub-layer transforms to the outer spec to preserve y.sort.
        transforms = spec.get("transform", [])
        assert any(
            "calculate" in t and t.get("as") == "__bar_bottom" for t in transforms
        ), f"bottom must add a __bar_bottom=0 transform; got: {transforms}"

    def test_bar_bottom_horizontal_anchors_at_x_baseline(self, make_chart):
        """Horizontal bar position=bottom anchors at x=0 (left/start edge of the bar)."""
        board_rs, board_ctx = _board_with_bar_labels_visible(True, position="bottom")
        chart = make_chart("bar", x="month", y="revenue")  # horizontal (x=categorical)
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        layers = self._text_layers(spec)
        assert layers, "Expected a text layer"
        lyr = layers[0]
        # V2 hoists sub-layer transforms to the outer spec to preserve y.sort.
        transforms = spec.get("transform", [])
        assert any(
            "calculate" in t and t.get("as") == "__bar_bottom" for t in transforms
        ), f"Horizontal bottom must add __bar_bottom=0 transform; got: {transforms}"
        enc = lyr.get("encoding", {})
        assert enc.get("x", {}).get("field") == "__bar_bottom", (
            f"x encoding must use __bar_bottom field for horizontal bar: {enc}"
        )

    def test_bar_middle_aligned_uses_joinaggregate(self, make_chart):
        """position=middle_aligned emits joinaggregate mean/2, not per-bar datum/2."""
        board_rs, board_ctx = _board_with_bar_labels_visible(
            True, position="middle_aligned"
        )
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        layers = self._text_layers(spec)
        assert layers, "Expected a text layer for middle_aligned"
        lyr = layers[0]
        # V2 hoists sub-layer transforms to the outer spec to preserve y.sort.
        transforms = spec.get("transform", [])
        assert any("joinaggregate" in t for t in transforms), (
            f"middle_aligned must include a joinaggregate transform; got: {transforms}"
        )
        mid_field = "__value_label_mid_aligned"
        enc = lyr.get("encoding", {})
        assert enc.get("y", {}).get("field") == mid_field, (
            f"middle_aligned text layer y must use '{mid_field}': {enc}"
        )
        assert enc.get("color") == {"value": resolved.canvas}, (
            f"middle_aligned is inside bar fill, must pin the canvas color: {enc}"
        )

    def test_stacked_bar_label_pins_constant_color(self, make_chart):
        """Regression: stacked value labels must pin a constant color encoding.

        Without it, the text layer inherits the outer nominal color encoding and
        VL paints each label in its own segment's fill — invisible. The layer
        must carry ``color: {value: <background>}`` so the number reads against
        the fill, and still anchor at the cumulative segment end (stack: zero).
        """
        stacked_data = [
            {"month": "Jan", "channel": "Web", "revenue": 100},
            {"month": "Jan", "channel": "Mobile", "revenue": 60},
            {"month": "Feb", "channel": "Web", "revenue": 120},
            {"month": "Feb", "channel": "Mobile", "revenue": 80},
        ]
        board_rs, board_ctx = _board_with_bar_labels_visible(True, position="top")
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            style={"bar": {"orientation": "vertical"}},
        )
        resolved = resolve(chart, stacked_data, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, stacked_data, board_style=board_rs, chart_style_context=board_ctx
        )
        layers = self._text_layers(spec)
        assert layers, "Expected a text layer on a stacked bar with labels visible"
        enc = layers[0].get("encoding", {})
        assert enc.get("color") == {"value": resolved.canvas}, (
            "stacked label must pin a constant color so it does not inherit the "
            f"series-color field: {enc.get('color')!r}"
        )
        assert enc.get("y", {}).get("stack") == "zero", (
            f"stacked label must anchor at the segment end (stack: zero): {enc}"
        )


class TestLineValueLabels:
    def test_labels_visible_true_emits_text_layer(self, make_chart):
        board_rs, board_ctx = _board_with_line_labels_visible(True)
        chart = make_chart("line", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert _has_text_layer(spec), (
            f"Expected text layer for line chart, got: {spec.get('layer')}"
        )

    def test_labels_visible_false_no_text_layer(self, make_chart):
        board_rs, board_ctx = _board_with_line_labels_visible(False)
        chart = make_chart("line", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert not _has_text_layer(spec)


class TestPointValueLabels:
    def test_labels_visible_true_emits_text_layer(self, make_chart):
        board_rs, board_ctx = _board_with_point_labels_visible(True)
        chart = make_chart("scatter", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert _has_text_layer(spec), (
            f"Expected text layer for scatter chart, got: {spec.get('layer')}"
        )

    def test_labels_visible_false_no_text_layer(self, make_chart):
        board_rs, board_ctx = _board_with_point_labels_visible(False)
        chart = make_chart("scatter", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert not _has_text_layer(spec)


# =============================================================================
# New comprehensive tests (TDD: written before implementation)
# =============================================================================


def _board_with_bar_labels(updates: dict[str, object]):
    """Build a resolved style with bar labels configured."""
    compiled = get_theme_style("clarity")
    new_labels = compiled.charts.marks.bar.labels.model_copy(
        update={"visible": True, **updates}
    )
    new_bar = compiled.charts.marks.bar.model_copy(update={"labels": new_labels})
    new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _board_with_point_labels(updates: dict[str, object]):
    """Build a resolved style with point labels configured."""
    compiled = get_theme_style("clarity")
    new_labels = compiled.charts.marks.point.labels.model_copy(
        update={"visible": True, **updates}
    )
    new_point = compiled.charts.marks.point.model_copy(update={"labels": new_labels})
    new_marks = compiled.charts.marks.model_copy(update={"point": new_point})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _get_text_layer(spec):
    """Return the first text mark layer dict, or None."""
    for lyr in spec.get("layer", []):
        m = lyr.get("mark", {})
        if isinstance(m, dict) and m.get("type") == "text":
            return lyr
    return None


class TestBarPositionMapping:
    """Horizontal bar (x="month", categorical → default horizontal orientation)."""

    def test_above_position_horizontal(self, make_chart):
        board_rs, board_ctx = _board_with_bar_labels({"position": "above"})
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        mark = lyr["mark"]
        assert mark.get("align") == "left"
        assert mark.get("dx") == 4

    def test_top_position_horizontal(self, make_chart):
        board_rs, board_ctx = _board_with_bar_labels({"position": "top"})
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        mark = lyr["mark"]
        assert mark.get("align") == "right"
        assert mark.get("dx") == -4

    def test_middle_position_horizontal(self, make_chart):
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        mark = lyr["mark"]
        assert mark.get("baseline") == "middle"
        # Horizontal middle: midpoint on x channel
        assert "x" in lyr["encoding"]
        assert "y" not in lyr["encoding"]

    def test_above_position_vertical(self, make_chart):
        board_rs, board_ctx = _board_with_bar_labels({"position": "above"})
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        mark = lyr["mark"]
        assert mark.get("baseline") == "bottom"
        assert mark.get("dy") == -4

    def test_top_position_vertical(self, make_chart):
        board_rs, board_ctx = _board_with_bar_labels({"position": "top"})
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        mark = lyr["mark"]
        assert mark.get("baseline") == "top"
        assert mark.get("dy") == 4

    def test_middle_position_vertical(self, make_chart):
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        # Vertical middle: midpoint on y channel
        assert "y" in lyr["encoding"]
        assert "x" not in lyr["encoding"]


class TestPointPositionMapping:
    def test_top_position(self, make_chart):
        board_rs, board_ctx = _board_with_point_labels({"position": "top"})
        chart = make_chart("scatter", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        mark = lyr["mark"]
        assert mark.get("baseline") == "bottom"
        assert mark.get("dy") == -4

    def test_bottom_position(self, make_chart):
        board_rs, board_ctx = _board_with_point_labels({"position": "bottom"})
        chart = make_chart("scatter", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        mark = lyr["mark"]
        assert mark.get("baseline") == "top"
        assert mark.get("dy") == 4

    def test_left_position(self, make_chart):
        board_rs, board_ctx = _board_with_point_labels({"position": "left"})
        chart = make_chart("scatter", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        mark = lyr["mark"]
        assert mark.get("align") == "right"
        assert mark.get("dx") == -4

    def test_right_position(self, make_chart):
        board_rs, board_ctx = _board_with_point_labels({"position": "right"})
        chart = make_chart("scatter", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        mark = lyr["mark"]
        assert mark.get("align") == "left"
        assert mark.get("dx") == 4

    def test_middle_position(self, make_chart):
        board_rs, board_ctx = _board_with_point_labels({"position": "middle"})
        chart = make_chart("scatter", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        mark = lyr["mark"]
        assert mark.get("baseline") == "middle"
        assert mark.get("align") == "center"


class TestDxDyOverride:
    def test_dx_override(self, make_chart):
        board_rs, board_ctx = _board_with_point_labels({"position": "left", "dx": 10})
        chart = make_chart("scatter", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["mark"].get("dx") == 10


class TestFormatExplicit:
    def test_explicit_format(self, make_chart):
        board_rs, board_ctx = _board_with_bar_labels({"format": ",.0f"})
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["encoding"]["text"].get("format") == ",.0f"

    def test_explicit_format_resolves_a_theme_alias(self, make_chart):
        """An explicit labels.format must resolve through the theme's alias
        table like every other format slot — value_labels.py hands the
        resolved spec to Vega verbatim, so an unresolved alias key
        ("currency_full") would compile clean and ship the literal word to
        Vega's d3 instead of "$,.2f"."""
        board_rs, board_ctx = _board_with_bar_labels({"format": "currency_full"})
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["encoding"]["text"].get("format") == "$,.2f"

    def test_explicit_affix_composes_a_calculate_transform(self, make_chart):
        """A FormatConfig prefix on labels.format has nowhere to ride inside
        VL's native `text.format` (a plain d3 spec string, per
        `_house_register_text_encoding`'s own docstring) -- it must instead
        compose a `calculate` transform (sign, prefix, digits, suffix), the
        same pattern the axis's `compose_axis_format` uses for its
        `labelExpr`."""
        from dbt_charts.core.compile.models.primitives import FormatConfig

        board_rs, board_ctx = _board_with_bar_labels(
            {"format": FormatConfig(spec=",.0f", prefix="EUR ")}
        )
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        text_field = lyr["encoding"]["text"]["field"]
        calc = next(
            t["calculate"]
            for t in spec.get("transform", [])
            if t.get("as") == text_field
        )
        assert "EUR" in calc, calc


class TestFormatNullInherit:
    def test_null_format_inherits_axis_quantitative(self, make_chart):
        """When labels.format is None, it inherits from axis_quantitative.format (not axis_y).

        axis_y.format is None in all shipped themes; the measure format lives on
        axis_quantitative. Here it is set to a literal d3 spec ("~s", not a
        predefined name) at the theme tier -- #7391's settled rule (name-vs-
        literal, never which tier wrote it) means a literal is honored
        verbatim: the label's text.format carries it straight to Vega, with
        no narrative-register calculate transform. See
        test_null_format_inherits_real_editorial_theme for the predefined-name
        sibling, which does take the house register.
        """
        compiled = get_theme_style("clarity")
        # Set axis_quantitative format explicitly (mirrors how shipped themes work)
        new_aq_labels = compiled.charts.axis_quantitative.labels.model_copy(
            update={"format": "~s"}
        )
        new_aq = compiled.charts.axis_quantitative.model_copy(
            update={"labels": new_aq_labels}
        )
        new_charts = compiled.charts.model_copy(update={"axis_quantitative": new_aq})
        # Set labels visible with format=None (the default sentinel)
        new_labels = compiled.charts.marks.bar.labels.model_copy(
            update={"visible": True, "format": None}
        )
        new_bar = compiled.charts.marks.bar.model_copy(update={"labels": new_labels})
        new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
        new_charts = new_charts.model_copy(update={"marks": new_marks})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": new_charts})
        )
        chart = make_chart("bar", x="month", y="revenue")
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        assert resolved.style.mark.labels.format.spec == "~s"
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        text_field = lyr["encoding"]["text"]["field"]
        assert lyr["encoding"]["text"].get("format") == "~s", (
            "a literal d3 spec must be handed to Vega verbatim, not wrapped in "
            "the house narrative register"
        )
        calc = next(
            (t for t in spec.get("transform", []) if t.get("as") == text_field), None
        )
        assert calc is None

    def test_null_format_inherits_real_editorial_theme(self, make_chart):
        """With the real editorial theme, labels.format=None takes the resolved
        axis_y format's narrative house register.

        V2 merges axis_quantitative.format into axis_y during resolution, so
        the label's calculate transform must be built from the resolved
        axis_y format even when no explicit format was authored.
        """
        board_rs, board_ctx = _board_with_bar_labels(
            {"visible": True}
        )  # format stays None
        chart = make_chart("bar", x="month", y="revenue")
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        assert resolved.style.axis_y.labels.format.spec is not None, (
            "V2 precondition: axis_y.labels.format.spec must be resolved (non-null) in editorial theme"
        )
        assert (
            resolved.style.mark.labels.format.spec
            == resolved.style.axis_y.labels.format.spec
        )
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        text_field = lyr["encoding"]["text"]["field"]
        expected = _house_register_expr(
            "datum['revenue']", resolved.style.axis_y.labels.format
        )
        calc = next(
            (t for t in spec.get("transform", []) if t.get("as") == text_field), None
        )
        assert calc is not None and calc["calculate"] == expected, (
            f"label's calculate transform should be built from the resolved "
            f"axis_y.labels.format ({resolved.style.axis_y.labels.format!r}): {calc}"
        )


class TestFormatTracksEffectiveAxis:
    """Value labels follow the EFFECTIVE measure-axis format, including per-chart
    overrides — not just the board-time axis_quantitative.format.

    Regression: a per-chart format (chart.format / style.number_format /
    style.axis_y.format) lands on the axis only, so labels stayed on the stale
    board value — clean ~s axis beside raw-float / mismatched labels.
    """

    def _label_fmt(self, make_chart, chart_kwargs):
        # editorial: axis_quantitative.format references the number alias (.3~s)
        board_rs, board_ctx = _board_with_bar_labels({})
        chart = make_chart("bar", x="month", y="revenue", **chart_kwargs)
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        return (
            lyr["encoding"]["text"].get("format"),
            resolved.style.axis_y.labels.format.spec,
        )

    def test_chart_format_flows_to_label(self, make_chart):
        label_fmt, axis_fmt = self._label_fmt(make_chart, {"format": ",.0f"})
        assert axis_fmt == ",.0f"
        assert label_fmt == ",.0f", (
            f"label should track the per-chart axis format ({axis_fmt!r}), "
            f"not the stale board inherit — got {label_fmt!r}"
        )

    def test_number_format_flows_to_label(self, make_chart):
        label_fmt, axis_fmt = self._label_fmt(
            make_chart, {"style": {"number_format": ",.0f"}}
        )
        assert label_fmt == axis_fmt == ",.0f"

    def test_axis_y_format_flows_to_label(self, make_chart):
        label_fmt, axis_fmt = self._label_fmt(
            make_chart, {"style": {"axis_y": {"labels": {"format": ",.0f"}}}}
        )
        assert label_fmt == axis_fmt == ",.0f"

    def test_explicit_chart_label_format_is_kept(self, make_chart):
        """An explicit chart-level label format wins over the axis format."""
        label_fmt, axis_fmt = self._label_fmt(
            make_chart,
            {
                "format": ",.0f",
                "style": {"marks": {"bar": {"labels": {"format": ".2%"}}}},
            },
        )
        assert axis_fmt == ",.0f"
        assert label_fmt == ".2%"

    def test_no_override_still_inherits_axis_quantitative(self, make_chart):
        """Without a per-chart format, the board inherit is preserved.

        The theme default resolves the ``number`` alias to ``.3~s`` — a
        bounded (3-sig-fig) SI format, so value labels on real data read ``9.18``
        rather than the unbounded ``9.18039`` that a bare ``~s`` produces. It is
        SI-shaped, so the label takes the narrative house register (a
        calculate transform built from that same ``.3~s`` spec) rather than a
        literal ``text.format``.
        """
        board_rs, board_ctx = _board_with_bar_labels({})
        chart = make_chart("bar", x="month", y="revenue")
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        assert (
            resolved.style.mark.labels.format.spec
            == resolved.style.axis_y.labels.format.spec
        )
        assert resolved.style.mark.labels.format.spec == ".3~s"
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["encoding"]["text"].get("format") is None
        text_field = lyr["encoding"]["text"]["field"]
        expected = _house_register_expr("datum['revenue']", ResolvedFormat(spec=".3~s"))
        calc = next(
            (t for t in spec.get("transform", []) if t.get("as") == text_field), None
        )
        assert calc is not None and calc["calculate"] == expected

    def test_line_label_tracks_per_chart_format(self, make_chart):
        board_rs, board_ctx = _board_with_line_labels_visible(True)
        chart = make_chart("line", x="month", y="revenue", format=",.0f")
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert (
            lyr["encoding"]["text"].get("format")
            == resolved.style.axis_y.labels.format.spec
        )
        assert lyr["encoding"]["text"].get("format") == ",.0f"

    def test_area_label_tracks_per_chart_format(self, make_chart):
        # Area value labels ride the overlaid line mark; the sync path differs
        # from bar/line, so exercise it explicitly.
        board_rs, board_ctx = _board_with_line_labels_visible(True)
        chart = make_chart("area", x="month", y="revenue", format=",.0f")
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert (
            lyr["encoding"]["text"].get("format")
            == resolved.style.axis_y.labels.format.spec
        )
        assert lyr["encoding"]["text"].get("format") == ",.0f"


class TestFontApplication:
    def test_font_color_in_encoding(self, make_chart):
        """font.color goes into encoding.color, not mark.color.

        Vega-Lite lets an inherited encoding channel beat a static mark prop, so
        a label carrying its color only on the mark is repainted by any outer
        color encoding.
        """
        from dbt_charts.core.compile.models.primitives import FontStyle

        board_rs, board_ctx = _board_with_bar_labels(
            {"font": FontStyle(color="#ff0000")}
        )
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["encoding"].get("color") == {"value": "#ff0000"}
        assert "color" not in lyr["mark"], (
            "a static mark color any outer encoding can beat must not linger"
        )

    def test_font_color_survives_bar_color_channel(self, make_chart):
        """An authored font.color wins over the chart's own color encoding."""
        from dbt_charts.core.compile.models.primitives import FontStyle

        board_rs, board_ctx = _board_with_bar_labels(
            {"font": FontStyle(color="#ff0000")}
        )
        chart = make_chart("bar", x="month", y="revenue", color="month")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["encoding"].get("color") == {"value": "#ff0000"}

    def test_font_size_in_encoding(self, make_chart):
        """font.size goes into encoding.size so it overrides inherited size on bubble charts."""
        from dbt_charts.core.compile.models.primitives import FontStyle

        board_rs, board_ctx = _board_with_point_labels({"font": FontStyle(size=14)})
        chart = make_chart("scatter", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["encoding"].get("size") == {"value": 14}

    def test_scatter_bubble_size_not_inherited_by_text(self, make_chart):
        """Scatter chart with size channel: text labels must not inherit size encoding."""
        board_rs, board_ctx = _board_with_point_labels_visible(True)
        chart = make_chart("scatter", x="month", y="revenue", size="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert "layer" in spec
        outer_enc = spec.get("encoding", {})
        assert "size" not in outer_enc, (
            f"Outer encoding must not expose size to text layer: {outer_enc}"
        )

    def test_middle_grouped_bar_has_background_encoding_color(self, make_chart) -> None:
        """position=middle on a grouped bar: label color equals chart background.

        A grouped (non-stacked) bar with a color channel has bars painted with
        the nominal palette.  A label at the midpoint inherits the bar fill color
        through the outer encoding → invisible.  The auto-background color is
        pinned as the layer's own encoding.color, which is what actually beats
        that inheritance, and uses the actual chart background rather than
        hardcoded white so dark-mode works.
        """
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        chart = make_chart("bar", x="month", y="revenue", color="month")
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None, (
            "Grouped bar with middle position must emit a text layer"
        )
        expected_bg = resolved.canvas
        assert lyr["encoding"].get("color") == {"value": expected_bg}, (
            f"middle-position label must pin canvas {expected_bg!r}, "
            f"got: {lyr['encoding']}"
        )

    def test_font_color_overrides_auto_background_for_top(self, make_chart) -> None:
        """Explicit font.color beats auto-background even on an inside-position stacked bar."""
        from dbt_charts.core.compile.models.primitives import FontStyle

        board_rs, board_ctx = _board_with_bar_labels(
            {"position": "top", "font": FontStyle(color="#ff0000")}
        )
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={"bar": {"stack": "zero", "orientation": "vertical"}},
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["encoding"].get("color") == {"value": "#ff0000"}, (
            "explicit font.color must take precedence over auto-background and be "
            f"pinned on the encoding, got: {lyr['encoding'].get('color')!r}"
        )

    def test_font_style_italic_in_mark(self, make_chart):
        """font.style reaches the mark as VL's fontStyle, so italic labels render italic."""
        from dbt_charts.core.compile.models.primitives import FontStyle

        board_rs, board_ctx = _board_with_bar_labels(
            {"font": FontStyle(style="italic")}
        )
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["mark"].get("fontStyle") == "italic", (
            f"labels.font.style must emit mark.fontStyle: {lyr['mark']}"
        )

    def test_point_font_style_italic_in_mark(self, make_chart):
        """Point labels take the same fontStyle path as bar labels."""
        from dbt_charts.core.compile.models.primitives import FontStyle

        board_rs, board_ctx = _board_with_point_labels(
            {"font": FontStyle(style="italic")}
        )
        chart = make_chart("scatter", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["mark"].get("fontStyle") == "italic", (
            f"point labels.font.style must emit mark.fontStyle: {lyr['mark']}"
        )

    def test_font_style_unset_emits_no_font_style(self, make_chart):
        """A label font with style unset emits no fontStyle key.

        The board must carry a real FontStyle here — an absent ``labels.font``
        returns from ``apply_label_font`` before the guard, so ``{}`` would pass
        this test with the guard deleted.
        """
        from dbt_charts.core.compile.models.primitives import FontStyle

        board_rs, board_ctx = _board_with_bar_labels({"font": FontStyle(size=11)})
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert "fontStyle" not in lyr["mark"], (
            f"unset font.style must leave fontStyle off the mark: {lyr['mark']}"
        )


class TestPerChartOverride:
    """Per-chart style.bar.marks.bar.labels.visible: true flows to the text layer."""

    def test_bar_per_chart_override_emits_text_layer(self, make_chart):
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={"bar": {"marks": {"bar": {"labels": {"visible": True}}}}},
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_CTX)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        assert _has_text_layer(spec), (
            "Per-chart bar.marks.bar.labels.visible=True must emit text layer"
        )

    def test_scatter_per_chart_override_emits_text_layer(self, make_chart):
        chart = make_chart(
            "scatter",
            x="month",
            y="revenue",
            style={"scatter": {"marks": {"point": {"labels": {"visible": True}}}}},
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_CTX)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        assert _has_text_layer(spec), (
            "Per-chart scatter.marks.point.labels.visible=True must emit text layer"
        )

    def test_line_per_chart_override_emits_text_layer(self, make_chart):
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style={"line": {"marks": {"line": {"labels": {"visible": True}}}}},
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_CTX)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        assert _has_text_layer(spec), (
            "Per-chart line.marks.line.labels.visible=True must emit text layer"
        )


class TestScatterXYPositioning:
    """Promoted scatter spec shares x/y encoding at outer level so text layer is positioned."""

    def test_scatter_text_layer_outer_has_xy(self, make_chart):
        board_rs, board_ctx = _board_with_point_labels_visible(True)
        chart = make_chart("scatter", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert "layer" in spec, "Promoted scatter spec must be layered"
        outer_enc = spec.get("encoding", {})
        assert "x" in outer_enc, f"Outer encoding must have x; got: {outer_enc}"
        assert "y" in outer_enc, f"Outer encoding must have y; got: {outer_enc}"


_MULTI_Y_DATA = [
    {"month": "Jan", "revenue": 100, "cost": 60},
    {"month": "Feb", "revenue": 200, "cost": 80},
]


class TestMultiYAndStackedGuards:
    def test_multi_y_line_no_text_layer(self, make_chart):
        """Multi-y line chart skips labels rather than silently labeling only the first series."""
        board_rs, board_ctx = _board_with_line_labels_visible(True)
        chart = make_chart("line", x="month", y=["revenue", "cost"])
        resolve(chart, _MULTI_Y_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _MULTI_Y_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert not _has_text_layer(spec), "Multi-y line must not emit a text layer"

    def test_stacked_bar_middle_position_emits_text_layer(self, make_chart):
        """Stacked bar + position:middle labels each segment at its band center."""
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"stack": "zero"}}
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert _has_text_layer(spec), (
            "Stacked bar with position=middle must emit a text layer"
        )

    def test_stacked_bar_top_position_still_emits_text_layer(self, make_chart):
        """Stacked bar + position:top (not middle) still gets labels."""
        board_rs, board_ctx = _board_with_bar_labels({"position": "top"})
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"stack": "zero"}}
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert _has_text_layer(spec), (
            "Stacked bar with position=top must still emit a text layer"
        )


_MULTI_SERIES_DATA = [
    {"month": "Jan", "revenue": 100, "region": "A"},
    {"month": "Feb", "revenue": 200, "region": "B"},
]


class TestLayeredPositioningAndMultiSeries:
    """Pins the text-layer positioning contract for bar/line and multi-series behavior."""

    def test_layered_bar_text_layer_positioned_via_shared_encoding(
        self, make_chart
    ) -> None:
        """Bar text layer relies on shared top-level x/y for positioning.

        The injected text layer carries only a `text` channel; x/y must be
        present in the spec's outer (shared) encoding so VL can position labels.
        """
        board_rs, board_ctx = _board_with_bar_labels_visible(True)
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert "layer" in spec, "Bar with labels must be a layered spec"
        outer_enc = spec.get("encoding", {})
        assert "x" in outer_enc, (
            f"Shared encoding must have x for label positioning: {outer_enc}"
        )
        assert "y" in outer_enc, (
            f"Shared encoding must have y for label positioning: {outer_enc}"
        )

    def test_multi_series_line_color_no_text_layer(self, make_chart) -> None:
        """Line chart with single y + color series silently emits no labels.

        The spec becomes hconcat (legend pane), so the injector finds neither
        `layer` nor `mark` at the top level and returns unchanged. This pins
        the known behavior so a future spec-shape change is caught.
        """
        board_rs, board_ctx = _board_with_line_labels_visible(True)
        chart = make_chart("line", x="month", y="revenue", color="region")
        resolve(chart, _MULTI_SERIES_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            _MULTI_SERIES_DATA,
            board_style=board_rs,
            chart_style_context=board_ctx,
        )
        assert not _has_text_layer(spec), (
            "Multi-series line (hconcat) must not emit a text layer"
        )


class TestBarTextFieldEncoding:
    """The text encoding field must be the measure (y for horizontal, y for vertical)."""

    def test_text_field_is_measure_horizontal(self, make_chart) -> None:
        """Horizontal bar: text sources the measure (revenue) — directly when
        the format is non-SI, or via a calculate transform when it takes the
        narrative house register (the theme default is SI-shaped)."""
        board_rs, board_ctx = _board_with_bar_labels_visible(True)
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        text_field = lyr["encoding"]["text"]["field"]
        assert text_field == "__value_label_text", f"got: {lyr['encoding']['text']}"
        calc = next(
            (t for t in spec.get("transform", []) if t.get("as") == text_field), None
        )
        assert calc is not None and "revenue" in calc["calculate"], (
            f"text must source the measure 'revenue' via calculate: {calc}"
        )

    def test_text_field_is_measure_vertical(self, make_chart) -> None:
        """Vertical bar: text still sources the measure (revenue), same rule
        as the horizontal case above."""
        board_rs, board_ctx = _board_with_bar_labels_visible(True)
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        text_field = lyr["encoding"]["text"]["field"]
        assert text_field == "__value_label_text", f"got: {lyr['encoding']['text']}"
        calc = next(
            (t for t in spec.get("transform", []) if t.get("as") == text_field), None
        )
        assert calc is not None and "revenue" in calc["calculate"], (
            f"text must source the measure 'revenue' via calculate: {calc}"
        )


class TestLinePointLabelsAlias:
    """marks.point.labels on line charts is accepted and treated as an alias for marks.line.labels."""

    def test_line_chart_marks_point_labels_accepted(self, make_chart) -> None:
        """marks.point.labels.visible: true on a line chart is valid (not schema-rejected)."""
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style={"line": {"marks": {"point": {"labels": {"visible": True}}}}},
        )
        # No ValidationError — marks.point.labels is now allowed on line charts
        resolve(chart, SAMPLE_DATA, chart_style_context=_BOARD_CTX)
        spec = generate_vega_lite_spec(chart, SAMPLE_DATA)
        assert _has_text_layer(spec), (
            "marks.point.labels.visible=True on line chart must emit text layer (alias for marks.line.labels)"
        )

    def test_line_marks_line_labels_takes_precedence(self, make_chart) -> None:
        """When both line.labels and point.labels are visible, marks.line.labels is used."""
        compiled = get_theme_style("clarity")
        # Set marks.line.labels with a distinctive format so we can confirm it's used.
        new_line_lbl = compiled.charts.marks.line.labels.model_copy(
            update={"visible": True, "format": ",.2f"}
        )
        new_line = compiled.charts.marks.line.model_copy(
            update={"labels": new_line_lbl}
        )
        # Set marks.point.labels visible too, but with a different format.
        new_point_lbl = compiled.charts.marks.point.labels.model_copy(
            update={"visible": True, "format": ".0%"}
        )
        new_point = compiled.charts.marks.point.model_copy(
            update={"labels": new_point_lbl}
        )
        new_marks = compiled.charts.marks.model_copy(
            update={"line": new_line, "point": new_point}
        )
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(
                update={
                    "charts": compiled.charts.model_copy(update={"marks": new_marks})
                }
            )
        )
        chart = make_chart("line", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None, "Must emit text layer"
        # marks.line.labels takes precedence: format should be ",.2f", not ".0%"
        assert lyr["encoding"]["text"].get("format") == ",.2f", (
            f"marks.line.labels format must win over marks.point.labels: {lyr}"
        )


class TestStackedBarLabelPositioning:
    """Stacked bar (explicit stack) labels must auto-upgrade above→top and use stacked position."""

    def test_stacked_bar_above_auto_upgrades_to_top(self, make_chart) -> None:
        """position=above on stacked bars auto-upgrades to top (inside) so labels stay in segments."""
        board_rs, board_ctx = _board_with_bar_labels({"position": "above"})
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={"bar": {"stack": "zero", "orientation": "vertical"}},
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None, (
            "Stacked bar must still emit a text layer for position=above"
        )
        mark = lyr["mark"]
        # after auto-upgrade to top: baseline=top, dy=4
        assert mark.get("baseline") == "top", (
            f"Stacked bar above auto-upgrades to top mark (baseline=top), got {mark}"
        )
        assert mark.get("dy") == 4, (
            f"Stacked bar above auto-upgrades to top mark (dy=4), got {mark}"
        )

    def test_stacked_bar_has_explicit_stacked_y_channel(self, make_chart) -> None:
        """Stacked vertical bar: text layer carries explicit y with stack='zero'."""
        board_rs, board_ctx = _board_with_bar_labels({"position": "top"})
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={"bar": {"stack": "zero", "orientation": "vertical"}},
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        enc = lyr.get("encoding", {})
        assert "y" in enc, (
            f"Stacked vertical bar text layer must have explicit y: {enc}"
        )
        assert enc["y"].get("stack") == "zero", (
            f"Stacked y must have stack=zero: {enc['y']}"
        )

    def test_stacked_horizontal_bar_has_explicit_stacked_x_channel(
        self, make_chart
    ) -> None:
        """Stacked horizontal bar: text layer carries explicit x with stack='zero'."""
        board_rs, board_ctx = _board_with_bar_labels({"position": "top"})
        # x="month" (categorical) → horizontal orientation by default
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={"bar": {"stack": "zero"}},
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        enc = lyr.get("encoding", {})
        assert "x" in enc, (
            f"Stacked horizontal bar text layer must have explicit x: {enc}"
        )
        assert enc["x"].get("stack") == "zero", (
            f"Stacked x must have stack=zero: {enc['x']}"
        )

    def test_stacked_bar_preserves_authored_dx_dy_font(self, make_chart) -> None:
        """Authored dx/dy/font overrides must not be dropped on stacked bars."""
        board_rs, board_ctx = _board_with_bar_labels(
            {"position": "top", "dx": 12, "dy": -8, "font": {"family": "monospace"}}
        )
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={"bar": {"stack": "zero", "orientation": "vertical"}},
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        mark = lyr["mark"]
        assert mark.get("dx") == 12, (
            f"Authored dx must be preserved on stacked bar: {mark}"
        )
        assert mark.get("dy") == -8, (
            f"Authored dy must be preserved on stacked bar: {mark}"
        )
        font_val = mark.get("font", "")
        assert "monospace" in font_val, (
            f"Authored font must be preserved on stacked bar: {mark}"
        )

    def test_stacked_bar_top_label_has_background_color(self, make_chart) -> None:
        """Stacked vertical bar with top: label color equals the chart background.

        Hardcoding white (#FFFFFF) assumes a white dashboard — use the resolved
        background so dark-mode themes produce legible labels too. The color is
        pinned as a constant ``encoding.color: {value: ...}`` on the label layer;
        a mark-level color would be silently overridden by the inherited nominal
        color channel (the segment's own fill), making the label invisible.
        """
        board_rs, board_ctx = _board_with_bar_labels({"position": "top"})
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={"bar": {"stack": "zero", "orientation": "vertical"}},
        )
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None, "Stacked bar must emit a text layer"
        expected_bg = resolved.canvas
        assert lyr["encoding"].get("color") == {"value": expected_bg}, (
            f"stacked top label must pin constant color {expected_bg!r} on the "
            f"encoding, got: {lyr['encoding'].get('color')!r}"
        )

    def test_nonstacked_bar_explicit_top_has_background_color(self, make_chart) -> None:
        """Non-stacked bar with explicit position=top also gets background color.

        The auto-background guard must not be gated on is_stacked — any inside-bar
        position on any bar chart needs it.
        """
        board_rs, board_ctx = _board_with_bar_labels({"position": "top"})
        chart = make_chart("bar", x="month", y="revenue", color="month")
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None, "Grouped bar with top must emit a text layer"
        expected_bg = resolved.canvas
        assert lyr["encoding"].get("color") == {"value": expected_bg}, (
            f"top non-stacked bar must pin chart canvas {expected_bg!r}, "
            f"got: {lyr['encoding']}"
        )


_STACKED_MULTI_SERIES_DATA = [
    {"month": "Jan", "revenue": 100, "region": "A"},
    {"month": "Jan", "revenue": 150, "region": "B"},
    {"month": "Feb", "revenue": 200, "region": "A"},
    {"month": "Feb", "revenue": 120, "region": "B"},
]


def _get_value_label_layer(spec: dict[str, Any]) -> dict[str, Any] | None:
    """Return the value-labels text layer: text mark with quantitative field
    encoding, or the computed narrative-register field (see
    _house_register_text_encoding, render/chart/features/value_labels.py).

    Distinguishes the value-label layer from support_table strip layers (which use
    a text mark but encode __support_table_N fields).
    """
    for lyr in spec.get("layer", []):
        m = lyr.get("mark", {})
        if not (isinstance(m, dict) and m.get("type") == "text"):
            continue
        enc = lyr.get("encoding", {})
        text_enc = enc.get("text", {})
        if text_enc.get("type") == "quantitative":
            return lyr
        if text_enc.get("field") == "__value_label_text":
            return lyr
    return None


class TestLayersDoNotSuppressBaseValueLabels:
    """A base chart's own value labels still apply when overlay ``layers:`` are
    present — an overlay layer with no labels config of its own must not
    suppress the base chart's labels (each family's own mark style is
    independent). Where a family exposes a labels-visible board, the base's
    own text layer must appear even though the chart also carries a
    (label-less) overlay layer; families with no labels-visible board (area,
    scatter — no override helper here) still emit none, unaffected."""

    @pytest.mark.parametrize("base_type", ["bar", "line", "area", "scatter"])
    def test_base_with_layers_keeps_its_own_value_labels(
        self, make_chart, base_type
    ) -> None:
        boards = {
            "bar": _board_with_bar_labels_visible(True),
            "line": _board_with_line_labels_visible(True),
        }
        board_rs, board_ctx = boards.get(
            base_type, resolve_style_and_context(get_theme_style("clarity"))
        )
        chart = make_chart(
            base_type,
            x="month",
            y="revenue",
            layers=[{"type": "line", "y": "revenue", "label": "Revenue Trend"}],
        )
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        if base_type in boards:
            assert _has_text_layer(spec), (
                f"{base_type} base's own value labels must still render with a layer present"
            )
        else:
            assert not _has_text_layer(spec), (
                f"{base_type}+overlay must not emit a value-label text layer when neither has labels enabled"
            )


class TestSupportTableValueLabels:
    """Value labels on a bar chart that also has a support_table attachment."""

    def test_bar_with_support_table_has_x_and_y_in_text_layer(self, make_chart) -> None:
        """After apply_chart_support_table_post_pass wraps the spec into layers, the injected
        text layer must still carry both x and y so labels are positioned correctly.

        _wrap_base_as_layer moves mark+encoding into layer[0]; the outer spec gets
        only tooltip in its encoding. Without the fix, the text layer has no x or y
        and labels appear at wrong positions.
        """
        from dbt_charts.core.compile.models.chart.authored import (
            ChartSupportTable,
            ChartSupportTableAggregate,
        )

        board_rs, board_ctx = _board_with_bar_labels({"position": "top"})
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            # "month" is a categorical string x, which auto-resolves to horizontal;
            # support_table is unsupported there, so pin vertical explicitly.
            style={"orientation": "vertical"},
            support_table=ChartSupportTable(
                entries=[ChartSupportTableAggregate(source="revenue", aggregate="sum")]
            ),
        )
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            SAMPLE_DATA,
            width=600.0,
            board_style=board_rs,
            chart_style_context=board_ctx,
        )
        lyr = _get_value_label_layer(spec)
        assert lyr is not None, (
            "Must emit value-label text layer when support_table is present"
        )
        enc = lyr.get("encoding", {})
        assert "x" in enc, (
            f"Value-label text layer must have x channel for positioning: {enc}"
        )
        assert "y" in enc, (
            f"Value-label text layer must have y channel for positioning: {enc}"
        )


# =============================================================================
# TestBarTotalLabel — stack total label feature (TDD: written before impl)
# =============================================================================

_STACKED_DATA = [
    {"month": "Jan", "channel": "Web", "revenue": 100},
    {"month": "Jan", "channel": "Mobile", "revenue": 60},
    {"month": "Feb", "channel": "Web", "revenue": 120},
    {"month": "Feb", "channel": "Mobile", "revenue": 80},
]


def _board_with_bar_total_label_visible(visible: bool) -> ResolvedStyle:
    """Build a resolved style with bar total_label.visible configured."""
    compiled = get_theme_style("clarity")
    new_total = compiled.charts.marks.bar.total_label.model_copy(
        update={"visible": visible}
    )
    new_bar = compiled.charts.marks.bar.model_copy(update={"total_label": new_total})
    new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _get_total_label_layer(spec: dict[str, Any]) -> dict[str, Any] | None:
    """Return the total-label text layer, or None.

    text.field is '__stack_total' when the format is non-SI (emitted
    verbatim) or '__stack_total_text' when it takes the narrative house
    register (an SI-shaped format, computed via a calculate transform —
    see _house_register_text_encoding in render/chart/features/value_labels.py).
    """
    for lyr in chart_pane(spec).get("layer", []):
        m = lyr.get("mark", {})
        if not (isinstance(m, dict) and m.get("type") == "text"):
            continue
        enc = lyr.get("encoding", {})
        if enc.get("text", {}).get("field") in ("__stack_total", "__stack_total_text"):
            return lyr
    return None


def _has_joinaggregate_sum(spec: dict[str, Any]) -> bool:
    """Return True if the spec has a joinaggregate sum into __stack_total."""
    transforms = chart_pane(spec).get("transform", [])
    for t in transforms:
        if "joinaggregate" not in t:
            continue
        for agg in t["joinaggregate"]:
            if agg.get("op") == "sum" and agg.get("as") == "__stack_total":
                return True
    return False


class TestBarTotalLabel:
    """Stack total label: additive text layer showing per-category segment sum."""

    def test_vertical_stacked_bar_emits_total_label_layer(self, make_chart) -> None:
        """Vertical stacked bar with total_label.visible=True emits a text layer
        that sources its position from __stack_total (joinaggregate sum).

        The default total_label.format is unauthored and inherits the theme's
        SI default (.3~s), so the text channel takes the narrative house
        register — a computed field, not __stack_total directly. See
        _house_register_text_encoding (render/chart/features/value_labels.py).
        """
        board_rs, board_ctx = _board_with_bar_total_label_visible(True)
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, _STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _STACKED_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        assert _has_joinaggregate_sum(spec), (
            "Vertical stacked bar total label must add a joinaggregate sum "
            f"transform into __stack_total; got transforms: {spec.get('transform')}"
        )
        lyr = _get_total_label_layer(spec)
        assert lyr is not None, "Must emit a total-label text layer"
        enc = lyr.get("encoding", {})
        assert enc.get("text", {}).get("field") == "__stack_total_text"
        assert enc.get("text", {}).get("type") == "nominal"
        calc = next(
            (
                t
                for t in chart_pane(spec).get("transform", [])
                if t.get("as") == "__stack_total_text"
            ),
            None,
        )
        assert calc is not None and "__stack_total" in calc["calculate"], (
            f"narrative label text must be computed from __stack_total: {calc}"
        )
        # Vertical: measure on y, category on x
        assert enc.get("y", {}).get("field") == "__stack_total", (
            f"Vertical total label must position on y=__stack_total: {enc}"
        )
        assert enc.get("x", {}).get("field") == "month", (
            f"Vertical total label must carry category on x: {enc}"
        )

    def test_horizontal_stacked_bar_emits_total_label_layer(self, make_chart) -> None:
        """Horizontal stacked bar with total_label.visible=True emits a text layer
        that sources its measure from __stack_total on the x channel."""
        board_rs, board_ctx = _board_with_bar_total_label_visible(True)
        # x="month" (categorical) → horizontal orientation by default
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
        )
        resolve(chart, _STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _STACKED_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        assert _has_joinaggregate_sum(spec), (
            "Horizontal stacked bar total label must add a joinaggregate sum "
            f"transform; got: {spec.get('transform')}"
        )
        lyr = _get_total_label_layer(spec)
        assert lyr is not None, "Must emit a total-label text layer for horizontal bars"
        enc = lyr.get("encoding", {})
        # Horizontal: measure on x, category on y
        assert enc.get("x", {}).get("field") == "__stack_total", (
            f"Horizontal total label must position on x=__stack_total: {enc}"
        )
        assert enc.get("y", {}).get("field") == "month", (
            f"Horizontal total label must carry category on y: {enc}"
        )
        # Default total_label.format is unauthored SI (theme default) — text
        # takes the narrative house register, a computed field.
        assert enc.get("text", {}).get("field") == "__stack_total_text"

    def test_total_label_explicit_format_passes_through_verbatim(
        self, make_chart
    ) -> None:
        """total_label.format = ".3s" (inline d3, not a predefined name) → passes
        verbatim to Vega without trim injection (three-way contract).

        The gate is predefined-membership alone: ".3s" is not a predefined name,
        so it classifies as inline d3 and the spec passes straight to Vega
        text.format unchanged. Authors who want trim write ".3~s" themselves.
        A format authored via a predefined name would get house trim baked
        (tested in test_total_label_explicit_format_resolves_a_theme_alias)."""
        board_rs, board_ctx = _board_with_bar_total_label_visible(True)
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            # Chart-local: marks.bar.total_label.format = ".3s" (authored literal)
            style={
                "marks": {"bar": {"total_label": {"format": ".3s"}}},
                "bar": {"orientation": "vertical"},
            },
        )
        resolve(chart, _STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            _STACKED_DATA,
            board_style=board_rs,
            chart_style_context=board_ctx,
        )

        lyr = _get_total_label_layer(spec)
        assert lyr is not None
        # Inline d3: spec reaches Vega verbatim (no trim injected).
        assert lyr["encoding"]["text"].get("format") == ".3s", (
            f"chart-local literal .3s must reach Vega unchanged (no trim for inline d3): "
            f"{lyr['encoding']['text']}"
        )
        # No calculate transform for a native-register total label
        text_field = lyr["encoding"]["text"]["field"]
        calc = next(
            (
                t
                for t in chart_pane(spec).get("transform", [])
                if t.get("as") == text_field
            ),
            None,
        )
        assert calc is None, (
            f"native register must not emit a calculate transform: {calc}"
        )

    def test_total_label_explicit_format_resolves_a_theme_alias(
        self, make_chart
    ) -> None:
        """An explicit total_label.format must resolve through the theme's
        alias table like every other format slot — bar.py's raw_total branch
        hands the resolved spec to Vega verbatim, so an unresolved alias key
        would compile clean and ship the literal word to Vega's d3."""
        compiled = get_theme_style("clarity")
        new_total = compiled.charts.marks.bar.total_label.model_copy(
            update={"visible": True, "format": "currency_full"}
        )
        new_bar = compiled.charts.marks.bar.model_copy(
            update={"total_label": new_total}
        )
        new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
        charts = compiled.charts.model_copy(update={"marks": new_marks})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, _STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            _STACKED_DATA,
            board_style=board_rs,
            chart_style_context=board_ctx,
        )

        lyr = _get_total_label_layer(spec)
        assert lyr is not None
        assert lyr["encoding"]["text"]["format"] == "$,.2f"

    def test_total_label_and_segment_labels_coexist(self, make_chart) -> None:
        """Per-segment labels and total_label can both be visible simultaneously."""
        compiled = get_theme_style("clarity")
        new_labels = compiled.charts.marks.bar.labels.model_copy(
            update={"visible": True}
        )
        new_total = compiled.charts.marks.bar.total_label.model_copy(
            update={"visible": True}
        )
        new_bar = compiled.charts.marks.bar.model_copy(
            update={"labels": new_labels, "total_label": new_total}
        )
        new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
        charts = compiled.charts.model_copy(update={"marks": new_marks})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, _STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _STACKED_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        text_layers = [
            lyr
            for lyr in chart_pane(spec).get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        total_lyr = _get_total_label_layer(spec)
        segment_lyrs = [
            lyr
            for lyr in text_layers
            if lyr.get("encoding", {}).get("text", {}).get("field")
            not in ("__stack_total", "__stack_total_text")
        ]
        assert total_lyr is not None, "Must emit total-label layer"
        assert segment_lyrs, "Must still emit per-segment label layer"
        assert all(
            lyr.get("encoding", {}).get("text", {}).get("field")
            in ("__value_label_text", "revenue")
            for lyr in segment_lyrs
        ), (
            f"segment label layer must be the per-segment field, not the total: "
            f"{[lyr.get('encoding', {}).get('text', {}) for lyr in segment_lyrs]}"
        )

    def test_non_stacked_bar_total_label_no_layer_no_crash(self, make_chart) -> None:
        """total_label on a non-stacked bar is a documented no-op: no extra layer,
        no error raised."""
        board_rs, board_ctx = _board_with_bar_total_label_visible(True)
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        assert _get_total_label_layer(spec) is None, (
            "total_label must have no effect on a non-stacked bar (no extra layer)"
        )
        assert not _has_joinaggregate_sum(spec), (
            "Non-stacked bar must not emit a joinaggregate sum transform"
        )

    def test_total_label_font_color_pinned_in_encoding(self, make_chart) -> None:
        """When font.color is set, it is pinned via encoding.color.value so it
        overrides the inherited outer nominal color:{field:segment} encoding."""
        from dbt_charts.core.compile.models.primitives import FontStyle

        compiled = get_theme_style("clarity")
        new_total = compiled.charts.marks.bar.total_label.model_copy(
            update={"visible": True, "font": FontStyle(color="#cc0000")}
        )
        new_bar = compiled.charts.marks.bar.model_copy(
            update={"total_label": new_total}
        )
        new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
        charts = compiled.charts.model_copy(update={"marks": new_marks})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, _STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _STACKED_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        lyr = _get_total_label_layer(spec)
        assert lyr is not None, "Must emit total-label layer"
        assert lyr["encoding"].get("color", {}).get("value") == "#cc0000", (
            f"font.color must be in encoding.color.value (not mark.color): {lyr['encoding']}"
        )

    def test_total_label_font_style_italic_in_mark(self, make_chart) -> None:
        """total_label.font.style reaches the total-label mark as fontStyle."""
        from dbt_charts.core.compile.models.primitives import FontStyle

        compiled = get_theme_style("clarity")
        new_total = compiled.charts.marks.bar.total_label.model_copy(
            update={"visible": True, "font": FontStyle(style="italic")}
        )
        new_bar = compiled.charts.marks.bar.model_copy(
            update={"total_label": new_total}
        )
        new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
        charts = compiled.charts.model_copy(update={"marks": new_marks})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, _STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _STACKED_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        lyr = _get_total_label_layer(spec)
        assert lyr is not None, "Must emit total-label layer"
        assert lyr["mark"].get("fontStyle") == "italic", (
            f"total_label.font.style must emit mark.fontStyle: {lyr['mark']}"
        )

    def test_total_label_format_applied(self, make_chart) -> None:
        """An explicit total_label.format is emitted on the text encoding."""
        compiled = get_theme_style("clarity")
        new_total = compiled.charts.marks.bar.total_label.model_copy(
            update={"visible": True, "format": ",.0f"}
        )
        new_bar = compiled.charts.marks.bar.model_copy(
            update={"total_label": new_total}
        )
        new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
        charts = compiled.charts.model_copy(update={"marks": new_marks})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, _STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _STACKED_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        lyr = _get_total_label_layer(spec)
        assert lyr is not None, "Must emit total-label layer"
        assert lyr["encoding"]["text"].get("format") == ",.0f", (
            f"total_label.format must be on text encoding: {lyr['encoding']['text']}"
        )

    def test_total_label_measure_channel_disables_inherited_stacking(
        self, make_chart
    ) -> None:
        """The total-label layer's measure-channel encoding must opt out of the
        outer shared channel's stacking.

        Regression: the outer chart's y (or x, for horizontal) encoding carries
        stack: "zero" for the segment bars. A sublayer whose own encoding for that
        channel omits "stack" entirely does not silently fall back to "no
        stacking" — Vega-Lite treats an unset "stack" on a channel that shares a
        stacked scale as inheriting stacking, so it tried to stack the
        already-summed, per-row-duplicated __stack_total field across the color
        groups. That produced a corrupted scale domain and collapsed the whole
        chart (bars included) to zero height — verified by rendering the spec
        through vl-convert.
        """
        board_rs, board_ctx = _board_with_bar_total_label_visible(True)
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, _STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _STACKED_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        lyr = _get_total_label_layer(spec)
        assert lyr is not None, "Must emit total-label layer"
        y_enc = lyr["encoding"]["y"]
        assert "stack" in y_enc and y_enc["stack"] is None, (
            "total-label y encoding must explicitly set stack: None to opt out "
            f"of the outer shared channel's inherited stacking: {y_enc}"
        )

    def test_total_label_vertical_stacked_bar_does_not_collapse_height(
        self, make_chart
    ) -> None:
        """End-to-end: render a vertical stacked bar with total_label through
        vl-convert and assert the plot area has non-zero height.

        This is the exact user-reported bug: adding total_label to a vertical
        stacked bar made the whole chart invisible. The structural test above
        pins the mechanism (stack: None); this test proves the visible symptom
        is actually fixed, since a spec-only assertion can't tell you whether
        Vega-Lite's own scale-domain resolution still blows up at render time.
        """
        try:
            import vl_convert as vlc  # noqa: F401 — skip marker
        except ImportError:
            pytest.skip("vl_convert not installed")

        import re

        board_rs, board_ctx = _board_with_bar_total_label_visible(True)
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, _STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _STACKED_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        svg = vlc.vegalite_to_svg(spec)
        match = re.search(r'class="background"[^>]*d="M0,0h[\d.]+v([\d.]+)h', svg)
        assert match, "no plot-area background path found in rendered SVG"
        assert float(match.group(1)) > 1.0, (
            f"plot area height collapsed to {match.group(1)} — the bars and "
            "everything else in the chart would be invisible"
        )

    def test_total_label_default_styling_pins_color(self, make_chart) -> None:
        """Default total_label (no explicit font authored) must still pin a single
        color via encoding.color.value, so overlapping __stack_total rows from the
        joinaggregate do not each inherit a different segment color from the outer
        color:{field:channel} encoding, which would render as multi-colored
        overprinting instead of one readable total label.
        """
        board_rs, board_ctx = _board_with_bar_total_label_visible(True)

        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, _STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _STACKED_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        lyr = _get_total_label_layer(spec)
        assert lyr is not None, "Must emit total-label layer"

        # Color must be in encoding.color.value (constant override), not mark.color.
        # mark.color is overridden by an inherited nominal color encoding in VL;
        # encoding.color.value wins over inherited field encodings.
        pinned_color = lyr["encoding"].get("color", {}).get("value")
        assert pinned_color is not None, (
            "Default total-label encoding must carry encoding.color.value (cascaded from "
            "charts.font) so overlapping joinaggregate rows do not each inherit a different "
            f"segment color. Encoding: {lyr['encoding']}"
        )

        # The pinned color must NOT equal the chart background — that would make
        # the label invisible against the canvas it sits on (background is chosen
        # for contrast against bar fills, not against the page).
        background = board_rs.chart_defaults.background
        assert pinned_color != background, (
            f"Default total-label color {pinned_color!r} must differ from chart "
            f"background {background!r}: pinning background makes the label invisible."
        )


# =============================================================================
# TestHouseRegister — mark labels speak the house register, not raw d3 SI.
# =============================================================================

_MILLIONS_DATA = [
    {"month": "Jan", "revenue": 1_200_000},
    {"month": "Feb", "revenue": 2_400_000},
    {"month": "Mar", "revenue": 3_600_000},
]


def _render_texts(spec: dict[str, Any]) -> list[str]:
    """Render *spec* through real vl_convert and return every <text> node's content."""
    import html
    import re

    import vl_convert as vlc

    svg = vlc.vegalite_to_svg(spec)
    return [html.unescape(m) for m in re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S)]


class TestHouseRegister:
    """A mark label is a callout — it stands alone, so it always takes the
    narrative register, never the axis's own (analytic or narrative) ruler
    register, and never d3's raw, un-post-processed SI suffix."""

    def test_bar_value_label_on_millions_renders_narrative(self, make_chart) -> None:
        """1,200,000 on a vertical bar's value label renders '1.2mn', never
        the unspaced-capital '1.2M' d3 emits raw (the rejected quadrant)."""
        board_rs, board_ctx = _board_with_bar_labels_visible(True)
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        texts = _render_texts(spec)
        assert "1.2mn" in texts, (
            f"expected narrative '1.2mn' in rendered texts: {texts}"
        )
        assert "1.2M" not in texts, (
            f"unspaced-capital '1.2M' (raw d3, the rejected quadrant) must not "
            f"appear: {texts}"
        )

    def test_stack_total_label_matches_narrative_register(self, make_chart) -> None:
        """The stack-total label is a separate call site (bar.py's raw_total
        branch) — it must reach the same narrative register as per-segment
        labels, not keep its own."""
        board_rs, board_ctx = _board_with_bar_total_label_visible(True)
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            stack="zero",
            style={"bar": {"orientation": "vertical"}},
        )
        data = [
            {"month": "Jan", "channel": "Web", "revenue": 1_200_000},
            {"month": "Jan", "channel": "Mobile", "revenue": 600_000},
        ]
        resolve(chart, data, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, data, board_style=board_rs, chart_style_context=board_ctx
        )
        texts = _render_texts(spec)
        assert "1.8mn" in texts, f"expected narrative stack total '1.8mn': {texts}"
        assert "1.8M" not in texts, f"unspaced-capital total must not appear: {texts}"

    def test_authored_explicit_format_stays_verbatim(self, make_chart) -> None:
        """format: "$,.0f" is not SI-shaped, so it is emitted unchanged —
        non-SI formats have no analytic/narrative distinction at all."""
        board_rs, board_ctx = _board_with_bar_labels({"format": "$,.0f"})
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["encoding"]["text"].get("format") == "$,.0f"
        assert lyr["encoding"]["text"].get("field") == "revenue"
        texts = _render_texts(spec)
        assert "$1,200,000" in texts, f"authored format must render verbatim: {texts}"

    def test_authored_alias_still_resolves_and_stays_verbatim(self, make_chart) -> None:
        """An authored alias (format: currency_full) resolves through the theme
        alias table and, since it isn't SI-shaped, is emitted verbatim — no
        narrative wrapping applies."""
        board_rs, board_ctx = _board_with_bar_labels({"format": "currency_full"})
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["encoding"]["text"].get("format") == "$,.2f"
        assert lyr["encoding"]["text"].get("field") == "revenue"

    def test_authored_literal_si_format_takes_native_register(self, make_chart) -> None:
        """A literal SI spec opts OUT of the narrative register.

        The gate is alias-membership alone (resolve_label_format): "~s" is not
        a key in the theme's formats table, so it classifies native regardless
        of whether it was set chart-locally or at the board/theme level --
        there is no separate authorship signal."""
        board_rs, board_ctx = _board_with_bar_labels_visible(True)
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={
                "marks": {"bar": {"labels": {"format": "~s"}}},
                "bar": {"orientation": "vertical"},
            },
        )
        resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        # Native: text.format set directly, no calculate transform
        assert lyr["encoding"]["text"].get("format") == "~s", (
            f"chart-local literal '~s' must pass straight to Vega text.format: {lyr['encoding']['text']}"
        )
        texts = _render_texts(spec)
        assert "1.2M" in texts, f"expected raw d3 '1.2M', got {texts}"
        assert "1.2mn" not in texts, (
            f"narrative register must NOT fire for chart-local literal spec: {texts}"
        )

    def test_authored_alias_si_format_takes_narrative_register(
        self, make_chart
    ) -> None:
        """A SI format authored via a predefined name (format: compact -> .3~s)
        fires the narrative register -- the predefined name is a house choice,
        not a hand-written d3 spec, so the callout still reads '1.2mn'."""
        board_rs, board_ctx = _board_with_bar_labels({"format": "number"})
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        # Narrative: calculate transform, no direct text.format
        assert "format" not in lyr["encoding"]["text"], (
            f"predefined-resolved SI must use a calculate transform (narrative): {lyr['encoding']['text']}"
        )
        text_field = lyr["encoding"]["text"]["field"]
        # "number" resolves to ".3~s" (trim already set; round_aware_spec is no-op).
        expected = _house_register_expr("datum['revenue']", ResolvedFormat(spec=".3~s"))
        calc = next(
            (t for t in spec.get("transform", []) if t.get("as") == text_field), None
        )
        assert calc is not None and calc["calculate"] == expected
        texts = _render_texts(spec)
        assert "1.2mn" in texts and "1.2M" not in texts, f"got {texts}"

    def test_axis_authored_literal_si_label_inherits_native(self, make_chart) -> None:
        """When the y-axis format is authored as a literal SI spec via
        style.axis_y.labels.format, the bar label -- which inherits the axis
        format when its own format is unset -- also inherits native mode (raw
        d3 '1.2M'). The authorship is the axis's, so it propagates."""
        board_rs, board_ctx = _board_with_bar_labels({})  # label format not set
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={
                "axis_y": {"labels": {"format": "~s"}},
                "bar": {"orientation": "vertical"},
            },
        )
        resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["encoding"]["text"].get("format") == "~s", (
            f"label inherited from literal-authored axis must be native (text.format): "
            f"{lyr['encoding']['text']}"
        )
        texts = _render_texts(spec)
        assert "1.2M" in texts, f"expected raw d3 '1.2M', got {texts}"
        assert "1.2mn" not in texts, (
            f"narrative must not fire for literal axis format: {texts}"
        )

    def test_sub_thousand_values_render_unchanged(self, make_chart) -> None:
        """Below a thousand, d3's SI spec emits no suffix at all — the
        narrative calculate transform and the old literal text.format render
        the identical text, so this must be a pure no-op on the visible
        output (SAMPLE_DATA is 100/200, well under any suffix tier)."""
        board_rs, board_ctx = _board_with_bar_labels_visible(True)
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        texts = _render_texts(spec)
        assert "100" in texts and "200" in texts, f"got {texts}"

    def test_vl_convert_parity_still_holds(self) -> None:
        """The register wiring in this task must not disturb the parity
        pinned by test_numeral_vega_expr.py: Python format_d3 and the emitted
        Vega expression must still agree for every SI tier and notation."""
        from dbt_charts.core.text.format_d3 import format_d3

        expr = numeral_vega_expr("datum.value", ".3~s", "narrative")
        spec = {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": [{"value": 1_200_000.0}]},
            "transform": [{"calculate": expr, "as": "label"}],
            "mark": "text",
            "encoding": {"text": {"field": "label", "type": "nominal"}},
        }
        rendered = _render_texts(spec)[0]
        expected = format_d3(1_200_000.0, ".3~s", notation="narrative")
        assert rendered == expected == "1.2mn"

    def test_authored_analytic_notation_overrides_the_narrative_default(
        self, make_chart
    ) -> None:
        """A house predefined name normally paints the narrative register ('1.2mn', see
        the class docstring's default).
        """
        from dbt_charts.core.compile.models.primitives import FormatConfig

        board_rs, board_ctx = _board_with_bar_labels(
            {"format": FormatConfig(spec="number", notation="analytic")}
        )
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        texts = _render_texts(spec)
        assert "1.2 M" in texts, f"expected analytic '1.2 M' in rendered texts: {texts}"
        assert "1.2mn" not in texts, (
            f"narrative default must not override an authored analytic "
            f"notation: {texts}"
        )

    def test_authored_analytic_notation_overrides_narrative_with_an_affix(
        self, make_chart
    ) -> None:
        """Same override, through the affix-composing branch
        (_affixed_digits_expr) rather than the bare numeral_vega_expr one."""
        from dbt_charts.core.compile.models.primitives import FormatConfig

        board_rs, board_ctx = _board_with_bar_labels(
            {"format": FormatConfig(spec="number", prefix="€", notation="analytic")}
        )
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        texts = _render_texts(spec)
        assert "€1.2 M" in texts, f"expected analytic '€1.2 M': {texts}"
        assert "€1.2mn" not in texts, f"narrative must not override: {texts}"


def test_affixed_digits_expr_suppresses_sign_when_value_rounds_to_zero() -> None:
    """A negative value that rounds to zero under the digit spec must not paint a sign.
    ``(<v> < 0 ? sign.
    """
    from dbt_charts.core.compile.models.primitives import ResolvedFormat
    from dbt_charts.core.render.chart.vl_field_maps import _affixed_digits_expr

    fmt = ResolvedFormat(spec=",.0f", prefix="€", sign_placement="before_prefix")
    expr = _affixed_digits_expr("datum.value", fmt.spec, fmt, anchored=False)
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"value": -0.4}]},
        "transform": [{"calculate": expr, "as": "label"}],
        "mark": "text",
        "encoding": {"text": {"field": "label", "type": "nominal"}},
    }
    rendered = _render_texts(spec)[0]
    assert rendered == "€0", (
        f"expected no sign on a zero-rounded value, got {rendered!r}"
    )


# =============================================================================
# TestKpiProvenance -- board-default vs chart-local KPI format provenance.
# _resolve_kpi's format_native gate reads the merged style (chart-local
# overriding theme default), so a board-level alias and a chart-local literal
# spec are classified independently of which one authored it.
# =============================================================================


_KPI_MILLIONS_DATA = [{"revenue": 1_200_000}]


class TestKpiProvenance:
    """_resolve_kpi format_native gate: alias vs. literal determines the register.

    format_native=True fires when a literal d3 spec string appears in
    primary.value.format (not a theme alias). Alias -> house (narrative);
    non-alias literal -> native (raw d3).
    """

    def test_chart_local_si_format_is_native(self, make_chart) -> None:
        """Chart-local style.value.format = "~s" must set format_native=True.

        The author explicitly wrote a literal d3 spec in their chart YAML --
        that opts out of the narrative register (raw d3 output: "1.2M").
        """
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart("kpi", value="revenue", style={"value": {"format": "~s"}})
        resolved = resolve(chart, _KPI_MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.format_native is True, (
            "chart-local literal SI format must set format_native=True"
        )

    def test_kpi_native_svg_has_raw_d3_suffix(self, make_chart) -> None:
        """KPI with chart-local "~s" format renders SVG with raw d3 suffix "M".

        format_native=True routes through format_kpi_parts(native=True) which
        keeps the raw d3 suffix character. The rendered SVG must contain a tspan
        with "M" (capital, unspaced) -- the rejected native quadrant -- not "mn".
        """
        from dbt_charts.core.render.chart.kpi import render_kpi_svg

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart("kpi", value="revenue", style={"value": {"format": "~s"}})
        resolved = resolve(chart, _KPI_MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.format_native is True
        svg = render_kpi_svg(resolved, _KPI_MILLIONS_DATA, board_style=board_rs)
        # The suffix "M" must appear in a tspan; "mn" must not.
        # SVG path data contains literal "M" (moveto) commands, so checking
        # bare "M" in svg is vacuous. The KPI suffix appears in a <tspan> like
        # ">M<" -- check for that pattern to avoid false positives.
        import re

        tspan_texts = re.findall(r">([^<]+)<", svg)
        assert any(t.strip() == "M" for t in tspan_texts) and "mn" not in svg, (
            f"native KPI SVG must contain raw d3 'M' in a tspan, not 'mn': "
            f"snippet={svg[:500]!r}"
        )


# =============================================================================
# TestLineLabelTransformRegister -- line/area/scatter labels use calculate
# transform (not top-level spec.transforms) for the narrative register.
# =============================================================================


def _board_with_line_labels(updates: dict[str, object]):
    """Build a resolved style with line mark labels configured."""
    compiled = get_theme_style("clarity")
    new_labels = compiled.charts.marks.line.labels.model_copy(
        update={"visible": True, **updates}
    )
    new_line = compiled.charts.marks.line.model_copy(update={"labels": new_labels})
    new_marks = compiled.charts.marks.model_copy(update={"line": new_line})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


class TestLineLabelTransformRegister:
    """Line/area/scatter narrative labels use a text-layer-level transform
    (not a top-level spec.transforms like bar).  This test pins that wiring
    through the full render path."""

    def test_line_alias_axis_label_uses_calculate_transform(self, make_chart) -> None:
        """Line chart with axis alias format "number" on labels: narrative
        calculate transform must appear inside the text mark layer, not in
        the top-level spec.transforms."""
        board_rs, board_ctx = _board_with_line_labels({"format": "number"})
        chart = make_chart("line", x="month", y="revenue")
        resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None, "expected a text mark layer"
        # Narrative: calculate transform in the text layer (not top-level)
        lyr_transforms = lyr.get("transform", [])
        calc = next((t for t in lyr_transforms if "calculate" in t), None)
        assert calc is not None, (
            f"alias-format line label must have a narrative calculate transform "
            f"in the text layer; text layer transforms: {lyr_transforms}"
        )
        # Top-level spec.transforms must NOT carry this (bar does, line does not)
        top_transforms = spec.get("transform", [])
        top_calc = next(
            (
                t
                for t in top_transforms
                if "calculate" in t and "narrative" in t.get("calculate", "")
            ),
            None,
        )
        assert top_calc is None, (
            "line label narrative transform must be in text layer, not spec.transforms"
        )

    def test_line_chart_local_si_label_is_native(self, make_chart) -> None:
        """Chart-local style.marks.line.labels.format = "~s" opts out of narrative.

        Provenance: chart-authored literal -> is_house=False -> text.format emitted
        verbatim, no calculate transform.
        """
        board_rs, board_ctx = _board_with_line_labels({})  # visible only, no format
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style={"marks": {"line": {"labels": {"format": "~s"}}}},
        )
        resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        assert lyr["encoding"]["text"].get("format") == "~s", (
            "chart-local literal '~s' line label must pass direct to Vega text.format"
        )


# =============================================================================
# TestLineVsPointLabelSelection -- line vs point label selection in value_labels.
# _apply_line picks chart.style.line_label_is_house vs point_label_is_house
# based on which mark is visible; the two flags can diverge independently.
# =============================================================================


class TestLineVsPointLabelSelection:
    """line_label_is_house and point_label_is_house are independent fields.

    A line chart where only line labels are visible should use line_label_is_house
    for the rendered text mark; one where only point labels are visible should
    use point_label_is_house. This test shows the two can diverge.
    """

    def test_line_visible_point_not_selects_line_register(self, make_chart) -> None:
        """When line labels are visible but point labels are not, the text mark
        uses the line register -- and the resolved model exposes line_label_is_house
        distinct from point_label_is_house."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        # Line labels visible with alias (narrative), point labels hidden
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style={
                "marks": {
                    "line": {"labels": {"visible": True, "format": "number"}},
                    "point": {"labels": {"visible": False}},
                }
            },
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        # Line labels authored with alias -> narrative
        assert resolved.style.line_label_is_house is True, (
            "line label with alias 'number' must be narrative (is_house=True)"
        )
        # Point labels not visible -- field must exist with a real value (not vacuous hasattr)
        assert resolved.style.point_label_is_house is True, (
            "point labels not visible, but register field must exist; "
            "no chart-local point format authored -> narrative (is_house=True)"
        )

    def test_line_label_is_house_can_differ_from_point(self, make_chart) -> None:
        """line_label_is_house and point_label_is_house are computed independently.

        Line labels have alias format (narrative); point labels have literal format
        (native). The two resolved flags must diverge."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style={
                "marks": {
                    # Line labels: alias -> narrative
                    "line": {"labels": {"visible": True, "format": "number"}},
                    # Point labels: literal -> native
                    "point": {"labels": {"visible": True, "format": "~s"}},
                }
            },
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.line_label_is_house is True, (
            "line label with alias must be narrative"
        )
        assert resolved.style.point_label_is_house is False, (
            "point label with literal '~s' must be native"
        )


# =============================================================================
# TestHistogramNoValueLabels -- histogram endpoint_labels.visible is always
# False. _resolve_histogram computes label_is_house through
# _label_format_fallback exactly as _resolve_bar does (its bin count is still
# a value-label surface with an authorable format); total_label_is_house is
# hardcoded False, since a histogram has no stack totals.
# =============================================================================


class TestHistogramNoValueLabels:
    """Histogram never emits endpoint labels -- endpoint_labels.visible must be
    False (bar's own per-bin value labels are a separate surface; see the
    TestHistogramAliasLabelNoCrash class below for that one).

    The histogram bins x and aggregates y to a count; there is no per-row value
    for an endpoint rail to anchor to. This is enforced at resolve time by
    forcing endpoint_labels.visible=False regardless of what the bar theme
    provides.
    """

    def test_histogram_endpoint_labels_always_off(self, make_chart) -> None:
        """A histogram resolves with endpoint_labels.visible=False even if the
        bar theme has endpoint labels enabled."""
        board_rs, board_ctx = _board_with_bar_labels_visible(True)
        chart = make_chart("histogram", x="revenue")
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.endpoint_labels.visible is False, (
            "histogram must always resolve with endpoint_labels.visible=False"
        )

    def test_histogram_unformatted_label_is_house_narrative(self, make_chart) -> None:
        """Histogram with no authored label format uses narrative register.

        The y-axis is a Count axis whose format provenance comes from the theme
        default (not chart-local authored). label_is_house must be True -- same
        as bar with no authored format -- so labels render as e.g. '1.2mn' not '1.2M'.
        total_label_is_house stays False (histograms have no stack totals).
        The correct provenance value comes from _bake_cartesian_axes, the same
        source _resolve_bar uses."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart("histogram", x="revenue")
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.label_is_house is True, (
            "histogram with no authored format must be narrative (label_is_house=True)"
        )
        assert resolved.style.total_label_is_house is False


# =============================================================================
# Histogram label alias resolution: _resolve_histogram must call
# _label_format_fallback so alias strings resolve before reaching the render
# layer, which uses is_d3_si_spec on the format string.
# =============================================================================


class TestHistogramAliasLabelNoCrash:
    """Histogram routing through _apply_bar can have authored value labels.

    An authored theme alias (format: compact) must resolve through
    _label_format_fallback before reaching value_labels.py -- the render
    layer calls is_d3_si_spec on the format string, and an unresolved alias
    key like "number" is not a valid d3 format spec, causing a parse error.
    _resolve_histogram calls _label_format_fallback like _resolve_bar does.
    """

    def test_histogram_authored_alias_label_resolves_without_crash(
        self, make_chart
    ) -> None:
        """Histogram with style.marks.bar.labels.format: "number" must resolve
        and emit a VL spec without raising D3FormatError."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        board_rs, _ = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "histogram",
            x="revenue",
            style={"marks": {"bar": {"labels": {"visible": True, "format": "number"}}}},
        )
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        # Must not raise D3FormatError; spec must contain a text mark layer
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert _has_text_layer(spec), (
            "histogram with visible alias-format labels must emit a text layer"
        )

    def test_histogram_authored_alias_label_is_house_narrative(
        self, make_chart
    ) -> None:
        """After _label_format_fallback runs on histogram, alias format maps to
        label_is_house=True (narrative) -- an alias is the theme's house choice."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "histogram",
            x="revenue",
            style={"marks": {"bar": {"labels": {"visible": True, "format": "number"}}}},
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.label_is_house is True, (
            "histogram with alias-format label must compute label_is_house=True"
        )


# =============================================================================
# Overlay house-register label must preserve the base chart's authored sort.
# =============================================================================


class TestOverlayBarLabelSortPreservation:
    """A chart's authored sort must survive a house-register overlay label,
    not just its own base-chart rendering.

    ``_reconcile_x_domain`` pins an explicit, correctly-sorted
    ``scale.domain`` array on every layered categorical x. It has to carry
    the sort order itself: verified empirically that Vega-Lite's own native
    sort-by-field does not resolve across a shared scale that a
    transform-carrying label sublayer forked, pinned domain or not. This
    class verifies the actual rendered order via vl_convert, not just the
    emitted spec's structure — a structural-only check already let two
    prior variants of this regression ship.
    """

    def test_overlay_bar_house_label_sort_preserved(self) -> None:
        """Bar chart with sort: {by: revenue, order: desc} + an overlay bar
        layer carrying an alias-format label must render in the authored
        sort order, not query-row order — via an explicit scale.domain
        pinned to that same order (the label's calculate transform is what
        breaks VL's own native sort here).

        Data order: Jan (1.2M), Feb (2.4M), Mar (3.6M).
        Authored sort: desc by revenue → expected render order: Mar, Feb, Jan.
        """
        pytest.importorskip("vl_convert")
        import vl_convert as vlc

        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = {
            "type": "bar",
            "query": "q",
            "x": "month",
            "y": "revenue",
            "sort": {"by": "revenue", "order": "desc"},
            "layers": [
                {
                    "type": "bar",
                    "y": "revenue",
                    "style": {
                        "marks": {
                            "bar": {
                                "labels": {
                                    "visible": True,
                                    # "number" is a theme alias for "~s" (SI).
                                    # resolve_label_format("number", ...) → is_house=True
                                    # → calculate transform, which breaks
                                    # VL's own native sort-by-field.
                                    "format": "number",
                                }
                            }
                        }
                    },
                }
            ],
        }
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(compiled, _MILLIONS_DATA, chart_style_context=board_ctx)
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _MILLIONS_DATA}
        )
        vl = session.finalize_vl(spec)

        from ...conftest import chart_pane

        pane = chart_pane(vl)
        x_enc = pane.get("encoding", {}).get("x", {})
        assert x_enc.get("sort"), (
            f"expected an authored sort on the x encoding, got {x_enc.get('sort')!r}"
        )
        domain = x_enc.get("scale", {}).get("domain")
        assert domain == ["Mar", "Feb", "Jan"], (
            f"scale.domain must be explicitly pinned in desc-by-revenue "
            f"order (Mar/Feb/Jan) for this trigger — got {domain!r}."
        )

        svg = vlc.vegalite_to_svg(vl)
        # Vega renders x-axis category ticks as <text> elements in domain
        # order; find them by their known text content rather than position
        # (robust to layout/theme changes).
        month_positions = {
            month: svg.index(f">{month}<") for month in ("Jan", "Feb", "Mar")
        }
        rendered_order = sorted(month_positions, key=month_positions.get)
        assert rendered_order == ["Mar", "Feb", "Jan"], (
            f"rendered category order must follow desc-by-revenue sort "
            f"(Mar/Feb/Jan), got {rendered_order!r} — the authored sort is "
            "being silently dropped."
        )

    def test_overlay_bar_no_sort_preserves_query_order(self) -> None:
        """Without an authored sort, scale.domain stays in first-seen row order.

        This is the unchanged-behavior regression guard: _reconcile_x_domain
        pins a domain on any layered categorical x, but must not apply any
        sort when none was authored.
        """
        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = {
            "type": "bar",
            "query": "q",
            "x": "month",
            "y": "revenue",
            # No sort: authored — domain must stay in query-row order.
            "layers": [
                {
                    "type": "bar",
                    "y": "revenue",
                    "style": {
                        "marks": {
                            "bar": {"labels": {"visible": True, "format": "number"}}
                        }
                    },
                }
            ],
        }
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(compiled, _MILLIONS_DATA, chart_style_context=board_ctx)
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _MILLIONS_DATA}
        )
        vl = session.finalize_vl(spec)

        from ...conftest import chart_pane

        pane = chart_pane(vl)
        domain = pane.get("encoding", {}).get("x", {}).get("scale", {}).get("domain")
        assert domain == ["Jan", "Feb", "Mar"], (
            f"without an authored sort, domain must be in query-row order "
            f"(Jan/Feb/Mar), got {domain!r}"
        )


# =============================================================================
# An overlay layer's own labels.field must be validated against ITS OWN rows
# (own query, when diverging from the base's; the base's own already-
# normalized rows otherwise) — not skipped entirely, and not checked against
# the wrong side's columns.
# =============================================================================


class TestOverlayLabelFieldValidation:
    """``_collect_label_fields`` must walk ``chart.layers`` too, validating
    each layer's own ``labels.field`` against ITS OWN rows: the base's rows
    when the layer shares the base's query (the common, unauthored-``query:``
    shape — ``_resolve_layer_rows``'s own docstring calls this "nearly every
    layer"), or ``datasets[layer.query_name]`` when the layer's resolved
    query name diverges from the base's — the same rule
    ``render_cartesian_overlay``/``_resolve_layer_rows`` use.

    Parametrized over all four overlay layer families (bar/line/area/
    scatter) so every branch of ``_layer_label_slots``' per-family dispatch
    is actually exercised, not just the line branch.
    """

    _BASE_DATA = [
        {"month": "Jan", "revenue": 100, "revenue_caption": "Strong"},
        {"month": "Feb", "revenue": 200, "revenue_caption": "Growing"},
    ]
    _TARGET_DATA = [
        {"month": "Jan", "target": 90, "target_caption": "On track"},
        {"month": "Feb", "target": 95, "target_caption": "Ahead"},
    ]
    # Where each layer family's `labels:` config lives under `style.marks`.
    _LAYER_MARK_KEY = {"bar": "bar", "line": "line", "area": "line", "scatter": "point"}

    def _resolved_and_datasets(
        self, layer_type: str, label_field: str, *, diverges: bool = True
    ) -> tuple[Any, dict[str | None, list[dict[str, Any]]], Any]:
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.normalize.charts import normalize_chart

        registry = {
            "q": SqlQuery(sql="SELECT 1", source="test"),
            "targets": SqlQuery(sql="SELECT 1", source="test"),
        }
        layer_def: dict[str, Any] = {
            "type": layer_type,
            "y": "target",
            "style": {
                "marks": {
                    self._LAYER_MARK_KEY[layer_type]: {
                        "labels": {"visible": True, "field": label_field}
                    }
                }
            },
        }
        if diverges:
            layer_def["query"] = "targets"
        chart_def = {
            "type": "bar",
            "query": "q",
            "x": "month",
            "y": "revenue",
            "layers": [layer_def],
        }
        compiled = normalize_chart("v2chart", chart_def, registry, sources={})
        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        resolved = resolve(compiled, self._BASE_DATA, chart_style_context=board_ctx)
        datasets: dict[str | None, list[dict[str, Any]]] = {
            resolved.query_name: self._BASE_DATA
        }
        if diverges:
            datasets["targets"] = self._TARGET_DATA
        return resolved, datasets, board_rs

    @pytest.mark.parametrize("layer_type", ["bar", "line", "area", "scatter"])
    def test_diverging_layer_typo_field_raises(self, layer_type: str) -> None:
        """A typo'd overlay labels.field on a diverging-query layer must
        fail fast, matching the base-chart contract — not render a blank
        (or NaN, per the number-classifier fallback) with no diagnostic."""
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.render.chart.session import BoardRenderSession

        resolved, datasets, board_rs = self._resolved_and_datasets(
            layer_type, "does_not_exist", diverges=True
        )
        session = BoardRenderSession.create(board_rs)
        with pytest.raises(
            ChartDataError,
            match=(
                rf"labels\.field 'does_not_exist' on overlay layer 0 "
                rf"\({layer_type}, query='targets'\) names a column not present"
            ),
        ):
            session.emit_chart(resolved, _DEFAULT_BOX, datasets)

    @pytest.mark.parametrize("layer_type", ["bar", "line", "area", "scatter"])
    def test_non_diverging_layer_typo_field_raises(self, layer_type: str) -> None:
        """The common, unauthored-``query:`` shape: the layer's resolved
        query name is baked to the base's own (``layer.query_name ==
        chart.query_name``), so it validates against the base's own ``data``
        rather than a ``datasets`` lookup. This is the branch every prior
        test in this class skipped by always authoring ``query: targets`` —
        it is the majority case per ``_resolve_layer_rows``'s own docstring,
        not an edge case, so a typo here must fail exactly as loudly."""
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.render.chart.session import BoardRenderSession

        resolved, datasets, board_rs = self._resolved_and_datasets(
            layer_type, "does_not_exist", diverges=False
        )
        assert resolved.layers[0].query_name == resolved.query_name, (
            "precondition: this test only proves something about the "
            "non-diverging branch if the layer's query actually matches "
            "the base's"
        )
        session = BoardRenderSession.create(board_rs)
        with pytest.raises(
            ChartDataError,
            match=(
                rf"labels\.field 'does_not_exist' on overlay layer 0 "
                rf"\({layer_type}, query='q'\) names a column not present"
            ),
        ):
            session.emit_chart(resolved, _DEFAULT_BOX, datasets)

    def test_field_present_only_in_layer_own_dataset_is_valid(self) -> None:
        """A field that exists only in the layer's OWN (diverging) dataset —
        not the base's — must validate clean: the layer's rows are what it
        actually renders against."""
        from dbt_charts.core.render.chart.session import BoardRenderSession

        resolved, datasets, board_rs = self._resolved_and_datasets(
            "line", "target_caption", diverges=True
        )
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(resolved, _DEFAULT_BOX, datasets)
        vl = session.finalize_vl(spec)
        overlay_text_layers = [
            lyr
            for lyr in vl.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert overlay_text_layers, "expected the overlay layer's own text layer"

    def test_field_present_only_in_base_dataset_raises_on_diverging_layer(self) -> None:
        """A field that exists only in the BASE's dataset, authored on a
        layer whose own query diverges from the base's, must raise — the
        layer never sees that column at render time, so it is the likelier
        authoring mistake, not a cross-dataset union."""
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.render.chart.session import BoardRenderSession

        resolved, datasets, board_rs = self._resolved_and_datasets(
            "line", "revenue_caption", diverges=True
        )
        session = BoardRenderSession.create(board_rs)
        with pytest.raises(
            ChartDataError,
            match=(
                r"labels\.field 'revenue_caption' on overlay layer 0 "
                r"\(line, query='targets'\) names a column not present"
            ),
        ):
            session.emit_chart(resolved, _DEFAULT_BOX, datasets)


# =============================================================================
# Overlay label render-level proof: is_house=False (native) must propagate
# from the resolved layer model to the emitted VL encoding.
# =============================================================================


class TestOverlayRenderLevelIsHouse:
    """Render-level (VL spec) proof that is_house=False (native) propagates
    from the resolved layer model to the emitted VL encoding.

    The resolve-model tests (TestOverlayLayerRegister etc.) check
    layer.label_is_house. This class verifies that label_is_house=False
    actually produces encoding.text.format = "<spec>" with no calculate
    transform on the overlay label sublayer.
    """

    def test_overlay_native_si_label_has_format_not_calculate(self, make_chart) -> None:
        """Bar overlay layer with marks.bar.labels.format: "~s" (literal, native).

        In the alias-gate design, the literal "~s" spec is not an alias key, so
        resolve_label_format("~s", formats) -> is_house=False. The VL text layer
        must carry encoding.text.format="~s" and no calculate transform.

        The format is set directly on marks.bar.labels.format in the overlay
        layer's style -- the alias-vs-literal gate checks this raw string against
        the formats dict to decide house vs. native.
        """
        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        _DUMMY_QUERY_REG: dict[str, Any] = {
            "q": __import__(
                "dbt_charts.core.compile.models.query.normalized",
                fromlist=["SqlQuery"],
            ).SqlQuery(sql="SELECT 1", source="test")
        }

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = {
            "type": "bar",
            "query": "q",
            "x": "month",
            "y": "revenue",
            "layers": [
                {
                    "type": "bar",
                    "y": "revenue",
                    # Literal SI spec on mark labels directly -> native (no calculate)
                    "style": {
                        "marks": {"bar": {"labels": {"visible": True, "format": "~s"}}}
                    },
                }
            ],
        }
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(compiled, _MILLIONS_DATA, chart_style_context=board_ctx)
        # Verify resolve-level: literal "~s" not an alias -> native
        assert resolved.layers
        assert resolved.layers[0].label_is_house is False, (
            "literal ~s on overlay mark label must resolve to native (label_is_house=False)"
        )
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _MILLIONS_DATA}
        )
        vl = session.finalize_vl(spec)

        from ...conftest import chart_pane

        pane = chart_pane(vl)
        overlay_text_layers = [
            lyr
            for lyr in pane.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert overlay_text_layers, "expected at least one overlay text layer"
        # For is_house=False: text.format must be set, no calculate transform
        lyr = overlay_text_layers[0]
        assert lyr["encoding"]["text"].get("format") == "~s", (
            f"native overlay label must have text.format='~s', "
            f"not a calculate transform: encoding={lyr['encoding']}"
        )
        assert "transform" not in lyr, (
            f"native overlay label must have NO calculate transform: {lyr.get('transform')}"
        )


# =============================================================================
# Area and scatter base-chart chart-local-literal SI format tests.
# =============================================================================


class TestAreaScatterLiteralSiFormat:
    """Area and scatter charts with chart-local literal SI format on their mark
    labels must use native register (label_is_house=False).
    """

    def test_area_chart_local_literal_si_label_is_native(self, make_chart) -> None:
        """Area chart with style.marks.line.labels.format: "~s" (chart-local
        literal) must compute label_is_house=False (native)."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "area",
            x="month",
            y="revenue",
            style={
                "marks": {"line": {"labels": {"visible": True, "format": "~s"}}},
            },
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.label_is_house is False, (
            "area chart with chart-local literal ~s must be native (label_is_house=False)"
        )

    def test_scatter_chart_local_literal_si_label_is_native(self, make_chart) -> None:
        """Scatter chart with style.marks.point.labels.format: "~s" (chart-local
        literal) must compute label_is_house=False (native)."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "scatter",
            x="month",
            y="revenue",
            style={
                "marks": {"point": {"labels": {"visible": True, "format": "~s"}}},
            },
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.label_is_house is False, (
            "scatter chart with chart-local literal ~s must be native (label_is_house=False)"
        )


# =============================================================================
# Overlay layer with temporal or quantitative x must render correctly:
# the label sublayer must not inject a hardcoded nominal x type.
# =============================================================================

_DATE_DATA = [
    {"date": "2025-01-01", "revenue": 1_200_000},
    {"date": "2025-02-01", "revenue": 2_400_000},
    {"date": "2025-03-01", "revenue": 3_600_000},
]

_QTY_DATA = [
    {"qty": 1.0, "revenue": 1_200_000},
    {"qty": 2.0, "revenue": 2_400_000},
    {"qty": 3.0, "revenue": 3_600_000},
]

_DUMMY_QUERY_REG: dict[str, Any] = {
    "q": __import__(
        "dbt_charts.core.compile.models.query.normalized",
        fromlist=["SqlQuery"],
    ).SqlQuery(sql="SELECT 1", source="test")
}


def _overlay_chart_def(
    chart_type: str, x_field: str, layer_type: str, mark_key: str
) -> dict[str, Any]:
    return {
        "type": chart_type,
        "query": "q",
        "x": x_field,
        "y": "revenue",
        "layers": [
            {
                "type": layer_type,
                "y": "revenue",
                "style": {"marks": {mark_key: {"labels": {"visible": True}}}},
            }
        ],
    }


class TestOverlayLayerXTypePreservation:
    """Overlay label x-channel injection must use the base chart's real x type.

    Injecting a hardcoded nominal type on a temporal x causes Vega-Lite to
    raise "Only time and utc scales accept interval strings" internally, which
    vl_convert swallows, producing a silently blank chart with zero <text>
    nodes. For quantitative x a spurious third axis appears. Neither error is
    visible at the resolve layer -- only caught via vl_convert render.
    """

    def test_line_overlay_label_on_temporal_x_renders_text(self) -> None:
        """Line chart with ISO-date temporal x + labeled line overlay must
        produce real <text> nodes. A nominal x-type injection causes blank charts."""
        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = _overlay_chart_def("line", "date", "line", "line")
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(compiled, _DATE_DATA, chart_style_context=board_ctx)
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _DATE_DATA}
        )
        vl = session.finalize_vl(spec)
        texts = _render_texts(vl)
        assert texts, (
            "line overlay with temporal x must render non-empty <text> nodes; "
            "got 0 -- the hardcoded nominal x-type injection blanked the chart"
        )

    def test_area_overlay_label_on_temporal_x_renders_text(self) -> None:
        """Area chart with ISO-date x + labeled area overlay must produce text."""
        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = _overlay_chart_def("area", "date", "area", "line")
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(compiled, _DATE_DATA, chart_style_context=board_ctx)
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _DATE_DATA}
        )
        vl = session.finalize_vl(spec)
        texts = _render_texts(vl)
        assert texts, "area overlay with temporal x must render non-empty <text> nodes"

    def test_scatter_overlay_label_on_quantitative_x_renders_text(self) -> None:
        """Scatter chart with quantitative x + labeled scatter overlay must render
        real text nodes via vl_convert (not blank). The old design injected a
        hardcoded nominal x type on the label sublayer, producing a spurious third
        axis and potentially blanking the chart. The label sublayer carries no x
        encoding of its own and must not have one injected.
        This test verifies the rendered SVG contains actual text nodes."""
        pytest.importorskip("vl_convert")
        import vl_convert as vlc

        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = _overlay_chart_def("scatter", "qty", "scatter", "point")
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(compiled, _QTY_DATA, chart_style_context=board_ctx)
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _QTY_DATA}
        )
        vl = session.finalize_vl(spec)
        # vl_convert renders the spec; any injected-wrong-type crash would appear
        # as an empty SVG or a vl_convert exception.
        svg = vlc.vegalite_to_svg(vl)
        assert "<text" in svg, (
            "scatter overlay on quantitative x must produce <text> nodes; "
            "got empty SVG -- likely a wrong x-type injection crash"
        )


# =============================================================================
# Histogram labels use the same narrative/native gate as bar labels.
# The three-case matrix: default -> narrative, alias -> narrative, literal -> native.
# =============================================================================


class TestHistogramLabelProvenance:
    """Histogram labels respect the same alias-vs-literal gate as bar labels.

    _resolve_histogram must use the real axis provenance from _bake_cartesian_axes,
    not a hardcoded authored flag -- the same pattern _resolve_bar uses. This
    ensures a theme-default format is not misclassified as a chart-author opt-out.
    """

    def test_histogram_default_format_is_narrative_like_bar(self, make_chart) -> None:
        """Histogram with labels.visible=True and no authored format must get
        label_is_house=True (narrative) -- same as a bar chart in the same theme.

        The theme default format is an alias, so label_is_house=True (narrative).
        A hardcoded authored=True flag would misclassify it as a literal opt-out."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_hist = make_chart(
            "histogram",
            x="revenue",
            style={"marks": {"bar": {"labels": {"visible": True}}}},
        )
        chart_bar = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={"marks": {"bar": {"labels": {"visible": True}}}},
        )
        res_hist = resolve(chart_hist, _MILLIONS_DATA, chart_style_context=board_ctx)
        res_bar = resolve(chart_bar, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert res_bar.style.label_is_house is True, "bar reference must be narrative"
        assert res_hist.style.label_is_house is True, (
            "histogram with no authored format must match bar: narrative (is_house=True)"
        )

    def test_histogram_authored_alias_label_is_narrative(self, make_chart) -> None:
        """Histogram with alias-format label stays narrative (is_house=True).
        This tests the same path as TestHistogramAliasLabelNoCrash but checks
        the three-case matrix row for the alias case."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "histogram",
            x="revenue",
            style={"marks": {"bar": {"labels": {"visible": True, "format": "number"}}}},
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.label_is_house is True, (
            "histogram with alias label must be narrative (is_house=True)"
        )

    def test_histogram_authored_literal_si_label_is_native(self, make_chart) -> None:
        """Histogram with a chart-local literal SI format opts out of narrative."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "histogram",
            x="revenue",
            style={"marks": {"bar": {"labels": {"visible": True, "format": "~s"}}}},
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.label_is_house is False, (
            "histogram with literal '~s' label must be native (is_house=False)"
        )


class TestAreaScatterIsHouseNarrative:
    """Area and scatter base charts with default/alias SI format must be narrative.

    These tests assert is_house=True (narrative) -- deleting the label_is_house
    wiring in area.py/scatter.py must cause them to fail.
    """

    def test_area_default_si_format_is_narrative(self, make_chart) -> None:
        """Area chart with visible labels and no authored format: label_is_house=True."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "area",
            x="month",
            y="revenue",
            style={"marks": {"line": {"labels": {"visible": True}}}},
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.label_is_house is True, (
            "area with visible labels and no authored format must be narrative"
        )

    def test_area_alias_si_format_is_narrative(self, make_chart) -> None:
        """Area chart with alias-format labels: label_is_house=True."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "area",
            x="month",
            y="revenue",
            style={
                "marks": {"line": {"labels": {"visible": True, "format": "number"}}}
            },
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.label_is_house is True, (
            "area with alias-format label must be narrative"
        )

    def test_scatter_default_si_format_is_narrative(self, make_chart) -> None:
        """Scatter chart with visible labels and no authored format: label_is_house=True."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "scatter",
            x="month",
            y="revenue",
            style={"marks": {"point": {"labels": {"visible": True}}}},
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.label_is_house is True, (
            "scatter with visible labels and no authored format must be narrative"
        )

    def test_scatter_alias_si_format_is_narrative(self, make_chart) -> None:
        """Scatter chart with alias-format labels: label_is_house=True."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "scatter",
            x="month",
            y="revenue",
            style={
                "marks": {"point": {"labels": {"visible": True, "format": "number"}}}
            },
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.style.label_is_house is True, (
            "scatter with alias-format label must be narrative"
        )


class TestAreaScatterOverlayRenderLevel:
    """Area and scatter overlay label arms of _build_layer_label_specs must
    produce VL text layers.

    ResolvedAreaLayer and ResolvedScatterLayer branches in
    _build_layer_label_specs each need at least one render-level test.
    """

    def test_area_overlay_with_visible_labels_emits_text_layer(self) -> None:
        """Area-type overlay with labels.visible=True must produce a VL text layer."""
        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = _overlay_chart_def("area", "month", "area", "line")
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(compiled, _MILLIONS_DATA, chart_style_context=board_ctx)
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _MILLIONS_DATA}
        )
        vl = session.finalize_vl(spec)
        pane = chart_pane(vl)
        text_layers = [
            lyr
            for lyr in pane.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers, (
            "area overlay with visible labels must produce at least one text layer"
        )

    def test_scatter_overlay_with_visible_labels_emits_text_layer(self) -> None:
        """Scatter-type overlay with labels.visible=True must produce a VL text layer."""
        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = _overlay_chart_def("scatter", "month", "scatter", "point")
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(compiled, _MILLIONS_DATA, chart_style_context=board_ctx)
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _MILLIONS_DATA}
        )
        vl = session.finalize_vl(spec)
        pane = chart_pane(vl)
        text_layers = [
            lyr
            for lyr in pane.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers, (
            "scatter overlay with visible labels must produce at least one text layer"
        )


class TestSharedAxisOverlayWithMarkFormat:
    """A layer authoring its own marks.<mark>.labels.format resolves that
    format directly through resolve_label_format, independent of axis_y.
    """

    @pytest.mark.parametrize(
        ("chart_type", "layer_type", "mark_key"),
        [
            ("bar", "bar", "bar"),
            ("line", "line", "line"),
            ("area", "area", "line"),
            ("scatter", "scatter", "point"),
        ],
    )
    def test_shared_axis_overlay_with_authored_mark_alias_is_narrative(
        self, make_chart, chart_type: str, layer_type: str, mark_key: str
    ) -> None:
        """Overlay layer with no axis_y, mark labels authored with alias: narrative.

        The base chart has no explicit axis format (inherits SI default from theme).
        The overlay layer authors marks.<mark>.labels.format: "number" (alias).
        Result must be label_is_house=True (narrative) because alias -> house rules.
        """
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            chart_type,
            x="month",
            y="revenue",
            layers=[
                {
                    "type": layer_type,
                    "y": "revenue",
                    "style": {
                        "marks": {
                            mark_key: {"labels": {"visible": True, "format": "number"}}
                        }
                    },
                }
            ],
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.layers
        assert resolved.layers[0].label_is_house is True, (
            f"{layer_type} overlay with alias mark format must be narrative"
        )

    @pytest.mark.parametrize(
        ("chart_type", "layer_type", "mark_key"),
        [
            ("bar", "bar", "bar"),
            ("line", "line", "line"),
            ("area", "area", "line"),
            ("scatter", "scatter", "point"),
        ],
    )
    def test_shared_axis_overlay_with_authored_mark_literal_is_native(
        self, make_chart, chart_type: str, layer_type: str, mark_key: str
    ) -> None:
        """Overlay layer with no axis_y, mark labels authored with literal "~s": native."""
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            chart_type,
            x="month",
            y="revenue",
            layers=[
                {
                    "type": layer_type,
                    "y": "revenue",
                    "style": {
                        "marks": {
                            mark_key: {"labels": {"visible": True, "format": "~s"}}
                        }
                    },
                }
            ],
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.layers
        assert resolved.layers[0].label_is_house is False, (
            f"{layer_type} overlay with literal mark format must be native"
        )


# =============================================================================
# Overlay label register: alias membership is the sole gate.
# =============================================================================


class TestOverlayAliasGateRegister:
    """Overlay layer label register is decided purely by alias membership.

    - No format → stays unformatted, label_is_house=False (no register question)
    - Alias format (marks.<mark>.labels.format = "number") → narrative
    - Literal SI format (marks.<mark>.labels.format = "~s") → native

    These hold regardless of whether the format was set by the chart author or
    inherited via style cascade from the base chart.
    """

    @pytest.mark.parametrize(
        ("chart_type", "layer_type", "mark_key"),
        [
            ("bar", "bar", "bar"),
            ("line", "line", "line"),
            ("area", "area", "line"),
            ("scatter", "scatter", "point"),
        ],
    )
    def test_unformatted_overlay_stays_unformatted(
        self, make_chart, chart_type: str, layer_type: str, mark_key: str
    ) -> None:
        """Overlay layer with no own mark format: label_is_house=False.

        An unformatted layer label never inherits any axis's format — no
        format means no register question, so label_is_house stays False.
        """
        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            chart_type,
            x="month",
            y="revenue",
            # Base chart axis has the theme's default SI alias format.
            # In new design, layers do NOT inherit this axis format.
            layers=[
                {
                    "type": layer_type,
                    "y": "revenue",
                    "style": {"marks": {mark_key: {"labels": {"visible": True}}}},
                }
            ],
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.layers
        assert resolved.layers[0].label_is_house is False, (
            f"{layer_type} overlay with no format must have label_is_house=False "
            f"(unformatted, no register question)"
        )


class TestOverlayLineVsPointRenderLevel:
    """A line-type overlay layer's is_house must pick line vs point exactly as
    _build_layer_label_specs does: line_label_is_house if line_mark.labels is
    visible, else point_label_is_house -- mirroring _layers.py's
    `layer_label_is_house = line_label_is_house if line_labels.visible is True
    else point_label_is_house`.

    TestLineVsPointLabelRenderLevel proves this ternary at the base-chart
    level; this class proves the overlay-layer analog, with the two mark
    labels authoring divergent formats (alias vs literal) so a flattened
    ternary is render-detectable, not just resolve-level.
    """

    def test_overlay_line_labels_visible_alias_has_calculate(self, make_chart) -> None:
        """Overlay line label visible + alias, point label literal but hidden:
        must use line_label_is_house=True (calculate transform).

        If the ternary always used point_label_is_house (False, literal "~s"),
        this would emit text.format instead of a calculate transform.
        """
        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            layers=[
                {
                    "type": "line",
                    "y": "revenue",
                    "style": {
                        "marks": {
                            "line": {"labels": {"visible": True, "format": "number"}},
                            "point": {"labels": {"visible": False, "format": "~s"}},
                        }
                    },
                }
            ],
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.layers and resolved.layers[0].label_is_house is True, (
            "overlay line labels visible with alias format must resolve "
            "label_is_house=True"
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        overlay_text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert overlay_text_layers, (
            "overlay with visible line labels must emit text layer"
        )
        lyr = overlay_text_layers[0]
        transforms = lyr.get("transform", [])
        assert any("calculate" in t for t in transforms), (
            f"overlay line alias label must have calculate transform "
            f"(line_label_is_house=True); got transforms={transforms!r}. "
            f"A flattened ternary using point_label_is_house=False would have none."
        )

    def test_overlay_point_labels_visible_literal_has_format(self, make_chart) -> None:
        """Overlay line label hidden but alias, point label visible + literal:
        must use point_label_is_house=False (text.format, no calculate).

        If the ternary always used line_label_is_house (True, alias "number"),
        this would emit a calculate transform instead of text.format.
        """
        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            layers=[
                {
                    "type": "line",
                    "y": "revenue",
                    "style": {
                        "marks": {
                            "line": {"labels": {"visible": False, "format": "number"}},
                            "point": {"labels": {"visible": True, "format": "~s"}},
                        }
                    },
                }
            ],
        )
        resolved = resolve(chart, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.layers and resolved.layers[0].label_is_house is False, (
            "overlay point labels visible with literal format must resolve "
            "label_is_house=False"
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        overlay_text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert overlay_text_layers, (
            "overlay with visible point labels must emit text layer"
        )
        lyr = overlay_text_layers[0]
        assert lyr.get("encoding", {}).get("text", {}).get("format") == "~s", (
            f"overlay point literal label must have text.format='~s' "
            f"(point_label_is_house=False); got {lyr.get('encoding', {}).get('text')!r}. "
            f"A flattened ternary using line_label_is_house=True would have a "
            f"calculate transform instead."
        )
        assert "transform" not in lyr, (
            f"overlay point literal label must NOT have a calculate transform: "
            f"{lyr.get('transform')!r}"
        )


# =============================================================================
# Authored categorical-domain sort: numeric coercion and aggregate choice.
# =============================================================================

# Revenue as numeric strings — the real warehouse row shape from some adapters.
_STRING_REVENUE_DATA = [
    {"month": "Jan", "revenue": "1200000"},
    {"month": "Feb", "revenue": "2400000"},
    {"month": "Mar", "revenue": "3600000"},
]

# Multi-row-per-x data: two series per month.  Sum-desc and min-desc give
# different orderings, exposing the wrong-aggregate bug.
#   Jan: A=3M B=100K -> sum=3.1M, min=100K
#   Feb: A=1.5M B=1.5M -> sum=3M,   min=1.5M
#   Mar: A=800K B=2M  -> sum=2.8M,  min=800K
# Sum-desc: Jan, Feb, Mar   Min-desc: Feb, Mar, Jan
_MULTI_ROW_SORT_DATA = [
    {"month": "Jan", "series": "A", "revenue": 3_000_000},
    {"month": "Jan", "series": "B", "revenue": 100_000},
    {"month": "Feb", "series": "A", "revenue": 1_500_000},
    {"month": "Feb", "series": "B", "revenue": 1_500_000},
    {"month": "Mar", "series": "A", "revenue": 800_000},
    {"month": "Mar", "series": "B", "revenue": 2_000_000},
]


def _overlay_chart_def_with_sort(
    sort_field: str, sort_order: str, base_data_has_color: bool = False
) -> dict[str, Any]:
    chart: dict[str, Any] = {
        "type": "bar",
        "query": "q",
        "x": "month",
        "y": "revenue",
        "sort": {"by": sort_field, "order": sort_order},
        "layers": [
            {
                "type": "bar",
                "y": "revenue",
                "style": {
                    "marks": {
                        "bar": {
                            "labels": {
                                "visible": True,
                                "format": "number",
                            }
                        }
                    }
                },
            }
        ],
    }
    if base_data_has_color:
        chart["color"] = "series"
    return chart


class TestOverlayBarSortNumericStrings:
    """The shared categorical-domain order handles numeric-string measures.

    Some warehouse adapters return measure columns as strings ("1200000").
    domain_sort_aggregates uses coerce_numeric_cell for type-safe coercion,
    so string-valued revenue rows are included in the sort aggregate.
    """

    def test_sort_with_string_revenue_values_desc(self) -> None:
        """Bar overlay with numeric string revenue, desc sort: must render Mar/Feb/Jan.

        coerce_numeric_cell("1200000") = 1200000.0, so string revenue values
        are included in the sort aggregate. Expected: Mar (3.6M), Feb (2.4M), Jan (1.2M).
        """
        pytest.importorskip("vl_convert")
        import vl_convert as vlc

        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = _overlay_chart_def_with_sort("revenue", "desc")
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(
            compiled, _STRING_REVENUE_DATA, chart_style_context=board_ctx
        )
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _STRING_REVENUE_DATA}
        )
        vl = session.finalize_vl(spec)

        pane = chart_pane(vl)
        domain = pane.get("encoding", {}).get("x", {}).get("scale", {}).get("domain")
        assert domain == ["Mar", "Feb", "Jan"], (
            f"desc-by-revenue with string values must sort Mar/Feb/Jan; "
            f"got {domain!r} -- numeric-string revenue values must be coerced, not dropped"
        )

        svg = vlc.vegalite_to_svg(vl)
        month_positions = {
            month: svg.index(f">{month}<") for month in ("Jan", "Feb", "Mar")
        }
        rendered_order = sorted(month_positions, key=month_positions.get)
        assert rendered_order == ["Mar", "Feb", "Jan"], (
            f"rendered order must follow desc-by-revenue (Mar/Feb/Jan), "
            f"got {rendered_order!r}"
        )


class TestOverlayBarSortAggregateMatchesUnlabeled:
    """A house-register overlay label must not change the base chart's own
    category order.

    ``rendered_x_domain`` pins an explicit domain (the label sublayer's
    calculate transform is what breaks Vega-Lite's native sort-by-field
    across the shared scale). That pinned domain must sort by the aggregate
    the encoding's own sort pins — ``min`` for a grouped bar, which stacks
    nothing and so orders each category by the measure's own value. With
    multi-row-per-x data, sum and min give different category orders, so this
    is the case that would catch a wrong aggregate: render the identical chart
    with and without the overlay label sublayer and assert the rendered order
    matches.
    """

    def test_multi_row_per_x_order_unchanged_by_overlay_label(self) -> None:
        """Non-stacked bar with a multi-row-per-x color series and desc sort:
        adding an overlay house-register label must not reorder the categories.

        Data (see _MULTI_ROW_SORT_DATA):
          sum-desc: Jan (3.1M), Feb (3M), Mar (2.8M) -> Jan/Feb/Mar
          min-desc: Feb (1.5M), Mar (800K), Jan (100K) -> Feb/Mar/Jan

        The unlabeled chart renders the min order from its own pinned ``op``;
        a wrong aggregate in the label-present path would flip it to the sum
        order instead.
        """
        pytest.importorskip("vl_convert")
        import vl_convert as vlc

        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))

        def render_order(chart_def: dict[str, Any]) -> list[str]:
            compiled = normalize_chart(
                "v2chart", chart_def, _DUMMY_QUERY_REG, sources={}
            )
            resolved = resolve(
                compiled, _MULTI_ROW_SORT_DATA, chart_style_context=board_ctx
            )
            session = BoardRenderSession.create(board_rs)
            spec = session.emit_chart(
                resolved, _DEFAULT_BOX, {resolved.query_name: _MULTI_ROW_SORT_DATA}
            )
            vl = session.finalize_vl(spec)
            svg = vlc.vegalite_to_svg(vl)
            month_positions = {
                month: svg.index(f">{month}<") for month in ("Jan", "Feb", "Mar")
            }
            return sorted(month_positions, key=month_positions.get)

        labeled_def = _overlay_chart_def_with_sort(
            "revenue", "desc", base_data_has_color=True
        )
        unlabeled_def = {k: v for k, v in labeled_def.items() if k != "layers"}

        unlabeled_order = render_order(unlabeled_def)
        labeled_order = render_order(labeled_def)

        assert unlabeled_order == ["Feb", "Mar", "Jan"], (
            f"min-desc without any overlay label must give Feb/Mar/Jan, "
            f"got {unlabeled_order!r}"
        )
        assert labeled_order == unlabeled_order, (
            f"adding an overlay house-register label must not change the "
            f"category order: unlabeled={unlabeled_order!r}, "
            f"labeled={labeled_order!r}"
        )


# =============================================================================
# KPI format_native gate: gated on the spec string, not on FormatConfig existence.
# =============================================================================


class TestKpiPrefixSuffixOnlyNotNative:
    """_resolve_kpi: a FormatConfig with only prefix or suffix (spec=None) must
    NOT set format_native=True.

    format_native is gated on the spec string being present, not on the
    FormatConfig object existing. FormatConfig(prefix="$") has no spec and must
    follow the "no format authored" path (format_native=False).
    """

    def test_kpi_prefix_only_is_not_native(self, make_chart) -> None:
        """FormatConfig(prefix='$') with no spec: format_native must be False."""
        from dbt_charts.core.compile.models.primitives import FormatConfig

        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "kpi",
            value="revenue",
            style={"value": {"format": FormatConfig(prefix="$")}},
        )
        resolved = resolve(chart, _KPI_MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.format_native is False, (
            "FormatConfig(prefix='$') with no spec must not set format_native=True "
            "(no spec authored -> house default, not native)"
        )

    def test_kpi_suffix_only_is_not_native(self, make_chart) -> None:
        """FormatConfig(suffix=' ARR') with no spec: format_native must be False."""
        from dbt_charts.core.compile.models.primitives import FormatConfig

        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "kpi",
            value="revenue",
            style={"value": {"format": FormatConfig(suffix=" ARR")}},
        )
        resolved = resolve(chart, _KPI_MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.format_native is False, (
            "FormatConfig(suffix=' ARR') with no spec must not set format_native=True"
        )

    def test_kpi_prefix_suffix_with_spec_is_native_when_literal(
        self, make_chart
    ) -> None:
        """FormatConfig(spec='~s', prefix='$') -- spec present, literal -> native."""
        from dbt_charts.core.compile.models.primitives import FormatConfig

        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "kpi",
            value="revenue",
            style={"value": {"format": FormatConfig(spec="~s", prefix="$")}},
        )
        resolved = resolve(chart, _KPI_MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.format_native is True, (
            "FormatConfig(spec='~s', prefix='$') -- literal SI spec -> native"
        )


# =============================================================================
# Area and scatter base-chart register verified at the render (emit) level.
# =============================================================================


class TestAreaScatterRenderLevelRegister:
    """Area and scatter base charts: is_house flag must reach the emitted VL spec.

    TestAreaScatterIsHouseNarrative and TestAreaScatterLiteralSiFormat cover the
    resolved model level (resolved.style.label_is_house). These tests add the
    render-level proof: the emitted text layer must carry a calculate transform
    when is_house=True (narrative), and text.format when is_house=False (native).
    """

    def test_area_alias_label_has_calculate_transform(self, make_chart) -> None:
        """Area chart with alias-format label: emitted text layer must carry calculate."""
        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "area",
            x="month",
            y="revenue",
            style={
                "marks": {"line": {"labels": {"visible": True, "format": "number"}}}
            },
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers, "area with visible labels must produce a text layer"
        lyr = text_layers[0]
        transforms = lyr.get("transform", [])
        assert any("calculate" in t for t in transforms), (
            f"area alias label must have a calculate transform (narrative); "
            f"got transforms={transforms!r}"
        )
        assert "format" not in lyr.get("encoding", {}).get("text", {}), (
            "area alias label must NOT have bare text.format (that is native)"
        )

    def test_area_literal_si_label_has_format_not_calculate(self, make_chart) -> None:
        """Area chart with literal SI label: text.format set, no calculate."""
        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "area",
            x="month",
            y="revenue",
            style={"marks": {"line": {"labels": {"visible": True, "format": "~s"}}}},
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers, "area with visible labels must produce a text layer"
        lyr = text_layers[0]
        text_enc = lyr.get("encoding", {}).get("text", {})
        assert text_enc.get("format") == "~s", (
            f"area literal '~s' label must carry text.format='~s'; got {text_enc!r}"
        )
        assert not lyr.get("transform"), (
            "area literal label must NOT have a calculate transform"
        )

    def test_scatter_alias_label_has_calculate_transform(self, make_chart) -> None:
        """Scatter chart with alias-format label: emitted text layer has calculate."""
        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "scatter",
            x="month",
            y="revenue",
            style={
                "marks": {"point": {"labels": {"visible": True, "format": "number"}}}
            },
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers, "scatter with visible labels must produce a text layer"
        lyr = text_layers[0]
        transforms = lyr.get("transform", [])
        assert any("calculate" in t for t in transforms), (
            f"scatter alias label must have a calculate transform; got {transforms!r}"
        )
        assert "format" not in lyr.get("encoding", {}).get("text", {}), (
            "scatter alias label must NOT have bare text.format"
        )

    def test_scatter_literal_si_label_has_format_not_calculate(
        self, make_chart
    ) -> None:
        """Scatter chart with literal SI label: text.format set, no calculate."""
        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "scatter",
            x="month",
            y="revenue",
            style={"marks": {"point": {"labels": {"visible": True, "format": "~s"}}}},
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers, "scatter with visible labels must produce a text layer"
        lyr = text_layers[0]
        text_enc = lyr.get("encoding", {}).get("text", {})
        assert text_enc.get("format") == "~s", (
            f"scatter literal '~s' label must carry text.format='~s'; got {text_enc!r}"
        )
        assert not lyr.get("transform"), (
            "scatter literal label must NOT have a calculate transform"
        )


# =============================================================================
# Line/point label ternary (line_label_is_house vs point_label_is_house)
# pinned at the render level.
# =============================================================================


class TestLineVsPointLabelRenderLevel:
    """Render-level proof that the line/point ternary in _apply_line picks the
    right is_house flag.

    The ternary: `is_house = line_label_is_house if use_line else point_label_is_house`.
    Existing tests (TestLineVsPointLabelSelection) pin the resolved-model flags but
    not the emitted spec. This class verifies that the VL text layer carries a
    calculate transform when the used flag is True, and text.format when False.
    """

    def test_line_labels_visible_alias_has_calculate(self, make_chart) -> None:
        """use_line=True, line_label_is_house=True: emitted text layer has calculate.

        If the ternary were flipped to always use point_label_is_house (False here),
        there would be no calculate transform.
        """
        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style={
                "marks": {
                    "line": {"labels": {"visible": True, "format": "number"}},
                    "point": {"labels": {"visible": False, "format": "~s"}},
                }
            },
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers, (
            "line chart with visible line labels must produce text layer"
        )
        lyr = text_layers[0]
        transforms = lyr.get("transform", [])
        assert any("calculate" in t for t in transforms), (
            f"line alias label (use_line=True, line_is_house=True) must have "
            f"calculate transform; got {transforms!r}. "
            f"Flip would use point_is_house=False -> no transform."
        )

    def test_point_labels_visible_literal_has_format(self, make_chart) -> None:
        """use_line=False, point_label_is_house=False: emitted text layer has text.format.

        If the ternary were flipped to use line_label_is_house (True here),
        there would be a calculate transform instead of text.format.
        """
        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            style={
                "marks": {
                    "line": {"labels": {"visible": False, "format": "number"}},
                    "point": {"labels": {"visible": True, "format": "~s"}},
                }
            },
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        text_layers = [
            lyr
            for lyr in spec.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert text_layers, (
            "line chart with visible point labels must produce text layer"
        )
        lyr = text_layers[0]
        text_enc = lyr.get("encoding", {}).get("text", {})
        assert text_enc.get("format") == "~s", (
            f"point literal label (use_line=False, point_is_house=False) must carry "
            f"text.format='~s'; got {text_enc!r}. "
            f"Flip would use line_is_house=True -> calculate."
        )
        assert not lyr.get("transform"), (
            "point literal label must NOT have a calculate transform"
        )


# =============================================================================
# Bar position bottom/middle/middle_aligned with is_house=True must splice the
# house-register calculate transform alongside the position transform.
# =============================================================================


def _bar_with_house_label(position: str):
    """Board+context with bar labels visible, alias format, and given position."""
    compiled = get_theme_style("clarity")
    new_labels = compiled.charts.marks.bar.labels.model_copy(
        update={"visible": True, "position": position, "format": "number"}
    )
    new_bar = compiled.charts.marks.bar.model_copy(update={"labels": new_labels})
    new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


class TestBarPositionHouseTransformSplice:
    """Bar positions bottom/middle/middle_aligned must splice the house-register
    calculate transform when is_house=True.

    The splice is `[*house_transforms, ...]` in each position branch of
    _build_bar_text_layer. Dropping it silently breaks the text encoding:
    the text channel references __value_label_text but nothing produces it.
    Existing position tests don't cover is_house=True -- they use SAMPLE_DATA
    (small values, may give empty house_transforms with a non-SI theme default).
    """

    def test_bottom_position_house_label_has_calculate_transform(
        self, make_chart
    ) -> None:
        """position=bottom + alias format (is_house=True): house calculate transform
        must be present alongside the __bar_bottom=0 position transform."""
        board_rs, board_ctx = _bar_with_house_label("bottom")
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        all_transforms = spec.get("transform", [])
        has_house_calculate = any(
            "calculate" in t and t.get("as") == "__value_label_text"
            for t in all_transforms
        )
        assert has_house_calculate, (
            f"bottom with is_house=True must include house-register calculate "
            f"(as __value_label_text); got transforms={all_transforms!r}"
        )

    def test_middle_position_house_label_has_calculate_transform(
        self, make_chart
    ) -> None:
        """position=middle + alias format: house calculate transform must be present."""
        board_rs, board_ctx = _bar_with_house_label("middle")
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        all_transforms = spec.get("transform", [])
        has_house_calculate = any(
            "calculate" in t and t.get("as") == "__value_label_text"
            for t in all_transforms
        )
        assert has_house_calculate, (
            f"middle with is_house=True must include house-register calculate; "
            f"got transforms={all_transforms!r}"
        )

    def test_middle_aligned_position_house_label_has_calculate_transform(
        self, make_chart
    ) -> None:
        """position=middle_aligned + alias format: house calculate transform present."""
        board_rs, board_ctx = _bar_with_house_label("middle_aligned")
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        all_transforms = spec.get("transform", [])
        has_house_calculate = any(
            "calculate" in t and t.get("as") == "__value_label_text"
            for t in all_transforms
        )
        assert has_house_calculate, (
            f"middle_aligned with is_house=True must include house-register calculate; "
            f"got transforms={all_transforms!r}"
        )


class TestStackedBarHouseTransformSplice:
    """A stacked bar's segment label must splice the house-register calculate
    transform the same way the non-stacked position branches do.

    _build_bar_text_layer's is_stacked branch conditionally sets
    stacked_result["transform"] = house_transforms. Dropping that line leaves
    the text channel pointing at __value_label_text with nothing producing
    it -- every stacked bar with value labels renders blank, silently. No
    existing test exercised is_stacked=True together with is_house=True.
    """

    def test_stacked_segment_house_label_has_calculate_transform(
        self, make_chart
    ) -> None:
        """Stacked bar + alias format (is_house=True): the segment label layer
        must carry the house calculate transform producing __value_label_text."""
        board_rs, board_ctx = _bar_with_house_label("top")
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            style={"bar": {"stack": "zero", "orientation": "vertical"}},
        )
        spec = generate_vega_lite_spec(
            chart, _MILLIONS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None, "Stacked bar must still emit a text layer"
        assert lyr["encoding"]["text"].get("field") == "__value_label_text", (
            f"is_house=True text channel must reference __value_label_text: "
            f"{lyr['encoding']['text']!r}"
        )
        # A single-consumer layer transform is hoisted to the outer spec's own
        # transform array (see the other position-splice tests above), so the
        # producing calculate lives at spec level, not on the layer itself.
        all_transforms = spec.get("transform", [])
        has_house_calculate = any(
            "calculate" in t and t.get("as") == "__value_label_text"
            for t in all_transforms
        )
        assert has_house_calculate, (
            f"stacked segment label with is_house=True must include the house "
            f"calculate transform producing __value_label_text; got "
            f"transforms={all_transforms!r}"
        )


# =============================================================================
# KPI notation field forces house register regardless of spec string.
# =============================================================================


class TestKpiNotationOverrideIsHouse:
    """_resolve_kpi: FormatConfig with notation not None must set format_native=False.

    resolve_label_format("~s", formats) returns is_house=False (literal, not alias).
    The notation field being not None overrides that to is_house=True, so
    format_native=False.
    """

    def test_notation_narrative_overrides_literal_spec_to_house(
        self, make_chart
    ) -> None:
        """FormatConfig(spec="~s", notation="narrative"): format_native must be False.

        The spec "~s" alone -> is_house=False (literal). The notation field being
        not None overrides that to is_house=True, so format_native=False.
        """
        from dbt_charts.core.compile.models.primitives import FormatConfig

        _, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart = make_chart(
            "kpi",
            value="revenue",
            style={"value": {"format": FormatConfig(spec="~s", notation="narrative")}},
        )
        resolved = resolve(chart, _KPI_MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.format_native is False, (
            "FormatConfig(spec='~s', notation='narrative') must override literal spec "
            "to house register (format_native=False); notation not None -> is_house=True"
        )


# =============================================================================
# Overlay is_house wiring verified at the render (emit) level for all four
# base-chart families.
# =============================================================================


class TestOverlayIsHouseAllFamiliesRenderLevel:
    """Overlay label is_house must reach the emitted VL spec for all four families.

    TestSharedAxisOverlayWithMarkFormat and TestOverlayAliasGateRegister pin
    resolved.layers[0].label_is_house. These tests add the render-level proof:
    - alias format (is_house=True) -> calculate transform in overlay text layer
    - literal "~s" (is_house=False) -> text.format="~s", no calculate transform
    """

    @pytest.mark.parametrize(
        ("chart_type", "layer_type", "mark_key"),
        [
            ("bar", "bar", "bar"),
            ("line", "line", "line"),
            ("area", "area", "line"),
            ("scatter", "scatter", "point"),
        ],
    )
    def test_overlay_alias_label_has_calculate_transform(
        self, chart_type: str, layer_type: str, mark_key: str
    ) -> None:
        """Overlay with alias-format label: text layer carries calculate transform.

        label_is_house=True (alias) -> _build_{line|point}_text_layer returns
        non-empty house_transforms -> the VL text layer has "transform" with a
        calculate entry and no bare text.format.
        """
        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = {
            "type": chart_type,
            "query": "q",
            "x": "month",
            "y": "revenue",
            "layers": [
                {
                    "type": layer_type,
                    "y": "revenue",
                    "style": {
                        "marks": {
                            mark_key: {"labels": {"visible": True, "format": "number"}}
                        }
                    },
                }
            ],
        }
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(compiled, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.layers and resolved.layers[0].label_is_house is True, (
            f"{layer_type} overlay with alias 'number' must resolve to house "
            f"(label_is_house=True)"
        )
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _MILLIONS_DATA}
        )
        vl = session.finalize_vl(spec)
        pane = chart_pane(vl)
        overlay_text_layers = [
            lyr
            for lyr in pane.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert overlay_text_layers, (
            f"{layer_type} overlay with visible alias labels must produce a text layer"
        )
        lyr = overlay_text_layers[0]
        lyr_transforms = lyr.get("transform", [])
        assert any("calculate" in t for t in lyr_transforms), (
            f"{layer_type} overlay alias label must have a calculate transform "
            f"(is_house=True); got transforms={lyr_transforms!r}"
        )
        assert "format" not in lyr.get("encoding", {}).get("text", {}), (
            f"{layer_type} overlay alias label must NOT have bare text.format"
        )

    @pytest.mark.parametrize(
        ("chart_type", "layer_type", "mark_key"),
        [
            ("bar", "bar", "bar"),
            ("line", "line", "line"),
            ("area", "area", "line"),
            ("scatter", "scatter", "point"),
        ],
    )
    def test_overlay_literal_label_has_format_not_calculate(
        self, chart_type: str, layer_type: str, mark_key: str
    ) -> None:
        """Overlay with literal "~s" label: text.format="~s" set, no calculate transform.

        label_is_house=False (literal) -> house_transforms empty -> no "transform"
        key in VL text layer, encoding.text.format == "~s".
        """
        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = {
            "type": chart_type,
            "query": "q",
            "x": "month",
            "y": "revenue",
            "layers": [
                {
                    "type": layer_type,
                    "y": "revenue",
                    "style": {
                        "marks": {
                            mark_key: {"labels": {"visible": True, "format": "~s"}}
                        }
                    },
                }
            ],
        }
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(compiled, _MILLIONS_DATA, chart_style_context=board_ctx)
        assert resolved.layers and resolved.layers[0].label_is_house is False, (
            f"{layer_type} overlay with literal '~s' must resolve to native "
            f"(label_is_house=False)"
        )
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _MILLIONS_DATA}
        )
        vl = session.finalize_vl(spec)
        pane = chart_pane(vl)
        overlay_text_layers = [
            lyr
            for lyr in pane.get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]
        assert overlay_text_layers, (
            f"{layer_type} overlay with visible labels must produce a text layer"
        )
        lyr = overlay_text_layers[0]
        text_enc = lyr.get("encoding", {}).get("text", {})
        assert text_enc.get("format") == "~s", (
            f"{layer_type} overlay literal label must carry text.format='~s'; "
            f"got {text_enc!r}"
        )
        assert "transform" not in lyr, (
            f"{layer_type} overlay literal label must NOT have a calculate transform"
        )


# =============================================================================
# datetime.date x-values must not break an overlay's authored sort.
# rendered_x_domain normalizes raw category keys so they match the ISO-string
# keys in the emitted domain array.
# =============================================================================


class TestOverlayDateXSortPreservation:
    """Overlay sort with datetime.date x-values must apply correctly.

    When data rows carry raw datetime.date objects (as returned by SQL adapters),
    ordered_distinct_values normalizes them to ISO strings in the domain.
    domain_sort_aggregates keys on raw datetime.date objects. Without remapping,
    v in sort_aggs is always False and sort is silently dropped.

    The fix: sort_aggs keys are remapped through normalize_scalar_for_json before
    the domain membership check.
    """

    def test_date_object_x_sort_desc_gives_correct_domain(self) -> None:
        """Bar overlay on datetime.date x, desc sort by revenue: domain must be
        ISO-string desc order.

        Data: date objects Jan (1.2M) < Feb (2.4M) < Mar (3.6M).
        Desc sort: ["2025-03-01", "2025-02-01", "2025-01-01"].
        Without the key-remap fix, all domain values fall into layer_only and
        the domain is ["2025-01-01", "2025-02-01", "2025-03-01"] (original order).
        """
        import datetime

        pytest.importorskip("vl_convert")
        import vl_convert as vlc

        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        date_data = [
            {"date": datetime.date(2025, 1, 1), "revenue": 1_200_000},
            {"date": datetime.date(2025, 2, 1), "revenue": 2_400_000},
            {"date": datetime.date(2025, 3, 1), "revenue": 3_600_000},
        ]
        chart_def = {
            "type": "bar",
            "query": "q",
            "x": "date",
            "y": "revenue",
            "sort": {"by": "revenue", "order": "desc"},
            "layers": [
                {
                    "type": "bar",
                    "y": "revenue",
                    "style": {
                        "marks": {
                            "bar": {"labels": {"visible": True, "format": "number"}}
                        }
                    },
                }
            ],
        }
        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(compiled, date_data, chart_style_context=board_ctx)
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: date_data}
        )
        vl = session.finalize_vl(spec)

        pane = chart_pane(vl)
        domain = pane.get("encoding", {}).get("x", {}).get("scale", {}).get("domain")
        assert domain == ["2025-03-01", "2025-02-01", "2025-01-01"], (
            f"desc-by-revenue with datetime.date x must give ISO-string domain in "
            f"desc order; got {domain!r} -- without key-remap fix, sort is silently "
            f"dropped and domain stays ['2025-01-01', '2025-02-01', '2025-03-01']"
        )

        # vl_convert renders ISO date ordinal labels as abbreviated month names
        # ("2025-03-01" -> "Mar"). Verify the rendered order by month-name position
        # in the SVG string -- earlier in the SVG string = leftmost bar.
        svg = vlc.vegalite_to_svg(vl)
        month_positions = {m: svg.index(f">{m}<") for m in ("Jan", "Feb", "Mar")}
        rendered_order = sorted(month_positions, key=month_positions.get)
        assert rendered_order == ["Mar", "Feb", "Jan"], (
            f"rendered order must follow desc-by-revenue (Mar first); "
            f"got {rendered_order!r}"
        )


# =============================================================================
# A stacked bar overlay must use sum for the sort aggregate.
# The shared categorical-domain helper uses sum, matching Vega-Lite's own
# EncodingSortField.op default for the bar shapes this codebase emits.
# =============================================================================


class TestOverlayStackedBarSumAggregate:
    """A stacked bar overlay's authored field sort uses the sum aggregate.

    Vega-Lite uses sum as the EncodingSortField.op default for stacked plots.
    With multi-row-per-x data, sum and min give different orderings. The
    rendered domain must match sum-desc, not min-desc.

    Data (see _MULTI_ROW_SORT_DATA):
      Jan: A=3M  + B=100K -> sum=3.1M, min=100K
      Feb: A=1.5M + B=1.5M -> sum=3M,  min=1.5M
      Mar: A=800K + B=2M   -> sum=2.8M, min=800K
    Sum-desc: Jan (3.1M), Feb (3M), Mar (2.8M) -> Jan/Feb/Mar
    Min-desc: Feb (1.5M), Mar (800K), Jan (100K) -> Feb/Mar/Jan
    """

    def test_stacked_bar_sort_uses_sum_aggregate(self) -> None:
        """Stacked bar (stack=zero + color) overlay, desc sort: sum-desc order required.

        Expected domain: ["Jan", "Feb", "Mar"] (sum-desc).
        """
        pytest.importorskip("vl_convert")
        import vl_convert as vlc

        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = {
            "type": "bar",
            "query": "q",
            "x": "month",
            "y": "revenue",
            "color": "series",
            "style": {"stack": "zero"},
            "sort": {"by": "revenue", "order": "desc"},
            "layers": [
                {
                    "type": "bar",
                    "y": "revenue",
                    "style": {
                        "marks": {
                            "bar": {"labels": {"visible": True, "format": "number"}}
                        }
                    },
                }
            ],
        }
        compiled = normalize_chart("v2chart", chart_def, _DUMMY_QUERY_REG, sources={})
        resolved = resolve(
            compiled, _MULTI_ROW_SORT_DATA, chart_style_context=board_ctx
        )
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(
            resolved, _DEFAULT_BOX, {resolved.query_name: _MULTI_ROW_SORT_DATA}
        )
        vl = session.finalize_vl(spec)

        pane = chart_pane(vl)
        domain = pane.get("encoding", {}).get("x", {}).get("scale", {}).get("domain")
        assert domain == ["Jan", "Feb", "Mar"], (
            f"stacked bar desc-sort must use sum-aggregate (Jan/Feb/Mar); "
            f"got {domain!r} -- a min aggregate would give Feb/Mar/Jan instead"
        )

        svg = vlc.vegalite_to_svg(vl)
        month_positions = {
            month: svg.index(f">{month}<") for month in ("Jan", "Feb", "Mar")
        }
        rendered_order = sorted(month_positions, key=month_positions.get)
        assert rendered_order == ["Jan", "Feb", "Mar"], (
            f"rendered order must be Jan/Feb/Mar (sum-desc), got {rendered_order!r}"
        )


# =============================================================================
# Null measures: no value, no label
# =============================================================================

_NULL_TAIL_DATA = [
    {"month": "Jan", "revenue": 441},
    {"month": "Feb", "revenue": 455},
    {"month": "Mar", "revenue": None},
    {"month": "Apr", "revenue": None},
]

_ZERO_ROW_DATA = [
    {"month": "Jan", "revenue": 441},
    {"month": "Feb", "revenue": 0},
    {"month": "Mar", "revenue": 455},
]


class TestNullMeasureLabels:
    """A null measure gets no label mark at all.

    Vega-Lite already drops a null-valued row from the mark, which is why
    ``above`` was always clean. The interior positions put a *calculated*
    field on the measure channel, and the calculation used to launder the null
    into a valid number (``null / 2`` is ``0`` in a Vega expression, and
    ``middle_aligned``/``bottom`` used a constant valid for every row), so the
    text mark survived and painted the string ``null`` — in knockout white, on
    the zero rule.
    """

    @pytest.mark.parametrize(
        "position", ["above", "top", "middle", "middle_aligned", "bottom"]
    )
    def test_null_measure_renders_no_label(self, make_chart, position) -> None:
        pytest.importorskip("vl_convert")
        board_rs, board_ctx = _board_with_bar_labels_visible(True, position=position)
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, _NULL_TAIL_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _NULL_TAIL_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        texts = _render_texts(spec)
        assert "null" not in texts, (
            f"position={position!r}: a null measure must draw no label; got {texts}"
        )
        for real in ("441", "455"):
            assert real in texts, (
                f"position={position!r}: real labels must survive; got {texts}"
            )

    @pytest.mark.parametrize("position", ["middle", "middle_aligned", "bottom"])
    def test_genuine_zero_still_labeled(self, make_chart, position) -> None:
        """An exact 0 is a value, not a gap — it keeps its label."""
        pytest.importorskip("vl_convert")
        board_rs, board_ctx = _board_with_bar_labels_visible(True, position=position)
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, _ZERO_ROW_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _ZERO_ROW_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        texts = _render_texts(spec)
        assert "0" in texts, (
            f"position={position!r}: an exact 0 must keep its label; got {texts}"
        )

    def test_overlay_layer_null_measure_renders_no_label(self) -> None:
        """The reported shape: a goal series running past the last actual.

        The overlay layer builds its text layer through the same
        ``_build_bar_text_layer``, but keeps the transform on its own
        sub-layer instead of hoisting it, and binds the guard to the
        *layer's* own measure column rather than the base chart's.
        """
        pytest.importorskip("vl_convert")

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.normalize.charts import normalize_chart
        from dbt_charts.core.render.chart.session import BoardRenderSession

        query_reg: dict[str, Any] = {"q": SqlQuery(sql="SELECT 1", source="test")}
        data = [
            {"month": "Jan", "goal": 500, "actual": 441},
            {"month": "Feb", "goal": 500, "actual": 455},
            {"month": "Mar", "goal": 500, "actual": None},
            {"month": "Apr", "goal": 500, "actual": None},
        ]
        board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
        chart_def = {
            "type": "bar",
            "query": "q",
            "x": "month",
            "y": "goal",
            "layers": [
                {
                    "type": "bar",
                    "y": "actual",
                    "style": {
                        "marks": {
                            "bar": {"labels": {"visible": True, "position": "middle"}}
                        }
                    },
                }
            ],
        }
        compiled = normalize_chart("v2chart", chart_def, query_reg, sources={})
        resolved = resolve(compiled, data, chart_style_context=board_ctx)
        session = BoardRenderSession.create(board_rs)
        spec = session.emit_chart(resolved, _DEFAULT_BOX, {resolved.query_name: data})

        texts = _render_texts(session.finalize_vl(spec))
        assert "null" not in texts, (
            f"an overlay layer's null measure must draw no label; got {texts}"
        )
        for real in ("441", "455"):
            assert real in texts, f"overlay real labels must survive; got {texts}"


# ---------------------------------------------------------------------------
# labels.field pointing at a TEXT column
# ---------------------------------------------------------------------------


def _text_label_encodings(data, label_format=None, label_field="caption"):
    """Every ``text`` channel encoding a bar chart emits for ``labels.field``."""
    compiled = get_theme_style("clarity")
    update = {"visible": True, "field": label_field}
    if label_format is not None:
        update["format"] = label_format
    new_labels = compiled.charts.marks.bar.labels.model_copy(update=update)
    new_bar = compiled.charts.marks.bar.model_copy(update={"labels": new_labels})
    new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
    charts = compiled.charts.model_copy(update={"marks": new_marks})
    board_rs, board_ctx = resolve_style_and_context(
        compiled.model_copy(update={"charts": charts})
    )
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    chart = BarChart(id="t", type="bar", x="month", y="revenue", query_name="q")
    spec = generate_vega_lite_spec(
        chart, data, board_style=board_rs, chart_style_context=board_ctx
    )
    return [
        lyr["encoding"]["text"]
        for lyr in spec.get("layer", [])
        if isinstance(lyr.get("mark"), dict)
        and lyr["mark"].get("type") == "text"
        and isinstance(lyr.get("encoding", {}).get("text"), dict)
    ]


_TEXT_ROWS = [
    {"month": "Jan", "revenue": 100, "caption": "Pace", "scaled": 1.2},
    {"month": "Feb", "revenue": 200, "caption": "Pace", "scaled": 2.4},
]


@pytest.mark.parametrize(
    "label_format",
    [
        pytest.param(None, id="theme-default-si-format"),
        pytest.param(",.0f", id="explicit-d3-format"),
    ],
)
def test_text_label_column_is_drawn_as_text(label_format):
    """A string column renders verbatim — the raw column, nominal, no format.

    Parametrized over both routes a format can reach the text encoding by,
    because each coerced the string to a number its own way: the theme's own
    SI-shaped default fires the house-register ``calculate``, and an explicit
    d3 spec is handed to Vega as ``text.format``. Both painted ``NaN``.

    A format *alias* is not a third route — ``integer`` resolves to ``,.0f``,
    which is not SI-shaped, so it lands on the same non-house branch as the
    literal spec. And the bare ``labels.format is None`` branch is unreachable
    through a theme, which always supplies a measure format.
    """
    captions = [
        enc
        for enc in _text_label_encodings(_TEXT_ROWS, label_format)
        if enc.get("field") == "caption"
    ]
    assert captions, "no encoding sourced the caption column"
    for enc in captions:
        assert enc.get("type") == "nominal", enc
        assert "format" not in enc, enc


def test_sparse_caption_column_past_the_sampling_window_is_still_text():
    """A caption populated only after the first ten rows still reads as text.

    The motivating shape: a reference mark captioned on the month in progress
    and null everywhere else. ``infer_vega_type_from_data`` samples ten rows
    and skips nulls, so an all-null prefix leaves it classified quantitative —
    which is the ``NaN`` this feature removes, reappearing on any board with
    more than ten categories.
    """
    rows = [
        {"month": f"M{i}", "revenue": 10 * i, "caption": None} for i in range(1, 13)
    ]
    rows.append({"month": "M13", "revenue": 130, "caption": "Pace"})

    captions = [
        enc for enc in _text_label_encodings(rows) if enc.get("field") == "caption"
    ]
    assert captions, "sparse caption column was classified as a number"
    for enc in captions:
        assert enc.get("type") == "nominal", enc
        assert "format" not in enc, enc


@pytest.mark.parametrize(
    ("label_format", "expected"),
    [
        pytest.param(
            None,
            {"field": "__value_label_text", "type": "nominal"},
            id="theme-default-si-format-takes-the-house-calculate",
        ),
        pytest.param(
            ",.0f",
            {"field": "scaled", "type": "quantitative", "format": ",.0f"},
            id="explicit-d3-format-goes-to-vega-verbatim",
        ),
    ],
)
def test_numeric_label_column_still_routes_through_number_formatting(
    label_format, expected
):
    """Control: the pre-existing numeric use of ``labels.field`` is untouched.

    Asserts the whole encoding each route emits, not merely that it isn't the
    text one. A negative assertion cannot see the loss that matters here — a
    silently dropped ``format`` leaves a quantitative encoding that passes any
    "is it text?" check while rendering the wrong string.
    """
    assert _text_label_encodings(_TEXT_ROWS, label_format, "scaled") == [expected]


# --- Stacked-segment midpoint labels + fit test -----------------------------

_SEGMENT_FIT_DATA = [
    {"month": "Jan", "seg": "Free", "revenue": 100},
    {"month": "Jan", "seg": "Paid", "revenue": 50},
    {"month": "Aug", "seg": "Free", "revenue": 30},
    {"month": "Aug", "seg": "Paid", "revenue": 4},
]


def _layered_pane(spec):
    """The unit spec carrying the mark layers.

    A stacked bar with a series color renders as a concat (series label rail +
    chart pane), which `chart_pane` already unwraps — one mapper for one
    concept, per core/AGENTS.md.
    """
    return chart_pane(spec)


def _text_layers(spec):
    return [
        layer
        for layer in _layered_pane(spec).get("layer", [])
        if (layer.get("mark") or {}).get("type") == "text"
    ]


def _stacked_spec(
    make_chart, position, *, height=None, orientation="vertical", data=None
):
    board_rs, board_ctx = _board_with_bar_labels({"position": position})
    rows = _SEGMENT_FIT_DATA if data is None else data
    chart = make_chart(
        "bar",
        x="month",
        y="revenue",
        color="seg",
        style={"bar": {"stack": "zero", "orientation": orientation}},
    )
    resolve(chart, rows, chart_style_context=board_ctx)
    kwargs = {} if height is None else {"height": height}
    return generate_vega_lite_spec(
        chart,
        rows,
        board_style=board_rs,
        chart_style_context=board_ctx,
        **kwargs,
    )


class TestStackedSegmentMidpointLabels:
    """position: middle on a stacked bar centers each label in its own segment."""

    def test_measure_channel_reads_segment_midpoint(self, make_chart):
        """The label sits at the segment midpoint field with stacking explicitly off.

        `stack: None` is load-bearing, not cosmetic: a sublayer sharing the
        outer measure channel inherits the outer `stack: "zero"` and would
        re-stack the already-absolute midpoint.
        """
        spec = _stacked_spec(make_chart, "middle")
        layers = _text_layers(spec)
        assert layers, "stacked bar + position=middle must emit a text layer"
        measure = layers[0]["encoding"]["y"]
        assert measure["field"] == "__value_label_mid", (
            f"expected the synthetic midpoint field, got: {measure}"
        )
        assert measure["stack"] is None, (
            f"midpoint label must opt out of outer stacking, got: {measure}"
        )

    def test_midpoint_computed_from_stack_bounds(self, make_chart):
        """A stack transform supplies the segment's own start/end bounds."""
        pane = _layered_pane(_stacked_spec(make_chart, "middle"))
        stack_tfs = [t for t in pane.get("transform", []) if "stack" in t]
        assert stack_tfs, (
            f"expected a stack transform for segment bounds, got: {pane.get('transform')}"
        )
        stack_tf = stack_tfs[0]
        assert stack_tf["groupby"] == ["month"], (
            f"segment bounds must group by the category field, got: {stack_tf}"
        )
        mid_tfs = [
            t for t in pane.get("transform", []) if t.get("as") == "__value_label_mid"
        ]
        assert mid_tfs, "expected a midpoint calculate transform"
        expr = mid_tfs[0]["calculate"]
        start, end = stack_tf["as"]
        assert start in expr and end in expr, (
            f"midpoint must average the stack bounds, got: {expr}"
        )

    def test_stack_bounds_sorted_by_the_charts_own_stack_order(self, make_chart):
        """Segment bounds use the bar layer's declared order, not VL's default.

        Mirroring VL's implicit sort would put labels on the wrong segments the
        moment style.stack_order is authored.
        """
        pane = _layered_pane(_stacked_spec(make_chart, "middle"))
        order_enc = pane["encoding"].get("order")
        assert order_enc is not None, (
            "precondition: a stacked bar with a series color declares an order encoding"
        )
        stack_tf = next(t for t in pane["transform"] if "stack" in t)
        assert stack_tf["sort"] == [
            {"field": order_enc["field"], "order": "ascending"}
        ], (
            f"stack bounds must sort by {order_enc['field']}, got: {stack_tf.get('sort')}"
        )

    def test_label_color_pinned_against_segment_fill(self, make_chart):
        """Without an explicit color the label inherits its segment's fill and vanishes."""
        layers = _text_layers(_stacked_spec(make_chart, "middle"))
        color = layers[0]["encoding"].get("color")
        assert color is not None and "value" in color, (
            f"midpoint label must pin an explicit color, got: {color}"
        )

    def test_unstacked_middle_position_unchanged(self, make_chart):
        """A simple bar keeps the datum[y]/2 midpoint — no stack transform."""
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        chart = make_chart("bar", x="month", y="revenue")
        resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        pane = _layered_pane(spec)
        assert not [t for t in pane.get("transform", []) if "stack" in t], (
            "a non-stacked bar must not grow a stack transform"
        )
        mid = next(t for t in pane["transform"] if t.get("as") == "__value_label_mid")
        assert "/ 2" in mid["calculate"], (
            f"simple bar midpoint should stay datum[y]/2, got: {mid['calculate']}"
        )

    def test_stacked_middle_aligned_raises(self, make_chart):
        """middle_aligned means 'a height shared across bars' — meaningless per segment.

        Silently dropping the layer is the bug this task fixes; erroring is the
        validate-and-error-fast answer.
        """
        with pytest.raises(ChartDataError):
            _stacked_spec(make_chart, "middle_aligned")


class TestValueLabelFitTest:
    """A label that cannot fit its segment is blanked, not drawn over its neighbors."""

    @staticmethod
    def _hide_tests(layer):
        condition = layer["encoding"]["text"].get("condition")
        return [] if condition is None else [condition["test"]]

    def test_short_segment_label_is_hidden(self, make_chart):
        """The 4-unit segment in a 150-unit stack cannot hold an 11px label.

        Rendered, not spec-level: the condition is emitted on every qualifying
        chart now, so its presence proves nothing about what it hides.
        """
        spec = _stacked_spec(make_chart, "middle", height=120.0)
        tests = self._hide_tests(_text_layers(spec)[0])
        assert tests and "revenue" in tests[0], (
            f"fit condition must test the measure field, got: {tests}"
        )
        texts = _render_texts(spec)
        assert "4" not in texts, (
            f"the 4-unit segment cannot hold its label and must drop it: {texts}"
        )
        assert "100" in texts, (
            f"the 100-unit segment has room and must keep its label: {texts}"
        )

    def test_fit_condition_blanks_rather_than_filters(self, make_chart):
        """No transform on the label layer, at any level.

        A `transform` array on a label sublayer makes vl-convert discard the
        shared categorical axis's sort — see zero_value_label.py's docstring,
        which records this trap being hit and backed out. A filter on the outer
        spec would drop the bar marks themselves.
        """
        pane = _layered_pane(_stacked_spec(make_chart, "middle", height=120.0))
        assert not [t for t in pane.get("transform", []) if "filter" in t], (
            f"no filter belongs on the outer spec, got: {pane.get('transform')}"
        )
        for layer in _text_layers(pane):
            assert not [t for t in (layer.get("transform") or []) if "filter" in t], (
                "the fit test must blank via a text condition, never a layer filter"
            )

    def test_sorted_bar_keeps_its_sort_when_a_label_is_hidden(self, make_chart):
        """The regression the condition mechanism exists to avoid."""
        board_rs, board_ctx = _board_with_bar_labels({"position": "top"})
        rows = [
            {"month": "Jan", "revenue": 90},
            {"month": "Feb", "revenue": 1},
            {"month": "Mar", "revenue": 45},
        ]
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            sort={"by": "revenue", "order": "desc"},
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, rows, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            rows,
            board_style=board_rs,
            chart_style_context=board_ctx,
            height=110.0,
        )
        pane = _layered_pane(spec)
        # Rendered precondition: an emitted condition no longer implies a
        # hidden label, so check Feb's 1-unit bar really did lose its own.
        rendered = _render_texts(spec)
        assert "1" not in rendered and "90" in rendered, (
            "precondition: Feb's 1-unit bar must actually be hidden here while "
            f"Jan's 90 keeps its label, or this test passes vacuously: {rendered}"
        )
        assert pane["encoding"]["x"].get("sort") is not None, (
            "the categorical axis must keep its sort with a hidden label present"
        )
        for layer in _text_layers(pane):
            assert layer.get("transform") is None, (
                f"a label-layer transform is what discards the sort: {layer.get('transform')}"
            )

    def test_exact_zero_is_exempt(self, make_chart):
        """A zero bar has no extent, but it is not a short segment.

        An unlabeled zero reads as missing data — the case
        ZeroValueLabelFeature exists to prevent.
        """
        layers = _text_layers(_stacked_spec(make_chart, "middle", height=120.0))
        test = self._hide_tests(layers[0])[0]
        assert "!= 0" in test, f"an exact zero must stay labeled, got: {test}"

    def test_every_label_survives_when_every_segment_fits(self, make_chart):
        """Charts with room render exactly as they do today — no visual churn.

        The condition is now emitted unconditionally: its threshold is a
        render-time value, so Python cannot evaluate it to decide whether to
        skip it, and the slot-derived value the old early-exit used was the
        smaller one, so it under-counted (see `_fit_hide_test`). What has to
        hold is that the emitted condition does not fire — which is a
        rendered property, not a spec-shape one.
        """
        roomy = [
            {"month": "Jan", "seg": "Free", "revenue": 100},
            {"month": "Jan", "seg": "Paid", "revenue": 90},
            {"month": "Aug", "seg": "Free", "revenue": 95},
            {"month": "Aug", "seg": "Paid", "revenue": 105},
        ]
        texts = _render_texts(
            _stacked_spec(make_chart, "middle", height=600.0, data=roomy)
        )
        for value in ("100", "90", "95", "105"):
            assert value in texts, (
                f"every segment has room at 600px; {value} must stay labeled, "
                f"got {texts}"
            )

    def test_above_position_is_never_hidden(self, make_chart):
        """`above` draws past the bar end, so the bar's extent cannot clip it.

        Only meaningful un-stacked: a stacked bar has no space above each
        segment, so the renderer remaps `above` to `top` — inside the segment,
        and correctly fit-tested.
        """
        board_rs, board_ctx = _board_with_bar_labels({"position": "above"})
        tiny = [{"month": "Jan", "revenue": 100}, {"month": "Aug", "revenue": 2}]
        chart = make_chart(
            "bar", x="month", y="revenue", style={"bar": {"orientation": "vertical"}}
        )
        resolve(chart, tiny, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            tiny,
            board_style=board_rs,
            chart_style_context=board_ctx,
            height=120.0,
        )
        for layer in _text_layers(spec):
            assert not self._hide_tests(layer), (
                "outside-the-bar labels must not be fit-tested"
            )

    def test_stack_total_label_is_never_hidden(self, make_chart):
        """The total sits above the whole stack — no segment has to hold it."""
        compiled = get_theme_style("clarity")
        new_labels = compiled.charts.marks.bar.labels.model_copy(
            update={"visible": True, "position": "middle"}
        )
        new_total = compiled.charts.marks.bar.total_label.model_copy(
            update={"visible": True}
        )
        new_bar = compiled.charts.marks.bar.model_copy(
            update={"labels": new_labels, "total_label": new_total}
        )
        new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
        charts = compiled.charts.model_copy(update={"marks": new_marks})
        board_rs, board_ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="seg",
            style={"bar": {"stack": "zero", "orientation": "vertical"}},
        )
        resolve(chart, _SEGMENT_FIT_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            _SEGMENT_FIT_DATA,
            board_style=board_rs,
            chart_style_context=board_ctx,
            height=120.0,
        )
        total_layers = [
            layer
            for layer in _text_layers(spec)
            if layer["encoding"].get("text", {}).get("field")
            in ("__stack_total", "__stack_total_text")
        ]
        assert total_layers, "precondition: the stack-total label layer renders"
        for layer in total_layers:
            assert not self._hide_tests(layer), (
                "the stack total must never be fit-tested"
            )

    def test_horizontal_bars_are_not_hidden(self, make_chart):
        """Horizontal bars fit against text WIDTH — a different problem, out of scope."""
        layers = _text_layers(
            _stacked_spec(make_chart, "middle", height=120.0, orientation="horizontal")
        )
        for layer in layers:
            assert not self._hide_tests(layer), (
                "the height fit test must not fire on horizontal bars"
            )


class TestStackGeometryFidelity:
    """The label layer must line up with the stack the BAR layer emitted.

    Every case here reconstructs geometry the emitter already decided. Getting
    it independently right for `stack: zero` and wrong for everything else is
    the failure mode these pin.
    """

    @staticmethod
    def _stack_transform(pane):
        return next(t for t in pane["transform"] if "stack" in t)

    @pytest.mark.parametrize("mode", ["zero", "center", "normalize"])
    def test_offset_matches_the_bars_own_stack_mode(self, make_chart, mode):
        """`normalize` pins a [0,1] axis and `center` straddles the baseline.

        Stacking labels from zero puts them off-axis or inside the neighboring
        segment — a wrong result that looks right.
        """
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="seg",
            style={"bar": {"stack": mode, "orientation": "vertical"}},
        )
        resolve(chart, _SEGMENT_FIT_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            _SEGMENT_FIT_DATA,
            board_style=board_rs,
            chart_style_context=board_ctx,
        )
        pane = _layered_pane(spec)
        bar_stack = pane["encoding"]["y"]["stack"]
        assert self._stack_transform(pane)["offset"] == bar_stack, (
            f"label stack offset must match the bar's own {bar_stack!r}"
        )

    def test_normalize_stands_the_fit_test_down(self, make_chart):
        """Rows carry raw values while the axis is a [0,1] share — wrong units.

        Fractional rows are the point: with values like 0.05 against a [0, 1]
        span the linear threshold DOES fire, so the stand-down is load-bearing.
        Whole-number rows dwarf that span and would pass this test either way.
        """
        fractions = [
            {"month": "Jan", "seg": "Free", "revenue": 0.9},
            {"month": "Jan", "seg": "Paid", "revenue": 0.05},
            {"month": "Feb", "seg": "Free", "revenue": 0.7},
            {"month": "Feb", "seg": "Paid", "revenue": 0.04},
        ]
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="seg",
            style={"bar": {"stack": "normalize", "orientation": "vertical"}},
        )
        resolve(chart, fractions, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            fractions,
            board_style=board_rs,
            chart_style_context=board_ctx,
            height=120.0,
        )
        for layer in _text_layers(spec):
            assert layer["encoding"]["text"].get("condition") is None, (
                "a normalized stack must not be fit-tested against raw values"
            )

    def test_authored_label_font_size_drives_the_threshold(self, make_chart):
        """labels.font.size is the first tier of the effective size.

        Without that tier the board's text-mark size is used for every chart
        and an authored size changes nothing — so the threshold must move with
        it, not merely exist.
        """
        rows = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 3}]
        thresholds = {}
        for size in (11.0, 33.0):
            compiled = get_theme_style("clarity")
            font = compiled.charts.marks.bar.labels.font
            new_font = (
                font.model_copy(update={"size": size})
                if font is not None
                else FontStyle(size=size)
            )
            new_labels = compiled.charts.marks.bar.labels.model_copy(
                update={"visible": True, "position": "top", "font": new_font}
            )
            new_bar = compiled.charts.marks.bar.model_copy(
                update={"labels": new_labels}
            )
            new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
            charts = compiled.charts.model_copy(update={"marks": new_marks})
            board_rs, board_ctx = resolve_style_and_context(
                compiled.model_copy(update={"charts": charts})
            )
            chart = make_chart(
                "bar",
                x="month",
                y="revenue",
                style={"bar": {"orientation": "vertical"}},
            )
            resolve(chart, rows, chart_style_context=board_ctx)
            spec = generate_vega_lite_spec(
                chart,
                rows,
                board_style=board_rs,
                chart_style_context=board_ctx,
                height=300.0,
            )
            condition = _text_layers(spec)[0]["encoding"]["text"].get("condition")
            assert condition is not None, f"precondition: size {size} hides something"
            thresholds[size] = float(condition["test"].rsplit("<", 1)[1].strip())
        assert thresholds[33.0] > thresholds[11.0] * 2.5, (
            f"a 3x label size must raise the fit threshold, got {thresholds}"
        )

    def test_quantitative_color_mirrors_vegalites_default_sort(self, make_chart):
        """The emitter declares `order` only for a nominal series color.

        A numeric color column leaves it unset — and VL does NOT then stack in
        row order, it sorts by the color field descending. An unsorted label
        stack lands labels on the wrong segments, silently.
        """
        rows = [
            {"month": "Jan", "yr": 2023, "revenue": 100},
            {"month": "Jan", "yr": 2024, "revenue": 50},
            {"month": "Feb", "yr": 2023, "revenue": 80},
            {"month": "Feb", "yr": 2024, "revenue": 60},
        ]
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="yr",
            style={"bar": {"stack": "zero", "orientation": "vertical"}},
        )
        resolve(chart, rows, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, rows, board_style=board_rs, chart_style_context=board_ctx
        )
        pane = _layered_pane(spec)
        assert pane["encoding"].get("order") is None, (
            "precondition: a quantitative color emits no order channel"
        )
        assert self._stack_transform(pane)["sort"] == [
            {"field": "yr", "order": "descending"}
        ], "must mirror VL's own default stack sort, not fall back to row order"

    def test_authored_domain_governs_the_fit_threshold(self, make_chart):
        """An authored domain overrides the baked stacked_domain_max.

        Measuring against a span the chart is not using pushes the test the
        unsafe way and hides labels that had room.
        """
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="seg",
            style={
                "bar": {"stack": "zero", "orientation": "vertical"},
                "axis_y": {"scale": {"continuous": {"domain": [0, 400]}}},
            },
        )
        resolve(chart, _SEGMENT_FIT_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            _SEGMENT_FIT_DATA,
            board_style=board_rs,
            chart_style_context=board_ctx,
            height=300.0,
        )
        pane = _layered_pane(spec)
        scale = pane["encoding"]["y"]["scale"]
        assert scale.get("domain") == [0.0, 400.0], (
            f"precondition: the authored domain is what the chart renders with, got {scale}"
        )
        condition = _text_layers(pane)[0]["encoding"]["text"].get("condition")
        assert condition is not None, (
            "precondition: something is too short at this span"
        )
        # The span is the divisor that turns a data value into pixels. It must
        # be the authored 400, not the baked 162 domainMax — measuring against
        # a span the chart is not using pushes the test the unsafe way. Parsed,
        # not substring-matched: "/ 4000" contains "/ 400".
        span = float(re.search(r"/\s*([\d.e+-]+)\s*<", condition["test"]).group(1))
        assert span == 400.0, (
            f"the fit test must divide by the authored 400 span, got {span} "
            f"in: {condition['test']}"
        )

    def test_mixed_sign_split_still_defines_the_sort_field(self, make_chart):
        """A mixed-sign bar with a corner radius splits into sign-filtered sublayers.

        Those sublayers carry the stack-order calculate while the outer spec
        keeps only the encoding — so the hoisted label stack would sort by an
        undefined field, which VL degrades to row order instead of failing.
        """
        mixed = [
            {"month": "Jan", "seg": "Free", "revenue": 100},
            {"month": "Jan", "seg": "Paid", "revenue": -40},
            {"month": "Feb", "seg": "Free", "revenue": 80},
            {"month": "Feb", "seg": "Paid", "revenue": -20},
        ]
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="seg",
            style={"bar": {"stack": "zero", "orientation": "vertical"}},
        )
        resolve(chart, mixed, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, mixed, board_style=board_rs, chart_style_context=board_ctx
        )
        pane = _layered_pane(spec)
        transforms = pane["transform"]
        stack_transform = next(t for t in transforms if "stack" in t)
        sort = stack_transform.get("sort")
        assert sort is not None, (
            "precondition: this shape declares a stack sort — skipping here would "
            "let round 1's missing-sort defect pass green"
        )
        field = sort[0]["field"]
        defined_at = [i for i, t in enumerate(transforms) if t.get("as") == field]
        assert defined_at, (
            f"the outer spec must define {field!r} the hoisted stack sorts by; "
            f"got transforms: {[t.get('as') or list(t)[0] for t in transforms]}"
        )
        assert defined_at[0] < transforms.index(stack_transform), (
            f"{field!r} must be computed before the stack transform reads it"
        )


class TestFitTestGeometryAssumptions:
    """The fit test converts a value to pixels. Both halves of that are gated."""

    @staticmethod
    def _condition(spec):
        return _text_layers(spec)[0]["encoding"]["text"].get("condition")

    def _spec(self, make_chart, rows, position, chart_style, height=300.0):
        board_rs, board_ctx = _board_with_bar_labels({"position": position})
        chart = make_chart("bar", x="month", y="revenue", style=chart_style)
        resolve(chart, rows, chart_style_context=board_ctx)
        return generate_vega_lite_spec(
            chart,
            rows,
            board_style=board_rs,
            chart_style_context=board_ctx,
            height=height,
        )

    def test_non_linear_scale_stands_the_fit_test_down(self, make_chart):
        """symlog maps value to pixel non-linearly — a linear span cannot bound it.

        30 of a 1080 domain is 3% linearly but paints near half the plot on
        symlog, so a linear threshold hides a label with room to spare.
        """
        rows = [
            {"month": "Jan", "revenue": 1000},
            {"month": "Feb", "revenue": 300},
            {"month": "Mar", "revenue": 30},
        ]
        spec = self._spec(
            make_chart,
            rows,
            "middle",
            {
                "bar": {"orientation": "vertical"},
                "axis_y": {"scale": {"continuous": {"type": "symlog"}}},
            },
        )
        assert _layered_pane(spec)["encoding"]["y"]["scale"]["type"] == "symlog", (
            "precondition: the chart really renders on a symlog scale"
        )
        assert self._condition(spec) is None, (
            "a non-linear measure scale must not be fit-tested with a linear span"
        )

    def test_negative_bars_are_not_hidden_for_edge_positions(self, make_chart):
        """`top` on a downward bar draws BELOW it — outside, cropping nothing."""
        rows = [
            {"month": "Jan", "revenue": 100},
            {"month": "Feb", "revenue": -3},
            {"month": "Mar", "revenue": 60},
            {"month": "Apr", "revenue": -40},
        ]
        spec = self._spec(make_chart, rows, "top", {"bar": {"orientation": "vertical"}})
        condition = self._condition(spec)
        assert condition is not None, "precondition: something is short enough to test"
        assert "> 0" in condition["test"], (
            f"edge positions must only measure upward bars, got: {condition['test']}"
        )
        assert "abs(" not in condition["test"], (
            f"abs() would hide a negative bar's label drawn clear of it: {condition['test']}"
        )

    def test_middle_still_measures_both_signs(self, make_chart):
        """`middle` is datum/2 — inside the bar whichever way it points."""
        rows = [
            {"month": "Jan", "revenue": 100},
            {"month": "Feb", "revenue": -3},
            {"month": "Mar", "revenue": 60},
        ]
        spec = self._spec(
            make_chart, rows, "middle", {"bar": {"orientation": "vertical"}}
        )
        condition = self._condition(spec)
        assert condition is not None and "abs(" in condition["test"], (
            f"middle must measure magnitude regardless of sign, got: {condition}"
        )

    def test_unstacked_bottom_is_fit_tested(self, make_chart):
        """Guards _FIT_TESTED_POSITIONS against silently losing a member."""
        rows = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 2}]
        spec = self._spec(
            make_chart,
            rows,
            "bottom",
            {"bar": {"orientation": "vertical"}},
            height=120.0,
        )
        assert self._condition(spec) is not None, (
            "an unstacked bottom label sits inside the bar and must be fit-tested"
        )

    def test_stacked_bottom_is_not_fit_tested(self, make_chart):
        """Stacked `bottom` pins every label to the zero baseline.

        The segment height the test would measure says nothing about where the
        label lands, so measuring it would compound a pre-existing placement
        bug rather than contain one.
        """
        board_rs, board_ctx = _board_with_bar_labels({"position": "bottom"})
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="seg",
            style={"bar": {"stack": "zero", "orientation": "vertical"}},
        )
        resolve(chart, _SEGMENT_FIT_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            _SEGMENT_FIT_DATA,
            board_style=board_rs,
            chart_style_context=board_ctx,
            height=120.0,
        )
        for layer in _text_layers(spec):
            assert layer["encoding"]["text"].get("condition") is None, (
                "stacked bottom labels do not sit in the segment being measured"
            )


class TestAuthoredLayersSpecShape:
    """A bar with authored `layers:` is restructured before features run.

    render_cartesian_overlay moves the measure encoding — stack offset and
    scale both — onto the bar sublayer, leaving only category and order on the
    outer spec. Reading the outer spec alone is how the wrong-offset defect
    reappeared on this shape after being fixed on the plain one.
    """

    _ROWS = [
        {"month": "Jan", "seg": "Free", "revenue": 100, "goal": 90},
        {"month": "Jan", "seg": "Paid", "revenue": 50, "goal": 90},
        {"month": "Feb", "seg": "Free", "revenue": 80, "goal": 95},
        {"month": "Feb", "seg": "Paid", "revenue": 4, "goal": 95},
    ]

    # A mixed-sign stack splits the bar into sign-filtered sublayers, so with
    # `layers:` on top the measure encoding sits TWO levels down — the only
    # shape that exercises _bar_channel_encoding's recursion.
    _MIXED_ROWS = [
        {"month": "Jan", "seg": "Free", "revenue": 100, "goal": 90},
        {"month": "Jan", "seg": "Paid", "revenue": -40, "goal": 90},
        {"month": "Feb", "seg": "Free", "revenue": 80, "goal": 95},
        {"month": "Feb", "seg": "Paid", "revenue": -3, "goal": 95},
    ]

    def _pane(self, make_chart, stack, height=300.0, rows=None):
        rows = self._ROWS if rows is None else rows
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="seg",
            style={"bar": {"stack": stack, "orientation": "vertical"}},
            layers=[{"type": "line", "y": "goal"}],
        )
        resolve(chart, rows, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            rows,
            board_style=board_rs,
            chart_style_context=board_ctx,
            height=height,
        )
        return _layered_pane(spec)

    @pytest.mark.parametrize("stack", ["zero", "center"])
    def test_offset_survives_the_overlay_restructure(self, make_chart, stack):
        pane = self._pane(make_chart, stack)
        assert "y" not in pane["encoding"], (
            "precondition: the overlay moved the measure encoding off the outer spec"
        )
        stack_transform = next(t for t in pane["transform"] if "stack" in t)
        assert stack_transform["offset"] == stack, (
            f"label offset must follow the bars' {stack!r}, got "
            f"{stack_transform.get('offset')!r} — a null offset is the outer-only read"
        )

    def test_fit_test_still_finds_the_measure_span(self, make_chart):
        """The scale moves with the measure encoding, so the span must follow it."""
        pane = self._pane(make_chart, "zero", height=120.0)
        conditions = [
            layer["encoding"]["text"].get("condition") for layer in _text_layers(pane)
        ]
        assert any(c is not None for c in conditions), (
            "the 4-unit segment is too short here; losing the span silently "
            "stands the fit test down for every layered bar chart"
        )

    @pytest.mark.parametrize("stack", ["zero", "center"])
    def test_mixed_sign_with_layers_reaches_the_nested_bar(self, make_chart, stack):
        """The only shape where the measure encoding is two levels down.

        A mixed-sign stack splits into sign-filtered sublayers, and `layers:`
        wraps that split in another layered spec. Without the recursion this
        emits `offset: null` against `center` bars and drops the fit test —
        rounds 1 and 3's CRITICALs recombined, on a shape nothing else covers.
        """
        pane = self._pane(make_chart, stack, height=120.0, rows=self._MIXED_ROWS)
        stack_transform = next(t for t in pane["transform"] if "stack" in t)
        assert stack_transform["offset"] == stack, (
            f"nested bar layer's offset must follow the bars' {stack!r}, got "
            f"{stack_transform.get('offset')!r}"
        )
        conditions = [
            layer["encoding"]["text"].get("condition") for layer in _text_layers(pane)
        ]
        assert any(c is not None for c in conditions), (
            "the -3 segment is short enough to test; a lost span silently "
            "stands the fit test down on this shape"
        )


# ---------------------------------------------------------------------------
# The fit test measures the plot rectangle, not the slot
# ---------------------------------------------------------------------------

# The "New Starts" shape the plot-rect bug was found on: a 283.68px slot with a
# pinned [0, 800] measure axis. Chrome (title, axis labels, padding) takes the
# real plot rect well below the slot, so a 40-unit segment sits in the wedge
# between the slot-derived threshold and the true one.
#
# These render through `render_chart` rather than `generate_vega_lite_spec`.
# The gap only exists after the layout pass corrects the pane height, and
# `generate_vega_lite_spec` is a standalone entry point that never runs it — a
# spec built there measures its plot rect at the full slot, so the bug is
# invisible to it.
_SLOT = 283.68
_SPAN = 800.0
_LABEL_FONT_SIZE = 11.0  # authored on the fixture below, not read from the theme


def _required_px() -> float:
    """One line of label, in pixels: the authored font size x the live multiplier.

    Read rather than pinned — `label_fit_line_height_multiplier` is tunable
    config, and hardcoding it here would silently rot if it moved.
    """
    from dbt_charts.core.compile.config import get_chart_rendering

    return _LABEL_FONT_SIZE * get_chart_rendering().bar.label_fit_line_height_multiplier


_WEDGE_SEGMENT = 40  # > 35.67, so today it is kept; < the true threshold

_STACKED_ROWS = [
    {"month": "Jan", "seg": "Free", "revenue": 300},
    {"month": "Jan", "seg": "Paid", "revenue": 300},
    {"month": "Aug", "seg": "Free", "revenue": _WEDGE_SEGMENT},
    {"month": "Aug", "seg": "Paid", "revenue": 300},
]
_SINGLE_ROWS = [
    {"month": "Jan", "revenue": 700},
    {"month": "Aug", "revenue": _WEDGE_SEGMENT},
]


def _fit_chart(make_chart, *, stacked, orientation="vertical"):
    board_rs, board_ctx = _board_with_bar_labels(
        {"position": "middle", "font": FontStyle(size=_LABEL_FONT_SIZE)}
    )
    rows = _STACKED_ROWS if stacked else _SINGLE_ROWS
    bar: dict[str, Any] = {"orientation": orientation}
    extra: dict[str, Any] = {}
    if stacked:
        bar["stack"] = "zero"
        extra["color"] = "seg"
    axis = "axis_x" if orientation == "horizontal" else "axis_y"
    chart = make_chart(
        "bar",
        x="month",
        y="revenue",
        title="Monthly starts by plan",
        style={"bar": bar, axis: {"scale": {"continuous": {"domain": [0, _SPAN]}}}},
        **extra,
    )
    resolve(chart, rows, chart_style_context=board_ctx)
    return chart, rows, board_rs, board_ctx


def _fit_svg(make_chart, *, stacked):
    chart, rows, board_rs, board_ctx = _fit_chart(make_chart, stacked=stacked)
    return render_chart(
        chart,
        board_rs,
        board_ctx,
        rows,
        format="svg",
        width=600.0,
        height=_SLOT,
    )


def _fit_spec(make_chart, *, stacked, orientation="vertical"):
    chart, rows, board_rs, board_ctx = _fit_chart(
        make_chart, stacked=stacked, orientation=orientation
    )
    return generate_vega_lite_spec(
        chart,
        rows,
        board_style=board_rs,
        chart_style_context=board_ctx,
        height=_SLOT,
    )


def _svg_texts(svg: str) -> list[str]:
    import html

    return [html.unescape(m) for m in re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S)]


def _svg_plot_rect(svg: str) -> float:
    """Pixel distance between the two pinned measure-axis ticks in the render.

    Vertical bars only — the measure axis is y. Collected as a list so a third
    text node reading "0" or "800" trips the assert instead of overwriting a
    dict entry and silently measuring the wrong pair.
    """
    rows = re.findall(
        r'<text[^>]*transform="translate\([-\d.]+,([-\d.]+)\)"[^>]*>([^<]*)</text>',
        svg,
    )
    ticks = [(label, float(y)) for y, label in rows if label in ("0", "800")]
    assert sorted(label for label, _ in ticks) == ["0", "800"], (
        f"expected exactly the two pinned ticks in the render, got {ticks}"
    )
    return abs(dict(ticks)["0"] - dict(ticks)["800"])


class TestFitTestMeasuresThePlotRect:
    """A segment is measured against the rectangle it is drawn in.

    The slot also holds the title, subtitle, legend, axis labels and padding.
    Dividing by the slot makes the threshold too small — and the threshold
    grows as the height shrinks, so every comparison against the slot
    under-counts what needs hiding. That direction is the bug.
    """

    @pytest.mark.parametrize("stacked", [False, True])
    def test_slot_is_meaningfully_taller_than_the_plot_rect(self, make_chart, stacked):
        """Precondition: without this gap the rest of the class passes vacuously."""
        plot_rect = _svg_plot_rect(_fit_svg(make_chart, stacked=stacked))
        assert plot_rect < _SLOT * 0.9, (
            f"chrome must take a real bite out of the {_SLOT}px slot, "
            f"got a {plot_rect}px plot rect"
        )

    @pytest.mark.parametrize("stacked", [False, True])
    def test_segment_between_the_two_thresholds_is_hidden(self, make_chart, stacked):
        """The bug, as a render.

        11px x 1.15 = 12.65px required. Against the 283.68px slot the threshold
        is 35.67 units, so the 40-unit segment is kept — with only
        40/800 * plot_rect of real room, far less than it needs. Against the
        plot rect the threshold clears 40 and the label correctly drops.

        Both shapes matter: `stacked=True` carries a series-label rail and so
        renders wrapped in an hconcat, where `height` has to resolve to the
        chart pane's own plot rect rather than the outer concat pane. A
        spec-level assertion cannot catch a wrong resolution there.
        """
        svg = _fit_svg(make_chart, stacked=stacked)
        plot_rect = _svg_plot_rect(svg)
        required_px = _required_px()
        slot_threshold = required_px * _SPAN / _SLOT
        true_threshold = required_px * _SPAN / plot_rect
        assert slot_threshold < _WEDGE_SEGMENT < true_threshold, (
            f"precondition: {_WEDGE_SEGMENT} must sit in the wedge between the "
            f"slot-derived {slot_threshold:.2f} and the true {true_threshold:.2f}"
        )
        room_px = _WEDGE_SEGMENT / _SPAN * plot_rect
        assert str(_WEDGE_SEGMENT) not in _svg_texts(svg), (
            f"a {_WEDGE_SEGMENT}-unit segment has only {room_px:.1f}px of room "
            f"for a {required_px:.2f}px label and must not be labeled"
        )

    @pytest.mark.parametrize(("stacked", "roomy"), [(False, "700"), (True, "300")])
    def test_segment_with_real_room_keeps_its_label(self, make_chart, stacked, roomy):
        """The stricter test must not start eating labels that fit."""
        texts = _svg_texts(_fit_svg(make_chart, stacked=stacked))
        assert roomy in texts, (
            f"a {roomy}-unit segment has room several times over and must stay "
            f"labeled, got {texts}"
        )

    def test_threshold_defers_the_geometry_to_render_time(self, make_chart):
        """Only `height` may carry geometry; the span stays Python-side.

        `domain('y')` returns NaN the moment a chart is wrapped in a concat:
        Vega-Lite hoists child scales to the root scope and renames them
        `concat_<i>_y`, so a literal `y` names a scale that does not exist.
        """
        layers = _text_layers(_fit_spec(make_chart, stacked=True))
        condition = layers[0]["encoding"]["text"].get("condition")
        assert condition is not None, "precondition: something is too short here"
        test = condition["test"]
        assert "height" in test, (
            f"the threshold must read the render-time plot rect: {test}"
        )
        assert "domain(" not in test, f"domain() is NaN under every concat wrap: {test}"

    def test_stacked_bar_renders_wrapped_in_hconcat(self, make_chart):
        """Precondition for the wrapped half of the parametrized cases above."""
        spec = _fit_spec(make_chart, stacked=True)
        assert "hconcat" in spec, (
            f"the series-label rail must wrap this in hconcat, got {sorted(spec)}"
        )

    def test_vconcat_pane_never_carries_a_fit_tested_label(self, make_chart):
        """No fit condition reaches a vconcat pane.

        `height` reads 0 inside a vconcat child, so a condition there would
        blank every inside label at once. Today that is unreachable for a
        single reason: `top_rail` (vconcat) is gated on
        `orientation == "horizontal"` in endpoint_labels.py, and `_fit_hide_test`
        stands down on horizontal bars, which fit against label WIDTH.

        So this exercises the horizontal stand-down on a genuinely
        vconcat-wrapped chart — it does NOT construct a vertical bar in a
        vconcat, which the render tree cannot currently produce. If horizontal
        bars ever gain fit-test support, this is the test that must be revisited
        before they do.
        """
        spec = _fit_spec(make_chart, stacked=True, orientation="horizontal")
        assert "vconcat" in spec, (
            "precondition: a horizontal stacked bar with a series-label rail "
            f"must still wrap in vconcat, or this test is vacuous: {sorted(spec)}"
        )
        panes = spec["vconcat"]
        for pane in panes:
            for layer in pane.get("layer", []):
                if (layer.get("mark") or {}).get("type") != "text":
                    continue
                text_enc = layer.get("encoding", {}).get("text", {})
                assert text_enc.get("condition") is None, (
                    "a fit condition inside a vconcat pane would read height "
                    "as 0 and blank every label"
                )

    def test_faceted_bar_keeps_every_label_that_fits(self, make_chart):
        """A bare `height` reads 0 inside a facet cell — every label would blank.

        Vega-Lite compiles `multiples:` to a facet root carrying
        `child_width`/`child_height` and no `height` of its own, so a literal
        `height` in a mark expression falls through to a root signal the facet
        root never sets. The threshold would go infinite and take every inside
        label with it.

        Rendered, not spec-level: the whole failure is a signal resolving to
        the wrong value, which a spec assertion cannot see.
        """
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        # 287 cannot be an axis tick, so the count is labels and nothing else.
        rows = [
            {"month": "Jan", "region": "East", "revenue": 287},
            {"month": "Feb", "region": "East", "revenue": 287},
            {"month": "Jan", "region": "West", "revenue": 287},
            {"month": "Feb", "region": "West", "revenue": 287},
        ]
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            multiples={"columns": "region"},
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, rows, chart_style_context=board_ctx)
        svg = render_chart(
            chart, board_rs, board_ctx, rows, format="svg", width=800.0, height=400.0
        )
        assert _svg_texts(svg).count("287") == 4, (
            "all four 287-unit segments must keep their labels in a facet; "
            f"got {_svg_texts(svg)}"
        )

    def test_faceted_bar_names_the_facet_cell_height_signal(self, make_chart):
        """The emitted expression must not carry a bare `height` on a facet."""
        board_rs, board_ctx = _board_with_bar_labels({"position": "middle"})
        rows = [
            {"month": "Jan", "region": "East", "revenue": 300},
            {"month": "Aug", "region": "East", "revenue": 4},
            {"month": "Jan", "region": "West", "revenue": 300},
            {"month": "Aug", "region": "West", "revenue": 4},
        ]
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            multiples={"columns": "region"},
            style={"bar": {"orientation": "vertical"}},
        )
        resolve(chart, rows, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            rows,
            board_style=board_rs,
            chart_style_context=board_ctx,
            height=400.0,
        )
        unit = spec["spec"]
        layers = [
            layer
            for layer in unit.get("layer", [])
            if (layer.get("mark") or {}).get("type") == "text"
        ]
        assert layers, f"precondition: the facet unit must carry a label layer: {unit}"
        test = layers[0]["encoding"]["text"]["condition"]["test"]
        assert "child_height" in test, (
            f"a facet cell's plot rect is child_height, not height: {test}"
        )
        assert not re.search(r"(?<!child_)\bheight\b", test), (
            f"a bare `height` reads 0 inside a facet cell: {test}"
        )


# =============================================================================
# TestDualAxisValueLabelPhantomAxis — a value-label text sublayer must never
# contribute its own y axis. A dual-axis layered chart emits
# resolve.scale.y = "independent" (emitters/_overlay.py), under which every
# sublayer's un-suppressed VL y channel earns its own axis — titled with a
# raw field name nobody authored. The phantom-bearing channel is y whatever
# its type: a vertical chart's label measure is quantitative y, a horizontal
# bar's is its nominal *category* y (the measure sits on the shared x, which
# must NOT be suppressed — an explicit null on a shared scale corrupts VL's
# axis merge).
# =============================================================================

_DUAL_AXIS_DATA = [
    {"month": "Jan", "count_services": 10.0, "count_connections": 100.0},
    {"month": "Feb", "count_services": 20.0, "count_connections": 300.0},
]

_DUAL_AXIS_STACKED_DATA = [
    {"month": "Jan", "channel": "Web", "revenue": 100.0, "target": 500.0},
    {"month": "Jan", "channel": "Mobile", "revenue": 60.0, "target": 500.0},
    {"month": "Feb", "channel": "Web", "revenue": 120.0, "target": 600.0},
    {"month": "Feb", "channel": "Mobile", "revenue": 80.0, "target": 600.0},
]


def _board_with_mark_label_slots(mark_updates: dict[str, dict[str, dict[str, Any]]]):
    """Resolve a board style with label slots configured on several marks.

    ``mark_updates`` maps mark name ("bar"/"line"/"point") to
    {slot attr ("labels"/"total_label"): field updates}.
    """
    compiled = get_theme_style("clarity")
    marks = compiled.charts.marks
    new_marks: dict[str, Any] = {}
    for mark_name, patches in mark_updates.items():
        mark = getattr(marks, mark_name)
        for attr, fields in patches.items():
            mark = mark.model_copy(
                update={attr: getattr(mark, attr).model_copy(update=fields)}
            )
        new_marks[mark_name] = mark
    charts = compiled.charts.model_copy(
        update={"marks": marks.model_copy(update=new_marks)}
    )
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


def _channel_axis_states(
    node: dict[str, Any],
    channel: str,
    _out: list[tuple[Any, str | None, Any]] | None = None,
) -> list[tuple[Any, str | None, Any]]:
    """(mark type, field, axis state) for every sublayer's ``channel`` encoding.

    Axis state is "absent" when no ``axis`` key exists — under an independent
    scale resolution that channel draws its own axis, same as a dict; only an
    explicit None suppresses it."""
    out = [] if _out is None else _out
    for lyr in node.get("layer", []):
        mark = lyr.get("mark")
        mark_type = mark.get("type") if isinstance(mark, dict) else mark
        enc = lyr.get("encoding", {}).get(channel)
        if isinstance(enc, dict) and "field" in enc:
            out.append((mark_type, enc.get("field"), enc.get("axis", "absent")))
        _channel_axis_states(lyr, channel, out)
    return out


class TestDualAxisValueLabelPhantomAxis:
    def _assert_only_authored_axes(self, spec: dict[str, Any]) -> None:
        assert spec.get("resolve", {}).get("scale", {}).get("y") == "independent", (
            f"precondition: dual-axis chart must emit an independent y scale: "
            f"{spec.get('resolve')}"
        )
        assert _has_text_layer(spec), "precondition: value labels must render"
        y_states = _channel_axis_states(spec, "y")
        phantom = [s for s in y_states if s[0] == "text" and s[2] is not None]
        assert not phantom, (
            f"value-label text sublayers must not draw their own y axis: {phantom}"
        )
        axis_bearing = [s for s in y_states if s[2] is not None]
        assert len(axis_bearing) == 2, (
            f"only the base's and the layer's authored axes may draw: {axis_bearing}"
        )
        # The x scale is never resolved independent, so it is always shared —
        # and an explicit axis: None on a shared scale corrupts VL's axis
        # merge. The suppression must not leak onto x.
        x_nulled = [
            s
            for s in _channel_axis_states(spec, "x")
            if s[0] == "text" and s[2] is None
        ]
        assert not x_nulled, (
            f"a text sublayer must never null the shared x axis: {x_nulled}"
        )

    @pytest.mark.parametrize(
        "position", ["above", "middle", "bottom", "middle_aligned"]
    )
    def test_bar_base_and_line_layer_labels_draw_no_phantom_axis(
        self, make_chart, position: str
    ) -> None:
        """Dashboard-1291 shape: dual-axis bar + line, value labels on both."""
        board_rs, board_ctx = _board_with_mark_label_slots(
            {
                "bar": {"labels": {"visible": True, "position": position}},
                "line": {"labels": {"visible": True}},
            }
        )
        chart = make_chart(
            "bar",
            x="month",
            y="count_services",
            style={"orientation": "vertical"},
            layers=[
                {
                    "type": "line",
                    "y": "count_connections",
                    "label": "Connections",
                    "axis_y": {"position": "right"},
                }
            ],
        )
        resolve(chart, _DUAL_AXIS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _DUAL_AXIS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        self._assert_only_authored_axes(spec)

    @pytest.mark.parametrize("position", ["top", "middle"])
    def test_stacked_base_segment_and_total_labels_draw_no_phantom_axis(
        self, make_chart, position: str
    ) -> None:
        board_rs, board_ctx = _board_with_mark_label_slots(
            {
                "bar": {
                    "labels": {"visible": True, "position": position},
                    "total_label": {"visible": True},
                }
            }
        )
        chart = make_chart(
            "bar",
            x="month",
            y="revenue",
            color="channel",
            style={"orientation": "vertical", "stack": "zero"},
            layers=[
                {
                    "type": "line",
                    "y": "target",
                    "label": "Target",
                    "axis_y": {"position": "right"},
                }
            ],
        )
        resolve(chart, _DUAL_AXIS_STACKED_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart,
            _DUAL_AXIS_STACKED_DATA,
            board_style=board_rs,
            chart_style_context=board_ctx,
        )
        self._assert_only_authored_axes(spec)

    def test_scatter_base_point_labels_draw_no_phantom_axis(self, make_chart) -> None:
        board_rs, board_ctx = _board_with_mark_label_slots(
            {"point": {"labels": {"visible": True}}}
        )
        chart = make_chart(
            "scatter",
            x="month",
            y="count_services",
            layers=[
                {
                    "type": "line",
                    "y": "count_connections",
                    "label": "Connections",
                    "axis_y": {"position": "right"},
                }
            ],
        )
        resolve(chart, _DUAL_AXIS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _DUAL_AXIS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        self._assert_only_authored_axes(spec)

    def test_horizontal_base_labels_carry_no_axis_key_at_all(self, make_chart) -> None:
        """The shared-scale gate on a HORIZONTAL base, where the channel pair is
        swapped: the measure is on VL x and the category on VL y.

        There is no dual-axis variant of this to test — ``axis_y.position``
        names a left/right side and a horizontal base measures along VL x, so
        that combination raises (see ``test_layer_base_orientation.py``). This
        once ran as a dual-axis case only because the overlay resolved the
        *category* scale independent and left the measure shared across base
        and layer, which is the wrong chart, not a configuration to pin.
        """
        board_rs, board_ctx = _board_with_mark_label_slots(
            {
                "bar": {"labels": {"visible": True}},
                "line": {"labels": {"visible": True}},
            }
        )
        chart = make_chart(
            "bar",
            x="month",
            y="count_services",
            style={"orientation": "horizontal"},
            layers=[{"type": "line", "y": "count_connections", "label": "Connections"}],
        )
        resolve(chart, _DUAL_AXIS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _DUAL_AXIS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert spec.get("resolve", {}).get("scale", {}).get("x") != "independent", (
            f"precondition: this chart must share one measure scale: "
            f"{spec.get('resolve')}"
        )
        assert _has_text_layer(spec), "precondition: value labels must render"
        for channel in ("x", "y"):
            keyed = [
                s
                for s in _channel_axis_states(spec, channel)
                if s[0] == "text" and s[2] != "absent"
            ]
            assert not keyed, (
                f"shared-scale label sublayers must not carry an axis key: {keyed}"
            )

    def test_shared_scale_labels_carry_no_axis_key_at_all(self, make_chart) -> None:
        """The gate itself, pinned directly: on a SHARED-scale layered chart
        (no layer pins a side, no independent resolve) the label sublayer must
        not carry an ``axis`` key in any state — an explicit None there
        corrupts Vega-Lite's axis merge (the merged axis loses its ticks or
        fails to parse)."""
        board_rs, board_ctx = _board_with_mark_label_slots(
            {
                "bar": {"labels": {"visible": True}},
                "line": {"labels": {"visible": True}},
            }
        )
        chart = make_chart(
            "bar",
            x="month",
            y="count_services",
            style={"orientation": "vertical"},
            layers=[{"type": "line", "y": "count_connections", "label": "Connections"}],
        )
        resolve(chart, _DUAL_AXIS_DATA, chart_style_context=board_ctx)
        spec = generate_vega_lite_spec(
            chart, _DUAL_AXIS_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        assert spec.get("resolve", {}).get("scale", {}).get("y") != "independent", (
            f"precondition: this chart must share one y scale: {spec.get('resolve')}"
        )
        assert _has_text_layer(spec), "precondition: value labels must render"
        for channel in ("x", "y"):
            keyed = [
                s
                for s in _channel_axis_states(spec, channel)
                if s[0] == "text" and s[2] != "absent"
            ]
            assert not keyed, (
                f"shared-scale label sublayers must not carry an axis key: {keyed}"
            )


def test_house_register_paints_and_measures_the_sign_after_a_spaced_prefix() -> None:
    from dbt_charts.core.compile.format import resolve_format_parts
    from dbt_charts.core.compile.models.primitives import FormatConfig
    from dbt_charts.core.render.chart.features.value_labels import (
        _house_register_expr,
        painted_label_text,
    )

    fmt = resolve_format_parts(
        FormatConfig(spec="number", prefix="EUR "), None, no_format_default=None
    )
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"value": -1_200_000.0}, {"value": -0.5}]},
        "transform": [
            {"calculate": _house_register_expr("datum.value", fmt), "as": "label"}
        ],
        "mark": "text",
        "encoding": {"text": {"field": "label", "type": "nominal"}},
    }
    assert sorted(_render_texts(spec)) == ["EUR −0.5", "EUR −1.2mn"]
    assert painted_label_text(-1_200_000.0, fmt, is_house=True) == "EUR −1.2mn"
    assert painted_label_text(-0.5, fmt, is_house=True) == "EUR −0.5"
