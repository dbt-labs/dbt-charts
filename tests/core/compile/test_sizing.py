"""Tests for compile-side sizing helpers: get_board_gap, parse_dimension, the hug.

Both are pure functions of Board/theme config or authored strings — no query
data, no executor, no render-phase measurement — so they live in
compile/sizing.py rather than render/sizing.py.
"""

import dataclasses
import types

import pytest

from dbt_charts.core.compile import compile, sizing
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.normalized import Board, Layout
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.compile.resolve.style.typography import resolve_title_font
from dbt_charts.core.compile.sizing import (
    _one_line_title_width,
    board_container_width,
    config_chart_width,
    get_board_gap,
    parse_dimension,
)

from .._board_utils import make_test_board
from .._svg_render import render_board_svg_result


def _with_gap_override(layout_type: str, gap: float) -> Board:
    """Build a Board whose resolved_style overrides the gap for one layout type.

    Uses model_copy to build a distinctive resolved style (per the
    distinctive-value-plus-propagation test pattern) rather than pinning a
    theme literal.
    """
    board = make_test_board(layout=Layout(type=layout_type))
    rs = board.resolved_style
    new_layout_section = getattr(rs.layout, layout_type).model_copy(update={"gap": gap})
    new_layout = rs.layout.model_copy(update={layout_type: new_layout_section})
    board.resolved_style = dataclasses.replace(rs, layout=new_layout)
    return board


class TestGetBoardGap:
    """get_board_gap dispatches to the correct config key per layout type."""

    def _make_board(self, layout_type: str, card_gap: bool = False) -> Board:
        return make_test_board(layout=Layout(type=layout_type), card_gap=card_gap)

    def test_card_gap_always_zero(self) -> None:
        for layout_type in ("rows", "cols", "grid"):
            board = self._make_board(layout_type, card_gap=True)
            assert get_board_gap(board) == 0.0, (
                f"card_gap=True should be 0 for {layout_type}"
            )

    def test_rows_returns_config_gap(self) -> None:
        board = self._make_board("rows")
        assert get_board_gap(board) == float(get_theme_style().layout.rows.gap)

    def test_cols_returns_config_gap(self) -> None:
        board = self._make_board("cols")
        assert get_board_gap(board) == float(get_theme_style().layout.cols.gap)

    def test_grid_returns_config_gap(self) -> None:
        board = self._make_board("grid")
        assert get_board_gap(board) == float(get_theme_style().layout.grid.gap)

    def test_tabs_returns_zero(self) -> None:
        board = self._make_board("tabs")
        assert get_board_gap(board) == 0.0

    def test_rows_gap_reads_board_resolved_style_not_global_theme(self) -> None:
        """A board-resolved style.layout.rows.gap override must not be dropped.

        Regression for the audit finding: get_board_gap took `board` but ignored
        its resolved_style, always reading the global default theme.
        """
        distinctive_gap = 12345.0
        board = _with_gap_override("rows", distinctive_gap)
        assert get_board_gap(board) == distinctive_gap
        assert get_board_gap(board) != float(get_theme_style().layout.rows.gap)

    def test_cols_gap_reads_board_resolved_style_not_global_theme(self) -> None:
        distinctive_gap = 12345.0
        board = _with_gap_override("cols", distinctive_gap)
        assert get_board_gap(board) == distinctive_gap
        assert get_board_gap(board) != float(get_theme_style().layout.cols.gap)

    def test_grid_gap_reads_board_resolved_style_not_global_theme(self) -> None:
        distinctive_gap = 12345.0
        board = _with_gap_override("grid", distinctive_gap)
        assert get_board_gap(board) == distinctive_gap
        assert get_board_gap(board) != float(get_theme_style().layout.grid.gap)


class TestParseDimension:
    """parse_dimension parses percentage/pixel dimension strings to floats."""

    def test_percent_is_fraction_of_total(self) -> None:
        assert parse_dimension("30%", 200.0) == 60.0

    def test_px_suffix_returns_exact_pixels(self) -> None:
        assert parse_dimension("150px", 200.0) == 150.0

    def test_bare_number_returns_pixels(self) -> None:
        assert parse_dimension("150", 200.0) == 150.0

    def test_none_returns_none(self) -> None:
        assert parse_dimension(None, 200.0) is None

    def test_unparsable_returns_none(self) -> None:
        assert parse_dimension("not-a-dimension", 200.0) is None


_LONE_CHART = """
queries:
  q:
    type: values
    rows:
      - {{month: a, n: 1}}
      - {{month: b, n: 2}}
charts:
  c:
    type: {chart_type}
    query: q
    title: "{title}"
{encoding}{chart_extra}{board_extra}rows:
  - c
"""
_ENCODINGS = {
    "bar": "    x: month\n    y: n\n",
    "line": "    x: month\n    y: n\n",
    "donut": "    theta: n\n    color: month\n",
}
_ENGINE_TITLE = "A chart title of ordinary length that fits a normal dashboard card"
_SHORT_TITLE = "Revenue"
_LONG_TITLE = (
    "A chart title of ordinary length that fits a normal dashboard card, "
    "followed by a second clause that runs it well past one card"
)


