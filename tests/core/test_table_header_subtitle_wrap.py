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
from types import SimpleNamespace

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.primitives import BoardLinks
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.font_measure import get_font_measurer

from ._engine_render import render_table_svg

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
# Below the table's natural width (~836px): headers wrap only when the table is
# short on room — a table with room for every column keeps headers on one line.
_SHORT_WIDTH = 700


def _chart(make_chart, *, subtitle: str | None = None):
    extra = {"subtitle": subtitle} if subtitle is not None else {}
    return make_chart(
        "table",
        x=None,
        y=None,
        title=_TITLE,
        style={
            # Header wrapping is what a slot imposes under `fit`.
            "column_layout": {"overflow": "fit"},
            "columns": {col: {"label": label} for col, label in _LABELS.items()},
        },
        **extra,
    )


def _render(make_chart, *, subtitle: str | None = None, width: float = _WIDTH) -> str:
    chart = _chart(make_chart, subtitle=subtitle)
    data = [_NARROW_ROW]
    return render_table_svg(
        resolve(chart, data, chart_style_context=_BOARD_CTX),
        data,
        width=width,
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
        svg = _render(make_chart, width=_SHORT_WIDTH)
        truncated = [el for el in _header_elements(svg) if "…" in el]
        assert not truncated, (
            f"Header labels truncated instead of wrapping: {truncated}"
        )

    def test_wrapped_header_emits_one_tspan_per_line(self, make_chart):
        """A header too wide for its column splits across stacked tspans."""
        svg = _render(make_chart, width=_SHORT_WIDTH)
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
        for element in _header_elements(svg := _render(make_chart, width=_SHORT_WIDTH)):
            for line in _header_lines(element):
                for token in line.rstrip("…").split():
                    assert token.lower() in vocabulary, (
                        f"Header line broke mid-word: {token!r} in {line!r}\n{svg}"
                    )

    def test_wrapped_headers_keep_every_word(self, make_chart):
        """Wrapping must not drop content the way truncation does."""
        svg = _render(make_chart, width=_SHORT_WIDTH)
        # Headers are title-cased by the theme, so compare case-insensitively.
        rendered = " ".join(_header_elements(svg)).lower()
        for word in ("recent", "mature", "mqls", "prior", "converted"):
            assert word in rendered, f"Header word {word!r} missing from {rendered!r}"


# One wide-content/short-header column plus seven of the inverse — the shape
# that made the text column swell while every numeric column sat below its own
# label. Every call below passes `word_floors`, because the render path always
# does and floors interact with the header-width growth.
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
        from dbt_charts.core.render.board_variables import root_board_width
        from dbt_charts.core.render.layout_sizing import _get_table_height_from_data

        chart = _chart(make_chart)
        executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
        executor.execute_chart.return_value = [_NARROW_ROW]

        resolved = resolve(chart, [_NARROW_ROW], chart_style_context=_BOARD_CTX)
        with root_board_width(_WIDTH):
            estimated = _get_table_height_from_data(
                chart,
                resolved,
                executor,
                {},
                card_padding=float(_BOARD_STYLE.frame.card_padding),
                board_style=_BOARD_STYLE,
                width=_WIDTH,
            )
        # The sizer measures inside the card padding; paint at the same width.
        svg = render_table_svg(
            resolved,
            [_NARROW_ROW],
            width=_WIDTH - 2 * float(_BOARD_STYLE.frame.card_padding),
            board_style=_BOARD_STYLE,
        )
        painted = float(re.search(r'<svg[^>]+height="([\d.]+)"', svg).group(1))
        assert estimated >= painted, (
            f"Sizer reserves {estimated}px but the renderer paints {painted}px — "
            "the explicit-slot path would drop rows or paginate early."
        )
