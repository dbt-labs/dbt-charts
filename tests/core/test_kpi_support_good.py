"""``support.good`` lowers to sign rules on the support column."""

from __future__ import annotations

import re
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
    resolve_style,
)

from ._engine_render import render_kpi_svg

_TONES = resolve_style(get_theme_style()).tones
_POSITIVE = _TONES.positive
_NEGATIVE = _TONES.negative
_WARNING = _TONES.warning


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


def _render(chart: KpiChart, change: Any) -> str:
    data = [{"total": 100, "change": change}]
    ctx = resolve_chart_style_context(get_theme_style())
    resolved = resolve(chart, data, chart_style_context=ctx)
    return render_kpi_svg(
        resolved,
        data,
        width=400,
        height=200,
        board_style=resolve_style(get_theme_style()),
    )


def _tspan_fills(svg: str) -> dict[str, str]:
    return {
        text.strip(): fill
        for fill, text in re.findall(
            r'<tspan[^>]*fill="([^"]+)"[^>]*>([^<]*)</tspan>', svg
        )
    }


@pytest.mark.parametrize(
    ("good", "change", "glyph", "text", "tone_color"),
    [
        ("up", 12, "▲", "+12.0", _POSITIVE),
        ("up", -3, "▼", "−3.0", _NEGATIVE),
        ("down", 12, "▲", "+12.0", _NEGATIVE),
        ("down", -3, "▼", "−3.0", _POSITIVE),
    ],
)
def test_good_follows_the_sign(
    good: str, change: float, glyph: str, text: str, tone_color: str
) -> None:
    fills = _tspan_fills(_render(_chart({"good": good}), change))
    assert fills[glyph] == tone_color
    assert fills[text] == tone_color


@pytest.mark.parametrize("good", ["up", "down"])
@pytest.mark.parametrize("change", [0, None])
def test_zero_and_null_get_no_glyph_and_neutral_fill(good: str, change: Any) -> None:
    svg = _render(_chart({"good": good}), change)
    assert "▲" not in svg and "▼" not in svg
    assert _POSITIVE not in svg and _NEGATIVE not in svg


def test_neutral_glyph_draws_on_zero() -> None:
    chart = _chart(style={"support": {"format": "+.1f", "glyph": {"neutral": "●"}}})
    assert "●" in _render(chart, 0)
    assert "●" not in _render(chart, 5)


def test_glyph_set_replaces_the_theme_pair_key_by_key() -> None:
    chart = _chart(style={"support": {"format": "+.1f", "glyph": {"up": "↑"}}})
    assert _tspan_fills(_render(chart, 5))["↑"] == _POSITIVE
    assert _tspan_fills(_render(chart, -5))["▼"] == _NEGATIVE


def test_authored_rule_wins_where_it_matches_and_derived_rules_cover_the_rest() -> None:
    chart = _chart(
        conditional_formatting={
            "change": {"when": [{"between": [-1, 1], "glyph": "●", "tone": "warning"}]}
        }
    )
    flat = _tspan_fills(_render(chart, 0.5))
    assert flat["●"] == _WARNING
    assert flat["+0.5"] == _WARNING
    assert _tspan_fills(_render(chart, 5))["▲"] == _POSITIVE


def test_authored_rule_leaving_tone_unset_keeps_the_derived_tone() -> None:
    chart = _chart(
        conditional_formatting={"change": {"when": [{"gt": 0, "glyph": "↑"}]}}
    )
    assert _tspan_fills(_render(chart, 5))["↑"] == _POSITIVE


def test_tone_rule_colors_the_value_as_well_as_the_glyph() -> None:
    chart = _chart(support={"good": None})
    chart = KpiChart(
        **{
            **chart.model_dump(exclude_none=True),
            "conditional_formatting": {
                "change": {"when": [{"gt": 0, "glyph": "▲", "tone": "warning"}]}
            },
        }
    )
    fills = _tspan_fills(_render(chart, 5))
    assert fills["▲"] == _WARNING
    assert fills["+5.0"] == _WARNING


def test_non_numeric_support_cell_raises_naming_chart_and_column() -> None:
    with pytest.raises(ValueError, match=r"'k'.*`change`"):
        _render(_chart(), "n/a")


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"style": {"support": {"tone": "positive"}}}, "good.*tone|tone.*good"),
        ({"support": {"glyph": "▲"}}, "good.*glyph|glyph.*good"),
        ({"support": {"value": None, "label": "x"}}, "good.*value|value.*good"),
        (
            {
                "conditional_formatting": {
                    "change": {"when": [{"default": True, "glyph": "●"}]}
                }
            },
            "default",
        ),
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


@pytest.mark.parametrize(
    ("value", "kwargs"),
    [
        ("total", {"style": {"value": {"format": ",.0f"}}}),
        (
            "change",
            {
                "conditional_formatting": {
                    "change": {"when": [{"default": True, "background": "#eee"}]}
                }
            },
        ),
    ],
    ids=["style_without_support", "headline_default_rule_on_shared_column"],
)
def test_ordinary_shapes_beside_good_are_accepted(
    value: str, kwargs: dict[str, Any]
) -> None:
    AuthoredKpiChart(
        id="k",
        query="select 1",
        type="kpi",
        value=value,
        support={"value": "change", "good": "up"},
        **kwargs,
    )


def test_tone_rule_on_a_non_support_column_raises_without_good() -> None:
    chart = KpiChart(
        id="k",
        query=SqlQuery(sql="SELECT 1", source="test"),
        query_name="q",
        type="kpi",
        value="total",
        support={"value": "change"},
        conditional_formatting={
            "total": {"when": [{"gt": 0, "glyph": "▲", "tone": "positive"}]}
        },
    )
    with pytest.raises(ValueError, match="support"):
        resolve(
            chart,
            [{"total": 1, "change": 1}],
            chart_style_context=resolve_chart_style_context(get_theme_style()),
        )


def test_literal_glyph_without_good_renders_as_before() -> None:
    chart = _chart(support={"good": None, "glyph": "▲"})
    chart = KpiChart(**{**chart.model_dump(exclude_none=True)})
    svg = _render(chart, -3)
    assert "▲" in svg and "▼" not in svg


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
