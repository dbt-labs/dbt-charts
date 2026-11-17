"""Chart-root ``good`` lowers to the same sign rules, on the headline column."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.authored import KpiChart as AuthoredKpiChart
from dbt_charts.core.compile.models.chart.normalized import KpiChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
)


def _chart(good: str | None = "up", **extra: Any) -> KpiChart:
    return KpiChart(
        id="k",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="kpi",
        value="total",
        good=good,
        **extra,
    )


def test_headline_good_with_an_unset_glyph_names_the_glyph_slot() -> None:
    style = get_theme_style()
    value = style.charts.kpi.value
    glyph = value.glyph.model_copy(update={"up": None})
    style = style.model_copy(
        update={
            "charts": style.charts.model_copy(
                update={
                    "kpi": style.charts.kpi.model_copy(
                        update={"value": value.model_copy(update={"glyph": glyph})}
                    )
                }
            )
        }
    )
    with pytest.raises(ValueError, match=r"style\.value\.glyph\.up"):
        resolve(
            _chart(),
            [{"total": 1}],
            chart_style_context=resolve_chart_style_context(style),
        )


def test_good_and_a_chart_root_glyph_are_exclusive() -> None:
    with pytest.raises(ValidationError, match="good.*glyph|glyph.*good"):
        AuthoredKpiChart(
            id="k",
            query="select 1",
            type="kpi",
            value="total",
            good="up",
            glyph="▲",
        )


def test_a_rule_cannot_set_a_headline_glyph_or_tone() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AuthoredKpiChart(
            id="k",
            query="select 1",
            type="kpi",
            value="total",
            style={"conditional": {"total": [{"gt": 0, "glyph": "↑"}]}},
        )


def test_ordinary_headline_shapes_beside_good_are_accepted() -> None:
    AuthoredKpiChart(
        id="k",
        query="select 1",
        type="kpi",
        value="total",
        good="down",
        style={
            "value": {"format": ",.0f"},
            "conditional": {
                "total": [
                    {"gt": 0, "background": "#eee"},
                    {"default": True, "background": "#fff"},
                ]
            },
        },
    )


def test_good_is_not_a_chart_root_field_beside_a_value_without_it() -> None:
    with pytest.raises(ValidationError, match="good"):
        AuthoredKpiChart(
            id="k", query="select 1", type="kpi", value="total", good="sideways"
        )


def test_normalizer_carries_the_chart_root_good() -> None:
    from dbt_charts.core.compile.normalize.charts import normalize_chart

    chart = normalize_chart(
        "k",
        {
            "type": "kpi",
            "query": {"sql": "SELECT 1", "source": "test"},
            "value": "total",
            "good": "down",
        },
        {},
        sources={},
    )
    assert chart.good == "down"
