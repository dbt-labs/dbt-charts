"""Tests for interactive table pagination controls and URL state.

Proves:
1. _resolve_visible_rows slices data by page number (not just first page).
2. render_table_svg emits a right-aligned paginator <g> when data overflows
   the page_rows or the height budget.
3. The paginator uses chevrons + a windowed sequence of page numbers, with
   the active page emphasized and disabled chevrons shown in a muted tone.
4. The chart's page variable name is used in the click handlers.
5. No paginator when all data fits or pagination is disabled.
6. Page number extracted from variables dict drives which rows render.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Generator

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.controls import controls_language, interactive_controls

from ._engine_render import render_table_svg
from ._pooled_table import visible_svg

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def _make_data(n: int) -> list[dict[str, str]]:
    """Generate n rows of test data."""
    return [{"name": f"row_{i}", "value": str(i)} for i in range(1, n + 1)]


def _find_paginator_group(svg: str, var_name: str) -> str:
    """Return the inner SVG of the paginator <g> for the given page-var."""
    m = re.search(
        rf'<g class="dbt-paginator" data-paginator="{re.escape(var_name)}">'
        r"(.*?)</g>",
        svg,
        re.DOTALL,
    )
    assert m, f"paginator group for {var_name!r} not found"
    return m.group(1)


def _find_active_page_text(svg: str, var_name: str) -> str:
    """Return the <text> element marked as the current/active page."""
    m = re.search(
        rf'<text[^>]*data-pagination-current="{re.escape(var_name)}"[^>]*>'
        r"[^<]*</text>",
        svg,
    )
    assert m, f"active-page text for {var_name!r} not found"
    return m.group(0)


class TestAutoShrinkOverridesTruncationFooter:
    """When pagination is enabled and the cell can't fit page_rows rows,
    the renderer must auto-shrink and paginate — never fall back to the
    "+N" truncation footer. When pagination is disabled, the
    renderer must use the table's natural height and render all rows instead.
    """

    def test_overflow_in_small_cell_paginates_not_truncates(self, make_chart) -> None:
        """30 rows, page_rows=20, cell that fits ~10 rows → paginator, no footer."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="autoshrink",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 20}),
        )
        data = _make_data(30)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            height=300,
            board_style=resolve_style(get_theme_style()),
        )

        assert "dbt-paginator" in svg, "small-cell overflow should paginate"
        assert "more rows" not in svg, (
            "auto-shrink path must replace the truncation footer when "
            "pagination is enabled"
        )

    def test_pagination_disabled_ignores_height_clamp_and_renders_all_rows(
        self, make_chart
    ) -> None:
        """Explicit ``pagination.enabled: false`` means no hidden rows."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="disabled_pagination",
            style=TableChartStylePatch(pagination={"enabled": False}),
        )
        data = _make_data(30)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )

        assert 'class="dbt-paginator"' not in svg
        assert "more rows" not in svg
        assert "row_30" in svg

    def test_pagination_disabled_five_row_table_does_not_drop_last_row(
        self, make_chart
    ) -> None:
        """Regression for #146: a 5-row static table must not render 4 + footer."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="five_rows",
            style=TableChartStylePatch(pagination={"enabled": False}),
        )
        data = _make_data(5)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            height=150,
            board_style=resolve_style(get_theme_style()),
        )

        assert 'class="dbt-paginator"' not in svg
        assert "more rows" not in svg
        assert "row_5" in svg


