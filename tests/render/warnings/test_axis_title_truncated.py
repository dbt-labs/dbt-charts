"""Tests for the WARN_AXIS_TITLE_TRUNCATED render-warning detector.

Detection rule: fires when an axis title is pre-wrapped to ≤2 lines and the
text had to be cut with an ellipsis — either because the authored title ran
past two wrapped lines at the given extent, or because a single word was wider
than the budget and got cut to fit. The truncation fact is recorded by the
single wrap site (`wrap_axis_title` via `resolve_xy_titles`); no re-measurement
occurs downstream.

The warning path and message reference the AUTHORED field name ("x_label" /
"y_label") so the squiggle lands on the YAML line the author actually wrote —
a horizontal bar swaps VL channels (authored y_label renders on VL x), but the
warning still names "y_label".
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.style.resolved._base import ResolvedAxisStyle
from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    build_resolved_axis,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.diagnostics import WARN_AXIS_TITLE_TRUNCATED
from dbt_charts.core.render.chart.emitters._cartesian import resolve_xy_titles
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.text_truncation import (
    TextTruncation,
    collect_text_truncations,
)
from dbt_charts.core.render.warnings import axis_title_truncated as detector
from dbt_charts.core.render.warnings.base import WarningContext

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart
from ...core.conftest import fixture_chart_for_type

# A title long enough to exceed two wrapped lines at a narrow extent.
_VERY_LONG = (
    "Connections Timeline Count Connections Observed Across All Categories "
    "Over The Past Twelve Months"
)
# A short title that fits in one line at normal extents.
_SHORT = "Revenue"


def _axes() -> tuple[ResolvedAxisStyle, ResolvedAxisStyle]:
    chart_style_context = resolve_chart_style_context(
        get_theme_style(get_default_theme_name())
    )
    (
        ax_merged,
        ay_merged,
        ax_band,
        ay_band,
        ay_format_authored,
        ay_format_is_alias,
        _ay_format_raw,
    ) = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type("bar"),
        "bar",
        "ordinal",
        "quantitative",
        AxisOverrides(),
    )
    return (
        build_resolved_axis(
            ax_merged,
            band_position=ax_band,
            chart_id="test",
            format_authored=ay_format_authored,
            format_is_alias=ay_format_is_alias,
        ),
        build_resolved_axis(
            ay_merged,
            band_position=ay_band,
            chart_id="test",
            format_authored=ay_format_authored,
            format_is_alias=ay_format_is_alias,
        ),
    )


def _make_ctx(
    chart: Any,
    rows: list[dict[str, Any]],
    text_truncations: dict[str, Any] | None = None,
) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        vega_specs={},
        text_truncations=text_truncations or {},
    )


# ── truncation-fact threading ─────────────────────────────────────────────────


def test_wrap_site_records_truncation_via_context_var() -> None:
    """resolve_xy_titles records truncation into the open ContextVar sink.

    This pins that the truncation fact comes from the single wrap call — no
    downstream re-measurement: the detector reads the sink, not the spec.
    The authored field name ("y_label") is recorded, not the VL channel ("y").
    """
    ax, ay = _axes()
    with collect_text_truncations() as sinks:
        # Very narrow box forces the long y title to be truncated.
        resolve_xy_titles(
            "month",
            "observed",
            None,
            _VERY_LONG,
            ax,
            ay,
            RenderBox(width=500, height=220),
            "chart1",
        )
    # Truncation was recorded for chart1 on the authored y_label field.
    assert "chart1" in sinks
    records = sinks["chart1"]
    assert len(records) == 1
    assert records[0].authored_field == "y_label"
    assert records[0].authored_text == _VERY_LONG


def test_wrap_site_does_not_record_when_title_fits() -> None:
    """No truncation is recorded when the title wraps cleanly to ≤2 lines."""
    ax, ay = _axes()
    with collect_text_truncations() as sinks:
        resolve_xy_titles(
            "month",
            "observed",
            None,
            _SHORT,
            ax,
            ay,
            RenderBox(width=500, height=900),
            "chart2",
        )
    assert "chart2" not in sinks


# ── detector output ───────────────────────────────────────────────────────────


def test_detector_fires_on_truncated_y_title() -> None:
    """Over-long y title → exactly one warning naming chart_id, authored field, text."""
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    chart = BarChart(
        id="c1", type="bar", query_name="q", x="month", y="revenue", y_label=_VERY_LONG
    )
    rows = [{"month": "Jan", "revenue": 10}]
    truncations = {
        "c1": [
            TextTruncation(
                surface="axis_title", authored_field="y_label", authored_text=_VERY_LONG
            )
        ]
    }
    ctx = _make_ctx(chart, rows, truncations)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_AXIS_TITLE_TRUNCATED.code
    assert w.chart == "c1"
    assert "c1" in w.message
    assert "y_label" in w.message
    assert _VERY_LONG in w.message
    assert w.path == "charts.c1.y_label"


def test_detector_fires_on_truncated_x_title() -> None:
    """Over-long x title → exactly one warning naming chart_id, authored field, text."""
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    chart = BarChart(
        id="c2", type="bar", query_name="q", x="month", y="revenue", x_label=_VERY_LONG
    )
    rows = [{"month": "Jan", "revenue": 10}]
    truncations = {
        "c2": [
            TextTruncation(
                surface="axis_title", authored_field="x_label", authored_text=_VERY_LONG
            )
        ]
    }
    ctx = _make_ctx(chart, rows, truncations)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_AXIS_TITLE_TRUNCATED.code
    assert "c2" in w.message
    assert "x_label" in w.message
    assert _VERY_LONG in w.message
    assert w.path == "charts.c2.x_label"


def test_detector_silent_on_clean_wrap() -> None:
    """A title that fits within two lines → no warning."""
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    chart = BarChart(
        id="c3", type="bar", query_name="q", x="month", y="revenue", y_label=_SHORT
    )
    rows = [{"month": "Jan", "revenue": 10}]
    ctx = _make_ctx(chart, rows, {})  # no truncations
    assert detector.detect(ctx) == []


def test_detector_produces_one_warning_per_truncated_axis() -> None:
    """Both x and y truncated → two warnings, one per authored field."""
    from dbt_charts.core.compile.models.chart.normalized import BarChart

    chart = BarChart(
        id="c4",
        type="bar",
        query_name="q",
        x="month",
        y="revenue",
        x_label=_VERY_LONG,
        y_label=_VERY_LONG,
    )
    rows = [{"month": "Jan", "revenue": 10}]
    truncations = {
        "c4": [
            TextTruncation(
                surface="axis_title", authored_field="x_label", authored_text=_VERY_LONG
            ),
            TextTruncation(
                surface="axis_title", authored_field="y_label", authored_text=_VERY_LONG
            ),
        ]
    }
    ctx = _make_ctx(chart, rows, truncations)
    warnings = detector.detect(ctx)
    assert len(warnings) == 2
    paths = {w.path for w in warnings}
    assert paths == {"charts.c4.x_label", "charts.c4.y_label"}


# ── horizontal bar: warning names authored field, not VL channel ──────────────


def test_horizontal_bar_records_authored_field_not_vl_channel() -> None:
    """Horizontal bar swaps channels, but the warning names the AUTHORED field.

    authored y_label renders on VL x in a horizontal bar. The squiggle must land
    on the authored y_label key — not on x_label — so resolve_xy_titles must be
    told the authored field mapping at the call site.
    """
    ax, ay = _axes()
    with collect_text_truncations() as sinks:
        # Horizontal bar passes authored y_label as x_label (VL channel x = measure).
        # x_authored_field="y_label" tells resolve_xy_titles the correct authored field.
        resolve_xy_titles(
            None,  # x_field in VL (measure, authored y space)
            "category",  # y_field in VL (category, authored x space)
            _VERY_LONG,  # x_label in VL = authored y_label
            None,  # y_label in VL = authored x_label
            ax,
            ay,
            RenderBox(width=200, height=600),
            "hbar",
            x_authored_field="y_label",
            y_authored_field="x_label",
        )
    assert "hbar" in sinks
    records = sinks["hbar"]
    assert len(records) == 1
    # Authored field 'y_label' is recorded (not VL channel 'x').
    assert records[0].authored_field == "y_label"
    assert records[0].authored_text == _VERY_LONG


def test_vertical_bar_records_authored_field_without_swap() -> None:
    """Vertical bar: no channel swap — authored y_label records as y_label."""
    ax, ay = _axes()
    with collect_text_truncations() as sinks:
        resolve_xy_titles(
            "month",  # x_field in VL (authored x)
            "observed",  # y_field in VL (authored y)
            None,
            _VERY_LONG,  # y_label (authored y_label)
            ax,
            ay,
            RenderBox(width=500, height=220),
            "vbar",
            # defaults: x_authored_field="x_label", y_authored_field="y_label"
        )
    assert "vbar" in sinks
    records = sinks["vbar"]
    assert len(records) == 1
    assert records[0].authored_field == "y_label"


# ── end-to-end wiring test (HIGH-3) ──────────────────────────────────────────


def test_render_dashboard_fires_warning_for_truncated_axis_title(
    tmp_path, local_project
) -> None:
    """render_dashboard wires axis_title_truncations into the warning pipeline.

    This is the wiring test: disabling the axis_title_truncations pass-through
    in renderer.py would leave this test failing even though all unit tests pass.
    A board with an over-long y_label → WARN_AXIS_TITLE_TRUNCATED in result.warnings.
    A board with a short y_label → no WARN_AXIS_TITLE_TRUNCATED.
    """
    from unittest.mock import Mock

    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.project import InMemoryBoard

    ok = Mock()
    ok.is_success = True
    ok.data = [{"month": "Jan", "revenue": 5}]
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    registry = Mock()
    registry.execute.return_value = ok
    registry.project_file_sources.return_value = {}

    project = local_project(tmp_path)
    long_label = (
        "Connections Timeline Count Connections Observed Across All Categories "
        "Over The Past Twelve Months"
    )
    truncated_board = InMemoryBoard(
        f"""
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    y_label: "{long_label}"
    width: 400
    style: {{orientation: vertical}}
