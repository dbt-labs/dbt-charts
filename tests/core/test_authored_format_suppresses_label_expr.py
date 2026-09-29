"""The premise the whole overlap measurement rests on.

Every authored-format branch in ``emitters/_label_overlap.py`` and
``time_unit_detect.py`` — the year-context row suppression, the two-row day
label suppression, the sub-month candidate branches, the promote-to-bare-year
override — is correct only because an authored ``format`` on a temporal axis
leaves one row of the author's own text per speaking tick, never the stacked
shapes.

On a continuous temporal axis it does that by suppressing the smart cadence
``labelExpr`` outright and letting VL's native ``axis.format`` paint — which
is correct there because that branch's ``values`` is already the visible set.
(The ordinal branch keeps a ``labelExpr``, whose TEXT half is the same one row
of the author's format; its gate is pinned in
``test_authored_format_keeps_the_cadence_gate.py``.)

Nothing pinned the temporal half before this file: deleting the ``"format"
not in result`` guard leaves every one of those measurements silently wrong
while the suite stays green.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import LineChart
from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _x_axis(time_format: str | None) -> dict[str, Any]:
    data: list[dict[str, Any]] = [
        {"d": f"2022-{month:02d}-01", "v": month} for month in range(1, 13)
    ]
    chart = LineChart(
        id="c",
        type="line",
        x="d",
        y="v",
        style=LineChartStylePatch(time_format=time_format)
        if time_format
        else LineChartStylePatch(),
    )
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    spec = render_resolved_chart(resolved, data, _BOARD_RS).payload
    axis: dict[str, Any] = spec["encoding"]["x"].get("axis", {})
    return axis


def test_no_authored_format_emits_the_smart_cadence_label_expr() -> None:
    axis = _x_axis(None)
    assert "labelExpr" in axis
    assert "format" not in axis


def test_an_authored_format_suppresses_it_and_paints_the_format() -> None:
    """One row of the author's own text on every tick — which is exactly what
    the measurement in ``_label_overlap.py`` assumes it has to measure."""
    axis = _x_axis("%b %Y")
    assert axis.get("format") == "%b %Y"
    assert "labelExpr" not in axis
