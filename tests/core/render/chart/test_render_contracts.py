"""Tests for the render-v2 contracts.

Covers:
- ChartSpec is VL-only (no non_vl_data field)
- Non-VL families (kpi, table) are not registered in get_emitter
- ChartSpec mutability
- ChartFeature / FeaturePipeline ordering and filtering
- ChartEmitter protocol structural satisfaction
- translate_to_vl() structural dispatch
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved.bar import (
    ResolvedBarChart,
    ResolvedBarStyle,
)
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.render.chart.emitter import ChartEmitter
from dbt_charts.core.render.chart.feature import FeaturePipeline
from dbt_charts.core.render.chart.features import DEFAULT_FEATURES
from dbt_charts.core.render.chart.features.bar_hover_band import BarHoverBandFeature
from dbt_charts.core.render.chart.features.baseline import BaselineFeature
from dbt_charts.core.render.chart.features.click_interactivity import (
    ClickInteractivityFeature,
)
from dbt_charts.core.render.chart.features.endpoint_labels import EndpointLabelFeature
from dbt_charts.core.render.chart.features.facet import FacetFeature
from dbt_charts.core.render.chart.features.mirror_axis import MirrorAxisFeature
from dbt_charts.core.render.chart.features.structured_tooltip import (
    StructuredTooltipFeature,
)
from dbt_charts.core.render.chart.features.value_labels import ValueLabelFeature
from dbt_charts.core.render.chart.features.zero_value_label import ZeroValueLabelFeature
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.translate import assemble_final_vl, translate_to_vl

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)


# Required base fields (no defaults on non-None resolved model fields).
def _default_legend():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.legend


def _default_title():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style(get_default_theme_name())).chart_defaults.title


def _default_resolved_table_style():
    from dbt_charts.core.compile.config import (
        get_default_theme_name,
        get_theme_style,
    )
    from dbt_charts.core.compile.models.style.resolved import ResolvedTableStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.resolve.style.typography import resolve_title_font

    charts = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    return ResolvedTableStyle(
        table=charts.table,
        title=charts.title,
        formats=charts.formats,
        title_font=resolve_title_font(charts, 600.0),
        pagination=charts.pagination,
    )


def _default_charts():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style(get_default_theme_name())).chart_defaults


_DEFAULT_CHARTS = _default_charts()
_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)
_B: dict[str, Any] = {
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {},
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "title_style": _DEFAULT_CHARTS.title,
    "layout_padding": _ZERO_PADDING,
}
_C: dict[str, Any] = dict(_B)

# ---------------------------------------------------------------------------
# Fake helpers (test-only; do NOT import in production code)
# ---------------------------------------------------------------------------


@dataclass
class _FakeFeature:
    """Records call order; can be toggled to not apply."""

    _applies: bool
    call_log: list[str]
    tag: str

    def applies_to(self, chart: ResolvedChart) -> bool:
        return self._applies

    def apply(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: dict[str | None, list[dict[str, Any]]],
    ) -> ChartSpec:
        self.call_log.append(self.tag)
        return spec


class _FakeBarEmitter:
    """Minimal ChartEmitter for ResolvedBarChart."""

    def emit(
        self,
        chart: ResolvedBarChart,
        box: RenderBox,
        data: list[dict[str, Any]],
    ) -> ChartSpec:
        return ChartSpec(
            mark="bar",
            encoding={"x": chart.x, "y": chart.y},
        )


# ---------------------------------------------------------------------------
# ChartSpec is VL-only — no non_vl_data field
# ---------------------------------------------------------------------------


def test_chartspec_rejects_non_vl_data_kwarg() -> None:
    """ChartSpec must not carry non_vl_data; kpi/table route before emit."""
    import pytest

    with pytest.raises(TypeError, match="non_vl_data"):
        ChartSpec(mark="bar", encoding={}, layers=[], non_vl_data={"x": 1})  # type: ignore[call-arg]


def test_get_emitter_raises_for_kpi() -> None:
    """kpi is a non-VL family; get_emitter must not have a kpi arm."""
    import pytest

    from dbt_charts.core.compile.models.chart.resolved import (
        ResolvedKpiChart,
        ResolvedKpiStyle,
    )
    from dbt_charts.core.render.chart.emitters import get_emitter
    from dbt_charts.core.render.errors import RenderError

    kpi = ResolvedKpiChart(
        id="k1",
        chart_type="kpi",
        value="total",
        style=ResolvedKpiStyle(title=_default_title()),
        **{k: v for k, v in _B.items() if k not in ("background", "title_style")},
    )
    with pytest.raises(RenderError, match="ResolvedKpiChart"):
        get_emitter(kpi)


def test_get_emitter_raises_for_table() -> None:
    """table is a non-VL family; get_emitter must not have a table arm."""
    import pytest

    from dbt_charts.core.compile.models.chart.resolved import ResolvedTableChart
    from dbt_charts.core.render.chart.emitters import get_emitter
    from dbt_charts.core.render.errors import RenderError

    table = ResolvedTableChart(
        id="t1",
        chart_type="table",
        style=_default_resolved_table_style(),
        variable_dependencies=frozenset(),
        palette=(),
        resolved_channels={},
        legend=_default_legend(),
        background=_DEFAULT_CHARTS.background,
        title_style=_DEFAULT_CHARTS.title,
        layout_padding=_ZERO_PADDING,
    )
    with pytest.raises(RenderError, match="ResolvedTableChart"):
        get_emitter(table)


# ---------------------------------------------------------------------------
# ChartSpec
# ---------------------------------------------------------------------------


def test_chart_spec_fields_mutable() -> None:
    spec = ChartSpec(mark="bar", encoding={}, layers=[], config={})
    spec.encoding["x"] = "date"
    assert spec.encoding["x"] == "date"
    spec.layers.append(ChartSpec(mark="rule", encoding={}, layers=[], config={}))
    assert len(spec.layers) == 1


# ---------------------------------------------------------------------------
# FeaturePipeline — ordering
# ---------------------------------------------------------------------------


def test_feature_pipeline_applies_in_list_order(
    bar_style: ResolvedBarStyle,
) -> None:
    """FeaturePipeline applies features in the order given — no re-sort."""
    call_log: list[str] = []
    f_a = _FakeFeature(_applies=True, call_log=call_log, tag="a")
    f_b = _FakeFeature(_applies=True, call_log=call_log, tag="b")
    f_c = _FakeFeature(_applies=True, call_log=call_log, tag="c")

    chart = _make_bar_chart(bar_style)

    forward_log: list[str] = []
    FeaturePipeline(
        [
            _FakeFeature(_applies=True, call_log=forward_log, tag="a"),
            _FakeFeature(_applies=True, call_log=forward_log, tag="b"),
            _FakeFeature(_applies=True, call_log=forward_log, tag="c"),
        ]
    ).apply(
        ChartSpec(mark="bar", encoding={}, layers=[], config={}),
        chart,
        _DEFAULT_BOX,
        {},
    )
    assert forward_log == ["a", "b", "c"]

    call_log.clear()
    FeaturePipeline([f_c, f_a, f_b]).apply(
        ChartSpec(mark="bar", encoding={}, layers=[], config={}),
        chart,
        _DEFAULT_BOX,
        {},
    )
    assert call_log == ["c", "a", "b"]


def test_default_features_pins_concrete_production_order() -> None:
    """DEFAULT_FEATURES is the sole ordering authority — pin its concrete order.

    FeaturePipeline no longer sorts, so a mistaken reorder of DEFAULT_FEATURES
    would otherwise only surface as a visual golden diff. This makes it a
    fast, direct test failure instead.
    """
    assert [type(f) for f in DEFAULT_FEATURES] == [
        ZeroValueLabelFeature,
        BarHoverBandFeature,
        BaselineFeature,
        EndpointLabelFeature,
        ValueLabelFeature,
        ClickInteractivityFeature,
        StructuredTooltipFeature,
        MirrorAxisFeature,
        FacetFeature,
    ]


def test_feature_pipeline_skips_non_applicable(bar_style: ResolvedBarStyle) -> None:
    call_log: list[str] = []
    f_skip = _FakeFeature(_applies=False, call_log=call_log, tag="skip")
    f_apply = _FakeFeature(_applies=True, call_log=call_log, tag="apply")

    pipeline = FeaturePipeline([f_skip, f_apply])

    chart = _make_bar_chart(bar_style)
    spec = ChartSpec(mark="bar", encoding={}, layers=[], config={})
    pipeline.apply(spec, chart, _DEFAULT_BOX, {})

    assert call_log == ["apply"]


def test_feature_pipeline_empty_returns_spec_unchanged(
    bar_style: ResolvedBarStyle,
) -> None:
    pipeline = FeaturePipeline([])
    chart = _make_bar_chart(bar_style)
    spec = ChartSpec(mark="bar", encoding={"x": "month"}, layers=[], config={})
    result = pipeline.apply(spec, chart, _DEFAULT_BOX, {})
    assert result is spec
    assert result.encoding["x"] == "month"


# ---------------------------------------------------------------------------
# Protocol structural typing
# ---------------------------------------------------------------------------


def test_emitter_protocol_satisfied_structurally() -> None:
    emitter = _FakeBarEmitter()
    # Runtime-checkable Protocol: isinstance check should pass
    assert isinstance(emitter, ChartEmitter)


# ---------------------------------------------------------------------------
# assemble_final_vl / translate_to_vl
# ---------------------------------------------------------------------------


def test_translate_bar_is_implemented() -> None:
    """translate_to_vl (structural helper) dispatches for known marks."""
    spec = ChartSpec(mark="bar", encoding={}, layers=[], config={})
    result = translate_to_vl(spec)
    assert isinstance(result, dict)
    assert "$schema" in result


def test_assemble_final_vl_is_implemented() -> None:
    """assemble_final_vl (public API) returns a config-merged VL dict."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    board_style = resolve_style(get_theme_style())
    spec = ChartSpec(
        mark="bar",
        encoding={},
        layers=[],
        config={},
        background=board_style.background,
        title_style=board_style.chart_defaults.title,
    )
    result = assemble_final_vl(spec, board_style)
    assert isinstance(result, dict)
    assert "$schema" in result
    assert "config" in result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_bar_chart(bar_style: ResolvedBarStyle) -> ResolvedBarChart:
    return ResolvedBarChart(
        panel_axes=(),
        id="c1",
        chart_type="bar",
        query=None,
        x="month",
        y="revenue",
        style=bar_style,
        **_C,
    )
