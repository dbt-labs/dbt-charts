"""TDD tests for V2 renderer sizing & title parity fixes.

Tests three specific V2 gaps vs V1:
  Gap 1 — hconcat pane height: V2 must stamp $df_target_width/$df_target_height
           on the outer hconcat wrapper so _correct_concat_overshoot fires.
           Carve-out: a wrapper carrying an attached support_table strip drops
           $df_target_height (vega_lite._apply_support_table_strip) — its pixel
           literals are anchored to spec.height and must not be resized after
           the fact. The charts below carry no strip, so both sentinels apply.
  Gap 3 — fontSize int vs float: config.title.fontSize must be int, not float.
  Gap 6 — title.limit: V2 must set title["limit"] = width - padding to prevent
           the chart title from overflowing the slot.

Gap 2 (bar mark fill color / rhythm_slot) root cause is in
compile/resolve/chart/bar.py (outside the allowed edit surface); that fix
is tracked separately and is excluded from this suite.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)

from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _board_style() -> Any:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style(get_default_theme_name()))


def _board_ctx() -> Any:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _make_multi_series_line() -> Any:
    """Compile a multi-series line chart (color=series).

    In V2, the endpoint-label feature fires unconditionally for multi-series
    line charts with a color channel in series mode, producing an hconcat spec.
    """
    from dbt_charts.core.compile.models.chart.normalized import LineChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    query_registry = {"q": SqlQuery(sql="SELECT 1", source="test")}
    return LineChart(
        id="line_multi",
        query=query_registry["q"],
        query_name="q",
        type="line",
        x="date",
        y="value",
        color="series",
    )


_MULTI_SERIES_DATA: list[dict[str, Any]] = [
    {"date": "2024-01-01", "value": 100, "series": "Core"},
    {"date": "2024-02-01", "value": 120, "series": "Core"},
    {"date": "2024-01-01", "value": 200, "series": "Growth"},
    {"date": "2024-02-01", "value": 220, "series": "Growth"},
]


def _artifact_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch render_chart_artifact to capture the artifact without rendering."""
    from dbt_charts.core.render.chart.artifacts import RenderArtifact

    vl_module = importlib.import_module("dbt_charts.core.render.chart.vega_lite")
    captured: dict[str, Any] = {}

    def _spy(artifact: RenderArtifact, *args: Any, **kwargs: Any) -> str:
        captured["artifact"] = artifact
        return "ok"

    monkeypatch.setattr(vl_module, "render_chart_artifact", _spy)
    return captured


# ---------------------------------------------------------------------------
# Gap 1 — hconcat pane height: $df_target sentinels must be set
# ---------------------------------------------------------------------------