rows:
  - c
""",
        path=project.path("charts/_t.yml"),
    )
    result = render_dashboard(
        board=truncated_board,
        adapter_registry=registry,
        format="json",
        project=project,
        result_cache=None,
    )
    codes = {w.code for w in result.warnings}
    assert WARN_AXIS_TITLE_TRUNCATED.code in codes, (
        f"Expected WARN_AXIS_TITLE_TRUNCATED in warnings, got: {codes}"
    )

    # A horizontal bar renders the authored y_label on the VL x channel; the
    # warning must still point at the authored key. This pins the
    # x_authored_field/y_authored_field mapping in bar._emit_horizontal —
    # removing those kwargs flips the path to charts.c.x_label and fails here.
    horizontal_board = InMemoryBoard(
        f"""
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    y_label: "{long_label}"
    width: 200
    style: {{orientation: horizontal}}
rows:
  - c
""",
        path=project.path("charts/_t.yml"),
    )
    horizontal_result = render_dashboard(
        board=horizontal_board,
        adapter_registry=registry,
        format="json",
        project=project,
        result_cache=None,
    )
    horizontal_warnings = [
        w
        for w in horizontal_result.warnings
        if w.code == WARN_AXIS_TITLE_TRUNCATED.code
    ]
    assert horizontal_warnings, (
        "Expected WARN_AXIS_TITLE_TRUNCATED for the horizontal bar, got: "
        f"{ {w.code for w in horizontal_result.warnings} }"
    )
    assert {w.path for w in horizontal_warnings} == {"charts.c.y_label"}

    # A short label must NOT trigger the warning.
    clean_board = InMemoryBoard(
        """
