"""The endpoint-label rail detector must map each degradation cause to the
warning that honestly names it.

`gap_did_not_fit` is a height problem (more height helps); `no_slope` is a data
problem (every series ends on one value — height cannot change it). One overloaded
code blamed height for both, telling authors to grow a chart that was already tall
enough. The detector picks the code off `EndpointLabelGapOverflow.cause`, and that
branch is what this pins: swapping the two would ship a wrong advisory silently.
"""

from __future__ import annotations

from typing import Literal

from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.diagnostics import (
    WARN_ENDPOINT_LABEL_GAP_OVERFLOW,
    WARN_ENDPOINT_LABEL_RAIL_OVERFLOW,
    WARN_ENDPOINT_LABEL_RAIL_TIED,
)
from dbt_charts.core.render.chart.endpoint_label_overflow import (
    EndpointLabelGapOverflow,
)
from dbt_charts.core.render.warnings import (
    WarningContext,
    endpoint_label_gap_overflow as detector,
)

from ...core._board_utils import (
    make_test_resolved_board,
    make_test_resolved_chart,
)


def _ctx(
    cause: Literal["gap_did_not_fit", "no_slope", "rail_overflow"],
    dropped_series: tuple[str, ...] = (),
) -> WarningContext:
    chart = TypeAdapter(Chart).validate_python(
        {"id": "c1", "type": "line", "query_name": "q", "title": ""}
    )
    resolved = make_test_resolved_chart(chart, [{"date": "2024-01-01", "value": 1.0}])
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={"c1": []},
        endpoint_label_gap_overflows={
            "c1": EndpointLabelGapOverflow(
                series_count=5,
                gap_px=18.0,
                cause=cause,
                dropped_series=dropped_series,
            )
        },
    )


def test_gap_did_not_fit_is_the_height_code() -> None:
    diags = detector.detect(_ctx("gap_did_not_fit"))
    assert [d.code for d in diags] == [WARN_ENDPOINT_LABEL_GAP_OVERFLOW.code]


def test_no_slope_is_the_tied_code() -> None:
    diags = detector.detect(_ctx("no_slope"))
    assert [d.code for d in diags] == [WARN_ENDPOINT_LABEL_RAIL_TIED.code]


def test_rail_overflow_is_the_dropped_code() -> None:
    diags = detector.detect(_ctx("rail_overflow", dropped_series=("s3", "s4")))
    assert [d.code for d in diags] == [WARN_ENDPOINT_LABEL_RAIL_OVERFLOW.code]
    assert "2 of 5" in diags[0].message
