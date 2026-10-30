"""Tests for table cell word-wrap support.

Long text values should wrap to multiple lines (tspan elements) instead of
being silently truncated with an ellipsis. Row heights grow to accommodate
wrapped content.
"""

from __future__ import annotations

import re

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

# A long value with distinctive, common English words so we can assert
# individual fragments appear across wrapped tspan lines.
_LONG_TEXT = "Acme Corporation Annual Strategic Opportunity Review Pipeline Deal"
_LONG_TEXT_WORDS = _LONG_TEXT.split()
_SHORT_TEXT = "Acme"


def _make_table(make_chart, style: dict | None = None) -> Chart:
    return make_chart("table", x=None, y=None, style=style)


def _text_group_tspans(svg: str) -> list[list[str]]:
    """Return, for each <text>…</text> group, the list of tspan inner texts.

    Single-tspan groups (e.g. numeric three-lane rendering) are returned
    as one-element lists; wrapped text cells appear as multi-element lists.
    """
    groups: list[list[str]] = []
    for m in re.finditer(r"<text\b[^>]*>(.*?)</text>", svg, re.DOTALL):
        body = m.group(1)
        tspans = re.findall(r"<tspan\b[^>]*>([^<]*)</tspan>", body)
        groups.append(tspans)
    return groups


def _wrapped_cell_groups(svg: str) -> list[list[str]]:
    """Only <text> groups with 2+ tspans — i.e. wrapped multi-line cells."""
    return [g for g in _text_group_tspans(svg) if len(g) >= 2]


def _table_height(svg: str) -> float:
    m = re.search(r'<svg[^>]+height="([^"]+)"', svg)
    assert m, "Could not find SVG height"
    return float(m.group(1))


def _has_ellipsis(svg: str) -> bool:
    return "…" in svg


def _render(chart, data, width):
    from ._engine_render import render_table_svg

    return render_table_svg(
        resolve(chart, data, chart_style_context=_BOARD_STYLE),
        data,
        width=width,
        board_style=resolve_style(get_theme_style()),
    )


# ---------------------------------------------------------------------------
# Core wrap behavior: wrap produces data-row tspans with text fragments
# ---------------------------------------------------------------------------


class TestCellWrapProducesTspans:
    """Long text values wrap into multiple tspan elements inside one <text>."""

    def test_long_text_splits_across_tspans_in_one_text_group(self, make_chart):
        """The wrapped cell emits a single <text> with multiple tspan children
        whose concatenation covers the original value."""
        chart = _make_table(make_chart)
        # Width chosen to force wrap at the current default body font size.
        # If a future density bump shrinks body font further, this width
        # may need to drop again — the assertion below catches that.
        svg = _render(chart, [{"description": _LONG_TEXT}], 300)

        wrapped = _wrapped_cell_groups(svg)
        assert wrapped, "Expected at least one <text> group with multiple tspans"
        # Pick the group that contains fragments of our long text, not a
        # numeric three-lane group.
        match = next(
            (g for g in wrapped if any(w in " ".join(g) for w in _LONG_TEXT_WORDS)),
            None,
        )
        assert match is not None, (
            f"No wrapped group contained fragments of long text; groups={wrapped}"
        )
        assert len(match) >= 2, f"Expected 2+ tspans in wrapped data cell, got {match}"
        joined = " ".join(match).replace("  ", " ").strip()
        # Every word from the original must appear somewhere in the joined
        # tspan text — wrap must not drop content.
        for w in _LONG_TEXT_WORDS:
            assert w in joined, f"Word {w!r} missing from wrapped output: {joined!r}"

    def test_short_text_produces_single_line_cell(self, make_chart):
        """Short text that fits in the column renders as one tspan (or one
        plain <text>), never a wrapped multi-tspan group."""
        chart = _make_table(make_chart)
        svg = _render(chart, [{"name": _SHORT_TEXT}], 800)

        # No wrapped <text> group should contain SHORT_TEXT — it should
        # render as a single-line cell.
        wrapped = _wrapped_cell_groups(svg)
        for g in wrapped:
            assert _SHORT_TEXT not in " ".join(g), (
                f"Short text unexpectedly wrapped across tspans: {g}"
            )

    def test_wrap_produces_more_data_row_tspans_than_wrap_false(self, make_chart):
        """Controlled comparison: the same long-text data with wrap:true
        produces strictly more tspans than with wrap:false (which truncates
        to a single line)."""
        data = [{"description": _LONG_TEXT}]
        chart_wrap = _make_table(make_chart)
        chart_no_wrap = _make_table(make_chart, style={"table": {"wrap": False}})

        svg_wrap = _render(chart_wrap, data, 300)
        svg_no_wrap = _render(chart_no_wrap, data, 300)

        wrap_count = sum(len(g) for g in _text_group_tspans(svg_wrap))
        no_wrap_count = sum(len(g) for g in _text_group_tspans(svg_no_wrap))
        assert wrap_count > no_wrap_count, (
            f"wrap:true should emit more tspans than wrap:false "
            f"(got wrap={wrap_count}, no_wrap={no_wrap_count})"
        )


