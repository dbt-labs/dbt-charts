"""An annotation the engine placed nowhere warns."""

from __future__ import annotations

from datetime import UTC, datetime

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.normalized import LineChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.warnings import (
    WarningContext,
    annotation_nothing_drawn as detector,
)

from ...core._board_utils import make_test_resolved_board


def _ctx(nothing_drawn: dict[str, dict[int, str]]) -> WarningContext:
    return WarningContext(
        board_spec=make_test_resolved_board(charts={}),
        chart_results={},
        layout_charts={},
        annotation_nothing_drawn=nothing_drawn,
    )


def test_an_annotation_the_engine_placed_nowhere_warns() -> None:
    [warning] = detector.detect(_ctx({"rev": {0: "it is off the plot"}}))
    assert warning.chart == "rev"
    assert "annotation 0" in warning.message
    assert "it is off the plot" in warning.message


def test_nothing_recorded_is_silent() -> None:
    assert detector.detect(_ctx({})) == []


def test_an_annotation_scope_with_no_rows_warns_from_the_resolved_chart() -> None:
    chart = resolve(
        LineChart.model_validate(
            {
                "id": "rev",
                "type": "line",
                "x": "month",
                "y": "revenue",
                "query_name": "q",
                "annotations": [{"type": "rule", "y": "max()"}],
            }
        ),
        [],
        chart_style_context=resolve_chart_style_context(
            get_theme_style(get_default_theme_name())
        ),
        rendered_at=datetime(2024, 6, 14, tzinfo=UTC),
    )
    ctx = _ctx({}).model_copy(
        update={"board_spec": make_test_resolved_board(charts={"rev": chart})}
    )
    [warning] = detector.detect(ctx)
    assert warning.chart == "rev" and "annotation 0" in warning.message
