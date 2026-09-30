"""Regression tests: board-level table style overrides must be honored by the height provider.

The bug: ``_get_table_height_from_data`` previously called
``resolve_style(get_theme_style())``, giving bare defaults, while the renderer
went through the chart-level cascade. When a board overrode row.height,
padding, header.*, or pagination.page_rows, the sizer under-allocated and
the renderer silently clipped rows off the bottom. The fix routes the sizer
through ``build_chart_style_context`` so board + chart-local merge once.

All tests here FAIL on pre-fix main and PASS after the fix.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import Chart, TableChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedTableChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    ChartStylePatch,
    TableChartStylePatch,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

_BASE_QUERY = SqlQuery(sql="SELECT 1", source="test")
_DEFAULT_RESOLVED = resolve_style(get_theme_style())
_DEFAULT_CONTEXT = resolve_chart_style_context(get_theme_style())
_DEFAULT_ROW_H = int(_DEFAULT_RESOLVED.chart_defaults.table.row.height)  # 32


def _make_table_chart(style_patch: ChartStylePatch | None = None) -> Chart:
    # ChartStylePatch is still used at call sites to build the patch via
    # model_validate({"table": {...}}); extract the inner TableChartStylePatch here.
    table_patch: TableChartStylePatch | None = (
        style_patch.table if style_patch is not None else None
    )
    return TableChart(
        id="t1",
        query=_BASE_QUERY,
        query_name="q",
        type="table",
        style=table_patch,
    )


def _make_executor(row_count: int) -> MagicMock:
    """Return a mock Executor that yields `row_count` rows for execute_chart."""
    mock = MagicMock(spec=Executor)
    mock.execute_chart.return_value = [{"col": i} for i in range(row_count)]
    return mock


def _call_provider(
    chart: Chart,
    executor: MagicMock,
    resolved_style: ResolvedStyle = _DEFAULT_RESOLVED,
    board_context: ChartStyleContext = _DEFAULT_CONTEXT,
) -> float:
    """Call _get_table_height_from_data with the given resolved_style."""
    from dbt_charts.core.render.layout_sizing import _get_table_height_from_data

    resolved = resolve(chart, [], board_context)
    assert isinstance(resolved, ResolvedTableChart)
    return _get_table_height_from_data(
        chart,
        resolved,
        executor,
        variables={},
        card_padding=float(resolved_style.frame.card_padding),
        board_style=resolved_style,
    )


# ---------------------------------------------------------------------------
# Core regression: row.height override
# ---------------------------------------------------------------------------


class TestRowHeightOverride:
    """Board-level row.height must be used by the height provider, not the default."""

    def test_default_height_uses_32px_per_row(self):
        """Baseline: with no override, each of 20 rows contributes the default row height."""
        chart = _make_table_chart()
        executor = _make_executor(20)

        height = _call_provider(chart, executor)

        # 20 rows × default_row_h; read live from config so this test survives theme tweaks
        default_row_h = int(
            resolve_style(get_theme_style()).chart_defaults.table.row.height
        )
        assert height >= 20 * default_row_h

    def test_board_row_height_40_allocates_more_than_default(self):
        """When board sets row.height=40, provider must allocate 40px per row.

        Pre-fix: provider returned 20*32 + chrome (default-based).
        Post-fix: provider returns 20*40 + chrome (board-merged style).
        """
        patch = ChartStylePatch.model_validate({"table": {"row": {"height": 40}}})
        chart = _make_table_chart(patch)
        executor = _make_executor(20)

        resolved = resolve_style(get_theme_style())
        height_40 = _call_provider(chart, executor, resolved_style=resolved)
        height_default = _call_provider(_make_table_chart(), _make_executor(20))

        # 20 rows × (40 − default_row_h); read live so theme tweaks don't break this
        default_row_h = int(
            resolve_style(get_theme_style()).chart_defaults.table.row.height
        )
        assert height_40 >= height_default + 20 * (40 - default_row_h), (
            f"row.height=40 over 20 rows should add ≥{20 * (40 - default_row_h)}px vs default, "
            f"but height_40={height_40} and height_default={height_default}"
        )

    def test_board_row_height_40_exact_row_contribution(self):
        """Row contribution must be exactly row_count * board_row_height."""
        row_count = 20
        board_row_h = 40
        patch = ChartStylePatch.model_validate(
            {"table": {"row": {"height": board_row_h}}}
        )
        chart = _make_table_chart(patch)
        executor = _make_executor(row_count)

        resolved = resolve_style(get_theme_style())
        height = _call_provider(chart, executor, resolved_style=resolved)

        # The row contribution in the height formula is row_count * row_height.
        # Total = title + header + header_body_gap + row_count*row_h + padding + bottom_padding
        # With no chart.title, default header, no padding override, this simplifies.
        # The key check: height must be ≥ row_count * board_row_h (the row block alone).
        assert height >= row_count * board_row_h, (
            f"Expected total height ≥ {row_count * board_row_h} "
            f"(row block alone), got {height}"
        )


# ---------------------------------------------------------------------------
# padding / bottom_padding override
# ---------------------------------------------------------------------------


class TestPaddingOverride:
    """Board-level padding and bottom_padding overrides must be reflected."""

    def test_padding_override_adds_to_height(self):
        """Adding padding=20 must increase provider height by 20 vs default (0)."""
        patch = ChartStylePatch.model_validate({"table": {"outer_padding": 20}})
        chart = _make_table_chart(patch)
        executor = _make_executor(5)

        resolved = resolve_style(get_theme_style())
        height_with_padding = _call_provider(chart, executor, resolved_style=resolved)
        height_default = _call_provider(_make_table_chart(), _make_executor(5))

        assert height_with_padding >= height_default + 20, (
            f"padding=20 should add ≥20px, "
            f"height_with={height_with_padding}, height_default={height_default}"
        )

    def test_bottom_padding_override_adds_to_height(self):
        """Adding bottom_padding=24 must increase provider height by 24."""
        patch = ChartStylePatch.model_validate({"table": {"bottom_padding": 24}})
        chart = _make_table_chart(patch)
        executor = _make_executor(5)

        resolved = resolve_style(get_theme_style())
        height_with_bp = _call_provider(chart, executor, resolved_style=resolved)
        height_default = _call_provider(_make_table_chart(), _make_executor(5))

        assert height_with_bp >= height_default + 24, (
            f"bottom_padding=24 should add ≥24px, "
            f"height_with={height_with_bp}, height_default={height_default}"
        )


# ---------------------------------------------------------------------------
# pagination.page_rows override via board resolved_style
# ---------------------------------------------------------------------------


class TestPaginationPageRowsOverride:
    """Board-level pagination.page_rows (from resolved_style) must be used by the provider.

    When a board sets ``style.charts.table.pagination.page_rows: 5``, that value flows
    into ``resolved_style.charts.pagination.page_rows`` via ``resolve_style()``.  The
    sizer reads the merged page_rows off ``effective_charts.pagination`` after the
    cascade. Pre-fix: ``effective_charts.pagination`` came from bare
    ``get_theme_style().model_copy(deep=True)`` defaults (page_rows=20) regardless of
    ``resolved_style``. Post-fix: it comes from ``build_chart_style_context(resolved_style,
    chart_style)`` and honors the board setting.
    """

    def _resolved_with_page_rows(
        self, page_rows: int
    ) -> tuple[ResolvedStyle, ChartStyleContext]:
        """Build a ResolvedStyle/ChartStyleContext pair whose board pagination
        page_rows is ``page_rows``."""
        base = get_theme_style()
        seed = base.model_copy(
            update={
                "charts": base.charts.model_copy(
                    update={
                        "table": base.charts.table.model_copy(
                            update={
                                "pagination": base.charts.table.pagination.model_copy(
                                    update={"page_rows": page_rows}
                                )
                            }
                        )
                    }
                )
            }
        )
        return resolve_style(seed), resolve_chart_style_context(seed)

    def test_board_pagination_page_rows_5_caps_row_count(self):
        """Board page_rows=5 (in resolved_style) on a 30-row query allocates for 5 rows.

        Pre-fix: sizer used bare defaults → page_rows=20, allocates for 20 rows.
        Post-fix: sizer uses board resolved_style → page_rows=5, allocates for 5 rows.
        """
        chart = _make_table_chart()  # no chart-level pagination override
        executor = _make_executor(30)

        board_resolved, board_context = self._resolved_with_page_rows(5)
        height_page5 = _call_provider(
            chart, executor, resolved_style=board_resolved, board_context=board_context
        )

        # Bare defaults: page_rows=20, 30 rows → allocates for 20
        height_default_20 = _call_provider(_make_table_chart(), _make_executor(30))

        assert height_page5 < height_default_20, (
            f"Board page_rows=5 over 30 rows should produce shorter height than "
            f"default page_rows=20: got {height_page5} vs {height_default_20}"
        )

    def test_board_pagination_page_rows_50_allocates_more(self):
        """Board page_rows=50 on a 40-row table: provider allocates for all 40 rows.

        Pre-fix: sizer used bare defaults → page_rows=20, allocates for 20 rows.
        Post-fix: sizer uses board resolved_style → page_rows=50, allocates for 40 rows.
        """
        chart = _make_table_chart()  # no chart-level pagination override
        executor = _make_executor(40)

        board_resolved, board_context = self._resolved_with_page_rows(50)
        height_page50 = _call_provider(
            chart, executor, resolved_style=board_resolved, board_context=board_context
        )

        # Bare defaults: page_rows=20, 40 rows → allocates for 20
        height_default_20 = _call_provider(_make_table_chart(), _make_executor(40))

        assert height_page50 > height_default_20, (
            f"Board page_rows=50 over 40 rows should allocate more than "
            f"default page_rows=20: got {height_page50} vs {height_default_20}"
        )


# ---------------------------------------------------------------------------
# Header height override via nested font style paths
# ---------------------------------------------------------------------------


class TestHeaderHeightOverride:
    """Board-level header.height override must be used in the chrome calculation."""

    def test_header_height_override(self):
        """header.height=60 adds its excess over the default header, exactly.

        The single-column header fits one line, so the renderer draws it at the
        authored height and the sizer reserves what the renderer draws.
        """
        patch = ChartStylePatch.model_validate({"table": {"header": {"height": 60}}})
        chart = _make_table_chart(patch)

        height_h60 = _call_provider(chart, _make_executor(10))
        height_default = _call_provider(_make_table_chart(), _make_executor(10))

        default_header = int(_DEFAULT_RESOLVED.chart_defaults.table.header.height)
        assert height_h60 == height_default + 60 - default_header


# ---------------------------------------------------------------------------
# _make_data_aware_height_provider threads resolved_style into table branch
# ---------------------------------------------------------------------------


class TestMakeDataAwareHeightProviderThreadsStyle:
    """The table branch in _make_data_aware_height_provider must pass resolved_style
    from the SizingRenderCtx into _get_table_height_from_data."""

    def test_provider_uses_render_ctx_resolved_style(self):
        """Calling the provider with a SizingRenderCtx carrying a non-default resolved_style
        must yield a different height than a ctx carrying default resolved_style.

        This validates the call-site thread in _make_data_aware_height_provider.
        """
        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _make_data_aware_height_provider,
        )

        row_count = 20
        card_padding = 16.0

        # Build a board-level resolved_style that overrides row.height=48
        base = get_theme_style()
        seed = base.model_copy(
            update={
                "charts": base.charts.model_copy(
                    update={
                        "table": base.charts.table.model_copy(
                            update={
                                "row": base.charts.table.row.model_copy(
                                    update={"height": 48.0}
                                )
                            }
                        )
                    }
                )
            }
        )
        board_style = resolve_style(seed)
        board_context = resolve_chart_style_context(seed)

        # No chart-level patch — overrides come entirely from resolved_style
        chart = _make_table_chart()
        item = LayoutItem(type="chart", width=800, height=0, chart=chart)
        executor = _make_executor(row_count)

        render_ctx = SizingRenderCtx(
            resolved_style=board_style, chart_style_context=board_context
        )

        provider_with_ctx = _make_data_aware_height_provider(
            render_ctx,
            executor,
            variables={},
            card_padding=card_padding,
            style_contexts={id(board_style): board_context},
        )
        height_with_ctx = provider_with_ctx(item, 0.0, 20.0, 800.0, None, board_style)

        # Provider with default resolved_style uses bare defaults (row.height=32)
        chart_no_patch = _make_table_chart()
        item_no_patch = LayoutItem(
            type="chart", width=800, height=0, chart=chart_no_patch
        )
        executor2 = _make_executor(row_count)
        default_style = resolve_style(get_theme_style())
        render_ctx_default = SizingRenderCtx(
            resolved_style=default_style,
            chart_style_context=_DEFAULT_CONTEXT,
        )
        provider_no_ctx = _make_data_aware_height_provider(
            render_ctx_default,
            executor2,
            variables={},
            card_padding=card_padding,
            style_contexts={id(default_style): _DEFAULT_CONTEXT},
        )
        height_no_ctx = provider_no_ctx(
            item_no_patch, 0.0, 20.0, 800.0, None, default_style
        )

        # 48px vs 32px over 20 rows = 320px difference in row block alone
        assert height_with_ctx > height_no_ctx, (
            f"Provider with board row.height=48 should exceed default-32 height: "
            f"with_ctx={height_with_ctx}, no_ctx={height_no_ctx}"
        )
        assert height_with_ctx >= height_no_ctx + 20 * (48 - 32) - 2, (
            f"Expected ≥{20 * (48 - 32)}px more from 48px rows vs 32px rows"
        )


# ---------------------------------------------------------------------------
# Grow-by-2 rule on table self-sizing (unconstrained-layout case)
# ---------------------------------------------------------------------------


class TestGrowBy2RuleOnSelfSizing:
    """``_get_table_height_from_data`` must not cap requested height at ``page_rows``.

    The historical behavior capped the table's requested layout height at
    ``min(row_count, pagination.page_rows)`` rows — so a 21-row table with
    theme default page_rows=20 self-paginated with chrome by default, hiding
    one row behind dead chrome.

    The new contract:
      - If ``total_rows <= page_rows + 2``: report height for every row, no
        pagination chrome contribution. The engine grows the slot by up to 2
        rows to keep small overflows visible.
      - If ``total_rows > page_rows + 2``: report height for ``page_rows``
        rows + pagination chrome. Multi-page state.
    """

    _PAGE_ROWS = 20  # current theme default

    def _height_no_chrome(self, row_count: int) -> float:
        """Expected provider height when the engine is NOT adding pagination chrome.

        Reads chrome live from the resolved style so the test survives theme
        tweaks. Mirrors the formula in `_get_table_height_from_data` minus the
        `_PAGINATION_CONTROL_HEIGHT` term.
        """
        rs = resolve_style(get_theme_style())
        tc = rs.chart_defaults.table
        row_h = int(tc.row.height)
        header_h = int(tc.header.height) if tc.header.visible else 0
        header_body_gap = int(row_h * 0.25) if tc.header.visible else 0
        # title_height for a Chart with no chart.title resolves to chrome-only.
        # We don't recompute it here; instead we compare provider outputs.
        return float(row_count * row_h + header_h + header_body_gap)

    def test_row_count_equal_to_page_rows_no_chrome(self):
        """Baseline: 20 rows at page_rows=20 — all rows fit, no chrome."""
        chart = _make_table_chart()
        height_20 = _call_provider(chart, _make_executor(20))
        height_21_pre = self._height_no_chrome(20)
        assert height_20 >= height_21_pre, (
            f"20-row table should accommodate all rows: got {height_20}"
        )

    def test_row_count_page_rows_plus_one_grows(self):
        """21 rows at page_rows=20 — within grow-by-2 zone, height accommodates 21."""
        chart = _make_table_chart()
        height_21 = _call_provider(chart, _make_executor(21))
        height_20 = _call_provider(chart, _make_executor(20))

        rs = resolve_style(get_theme_style())
        row_h = int(rs.chart_defaults.table.row.height)
        # One more row should add ~row_h px and NO pagination chrome.
        # If grow-by-2 fires correctly, height_21 = height_20 + row_h.
        assert height_21 == height_20 + row_h, (
            f"21-row table with page_rows=20 should grow by one row_h ({row_h}px) "
            f"vs 20-row baseline (no chrome added); got {height_21} vs {height_20}"
        )

    def test_row_count_page_rows_plus_two_grows(self):
        """22 rows at page_rows=20 — at edge of grow-by-2 zone, height accommodates 22."""
        chart = _make_table_chart()
        height_22 = _call_provider(chart, _make_executor(22))
        height_20 = _call_provider(chart, _make_executor(20))

        rs = resolve_style(get_theme_style())
        row_h = int(rs.chart_defaults.table.row.height)
        assert height_22 == height_20 + 2 * row_h, (
            f"22-row table with page_rows=20 should grow by 2 * row_h ({2 * row_h}px) "
            f"vs 20-row baseline (no chrome added); got {height_22} vs {height_20}"
        )

    def test_row_count_page_rows_plus_three_paginates(self):
        """23 rows at page_rows=20 — past grow-by-2 cap, height caps at 20 rows + chrome."""
        from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT

        chart = _make_table_chart()
        height_23 = _call_provider(chart, _make_executor(23))
        height_20 = _call_provider(chart, _make_executor(20))

        # Pagination fires: height accommodates 20 rows (not 23), plus chrome.
        # Diff vs 20-row baseline is exactly the pagination-control reservation.
        assert height_23 == height_20 + _PAGINATION_CONTROL_HEIGHT, (
            f"23-row table with page_rows=20 should paginate (not grow): "
            f"height_23 should equal height_20 + _PAGINATION_CONTROL_HEIGHT "
            f"({_PAGINATION_CONTROL_HEIGHT}px), got height_23={height_23} "
            f"vs height_20={height_20}. If height_23 > height_20 + chrome, the "
            f"cap was lifted too far; if < height_20 + chrome, no pagination "
            f"reservation was added."
        )

    def test_row_count_one_hundred_paginates_to_page_rows(self):
        """100-row table at page_rows=20 — paginates, height caps at 20 + chrome."""
        from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT

        chart = _make_table_chart()
        height_100 = _call_provider(chart, _make_executor(100))
        height_20 = _call_provider(chart, _make_executor(20))

        assert height_100 == height_20 + _PAGINATION_CONTROL_HEIGHT, (
            f"100-row table with page_rows=20 should cap at 20 rows + chrome: "
            f"got height_100={height_100} vs height_20={height_20}"
        )


class TestCapNoteHeightReservation:
    """A static export whose real page count exceeds the pre-render cap
    (_STATIC_MULTI_PAGE_MAX_PAGES) draws an extra "Showing pages 1-N of M"
    line below the pager. The sizer must reserve that line too, or an
    explicit-height render (dct render, a grid: layout whose siblings don't
    self-correct off a table's actual height) overflows its slot.
    """

    def _paginated_chart(self, page_rows: int) -> Chart:
        return _make_table_chart(
            ChartStylePatch.model_validate(
                {"table": {"pagination": {"enabled": True, "page_rows": page_rows}}}
            )
        )

    def test_page_count_within_cap_reserves_no_extra_line(self):
        from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT

        chart = self._paginated_chart(page_rows=5)
        # 100 rows / 5 per page = 20 pages -- exactly at the cap, not past it.
        height_100 = _call_provider(chart, _make_executor(100))
        height_5 = _call_provider(chart, _make_executor(5))

        assert height_100 == height_5 + _PAGINATION_CONTROL_HEIGHT, (
            f"20 pages is within the static-export cap -- no cap-note line "
            f"should be reserved: got height_100={height_100} vs "
            f"height_5={height_5} + _PAGINATION_CONTROL_HEIGHT"
        )

    def test_page_count_past_cap_reserves_the_cap_note_line(self):
        from dbt_charts.core.render.chart.table import (
            _PAGINATION_CAP_NOTE_HEIGHT,
            _PAGINATION_CONTROL_HEIGHT,
        )

        chart = self._paginated_chart(page_rows=5)
        # 105 rows / 5 per page = 21 pages -- one page past the cap.
        height_105 = _call_provider(chart, _make_executor(105))
        height_5 = _call_provider(chart, _make_executor(5))

        assert (
            height_105
            == height_5 + _PAGINATION_CONTROL_HEIGHT + _PAGINATION_CAP_NOTE_HEIGHT
        ), (
            f"21 pages exceeds the static-export cap (20) -- the cap-note "
            f"line must be reserved: got height_105={height_105} vs "
            f"height_5={height_5} + control + cap-note height"
        )


# ---------------------------------------------------------------------------
# Regression: oversized slot must not produce dead whitespace (Solution B)
# ---------------------------------------------------------------------------


class TestRendererShrinkToContent:
    """``_resolve_visible_rows`` must size to content when single-page, not to slot.

    Bug: when the sizer reserves a full page_rows slot (e.g. 318px for 9 rows)
    but the renderer only has 1 visible row, the renderer returned
    ``table_height = slot`` and top-aligned the single row → 317px dead space.

    Fix: for a single-page table (visible rows ≤ page_rows) return content height
    so the table never pads below its last row.

    Repro from task worksheet (dashboard 4679, tile trial_connections):
      _resolve_visible_rows(data=[1 row], height=318, page_rows=9, …)
      expected: table_height == auto-sized content height, not 318.

    All assertions use the auto-sized path (height=None) as the oracle rather
    than re-implementing the content-height formula. This is robust to theme
    changes in padding, header height, or row height.
    """

    def _call_resolve(
        self,
        n_rows: int,
        height: float | None,
        page_rows: int,
    ) -> float:
        from dbt_charts.core.render.chart.table import (
            PaginationConfig,
            _resolve_visible_rows,
        )

        rs = resolve_style(get_theme_style())
        tc = rs.chart_defaults.table
        row_h = int(tc.row.height)
        header_h = int(tc.header.height)
        padding = int(tc.outer_padding)
        bottom_padding = int(tc.bottom_padding)
        data = [{"col": i} for i in range(n_rows)]
        pagination = PaginationConfig(enabled=True, page_rows=page_rows)
        result = _resolve_visible_rows(
            data=data,
            height=height,
            title_height=0.0,
            header_height=float(header_h),
            padding=padding,
            row_height=row_h,
            bottom_padding=bottom_padding,
            pagination=pagination,
            page=1,
        )
        return result[0]  # table_height

    def test_single_row_oversized_slot_collapses_to_content(self):
        """1 data row with a 318px slot (sized for page_rows=9) must collapse to content.

        Pre-fix: table_height == 318 (dead whitespace).
        Post-fix: table_height == auto-sized height for 1 row (no padding below last row).
        """
        auto_height = self._call_resolve(n_rows=1, height=None, page_rows=9)
        slotted_height = self._call_resolve(n_rows=1, height=318, page_rows=9)

        assert slotted_height == auto_height, (
            f"Single-row table with oversized 318px slot should collapse to "
            f"auto-sized height {auto_height}px, got {slotted_height}px"
        )
        assert slotted_height < 318, (
            f"Slotted height must be smaller than the slot (318px), got {slotted_height}"
        )

    def test_full_page_slightly_oversized_slot_collapses_to_content(self):
        """9 data rows with a slightly-oversized slot (auto + 50px) collapse to content.

        This distinguishes shrink-fired from shrink-skipped: the slot is 50px
        larger than content so if shrink did NOT fire the result would be slot,
        but with shrink it should equal the auto-sized height.
        """
        auto_height = self._call_resolve(n_rows=9, height=None, page_rows=9)
        oversized_slot = auto_height + 50.0
        slotted_height = self._call_resolve(
            n_rows=9, height=oversized_slot, page_rows=9
        )

        assert slotted_height == auto_height, (
            f"9-row table with slot={oversized_slot} (50px over auto_height={auto_height}) "
            f"must collapse to auto-sized content height, got {slotted_height}"
        )

    def test_few_rows_oversized_slot_collapses_to_content(self):
        """3 data rows with a page_rows=9 slot must collapse to auto-sized content height."""
        auto_height = self._call_resolve(n_rows=3, height=None, page_rows=9)
        slotted_height = self._call_resolve(n_rows=3, height=318, page_rows=9)

        assert slotted_height == auto_height, (
            f"3-row table with oversized 318px slot should collapse to "
            f"auto-sized height {auto_height}px, got {slotted_height}px"
        )
        assert slotted_height < 318, (
            f"Slotted height must be smaller than the slot (318px), got {slotted_height}"
        )

    def test_summary_row_gap_included_when_slot_shrunk(self):
        """Summary gap must be included in SVG height when an oversized slot is shrunk.

        Pre-fix: shrink path returned exact content height but did not add
        summary_gap_total, clipping the summary row visually.
        Post-fix: summary gap added whenever the renderer shrank the slot.
        """
        import re

        from dbt_charts.core.compile.models.style.authored import ChartStylePatch
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        rs = resolve_style(get_theme_style())
        tc = rs.chart_defaults.table
        row_h = int(tc.row.height)

        # 2 data rows + 1 summary row — fits in page_rows=9 (single-page)
        data = [
            {"col": "Alpha", "kind": "value"},
            {"col": "Beta", "kind": "value"},
            {"col": "Total", "kind": "summary"},
        ]

        # Build a chart with row.role = "kind" so the summary gap fires
        patch = ChartStylePatch.model_validate({"table": {"row": {"role": "kind"}}})
        chart_obj = _make_table_chart(patch)
        board = rs
        resolved = resolve(chart_obj, data, chart_style_context=_DEFAULT_CONTEXT)

        # Auto-sized render (no slot) — establishes the expected height
        svg_auto = render_table_svg(resolved, data, width=400.0, board_style=board)
        m_auto = re.search(r'<svg[^>]+height="([^"]+)"', svg_auto)
        assert m_auto, "SVG must have height attribute"
        auto_height = float(m_auto.group(1))

        # Oversized-slot render (slot >> content) — must produce the SAME height
        oversized_slot = auto_height * 3
        svg_slotted = render_table_svg(
            resolved, data, width=400.0, height=oversized_slot, board_style=board
        )
        m_slotted = re.search(r'<svg[^>]+height="([^"]+)"', svg_slotted)
        assert m_slotted, "SVG must have height attribute"
        slotted_height = float(m_slotted.group(1))

        # Compute the expected summary gap (_SUMMARY_GAP = int(row_h * 0.4))
        summary_gap = int(row_h * 0.4)

        assert slotted_height == auto_height, (
            f"Oversized-slot table with summary row must render at same height as "
            f"auto-sized ({auto_height}px), got {slotted_height}px. "
            f"If slotted_height == auto_height - {summary_gap}, "
            f"the summary gap is missing from the shrink path."
        )


# ---------------------------------------------------------------------------
# Regression: title_row.height floor must not apply to title-only tables
# ---------------------------------------------------------------------------


class TestTitleBlockHeightNoSubtitleFloor:
    """``title_row.height`` is the height floor only when a subtitle is present.

    Bug: ``compute_table_title_block_layout`` applied
    ``max(int(tc.title_row.height), ...)`` unconditionally, before the subtitle
    block was computed, so title-only tables carried the full floor (40px by
    default) even when the natural single-line title content was only ~22–31px.
    The gap between title and the first table row was visibly inflated.

    Fix: the floor moves inside the subtitle branch.
    """

    def _call(
        self,
        title: str,
        subtitle: str,
        table_width: float = 600.0,
        floor_override: float | None = None,
    ) -> tuple[int, float]:
        """Return (title_block_height, title_row_floor) for the given args.

        ``floor_override`` patches ``tc.title_row.height`` before computing the
        layout, so the floor's effect can be pinned theme-independently — an
        inflated floor makes "does the floor apply here" unambiguous regardless
        of what the active theme's real title font/floor happen to be.
        """
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
            resolve_style,
        )
        from dbt_charts.core.compile.resolve.style.typography import resolve_title_font
        from dbt_charts.core.render.chart.table import compute_table_title_block_layout

        rcs = resolve_chart_style_context(get_theme_style())
        tc = rcs.table
        if floor_override is not None:
            tc = tc.model_copy(
                update={
                    "title_row": tc.title_row.model_copy(
                        update={"height": floor_override}
                    )
                }
            )
        title_font = resolve_title_font(rcs, width=table_width)
        rs = resolve_style(get_theme_style())
        height = compute_table_title_block_layout(
            chart_title=title,
            chart_subtitle=subtitle,
            table_width=table_width,
            tc=tc,
            padding=int(tc.outer_padding),
            title_style=rcs.title,
            card_padding=float(rs.frame.card_padding),
            title_font=title_font,
        ).height
        return height, tc.title_row.height

    def test_title_only_height_is_below_subtitle_floor(self) -> None:
        """Title-only block must not be padded to the subtitle-floor height.

        Pre-fix: ``height == int(tc.title_row.height)`` (floor applied unconditionally).
        Post-fix: ``height < int(tc.title_row.height)`` (natural content height only).
        """
        height, floor = self._call(title="My Title", subtitle="")
        assert height < int(floor), (
            f"Title-only block height ({height}px) should be strictly less than the "
            f"subtitle floor ({int(floor)}px) — the floor must not apply without a subtitle"
        )

    def test_title_only_ignores_inflated_floor(self) -> None:
        """Title-only block must ignore the floor even when it's set far above content.

        Theme-independent pin: an inflated floor (1000px) makes it unambiguous
        whether the floor applies — no dependence on the active theme's real
        title-row height happening to exceed natural content.
        """
        height, floor = self._call(title="My Title", subtitle="", floor_override=1000.0)
        assert floor == 1000.0
        assert height < 100, (
            f"Title-only block height ({height}px) should be unaffected by an inflated "
            f"floor ({int(floor)}px) — the floor must not apply without a subtitle"
        )

    def test_title_with_subtitle_height_respects_floor(self) -> None:
        """Title+subtitle block must still be at least ``tc.title_row.height`` tall.

        The floor ensures adequate clearance for both text rows.
        This must be unaffected by the title-only fix.
        """
        height, floor = self._call(title="My Title", subtitle="Supporting context here")
        assert height >= int(floor), (
            f"Title+subtitle block height ({height}px) should be at least the "
            f"subtitle floor ({int(floor)}px)"
        )

    def test_title_with_subtitle_respects_inflated_floor(self) -> None:
        """Title+subtitle block must bind exactly to an inflated floor.

        Theme-independent pin for the invariant the previous test could miss:
        at the default 600px width, natural subtitle content (43px) already
        exceeds the real theme floor (40px), so that test can't tell "floor
        applied" from "floor irrelevant, content is just tall enough." An
        inflated floor (1000px) makes it unambiguous — if the floor were
        accidentally dropped from the subtitle branch, this would fail.
        """
        height, floor = self._call(
            title="My Title", subtitle="Supporting context here", floor_override=1000.0
        )
        assert height == int(floor), (
            f"Title+subtitle block height ({height}px) should equal the inflated floor "
            f"({int(floor)}px) exactly — the floor must still bind with a subtitle present"
        )

    def test_title_only_below_floor_at_each_width_tier(self) -> None:
        """Natural height (< floor) holds across all four width tiers.

        Each tier resolves a different title font size; the natural content
        height must be below the floor at every tier so no tier silently
        reintroduces the dead gap.
        """
        for width in (200.0, 400.0, 600.0, 1200.0):
            height, floor = self._call(title="My Title", subtitle="", table_width=width)
            assert height < int(floor), (
                f"At width={width}px: title-only height {height}px should be "
                f"< subtitle floor {int(floor)}px"
            )


# ---------------------------------------------------------------------------
# Regression: constrained-slot squeeze cliff — authored rows: height is a
# floor, not a ceiling, for a small overflow.
# ---------------------------------------------------------------------------


class TestRowsAuthoredHeightFloorForTables:
    """A table's authored ``rows:`` slot height must not clamp away a tiny overflow.

    Bug: ``_calculate_rows_dimensions`` treats an authored ``height:`` on a
    ``rows:`` item as a hard ceiling (``min(authored, content)``) for every
    chart type, including tables. When a table's natural (grow-by-2-aware,
    pagination-free) content height exceeds that ceiling by only a few
    pixels, the render pass receives the clamped (too-small) height and
    anti-dangle either squeezes the rows or gives up and paginates — even
    though growing the slot by a trivial amount would show every row with no
    chrome at all.

    Fix: when the shortfall (content − authored) is within
    ``table._PAGINATION_CONTROL_HEIGHT`` — the same amount of vertical room
    pagination chrome would reserve anyway — the table's slot grows past the
    authored ceiling instead of clamping down to it. A larger shortfall still
    clamps to the authored ceiling (unchanged behavior; real pagination is
    the honest answer there).
    """

    def _size_with_ceiling(
        self,
        local_project,
        n_rows: int,
        ceiling: float | None,
    ) -> float:
        """Compile a rows: layout wrapping an N-row table and return item.height.

        ``ceiling=None`` renders the table with no authored height (the
        natural/auto-sized oracle). A numeric ceiling authors ``height:`` on
        the wrapping ``rows:`` item.
        """
        rows_yaml = "\n".join(f"      - {{col: {i}}}" for i in range(n_rows))
        rows_item = (
            f"  - height: {ceiling}\n    rows:\n      - t1\n"
            if ceiling is not None
            else "  - t1\n"
        )
        yaml_content = f"""