source: examples_db
queries:
  q: SELECT month, revenue FROM t
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    y_label: Revenue
    width: 400
    height: 220
rows:
  - c
""",
        path=project.path("charts/_t.yml"),
    )
    clean_result = render_dashboard(
        board=clean_board,
        adapter_registry=registry,
        format="json",
        project=project,
        result_cache=None,
    )
    clean_codes = {w.code for w in clean_result.warnings}
    assert WARN_AXIS_TITLE_TRUNCATED.code not in clean_codes


def test_wrap_site_records_truncation_for_a_single_over_wide_word() -> None:
    """A lone word wider than its budget is cut, so the warning must fire.

    The line count alone cannot carry this signal: a word cut to fit still
    occupies one line, which is not *more* than ``max_lines``.
    """
    ax, ay = _axes()
    with collect_text_truncations() as sinks:
        resolve_xy_titles(
            "month",
            "observed",
            None,
            "commits",
            ax,
            ay,
            RenderBox(width=500, height=110),
            "chart3",
        )
    assert sinks["chart3"][0].authored_text == "commits"


def test_wrap_site_stays_silent_when_the_panel_budget_is_honest() -> None:
    """The same title in a 7-row facet panel fits, so no warning is recorded."""
    ax, ay = _axes()
    with collect_text_truncations() as sinks:
        resolve_xy_titles(
            "month",
            "observed",
            None,
            "commits",
            ax,
            ay,
            RenderBox(width=500, height=110, panel_rows=7),
            "chart4",
        )
    assert "chart4" not in sinks
