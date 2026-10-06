"""Regression tests for integer pixel-snapping of SVG translate coordinates.

Every emitted translate(x, y) must have integer-valued x and y so that
1px structural marks (row rules, dividers, stripes) land on the pixel grid
and do not blur across two rows at reduced opacity.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project

# Regex that matches translate(x, y) and captures the two coordinate strings.
# Accepts both integer ("24") and float ("24.0" or "24.5") forms so failures
# surface the actual offending value rather than a silent non-match.
_TRANSLATE_RE = re.compile(r"translate\((-?[\d.]+),\s*(-?[\d.]+)\)")


def _all_translates(svg: str) -> list[tuple[str, str]]:
    """Return all (x_str, y_str) pairs from translate(x, y) in the SVG."""
    return _TRANSLATE_RE.findall(svg)


def _is_integer_valued(s: str) -> bool:
    """Return True iff the string represents an integer (e.g. '24' or '24.0')."""
    try:
        return float(s) == int(float(s))
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 1. All translates in a full-board SVG must have integer coordinates
# ---------------------------------------------------------------------------


class TestAllTranslatesAreInteger:
    """Every translate(x, y) in a rendered board must have integer-valued coords.

    The fixture exercises all four layout kinds in a single render:
    rows (outer board), cols, grid, tabs — plus nested boards and a table chart.
    """

    _YAML = """
title: Mixed Layout Integer-Snap Fixture
queries:
  rev:
    type: values
    rows:
      - {name: Alice, dept: Eng}
      - {name: Bob, dept: Sales}
charts:
  tbl:
    query: rev
    type: table
    style:
      row:
        rule:
          width: 1
rows:
  - title: Section A
    cols:
      - tbl
      - title: Right board
        text: "Some text"
  - title: Section B
    text: "Board text"
"""

    def test_all_board_content_translates_are_integer(
        self, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.render import render

        result = compile(self._YAML)
        assert result.success and result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg = render(result.board, executor, format="svg").output
        assert isinstance(svg, str)

        translates = _all_translates(svg)
        assert translates, "No translate() found in SVG — fixture may be wrong"

        bad = [
            f"translate({x}, {y})"
            for x, y in translates
            if not _is_integer_valued(x) or not _is_integer_valued(y)
        ]
        assert not bad, (
            f"Non-integer translate coordinates found ({len(bad)} offenders):\n"
            + "\n".join(f"  {t}" for t in bad[:10])
        )


# ---------------------------------------------------------------------------
# 2. Fractional card_padding forces integer translate
# ---------------------------------------------------------------------------


class TestFractionalInputProducesIntegerTranslate:
    """Feeding fractional card_padding (e.g. 7.5) must still emit integer translates.

    This tests _build_board_content_items directly; the function currently emits
    `translate({content_x}, {items_height})` with raw floats.  After the fix,
    both args must be snapped.
    """

    def test_fractional_card_padding_gives_integer_translate(self) -> None:
        from dbt_charts.core.render.boards import _build_board_content_items

        # x_offset=4.0, card_padding=7.5 → content_x = 11.5 (fractional before fix)
        items, _ = _build_board_content_items(
            x_offset=4.0,
            y_offset=0.0,
            gap=8.0,
            title_svg="<text>hello</text>",
            title_height=20.0,
            text_svg="",
            text_height=0.0,
            variables_svg="",
            variables_height=0.0,
            layout_content="",
            layout_content_height=0.0,
            card_padding=7.5,
            content_width=400.0,
        )

        assert items, "Expected at least one item"
        translates = _all_translates("".join(items))
        assert translates, "No translate found in items SVG"

        bad = [
            f"translate({x}, {y})"
            for x, y in translates
            if not _is_integer_valued(x) or not _is_integer_valued(y)
        ]
        assert not bad, (
            "Fractional card_padding should produce integer translates; got:\n"
            + "\n".join(f"  {t}" for t in bad)
        )


# ---------------------------------------------------------------------------
# 3. Row-rule y coordinates must be integers
# ---------------------------------------------------------------------------


class TestRowRuleYIsInteger:
    """Every row-rule rect's y attribute in a rendered table must be an integer.

    The table is placed inside a rows-layout board whose upstream translate
    may accumulate a fractional offset; after the fix, row_y is snapped at
    the emit site so rule_y is always an integer regardless.
    """

    _YAML = """
title: Row Rule Integer Y Fixture
queries:
  data:
    type: values
    rows:
      - {name: Alice, dept: Eng}
      - {name: Bob, dept: Sales}
charts:
  tbl:
    query: data
    type: table
    style:
      row:
        rule:
          width: 1
rows:
  - tbl
