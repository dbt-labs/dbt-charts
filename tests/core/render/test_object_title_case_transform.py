"""Object title renderers apply ``style.title.font.case`` consistently.

Chart, table, and spark titles share the case transform. Pre-fix, Vega chart
titles ran ``apply_case`` via ``set_chart_title`` but table titles called
``prepare_title_text`` directly and skipped it — adjacent chart/table titles
disagreed on casing in any theme that set ``style.title.font.case``.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import (
    get_config,
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

from .._engine_render import render_table_svg


def _resolved_style_with_title_case(case: str) -> tuple:
    """Build resolved (ResolvedStyle, ChartStyleContext) with the given title.font.case."""
    get_config()  # ensure settings initialized
    new_font = get_theme_style().title.font.model_copy(update={"case": case})
    new_title = get_theme_style().title.model_copy(update={"font": new_font})
    new_style = get_theme_style().model_copy(update={"title": new_title})
    return resolve_style_and_context(new_style)


def _make_table_chart(title: str):
    from dbt_charts.core.compile.models.chart.normalized import TableChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    return TableChart(
        id="tbl_test",
        type="table",
        title=title,
        query=SqlQuery(sql="SELECT 1", source="test_db"),
    )


class TestTableTitleCaseTransform:
    """Table title renderer applies ``style.title.font.case``."""

    _data = [{"col_a": "x", "col_b": 1}]

    def test_upper_case_title_renders_in_uppercase(self) -> None:
        """style.title.font.case='upper' uppercases the table title in the SVG."""

        board_rs, board_ctx = _resolved_style_with_title_case("upper")
        chart = resolve(
            _make_table_chart("hello world"),
            self._data,
            chart_style_context=board_ctx,
        )
        svg = render_table_svg(
            chart,
            self._data,
            720.0,
            board_style=board_rs,
        )
        assert "HELLO WORLD" in svg, (
            "Table title must uppercase when style.title.font.case=='upper'. "
            "The Vega chart path applies this via apply_case in set_chart_title; "
            "the table path must agree."
        )
        assert "hello world" not in svg.replace(">HELLO WORLD<", ""), (
            "Table SVG still contains lowercase 'hello world' outside the "
            "uppercased title — case transform was not applied."
        )

    def test_title_case_renders_in_title_case(self) -> None:
        """style.title.font.case='title' produces Title Case (Chicago/Gruber)."""

        board_rs, board_ctx = _resolved_style_with_title_case("title")
        chart = resolve(
            _make_table_chart("the rise of dashboards"),
            self._data,
            chart_style_context=board_ctx,
        )
        svg = render_table_svg(
            chart,
            self._data,
            720.0,
            board_style=board_rs,
        )
        # 'the' is a stopword that Chicago title-case lowercases except as
        # first word — full input lowercased should not appear verbatim.
        assert "The Rise of Dashboards" in svg, (
            "Table title must apply Chicago title-case when "
            "style.title.font.case=='title'."
        )

    def test_none_case_leaves_title_unchanged(self) -> None:
        """style.title.font.case='none' (default) emits the authored string."""

        board_rs, board_ctx = _resolved_style_with_title_case("none")
        chart = resolve(
            _make_table_chart("hello world"),
            self._data,
            chart_style_context=board_ctx,
        )
        svg = render_table_svg(
            chart,
            self._data,
            720.0,
            board_style=board_rs,
        )
        assert "hello world" in svg
        assert "HELLO WORLD" not in svg


class TestSparkTitleCaseTransform:
    """Spark title renderer applies ``style.title.font.case``."""

    _data = [{"k": "a", "v": 1}, {"k": "b", "v": 2}]

    def _make_spark_chart(self, title: str):
        from dbt_charts.core.compile.models.chart.normalized import SparkBarChart
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        return SparkBarChart(
            id="spk_test",
            type="spark_bar",
            title=title,
            x="v",
            y="k",
            query=SqlQuery(sql="SELECT 1", source="test_db"),
        )

    def test_title_case_renders_in_title_case(self) -> None:
        from .._engine_render import render_spark_bar_svg

        board_rs, board_ctx = _resolved_style_with_title_case("title")
        chart = resolve(
            self._make_spark_chart("spark narrow"),
            self._data,
            chart_style_context=board_ctx,
        )
        svg = render_spark_bar_svg(
            chart,
            self._data,
            width=400.0,
            board_style=board_rs,
        )
        assert "Spark Narrow" in svg, (
            "Spark title must apply Chicago title-case when "
            "style.title.font.case=='title' (matches chart and table)."
        )

    def test_upper_case_renders_in_uppercase(self) -> None:
        from .._engine_render import render_spark_bar_svg

        board_rs, board_ctx = _resolved_style_with_title_case("upper")
        chart = resolve(
            self._make_spark_chart("spark narrow"),
            self._data,
            chart_style_context=board_ctx,
        )
        svg = render_spark_bar_svg(
            chart,
            self._data,
            width=400.0,
            board_style=board_rs,
        )
        assert "SPARK NARROW" in svg
