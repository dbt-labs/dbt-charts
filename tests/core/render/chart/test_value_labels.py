"""TDD tests: style.marks.<mark>.labels value-label layer injection."""

from __future__ import annotations

import re
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.style.theme import FontStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import render_chart

from ..._engine_render import render_chart_svg
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


class TestBarValueLabels:
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
            render_chart_svg(
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
            render_chart_svg(
                chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
            )

    def _text_layers(self, spec):
        return [
            lyr
            for lyr in chart_pane(spec).get("layer", [])
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "text"
        ]


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


def _get_text_layer(spec):
    """Return the first text mark layer dict, or None."""
    for lyr in spec.get("layer", []):
        m = lyr.get("mark", {})
        if isinstance(m, dict) and m.get("type") == "text":
            return lyr
    return None


class TestFormatTracksEffectiveAxis:
    """Value labels follow the EFFECTIVE measure-axis format, including per-chart
    overrides — not just the board-time axis_quantitative.format.

    Regression: a per-chart axis format (style.axis_y.labels.format) lands on the axis only, so labels stayed on the stale
    board value — clean ~s axis beside raw-float / mismatched labels.
    """

    def _label_fmt(self, make_chart, chart_kwargs):
        # editorial: axis_quantitative.format references the number alias (.3~s)
        board_rs, board_ctx = _board_with_bar_labels({})
        chart = make_chart("bar", x="month", y="revenue", **chart_kwargs)
        resolved = resolve(chart, SAMPLE_DATA, chart_style_context=board_ctx)
        spec = render_chart_svg(
            chart, SAMPLE_DATA, board_style=board_rs, chart_style_context=board_ctx
        )
        lyr = _get_text_layer(spec)
        assert lyr is not None
        return (
            lyr["encoding"]["text"].get("format"),
            resolved.style.axis_y.labels.format.spec,
        )


# =============================================================================
# TestBarTotalLabel — stack total label feature (TDD: written before impl)
# =============================================================================


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
    # (?<!/) skips a self-closing <text/> — a blanked label.
    pattern = r"<text\b[^>]*(?<!/)>(.*?)</text>"
    return [html.unescape(m) for m in re.findall(pattern, svg, re.S)]


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
        from ..._engine_render import render_kpi_svg

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


# =============================================================================
# Overlay label render-level proof: is_house=False (native) must propagate
# from the resolved layer model to the emitted VL encoding.
# =============================================================================


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


_DUMMY_QUERY_REG: dict[str, Any] = {
    "q": __import__(
        "dbt_charts.core.compile.models.query.normalized",
        fromlist=["SqlQuery"],
    ).SqlQuery(sql="SELECT 1", source="test")
}


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


# =============================================================================
# Authored categorical-domain sort: numeric coercion and aggregate choice.
# =============================================================================


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


# =============================================================================
# Line/point label ternary (line_label_is_house vs point_label_is_house)
# pinned at the render level.
# =============================================================================


# =============================================================================
# Bar position bottom/middle/middle_aligned with is_house=True must splice the
# house-register calculate transform alongside the position transform.
# =============================================================================


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


# =============================================================================
# datetime.date x-values must not break an overlay's authored sort.
# rendered_x_domain normalizes raw category keys so they match the ISO-string
# keys in the emitted domain array.
# =============================================================================


# =============================================================================
# A stacked bar overlay must use sum for the sort aggregate.
# The shared categorical-domain helper uses sum, matching Vega-Lite's own
# EncodingSortField.op default for the bar shapes this codebase emits.
# =============================================================================


# =============================================================================
# Null measures: no value, no label
# =============================================================================


# ---------------------------------------------------------------------------
# labels.field pointing at a TEXT column
# ---------------------------------------------------------------------------


# --- Stacked-segment midpoint labels + fit test -----------------------------


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


class TestValueLabelFitTest:
    """A label that cannot fit its segment is blanked, not drawn over its neighbors."""

    @staticmethod
    def _hide_tests(layer):
        condition = layer["encoding"]["text"].get("condition")
        return [] if condition is None else [condition["test"]]


class TestStackGeometryFidelity:
    """The label layer must line up with the stack the BAR layer emitted.

    Every case here reconstructs geometry the emitter already decided. Getting
    it independently right for `stack: zero` and wrong for everything else is
    the failure mode these pin.
    """

    @staticmethod
    def _stack_transform(pane):
        return next(t for t in pane["transform"] if "stack" in t)


class TestFitTestGeometryAssumptions:
    """The fit test converts a value to pixels. Both halves of that are gated."""

    @staticmethod
    def _condition(spec):
        return _text_layers(spec)[0]["encoding"]["text"].get("condition")

    def _spec(self, make_chart, rows, position, chart_style, height=300.0):
        board_rs, board_ctx = _board_with_bar_labels({"position": position})
        chart = make_chart("bar", x="month", y="revenue", style=chart_style)
        resolve(chart, rows, chart_style_context=board_ctx)
        return render_chart_svg(
            chart,
            rows,
            board_style=board_rs,
            chart_style_context=board_ctx,
            height=height,
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
        spec = render_chart_svg(
            chart,
            rows,
            board_style=board_rs,
            chart_style_context=board_ctx,
            height=height,
        )
        return _layered_pane(spec)


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


def _svg_texts(svg: str) -> list[str]:
    import html

    # (?<!/) skips a self-closing <text/> — a blanked label.
    pattern = r"<text\b[^>]*(?<!/)>(.*?)</text>"
    return [html.unescape(m) for m in re.findall(pattern, svg, re.S)]


class TestFitTestMeasuresThePlotRect:
    """A segment is measured against the rectangle it is drawn in.

    The slot also holds the title, subtitle, legend, axis labels and padding.
    Dividing by the slot makes the threshold too small — and the threshold
    grows as the height shrinks, so every comparison against the slot
    under-counts what needs hiding. That direction is the bug.
    """

    @pytest.mark.parametrize(("stacked", "roomy"), [(False, "700"), (True, "300")])
    def test_segment_with_real_room_keeps_its_label(self, make_chart, stacked, roomy):
        """The stricter test must not start eating labels that fit."""
        texts = _svg_texts(_fit_svg(make_chart, stacked=stacked))
        assert roomy in texts, (
            f"a {roomy}-unit segment has room several times over and must stay "
            f"labeled, got {texts}"
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


# ---------------------------------------------------------------------------
# labels.field with a NULL value
# ---------------------------------------------------------------------------