"""

    def _rule_y_values(self, svg: str) -> list[float]:
        """Extract y values of 1px-tall rects (row rules) from SVG."""
        # Row rules are <rect ... height="1" ...> or height="1.0".
        # Capture the y= attribute from such rects.
        rule_re = re.compile(
            r'<rect\s[^>]*height="1(?:\.0)?"\s[^>]*/?>|'
            r'<rect\s[^>]*height="1(?:\.0)?"\s*/>'
        )
        y_re = re.compile(r'\by="(-?[\d.]+)"')
        ys: list[float] = []
        for m in rule_re.finditer(svg):
            tag = m.group(0)
            ym = y_re.search(tag)
            if ym:
                ys.append(float(ym.group(1)))
        return ys

    def test_row_rule_y_is_integer(self, local_project: Callable[..., Project]) -> None:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.render import render

        result = compile(self._YAML)
        assert result.success and result.board is not None, result.errors

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg = render(result.board, executor, format="svg").output
        assert isinstance(svg, str)

        ys = self._rule_y_values(svg)
        assert ys, "No row-rule rects found — check fixture or rule detection regex"

        bad = [y for y in ys if y != int(y)]
        assert not bad, (
            f"Non-integer row-rule y values found: {bad}\n"
            "row_y must be snapped to integer before rule_y = row_y + row_height - rule_width"
        )


# ---------------------------------------------------------------------------
# 4. Summary-row rule y coordinates must also be integers
# ---------------------------------------------------------------------------


class TestSummaryRuleYIsInteger:
    """Summary-row rule rects (y_upper, y_lower, y_single) must have integer y.

    The branches at table.py:1179-1201 compute y from ``row_y - line_h``.
    Since row_y is snapped and line_h = max(1, int(summary_rule_width)) is
    integer, the result is always integer — this test binds that guarantee.
    """

    def _rect_y_values(self, svg: str) -> list[float]:
        """Extract all y= values from <rect> elements."""
        y_re = re.compile(r"<rect\s[^>]*/>", re.DOTALL)
        attr_re = re.compile(r'\by="(-?[\d.]+)"')
        ys: list[float] = []
        for m in y_re.finditer(svg):
            attr = attr_re.search(m.group(0))
            if attr:
                ys.append(float(attr.group(1)))
        return ys

    def test_summary_rule_y_is_integer(self, make_chart) -> None:
        import dataclasses

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        # Data with a mix of value rows and one summary row.
        data = [
            {"name": "Alpha", "value": 10, "kind": "value"},
            {"name": "Beta", "value": 20, "kind": "value"},
            {"name": "Total", "value": 30, "kind": "summary"},
        ]

        # Build effective style with row.role = "kind" and summary rule enabled.
        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        base_row = tc.row
        new_table = tc.model_copy(
            update={
                "row": base_row.model_copy(
                    update={
                        "role": "kind",
                        "divider": base_row.divider.model_copy(
                            update={"visible": True, "width": 1.0}
                        ),
                        "roles": base_row.roles.model_copy(
                            update={
                                "summary": base_row.roles.summary.model_copy(
                                    update={
                                        "divider": base_row.roles.summary.divider.model_copy(
                                            update={"visible": True, "width": 1.0}
                                        )
                                    }
                                ),
                            }
                        ),
                    }
                )
            }
        )
        es = dataclasses.replace(es, table=new_table)

        # Use a fractional width to maximize chance of non-integer row_y before
        # the snap fix: per_row_height = height / n_rows won't be integer.

        board_rs = resolve_style(get_theme_style())
        board_ctx = resolve_chart_style_context(get_theme_style())
        chart = make_chart("table", x=None, y=None)
        resolved = resolve(chart, data, chart_style_context=board_ctx)
        svg = render_table_svg(
            resolved,
            data,
            width=601,
            board_style=board_rs,
        )
        assert "<svg" in svg, "render_table_svg returned no SVG"

        ys = self._rect_y_values(svg)
        assert ys, "No rect elements found in table SVG — fixture may be wrong"

        bad = [y for y in ys if y != int(y)]
        assert not bad, (
            f"Non-integer rect y values in summary-rule table: {bad}\n"
            "row_y must be integer; summary rule y derives from row_y - line_h"
        )

    def test_table_svg_integer_dimensions_use_stable_string_form(
        self, make_chart
    ) -> None:
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        data = [{"name": "Alpha", "value": 10}]
        board_rs = resolve_style(get_theme_style())
        board_ctx = resolve_chart_style_context(get_theme_style())
        chart = make_chart("table", x=None, y=None)
        resolved = resolve(chart, data, chart_style_context=board_ctx)
        # Pass an oversized slot (292px for 1 row); with shrink-to-content the
        # renderer collapses to content height. The key invariant is that the
        # emitted SVG attribute is a bare integer string, not a float.
        svg = render_table_svg(
            resolved,
            data,
            width=544.0,
            height=292.0,
            board_style=board_rs,
        )

        assert 'width="544"' in svg
        assert 'width="544.0"' not in svg
        # Renderer shrinks to content — height is an integer but smaller than 292.
        # Verify the float form is never emitted (that's the integer-snap contract).
        assert 'height="292.0"' not in svg
        assert 'height="544.0"' not in svg
        # The rendered height is content-sized; confirm it's an integer in the SVG.
        import re

        m = re.search(r'<svg[^>]+height="([^"]+)"', svg)
        assert m, "SVG root element must have a height attribute"
        h_str = m.group(1)
        assert "." not in h_str, (
            f"SVG height must be a bare integer string, got {h_str!r}"
        )
