"""WARN_POINT_MAP_OUT_OF_PROJECTION: the detector turns the engine's report of the
points a bounded projection (albersUsa) cannot place into the author-facing warning.

The count itself comes from the engine; ``render/test_geo_engine_route.py`` covers
it from a real board.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_POINT_MAP_OUT_OF_PROJECTION
from dbt_charts.core.render.chart.geometry_warnings import EngineParams
from dbt_charts.core.render.warnings import (
    WarningContext,
    point_map_out_of_projection as detector,
)

from ._board_utils import make_test_resolved_board


def _ctx(reported: list[EngineParams]) -> WarningContext:
    return WarningContext(
        board_spec=make_test_resolved_board(charts={}),
        chart_results={},
        geometry_warnings={
            "c1": {WARN_POINT_MAP_OUT_OF_PROJECTION.code: reported} if reported else {}
        },
    )


def test_fires_with_the_engines_counts() -> None:
    params: EngineParams = {
        "chart_id": "c1",
        "dropped_count": 2,
        "projection": "albersUsa",
        "total_count": 5,
    }

    (warning,) = detector.detect(_ctx([params]))

    assert warning.code == WARN_POINT_MAP_OUT_OF_PROJECTION.code
    assert warning.chart == "c1"
    assert warning.path == "charts.c1.projection"
    assert warning.message == WARN_POINT_MAP_OUT_OF_PROJECTION.message_template.format(
        **params
    )
    assert warning.fix == WARN_POINT_MAP_OUT_OF_PROJECTION.fix_template


def test_silent_when_the_engine_reported_nothing() -> None:
    assert detector.detect(_ctx([])) == []
