"""A `style.key.direct.overlay_x` key naming an undrawn series warns."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.normalized import LineChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.warnings import (
    WarningContext,
    direct_overlay_x_unseen as detector,
)

from ...core._board_utils import make_test_resolved_board

ROWS: list[dict[str, Any]] = [
    {"month": date(2024, m, 1), "region": region, "revenue": float(m)}
    for m in (1, 2, 3)
    for region in ("EMEA", "APAC")
]


def _detect(at: dict[str, Any]) -> list[Any]:
    chart = resolve(
        LineChart.model_validate(
            {
                "id": "rev",
                "type": "line",
                "x": "month",
                "y": "revenue",
                "color": "region",
                "query_name": "q",
                "style": {"key": {"type": "direct", "direct": {"overlay_x": at}}},
            }
        ),
        ROWS,
        chart_style_context=resolve_chart_style_context(
            get_theme_style(get_default_theme_name())
        ),
        rendered_at=datetime(2024, 6, 14, tzinfo=UTC),
    )
    return detector.detect(
        WarningContext(
            board_spec=make_test_resolved_board(charts={"rev": chart}),
            chart_results={"rev": ROWS},
            layout_charts={},
        )
    )


def test_an_unseen_series_warns_naming_the_drawn_ones() -> None:
    [warning] = _detect({"Oceania": "max()"})
    assert warning.chart == "rev"
    assert "`Oceania`" in warning.message
    assert "`APAC`" in warning.message and "`EMEA`" in warning.message


def test_a_drawn_series_is_silent() -> None:
    assert _detect({"EMEA": "max()"}) == []
