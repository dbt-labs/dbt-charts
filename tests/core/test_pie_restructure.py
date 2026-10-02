"""TDD tests for the pie style restructure (ADR-005 / ADR-006).

Covers:
- axis_x/axis_y rejection on pie
- style.arc stale-key hint
- style.inner_radius flat field (pie-only; rejected on bar)
- chart-root inner_radius moved to style
- type:donut alias normalizes to style.inner_radius
- total copy vs paint split
- total.format theme-alias resolution
- total.format FormatConfig acceptance
- minimal pie compile regression
- default theme pie shape
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored import AuthoredChart
from dbt_charts.core.compile.models.chart.normalized import PieChart
from dbt_charts.core.compile.resolve import resolve

_chart_patch_adapter = TypeAdapter(AuthoredChart)


@pytest.fixture(autouse=True)
def _reset():
    from dbt_charts.core.compile.config import reset_config

    reset_config()
    yield
    reset_config()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PIE_BARE = {
    "type": "pie",
    "theta": "value",
    "color": "category",
    "query": "q",
}

_BAR_BARE = {
    "type": "bar",
    "x": "date",
    "y": "value",
    "query": "q",
}


def _patch(**overrides):
    """Build a AuthoredChart dict for model_validate."""
    data = {**_PIE_BARE, **overrides}
    return _chart_patch_adapter.validate_python(data)


def _patch_bar(**overrides):
    data = {**_BAR_BARE, **overrides}
    return _chart_patch_adapter.validate_python(data)


def _compile_yaml(yaml_content: str):
    from dbt_charts.core.compile import compile as df_compile

    return df_compile(yaml_content)


_MINIMAL_PIE_YAML = """
queries:
  q:
    columns: [cat, val]
    values:
      - [A, 40]
      - [B, 60]
charts:
  c:
    query: q
    type: pie
    theta: val
    color: cat
rows:
  - c
