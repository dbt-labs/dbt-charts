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
    theme: Style, slot: str, vl_type: str
) -> ResolvedAxisGridTornBaselineStyle:
    return resolved_axis_style(
        resolve_chart_style_context(theme),
        slot,
        vl_type,
        chart_type="line",
        labeled=False,
    ).grid.torn_baseline


def test_off_by_default_on_both_axes() -> None:
    theme = get_theme_style()
    assert _resolved_torn(theme, "axis_y", "quantitative").enabled is False
    assert _resolved_torn(theme, "axis_x", "ordinal").enabled is False


def test_theme_values_bake_field_for_field() -> None:
    theme = get_theme_style()
    authored = theme.charts.axis.grid.torn_baseline
    for slot, vl_type in (("axis_y", "quantitative"), ("axis_x", "ordinal")):
        assert _resolved_torn(theme, slot, vl_type) == (
            ResolvedAxisGridTornBaselineStyle(
                enabled=authored.enabled,
                amplitude=authored.amplitude,
                wavelength=authored.wavelength,
                sharpness=authored.sharpness,
            )
        )


def test_an_axis_y_override_reaches_axis_y_only() -> None:
    base = get_theme_style()
    override = AxisGridTornBaselineStyle(
        enabled=True, amplitude=6.5, wavelength=21.0, sharpness=0.25
    )
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
        ResolvedAxisGridTornBaselineStyle(
            enabled=True, amplitude=6.5, wavelength=21.0, sharpness=0.25
        )
    )
    assert _resolved_torn(theme, "axis_x", "ordinal").enabled is False


@pytest.mark.parametrize(
    ("field", "value"),
    [("amplitude", 0.0), ("wavelength", 0.5), ("sharpness", 1.5)],
)
def test_out_of_range_wave_settings_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError, match=field):
        AxisGridTornBaselineStyle(**{field: value})


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
