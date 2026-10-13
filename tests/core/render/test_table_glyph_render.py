"""Render-level tests for colored glyph tspans in table cells."""

from __future__ import annotations

import re

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.authored import (
    ConditionalRule,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.render.chart.table_support import resolve_conditional_styles

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def _glyph_tspan_pattern(glyph: str, color: str) -> re.Pattern[str]:
    return re.compile(
        rf'<tspan[^>]*fill="{re.escape(color)}"[^>]*>{re.escape(glyph)} ?</tspan>'
    )


class TestGlyphRendersInSvg:
    """Glyphs from when-rules render as colored tspans inside table cells."""

    def test_negative_row_emits_red_down_arrow(self, make_chart) -> None:
        from .._engine_render import render_table_svg

        chart = make_chart(
            "table",
            title="Growth",
            conditional_formatting={
                "growth": {
                    "when": [
                        {"lt": 0, "glyph": "▼", "glyph_color": "#dc2626"},
                        {"gt": 0, "glyph": "▲", "glyph_color": "#16a34a"},
                    ]
                }
            },
            style={
                "columns": {
                    "company": {},
                    "growth": {"format": ".0%"},
                }
            },
        )
        data = [
            {"company": "Apex", "growth": 0.12},
            {"company": "Beta", "growth": -0.05},
            {"company": "Gamma", "growth": 0.20},
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        assert _glyph_tspan_pattern("▼", "#dc2626").search(svg), (
            "Expected a red ▼ tspan for the negative growth row"
        )
        assert _glyph_tspan_pattern("▲", "#16a34a").search(svg), (
            "Expected a green ▲ tspan for positive growth rows"
        )

    def test_tone_glyph_renders_in_theme_tone_color(self, make_chart) -> None:
        """A `tone:` rule (no explicit glyph_color) renders the glyph in the
        theme's resolved tone color — the full authored→render path, with the
        tones threaded through render_table_svg (not just the resolver)."""
        from .._engine_render import render_table_svg

        board_rs = resolve_style(get_theme_style())
        board_ctx = resolve_chart_style_context(get_theme_style())
        # Read the tone hex from the same resolved style the renderer threads,
        # so this pins propagation — not a hardcoded theme literal.
        negative_hex = board_ctx.tones.negative

        chart = make_chart(
            "table",
            title="Growth",
            conditional_formatting={
                "growth": {"when": [{"lt": 0, "glyph": "▼", "tone": "negative"}]}
            },
            style={"columns": {"company": {}, "growth": {"format": ".0%"}}},
        )
        data = [
            {"company": "Apex", "growth": 0.12},
            {"company": "Beta", "growth": -0.05},
        ]
        chart = resolve(chart, data, chart_style_context=board_ctx)
        svg = render_table_svg(chart, data, width=600, board_style=board_rs)
        assert _glyph_tspan_pattern("▼", negative_hex).search(svg), (
            "A `tone: negative` rule must render its glyph in the theme's "
            "resolved negative tone color, threaded through render_table_svg"
        )

    def test_static_column_glyph_renders_on_every_row(self, make_chart) -> None:
        from .._engine_render import render_table_svg

        chart = make_chart(
            "table",
            title="Status",
            style={
                "columns": {
                    "company": {},
                    "growth": {
                        "format": ".0%",
                        "glyph": "●",
                        "glyph_color": "#888888",
                    },
                }
            },
        )
        data = [
            {"company": "Apex", "growth": 0.12},
            {"company": "Beta", "growth": -0.05},
            {"company": "Gamma", "growth": 0.20},
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        matches = _glyph_tspan_pattern("●", "#888888").findall(svg)
        assert len(matches) >= 3, (
            f"Expected at least 3 ● tspans (one per row), got {len(matches)}"
        )


class TestGlyphOnNonNumericCells:
    """Glyphs on text/null/date cells must render, not silently disappear."""

    def test_static_glyph_on_text_column(self, make_chart) -> None:
        from .._engine_render import render_table_svg

        chart = make_chart(
            "table",
            title="Status",
            style={
                "columns": {
                    "status": {
                        "glyph": "●",
                        "glyph_color": "#16a34a",
                    },
                    "company": {},
                }
            },
        )
        data = [
            {"status": "OK", "company": "Apex"},
            {"status": "Stale", "company": "Beta"},
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        assert _glyph_tspan_pattern("●", "#16a34a").search(svg), (
            "Glyph configured on a text column must still render, not silently drop"
        )

    def test_when_glyph_on_null_value(self, make_chart) -> None:
        from .._engine_render import render_table_svg

        chart = make_chart(
            "table",
            title="Missing",
            conditional_formatting={
                "amount": {
                    "when": [
                        {
                            "is_null": True,
                            "glyph": "—",
                            "glyph_color": "#9ca3af",
                        }
                    ]
                }
            },
            style={
                "columns": {
                    "company": {},
                    "amount": {"format": "$,.0f"},
                }
            },
        )
        data = [
            {"company": "Apex", "amount": 100},
            {"company": "Beta", "amount": None},
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=resolve_style(get_theme_style()),
        )
        assert _glyph_tspan_pattern("—", "#9ca3af").search(svg), (
            "is_null + glyph rule must render the glyph for the missing cell"
        )


_CELL_RE = re.compile(
    r'<rect x="(?P<rx>[\d.]+)" y="[\d.]+" width="(?P<rw>[\d.]+)" height="[\d.]+" '
    r'fill="(?P<fill>#[0-9a-fA-F]+)"/>'
    r'(?P<el><text x="(?P<tx>[\d.]+)" .*?</text>)'
)
_FONT_SIZE_RE = re.compile(r'font-size="([\d.]+)"')
_FONT_FAMILY_RE = re.compile(r'font-family="([^"]+)"')
# The lookahead is load-bearing: a wrapped glyph cell's first line nests the
# glyph's own <tspan>, so a plain non-greedy match would stop at that inner
# close and silently drop the rest of the line.
_LINE_TSPAN_RE = re.compile(
    r'<tspan x="[\d.]+" y="[\d.]+">(.*?)</tspan>(?=<tspan x="|$)'
)
_INNER_TAG_RE = re.compile(r"</?tspan[^>]*>")


class _GlyphCell:
    """One rendered cell: its fill rect and the text lines painted over it.

    Handles both cell shapes the renderer emits — a single `<text>` run and
    the wrapped form whose lines are positioned `<tspan>`s — so a test can
    assert containment without caring which one it got.
    """

    def __init__(self, match: re.Match[str]) -> None:
        from dbt_charts.core.font_measure import get_font_measurer

        el = match.group("el")
        self.fill = match.group("fill")
        self.fill_right = float(match.group("rx")) + float(match.group("rw"))
        self.x = float(match.group("tx"))
        font_size = _FONT_SIZE_RE.search(el)
        font_family = _FONT_FAMILY_RE.search(el)
        assert font_size and font_family, f"cell markup lost its font: {el}"
        self.font_size = float(font_size.group(1))
        self.measurer = get_font_measurer(font_family.group(1))
        body = el[el.index(">") + 1 : -len("</text>")]
        raw_lines = _LINE_TSPAN_RE.findall(body) or [body]
        self.lines = [_INNER_TAG_RE.sub("", line) for line in raw_lines]

    @property
    def has_glyph(self) -> bool:
        return bool(self.lines) and self.lines[0][:1] in {"●", "○", "▲", "▼", "⚠", "—"}

    @property
    def text_right(self) -> float:
        return self.x + max(
            self.measurer.measure(line, self.font_size) for line in self.lines
        )


def _glyph_cells(svg: str) -> list[_GlyphCell]:
    cells = [_GlyphCell(m) for m in _CELL_RE.finditer(svg)]
    return [c for c in cells if c.has_glyph]


_OK_RULE = ConditionalRule.model_validate(
    {"eq": "OK", "glyph": "○", "glyph_color": "#888888"}
)
_UNDETERMINED_RULE = ConditionalRule.model_validate(
    {"eq": "UNDETERMINED", "glyph": "○", "glyph_color": "#888888"}
)
_GLYPH_RULES = (
    ConditionalRule.model_validate(
        {"eq": "INSUFFICIENT", "glyph": "○", "glyph_color": "#888888"}
    ),
    _OK_RULE,
)
_GLYPH_RULES_ACT = (
    ConditionalRule.model_validate(
        {"eq": "ACT NOW", "glyph": "●", "glyph_color": "#aa3333"}
    ),
)

_MEASURE_ARGS: dict[str, object] = {
    "columns": ["overall"],
    "column_configs": {},
    "data": [{"overall": "INSUFFICIENT"}],
    "measurer": get_font_measurer(),
    "header_measurer": get_font_measurer(),
    "font_size": 13.0,
    "header_font_size": 12.0,
    "cell_pad": 8,
}


class TestGlyphWidthIsReserved:
    """A glyph is painted before the value, so the column must be sized for it.

    Without the reservation the column is sized to the bare value and every
    widest cell spills past its own background fill into the next column.
    """

    def test_glyph_cell_text_fits_inside_its_background(self, make_chart) -> None:
        from .._engine_render import render_table_svg

        chart = make_chart(
            "table",
            title="Motion health",
            conditional_formatting={
                "overall": {
                    "when": [
                        {
                            "eq": "INSUFFICIENT",
                            "glyph": "○",
                            "glyph_color": "#888888",
                            "background": "#eeeeec",
                        },
                        {
                            "eq": "INVESTIGATE",
                            "glyph": "●",
                            "glyph_color": "#8a6d1f",
                            "background": "#f7f0dd",
                        },
                    ]
                }
            },
            style={
                "columns": {"region": {}, "motion": {}, "overall": {}, "signal": {}}
            },
        )
        data = [
            {
                "region": "AMER",
                "motion": "NB Enterprise",
                "overall": "INSUFFICIENT",
                "signal": "Close 104%; generation pending for this motion",
            },
            {
                "region": "EMEA",
                "motion": "Commercial Expansion",
                "overall": "INVESTIGATE",
                "signal": "MQLs -63%, stage 1 dollars -14%; close 84%",
            },
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=700, board_style=resolve_style(get_theme_style())
        )

        cells = _glyph_cells(svg)
        assert len(cells) == 2, f"Expected 2 glyph cells to check, found {len(cells)}"
        for cell in cells:
            assert len(cell.lines) == 1, (
                f"{cell.lines} wrapped — a status word must stay whole"
            )
            assert cell.text_right <= cell.fill_right, (
                f"{cell.lines[0]!r} ends at {cell.text_right:.1f} but its "
                f"{cell.fill} fill ends at {cell.fill_right:.1f} — the glyph run "
                "was not reserved when the column was measured"
            )

    def test_a_column_too_narrow_for_the_glyph_still_wraps(self, make_chart) -> None:
        """The glyph subtraction must not push a cell out of the wrap cache.

        A missing cache entry is painted as one unwrapped, untruncated line —
        a spill across several neighbors. Nothing fits inside a column this
        narrow, so the guarantee here is the cache entry (one character per
        line, the same shape a glyph-free column of this width produces), not
        containment.
        """
        from .._engine_render import render_table_svg

        columns = [f"c{i}" for i in range(22)]
        chart = make_chart(
            "table",
            title="Narrow",
            conditional_formatting={
                "c0": {
                    "when": [
                        {"eq": "ACT NOW", "glyph": "●", "background": "#f7f0dd"},
                    ]
                }
            },
            style={"columns": dict.fromkeys(columns, {})},
        )
        data = [dict.fromkeys(columns, "ACT NOW")]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=300, board_style=resolve_style(get_theme_style())
        )

        cells = _glyph_cells(svg)
        assert len(cells) == 1, f"Expected one glyph cell, found {len(cells)}"
        cell = cells[0]
        assert len(cell.lines) > 1, (
            f"{cell.lines} was painted as one unwrapped line — the glyph "
            "subtraction dropped the cell out of the wrap cache, whose "
            "missing-entry path neither wraps nor truncates"
        )

    def test_truncation_reserves_the_glyph_run(self, make_chart) -> None:
        """With wrap off, the ellipsis budget is what the glyph leaves behind."""
        from .._engine_render import render_table_svg

        chart = make_chart(
            "table",
            title="Status",
            conditional_formatting={
                "overall": {
                    "when": [
                        {
                            "eq": "INSUFFICIENT",
                            "glyph": "○",
                            "glyph_color": "#888888",
                            "background": "#eeeeec",
                        }
                    ]
                }
            },
            style={
                "wrap": False,
                "columns": {"overall": {"width": 70}, "signal": {}},
            },
        )
        data = [{"overall": "INSUFFICIENT", "signal": "Close 104%; generation pending"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart, data, width=400, board_style=resolve_style(get_theme_style())
        )

        cells = _glyph_cells(svg)
        assert len(cells) == 1, f"Expected one glyph cell, found {len(cells)}"
        cell = cells[0]
        assert cell.lines[0].endswith("…"), (
            "A 70px column cannot hold '○ INSUFFICIENT'; the value must be "
            f"ellipsized, got {cell.lines!r}"
        )
        assert cell.text_right <= cell.fill_right, (
            f"{cell.lines[0]!r} ends at {cell.text_right:.1f}, past its fill at "
            f"{cell.fill_right:.1f} — truncation measured against the full "
            "content area rather than what the glyph leaves"
        )


class TestGlyphInOverrides:
    """Glyph keys round-trip through resolve_conditional_styles."""

    def test_glyph_propagation(self) -> None:
        rules = [
            ConditionalRule.model_validate(
                {"lt": 0, "glyph": "▼", "glyph_color": "#dc2626"}
            )
        ]
        out = resolve_conditional_styles(rules, -1)
        assert out["glyph"] == "▼"
        assert out["glyph_color"] == "#dc2626"