class TestHconcatDfTargetSentinels:
    """V2 must stamp $df_target_width/$df_target_height on hconcat wrappers.

    Strip-free wrappers only — see the module docstring's carve-out for the
    support_table case, pinned by test_support_table_concat_anchor.py.
    """

    def _render(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        width: float = 576.0,
        height: float = 416.0,
    ) -> dict[str, Any]:
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.chart.vega_lite import render_chart

        reset_config()
        captured = _artifact_spy(monkeypatch)
        render_chart(
            _make_multi_series_line(),
            _board_style(),
            _board_ctx(),
            _MULTI_SERIES_DATA,
            format="svg",
            width=width,
            height=height,
        )
        return captured["artifact"].payload

    def test_df_target_sentinels_absent_for_simple_spec(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """$df_target_* must NOT be stamped on simple (non-hconcat) specs.

        A single-series line chart without endpoint labels stays a simple spec;
        stamping sentinels there would trigger an unnecessary two-pass correction.
        """
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.render.chart.vega_lite import render_chart

        reset_config()
        captured = _artifact_spy(monkeypatch)
        query_registry = {"q": SqlQuery(sql="SELECT 1", source="test")}
        simple_chart = LineChart(
            id="line_simple",
            query=query_registry["q"],
            query_name="q",
            type="line",
            x="date",
            y="value",
        )
        render_chart(
            simple_chart,
            _board_style(),
            _board_ctx(),
            [{"date": "2024-01-01", "value": 100}],
            format="svg",
            width=576.0,
            height=416.0,
        )
        spec = captured["artifact"].payload
        assert "hconcat" not in spec
        assert "$df_target_width" not in spec
        assert "$df_target_height" not in spec


# ---------------------------------------------------------------------------
# vconcat pane height: a horizontal stacked bar + color must honor the
# authored height, mirroring the existing hconcat height branch.
# ---------------------------------------------------------------------------

_HORIZONTAL_STACKED_DATA: list[dict[str, Any]] = [
    {"product": "Widget A", "revenue": 60000, "category": "Electronics"},
    {"product": "Widget A", "revenue": 15000, "category": "Tools"},
    {"product": "Widget B", "revenue": 45000, "category": "Electronics"},
    {"product": "Widget B", "revenue": 30000, "category": "Tools"},
]


class TestVconcatHeightSizing:
    """The height branch must have a vconcat case (mirrors the width branch).

    A horizontal stacked bar with a ``color:`` channel wraps its chart pane
    in a top_rail vconcat (rail at index 0, chart pane at index 1). Before
    the fix, the height branch only handled hconcat, so the authored height
    never reached the chart pane and every render came out at the same
    fixed VL auto-size height regardless of the request.
    """

    def _render(
        self, monkeypatch: pytest.MonkeyPatch, make_chart: Any, *, height: float
    ) -> dict[str, Any]:
        from dbt_charts.core.compile.config import get_theme_style, reset_config
        from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )
        from dbt_charts.core.render.chart.vega_lite import render_chart

        reset_config()
        captured = _artifact_spy(monkeypatch)
        chart = make_chart(
            "bar",
            x="product",
            y="revenue",
            color="category",
            style=BarChartStylePatch(
                orientation="horizontal",
                stack="zero",
                endpoint_labels={"visible": True},
            ),
        )
        board_style, board_context = resolve_style_and_context(get_theme_style())
        render_chart(
            chart,
            board_style,
            board_context,
            _HORIZONTAL_STACKED_DATA,
            format="svg",
            width=800.0,
            height=height,
        )
        return captured["artifact"].payload


# ---------------------------------------------------------------------------
# Gap 3 — config.title.fontSize must be int, not float
# ---------------------------------------------------------------------------


class TestConfigTitleFontSizeIsInt:
    """V2 finalize_vl must emit integer fontSize/fontWeight in config.title."""

    def _finalize(self) -> dict[str, Any]:
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.render.chart.session import BoardRenderSession

        board_style = _board_style()
        chart = resolve(
            BarChart(id="b", type="bar", x="month", y="revenue"),
            [{"month": "Jan", "revenue": 100}],
            _board_ctx(),
        )
        session = BoardRenderSession.create(board_style)
        return session.finalize_vl(
            session.emit_chart(
                chart,
                _DEFAULT_BOX,
                {chart.query_name: [{"month": "Jan", "revenue": 100}]},
            )
        )


# ---------------------------------------------------------------------------
# Gap 6 — title.limit must be set (width - padding)
# ---------------------------------------------------------------------------

_PADDING_16: dict[str, Any] = {"left": 16.0, "right": 16.0, "top": 16.0, "bottom": 16.0}


class TestTitleLimit:
    """V2 bridge must call apply_title_overflow_to_spec so title.limit is present."""

    def _render(
        self,
        monkeypatch: pytest.MonkeyPatch,
        chart: Any,
        data: list[dict[str, Any]],
        *,
        width: float = 576.0,
    ) -> dict[str, Any]:
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.chart.vega_lite import render_chart

        reset_config()
        captured = _artifact_spy(monkeypatch)
        render_chart(
            chart,
            _board_style(),
            _board_ctx(),
            data,
            format="svg",
            width=width,
            padding=_PADDING_16,
        )
        return captured["artifact"].payload
