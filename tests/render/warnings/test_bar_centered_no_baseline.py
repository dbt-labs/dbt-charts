"""Tests for the WARN_BAR_CENTERED_NO_BASELINE detector.

Detection rule: fires on every resolved bar whose stack mode is `center`, in
either orientation and with any number of series, and so on every funnel.
"""

from __future__ import annotations

from typing import Literal

import pytest

from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.diagnostics import WARN_BAR_CENTERED_NO_BASELINE, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    bar_centered_no_baseline as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

_ROWS = [
    {"stage": "Visit", "kind": "web", "n": 100},
    {"stage": "Visit", "kind": "app", "n": 60},
    {"stage": "Buy", "kind": "web", "n": 20},
    {"stage": "Buy", "kind": "app", "n": 10},
]


def _detect(
    stack: Literal["none", "zero", "normalize", "center"] | None,
    orientation: str = "horizontal",
    color: str | None = None,
) -> list[Diagnostic]:
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="stage",
        y="n",
        color=color,
        stack=stack,
        style=BarChartStylePatch.model_validate(
            {"orientation": orientation, **({"stack": stack} if stack else {})}
        ),
    )
    resolved = make_test_resolved_chart(chart, _ROWS)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return detector.detect(
        WarningContext(board_spec=board, chart_results={resolved.id: _ROWS})
    )


@pytest.mark.parametrize(
    ("orientation", "color"),
    [("horizontal", None), ("vertical", None), ("horizontal", "kind")],
    ids=["horizontal", "vertical", "multi-series"],
)
def test_fires_on_a_centered_bar(orientation: str, color: str | None) -> None:
    warnings = _detect(stack="center", orientation=orientation, color=color)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_BAR_CENTERED_NO_BASELINE.code
    assert w.message == (
        "Centered bars share no baseline, so their lengths are hard to compare."
    )
    assert w.fix == (
        "To compare them precisely, set `style.stack: zero` so every bar starts at "
        "zero."
    )
    assert w.path == "charts.c1.style.stack"


def test_fires_on_a_funnel_centered_by_its_recipe() -> None:
    chart = BarChart(id="c1", type="funnel", query_name="q", x="stage", y="n")
    resolved = make_test_resolved_chart(chart, _ROWS)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    (w,) = detector.detect(
        WarningContext(board_spec=board, chart_results={resolved.id: _ROWS})
    )
    assert w.code == WARN_BAR_CENTERED_NO_BASELINE.code


@pytest.mark.parametrize("stack", [None, "zero", "normalize", "none"])
def test_silent_on_every_other_stack(
    stack: Literal["none", "zero", "normalize"] | None,
) -> None:
    assert _detect(stack=stack, color="kind") == []