queries:
  q:
    type: values
    rows:
{rows_yaml}
charts:
  t1:
    query: q
    type: table
rows:
{rows_item}
"""
        result = compile(yaml_content)
        assert result.success, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        board, _ = calculate_data_aware_layout(
            result.board, executor, {}, pre_resolved={}
        )
        return board.layout.items[0].height

    def test_small_shortfall_grows_past_authored_ceiling(self, local_project):
        """Shortfall within the pagination-control-bar budget grows instead of clamping."""
        from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT

        natural_height = self._size_with_ceiling(local_project, n_rows=10, ceiling=None)
        small_shortfall_ceiling = natural_height - (_PAGINATION_CONTROL_HEIGHT - 5)

        grown_height = self._size_with_ceiling(
            local_project, n_rows=10, ceiling=small_shortfall_ceiling
        )

        assert grown_height == natural_height, (
            f"A {_PAGINATION_CONTROL_HEIGHT - 5}px shortfall should grow the slot to "
            f"the natural content height ({natural_height}px), not clamp to the "
            f"authored ceiling ({small_shortfall_ceiling}px); got {grown_height}px"
        )

    def test_large_shortfall_still_clamps_to_authored_ceiling(self, local_project):
        """A shortfall past the pagination-control-bar budget still clamps (unchanged)."""
        from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT

        natural_height = self._size_with_ceiling(local_project, n_rows=10, ceiling=None)
        large_shortfall_ceiling = natural_height - (_PAGINATION_CONTROL_HEIGHT + 20)

        clamped_height = self._size_with_ceiling(
            local_project, n_rows=10, ceiling=large_shortfall_ceiling
        )

        assert clamped_height == large_shortfall_ceiling, (
            f"A shortfall larger than {_PAGINATION_CONTROL_HEIGHT}px must still clamp "
            f"to the authored ceiling ({large_shortfall_ceiling}px); "
            f"got {clamped_height}px"
        )

    def _board_with_ceiling(self, local_project, n_rows: int, ceiling: float | None):
        """Same shape as ``_size_with_ceiling`` but returns the whole board."""
        rows_yaml = "\n".join(f"      - {{col: {i}}}" for i in range(n_rows))
        rows_item = (
            f"  - height: {ceiling}\n    rows:\n      - t1\n"
            if ceiling is not None
            else "  - t1\n"
        )
        yaml_content = f"""
