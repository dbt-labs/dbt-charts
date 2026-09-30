"""Tests for BoardRenderSession — board-level coordinator for render-v2."""

from __future__ import annotations

import datetime
import json
from typing import Any

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedBarChart,
    ResolvedBarStyle,
    ResolvedCalloutChart,
    ResolvedChart,
    ResolvedKpiChart,
)
from dbt_charts.core.compile.models.style.resolved import ResolvedKpiStyle
from dbt_charts.core.compile.models.style.resolved.callout import ResolvedCalloutStyle
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.feature import FeaturePipeline
from dbt_charts.core.render.chart.session import BoardRenderSession
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)


# Required base fields (no defaults on non-None resolved model fields).
def _default_legend():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.legend


def _default_callout_style() -> ResolvedCalloutStyle:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.callout


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
    "canvas": _DEFAULT_CHARTS.ink_canvas,
    "title_style": _DEFAULT_CHARTS.title,
    "layout_padding": _ZERO_PADDING,
}
_C: dict[str, Any] = dict(_B)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_style() -> Any:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style(get_default_theme_name()))


def _bar(bar_style: ResolvedBarStyle) -> ResolvedBarChart:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )

    from ...conftest import fixture_chart_for_type

    rcs = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    ax_merged, ay_merged, ax_band_position, ay_band_position, _ = _bake_cartesian_axes(
        rcs,
        fixture_chart_for_type("bar"),
        "bar",
        "nominal",
        "quantitative",
        AxisOverrides(),
    )
    ax = build_resolved_axis(
        ax_merged,
        band_position=ax_band_position,
        chart_id="test",
        format_raw=None,
    )
    ay = build_resolved_axis(
        ay_merged,
        band_position=ay_band_position,
        chart_id="test",
        format_raw=None,
    )
    return ResolvedBarChart(
        panel_axes=(),
        id="bar1",
        chart_type="bar",
        x="month",
        y="revenue",
        query_name="q",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_builds_session() -> None:
    board_style = _make_style()
    session = BoardRenderSession.create(board_style)

    assert isinstance(session, BoardRenderSession)
    assert session.board_style is board_style
    assert isinstance(session.features, FeaturePipeline)


def test_emit_chart_returns_chart_spec(bar_style: ResolvedBarStyle) -> None:
    session = BoardRenderSession.create(_make_style())
    spec = session.emit_chart(
        _bar(bar_style), _DEFAULT_BOX, {"q": [{"month": "Jan", "revenue": 100}]}
    )
    assert isinstance(spec, ChartSpec)


def test_finalize_vl_applies_presentation_config(bar_style: ResolvedBarStyle) -> None:
    """finalize_vl must embed effective vega config into the VL output."""
    board_style = _make_style()
    session = BoardRenderSession.create(board_style)
    spec = session.emit_chart(
        _bar(bar_style), _DEFAULT_BOX, {"q": [{"month": "Jan", "revenue": 100}]}
    )
    vl = session.finalize_vl(spec)
    assert isinstance(vl, dict)
    assert "$schema" in vl
    # board_style.vega_config must appear in the VL config block
    for key in board_style.vega_config:
        assert key in vl.get("config", {}), (
            f"expected '{key}' in vl['config'] after finalize_vl; "
            f"got keys: {sorted(vl.get('config', {}))}"
        )


def test_emit_chart_embeds_data_in_finalized_vl(bar_style: ResolvedBarStyle) -> None:
    """finalize_vl must embed row data as VL data.values."""
    data = [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 200}]
    session = BoardRenderSession.create(_make_style())
    spec = session.emit_chart(_bar(bar_style), _DEFAULT_BOX, {"q": data})
    vl = session.finalize_vl(spec)
    assert vl.get("data") == {"values": data}


def test_emit_chart_normalizes_date_types_in_data(bar_style: ResolvedBarStyle) -> None:
    """Data embedded in the VL spec must be JSON-serializable (date → str).

    vl_convert.vegalite_to_svg serializes the spec dict to JSON internally;
    Python date/datetime objects in the data values cause 'unsupported type date'
    TypeErrors. emit_chart must normalize before setting spec.data.
    """
    d = datetime.date(2024, 1, 1)
    data = [{"month": d, "revenue": 100}]
    session = BoardRenderSession.create(_make_style())
    spec = session.emit_chart(_bar(bar_style), _DEFAULT_BOX, {"q": data})
    vl = session.finalize_vl(spec)
    embedded = vl.get("data", {}).get("values", [])
    # Must be JSON-serializable — no TypeError
    json.dumps(embedded)
    # date must be stringified
    assert embedded[0]["month"] == str(d)


def test_emit_chart_applies_features(bar_style: ResolvedBarStyle) -> None:
    """A stub feature with a sentinel mutation must appear in the returned ChartSpec."""

    class _SentinelFeature:
        def applies_to(self, chart: ResolvedChart) -> bool:
            return True

        def apply(
            self,
            spec: ChartSpec,
            chart: ResolvedChart,
            box: RenderBox,
            datasets: dict[str | None, list[dict[str, Any]]],
        ) -> ChartSpec:
            spec.data_name = "feature-applied"
            return spec

    session = BoardRenderSession(
        board_style=_make_style(),
        features=FeaturePipeline([_SentinelFeature()]),
    )
    spec = session.emit_chart(_bar(bar_style), _DEFAULT_BOX, {"q": []})
    assert spec.data_name == "feature-applied"


def test_resolved_kpi_chart_has_no_title_or_subtitle() -> None:
    """ResolvedKpiChart must carry title/subtitle fields (always None) so the
    session can read chart.title/chart.subtitle directly with zero getattr.
    (kpi is a non-VL family; it does not flow through emit_chart.)"""
    kpi = ResolvedKpiChart(
        id="kpi1",
        chart_type="kpi",
        value="total",
        style=ResolvedKpiStyle(title=_make_style().chart_defaults.title),
        **{
            k: v
            for k, v in _B.items()
            if k not in ("background", "canvas", "title_style")
        },
    )
    assert kpi.title is None
    assert kpi.subtitle is None


def test_resolved_callout_chart_carries_title_and_subtitle() -> None:
    """ResolvedCalloutChart must carry title (real) and subtitle (always None)
    fields so session.py can read chart.title/chart.subtitle directly with zero
    getattr. (Callout has no v2 emitter yet, so it can't flow through
    session.emit_chart -- assert the field directly on the resolved model.)"""
    callout = ResolvedCalloutChart(
        id="c1",
        chart_type="callout",
        message="hi",
        title="Heads up",
        variable_dependencies=frozenset(),
        style=_default_callout_style(),
        layout_padding=_ZERO_PADDING,
    )
    assert callout.title == "Heads up"
    assert callout.subtitle is None