# ---------------------------------------------------------------------------
# Row height grows with wrapping
# ---------------------------------------------------------------------------


class TestRowHeightGrowsWithWrapping:
    """When cells wrap, the table SVG is taller than an equivalent single-line table."""

    def test_wrapped_table_strictly_taller_than_single_line(self, make_chart):
        chart = _make_table(make_chart)

        svg_long = _render(chart, [{"description": _LONG_TEXT}], 300)
        svg_short = _render(chart, [{"description": _SHORT_TEXT}], 300)

        height_long = _table_height(svg_long)
        height_short = _table_height(svg_short)

        assert height_long > height_short, (
            f"Wrapped table height ({height_long}) must exceed "
            f"single-line height ({height_short})"
        )

    def test_multi_row_heights_accumulate(self, make_chart):
        """Three wrapped rows should be strictly taller than three single-line rows."""
        chart = _make_table(make_chart)

        svg_long = _render(chart, [{"description": _LONG_TEXT}] * 3, 300)
        svg_short = _render(chart, [{"description": _SHORT_TEXT}] * 3, 300)

        assert _table_height(svg_long) > _table_height(svg_short)


# ---------------------------------------------------------------------------
# Paginated / height-constrained tables respect per-row heights
# ---------------------------------------------------------------------------


class TestPaginatedAndHeightConstrained:
    """Wrap must participate in pagination slicing and fixed-height row counts,
    otherwise tables inside sized containers overflow their box."""

    def test_fixed_height_with_wrap_does_not_overflow(self, make_chart):
        """With a fixed height, wrapping must not push data rows below the box.
        Row heights must drive how many rows fit, not the uniform row_height."""
        # Wrap forces each row to take 3+ lines; 10 wrapped rows would need
        # far more than 200px. Per-row heights must cap the slice.
        data = [{"description": _LONG_TEXT} for _ in range(10)]
        chart = resolve(_make_table(make_chart), data, chart_style_context=_BOARD_STYLE)
        from ._engine_render import render_table_svg

        svg = render_table_svg(
            chart,
            data,
            width=300,
            height=200,
            board_style=resolve_style(get_theme_style()),
        )

        # SVG height must honor the requested 200px.
        assert _table_height(svg) == 200

        # Data-row tspan y-coords (inside wrapped <text> groups whose content
        # includes long-text fragments) must fit inside the 200px box.
        # Pre-fix, the uniform row_height / available arithmetic picked
        # ~6 wrapped rows whose tspans landed well below 200.
        data_tspan_ys: list[float] = []
        for m in re.finditer(r"<text\b[^>]*>(.*?)</text>", svg, re.DOTALL):
            body = m.group(1)
            if not any(w in body for w in _LONG_TEXT_WORDS):
                continue
            for ym in re.finditer(r'<tspan[^>]*y="([0-9.]+)"', body):
                data_tspan_ys.append(float(ym.group(1)))
        assert data_tspan_ys, "Expected at least one wrapped data-row tspan"
        assert max(data_tspan_ys) <= 200, (
            f"Wrapped data rows extend past fixed height=200 "
            f"(max y={max(data_tspan_ys)}) — per-row height slicing failed"
        )

    def test_fixed_height_pagination_handles_mixed_row_heights(self, make_chart):
        """With fixed height + pagination and mixed short/long rows, *every*
        page must fit in the box. The pre-fix probe-leading-rows bug picked
        an ``effective`` page size that fit page 1 (all short) but overflowed
        later pages once the long rows kicked in.

        Iterate every page — at least one page must render a long-wrapped row,
        and no page may overflow the 300px height.
        """
        # First 10 rows short, next 10 long — a naive leading-rows probe
        # overestimates ``effective``.
        data = [{"description": _SHORT_TEXT} for _ in range(10)] + [
            {"description": _LONG_TEXT} for _ in range(10)
        ]
        _raw_chart = _make_table(
            make_chart,
            style={"pagination": {"enabled": True, "page_rows": 10}},
        )
        chart = resolve(_raw_chart, data, chart_style_context=_BOARD_STYLE)

        from ._engine_render import render_table_svg

        any_long_page_observed = False
        total_pages_seen = 0
        for page in range(1, 30):  # upper bound; break when current > total
            svg = render_table_svg(
                chart,
                data,
                width=300,
                height=300,
                variables={"test_table_page": page},
                board_style=resolve_style(get_theme_style()),
            )
            # New paginator: detect presence + extract total via the rightmost
            # page-number text element inside the paginator <g>. The active
            # page is marked with data-pagination-current and emits page N;
            # the last numeric item we see represents the total (the trailing
            # boundary page).
            paginator_m = re.search(
                r'<g class="dbt-paginator"[^>]*>(.*?)</g>', svg, re.DOTALL
            )
            if not paginator_m:
                break
            page_nums = [
                int(t)
                for t in re.findall(
                    r'<text[^>]*data-paginator-role="page"[^>]*>\s*(\d+)\s*</text>',
                    paginator_m.group(1),
                )
            ]
            if not page_nums:
                break
            total = max(page_nums)
            total_pages_seen = max(total_pages_seen, total)
            if page > total:
                break

            # Long-text tspans on this page must not overflow the 300px box.
            long_ys: list[float] = []
            for tm in re.finditer(r"<text\b[^>]*>(.*?)</text>", svg, re.DOTALL):
                body = tm.group(1)
                if not any(w in body for w in _LONG_TEXT_WORDS):
                    continue
                for ym in re.finditer(r'<tspan[^>]*y="([0-9.]+)"', body):
                    long_ys.append(float(ym.group(1)))
            if long_ys:
                any_long_page_observed = True
                assert max(long_ys) <= 300, (
                    f"Page {page} wrapped data rows overflowed 300px "
                    f"(max y={max(long_ys)}) — pagination probe unsafe"
                )

        assert total_pages_seen >= 2, (
            f"Expected the dataset to force multi-page rendering; saw only {total_pages_seen}"
        )
        assert any_long_page_observed, (
            "No page ever rendered a long-wrapped row — can't verify the "
            "cross-page safety property with this dataset"
        )

    def test_fixed_height_pagination_indicator_y_stable_across_pages(self, make_chart):
        """In a fixed-height paginated wrap table the pagination indicator
        must sit at the same y-coordinate on every page, even when rows per
        page have wildly different heights. Otherwise controls wobble as the
        user clicks prev/next."""
        data = [{"description": _SHORT_TEXT} for _ in range(10)] + [
            {"description": _LONG_TEXT} for _ in range(10)
        ]
        chart = resolve(
            _make_table(
                make_chart, style={"pagination": {"enabled": True, "page_rows": 10}}
            ),
            data,
            chart_style_context=_BOARD_STYLE,
        )

        from ._engine_render import render_table_svg

        indicator_ys: set[float] = set()
        for page in range(1, 30):
            svg = render_table_svg(
                chart,
                data,
                width=300,
                height=400,
                variables={"test_table_page": page},
                board_style=resolve_style(get_theme_style()),
            )
            paginator_m = re.search(
                r'<g class="dbt-paginator"[^>]*>(.*?)</g>', svg, re.DOTALL
            )
            if not paginator_m:
                break
            inner = paginator_m.group(1)
            page_nums = [
                int(t)
                for t in re.findall(
                    r'<text[^>]*data-paginator-role="page"[^>]*>\s*(\d+)\s*</text>',
                    inner,
                )
            ]
            if not page_nums or page > max(page_nums):
                break
            # The active page text is the stable per-page anchor for y stability.
            ind_m = re.search(
                r'<text[^>]*\sy="([0-9.]+)"[^>]*data-pagination-current',
                inner,
            )
            assert ind_m, f"Expected active-page <text> on page {page}"
            indicator_ys.add(float(ind_m.group(1)))

        assert len(indicator_ys) == 1, (
            f"Pagination indicator y wobbled across pages: saw {sorted(indicator_ys)}"
        )

    def test_paginated_table_height_reflects_wrapped_rows(self, make_chart):
        """A paginated table with wrapping should size its height from the
        tallest page, not page_rows * uniform row_height."""
        data_wrap = [{"description": _LONG_TEXT} for _ in range(20)]
        data_plain = [{"description": _SHORT_TEXT} for _ in range(20)]

        _raw = _make_table(
            make_chart, style={"pagination": {"enabled": True, "page_rows": 5}}
        )

        from ._engine_render import render_table_svg

        svg_wrap = render_table_svg(
            resolve(_raw, data_wrap, chart_style_context=_BOARD_STYLE),
            data_wrap,
            width=300,
            board_style=resolve_style(get_theme_style()),
        )
        svg_plain = render_table_svg(
            resolve(_raw, data_plain, chart_style_context=_BOARD_STYLE),
            data_plain,
            width=300,
            board_style=resolve_style(get_theme_style()),
        )

        assert _table_height(svg_wrap) > _table_height(svg_plain), (
            "Paginated table with wrapped rows must be taller than one with "
            "short rows — table_height must derive from per-row heights"
        )


