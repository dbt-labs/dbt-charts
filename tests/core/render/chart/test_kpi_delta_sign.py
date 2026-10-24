"""A KPI delta support value is colored by its sign; `tone` is the direction."""

from __future__ import annotations

import re
from typing import Any

import pytest

from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve

from ..._engine_render import render_kpi_svg
from .test_kpi_align import _styles


def _support_fill(delta: float, tone: str | None, fmt: str = "percent_delta") -> str:
    rows = [{"revenue": 128_000, "delta": delta}]
    style_support: dict[str, Any] = {"format": fmt}
    if tone is not None:
        style_support["tone"] = tone
    chart_def = {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Revenue",
        "support": {"value": "delta"},
        "style": {"support": style_support},
    }
    board_rs, board_ctx = _styles()
    flat = normalize_chart(
        "chart1", chart_def, {"q": ValuesQuery(rows=rows)}, sources={}
    )
    resolved = resolve(flat, rows, chart_style_context=board_ctx)
    svg = render_kpi_svg(resolved, rows, board_style=board_rs)
    # The headline prints first, the support value last.
    *_, (fill, _text) = re.findall(
        r'fill="(#[0-9A-Fa-f]{6})">([^<]*\d[^<]*)</tspan>', svg
    )
    return fill


@pytest.mark.parametrize(
    ("tone", "up", "down"),
    [
        (None, "positive", "negative"),
        ("positive", "positive", "negative"),
        ("negative", "negative", "positive"),
    ],
)
def test_a_delta_is_colored_by_sign_and_tone_is_the_direction(
    tone: str | None, up: str, down: str
) -> None:
    _, ctx = _styles()
    assert _support_fill(0.12, tone) == getattr(ctx.tones, up)
    assert _support_fill(-0.12, tone) == getattr(ctx.tones, down)


def test_a_plain_percent_support_keeps_its_tone_or_muted_ink() -> None:
    _, ctx = _styles()
    assert _support_fill(0.12, "positive", "percent") == ctx.tones.positive
    assert _support_fill(-0.12, "positive", "percent") == ctx.tones.positive
    assert _support_fill(0.12, None, "percent") == ctx.cascaded.secondary_ink()
