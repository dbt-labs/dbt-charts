"""Chart-root ``good`` lowers to the same sign rules, on the headline column."""

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
from .test_kpi_support_good import _tspan_fills

_TONES = resolve_style(get_theme_style()).tones
_POSITIVE = _TONES.positive
_NEGATIVE = _TONES.negative


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


def _render(chart: KpiChart, total: Any) -> str:
    data = [{"total": total}]
    resolved = resolve(
        chart, data, chart_style_context=resolve_chart_style_context(get_theme_style())
    )
    return render_kpi_svg(
        resolved,
        data,
        width=400,
        height=200,
        board_style=resolve_style(get_theme_style()),
    )


@pytest.mark.parametrize(
    ("good", "total", "glyph", "tone_color"),
    [
        ("up", 12, "▲", _POSITIVE),
        ("up", -3, "▼", _NEGATIVE),
        ("down", 12, "▲", _NEGATIVE),
        ("down", -3, "▼", _POSITIVE),
    ],
)
def test_headline_good_follows_the_sign(
    good: str, total: float, glyph: str, tone_color: str
) -> None:
    fills = _tspan_fills(_render(_chart(good), total))
    assert fills[glyph] == tone_color
    assert fills[f"{total:,}".replace("-", "−")] == tone_color


@pytest.mark.parametrize("good", ["up", "down"])
@pytest.mark.parametrize("total", [0, None])
def test_zero_and_null_headline_get_no_glyph_and_neutral_fill(
    good: str, total: Any
) -> None:
    svg = _render(_chart(good), total)
    assert "▲" not in svg and "▼" not in svg
    assert _POSITIVE not in svg and _NEGATIVE not in svg


def test_headline_glyph_set_is_styled_beside_the_headline_glyph_font() -> None:
    chart = _chart(style={"value": {"glyph": {"up": "↑", "neutral": "●"}}})
    assert _tspan_fills(_render(chart, 5))["↑"] == _POSITIVE
    assert _tspan_fills(_render(chart, -5))["▼"] == _NEGATIVE
    assert "●" in _render(chart, 0)


def test_headline_and_support_good_are_independent() -> None:
    chart = _chart(
        "down",
        support={"value": "change", "good": "up"},
        style={"support": {"format": "+.1f"}},
    )
    data = [{"total": 5, "change": 5}]
    resolved = resolve(
        chart, data, chart_style_context=resolve_chart_style_context(get_theme_style())
    )
    svg = render_kpi_svg(
        resolved,
        data,
        width=400,
        height=200,
        board_style=resolve_style(get_theme_style()),
    )
    fills = _tspan_fills(svg)
    assert fills["5"] == _NEGATIVE
    assert fills["+5.0"] == _POSITIVE


def test_authored_headline_color_rule_wins_over_the_derived_tone() -> None:
    chart = _chart(
        conditional_formatting={
            "total": {"when": [{"gt": 0, "font": {"color": "#123456"}}]}
        }
    )
    fills = _tspan_fills(_render(chart, 5))
    assert fills["5"] == "#123456"


def test_non_numeric_headline_raises_naming_chart_and_column() -> None:
    with pytest.raises(ValueError, match=r"'k'.*`total`"):
        _render(_chart(), "n/a")


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


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"glyph": "▲"}, "good.*glyph|glyph.*good"),
        (
            {
                "conditional_formatting": {
                    "total": {"when": [{"default": True, "background": "#eee"}]}
                }
            },
            "default",
        ),
    ],
)
def test_invalid_headline_good_shapes_are_rejected(
    kwargs: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        AuthoredKpiChart(
            id="k", query="select 1", type="kpi", value="total", good="up", **kwargs
        )


def test_an_authored_glyph_or_tone_rule_on_the_headline_stays_rejected() -> None:
    chart = _chart(
        None,
        conditional_formatting={
            "total": {"when": [{"gt": 0, "glyph": "↑", "tone": "positive"}]}
        },
    )
    with pytest.raises(ValueError, match="chart-root `good:`"):
        _render(chart, 1)


def test_ordinary_headline_shapes_beside_good_are_accepted() -> None:
    AuthoredKpiChart(
        id="k",
        query="select 1",
        type="kpi",
        value="total",
        good="down",
        conditional_formatting={"total": {"when": [{"gt": 0, "background": "#eee"}]}},
        style={"value": {"format": ",.0f"}},
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


def test_good_arrow_is_sized_between_the_label_and_the_numerals_authored_glyph_is_not() -> (
    None
):
    kpi = resolve_chart_style_context(get_theme_style()).kpi
    assert kpi.support.font.size < kpi.value.glyph.arrow.font.size < kpi.value.font.size
    assert kpi.value.glyph.font.size == kpi.support.font.size
    arrow = _render(_chart("up"), 5)
    authored = _render(_chart(None, glyph="▲"), 5)

    def size_of(svg: str) -> re.Match[str] | None:
        return re.search(r'font-size="([\d.]+)"[^>]*>▲', svg)

    assert float(size_of(arrow).group(1)) == kpi.value.glyph.arrow.font.size
    assert float(size_of(authored).group(1)) == kpi.value.glyph.font.size
