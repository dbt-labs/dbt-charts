"""WARN_POINT_MAP_NEGATIVE_SIZE_VALUES: the detector turns the engine's report of the
bubble_map rows dropped for a negative ``size:`` into the author-facing warning.

Mark AREA cannot be negative, so the engine drops those rows rather than drawing
them misleadingly small. The count (zero is not negative) comes from the engine;
``render/test_geo_engine_route.py`` covers it from a real board.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_POINT_MAP_NEGATIVE_SIZE_VALUES
from dbt_charts.core.render.chart.geometry_warnings import EngineParams
from dbt_charts.core.render.warnings import (
    WarningContext,
    point_map_negative_size_values as detector,
)

from ._board_utils import make_test_resolved_board


def _ctx(reported: list[EngineParams]) -> WarningContext:
    return WarningContext(
        board_spec=make_test_resolved_board(charts={}),
        chart_results={},
        geometry_warnings={
            "c1": {WARN_POINT_MAP_NEGATIVE_SIZE_VALUES.code: reported}
            if reported
            else {}
        },
    )


def test_fires_with_the_engines_counts_and_names_the_size_column() -> None:
    params: EngineParams = {
        "chart_id": "c1",
        "dropped_count": 2,
        "size_field": "profit",
        "total_count": 5,
    }

    (warning,) = detector.detect(_ctx([params]))

    assert warning.code == WARN_POINT_MAP_NEGATIVE_SIZE_VALUES.code
    assert warning.chart == "c1"
    assert warning.path == "charts.c1.size"
    assert warning.field == "profit"
    assert (
        warning.message
        == WARN_POINT_MAP_NEGATIVE_SIZE_VALUES.message_template.format(**params)
    )
    assert warning.fix == WARN_POINT_MAP_NEGATIVE_SIZE_VALUES.fix_template.format(
        size_field="profit"
    )


def test_silent_when_the_engine_reported_nothing() -> None:
    assert detector.detect(_ctx([])) == []
