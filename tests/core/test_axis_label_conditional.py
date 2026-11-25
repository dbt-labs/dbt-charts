"""A ``style.conditional`` rule with an ``axis_x`` style restyles x-axis labels. A rule on the
x column tests each label's own value; a rule on another column tests the one row the label
names."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    HeatmapChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError

from ._engine_render import render_chart_svg

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

RED = "#c62828"
GREEN = "#2e7d32"

_ROWS: list[dict[str, Any]] = [
    {"month": f"2024-{m:02d}-01", "revenue": 10 + m, "team": "a"} for m in range(1, 7)
]


def _chart(conditional: dict[str, Any], **channels: str | list[str]) -> LineChart:
    return LineChart.model_validate(
        {
            "id": "c",
            "type": "line",
            "x": "month",
            "y": "revenue",
            **channels,
            "style": {"conditional": conditional},
        }
    )


def _svg(chart: Any, rows: list[dict[str, Any]]) -> str:
    return render_chart_svg(
        chart,
        rows,
        width=600,
        height=360,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )


def _months(count: int) -> list[dict[str, Any]]:
    return [
        {
            "month": f"{2023 + m // 12}-{m % 12 + 1:02d}-01",
            "revenue": (-1) ** m * (m + 1),
            "team": "a",
        }
        for m in range(count)
    ]


_SIGNED: dict[str, Any] = {
    "revenue": [
        {"lt": 0, "axis_x": {"labels": {"font": {"color": RED}}}},
        {"gte": 0, "axis_x": {"labels": {"font": {"color": GREEN}}}},
    ]
}


def _red_label(column: str = "month") -> dict[str, Any]:
    return {
        column: [
            {
                "between": [dt.date(2024, 3, 1), dt.date(2024, 4, 30)],
                "axis_x": {"labels": {"font": {"color": RED}}},
            }
        ]
    }


def _x_labels(svg: str, axis_name: str = "X-axis") -> list[tuple[str, str]]:
    axis = re.search(
        rf'aria-roledescription="axis"[^>]*aria-label="{axis_name}.*?</g></g>',
        svg,
        re.S,
    )
    assert axis is not None
    return re.findall(r'<text[^>]*?fill="([^"]+)"[^>]*>([^<]+)</text>', axis.group(0))


def test_an_x_column_rule_colors_only_the_matching_tick_labels() -> None:
    red = [
        text
        for fill, text in _x_labels(_svg(_chart(_red_label()), _ROWS))
        if fill == RED
    ]
    assert red == ["Mar", "Apr"]


def test_a_measure_rule_colors_each_label_by_its_rows_value() -> None:
    labels = _x_labels(_svg(_chart(_SIGNED), _months(6)))
    assert labels == [
        (GREEN, "Jan"),
        (GREEN, "2023"),
        (RED, "Feb"),
        (GREEN, "Mar"),
        (RED, "Apr"),
        (GREEN, "May"),
        (RED, "Jun"),
    ]


def test_labels_coarser_than_the_rows_are_refused_for_a_row_rule() -> None:
    with pytest.raises(ChartDataError, match="names several rows"):
        _svg(_chart(_SIGNED), _months(24))


def test_a_label_over_several_series_is_refused_for_a_row_rule() -> None:
    rows = [
        {**row, "team": team, "revenue": sign * row["revenue"]}
        for row in _months(6)
        for team, sign in (("a", 1), ("b", -1))
    ]
    with pytest.raises(ChartDataError, match="names several rows"):
        _svg(_chart(_SIGNED, color="team"), rows)


def test_a_coarser_label_ladder_tests_each_labels_own_period_start() -> None:
    months = [
        {
            "month": f"{2023 + m // 12}-{m % 12 + 1:02d}-01",
            "revenue": 10 + m,
            "team": "a",
        }
        for m in range(24)
    ]
    labels = _x_labels(_svg(_chart(_red_label()), months))
    assert [text for _, text in labels].count("Q2") == 2
    assert [text for fill, text in labels if fill == RED] == ["Q2"]


_CATEGORIES: list[dict[str, Any]] = [
    {"region": region, "revenue": revenue}
    for region, revenue in (("East", 5), ("North", -3), ("South", 2), ("West", -1))
]
_SIGNED_BY_REGION = {
    "revenue": [
        {"lt": 0, "axis_x": {"labels": {"font": {"color": RED}}}},
        {"gte": 0, "axis_x": {"labels": {"font": {"color": GREEN}}}},
    ]
}
_REGION_FILLS = [(GREEN, "East"), (RED, "North"), (GREEN, "South"), (RED, "West")]


def _style(conditional: dict[str, Any], **style: Any) -> dict[str, Any]:
    return {**style, "conditional": conditional}


def test_an_area_chart_colors_its_labels_like_a_line() -> None:
    chart = AreaChart.model_validate(
        {
            "id": "c",
            "type": "area",
            "x": "month",
            "y": "revenue",
            "style": _style(_SIGNED),
        }
    )
    fills = [
        fill for fill, text in _x_labels(_svg(chart, _months(6))) if text != "2023"
    ]
    assert fills == [GREEN, RED, GREEN, RED, GREEN, RED]


@pytest.mark.parametrize(
    ("orientation", "axis_name"), [("vertical", "X-axis"), ("horizontal", "Y-axis")]
)
def test_a_bar_chart_colors_each_category_label_by_its_row(
    orientation: str, axis_name: str
) -> None:
    chart = BarChart.model_validate(
        {
            "id": "c",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "style": _style(_SIGNED_BY_REGION, orientation=orientation),
        }
    )
    labels = _x_labels(_svg(chart, _CATEGORIES), axis_name)
    assert sorted(labels, key=lambda label: label[1]) == _REGION_FILLS


def test_a_scatter_date_axis_colors_labels_by_their_own_date() -> None:
    chart = ScatterChart.model_validate(
        {
            "id": "c",
            "type": "scatter",
            "x": "month",
            "y": "revenue",
            "style": _style(_red_label()),
        }
    )
    red = [text for fill, text in _x_labels(_svg(chart, _ROWS)) if fill == RED]
    assert red == ["Mar", "Apr"]


def test_a_scatter_date_axis_refuses_a_row_rule() -> None:
    chart = ScatterChart.model_validate(
        {
            "id": "c",
            "type": "scatter",
            "x": "month",
            "y": "revenue",
            "style": _style(_SIGNED),
        }
    )
    with pytest.raises(ChartDataError, match="name instants, not rows"):
        _svg(chart, _months(6))


def _heatmap(conditional: dict[str, Any]) -> HeatmapChart:
    return HeatmapChart.model_validate(
        {
            "id": "c",
            "type": "heatmap",
            "x": "region",
            "y": "team",
            "color": "revenue",
            "style": _style(conditional),
        }
    )


_GRID = [
    {**row, "team": team, "revenue": sign * row["revenue"]}
    for row in _CATEGORIES
    for team, sign in (("a", 1), ("b", -1))
]


def test_a_heatmap_colors_category_labels_by_their_own_value() -> None:
    rule = {
        "region": [
            {"in": ["North", "West"], "axis_x": {"labels": {"font": {"color": RED}}}}
        ]
    }
    red = [text for fill, text in _x_labels(_svg(_heatmap(rule), _GRID)) if fill == RED]
    assert red == ["North", "West"]


def test_a_heatmap_refuses_a_row_rule_since_each_column_holds_several_rows() -> None:
    with pytest.raises(ChartDataError, match="names several rows"):
        _svg(_heatmap(_SIGNED_BY_REGION), _GRID)


_RED_X = [{"gte": 2, "axis_x": {"labels": {"font": {"color": RED}}}}]
_NUMERIC = [{"n": n, "revenue": n * 2} for n in (1.5, 2.5, 3.5, 4.5)]


@pytest.mark.parametrize("kind", ["line", "scatter"])
def test_a_numeric_x_axis_refuses_label_rules(kind: str) -> None:
    cls = LineChart if kind == "line" else ScatterChart
    chart = cls.model_validate(
        {
            "id": "c",
            "type": kind,
            "x": "n",
            "y": "revenue",
            "style": _style({"n": _RED_X}),
        }
    )
    with pytest.raises(ChartDataError, match="draws its labels per tick"):
        _svg(chart, _NUMERIC)


def test_a_heatmap_with_a_date_x_refuses_label_rules() -> None:
    rows = [{**row, "team": team} for row in _months(4) for team in ("a", "b")]
    chart = HeatmapChart.model_validate(
        {
            "id": "c",
            "type": "heatmap",
            "x": "month",
            "y": "team",
            "color": "revenue",
            "style": _style(_red_label()),
        }
    )
    with pytest.raises(ChartDataError, match="date x axis draws its labels per tick"):
        _svg(chart, rows)


def test_two_measures_may_not_both_color_the_shared_labels() -> None:
    rows = [{**row, "cost": 1} for row in _months(4)]
    chart = _chart(
        {
            "revenue": [{"lt": 0, "axis_x": {"labels": {"font": {"color": RED}}}}],
            "cost": [{"gte": 0, "axis_x": {"labels": {"font": {"color": GREEN}}}}],
        },
        y=["revenue", "cost"],
    )
    with pytest.raises(ValueError, match="both patch axis_x.labels.font.color"):
        resolve(chart, rows, chart_style_context=_BOARD_CTX)


def test_a_rule_on_a_column_all_series_share_colors_the_label() -> None:
    rows = [
        {**row, "team": team, "flag": row["revenue"] < 0}
        for row in _months(4)
        for team in ("a", "b")
    ]
    rule = {"flag": [{"eq": True, "axis_x": {"labels": {"font": {"color": RED}}}}]}
    labels = _x_labels(_svg(_chart(rule, color="team"), rows))
    assert [text for fill, text in labels if fill == RED] == ["Feb", "Apr"]


def test_a_year_column_label_compares_as_a_date_like_its_marks() -> None:
    rows = [{"year": year, "revenue": year - 2019} for year in (2020, 2021, 2022, 2023)]
    chart = BarChart.model_validate(
        {
            "id": "c",
            "type": "bar",
            "x": "year",
            "y": "revenue",
            "style": _style(
                {
                    "year": [
                        {
                            "gte": dt.date(2022, 1, 1),
                            "axis_x": {"labels": {"font": {"color": RED}}},
                        }
                    ]
                },
                orientation="vertical",
            ),
        }
    )
    red = [text for fill, text in _x_labels(_svg(chart, rows)) if fill == RED]
    assert red == ["2022", "2023"]


def test_monthly_bars_color_each_month_label_by_its_row() -> None:
    chart = BarChart.model_validate(
        {
            "id": "c",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "style": _style(_SIGNED),
        }
    )
    fills = [
        fill for fill, text in _x_labels(_svg(chart, _months(6))) if text != "2023"
    ]
    assert fills == [GREEN, RED, GREEN, RED, GREEN, RED]


def test_a_bar_interval_label_tests_its_own_period_start() -> None:
    weeks = [
        {
            "week": (dt.date(2024, 1, 1) + dt.timedelta(weeks=i)).isoformat(),
            "revenue": 1,
        }
        for i in range(52)
    ]
    rule = {
        "week": [
            {
                "between": [dt.date(2024, 2, 1), dt.date(2024, 2, 29)],
                "axis_x": {"labels": {"font": {"color": RED}}},
            }
        ]
    }
    chart = BarChart.model_validate(
        {"id": "c", "type": "bar", "x": "week", "y": "revenue", "style": _style(rule)}
    )
    red = [text for fill, text in _x_labels(_svg(chart, weeks)) if fill == RED]
    assert red == ["Feb"]


def _weeks(count: int) -> list[dict[str, Any]]:
    return [
        {
            "week": (dt.date(2024, 1, 1) + dt.timedelta(weeks=i)).isoformat(),
            "revenue": i % 3 - 1,
        }
        for i in range(count)
    ]


def test_an_edge_label_tests_the_datas_first_date() -> None:
    days = [
        {"day": (dt.date(2024, 1, 3) + dt.timedelta(days=i)).isoformat(), "revenue": 1}
        for i in range(85)
    ]
    rule = {
        "day": [{"eq": "2024-01-03", "axis_x": {"labels": {"font": {"color": RED}}}}]
    }
    chart = LineChart.model_validate(
        {"id": "c", "type": "line", "x": "day", "y": "revenue", "style": _style(rule)}
    )
    assert [text for fill, text in _x_labels(_svg(chart, days)) if fill == RED] == [
        "3",
        "Jan'24",
    ]


def test_a_label_between_the_datas_buckets_refuses_a_row_rule() -> None:
    chart = LineChart.model_validate(
        {
            "id": "c",
            "type": "line",
            "x": "week",
            "y": "revenue",
            "style": _style(_SIGNED, axis_x={"labels": {"time_unit": "yearweek"}}),
        }
    )
    with pytest.raises(ChartDataError, match="sits between two of the data's buckets"):
        _svg(chart, _weeks(6))


def test_a_numeric_x_bar_refuses_label_rules() -> None:
    chart = BarChart.model_validate(
        {
            "id": "c",
            "type": "bar",
            "x": "n",
            "y": "revenue",
            "style": _style({"n": _RED_X}, orientation="vertical"),
        }
    )
    with pytest.raises(
        ChartDataError, match="numeric x axis draws its labels per tick"
    ):
        _svg(chart, _NUMERIC)


def _time_scale_bar(conditional: dict[str, Any]) -> BarChart:
    return BarChart.model_validate(
        {
            "id": "c",
            "type": "bar",
            "x": "week",
            "y": "revenue",
            "style": _style(conditional, axis_x={"type": "temporal"}),
        }
    )


def test_a_time_scale_bar_colors_labels_by_their_own_date() -> None:
    rule = {
        "week": [
            {"gte": dt.date(2024, 2, 1), "axis_x": {"labels": {"font": {"color": RED}}}}
        ]
    }
    labels = _x_labels(_svg(_time_scale_bar(rule), _weeks(6)))
    assert [text for fill, text in labels if fill == RED][:2] == ["5", "Feb"]
    assert all(fill != RED for fill, text in labels if text in {"8", "15", "22", "29"})


def test_a_time_scale_bar_refuses_a_row_rule() -> None:
    with pytest.raises(ChartDataError, match="name instants, not rows"):
        _svg(_time_scale_bar(_SIGNED), _weeks(6))


def test_a_faceted_heatmap_refuses_label_rules() -> None:
    rows = [{**row, "panel": panel} for row in _GRID for panel in ("p1", "p2")]
    rule = {"region": [{"eq": "East", "axis_x": {"labels": {"font": {"color": RED}}}}]}
    chart = HeatmapChart.model_validate(
        {
            "id": "c",
            "type": "heatmap",
            "x": "region",
            "y": "team",
            "color": "revenue",
            "multiples": {"columns": "panel"},
            "style": _style(rule),
        }
    )
    with pytest.raises(ChartDataError, match="faceted heatmap's shared x axis"):
        _svg(chart, rows)


def test_a_point_scale_scatter_date_axis_refuses_label_rules() -> None:
    chart = ScatterChart.model_validate(
        {
            "id": "c",
            "type": "scatter",
            "x": "month",
            "y": "revenue",
            "style": _style(_red_label(), axis_x={"type": "ordinal"}),
        }
    )
    with pytest.raises(ChartDataError, match="point-scale time axis"):
        _svg(chart, _ROWS)