class TestPaginationControlsInSvg:
    """render_table_svg should emit a right-aligned paginator.

    Visual contract: a ``<g class="dbt-paginator" data-paginator="<chart>_page">``
    group containing a leading chevron, a windowed sequence of page numbers
    (with ellipsis placeholders for hidden ranges), and a trailing chevron.
    The active page text element carries ``data-pagination-current``;
    clickable items get an invisible ``<rect>`` with
    ``onclick=updateVariable(...)`` as the hit target — the contract for a
    host that ships variables.js (dct serve, Cloud). Static exports render a
    different, JS-toggled contract (``TestStaticMultiPagePagination`` below).
    """

    @pytest.fixture(autouse=True)
    def _interactive_host(self) -> Generator[None]:
        with interactive_controls(True):
            yield

    def test_paginator_group_present_when_multi_page(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="details",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )

        inner = _find_paginator_group(svg, "details_page")
        assert "‹" in inner  # leading chevron
        assert "›" in inner  # trailing chevron
        # No "Page X of Y" prose — bare numbers + chevrons + ellipses only.
        assert "Page " not in inner

    def test_no_pagination_controls_when_single_page(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="small_table",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 50}),
        )
        data = _make_data(10)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )

        assert "small_table_page" not in svg
        assert 'class="dbt-paginator"' not in svg

    def test_no_pagination_controls_when_disabled(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="no_pages",
            style=TableChartStylePatch(pagination={"enabled": False}),
        )
        data = _make_data(100)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )

        assert "no_pages_page" not in svg
        assert 'class="dbt-paginator"' not in svg

    def test_page_from_variables_shows_correct_data(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="paged",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = visible_svg(
            render_table_svg(
                chart,
                data,
                width=600,
                variables={"paged_page": "2"},
                board_style=resolve_style(get_theme_style()),
            )
        )

        assert "row_6" in svg
        assert "row_10" in svg
        # Page 1 rows should NOT be visible (use word boundary to avoid
        # matching row_1 inside row_10/row_11/etc.)
        assert not re.search(r"\brow_1\b", svg)
        assert "row_5" not in svg

    def test_clickable_items_name_a_page_target_and_no_variable(
        self, make_chart
    ) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="nav_test",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        inner = _find_paginator_group(svg, "nav_test_page")

        rects = re.findall(r"<rect[^>]*/>", inner)
        # A pooled table pages in the page: its buttons name a target for the
        # table runtime and never a variable for the controls runtime. No
        # onclick — code never ships inside a board.
        clickable = [r for r in rects if "data-dbt-page-target" in r]
        assert clickable, "no hit-rects naming a page target in paginator"
        assert not any("data-dbt-page-var" in r for r in rects)
        assert not any("updateVariable" in r for r in rects)
        for rect in clickable:
            assert 'fill="transparent"' in rect
            # The pointer cursor is the host's rule on the class, not an inline style.
            assert 'class="dbt-page-target"' in rect

    def test_first_page_prev_chevron_is_disabled(self, make_chart) -> None:
        """On page 1 the prev chevron renders in the disabled tone with no
        click handler — disabled state is signaled by color, not opacity."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="first_page",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        inner = _find_paginator_group(svg, "first_page_page")

        prev = re.search(
            r'<text[^>]*data-paginator-role="prev"[^>]*>[^<]*</text>', inner
        )
        assert prev, "prev chevron <text> not found"
        assert "onclick" not in prev.group(0)
        assert not re.search(r"updateVariable\('first_page_page', '0'\)", inner), (
            "disabled prev should not emit a click handler"
        )

    def test_last_page_next_chevron_is_disabled(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="last_page",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            variables={"last_page_page": "4"},
            board_style=resolve_style(get_theme_style()),
        )

        assert "row_16" in svg
        assert "row_20" in svg
        inner = _find_paginator_group(svg, "last_page_page")
        next_ch = re.search(
            r'<text[^>]*data-paginator-role="next"[^>]*>[^<]*</text>', inner
        )
        assert next_ch, "next chevron <text> not found"
        assert "onclick" not in next_ch.group(0)

    def test_out_of_range_page_previous_targets_last_page_minus_one(
        self, make_chart
    ) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="stale",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = visible_svg(
            render_table_svg(
                chart,
                data,
                width=600,
                variables={"stale_page": "9"},
                board_style=resolve_style(get_theme_style()),
            )
        )

        inner = _find_paginator_group(svg, "stale_page")
        targets = [int(t) for t in re.findall(r'data-dbt-page-target="(\d+)"', inner)]
        assert targets and max(targets) <= 4
        assert 3 in targets
        assert 'data-pagination-current="stale_page"' in svg
        assert ">4</text>" in _find_active_page_text(svg, "stale_page")

    def test_active_page_has_emphasis_weight(self, make_chart) -> None:
        """Current page renders at the theme's active weight (default 600)
        and is marked with ``data-pagination-current``. Other page numbers
        render at the inactive weight (default 400)."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="indicator",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 10}),
        )
        data = _make_data(30)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = visible_svg(
            render_table_svg(
                chart,
                data,
                width=600,
                variables={"indicator_page": "2"},
                board_style=resolve_style(get_theme_style()),
            )
        )

        active = _find_active_page_text(svg, "indicator_page")
        assert 'font-weight="600"' in active
        m = re.search(r">(\s*\d+\s*)</text>", active)
        assert m and m.group(1).strip() == "2"

        inner = _find_paginator_group(svg, "indicator_page")
        inactive_texts = [
            t
            for t in re.findall(r"<text[^>]*>[^<]*</text>", inner)
            if "data-pagination-current" not in t
            and re.search(r">\s*[13]\s*</text>", t)
        ]
        assert inactive_texts, "no inactive page-number text elements found"
        for t in inactive_texts:
            assert 'font-weight="400"' in t

    def test_paginator_uses_tabular_figures(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="tab",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        inner = _find_paginator_group(svg, "tab_page")
        text_elems = re.findall(r"<text[^>]*>[^<]*</text>", inner)
        assert text_elems
        for t in text_elems:
            assert "tabular-nums" in t

    def test_paginator_right_anchored(self, make_chart) -> None:
        """The paginator hugs the right edge of the table."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="ra",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        table_width = 600.0
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=table_width,
            board_style=resolve_style(get_theme_style()),
        )
        inner = _find_paginator_group(svg, "ra_page")
        xs = [float(m.group(1)) for m in re.finditer(r'<text x="([\d.]+)"', inner)]
        assert xs, "no paginator text centers found"
        rightmost = max(xs)
        assert table_width - 40.0 <= rightmost <= table_width, (
            f"rightmost paginator item at x={rightmost} not anchored to "
            f"table_width={table_width}"
        )


class TestPaginationViewBoxContainment:
    """Pagination controls must render within the SVG viewBox.

    Regression: when the caller passes an explicit height that doesn't
    account for pagination, the renderer previously skipped reserving
    pagination_control_height and drew pagination below the viewBox, so
    the board layout clipped the control text. The renderer must always
    reserve space when pagination will fire — height-limited or
    page_rows-limited — and shrink visible rows accordingly.
    """

    def _pagination_text_y(self, svg: str, var_name: str) -> float:
        """Return the y-baseline of the active page text element, or NaN."""
        import math

        m = re.search(
            rf'<text x="[^"]+" y="([\d.]+)"[^>]*data-pagination-current="'
            rf'{re.escape(var_name)}"',
            svg,
        )
        return float(m.group(1)) if m else math.nan

    def _viewbox_height(self, svg: str) -> float:
        m = re.search(r'<svg[^>]+viewBox="0 0 [\d.]+ ([\d.]+)"', svg)
        assert m, "SVG root viewBox not found"
        return float(m.group(1))

    def test_height_limited_pagination_fits_in_viewbox(self, make_chart) -> None:
        """Height forces pagination (data > max_rows, data <= page_rows).

        Leaderboard-shaped case: 12 rows, page_rows=20 (default), but the
        allotted height only fits 11 rows + pagination. Pagination must
        appear and stay within viewBox.
        """
        from ._engine_render import render_table_svg

        chart = make_chart("table", id="ht_lim")
        chart.title = "Most Visited Museums"
        chart.subtitle = "C1 — Auto-sized, no column config"
        data = _make_data(12)
        # Use a height tight enough to force pagination regardless of the
        # current default row height (densification shrinks row.height
        # over time). 200px leaves room for only a few rows after
        # title/subtitle/header — well below 12.
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=544,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )

        # Confirm pagination actually fired (otherwise test is vacuous).
        # The paginator group is only emitted when there's >1 page, so its
        # presence is enough — we don't have to extract the page count.
        assert "dbt-paginator" in svg, "pagination expected for 12 rows in 200px"

        y = self._pagination_text_y(svg, "ht_lim_page")
        vb_h = self._viewbox_height(svg)
        # Pagination text baseline + descender headroom (~3px for 11px font)
        # must fit within the viewBox. A small margin (1px) guards against
        # sub-pixel drift from font metrics.
        assert y + 3 <= vb_h, (
            f"pagination text baseline y={y} + descender 3 exceeds viewBox "
            f"height {vb_h}; controls are clipped"
        )

    def test_pagesize_limited_pagination_fits_in_viewbox(self, make_chart) -> None:
        """Page_size forces pagination (data > page_rows) — the case the old
        code handled. This guard prevents regression."""
        from ._engine_render import render_table_svg

        chart = make_chart(
            "table",
            id="ps_lim",
            style={"pagination": {"enabled": True, "page_rows": 5}},
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=544,
            height=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert "dbt-paginator" in svg
        y = self._pagination_text_y(svg, "ps_lim_page")
        vb_h = self._viewbox_height(svg)
        assert y + 3 <= vb_h


class TestStaticMultiPagePagination:
    """A static export (no interactive host — the default, matching ``dct
    render``) must ship pagination controls that actually work: every page's
    rows are pre-rendered into a toggle group and a small inline script
    (table_pagination.js) flips visibility on click, instead of an
    ``onclick=updateVariable(...)`` handler with no runtime present to answer
    it (ERR: ReferenceError when the exported HTML/SVG is opened standalone).

    ``render_table_svg`` called directly (as every test in this file does)
    runs outside ``interactive_controls(True)`` by default — this class pins
    that default, non-interactive behavior.
    """

    def test_no_update_variable_onclick_in_static_export(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="static1",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=600, board_style=resolve_style(get_theme_style())
        )

        assert "updateVariable" not in svg, (
            "a static export must never emit a call to a runtime "
            "(variables.js) it does not ship"
        )
        assert 'data-dbt-page-target="2"' in svg

    def test_every_page_rows_present_toggled_by_display(self, make_chart) -> None:
        """All 4 pages' rows are in the DOM; only the current page is visible."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="static2",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)  # 4 pages of 5
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=600, board_style=resolve_style(get_theme_style())
        )

        for row_name in ("row_1", "row_10", "row_15", "row_20"):
            assert row_name in svg, f"{row_name} missing — not every page was rendered"

        groups = re.findall(
            r'<g class="dbt-table-page" data-dbt-table-page="static2" '
            r'data-page="(\d)" style="display:([^"]*)">',
            svg,
        )
        assert [p for p, _ in groups] == ["1", "2", "3", "4"]
        displays = dict(groups)
        assert displays["1"] == ""
        assert displays["2"] == "none"
        assert displays["3"] == "none"
        assert displays["4"] == "none"

    def test_variables_page_selects_initially_visible_group(self, make_chart) -> None:
        """A variables-supplied page still picks which pre-rendered group starts visible."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="static3",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            variables={"static3_page": "3"},
            board_style=resolve_style(get_theme_style()),
        )

        groups = dict(
            re.findall(
                r'data-dbt-table-page="static3" data-page="(\d)" style="display:([^"]*)"',
                svg,
            )
        )
        assert groups["3"] == ""
        assert groups["1"] == "none"

    def test_pagination_script_embedded_when_multi_page(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="static4",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=600, board_style=resolve_style(get_theme_style())
        )

        assert "<script" in svg
        assert "data-dbt-page-target" in svg
        assert "data-dbt-table-page" in svg

    def test_no_script_when_single_page(self, make_chart) -> None:
        """A table that fits on one page ships no pagination script at all."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="static5",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 50}),
        )
        data = _make_data(10)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=600, board_style=resolve_style(get_theme_style())
        )

        assert "<script" not in svg
        assert "dbt-table-page" not in svg

    def test_pre_rendered_pages_capped_regardless_of_total_rows(
        self, make_chart
    ) -> None:
        """Export size must not scale with total row count.

        A table whose real page count exceeds the static-export cap only
        pre-renders up to the cap; rows past it are never drawn, and the
        paginator itself never links to an unrendered page — clicking a
        capped-out target would otherwise blank the table (JS finds no
        matching toggle group)."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        _STATIC_MULTI_PAGE_MAX_PAGES = 20

        n_rows = (_STATIC_MULTI_PAGE_MAX_PAGES + 5) * 5
        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="static_cap",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(n_rows)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=600, board_style=resolve_style(get_theme_style())
        )

        groups = re.findall(r'data-dbt-table-page="static_cap" data-page="(\d+)"', svg)
        assert [int(p) for p in groups] == list(
            range(1, _STATIC_MULTI_PAGE_MAX_PAGES + 1)
        )
        assert f"row_{n_rows}" not in svg, "rows past the cap must never render"

        targets = {int(t) for t in re.findall(r'data-dbt-page-target="(\d+)"', svg)}
        assert max(targets) <= _STATIC_MULTI_PAGE_MAX_PAGES, (
            "paginator must never link to a page beyond what was pre-rendered"
        )
        assert ">25</text>" in svg, "the pager keeps the true last page"
        assert "Showing pages" not in svg, "no generated English cap note"

    def test_explicit_height_is_not_grown_past_when_capped(self, make_chart) -> None:
        """An explicit height:, once past sizing, is an invariant the renderer
        must not silently exceed -- a grid: layout places siblings at a
        precomputed pixel_y that a table's actual height never corrects
        (unlike rows:/cols:, which read it back), so growing past an
        explicit height paints into whatever the grid placed below.
        """
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        _STATIC_MULTI_PAGE_MAX_PAGES = 20

        n_rows = (_STATIC_MULTI_PAGE_MAX_PAGES + 5) * 5
        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="static_cap3",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(n_rows)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)

        for explicit_height in (300, 400):
            svg = render_table_svg(
                chart,
                data,
                width=600,
                height=explicit_height,
                board_style=resolve_style(get_theme_style()),
            )
            m = re.search(r'<svg[^>]+height="([\d.]+)"', svg)
            assert m, svg
            assert float(m.group(1)) == explicit_height, (
                f"explicit height={explicit_height} must be honored exactly "
                f"even when the table is static-export-capped; got "
                f"{m.group(1)}"
            )

    def test_no_cap_note_when_total_pages_within_cap(self, make_chart) -> None:
        """A table whose real page count fits the cap has no disabled pages."""
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="static_uncapped",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)  # 4 pages — well under the cap
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=600, board_style=resolve_style(get_theme_style())
        )

        assert "static export" not in svg.lower()

    def test_summary_gap_sized_from_worst_page_not_current_page(
        self, make_chart
    ) -> None:
        """``table_height`` must budget for whichever rendered page needs the
        summary-row breathing gap, not just the page ``visible_data`` happens
        to hold.

        Bug: in the static multi-page branch every page paints its own
        row-role transitions independently, but the sizing pass only counted
        gaps on the current (default: first) page. A totals row landing on a
        later page painted below the table's own background rect — nothing
        here reserved room for it.
        """
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        # page_rows=5 -> 3 pages of 5. The totals row is the very last row,
        # so it lands on page 3 alone; pages 1-2 have no role transition.
        n_rows = 15
        data_with_summary = [
            {"name": f"row_{i}", "value": str(i), "kind": "value"}
            for i in range(1, n_rows)
        ]
        data_with_summary.append({"name": "Total", "value": "999", "kind": "summary"})
        data_without_summary = [
            {"name": f"row_{i}", "value": str(i), "kind": "value"}
            for i in range(1, n_rows + 1)
        ]

        def _render(rows: list[dict[str, str]]) -> str:
            chart = make_chart(
                "table",
                x=None,
                y=None,
                id="static_gap",
                style=TableChartStylePatch(
                    pagination={"enabled": True, "page_rows": 5},
                    row={"role": "kind"},
                ),
            )
            resolved = resolve(chart, rows, chart_style_context=_BOARD_STYLE)
            return render_table_svg(
                resolved, rows, width=600, board_style=resolve_style(get_theme_style())
            )

        def _svg_height(svg: str) -> float:
            m = re.search(r'<svg[^>]+height="([^"]+)"', svg)
            assert m, "SVG must have a height attribute"
            return float(m.group(1))

        height_with_summary = _svg_height(_render(data_with_summary))
        height_without_summary = _svg_height(_render(data_without_summary))

        rs = resolve_style(get_theme_style())
        summary_gap = int(rs.chart_defaults.table.row.height * 0.4)

        assert height_with_summary - height_without_summary == summary_gap, (
            f"table_height must grow by the page-3 summary gap ({summary_gap}px) "
            f"regardless of which page is being sized; got a "
            f"{height_with_summary - height_without_summary}px delta"
        )


class TestStripPaginationChrome:
    """PNG/PDF (and any other rasterizer) must not show dead pagination controls.

    They rasterize the same board SVG the static-multi-page path draws, but
    can never run ``table_pagination.js`` — the visible page's paginator
    would look clickable and do nothing. ``strip_pagination_chrome`` removes
    that one ``<g>`` before the SVG is handed to a rasterizer; the rows stay.
    """

    def test_removes_paginator_group_keeps_rows(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.render.svg_utils import strip_pagination_chrome

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="raster1",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
        )
        data = _make_data(20)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=600, board_style=resolve_style(get_theme_style())
        )
        assert "dbt-paginator" in svg  # sanity: chrome is present pre-strip

        stripped = strip_pagination_chrome(svg)

        # The visible, clickable-looking markup is gone. The pagination
        # script's own inert source text (never executed by a rasterizer)
        # is left alone, same as chart_interactivity.js always is — a
        # <script> element paints no pixels, so it isn't the dead-looking
        # affordance this guards against.
        assert 'class="dbt-paginator"' not in stripped
        assert "row_1" in stripped, "page-1 rows must survive stripping"
        # The row-range label is content ("how much am I not seeing?"), not
        # clickable-looking chrome -- it must survive even though it used to
        # live inside the stripped <g class="dbt-paginator"> group.
        assert "1–5 / 20" in stripped

    def test_no_op_on_single_page_table(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.render.svg_utils import strip_pagination_chrome

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="raster2",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 50}),
        )
        data = _make_data(10)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=600, board_style=resolve_style(get_theme_style())
        )

        assert strip_pagination_chrome(svg) == svg


class TestPaginatorRowRangeLabel:
    """The paginator states what it pages: "N–M / T" left of the chevrons.

    An unlabeled ``‹ 1 2 … 37 ›`` reads as "37 pages of dashboards I
    apparently created", not "this table has 37 pages of rows".
    """

    @pytest.fixture(autouse=True)
    def _interactive_host(self) -> Generator[None]:
        with interactive_controls(True):
            yield

    def test_middle_page_shows_its_own_row_range(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="labeled",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 10}),
        )
        data = _make_data(50)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            variables={"labeled_page": "3"},
            board_style=resolve_style(get_theme_style()),
        )

        assert "21–30 / 50" in svg

    def test_short_last_page_reports_its_real_end_not_page_times_page_rows(
        self, make_chart
    ) -> None:
        """41 rows at 20/page: page 3 is a 1-row tail — its end is the real
        last row (41), never ``page * page_rows`` (60).
        """
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="tail",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 20}),
        )
        data = _make_data(41)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            variables={"tail_page": "3"},
            board_style=resolve_style(get_theme_style()),
        )

        assert "41–41 / 41" in svg

    def test_absent_when_table_is_not_paginated(self, make_chart) -> None:
        chart = make_chart("table", x=None, y=None, id="unpaged")
        data = _make_data(5)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=600, board_style=resolve_style(get_theme_style())
        )

        assert re.search(r"\d+\u2013\d+ / \d+", svg) is None

    def test_label_dropped_when_it_would_collide_with_the_pager(
        self, make_chart
    ) -> None:
        """The label's guard (padding + label_w <= cursor_left) is new
        behavior with its own branch -- delete the guard and every other
        test in this class still passes at width=600, where there is
        always room. A realistic narrow card (a 4-6 column grid) is where
        the branch actually flips.
        """
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="labeled",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 10}),
        )
        data = _make_data(50)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = visible_svg(
            render_table_svg(
                chart,
                data,
                width=160,
                variables={"labeled_page": "3"},
                board_style=resolve_style(get_theme_style()),
            )
        )

        assert re.search(r"\d+\u2013\d+ / \d+", svg) is None
        # Dropping the label must not drop the pager itself.
        assert "dbt-paginator" in svg

    def test_label_kept_when_it_fits(self, make_chart) -> None:
        """Same chart/page as the collision test above, at a width where
        the label and the right-anchored pager both fit -- proves the
        collision test's absence is the guard firing, not the label being
        broken at this chart's geometry generally.
        """
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="labeled",
            style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 10}),
        )
        data = _make_data(50)
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=250,
            variables={"labeled_page": "3"},
            board_style=resolve_style(get_theme_style()),
        )

        assert "21–30 / 50" in svg


class TestPooledPagerSitsAtOneY:
    """A pooled table's pager sits below the tallest page a sort could build,
    at one y on every page, so a re-sorted page never meets it and the card
    never resizes between pages.
    """

    @pytest.fixture(autouse=True)
    def _interactive_host(self) -> Generator[None]:
        # Without this the table takes the static_multi_page path.
        with interactive_controls(True):
            yield

    def test_pager_sits_at_the_same_y_on_every_page(self, make_chart) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            id="wobble",
            style=TableChartStylePatch(
                pagination={"enabled": True, "page_rows": 5},
                columns={"note": {"width": 60, "visible": True}},
            ),
        )
        # page 1 (rows 1-5) is uniform height; row_7, on page 2 (rows 6-10),
        # wraps its narrow "note" column into many lines and makes page 2
        # much taller.
        long_value = "x" * 200
        data = [
            {"name": f"row_{i}", "note": long_value if i == 7 else "ok"}
            for i in range(1, 16)
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)

        def _render(page: int) -> str:
            return visible_svg(
                render_table_svg(
                    chart,
                    data,
                    width=600,
                    variables={"wobble_page": str(page)},
                    board_style=resolve_style(get_theme_style()),
                )
            )

        def _prev_text_y(svg: str) -> float:
            # The "prev" chevron glyph always renders (disabled on page 1,
            # live from page 2 on) -- a reliable y-anchor on every page. This
            # is the raw <text> y (== indicator_y + 18, the baseline offset
            # _render_pagination_controls applies) -- only ever compared
            # relatively below, so the constant offset cancels and there is
            # no need to undo it.
            m = re.search(
                r'<text x="[\d.]+" y="([\d.]+)"[^>]*data-paginator-role="prev"',
                svg,
            )
            assert m, svg
            return float(m.group(1))

        def _table_height(svg: str) -> float:
            m = re.search(r'<svg[^>]+width="[\d.]+" height="([\d.]+)"', svg)
            assert m, svg
            return float(m.group(1))

        svg_page1 = _render(1)
        svg_page2 = _render(2)

        assert _prev_text_y(svg_page1) == _prev_text_y(svg_page2)
        assert _table_height(svg_page1) == _table_height(svg_page2), (
            "table height must stay constant across pages"
        )


def test_a_live_paginator_over_the_row_pool_cap_publishes_its_variable(
    make_chart,
) -> None:
    """Code never ships inside a board. A table too large to pool still pages
    through its variable: the button names the variable it drives and the
    runtime commits it."""
    from dbt_charts.core.compile.models.style.authored import TableChartStylePatch
    from dbt_charts.core.render.controls import interactive_controls

    chart = make_chart(
        "table",
        x=None,
        y=None,
        id="live1",
        style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
    )
    data = _make_data(1001)
    chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    with interactive_controls(True):
        svg = render_table_svg(
            chart, data, width=600, board_style=resolve_style(get_theme_style())
        )

    assert "onclick=" not in svg
    assert "data-dbt-table=" not in svg
    assert 'data-dbt-page-var="live1_page"' in svg
    assert 'data-dbt-page-target="2"' in svg


def test_a_pooled_table_carries_the_board_languages_menu_words(make_chart) -> None:
    """The engine's slice is the shipped controls slice: the words a live page
    reads off the table root are the ones a stored render gets in English."""
    from dbt_charts.core.compile.models.style.authored import TableChartStylePatch
    from dbt_charts.core.text.format_d3 import language_scope

    chart = make_chart(
        "table",
        x=None,
        y=None,
        id="t1",
        style=TableChartStylePatch(pagination={"enabled": True, "page_rows": 5}),
    )
    data = _make_data(12)
    chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    with language_scope("de"), interactive_controls(True):
        svg = render_table_svg(
            chart, data, width=600, board_style=resolve_style(get_theme_style())
        )
        slice_ = controls_language()
    raw = re.search(r'data-dbt-table="t1"[^>]*data-dbt-language="([^"]*)"', svg)
    assert raw is not None, svg
    words = json.loads(html.unescape(raw.group(1)))
    assert words == {word: slice_[word] for word in words}
    assert words["clear_sort"] == "Sortierung aufheben"
    assert words["page_range"] == "{first}–{last} / {total}"
