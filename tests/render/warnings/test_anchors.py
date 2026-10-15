"""Every render warning points at the authored key it is about.

A detector names the most specific authored path its complaint concerns, so the
editor squiggle lands on the field at fault rather than the whole chart block.
The anchor does not have to exist in the file: ``stamp_diagnostics`` walks up to
the nearest authored ancestor, so an anchor naming an absent key (a missing axis
format is exactly a missing ``format:`` line) degrades instead of misfiring.

Detectors with nothing more specific to say than "this chart" leave ``path``
unset and ``run_all`` fills the ``charts.<id>`` fallback; that fallback is pinned
in ``test_registry.py``, not here.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    LineChart,
    PieChart,
    PointMapChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.style.authored.bar import BarChartStylePatch
from dbt_charts.core.render.warnings import (
    WarningContext,
    pie_dominant_segment,
    point_map_out_of_projection,
    query_returned_zero_rows,
    redundant_encoding,
    temporal_single_point,
    too_many_color_categories,
    too_many_x_categories,
    y_encoding_mostly_null,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _ctx(chart: Any, rows: list[dict[str, Any]]) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={},
    )


def test_too_many_x_categories_anchors_on_x() -> None:
    rows = [{"cat": f"c{i}", "val": i} for i in range(51)]
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="cat",
        y="val",
        style=BarChartStylePatch.model_construct(orientation="vertical"),
    )
    ctx = _ctx(chart, rows)

    (w,) = too_many_x_categories.detect(ctx)
    assert w.path == "charts.c1.x"


def test_temporal_single_point_anchors_on_x() -> None:
    rows = [{"day": "2026-01-01", "val": 1}]
    chart = LineChart(id="c1", type="line", query_name="q", x="day", y="val")
    ctx = _ctx(chart, rows)

    (w,) = temporal_single_point.detect(ctx)
    assert w.path == "charts.c1.x"


def test_too_many_color_categories_anchors_on_color() -> None:
    rows = [{"cat": f"c{i}", "val": i, "series": f"s{i}"} for i in range(40)]
    chart = LineChart(
        id="c1", type="line", query_name="q", x="cat", y="val", color="series"
    )
    ctx = _ctx(chart, rows)

    warnings = too_many_color_categories.detect(ctx)
    assert [w.path for w in warnings] == ["charts.c1.color"]


def test_y_encoding_mostly_null_anchors_on_y() -> None:
    rows = [{"cat": f"c{i}", "val": None} for i in range(10)]
    chart = BarChart(id="c1", type="bar", query_name="q", x="cat", y="val")

    (w,) = y_encoding_mostly_null.detect(_ctx(chart, rows))
    assert w.path == "charts.c1.y"


def test_pie_dominant_segment_anchors_on_type_because_the_fix_is_a_different_chart() -> (
    None
):
    """The warning is answered by "this should not be a pie" — the fix text
    says so — so the mark belongs on `type:`, not on the data channel."""
    chart = PieChart(id="c1", type="pie", query_name="q", color="seg", theta="val")
    dominant = [{"seg": "a", "val": 999}, {"seg": "b", "val": 1}]
    (w,) = pie_dominant_segment.detect(_ctx(chart, dominant))
    assert w.path == "charts.c1.type"


def test_query_returned_zero_rows_anchors_on_the_query_not_the_chart() -> None:
    """Nothing about the chart definition is wrong — the query returned
    nothing, so the mark belongs on `query:`."""
    chart = BarChart(id="c1", type="bar", query_name="q", x="cat", y="val")

    (w,) = query_returned_zero_rows.detect(_ctx(chart, []))
    assert w.path == "charts.c1.query"


def test_redundant_encoding_marks_one_channel_and_relates_the_other() -> None:
    """The complaint is about a *pair* of channels, so one squiggle is half the
    story: the second channel rides along as a related location."""
    rows = [{"cat": "a", "val": 1}]
    chart = ScatterChart(
        id="c1", type="scatter", query_name="q", x="cat", y="val", size="val"
    )
    ctx = _ctx(chart, rows)

    warnings = redundant_encoding.detect(ctx)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.path is not None
    assert w.path.startswith("charts.c1.")
    assert len(w.related) == 1
    assert w.related[0].path is not None
    assert w.related[0].path.startswith("charts.c1.")
    assert w.related[0].path != w.path
    assert w.related[0].message is not None


def test_point_map_out_of_projection_anchors_on_projection() -> None:
    rows = [{"lat": 0.0, "lon": 0.0, "val": 1}]
    chart = PointMapChart(
        id="c1",
        type="point_map",
        query_name="q",
        latitude="lat",
        longitude="lon",
        projection="albersUsa",
    )
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={resolved.id: {"data": {"values": []}}},
    )

    for w in point_map_out_of_projection.detect(ctx):
        assert w.path == "charts.c1.projection"


class TestDeepStyleAnchorsNameRealAuthoredPaths:
    """The three anchors that reach deep into `style:` must name keys the schema
    actually has.

    A unit assertion on `Diagnostic.path` cannot catch an invented key: the
    string simply fails to resolve and the mark silently lands on a containing
    node, so the warning still "works" while pointing at the wrong line. These
    compile real YAML and assert the anchor is a key in the resulting source
    map, which is the property an invented path cannot fake.

    All three were wrong on first write — `style.color.scale.palette` (no
    `scale` node on ColorStyle), `style.axis_y.format` (pre-0.4.0 spelling;
    `format` lives on `axis_y.labels`), and `style.<family>.labels` (value
    labels are authored under `marks.<mark>.labels`).
    """

    def _source_map_keys(self, yaml: str) -> frozenset[str]:
        from dbt_charts.core.compile import compile

        result = compile(yaml, file="f.yaml")
        assert result.success, result.errors
        return frozenset(result.source_map)

    def test_palette_anchor_is_a_real_authored_path(self) -> None:
        keys = self._source_map_keys(
            """queries:
  q:
    type: values
    rows:
      - {cat: a, val: 1}
charts:
  c1:
    type: bar
    query: q
    x: cat
    y: val
    color: cat
    style:
      color:
        categorical:
          palette: RdYlGn
rows:
  - c1
"""
        )
        assert "charts.c1.style.color.categorical.palette" in keys
        assert "charts.c1.style.color.scale.palette" not in keys

    def test_axis_format_anchor_is_a_real_authored_path(self) -> None:
        keys = self._source_map_keys(
            """queries:
  q:
    type: values
    rows:
      - {month: 1, revenue_amount: 5}
charts:
  c1:
    type: bar
    query: q
    x: month
    y: revenue_amount
    style:
      axis_y:
        labels:
          format: "$,.2f"
rows:
  - c1
"""
        )
        assert "charts.c1.style.axis_y.labels.format" in keys

    def test_value_label_anchor_is_a_real_authored_path(self) -> None:
        keys = self._source_map_keys(
            """queries:
  q:
    type: values
    rows:
      - {cat: a, val: 1}
charts:
  c1:
    type: bar
    query: q
    x: cat
    y: val
    style:
      marks:
        bar:
          labels:
            visible: true
rows:
  - c1
"""
        )
        assert "charts.c1.style.marks.bar.labels" in keys
        # The family segment (`style.bar.…`) is board-level, not chart-local.
        assert "charts.c1.style.bar.marks.bar.labels" not in keys
