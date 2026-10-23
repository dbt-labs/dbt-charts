"""Tests for BoardRenderSession — board-level coordinator for render-v2."""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedCalloutChart,
    ResolvedKpiChart,
)
from dbt_charts.core.compile.models.style.resolved import ResolvedKpiStyle
from dbt_charts.core.compile.models.style.resolved.callout import ResolvedCalloutStyle
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.render.chart.feature import FeaturePipeline
from dbt_charts.core.render.chart.session import BoardRenderSession


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
    ).chart_defaults.callout_error


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_builds_session() -> None:
    board_style = _make_style()
    session = BoardRenderSession.create(board_style)

    assert isinstance(session, BoardRenderSession)
    assert session.board_style is board_style
    assert isinstance(session.features, FeaturePipeline)


def test_resolved_kpi_chart_has_no_title_or_subtitle() -> None:
    """ResolvedKpiChart must carry title/subtitle fields (always None) so the
    session can read chart.title/chart.subtitle directly with zero getattr.
    (kpi is a non-VL family; it does not flow through emit_chart.)"""
    kpi = ResolvedKpiChart(
        id="kpi1",
        chart_type="kpi",
        value="total",
        variant="stacked",
        style=ResolvedKpiStyle(),
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