"""

# ---------------------------------------------------------------------------
# 1. style.axis_x / style.axis_y rejected on pie
# ---------------------------------------------------------------------------


class TestAxisRejectionOnPie:
    """style.axis_x and style.axis_y are not fields on PieChartStylePatch — extra_forbidden."""

    def test_axis_x_on_pie_raises(self):
        from pydantic import ValidationError

        # PieChartStylePatch has no axis_x — extra_forbidden rejects it structurally.
        with pytest.raises(ValidationError, match="axis_x"):
            _patch(style={"axis_x": {"format": "%b"}})

    def test_axis_y_on_pie_raises(self):
        from pydantic import ValidationError

        # PieChartStylePatch has no axis_y — extra_forbidden rejects it structurally.
        with pytest.raises(ValidationError, match="axis_y"):
            _patch(style={"axis_y": {"format": ",.2f"}})

    def test_axis_x_on_bar_does_not_raise(self):
        """axis_x is valid on cartesian charts — only rejected on pie."""
        _patch_bar(style={"axis_x": {"labels": {"format": "%b"}}})  # must not raise


# ---------------------------------------------------------------------------
# 2. style.arc (stale key) → hint
# ---------------------------------------------------------------------------


class TestStaleArcKey:
    """style.arc has been removed; stale key raises with migration hint."""

    def test_style_arc_on_pie_raises(self):
        from pydantic import ValidationError

        # PieChartStylePatch has no 'arc' field — extra_forbidden rejects it.
        with pytest.raises(ValidationError, match="arc"):
            _patch(style={"arc": {"gap": 0.02}})

    def test_style_slice_on_pie_is_accepted(self):
        """style.marks.slice is the flat per-family shape for PieChartStylePatch."""
        _patch(style={"marks": {"slice": {"gap": 0.02}}})

    def test_style_slice_gap_propagates_to_mark(self):
        """Regression: style.pie.marks.slice.gap (formerly pad_angle) reaches the VL mark."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.chart.normalized import PieChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        compiled = get_theme_style("clarity")
        # Override marks.slice.gap — gap lives at the global level after migration
        global_slice = compiled.charts.marks.slice
        updated_slice_mark = global_slice.model_copy(update={"gap": 0.07})
        updated_marks = compiled.charts.marks.model_copy(
            update={"slice": updated_slice_mark}
        )
        charts = compiled.charts.model_copy(update={"marks": updated_marks})
        _rs, _ctx = resolve_style_and_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = PieChart(
            id="t",
            type="pie",
            theta="val",
            color="cat",
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        spec = generate_vega_lite_spec(
            chart,
            [{"cat": "A", "val": 1}],
            width=400,
            board_style=_rs,
            chart_style_context=_ctx,
        )
        mark = spec.get("mark") or spec.get("layer", [{}])[0].get("mark", {})
        assert mark.get("padAngle") == pytest.approx(0.07)


# ---------------------------------------------------------------------------
# 3. style.inner_radius — pie-only field
# ---------------------------------------------------------------------------


class TestStyleInnerRadius:
    """style.inner_radius is a pie-only flat field (rejected on bar)."""

    def test_inner_radius_in_style_on_pie_is_accepted(self):
        # Flat per-family shape: inner_radius directly on PieChartStylePatch.
        _patch(style={"inner_radius": 0.5})  # must not raise

    def test_inner_radius_in_style_on_bar_raises(self):
        from pydantic import ValidationError

        # BarChartStylePatch has no inner_radius — extra_forbidden rejects it.
        with pytest.raises(ValidationError, match="inner_radius"):
            _patch_bar(style={"inner_radius": 0.5})

    def test_style_inner_radius_flows_to_resolved_chart(self):
        """style.inner_radius reaches ResolvedChartsStyle.pie.inner_radius."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        compiled = get_theme_style("clarity")
        chart = PieChart(
            id="t",
            type="pie",
            theta="val",
            color="cat",
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
            style={"inner_radius": 0.4},
        )
        board_style = resolve_chart_style_context(compiled)
        rc = resolve(chart, [{"cat": "A", "val": 1}], chart_style_context=board_style)
        assert rc.style.inner_radius == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# 4. chart-root inner_radius rejected with hint
# ---------------------------------------------------------------------------


class TestChartRootInnerRadius:
    """inner_radius at chart root is rejected; authors must use style.inner_radius."""

    def test_chart_root_inner_radius_raises_on_pie(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="inner_radius"):
            _patch(inner_radius=0.5)

    def test_chart_root_inner_radius_raises_on_any_type(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="inner_radius"):
            _patch_bar(inner_radius=0.5)


# ---------------------------------------------------------------------------
# 5. type:donut alias → style.inner_radius
# ---------------------------------------------------------------------------


class TestDonutAlias:
    """type:donut normalizes to type:pie with style.inner_radius=0.6."""

    def test_donut_compiles_successfully(self):
        result = _compile_yaml(
            """
queries:
  q:
    columns: [cat, val]
    values:
      - [A, 40]
charts:
  c:
    query: q
    type: donut
    theta: val
    color: cat
rows:
  - c
"""
        )
        assert result.success, result.errors

    def test_donut_normalizes_to_pie_type(self):
        result = _compile_yaml(
            """
queries:
  q:
    columns: [cat, val]
    values: [[A, 40]]
charts:
  c:
    query: q
    type: donut
    theta: val
    color: cat
rows:
  - c
"""
        )
        assert result.success
        assert result.board.charts["c"].type == "pie"

    def test_donut_normalizes_style_inner_radius_to_default(self):
        """Donut alias lands inner_radius at style.inner_radius, not chart root."""
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        result = _compile_yaml(
            """
queries:
  q:
    columns: [cat, val]
    values: [[A, 40]]
charts:
  c:
    query: q
    type: donut
    theta: val
    color: cat
rows:
  - c
"""
        )
        assert result.success
        chart = result.board.charts["c"]
        rc = resolve(
            chart,
            [{"cat": "A", "val": 40}],
            chart_style_context=resolve_chart_style_context(get_theme_style()),
        )
        assert rc.style.inner_radius == pytest.approx(0.6)

    def test_explicit_donut_inner_radius_is_preserved(self):
        """style.inner_radius on a donut chart (flat per-family shape) preserves author's value."""
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        result = _compile_yaml(
            """
queries:
  q:
    columns: [cat, val]
    values: [[A, 40]]
charts:
  c:
    query: q
    type: donut
    theta: val
    color: cat
    style:
      inner_radius: 0.3
rows:
  - c
"""
        )
        assert result.success
        chart = result.board.charts["c"]
        rc = resolve(
            chart,
            [{"cat": "A", "val": 40}],
            chart_style_context=resolve_chart_style_context(get_theme_style()),
        )
        assert rc.style.inner_radius == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# 6. chart-root total (copy) vs style.total (paint)
# ---------------------------------------------------------------------------


class TestTotalCopyVsPaint:
    """chart.total holds copy (label only); style.total.value holds paint + format."""

    def test_chart_root_total_label_compiles(self):
        result = _compile_yaml(
            """
queries:
  q:
    columns: [cat, val]
    values: [[A, 40]]
charts:
  c:
    query: q
    type: pie
    theta: val
    color: cat
    total:
      label: "Total"
rows:
  - c
"""
        )
        assert result.success
        chart = result.board.charts["c"]
        assert chart.total is not None
        assert chart.total.label == "Total"

    def test_chart_root_total_format_migrates_to_style(self):
        """The old chart-root total.format grammar still compiles -- migrated in
        memory to style.total.value.format, not rejected outright."""
        result = _compile_yaml(
            """
queries:
  q:
    columns: [cat, val]
    values: [[A, 40]]
charts:
  c:
    query: q
    type: pie
    theta: val
    color: cat
    total:
      label: "Total"
      format: "$,.0f"
rows:
  - c
"""
        )
        assert result.success
        chart = result.board.charts["c"]
        assert chart.total is not None
        assert chart.total.label == "Total"
        assert chart.style is not None
        assert chart.style.total.value.format == "$,.0f"

    def test_total_format_rejected_on_chart_total_model(self):
        """format: no longer belongs on ChartTotal -- it's a style field now."""
        from dbt_charts.core.compile.models.chart.authored import ChartTotal

        with pytest.raises(ValueError, match="format"):
            ChartTotal.model_validate({"label": "Total", "format": "$,.0f"})

    def test_style_total_font_is_paint_slot(self):
        """style.total.font is the paint slot for the donut center (flat per-family shape)."""
        _patch(
            style={"total": {"value": {"font": {"size": 24}}}},
        )  # must not raise

    def test_style_total_value_format_is_paint_slot(self):
        """style.total.value.format is where the donut center format lives now."""
        _patch(
            style={"total": {"value": {"format": "currency"}}},
        )  # must not raise

    def test_style_total_label_format_rejected(self):
        """The label slot has no format field -- it's caption text, never numeric."""
        with pytest.raises(ValueError, match="format"):
            _patch(style={"total": {"label": {"format": "currency"}}})

    def test_style_total_flows_through_cascade(self):
        """style.total.value.font.size reaches effective.pie.total.value.font.size."""
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        compiled = get_theme_style("clarity")
        pie = compiled.charts.pie
        updated_value_font = pie.total.value.font.model_copy(update={"size": 99.0})
        updated_total = pie.total.model_copy(
            update={
                "value": pie.total.value.model_copy(update={"font": updated_value_font})
            }
        )
        updated_pie = pie.model_copy(update={"total": updated_total})
        charts = compiled.charts.model_copy(update={"pie": updated_pie})
        ctx = resolve_chart_style_context(
            compiled.model_copy(update={"charts": charts})
        )
        chart = PieChart(
            id="t",
            type="pie",
            theta="val",
            color="cat",
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        rc = resolve(chart, [{"cat": "A", "val": 1}], chart_style_context=ctx)
        assert rc.style.total_style.value.font.size == pytest.approx(99.0)

    def test_board_tier_total_format_flows_through_cascade(self):
        """style.charts.pie.total.value.format (board/theme tier) reaches
        the resolved chart -- the whole point of moving format into style is
        that it gets this cascade tier for free, unlike the old chart-root
        ChartTotal.format which only ever existed per-chart."""
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        board_patch = StylePatch.model_validate(
            {"charts": {"pie": {"total": {"value": {"format": ",.1f"}}}}}
        )
        _, ctx = resolve_style_and_context(get_theme_style(), board_patch)
        chart = PieChart(id="t", type="pie", theta="val", color="cat")
        rc = resolve(chart, [{"cat": "A", "val": 1}], chart_style_context=ctx)
        assert rc.style.total_style.value.format.spec == ",.1f"

    def test_chart_local_total_format_wins_over_board_tier(self):
        """A chart-local style.total.value.format overrides the board/theme
        tier, same precedence chart-local always wins at."""
        from dbt_charts.core.compile.models.style.authored import (
            PieChartStylePatch,
            StylePatch,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        board_patch = StylePatch.model_validate(
            {"charts": {"pie": {"total": {"value": {"format": ",.1f"}}}}}
        )
        _, ctx = resolve_style_and_context(get_theme_style(), board_patch)
        chart = PieChart(
            id="t",
            type="pie",
            theta="val",
            color="cat",
            style=PieChartStylePatch.model_validate(
                {"total": {"value": {"format": ",.2f"}}}
            ),
        )
        rc = resolve(chart, [{"cat": "A", "val": 1}], chart_style_context=ctx)
        assert rc.style.total_style.value.format.spec == ",.2f"


# ---------------------------------------------------------------------------
# 7. total.format theme-alias resolution
# ---------------------------------------------------------------------------


class TestTotalFormatResolution:
    """style.total.value.format routes through resolve_format so aliases work."""

    def test_total_format_currency_alias_resolves_in_spec(self):
        """style.total.value.format: 'currency' must resolve to a format string, not literal."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import ChartTotal
        from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.style.authored import PieChartStylePatch
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
        from dbt_charts.core.render.chart.emitters.pie import PieEmitter

        chart = PieChart(
            id="t",
            type="pie",
            theta="val",
            color="cat",
            total=ChartTotal.model_validate({"label": "Total"}),
            style=PieChartStylePatch.model_validate(
                {"total": {"value": {"format": "currency"}}}
            ),
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        data = [{"cat": "A", "val": 100}, {"cat": "B", "val": 200}]
        rc = resolve(
            chart,
            data,
            chart_style_context=resolve_chart_style_context(get_theme_style()),
        )
        assert isinstance(rc, ResolvedPieChart)
        mapped = PieEmitter().emit(rc, _DEFAULT_BOX, regroup((), data))
        # If format was passed raw, it would still be "currency" string.
        # After fix, it must be the resolved d3 format spec (not "currency").
        value_layer = mapped.layers[1]
        emitted_format = value_layer.encoding["text"].get("format")
        assert emitted_format != "currency", (
            "style.total.value.format: 'currency' must resolve via theme aliases, not pass raw"
        )
        assert emitted_format is not None

    def test_total_format_d3_string_passes_through(self):
        """A literal d3 format string passes through unchanged."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import ChartTotal
        from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.style.authored import PieChartStylePatch
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
        from dbt_charts.core.render.chart.emitters.pie import PieEmitter

        chart = PieChart(
            id="t",
            type="pie",
            theta="val",
            color="cat",
            total=ChartTotal(),
            style=PieChartStylePatch.model_validate(
                {"total": {"value": {"format": "$,.2f"}}}
            ),
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        data = [{"cat": "A", "val": 100}]
        rc = resolve(
            chart,
            data,
            chart_style_context=resolve_chart_style_context(get_theme_style()),
        )
        assert isinstance(rc, ResolvedPieChart)
        mapped = PieEmitter().emit(rc, _DEFAULT_BOX, regroup((), data))
        value_layer = mapped.layers[1]
        assert value_layer.encoding["text"].get("format") == "$,.2f"

    def test_sub_dollar_total_and_tooltip_format_as_plain_digits(self):
        """A donut whose slices sum below $1 must not misread as SI milli.

        $0.67 painted "$670m" (d3's SI milli prefix colliding case-only with
        the house million grammar) before resolve_format_parts_for_values voted
        the whole slot (theta values + their sum) to the plain-digit
        fallback. Center total and slice tooltip must agree -- they vote on
        the same set.
        """
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.chart.authored import ChartTotal
        from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.style.authored import (
            PieChartStylePatch,
            StylePatch,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )
        from dbt_charts.core.render.chart.emitters.pie import PieEmitter
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec
        from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

        tooltip_patch = StylePatch.model_validate(
            {"charts": {"tooltip": {"format": "currency"}}}
        )
        board_style, ctx = resolve_style_and_context(get_theme_style(), tooltip_patch)
        chart = PieChart(
            id="t",
            type="pie",
            theta="cents",
            color="cat",
            total=ChartTotal.model_validate({"label": "Total"}),
            style=PieChartStylePatch.model_validate(
                {"total": {"value": {"format": "currency"}}}
            ),
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        data = [{"cat": "A", "cents": 0.42}, {"cat": "B", "cents": 0.25}]
        rc = resolve(chart, data, chart_style_context=ctx)
        assert isinstance(rc, ResolvedPieChart)

        # Center total: baked spec must be the plain-digit fallback.
        mapped = PieEmitter().emit(rc, _DEFAULT_BOX, regroup((), data))
        value_layer = mapped.layers[1]
        assert (
            value_layer.encoding["text"].get("format")
            == PREDEFINED_SPECS["currency_full"]
        )

        # Slice tooltip: the theta encoding's format must match.
        assert rc.style.tooltip_format.spec == PREDEFINED_SPECS["currency_full"]

        import vl_convert as vlc

        spec = generate_vega_lite_spec(
            chart,
            data,
            width=600,
            board_style=board_style,
            chart_style_context=ctx,
        )
        svg = vlc.vegalite_to_svg(spec)
        assert "$0.67" in svg
        assert "670m" not in svg


# ---------------------------------------------------------------------------
# 8. style.total.value.format accepts FormatConfig
# ---------------------------------------------------------------------------


class TestTotalFormatConfig:
    """style.total.value.format accepts FormatConfig (not just plain strings)."""

    def test_total_format_as_format_config_is_accepted(self):
        from dbt_charts.core.compile.models.style.theme import TotalValueSlotStyle

        value = TotalValueSlotStyle.model_validate(
            {"format": {"spec": ",.2s", "prefix": "▲ "}}
        )
        assert value.format is not None

    def test_total_format_config_prefix_propagates_to_spec(self):
        """FormatConfig with prefix reaches the VL encoding. d3's grammar admits only
        "$"/"#" as a spec's own symbol character (libs/d3-format's spec.py).
        """
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import ChartTotal
        from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.style.authored import PieChartStylePatch
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
        from dbt_charts.core.render.chart.emitters.pie import PieEmitter

        chart = PieChart(
            id="t",
            type="pie",
            theta="val",
            color="cat",
            total=ChartTotal(),
            style=PieChartStylePatch.model_validate(
                {"total": {"value": {"format": {"spec": ",.2s", "prefix": "▲ "}}}}
            ),
            query=SqlQuery(sql="SELECT 1", source="src"),
            query_name="q",
        )
        data = [{"cat": "A", "val": 100}]
        rc = resolve(
            chart,
            data,
            chart_style_context=resolve_chart_style_context(get_theme_style()),
        )
        assert isinstance(rc, ResolvedPieChart)
        mapped = PieEmitter().emit(rc, _DEFAULT_BOX, regroup((), data))
        value_layer = mapped.layers[1]
        text_enc = value_layer.encoding["text"]
        # The prefix has nowhere to live inside a plain text.format d3 spec,
        # so it composes into a calculate transform instead.
        calc_field = text_enc["field"]
        calculate_exprs = [
            t["calculate"] for t in value_layer.transforms if t.get("as") == calc_field
        ]
        assert len(calculate_exprs) == 1
        # json.dumps escapes non-ASCII by default, so check for the decoded
        # prefix inside the expression's JSON-quoted literal.
        assert '"\\u25b2 "' in calculate_exprs[0]


# ---------------------------------------------------------------------------
# 9. Minimal pie compile regression
# ---------------------------------------------------------------------------


class TestMinimalPieCompile:
    """A minimal pie chart compiles after restructure without errors."""

    def test_minimal_pie_compiles(self):
        result = _compile_yaml(_MINIMAL_PIE_YAML)
        assert result.success, result.errors

    def test_pie_with_style_slice_compiles(self):
        # aspect_ratio is excluded from PieChartStylePatch (geometry lives at chart
        # root for cartesian charts only). Pie sizing is theme-controlled via
        # PieChartStyle.aspect_ratio. Only paint-only fields belong in style: here.
        result = _compile_yaml(
            """
queries:
  q:
    columns: [cat, val]
    values: [[A, 40], [B, 60]]
charts:
  c:
    query: q
    type: pie
    theta: val
    color: cat
    style:
      marks:
        slice:
          gap: 0.01
          corner_radius: 4
      inner_radius: 0.0
rows:
  - c
"""
        )
        assert result.success, result.errors

    def test_pie_with_style_total_compiles(self):
        result = _compile_yaml(
            """
queries:
  q:
    columns: [cat, val]
    values: [[A, 40], [B, 60]]
charts:
  c:
    query: q
    type: pie
    theta: val
    color: cat
    total:
      label: "Total"
      format: "$,.0f"
    style:
      total:
        value:
          font:
            size: 20
rows:
  - c
"""
        )
        assert result.success, result.errors


# ---------------------------------------------------------------------------
# 10. Default theme pie shape
# ---------------------------------------------------------------------------


class TestDefaultThemePieShape:
    """The default theme compiles to the new PieChartStyle / SliceStyle / TotalStyle shape."""

    def test_compiled_charts_has_pie_not_arc(self):
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.theme import PieChartStyle

        compiled = get_theme_style("clarity")
        assert isinstance(compiled.charts.pie, PieChartStyle)

    def test_global_marks_has_slice_sub_block(self):
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.theme import SliceMarkStyle

        compiled = get_theme_style("clarity")
        assert isinstance(compiled.charts.marks.slice, SliceMarkStyle)
        assert isinstance(compiled.charts.marks.slice.gap, float)

    def test_pie_has_total_sub_block(self):
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.theme import TotalStyle

        compiled = get_theme_style("clarity")
        assert isinstance(compiled.charts.pie.total, TotalStyle)
        assert compiled.charts.pie.total.value.font.size

    def test_resolved_style_has_pie_field(self):
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.theme import PieChartStyle
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        compiled = get_theme_style("clarity")
        ctx = resolve_chart_style_context(compiled)
        assert isinstance(ctx.pie, PieChartStyle)
