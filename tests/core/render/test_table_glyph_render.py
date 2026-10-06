"""Render-level tests for colored glyph tspans in table cells."""

from __future__ import annotations

import re

import pytest

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
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

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
        from dbt_charts.core.render.chart import render_table_svg

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
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

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
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

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
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

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
        from dbt_charts.core.render.chart.table import render_table_svg

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
        from dbt_charts.core.render.chart.table import render_table_svg

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

    def test_demand_reserves_the_glyph_run(self) -> None:
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table_support import measure_column_demands

        measurer = get_font_measurer()
        bare, _ = measure_column_demands(**_MEASURE_ARGS, column_when_rules={})
        with_glyph, _ = measure_column_demands(
            **_MEASURE_ARGS, column_when_rules={"overall": _GLYPH_RULES}
        )
        assert with_glyph["overall"] - bare["overall"] == pytest.approx(
            measurer.measure("○ ", 13.0)
        ), (
            "A column whose cells paint a glyph must demand exactly the glyph "
            "run's width more than the same column without one"
        )

    def test_word_floor_reserves_the_glyph_run(self) -> None:
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table_support import (
            measure_column_word_floors,
        )

        measurer = get_font_measurer()
        bare = measure_column_word_floors(**_MEASURE_ARGS, column_when_rules={})
        with_glyph = measure_column_word_floors(
            **_MEASURE_ARGS, column_when_rules={"overall": _GLYPH_RULES}
        )
        assert with_glyph["overall"] - bare["overall"] == pytest.approx(
            measurer.measure("○ ", 13.0)
        ), (
            "The min-word floor must reserve the glyph run — the glyph is glued "
            "to the first word and cannot break away from it"
        )

    def test_word_floor_pairs_each_glyph_with_its_own_cell(self) -> None:
        """The floor is a first-line unit some cell actually paints.

        Here the widest token and the only glyph belong to different cells,
        so pairing the two column-wide maxima would reserve a unit nothing
        renders — width taken straight out of a text neighbor.
        """
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table_support import (
            measure_column_word_floors,
        )

        measurer = get_font_measurer()
        floors = measure_column_word_floors(
            # "glyphed": the glyph sits on the widest cell, so the floor has
            # to grow. "split": the glyph and the widest token belong to
            # different cells, so it must not.
            ["glyphed", "split"],
            {},
            [
                {"glyphed": "UNDETERMINED", "split": "UNDETERMINED"},
                {"glyphed": "OK", "split": "OK"},
            ],
            measurer,
            header_measurer=measurer,
            font_size=13.0,
            header_font_size=13.0,
            cell_pad=0,
            header_visible=False,
            column_when_rules={
                "glyphed": (_UNDETERMINED_RULE,),
                "split": (_OK_RULE,),
            },
        )
        widest = measurer.measure("UNDETERMINED", 13.0)
        run = measurer.measure("○ ", 13.0)
        assert floors["glyphed"] == pytest.approx(widest + run), (
            "The widest cell paints a glyph, so the floor that keeps its word "
            "whole must carry the glyph too"
        )
        assert floors["split"] == pytest.approx(widest), (
            "Only the 'OK' cell paints a glyph; charging the glyph-free "
            "'UNDETERMINED' cell for it reserves a first-line unit nothing "
            "renders and takes the width from a text neighbor"
        )

    def test_overflow_check_sees_the_glyph_run(self) -> None:
        """The fit cascade asks the question with the glyph included.

        A column wide enough for the bare value but not for glyph + value
        must read as overflow, or the cascade concludes the table fits and
        never shrinks the font.
        """
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table import _has_overflow

        measurer = get_font_measurer("Inter Variable")
        font = FontStyle(size=11.0, family="Inter Variable")
        # Exactly the bare value's width plus padding and the fit breath, so
        # only the glyph run can tip it over.
        width = measurer.measure("INSUFFICIENT", 11.0) + 12 + 4
        common = {
            "columns": ["overall"],
            "data": [{"overall": "INSUFFICIENT"}],
            "column_configs": {},
            "col_widths": {"overall": width},
            "cell_pad": 6,
            "cell_font": font,
            "header_font": None,
            "wrap": False,
            "formats": None,
        }
        assert not _has_overflow(**common, column_when_rules={})
        assert _has_overflow(**common, column_when_rules={"overall": _GLYPH_RULES})

    def test_wrap_search_reserves_the_glyph_run(self) -> None:
        """The wrap search runs against what is left after the glyph.

        A cell sized to fit its text exactly must wrap once a glyph shares
        the line, or the first line renders wider than the column.
        """
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table import _compute_wrap_layout

        measurer = get_font_measurer("Inter Variable")
        rows = [{"overall": "ACT NOW"}]
        width = measurer.measure("ACT NOW", 11.0) + 12
        common = {
            "rows": rows,
            "text_columns": ["overall"],
            "column_configs": {},
            "col_widths": {"overall": width},
            "cell_pad": 6,
            "font_size": 11,
            "row_height": 24,
            "text_baseline_offset": 4.0,
            "measurer": measurer,
        }
        _, bare = _compute_wrap_layout(**common, column_when_rules={})
        _, with_glyph = _compute_wrap_layout(
            **common, column_when_rules={"overall": _GLYPH_RULES_ACT}
        )
        assert bare == [{}], "The bare text fits on one line; nothing should wrap"
        assert with_glyph[0]["overall"] == ["ACT", "NOW"], (
            "Once a glyph shares the first line the text no longer fits and "
            f"must wrap; got {with_glyph[0].get('overall')}"
        )

    def test_truncation_reserves_the_glyph_run(self, make_chart) -> None:
        """With wrap off, the ellipsis budget is what the glyph leaves behind."""
        from dbt_charts.core.render.chart.table import render_table_svg

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


def test_word_floor_measures_formatted_text_not_raw_repr() -> None:
    """A float's raw repr is far wider than the text the cell paints."""
    from dbt_charts.core.compile.models.style.authored.table import TableColumnConfig
    from dbt_charts.core.render.chart.table_support import (
        measure_column_word_floors,
    )

    measurer = get_font_measurer()
    floors = measure_column_word_floors(
        **{
            **_MEASURE_ARGS,
            "cell_pad": 0,
            "columns": ["v"],
            "column_configs": {"v": TableColumnConfig(format=".1f")},
            "data": [{"v": -201.10000000000002}],
        },
        column_when_rules={},
    )
    assert floors["v"] == pytest.approx(measurer.measure("\u2212201.1", 13.0))
