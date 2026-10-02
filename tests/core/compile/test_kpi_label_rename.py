"""Contract tests for the KPI ``title`` → ``label`` rename.

* Top-level ``label:`` is the KPI's authored label (replaces ``title:``)
* ``support.label:`` replaces ``support.title:``
* ``label:`` is rejected on non-KPI chart types
* ``title:`` is rejected on KPI chart types
* Omitted ``label``/``title`` stays empty; chart ids are not user-facing prose
* ``data-chart-title`` HTML attribute (kept under that name for accessibility
  reasons) is sourced from ``chart.label`` for KPI / ``chart.title`` otherwise
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.authored import (
    AuthoredChart,
    KpiChart,
    KpiSupportConfig,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.execute.adapters import build_adapter_registry

from .conftest import (
    compile_with_board_sources as compile_board,
    project_with_db_source,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

_chart_patch_adapter = TypeAdapter(AuthoredChart)


def _compile_one(yaml_content: str):
    result = compile_board(yaml_content)
    assert result.success, [str(e) for e in result.errors]
    return result.board


class TestLabelAcceptedOnKpi:
    def test_label_is_a_chartpatch_field(self):
        p = KpiChart(type="kpi", value="revenue", label="Quarterly revenue")
        assert p.label == "Quarterly revenue"

    def test_kpi_with_label_round_trips_through_compile(self):
        board = _compile_one(
            """
title: T
queries:
  q:
    source: db
    sql: SELECT 1500000 AS revenue
charts:
  rev:
    query: q
    type: kpi
    value: revenue
    label: "Quarterly revenue"
rows:
  - rev
"""
        )
        chart = board.charts["rev"]
        assert chart.label == "Quarterly revenue"


class TestTitleRejectedOnKpi:
    def test_title_rejected_with_clear_error(self):
        with pytest.raises(ValueError, match=r"`title:` is not used on `type: kpi`"):
            KpiChart(type="kpi", value="revenue", title="Quarterly revenue")

    def test_empty_string_title_rejected(self):
        """`title: ""` must be rejected like any other title: value — the
        original truthiness check (`data.get("title")`) let falsy-but-present
        empty strings silently through."""
        with pytest.raises(ValueError, match=r"`title:` is not used on `type: kpi`"):
            KpiChart(type="kpi", value="revenue", title="")

    def test_empty_string_subtitle_rejected(self):
        with pytest.raises(ValueError, match="subtitle"):
            KpiChart.model_validate({"type": "kpi", "value": "revenue", "subtitle": ""})


class TestLabelRejectedOnNonKpi:
    @pytest.mark.parametrize("chart_type", ["bar", "line", "area", "scatter", "pie"])
    def test_label_rejected_on_non_kpi(self, chart_type):
        # label: is a KPI-only field. Non-KPI family patches use extra="forbid",
        # so label: is rejected as an extra input (ValidationError, not ValueError).
        extra: dict[str, str] = {}
        if chart_type == "pie":
            extra = {"theta": "amount"}
        with pytest.raises(ValidationError, match="label"):
            _chart_patch_adapter.validate_python(
                {"type": chart_type, "label": "oops", **extra}
            )


class TestSupportLabel:
    def test_support_label_round_trips(self):
        s = KpiSupportConfig(value="delta_pct", label="vs LQ")
        assert s.label == "vs LQ"

    def test_support_title_field_is_gone(self):
        with pytest.raises(ValueError, match="title"):
            KpiSupportConfig(value="delta_pct", title="vs LQ")  # type: ignore[call-arg]


class TestOmittedDisplayText:
    def test_kpi_omitted_label_stays_empty(self):
        board = _compile_one(
            """
title: T
queries:
  q:
    source: db
    sql: SELECT 1500000 AS revenue
charts:
  quarterly_revenue:
    query: q
    type: kpi
    value: revenue
rows:
  - quarterly_revenue
"""
        )
        chart = board.charts["quarterly_revenue"]
        assert chart.label == ""

    def test_kpi_explicit_empty_label_is_respected(self):
        board = _compile_one(
            """
title: T
queries:
  q:
    source: db
    sql: SELECT 1500000 AS revenue
charts:
  quarterly_revenue:
    query: q
    type: kpi
    value: revenue
    label: ""
rows:
  - quarterly_revenue
"""
        )
        chart = board.charts["quarterly_revenue"]
        assert chart.label == ""

    def test_non_kpi_omitted_title_stays_empty(self):
        board = _compile_one(
            """
title: T
queries:
  q:
    source: db
    sql: SELECT 'a' AS x, 1 AS y
charts:
  monthly_revenue:
    query: q
    type: bar
    x: x
    y: y
rows:
  - monthly_revenue
