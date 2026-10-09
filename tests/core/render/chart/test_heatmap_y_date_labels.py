"""A date-like heatmap y gets the same calendar vocabulary and cadence x does."""

from __future__ import annotations

import datetime as dt
from typing import Any

import vl_convert as vlc

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context


def _months(count: int) -> list[str]:
    return [dt.date(2024 + i // 12, i % 12 + 1, 1).isoformat() for i in range(count)]


def _render(
    make_chart: Any,
    cohorts: list[str],
    height: float,
    theme: str | None = None,
    **chart_kwargs: Any,
) -> dict[str, Any]:
    from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

    style, ctx = resolve_style_and_context(get_theme_style(theme))
    data = [
        {"cohort": cohort, "age": age, "value": i + age}
        for i, cohort in enumerate(cohorts)
        for age in range(3)
    ]
    chart = make_chart("heatmap", x="age", y="cohort", color="value", **chart_kwargs)
    artifact = render_resolved_chart(
        resolve(chart, data, chart_style_context=ctx),
        data,
        style,
        width=600,
        height=height,
    )
    assert artifact.kind == "vega_spec"
    assert artifact.payload["encoding"]["y"]["type"] == "nominal"
    return artifact.payload


def _y_axis(make_chart: Any, count: int, height: float, **kw: Any) -> dict[str, Any]:
    return _render(make_chart, _months(count), height, **kw)["encoding"]["y"]["axis"]


def _painted_y_labels(spec: dict[str, Any]) -> list[Any]:
    """Text Vega paints on the y axis: the axis-label mark whose text isn't numeric."""
    scenegraph = vlc.vegalite_to_scenegraph(dict(spec))
    marks: list[list[Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("marktype") == "text" and node.get("role") == "axis-label":
                marks.append([item.get("text") for item in node.get("items", [])])
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(scenegraph)
    (y_marks,) = [m for m in marks if any(not str(t).isdigit() for t in m)]
    return y_marks


def test_date_y_gets_label_expr_and_thinned_values(make_chart: Any) -> None:
    axis = _y_axis(make_chart, 24, 340)
    assert "labelExpr" in axis
    assert 0 < len(axis["values"]) < 24


def test_painted_labels_are_calendar_not_iso(make_chart: Any) -> None:
    painted = _painted_y_labels(_render(make_chart, _months(24), 340))
    flat = [t for t in painted if t]
    assert flat
    assert not any("2024-" in str(t) for t in flat)
    assert ["Jan", "2024"] in flat


def test_cadence_follows_height(make_chart: Any) -> None:
    short = _y_axis(make_chart, 24, 200)
    tall = _y_axis(make_chart, 24, 900)
    assert len(short["values"]) < len(tall["values"])


def test_query_row_order_is_preserved(make_chart: Any) -> None:
    desc = _months(12)[::-1]
    payload = _render(make_chart, desc, 340)
    assert payload["encoding"]["y"]["sort"] is None
    seen = list(dict.fromkeys(r["cohort"] for r in payload["data"]["values"]))
    assert seen == desc


def test_dated_y_takes_away_side_gutter_not_iso_measured(make_chart: Any) -> None:
    axis = _y_axis(make_chart, 24, 340, theme="clarity")
    assert axis.get("labelPadding", 0) < 40


def test_coarsest_cadence_still_colliding_falls_back_to_greedy(
    make_chart: Any,
) -> None:
    assert _y_axis(make_chart, 120, 120)["labelOverlap"] == "greedy"


def test_authored_expr_wins(make_chart: Any) -> None:
    axis = _y_axis(
        make_chart,
        24,
        340,
        style={
            "axis_y": {
                "labels": {"expr": "utcmonth(toDate(datum.value)) % 3 === 0 ? 'Q' : ''"}
            }
        },
    )
    assert axis["labelExpr"].endswith("? 'Q' : ''")


def test_non_date_y_is_untouched(make_chart: Any) -> None:
    axis = _render(make_chart, [f"s{i}" for i in range(24)], 340)["encoding"]["y"].get(
        "axis", {}
    )
    assert "values" not in axis
    assert "labelExpr" not in axis
