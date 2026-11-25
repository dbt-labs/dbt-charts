"""`grid.torn_baseline`: cascade bake, validation, rendered wave."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AxisYStylePatch,
    BaseAxisGridStylePatch,
    LineChartStylePatch,
)
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAxisGridTornBaselineStyle,
)
from dbt_charts.core.compile.models.style.theme import AxisGridTornBaselineStyle
from dbt_charts.core.compile.models.style.theme.style import Style
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

from ._engine_render import render_chart_svg

_CHART_ADAPTER: TypeAdapter[Chart] = TypeAdapter(Chart)


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _resolved_torn(
    theme: Style, slot: str, vl_type: str, chart_type: str = "line"
) -> ResolvedAxisGridTornBaselineStyle:
    return resolved_axis_style(
        resolve_chart_style_context(theme),
        slot,
        vl_type,
        chart_type=chart_type,
        labeled=False,
    ).grid.torn_baseline


@pytest.mark.parametrize("chart_type", ["line", "bar"])
def test_on_by_default_for_lines_and_floating_bars(chart_type: str) -> None:
    theme = get_theme_style()
    assert _resolved_torn(theme, "axis_y", "quantitative", chart_type).enabled is True


def test_scatter_is_off_by_default_on_both_axes() -> None:
    theme = get_theme_style()
    assert _resolved_torn(theme, "axis_y", "quantitative", "scatter").enabled is False
    assert _resolved_torn(theme, "axis_x", "quantitative", "scatter").enabled is False


def test_theme_values_bake_field_for_field() -> None:
    theme = get_theme_style()
    authored = theme.charts.axis.grid.torn_baseline
    for slot, vl_type in (("axis_y", "quantitative"), ("axis_x", "ordinal")):
        assert _resolved_torn(theme, slot, vl_type) == (
            ResolvedAxisGridTornBaselineStyle(
                enabled=authored.enabled,
                amplitude=authored.amplitude,
            )
        )


def test_an_axis_y_override_reaches_axis_y_only() -> None:
    base = get_theme_style()
    override = AxisGridTornBaselineStyle(enabled=True, amplitude=6.5)
    axis_y = base.charts.axis_y.model_copy(
        update={
            "grid": base.charts.axis_y.grid.model_copy(
                update={"torn_baseline": override}
            )
        }
    )
    theme = base.model_copy(
        update={"charts": base.charts.model_copy(update={"axis_y": axis_y})}
    )
    assert _resolved_torn(theme, "axis_y", "quantitative") == (
        ResolvedAxisGridTornBaselineStyle(enabled=True, amplitude=6.5)
    )
    assert _resolved_torn(theme, "axis_x", "ordinal") == _resolved_torn(
        base, "axis_x", "ordinal"
    )


def test_a_bite_of_zero_is_rejected() -> None:
    with pytest.raises(ValidationError, match="amplitude"):
        AxisGridTornBaselineStyle(amplitude=0.0)


@pytest.mark.parametrize("field", ["wavelength", "sharpness"])
def test_the_wave_settings_are_gone(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        AxisGridTornBaselineStyle.model_validate({field: 1.0})


def _line_svg(torn_enabled: bool) -> str:
    fields: dict[str, Any] = {
        "id": "price",
        "type": "line",
        "x": "month",
        "y": "price",
        "query": SqlQuery(sql="SELECT 1", source="src"),
        "query_name": "q",
        "style": LineChartStylePatch(
            axis_y=AxisYStylePatch(
                grid=BaseAxisGridStylePatch(
                    visible=True, torn_baseline={"enabled": torn_enabled}
                )
            )
        ),
    }
    data = [{"month": f"2026-{m:02d}-01", "price": 52.0 + m} for m in range(1, 7)]
    return render_chart_svg(_CHART_ADAPTER.validate_python(fields), data)


def test_enabled_torn_baseline_draws_the_wave() -> None:
    assert 'class="torn-baseline"' in _line_svg(True)
    assert 'class="torn-baseline"' not in _line_svg(False)


def _scatter_svg(style: dict[str, Any]) -> str:
    fields: dict[str, Any] = {
        "id": "dots",
        "type": "scatter",
        "x": "spend",
        "y": "price",
        "query": SqlQuery(sql="SELECT 1", source="src"),
        "query_name": "q",
        "style": {
            **style,
            "axis_y": {
                **style.get("axis_y", {}),
                "scale": {"continuous": {"zero": False}},
            },
        },
    }
    data = [{"spend": 10.0 + m, "price": 52.0 + 3 * m} for m in range(6)]
    return render_chart_svg(_CHART_ADAPTER.validate_python(fields), data)


def test_scatter_tears_only_when_its_chart_asks() -> None:
    assert 'class="torn-baseline"' not in _scatter_svg({})
    chart_wide = {"axis": {"grid": {"torn_baseline": {"enabled": True}}}}
    assert 'class="torn-baseline"' in _scatter_svg(chart_wide)
    one_axis = {"axis_y": {"grid": {"torn_baseline": {"enabled": True}}}}
    assert _scatter_svg(one_axis).count('class="torn-baseline"') == 1
