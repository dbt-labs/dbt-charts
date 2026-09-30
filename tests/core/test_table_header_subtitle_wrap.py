"""Regression tests: table headers and subtitles must wrap, not truncate.

Three independent defects compounded on the same table — a long subtitle over
eight columns, one text and seven narrow numerics with long labels:

* The table subtitle was emitted as one unbounded ``<text>`` — no width limit,
  no wrap, no reserved height — so long subtitles ran off the card edge.
* Column headers fell through to the board ``title.overflow`` (``truncate``)
  instead of the theme's ``charts.table.header.overflow`` (``wrap-two``), so
  multi-word labels rendered as ``Recent…`` in a narrow column.
* Column allocation spent surplus width in proportion to *cell* content, so the
  one text column swelled well past what it needed while every numeric column
  landed below the width of its own header label.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.render.chart.table import render_table_svg

_BOARD_STYLE = resolve_style(get_theme_style())
_BOARD_CTX = resolve_chart_style_context(get_theme_style())

# Eight narrow numeric columns squeeze every header below its single-line
# width, which is exactly the shape that exposed the truncate default.
_NARROW_ROW = {
    "metric": "MQL to Stage 0 within 7 days",
    "recent_eligible": 644,
    "recent_converted": 61,
    "recent_rate": 0.095,
    "prior_eligible": 2671,
    "prior_converted": 144,
    "prior_rate": 0.054,
    "change": 0.041,
}
_LABELS = {
    "metric": "Conversion measure",
    "recent_eligible": "Recent mature MQLs",
    "recent_converted": "Converted in 7d",
    "recent_rate": "Jun 29–Jul 26",
    "prior_eligible": "Prior mature MQLs",
    "prior_converted": "Converted in 7d",
    "prior_rate": "Jun 1–Jun 28",
    "change": "Change",
}
_LONG_SUBTITLE = (
    "61 of 644 mature MQLs converted (9.5%) versus 144 of 2,671 (5.4%): "
    "+4.1 points, approximate 95% interval +1.7 to +6.5 points, measured "
    "across the trailing 28-day window ending Jul 26 against the prior period"
)
_TITLE = "Seven-Day MQL Conversion"
_WIDTH = 836


def _chart(make_chart, *, subtitle: str | None = None):
    extra = {"subtitle": subtitle} if subtitle is not None else {}
    return make_chart(
        "table",
        x=None,
        y=None,
        title=_TITLE,
        style={"columns": {col: {"label": label} for col, label in _LABELS.items()}},
        **extra,
    )


def _render(make_chart, *, subtitle: str | None = None) -> str:
    chart = _chart(make_chart, subtitle=subtitle)
    data = [_NARROW_ROW]
    return render_table_svg(
        resolve(chart, data, chart_style_context=_BOARD_CTX),
        data,
        width=_WIDTH,
        board_style=_BOARD_STYLE,
    )


def _text_elements(svg: str) -> list[str]:
    return re.findall(r"<text\b[^>]*>.*?</text>", svg, re.DOTALL)


def _header_elements(svg: str) -> list[str]:
    """Every <text> element in the header band, selected structurally.

    The renderer emits title, then subtitle, then the header row, then the data
    rows, so the header band is everything between the last title-block element
    and the first cell of row one. Selecting by position rather than by a themed
    font weight keeps the assertions live if the theme is retuned — and catches
    a header abbreviated past its first word (``Con…``), which a label-substring
    filter would miss entirely.
    """
    elements = _text_elements(svg)
    first_cell = next(i for i, el in enumerate(elements) if _NARROW_ROW["metric"] in el)
    title_block = [
        i
        for i, el in enumerate(elements[:first_cell])
        if _TITLE in el or "mature MQLs converted" in el
    ]
    headers = elements[max(title_block) + 1 : first_cell]
    assert headers, f"No header elements found between title and first row: {svg}"
    assert len(headers) == len(_LABELS), (
        f"Expected {len(_LABELS)} header elements, found {len(headers)}. "
        "The header band selector is out of step with the renderer."
    )
    return headers


def _header_lines(element: str) -> list[str]:
    tspans = re.findall(r"<tspan\b[^>]*>([^<]*)</tspan>", element)
    return tspans or [re.sub(r"<[^>]+>", "", element)]


class TestHeaderWrap:
    def test_headers_are_never_abbreviated(self, make_chart):
        """No header label is cut short with an ellipsis at this width.

        The theme ships ``charts.table.header.overflow: wrap-two``; the renderer
        must honor it rather than inheriting the board title's ``truncate``.
        """
        svg = _render(make_chart)
        truncated = [el for el in _header_elements(svg) if "…" in el]
        assert not truncated, (
            f"Header labels truncated instead of wrapping: {truncated}"
        )

    def test_wrapped_header_emits_one_tspan_per_line(self, make_chart):
        """A header too wide for its column splits across stacked tspans."""
        svg = _render(make_chart)
        multiline = [
            el for el in _header_elements(svg) if len(re.findall(r"<tspan\b", el)) >= 2
        ]
        assert multiline, (
            "Expected at least one header wrapped across 2+ tspans; "
            f"headers={_header_elements(svg)}"
        )

    def test_headers_never_break_mid_word(self, make_chart):
        """Column widths honor the min-word floor, so no line ends mid-token.

        Without the floor a narrow numeric column renders ``Conver`` / ``ted in…``
        — a break no overflow mode can undo, unlike a word-boundary wrap.
        """
        vocabulary = {
            word.lower() for label in _LABELS.values() for word in label.split()
        }
        for element in _header_elements(svg := _render(make_chart)):
            for line in _header_lines(element):
                for token in line.rstrip("…").split():
                    assert token.lower() in vocabulary, (
                        f"Header line broke mid-word: {token!r} in {line!r}\n{svg}"
                    )

    def test_wrapped_headers_keep_every_word(self, make_chart):
        """Wrapping must not drop content the way truncation does."""
        svg = _render(make_chart)
        # Headers are title-cased by the theme, so compare case-insensitively.
        rendered = " ".join(_header_elements(svg)).lower()
        for word in ("recent", "mature", "mqls", "prior", "converted"):
            assert word in rendered, f"Header word {word!r} missing from {rendered!r}"


# One wide-content/short-header column plus seven of the inverse — the shape
# that made the text column swell while every numeric column sat below its own
# label. Every call below passes `word_floors`, because the render path always
# does (table.py) and floors interact with the header-width growth.
_CONTENT_DEMANDS = dict.fromkeys(_LABELS, 40.0) | {"metric": 168.0}
_HEADER_DEMANDS = {
    "metric": 108.0,
    "recent_eligible": 110.0,
    "recent_converted": 83.0,
    "recent_rate": 74.0,
    "prior_eligible": 98.0,
    "prior_converted": 83.0,
    "prior_rate": 68.0,
    "change": 40.0,
}
_WORD_FLOORS = dict.fromkeys(_LABELS, 45.0) | {"metric": 75.0}


def _allocate(width, *, floors=_WORD_FLOORS):
    from dbt_charts.core.render.chart.table_support import calculate_column_layout

    return calculate_column_layout(
        list(_LABELS),
        {},
        width,
        demands=_CONTENT_DEMANDS,
        header_demands=_HEADER_DEMANDS,
        word_floors=floors,
    )


class TestColumnBudget:
    def test_surplus_goes_to_header_width_before_content_width(self):
        """No column sits below its header while another holds spare width.

        Scaling by content demand alone hands the surplus to whichever column has
        the widest *values*, so the text column swells past what it needs while
        numeric columns land under their own labels.
        """
        widths, _, total = _allocate(_WIDTH)
        for col, header_width in _HEADER_DEMANDS.items():
            assert widths[col] >= header_width, (
                f"{col} allocated {widths[col]:.1f}px, below its {header_width}px "
                f"header while the budget still had room: {widths}"
            )
        assert total == pytest.approx(_WIDTH), "Allocation must spend the full budget"

    def test_a_tall_word_floor_does_not_rob_a_column_of_its_header_width(self):
        """A single outsized floor must not drag its siblings back under theirs.

        Enforcing floors as a *corrective pass* after growing columns to their
        header width let the floored column claw width back out of the columns
        that pass had just raised — `b`'s 150px floor is funded proportionally
        from every donor, dropping `a` from 150.7px to ~105px, under its 110px
        header. Folding the floor into the demand means one allocation honors
        both constraints and nothing is undone afterwards.
        """
        from dbt_charts.core.render.chart.table_support import calculate_column_layout

        columns = ["a", "b", "c", "d"]
        widths, _, total = calculate_column_layout(
            columns,
            {},
            400.0,
            demands=dict.fromkeys(columns, 40.0),
            header_demands={"a": 110.0, "b": 40.0, "c": 74.0, "d": 68.0},
            word_floors={"a": 45.0, "b": 150.0, "c": 40.0, "d": 38.0},
        )
        assert widths["b"] >= 150.0, f"Outsized floor not honored: {widths}"
        assert total == pytest.approx(400.0)

        # This budget cannot cover every header (129px of gap, 127px of
        # surplus), so some column must fall short — the invariant is that the
        # shortfall is shared, not that one column is starved to pad another.
        # Under the old two-pass order `a` landed at 105.4px, below its 110px
        # header, while `c` and `d` sat *above* theirs at 75.1 and 69.5.
        need = {"a": 110.0, "b": 150.0, "c": 74.0, "d": 68.0}
        starved = [c for c in columns if widths[c] < need[c] - 0.5]
        padded = [c for c in columns if widths[c] > need[c] + 0.5]
        assert not (starved and padded), (
            f"{starved} left short of their header while {padded} were padded "
            f"beyond what they need: {widths}"
        )

    def test_allocation_spends_the_budget_exactly_when_over_subscribed(self):
        """Under-budget tables scale down without leaking or losing width."""
        _, _, total = _allocate(300.0)
        assert total == pytest.approx(300.0)


class TestSubtitleWrap:
    def test_long_subtitle_wraps_across_lines(self, make_chart):
        svg = _render(make_chart, subtitle=_LONG_SUBTITLE)
        subtitle_els = [
            el for el in _text_elements(svg) if "mature MQLs converted" in el
        ]
        assert len(subtitle_els) == 1, (
            f"Expected one subtitle <text>, got {subtitle_els}"
        )
        lines = re.findall(r"<tspan\b[^>]*>([^<]*)</tspan>", subtitle_els[0])
        assert len(lines) >= 2, f"Subtitle did not wrap: {subtitle_els[0]!r}"

    def test_every_subtitle_line_fits_the_card(self, make_chart):
        """No line may measure wider than the card's content width."""
        svg = _render(make_chart, subtitle=_LONG_SUBTITLE)
        subtitle_el = next(
            el for el in _text_elements(svg) if "mature MQLs converted" in el
        )
        font_size = float(re.search(r'font-size="([\d.]+)"', subtitle_el).group(1))
        family = re.search(r'font-family="([^"]+)"', subtitle_el).group(1)
        measurer = get_font_measurer(family)
        lines = re.findall(r"<tspan\b[^>]*>([^<]*)</tspan>", subtitle_el) or [
            re.sub(r"<[^>]+>", "", subtitle_el)
        ]
        # compute_title_limit subtracts outer padding from both edges — assert
        # the limit the code enforces, not the looser full-card width.
        padding = int(_BOARD_STYLE.chart_defaults.table.outer_padding)
        limit = _WIDTH - 2 * padding
        for line in lines:
            assert measurer.measure(line, font_size) <= limit, (
                f"Subtitle line overflows the {limit}px content width: {line!r}"
            )

    def test_subtitle_lines_cover_the_authored_text(self, make_chart):
        svg = _render(make_chart, subtitle=_LONG_SUBTITLE)
        subtitle_el = next(
            el for el in _text_elements(svg) if "mature MQLs converted" in el
        )
        joined = " ".join(re.findall(r"<tspan\b[^>]*>([^<]*)</tspan>", subtitle_el))
        assert "+6.5 points" in joined, f"Subtitle tail dropped: {joined!r}"


