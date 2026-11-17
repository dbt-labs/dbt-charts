"""``support.good`` lowers to sign rules on the support column."""

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


def _chart(
    support: dict[str, Any] | None = None,
    style: dict[str, Any] | None = None,
    **extra: Any,
) -> KpiChart:
    return KpiChart(
        id="k",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="kpi",
        value="total",
        support={
            "value": "change",
            "label": "vs prior",
            "good": "up",
            **(support or {}),
        },
        style={"support": {"format": "+.1f"}, **(style or {})},
        **extra,
    )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"style": {"support": {"tone": "positive"}}}, "good.*tone|tone.*good"),
        ({"support": {"glyph": "▲"}}, "good.*glyph|glyph.*good"),
        ({"support": {"value": None, "label": "x"}}, "good.*value|value.*good"),
    ],
)
def test_invalid_authored_shapes_are_rejected(
    kwargs: dict[str, Any], match: str
) -> None:
    support = {"value": "change", "good": "up", **kwargs.pop("support", {})}
    with pytest.raises(ValidationError, match=match):
        AuthoredKpiChart(
            id="k",
            query="select 1",
            type="kpi",
            value="total",
            support=support,
            **kwargs,
        )


def test_ordinary_shapes_beside_good_are_accepted() -> None:
    AuthoredKpiChart(
        id="k",
        query="select 1",
        type="kpi",
        value="change",
        support={"value": "change", "good": "up"},
        style={
            "conditional": {"change": [{"default": True, "background": "#eee"}]},
        },
    )


def test_good_with_an_unset_direction_glyph_names_the_glyph_slot() -> None:
    chart = _chart(support={"value": "change", "good": "up"})
    style = get_theme_style()
    support = style.charts.kpi.support
    glyph = support.glyph.model_copy(update={"up": None})
    style = style.model_copy(
        update={
            "charts": style.charts.model_copy(
                update={
                    "kpi": style.charts.kpi.model_copy(
                        update={"support": support.model_copy(update={"glyph": glyph})}
                    )
                }
            )
        }
    )
    with pytest.raises(ValueError, match=r"style\.support\.glyph\.up"):
        resolve(
            chart,
            [{"total": 1, "change": 1}],
            chart_style_context=resolve_chart_style_context(style),
        )
