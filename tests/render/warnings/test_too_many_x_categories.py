"""Tests for the TOO_MANY_X_CATEGORIES render-warning detector.

Detection rule: fires when a nominal/ordinal x-axis has > 50 distinct values,
and on a bar chart's temporal x-axis too. A quantitative x-axis never trips it,
nor does a temporal axis on line/area/scatter.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    Chart,
    LineChart,
    PieChart,
)
from dbt_charts.core.compile.models.style.authored.bar import BarChartStylePatch
from dbt_charts.core.diagnostics import WARN_TOO_MANY_X_CATEGORIES, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    too_many_x_categories as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _vertical_bar(chart_id: str = "c1") -> BarChart:
    return BarChart(
        id=chart_id,
        type="bar",
        query_name="q",
        x="cat",
        y="val",
        style=BarChartStylePatch.model_construct(orientation="vertical"),
    )


def _rows(n: int) -> list[dict[str, Any]]:
    return [{"cat": f"c{i}", "val": i} for i in range(n)]


def _month_rows(n: int) -> list[dict[str, Any]]:
    return [
        {"cat": date(2019 + i // 12, i % 12 + 1, 1), "val": i * 1.0} for i in range(n)
    ]


def _make_ctx(chart: Chart, rows: list[dict[str, Any]]) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
    )


def test_fires_above_threshold_on_nominal_x() -> None:
    chart = _vertical_bar()
    warnings = detector.detect(_make_ctx(chart, _rows(51)))
    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_TOO_MANY_X_CATEGORIES.code
    assert w.field == "cat"
    assert "51" in w.message
    assert w.fix is not None


def test_no_fire_at_threshold() -> None:
    chart = _vertical_bar()
    assert detector.detect(_make_ctx(chart, _rows(50))) == []


def test_no_fire_on_temporal_x_for_line() -> None:
    """A line chart's temporal axis is a continuous draw, not a per-x band —
    density alone is never a defect there, however many points."""
    chart = LineChart(id="c1", type="line", query_name="q", x="cat", y="val")
    assert detector.detect(_make_ctx(chart, _month_rows(100))) == []


def test_fires_on_temporal_x_for_bar_above_threshold() -> None:
    """The density gate flips a bucketed temporal x-axis from ordinal to
    temporal above ~60 points — a bar still draws one band per distinct
    value regardless of the Vega-Lite encoding type, so the crowding warning
    must not go blind when that flip happens.
    """
    chart = _vertical_bar()
    warnings = detector.detect(_make_ctx(chart, _month_rows(200)))
    assert len(warnings) == 1
    assert warnings[0].code == WARN_TOO_MANY_X_CATEGORIES.code
    assert "200" in warnings[0].message


def test_no_fire_on_temporal_x_for_bar_at_low_density() -> None:
    """Below the threshold, a temporal bar axis stays silent same as ordinal."""
    chart = _vertical_bar()
    assert detector.detect(_make_ctx(chart, _month_rows(50))) == []


def test_no_fire_on_a_horizontal_bar() -> None:
    """A horizontal bar's category axis is y; the band-width detector owns it."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="cat",
        y="val",
        style=BarChartStylePatch.model_construct(orientation="horizontal"),
    )
    assert detector.detect(_make_ctx(chart, _rows(60))) == []


def test_no_fire_without_x() -> None:
    chart = PieChart(id="c1", type="pie", query_name="q", theta="val", color="cat")
    assert detector.detect(_make_ctx(chart, _rows(60))) == []


class TestTemporalBarWording:
    """The temporal arm fires for band thinning, so it must not borrow the
    categorical arm's words: no label-collision claim, no category-shaped fix.
    """

    def _warning(self) -> Diagnostic:
        chart = _vertical_bar("monthly")
        rows = _month_rows(76)
        warnings = detector.detect(_make_ctx(chart, rows))
        assert len(warnings) == 1
        return warnings[0]

    def test_message_claims_no_label_collision(self) -> None:
        message = self._warning().message
        assert "collide" not in message
        assert "labels" not in message
        assert "76" in message

    def test_fix_drops_the_categorical_vocabulary(self) -> None:
        fix = self._warning().fix
        assert fix is not None
        assert "top N" not in fix
        assert "table" not in fix
        assert "categories" not in fix

    def test_message_is_the_temporal_template_verbatim(self) -> None:
        """Pins wording and threshold together: the only limit this detector
        applies is its own category count, so that is the only one it may name.
        """
        assert self._warning().message == (
            "Chart 'monthly': x field 'cat' has 76 distinct time buckets; "
            "a bar draws one band per bucket, so the bands are too thin to read "
            f"(limit: {detector._MAX_CATEGORIES})."
        )


def test_categorical_arm_still_reads_the_registry_templates() -> None:
    """The temporal branch must not leak into the nominal/ordinal arm, which
    keeps deriving both strings from the registry.
    """
    chart = _vertical_bar()
    warnings = detector.detect(_make_ctx(chart, _rows(51)))
    assert warnings[0].message == WARN_TOO_MANY_X_CATEGORIES.message_template.format(
        chart_id="c1", field="cat", count=51, max_categories=detector._MAX_CATEGORIES
    )
    assert warnings[0].fix == WARN_TOO_MANY_X_CATEGORIES.fix_template


def _ctx_for(*charts_and_rows: tuple[Chart, list[dict[str, Any]]]) -> WarningContext:
    resolved = [make_test_resolved_chart(c, rows) for c, rows in charts_and_rows]
    return WarningContext(
        board_spec=make_test_resolved_board(charts={r.id: r for r in resolved}),
        chart_results={
            r.id: rows for r, (_, rows) in zip(resolved, charts_and_rows, strict=True)
        },
    )


def test_one_chart_whose_rows_do_not_regroup_does_not_silence_the_others() -> None:
    """Rows carrying a panel the chart was resolved without fail `regroup`
    (`ChartDataError`); a healthy neighbor must still be judged."""
    bad = BarChart.model_validate(
        {
            "id": "bad",
            "type": "bar",
            "query_name": "q",
            "x": "cat",
            "y": "val",
            "multiples": {"columns": "panel"},
            "style": {"orientation": "vertical"},
        }
    )
    resolved_rows = [{**row, "panel": "p"} for row in _rows(60)]
    stray_rows = [{**row, "panel": "stray"} for row in _rows(60)]
    good = LineChart(id="good", type="line", query_name="q", x="cat", y="val")
    resolved = [
        make_test_resolved_chart(bad, resolved_rows),
        make_test_resolved_chart(good, _rows(60)),
    ]
    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={r.id: r for r in resolved}),
        chart_results={"bad": stray_rows, "good": _rows(60)},
    )
    assert [w.chart for w in detector.detect(ctx)] == ["good"]


def test_fires_on_a_colored_line_with_too_many_categories() -> None:
    chart = LineChart(
        id="c1", type="line", query_name="q", x="cat", y="val", color="grp"
    )
    rows = [{**row, "grp": "a"} for row in _rows(60)]
    assert len(detector.detect(_ctx_for((chart, rows)))) == 1


def test_fires_on_a_faceted_bar_with_too_many_categories() -> None:
    chart = BarChart.model_validate(
        {
            "id": "c1",
            "type": "bar",
            "query_name": "q",
            "x": "cat",
            "y": "val",
            "multiples": {"columns": "panel"},
            "style": {"orientation": "vertical"},
        }
    )
    rows = [{**row, "panel": "p"} for row in _rows(60)]
    assert len(detector.detect(_ctx_for((chart, rows)))) == 1