"""
        )
        chart = board.charts["monthly_revenue"]
        assert chart.title == ""


class TestKpiTitleAndLabelInResolvedChart:
    """KPI chart resolve(): label and title are passed through as authored."""

    def test_resolved_kpi_label_and_title_are_as_authored(self):
        from dbt_charts.core.compile.models.chart.normalized import KpiChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        chart = KpiChart(
            id="quarterly_revenue",
            type="kpi",
            label="Quarterly Revenue",
            value="total",
            query=SqlQuery(sql="SELECT 1500000 AS total", source="db"),
            query_name="q",
        )
        resolved = resolve(
            chart, [{"total": 1500000}], chart_style_context=_BOARD_STYLE
        )
        assert resolved.chart_type == "kpi"
        assert resolved.label == "Quarterly Revenue"
        assert resolved.title is None

    def test_resolved_kpi_without_label_stays_unlabeled(self):
        from dbt_charts.core.compile.models.chart.normalized import KpiChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        chart = KpiChart(
            id="quarterly_revenue",
            type="kpi",
            label="",
            value="total",
            query=SqlQuery(sql="SELECT 1500000 AS total", source="db"),
            query_name="q",
        )
        resolved = resolve(
            chart, [{"total": 1500000}], chart_style_context=_BOARD_STYLE
        )
        assert resolved.chart_type == "kpi"
        assert resolved.label == ""
        assert resolved.title is None


class TestKpiLabelJinjaResolution:
    """Variables in ``label:`` must be resolved before the KPI renderer reads
    the field — otherwise the SVG embeds the literal ``{{ var }}``."""

    _BOARD_YAML = """
title: T
sources:
  db:
    type: duckdb
    path: ":memory:"
variables:
  region:
    input: text
    default: "EMEA"
queries:
  q:
    source: db
    sql: SELECT 1500000 AS revenue
charts:
  rev:
    query: q
    type: kpi
    value: revenue
    label: "{{ region }} revenue"
rows:
  - rev
"""

    def test_kpi_label_jinja_resolves_at_render_time(self):
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.board_resolve import build_resolved_board
        from dbt_charts.core.render.chart.rendering import render_chart_item

        board = _compile_one(self._BOARD_YAML)
        # Sanity: the authored label survives compile with the jinja braces
        # intact — resolution happens at render time, not compile time.
        assert "{{ region }}" in board.charts["rev"].label

        executor = Executor(
            board, adapter_registry=build_adapter_registry(project_with_db_source())
        )
        variables = {"region": "EMEA"}
        resolved_board, render_cache = build_resolved_board(board, executor, variables)
        kpi = resolved_board.charts["rev"]

        svg, _ = render_chart_item(
            kpi,
            executor,
            variables,
            300.0,
            180.0,
            resolved_style=resolved_board.style,
            render_cache=render_cache,
        )
        # The variable must be resolved (jinja braces gone) and EMEA present
        assert "EMEA" in svg
        assert "{{ region }}" not in svg

    def test_kpi_label_jinja_resolves_in_terminal_path(self):
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.terminal import render_chart_item_terminal

        board = _compile_one(self._BOARD_YAML)
        chart = board.charts["rev"]
        executor = Executor(
            board, adapter_registry=build_adapter_registry(project_with_db_source())
        )
        # Terminal renderer must mirror the SVG path: chart.label is jinja-
        # resolved before being passed to render_kpi_terminal. Without this,
        # the literal ``{{ region }}`` would print to the terminal.
        output = render_chart_item_terminal(
            chart,
            executor,
            variables={"region": "EMEA"},
            available_width=80,
            available_height=20,
            formats=None,
        )
        assert "EMEA revenue" in output
        assert "{{ region }}" not in output


class TestDataChartTitleAttributeContract:
    """The HTML wrapper attribute stays ``data-chart-title`` for both KPI and
    non-KPI charts. Value sources from ``chart.label`` for KPI / ``chart.title``
    otherwise — verified at the wire level via render_chart_item."""

    def _render_chart(self, board_yaml: str, chart_id: str) -> str:
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.chart.rendering import render_chart_item

        board = _compile_one(board_yaml)
        executor = Executor(
            board, adapter_registry=build_adapter_registry(project_with_db_source())
        )
        rs, ctx = resolve_style_and_context(get_theme_style())
        chart = board.charts[chart_id]
        data = executor.execute_chart(chart, {})
        resolved = resolve(board.charts[chart_id], data, chart_style_context=ctx)
        svg, _ = render_chart_item(
            resolved, executor, {}, 400, 200, resolved_style=rs, render_cache={}
        )
        return svg

    def test_kpi_emits_data_chart_title_from_label(self):
        svg = self._render_chart(
            """
title: T
sources:
  db:
    type: duckdb
    path: ":memory:"
queries:
  q:
    source: db
    sql: SELECT 1500000 AS revenue
charts:
  rev:
    query: q
    type: kpi
    value: revenue
    label: "Quarterly revenue"
rows:
  - rev
""",
            "rev",
        )
        assert 'data-chart-title="Quarterly revenue"' in svg
        assert "data-chart-label=" not in svg

    def test_bar_emits_data_chart_title_from_title(self):
        svg = self._render_chart(
            """
title: T
sources:
  db:
    type: duckdb
    path: ":memory:"
queries:
  q:
    source: db
    sql: |
      SELECT 'a' AS x, 1 AS y UNION ALL SELECT 'b', 2
charts:
  rev:
    query: q
    type: bar
    x: x
    y: y
    title: "Quarterly revenue"
rows:
  - rev
""",
            "rev",
        )
        assert 'data-chart-title="Quarterly revenue"' in svg