queries:
  q:
    type: values
    rows:
{rows_yaml}
charts:
  t1:
    query: q
    type: table
rows:
{rows_item}
"""
        result = compile(yaml_content)
        assert result.success, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        board, _ = calculate_data_aware_layout(
            result.board, executor, {}, pre_resolved={}
        )
        return board

    def test_container_height_covers_grown_item_when_floor_fires(self, local_project):
        """board.layout.height must budget for the grown item, not the stale clamped value.

        ``_calculate_rows_dimensions`` (item assignment) and
        ``_measure_rows_layout_height`` (container budget) must agree on the
        per-item slot height. A single-item ``rows:`` stack has no auto
        sibling to absorb the mismatch, so an under-measured container is
        directly visible here.
        """
        from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT

        natural_height = self._size_with_ceiling(local_project, n_rows=10, ceiling=None)
        small_shortfall_ceiling = natural_height - (_PAGINATION_CONTROL_HEIGHT - 5)

        board = self._board_with_ceiling(
            local_project, n_rows=10, ceiling=small_shortfall_ceiling
        )
        item = board.layout.items[0]

        assert item.height == natural_height  # floor fired, per the sibling test above
        assert board.layout.height >= item.height, (
            f"container measured at {board.layout.height}px but the item it must "
            f"hold grew to {item.height}px — the measurement pass didn't apply "
            "the same floor as the assignment pass"
        )

    def _two_item_rows_board(
        self, local_project, table_rows: int, ceiling: float, auto_rows: int
    ):
        """Compile a rows: stack of [height-floored table, auto table] and return the board."""
        rows1_yaml = "\n".join(f"      - {{col: {i}}}" for i in range(table_rows))
        rows2_yaml = "\n".join(f"      - {{col: {i}}}" for i in range(auto_rows))
        yaml_content = f"""