class TestSizerAgreesWithRenderer:
    """The layout sizer must never reserve less height than the renderer paints.

    Under the old ``truncate`` default headers were always one line, so the flat
    ``header.height`` the sizer reserved happened to be enough. With ``wrap-two``
    as the effective default a header wider than its column takes two lines; if
    the sizer still reserved one, the explicit-slot path would subtract too
    little chrome and drop rows or paginate early.
    """

    def test_estimated_height_covers_the_rendered_height(self, make_chart):
        """End-to-end: the slot the sizer asks for fits what the renderer draws."""
        from unittest.mock import MagicMock

        from dbt_charts.core.execute.executor import Executor
        from dbt_charts.core.render.layout_sizing import _get_table_height_from_data

        chart = _chart(make_chart)
        executor = MagicMock(spec=Executor)
        executor.execute_chart.return_value = [_NARROW_ROW]

        resolved = resolve(chart, [_NARROW_ROW], chart_style_context=_BOARD_CTX)
        estimated = _get_table_height_from_data(
            chart,
            resolved,
            executor,
            {},
            card_padding=float(_BOARD_STYLE.frame.card_padding),
            board_style=_BOARD_STYLE,
            width=_WIDTH,
        )
        svg = render_table_svg(
            resolved,
            [_NARROW_ROW],
            width=_WIDTH,
            board_style=_BOARD_STYLE,
        )
        painted = float(re.search(r'<svg[^>]+height="([\d.]+)"', svg).group(1))
        assert estimated >= painted, (
            f"Sizer reserves {estimated}px but the renderer paints {painted}px — "
            "the explicit-slot path would drop rows or paginate early."
        )

    def test_reservation_covers_a_two_line_header_band(self):
        """The reserved band is never smaller than the renderer's wrapped band.

        ``resolve_wrapped_headers`` returns ``max(header.height, padding*2 +
        lines*line_height)``. Reserving the flat ``header.height`` was enough
        while headers were always one line; with ``wrap-two`` as the effective
        default the two-line result can exceed it, and the sizer has to cover
        that or the explicit-slot path subtracts too little chrome.
        """
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.render.chart.table_support import (
            reserve_header_band,
            resolve_wrapped_headers,
        )

        tc = _BOARD_STYLE.chart_defaults.table
        body = int(tc.font.size)
        header_font = FontStyle(
            size=float(tc.header.font.size or body),
            family=tc.font.family,
            weight=tc.header.font.weight,
            case=tc.header.font.case,
        )
        # Columns far too narrow for their labels — forces the two-line branch.
        columns = list(_LABELS)
        _, _truncated, rendered_band = resolve_wrapped_headers(
            columns,
            {},
            dict.fromkeys(columns, 60.0),
            header_overflow="wrap-two",
            header_height=float(tc.header.height),
            header_font=header_font,
            padding=int(tc.outer_padding),
            table_config=tc,
            measurer=get_font_measurer(tc.font.family),
        )
        assert reserve_header_band(tc, body) >= rendered_band, (
            "Sizer reserves less than the renderer's wrapped header band"
        )

    def test_hidden_header_reserves_nothing(self):
        from dbt_charts.core.render.chart.table_support import reserve_header_band

        tc = _BOARD_STYLE.chart_defaults.table
        hidden = tc.model_copy(
            update={"header": tc.header.model_copy(update={"visible": False})}
        )
        assert reserve_header_band(hidden, 14) == 0