# ---------------------------------------------------------------------------
# wrap: false preserves truncation
# ---------------------------------------------------------------------------


class TestWrapFalsePreservesTruncation:
    """When wrap is disabled (style.table.wrap: false), truncation is used instead."""

    def test_wrap_false_emits_ellipsis(self, make_chart):
        # `fit`: a slot-squeezed single line truncates; `scroll` would widen it.
        chart = _make_table(
            make_chart,
            style={"table": {"wrap": False, "column_layout": {"overflow": "fit"}}},
        )
        svg = _render(chart, [{"description": _LONG_TEXT}], 300)
        assert _has_ellipsis(svg), "wrap:false must truncate long text with ellipsis"

    def test_wrap_true_does_not_emit_ellipsis_for_wrappable_text(self, make_chart):
        chart = _make_table(make_chart)
        svg = _render(chart, [{"description": _LONG_TEXT}], 400)
        assert not _has_ellipsis(svg), (
            "wrap:true must not truncate — the whole value should wrap across lines"
        )

    def test_wrap_false_strictly_shorter_than_wrap_true_for_wrappable_text(
        self, make_chart
    ):
        data = [{"description": _LONG_TEXT}]
        chart_wrap = _make_table(make_chart)
        chart_no_wrap = _make_table(make_chart, style={"table": {"wrap": False}})

        svg_wrap = _render(chart_wrap, data, 300)
        svg_no_wrap = _render(chart_no_wrap, data, 300)

        assert _table_height(svg_no_wrap) < _table_height(svg_wrap), (
            f"wrap:false should not grow the row; "
            f"wrap:false={_table_height(svg_no_wrap)}, wrap:true={_table_height(svg_wrap)}"
        )


# ---------------------------------------------------------------------------
# Numeric cells stay in three-lane layout, not wrapped
# ---------------------------------------------------------------------------


class TestNumericCellsNotWrapped:
    """Numeric cells use the three-lane layout, never word-wrap."""

    def test_numeric_cell_renders_as_single_value(self, make_chart):
        """A numeric value should appear verbatim (comma-formatted) in the
        SVG — never split by wrapping across tspans."""
        chart = _make_table(make_chart)
        svg = _render(chart, [{"amount": 1234567890.0}], 200)

        # The formatted number renders as a single contiguous value tspan (the
        # theme SI default: 1234567890 -> "1.23" + "B"). Wrapping would split
        # the digits character-by-character across lines instead.
        assert ">1.23</tspan>" in svg, (
            "Numeric cell must render as a single value (SI-formatted), "
            "not split by wrap"
        )
        assert ">B</tspan>" in svg