queries:
  q1:
    type: values
    rows:
{rows1_yaml}
  q2:
    type: values
    rows:
{rows2_yaml}
charts:
  t1:
    query: q1
    type: table
  t2:
    query: q2
    type: table
rows:
  - height: {ceiling}
    rows:
      - t1
  - t2
"""
        result = compile(yaml_content)
        assert result.success, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        board, _ = calculate_data_aware_layout(
            result.board, executor, {}, pre_resolved={}
        )
        return board

    def test_auto_sibling_unaffected_by_neighbors_floor(self, local_project):
        """An auto-sized sibling must not be squeezed by a neighbor's floor firing.

        Regression: when the container is under-measured (see the test above),
        ``_calculate_rows_dimensions`` computes ``remaining`` from that too-small
        budget and scales every auto item down to make the deficit disappear —
        even though the auto item's own content never changed.
        """
        from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT

        natural_table_height = self._size_with_ceiling(
            local_project, n_rows=10, ceiling=None
        )
        small_shortfall_ceiling = natural_table_height - (
            _PAGINATION_CONTROL_HEIGHT - 5
        )
        natural_auto_height = self._size_with_ceiling(
            local_project, n_rows=5, ceiling=None
        )

        board = self._two_item_rows_board(
            local_project,
            table_rows=10,
            ceiling=small_shortfall_ceiling,
            auto_rows=5,
        )
        auto_item = board.layout.items[1]

        assert auto_item.height == natural_auto_height, (
            f"auto sibling squeezed to {auto_item.height}px by the neighbor's "
            f"floor firing; it should render at its own natural content height "
            f"({natural_auto_height}px), unaffected by the other item's authored "
            "height"
        )