class TestMixedBranchFloors:
    """Folding floors into the demand changes the mixed branch too.

    ``auto_demands`` feeds ``_classify_columns``, the headroom factor, and the
    text budget — so a header word floor now shifts all three, not just the
    all-compact path the reported bug lived on.
    """

    def test_header_floor_reclassifies_a_column_as_text(self):
        """A header token wider than the compact ceiling makes its column text.

        Compact columns are pinned to their demand; text columns compete for the
        remaining budget. A column whose *label* needs more than the ceiling is
        no longer compact, so it must join the text pool rather than pin wide.
        """
        from dbt_charts.core.render.chart.table_support import (
            _COMPACT_DEMAND_CEILING,
            _classify_columns,
            calculate_column_layout,
        )

        columns = ["narrow", "wide_label"]
        demands = {"narrow": 40.0, "wide_label": 40.0}
        floors = {"narrow": 40.0, "wide_label": _COMPACT_DEMAND_CEILING + 50.0}

        # Without the floor folded in, both columns look compact.
        assert _classify_columns(demands, {}) == ({"narrow", "wide_label"}, set())

        widths, _, total = calculate_column_layout(
            columns, {}, 600.0, demands=demands, word_floors=floors
        )
        assert widths["wide_label"] >= floors["wide_label"], (
            "A column whose header token exceeds the compact ceiling must still "
            f"get its floor: {widths}"
        )
        assert total == pytest.approx(600.0)

    def test_a_floor_is_clamped_to_max_width_before_becoming_demand(self):
        """Folding a floor in must not let it outrank a declared ``max_width``.

        A 300px floor on a column capped at 60px contributes 60px of demand, not
        300 — so the allocation is identical to one where the floor *was* 60.

        (Separately, the all-compact branch's proportional residual can still
        grow a capped column past its ``max_width``. That gap predates this
        change — verified against the base commit — and is filed as follow-up
        rather than fixed here.)
        """
        from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
        from dbt_charts.core.render.chart.table_support import calculate_column_layout

        def widths_for(floor: float) -> dict[str, float]:
            result, _, _ = calculate_column_layout(
                ["capped", "other"],
                {"capped": TableColumnConfig(max_width=60)},
                600.0,
                demands={"capped": 40.0, "other": 40.0},
                word_floors={"capped": floor, "other": 40.0},
            )
            return result

        assert widths_for(300.0) == widths_for(60.0), (
            "A word floor above max_width changed the allocation — it was "
            "applied raw instead of being clamped at the cap."
        )
