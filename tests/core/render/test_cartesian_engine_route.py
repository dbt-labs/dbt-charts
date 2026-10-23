"""The six cartesian families render in the engine and never reach Vega-Lite."""

from __future__ import annotations

from typing import Any

import pytest
import vl_convert

from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.render.chart import session
from dbt_charts.core.render.chart.emitters import get_emitter
from dbt_charts.core.render.errors import RenderError

from .._engine_render import render_chart_svg

_ROWS = [
    {
        "x": f"c{i}",
        "y": i + 1,
        "z": i % 2,
        "w": i * i + 3,
        "series": "a" if i % 2 else "b",
    }
    for i in range(6)
]


def _chart(chart_type: str, **fields: Any) -> Chart:
    from pydantic import TypeAdapter

    return TypeAdapter(Chart).validate_python(
        {"id": "c", "type": chart_type, "query_name": "q", **fields}
    )


_CHARTS = {
    "bar": {"x": "x", "y": "y"},
    "histogram": {"x": "w"},
    "line": {"x": "x", "y": "y"},
    "area": {"x": "x", "y": "y"},
    "scatter": {"x": "z", "y": "y"},
    "heatmap": {"x": "x", "y": "series", "color": "y"},
}


@pytest.mark.parametrize("chart_type", sorted(_CHARTS))
def test_a_cartesian_chart_renders_in_the_engine_without_vega_lite(
    chart_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the Vega-Lite path ran")

    monkeypatch.setattr(vl_convert, "vegalite_to_svg", refuse)
    monkeypatch.setattr(vl_convert, "vegalite_to_scenegraph", refuse)
    monkeypatch.setattr(session, "get_emitter", refuse)

    svg = render_chart_svg(_chart(chart_type, **_CHARTS[chart_type]), _ROWS, width=600)

    assert svg.startswith("<svg")


@pytest.mark.parametrize("chart_type", sorted(_CHARTS))
def test_a_cartesian_chart_has_no_emitter(chart_type: str) -> None:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_style_and_context,
    )

    _, context = resolve_style_and_context(get_theme_style())
    resolved = resolve(
        _chart(chart_type, **_CHARTS[chart_type]),
        _ROWS,
        chart_style_context=context,
        width=600,
    )
    with pytest.raises(RenderError, match="No emitter registered"):
        get_emitter(resolved)


@pytest.mark.parametrize(
    ("time_unit", "dates", "label"),
    [
        ("monthofyear", [f"2024-{m:02d}-01" for m in range(1, 13)], "Jan"),
        ("dayofweek", [f"2024-01-{d:02d}" for d in range(1, 15)], "Mon"),
        ("hourofday", [f"2024-01-01T{h:02d}:00:00" for h in range(24)], "12:00"),
    ],
)
def test_a_bar_time_part_axis_renders_the_part_labels(
    time_unit: str, dates: list[str], label: str
) -> None:
    rows = [{"d": d, "y": i + 1} for i, d in enumerate(dates)]
    svg = render_chart_svg(
        _chart("bar", x="d", y="y", style={"axis_x": {"time_unit": time_unit}}),
        rows,
        width=600,
    )
    assert f">{label}</text>" in svg


def test_an_ordered_support_table_strip_past_forty_ticks_renders() -> None:
    rows = [
        {"x": f"2024-{i // 28 + 1:02d}-{i % 28 + 1:02d}", "y": i} for i in range(50)
    ]
    svg = render_chart_svg(
        _chart(
            "bar",
            x="x",
            y="y",
            support_table={"entries": [{"source": "y", "aggregate": "sum"}]},
            style={"support_table": {"position": "bottom"}},
        ),
        rows,
        width=900,
    )
    assert "<svg" in svg


def test_a_line_mirror_expr_speaks_the_ghost_axis() -> None:
    svg = render_chart_svg(
        _chart(
            "line",
            x="x",
            y="y",
            style={"axis_y": {"mirror": {"expr": "datum.value + '%'"}}},
        ),
        _ROWS,
        width=600,
    )
    assert "%</text>" in svg