def _compile_lone_chart(
    title: str, chart_extra: str = "", board_extra: str = "", chart_type: str = "bar"
) -> Board:
    result = compile(
        _LONE_CHART.format(
            title=title,
            chart_type=chart_type,
            encoding=_ENCODINGS[chart_type],
            chart_extra=chart_extra,
            board_extra=board_extra,
        )
    )
    assert result.success and result.board is not None, result.errors
    return result.board


def _lone_chart_width(
    title: str, chart_extra: str = "", board_extra: str = "", chart_type: str = "bar"
) -> float:
    board = _compile_lone_chart(title, chart_extra, board_extra, chart_type)
    return board_container_width(board, board.variable_defaults)


class TestChartTitleWidensHug:
    """A board hugging a lone chart grows to fit that chart's one-line title."""

    @pytest.mark.parametrize("chart_type", ["bar", "line", "donut"])
    def test_long_title_widens_the_hug(self, chart_type: str) -> None:
        assert _lone_chart_width(
            _LONG_TITLE, chart_type=chart_type
        ) > _lone_chart_width(_SHORT_TITLE, chart_type=chart_type)

    def test_growth_stops_at_max_width(self) -> None:
        max_width = float(get_theme_style().frame.max_width)
        assert _lone_chart_width(_LONG_TITLE * 4) == max_width

    @pytest.mark.parametrize(
        "pin",
        ["    width: 400\n", "    style:\n      preferred_width: 400\n"],
        ids=["width", "preferred_width"],
    )
    def test_authored_chart_width_wins(self, pin: str) -> None:
        assert _lone_chart_width(_LONG_TITLE, pin) == _lone_chart_width(
            _SHORT_TITLE, pin
        )

    def test_wrapping_title_does_not_widen(self) -> None:
        wrap = "style:\n  charts:\n    title:\n      overflow: wrap\n"
        assert _lone_chart_width(_LONG_TITLE, board_extra=wrap) == _lone_chart_width(
            _SHORT_TITLE, board_extra=wrap
        )

    def test_templated_title_does_not_widen(self) -> None:
        assert _lone_chart_width(_LONG_TITLE + " {{ 'x' }}") == _lone_chart_width(
            _SHORT_TITLE
        )

    def test_hug_fits_the_title_at_the_tier_it_lands_in(self) -> None:
        # Medium titles draw smaller than wide ones, so growth past the wide
        # breakpoint enlarges the title it was sized for.
        style = (
            "style:\n  frame:\n    max_width: 4000\n"
            "  title:\n    width_offsets:\n      medium: 2\n      wide: 0\n"
        )
        board = _compile_lone_chart(_LONG_TITLE * 3, board_extra=style)
        chart = board.layout.items[0].chart
        assert chart is not None
        ctx = board.chart_style_context
        width = config_chart_width(chart, board.resolved_style, ctx).preferred
        assert width is not None
        assert width >= _one_line_title_width(chart, ctx, width)

    def test_measures_the_size_the_title_is_painted_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        measured: list[float] = []
        real = sizing.get_font_measurer

        def record(family: str) -> types.SimpleNamespace:
            measurer = real(family)
            inner = measurer.measure

            def measure(text: str, size: float) -> float:
                measured.append(size)
                return inner(text, size)

            return types.SimpleNamespace(measure=measure)

        monkeypatch.setattr(sizing, "get_font_measurer", record)
        board = _compile_lone_chart(_LONG_TITLE)
        chart = board.layout.items[0].chart
        assert chart is not None
        ctx = board.chart_style_context
        painted = resolve_title_font(build_chart_style_context(ctx, chart), 200.0).size
        _one_line_title_width(chart, ctx, 200.0)
        assert painted != 18
        assert measured == [float(painted)]

    @pytest.mark.parametrize(
        "board_extra",
        ["", "style:\n  charts:\n    title:\n      font:\n        size: 24\n"],
        ids=["ramp", "explicit_24"],
    )
    def test_a_title_the_hug_fits_is_not_cut_by_the_engine(
        self, board_extra: str
    ) -> None:
        # Both sides of the boundary must read one painted size: the narrowest
        # card Python says holds the title on one line must not truncate it.
        board = _compile_lone_chart(_ENGINE_TITLE, board_extra=board_extra)
        chart = board.layout.items[0].chart
        assert chart is not None
        ctx = board.chart_style_context
        fit = next(
            w for w in range(200, 1200) if _one_line_title_width(chart, ctx, w) <= w
        )

        def codes(width: int) -> list[str]:
            text = _LONE_CHART.format(
                title=_ENGINE_TITLE,
                chart_type="bar",
                encoding=_ENCODINGS["bar"],
                chart_extra=f"    width: {width}\n",
                board_extra=board_extra,
            )
            result = render_board_svg_result(text)
            assert not result.chart_errors
            return [w.code for w in result.warnings]

        assert "WARN-CHART-TITLE-TRUNCATED" not in codes(fit)
        assert "WARN-CHART-TITLE-TRUNCATED" in codes(fit // 2)
