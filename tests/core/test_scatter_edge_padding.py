"""A scatter keeps its end dots off the plot edge by padding the x range in pixels.

The pad is ``scale.padding`` (Vega's ``padDomain``: pixels of the plot, not a
share of the domain), so the first and last tick still name the real data
extents while the end dots sit fully inside the plot.
"""

import re
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.normalized import Chart

from ._engine_render import render_chart_svg

_Y = [3, 5, 4, 8, 6, 9]
# The pad is the dot's radius plus this visible gap, at every dot size.
_GAP = 2.0


def _svg(xs: list[Any], style: dict[str, Any] | None = None) -> str:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "scatter",
            "x": "x",
            "y": "y",
            **({"style": style} if style else {}),
        }
    )
    return render_chart_svg(
        chart, [{"x": x, "y": y} for x, y in zip(xs, _Y, strict=True)]
    )


def _dot_xs(svg: str) -> list[float]:
    return [
        float(m)
        for m in re.findall(
            r'<path transform="translate\(([\d.]+),[\d.]+\)"[^>]*data-dct-role="point"',
            svg,
        )
    ]


def _radius(svg: str) -> float:
    return float(re.search(r'<path transform="[^"]*" d="M([\d.]+),0A', svg).group(1))


def _plot_width(svg: str) -> float:
    ends = re.findall(
        r'<line x1="0.5" y1="([\d.]+)" x2="([\d.]+)" y2="\1" stroke="#D0D3D9"', svg
    )
    return float(ends[0][1]) - 0.5


_YEARS = [2000 + i for i in range(6)]
_DATES = [f"2024-0{i + 1}-01" for i in range(6)]
_NUMS = [10.0 + i for i in range(6)]
# An unrounded domain is the one that would otherwise end exactly on the data.
_UNNICED = {"axis_x": {"scale": {"nice": False, "continuous": {"zero": False}}}}


@pytest.mark.parametrize(
    ("xs", "style"),
    [(_YEARS, None), (_DATES, None), (_NUMS, _UNNICED)],
    ids=["year", "date", "number"],
)
def test_end_dots_sit_inside_the_plot_edge(
    xs: list[Any], style: dict[str, Any] | None
) -> None:
    svg = _svg(xs, style)
    dots = _dot_xs(svg)
    assert len(dots) == len(xs)
    radius = _radius(svg)
    assert min(dots) - radius == pytest.approx(_GAP, abs=0.05)
    assert _plot_width(svg) - max(dots) - radius == pytest.approx(_GAP, abs=0.05)


@pytest.mark.parametrize(
    ("xs", "style"),
    [(_YEARS, None), (_DATES, None), (_NUMS, _UNNICED)],
    ids=["year", "date", "number"],
)
def test_a_fat_dot_keeps_the_same_gap(
    xs: list[Any], style: dict[str, Any] | None
) -> None:
    fat = {**(style or {}), "marks": {"point": {"size": 784}}}
    svg = _svg(xs, fat)
    dots = _dot_xs(svg)
    radius = _radius(svg)
    assert radius == 14.0
    assert min(dots) - radius == pytest.approx(_GAP, abs=0.05)
    assert _plot_width(svg) - max(dots) - radius == pytest.approx(_GAP, abs=0.05)


def test_padding_does_not_widen_the_labeled_span() -> None:
    svg = _svg(_YEARS)
    labels = re.findall(r">(\d{4})</text>", svg)
    assert labels[0] == "2000"
    assert labels[-1] == "2005"


def test_authored_padding_sets_the_pixel_inset() -> None:
    svg = _svg(_YEARS, {"axis_x": {"scale": {"padding": 20}}})
    assert min(_dot_xs(svg)) == 20.0


def test_authored_domain_stays_verbatim() -> None:
    svg = _svg(
        _DATES,
        {"axis_x": {"scale": {"continuous": {"domain": ["2024-01-01", "2024-06-01"]}}}},
    )
    dots = _dot_xs(svg)
    assert min(dots) == 0.0
    assert _plot_width(svg) == max(dots)
