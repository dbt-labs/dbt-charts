"""Tests for the WARN_SERIES_LABEL_TRUNCATED render-warning detector.

Detection rule: fires when the endpoint-label feature capped the rail on a
layout that honors that width, so Vega will ellipsize the recorded names. The
truncation fact is recorded by the feature at the site that commits the layout;
the detector reads the sink, never the spec.

The warning names the authored key the labels came from — `color` for a
series-color chart, `y` for a wide-form area, which has no color key at all.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.diagnostics import WARN_SERIES_LABEL_TRUNCATED
from dbt_charts.core.render.chart.series_label_truncation import (
    SeriesLabelSource,
    SeriesLabelTruncation,
)
from dbt_charts.core.render.warnings import series_label_truncated as detector
from dbt_charts.core.render.warnings.base import WarningContext

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

_LONG = "Enterprise Cloud Data Integration Platform — North America Region West"


def _ctx(truncations: dict[str, list[SeriesLabelTruncation]] | None = None):
    from dbt_charts.core.compile.models.chart.normalized import LineChart

    chart = LineChart(
        id="c1", type="line", query_name="q", x="month", y="revenue", color="segment"
    )
    rows: list[dict[str, Any]] = [
        {"month": "2024-01-01", "revenue": 10, "segment": _LONG}
    ]
    resolved = make_test_resolved_chart(chart, rows)
    return WarningContext(
        board_spec=make_test_resolved_board(charts={resolved.id: resolved}),
        chart_results={resolved.id: rows},
        series_label_truncations=truncations or {},
    )


def _cut(
    *names: str, authored_field: SeriesLabelSource = "color"
) -> list[SeriesLabelTruncation]:
    return [
        SeriesLabelTruncation(authored_field=authored_field, series_name=n)
        for n in names
    ]


def test_detector_anchors_on_the_color_key_and_quotes_the_full_value() -> None:
    """A cut color label → one warning on `color`, carrying the untruncated text."""
    warnings = detector.detect(_ctx({"c1": _cut(_LONG)}))

    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.code == WARN_SERIES_LABEL_TRUNCATED.code
    assert warning.path == "charts.c1.color"
    # The full value must survive into the message — seeing what was cut is the
    # entire point of the warning.
    assert _LONG in warning.message
    assert warning.fix is not None
    assert "style.endpoint_labels.visible: false" in warning.fix


def test_detector_anchors_on_y_for_wide_form_measures() -> None:
    """A wide-form area has no color key, so the warning points at `y`."""
    warnings = detector.detect(_ctx({"c1": _cut(_LONG, authored_field="y")}))

    assert len(warnings) == 1
    assert warnings[0].path == "charts.c1.y"
    # The internal fold column must never surface in author-facing text.
    assert "__df_wide_measure_label" not in warnings[0].message
    fix = warnings[0].fix
    assert fix is not None and "Shorten the y values" in fix


def test_detector_folds_many_cut_labels_into_one_warning() -> None:
    """Thirty long categories are one problem, not thirty warnings."""
    warnings = detector.detect(_ctx({"c1": _cut(*(f"{_LONG} {i}" for i in range(30)))}))

    assert len(warnings) == 1
    assert "30 series labels" in warnings[0].message
    assert "and 27 more" in warnings[0].message


def test_detector_silent_when_nothing_was_truncated() -> None:
    """An empty sink → no warnings."""
    assert detector.detect(_ctx()) == []


# ── end-to-end wiring ────────────────────────────────────────────────────────


def test_render_dashboard_fires_the_warning_for_a_long_color_column(
    tmp_path, local_project
) -> None:
    """render_dashboard wires series_label_truncations into the warning pipeline.

    This is the wiring test: dropping the pass-through in renderer.py or the
    DETECTORS entry in registry.py would leave this failing while every unit
    test above still passes.
    """
    from unittest.mock import Mock

    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.project import InMemoryBoard

    def _render(segment: str) -> set[str]:
        ok = Mock()
        ok.is_success = True
        ok.data = [
            {"month": month, "revenue": value, "segment": segment}
            for month, value in (("2024-01-01", 10), ("2024-02-01", 20))
        ]
        ok.resolved_relations = None
        ok.truncated_reason = None
        registry = Mock()
        registry.execute.return_value = ok
        registry.project_file_sources.return_value = {}
        project = local_project(tmp_path)
        board = InMemoryBoard(
            """
source: examples_db
queries:
  q: SELECT month, revenue, segment FROM t
charts:
  c:
    query: q
    type: line
    x: month
    y: revenue
    color: segment
    width: 400
rows:
  - c
""",
            path=project.path("charts/_t.yml"),
        )
        result = render_dashboard(
            board=board,
            adapter_registry=registry,
            format="json",
            project=project,
            result_cache=None,
        )
        return {w.code for w in result.warnings}

    long_name = "Enterprise Cloud Data Integration Platform — North America West"
    assert WARN_SERIES_LABEL_TRUNCATED.code in _render(long_name)
    assert WARN_SERIES_LABEL_TRUNCATED.code not in _render("Retail")
