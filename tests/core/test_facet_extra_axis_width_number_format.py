"""``facet_extra_axis_width_px`` (emitters/_cartesian.py) must measure the
number format Vega actually paints on a numeric per-panel category axis, not
the raw row value — same VL "format" property gate_label_format already
proved renders (a band-scale axis with numeric-string ticks).
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import HeatmapChart, ScatterChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.emitters._cartesian import facet_extra_axis_width_px

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style("clarity"))


def _sparse_region_data(products: list[str]) -> list[dict[str, Any]]:
    regions = {"Japan": products[:2], "US": products}
    return [
        {"week": w, "product": p, "region": r, "value": 10}
        for r, prods in regions.items()
        for p in prods
        for w in ("2024-W01", "2024-W02")
    ]


def _resolved(
    number_format: str | None,
    case: str | None = None,
    products: list[str] | None = None,
):
    if products is None:
        products = [str((i + 1) * 1_000_000) for i in range(5)]
    data = _sparse_region_data(products)
    labels: dict[str, Any] = {}
    if number_format is not None:
        labels["format"] = number_format
    if case is not None:
        labels["font"] = {"case": case}
    style: dict[str, Any] = {"axis_y": {"labels": labels}} if labels else {}
    chart = HeatmapChart.model_validate(
        {
            "id": "t",
            "type": "heatmap",
            "query_name": "q",
            "x": "week",
            "y": "product",
            "color": "value",
            "multiples": {"columns": "region"},
            **({"style": style} if style else {}),
        }
    )
    return resolve(chart, data, chart_style_context=_BOARD_CTX, width=1200.0), data


def test_authored_number_format_widens_the_extra_axis_budget() -> None:
    bare, bare_data = _resolved(None)
    formatted, formatted_data = _resolved("$,.0f")
    assert bare.multiples is not None
    assert formatted.multiples is not None

    bare_width = facet_extra_axis_width_px(bare, bare.multiples, bare_data)
    formatted_width = facet_extra_axis_width_px(
        formatted, formatted.multiples, formatted_data
    )

    assert formatted_width > bare_width


def test_heatmap_case_does_not_change_the_extra_axis_budget() -> None:
    # A heatmap's y axis never gets the case expr, so it paints uncased text.
    products = ["acme", "globex", "initech", "umbrella", "hooli"]
    bare, bare_data = _resolved(None, products=products)
    upper, upper_data = _resolved(None, case="upper", products=products)
    assert bare.multiples is not None
    assert upper.multiples is not None
    assert facet_extra_axis_width_px(
        upper, upper.multiples, upper_data
    ) == facet_extra_axis_width_px(bare, bare.multiples, bare_data)


def _scatter(case: str | None):
    products = ["acme", "globex", "initech", "umbrella", "hooli"]
    data = [
        {"sales": float(i), "product": p, "region": r}
        for r, prods in {"Japan": products[:2], "US": products}.items()
        for i, p in enumerate(prods)
    ]
    style: dict[str, Any] = (
        {"axis_y": {"labels": {"font": {"case": case}}}} if case is not None else {}
    )
    chart = ScatterChart.model_validate(
        {
            "id": "t",
            "type": "scatter",
            "query_name": "q",
            "x": "sales",
            "y": "product",
            "multiples": {"columns": "region"},
            **({"style": style} if style else {}),
        }
    )
    return resolve(chart, data, chart_style_context=_BOARD_CTX, width=1200.0), data


def test_scatter_upper_case_widens_the_extra_axis_budget() -> None:
    # Scatter's nominal y gets the case expr, so it paints the cased text.
    bare, bare_data = _scatter(None)
    upper, upper_data = _scatter("upper")
    assert bare.multiples is not None
    assert upper.multiples is not None
    assert facet_extra_axis_width_px(
        upper, upper.multiples, upper_data
    ) > facet_extra_axis_width_px(bare, bare.multiples, bare_data)
