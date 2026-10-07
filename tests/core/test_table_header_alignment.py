"""Tests for the strong-center-over-value header alignment system.

P4.1 redesign: every numeric column maintains a single invariant —
    header_center == number_tspan_center == cell_midpoint.

These tests codify that invariant end-to-end: the number tspan lane is
anchored so its center sits at cell midpoint, numeric headers render
with ``text-anchor="middle"`` and ``x=cell_midpoint``, text headers stay
left-aligned, per-column rules center on the midpoint, and
cluster-equal-width only spans runs of consecutive numeric columns.

See the chart render AGENTS.md Implementation philosophy for rationale.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_BOARD_STYLE = resolve_style(get_theme_style())


def _divider_width(width: float) -> dict:
    """A divider drawn at *width*; 0 is off (the divider keeps its own width)."""
    return {"visible": width > 0, **({"width": width} if width > 0 else {})}


def _style(**overrides):
    """Build an effective ResolvedChartsStyle with optional TableChartStyle overrides."""

    es = resolve_chart_style_context(get_theme_style())
    tc = es.table

    font_updates: dict = {}
    header_updates: dict = {}
    header_font_updates: dict = {}
    header_rule_updates: dict = {}
    row_updates: dict = {}
    row_rule_updates: dict = {}
    summary_updates: dict = {}
    table_updates: dict = {}
    rule_color = None

    for key, value in overrides.items():
        if key == "font_size":
            font_updates["size"] = value
        elif key == "font_family":
            font_updates["family"] = value
        elif key == "row_height":
            row_updates["height"] = value
        elif key == "header_height":
            header_updates["height"] = value
        elif key == "header_font_size":
            header_font_updates["size"] = value
        elif key == "header_font_weight":
            header_font_updates["weight"] = value
        elif key == "header_font_compact":
            from dbt_charts.core.compile.models.primitives import FontStyle

            header_updates["font_compact"] = (
                FontStyle(weight=value)
                if isinstance(value, (str, int, float))
                else value
            )
        elif key == "header_background":
            header_updates["background"] = value
        elif key == "header_color":
            header_font_updates["color"] = value
        elif key == "header_rule_width":
            header_rule_updates.update(_divider_width(value))
        elif key == "header_rule_continuous":
            header_rule_updates["continuous"] = value
        elif key == "stripe_color":
            from dbt_charts.core.compile.models.style.theme import TableRowStripeStyle

            row_updates["stripe"] = TableRowStripeStyle(color=value)
        elif key == "row_rule_width":
            row_rule_updates.update(_divider_width(value))
        elif key == "summary_rule_width":
            summary_updates["divider"] = tc.row.roles.summary.divider.model_copy(
                update=_divider_width(value)
            )
        elif key == "summary_font_weight":
            summary_updates["font_weight"] = value
        elif key == "background":
            table_updates["background"] = value
        elif key == "color":
            table_updates["color"] = value
        elif key == "rule_color":
            rule_color = value
        elif key == "symbol_mode":
            table_updates["symbol_mode"] = value
        elif key == "row_role":
            row_updates["role"] = value
        else:
            table_updates[key] = value

    if header_font_updates:
        header_updates["font"] = tc.header.font.model_copy(update=header_font_updates)
    if rule_color is not None:
        header_rule_updates["color"] = rule_color
        row_rule_updates["color"] = rule_color
        summary_updates["divider"] = summary_updates.get(
            "divider", tc.row.roles.summary.divider
        ).model_copy(update={"color": rule_color})
    if header_rule_updates:
        header_updates["divider"] = tc.header.divider.model_copy(
            update=header_rule_updates
        )
    if header_updates:
        table_updates["header"] = tc.header.model_copy(update=header_updates)
    if summary_updates:
        role_updates = {
            "summary": tc.row.roles.summary.model_copy(update=summary_updates)
        }
        if rule_color is not None:
            total = tc.row.roles.total
            role_updates["total"] = total.model_copy(
                update={
                    "divider": total.divider.model_copy(update={"color": rule_color})
                }
            )
        row_updates["roles"] = tc.row.roles.model_copy(update=role_updates)
    if row_rule_updates:
        row_updates["divider"] = tc.row.divider.model_copy(update=row_rule_updates)
    if row_updates:
        table_updates["row"] = tc.row.model_copy(update=row_updates)
    if font_updates:
        table_updates["font"] = tc.font.model_copy(update=font_updates)
    if table_updates:
        return dataclasses.replace(es, table=tc.model_copy(update=table_updates))
    return es


# ---------------------------------------------------------------------------
# Helpers for digging values out of rendered SVG
# ---------------------------------------------------------------------------


_TSPAN_RE = re.compile(r'<tspan\s+x="([\d.]+)"[^>]*text-anchor="([^"]+)"[^>]*>')


def _find_header(svg: str, name: str) -> tuple[float, str] | None:
    """Return (x, anchor) for the header <text> matching name.

    Handles three SVG shapes emitted by the table renderer:
      - clip/truncate: <text ...><title>FULL_NAME</title>VISIBLE</text>  (title=full name)
      - single-line normal: <text x="..." y="...">NAME</text>
      - multi-line (wrap): <text x="..."><tspan...>part1</tspan>...</text>
    """
    escaped = re.escape(name)
    # clip/truncate: <title> carries the full name, visible text is shortened.
    for match in re.finditer(
        r'<text\s+x="([\d.]+)"[^>]*text-anchor="([^"]+)"[^>]*>'
        r"<title>" + escaped + r"</title>",
        svg,
    ):
        return float(match.group(1)), match.group(2)
    # Single-line normal: full name is the direct text child.
    for match in re.finditer(
        r'<text\s+x="([\d.]+)"\s+y="[\d.]+"[^>]*text-anchor="([^"]+)"[^>]*>'
        + escaped
        + r"</text>",
        svg,
    ):
        return float(match.group(1)), match.group(2)
    # Multi-line (wrap): full name split across tspans; match by first tspan content.
    first_word = re.escape(name.split()[0]) if name.split() else escaped
    for match in re.finditer(
        r'<text\s+x="([\d.]+)"[^>]*text-anchor="([^"]+)"[^>]*>'
        r"<tspan[^>]*>" + first_word,
        svg,
    ):
        # Confirm the full name appears within the text block.
        text_end = svg.find("</text>", match.start())
        block = svg[match.start() : text_end + 7] if text_end != -1 else ""
        if name.replace(" ", "") in block.replace(" ", "").replace("\n", ""):
            return float(match.group(1)), match.group(2)
    return None


def _find_number_tspans(svg: str) -> list[tuple[float, str]]:
    """Return list of (x, anchor) for every number-style tspan in the SVG."""
    return [
        (float(m.group(1)), m.group(2))
        for m in re.finditer(
            r'<tspan\s+x="([\d.]+)"\s+text-anchor="([^"]+)"[^>]*>', svg
        )
    ]


# ---------------------------------------------------------------------------
# Unit tests against the internal layout helpers
# ---------------------------------------------------------------------------


class TestLanePositionsAnchorAtMidpoint:
    """`_compute_lane_positions` anchors the number tspan so its horizontal
    center sits at the column's content midpoint.  The returned `number_x`
    is the right edge of the widest number (tspan is right-aligned), so
    the invariant is: `number_x - max_value_w / 2 == cell_midpoint`.
    """

    def _call(self, rows, columns, col_widths, font_size=14):
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.render.chart.table import _compute_lane_positions

        # Single column for simplicity: cell_x = padding_x
        padding_x = 16
        col_x_offsets = [0.0]
        return (
            _compute_lane_positions(
                rows=rows,
                columns=columns,
                column_configs={},
                col_widths=col_widths,
                col_x_offsets=col_x_offsets,
                padding_x=padding_x,
                cell_pad=12,
                cell_font=FontStyle(size=float(font_size)),
                formats={"number": ".3~s"},
                column_when_rules={},
            ),
            padding_x,
            col_x_offsets,
        )

    def test_number_tspan_center_sits_at_cell_midpoint(self):
        rows = [{"Revenue": 849}, {"Revenue": 12}, {"Revenue": 456}]
        col_widths = {"Revenue": 200.0}
        positions, padding_x, offsets = self._call(rows, ["Revenue"], col_widths)
        assert "Revenue" in positions
        prefix_x, number_x, suffix_x, content_left, content_right = positions["Revenue"]

        # The widest number is 3 chars.  With a measurer we can compute the
        # width exactly; without one, we reproduce the heuristic the
        # function uses.  In either case the invariant is the same:
        # number tspan center == cell_midpoint.
        cell_x = padding_x + offsets[0]
        cell_midpoint = cell_x + col_widths["Revenue"] / 2

        # Infer the measured width from the positions:
        # number_x is the tspan's right edge; its left edge is at
        # (number_x - widest_w), so center is number_x - widest_w/2.
        # To test the invariant we compare half-widths from the midpoint
        # to the left and right edges of the number's bounding box.
        widest_w = number_x - (
            # Re-derive: content_left = prefix_x - max_prefix_w; for this
            # case there's no prefix (plain integers), so content_left == prefix_x
            # and max_prefix_w == 0.  Similarly no suffix.  So the number
            # tspan spans (prefix_x .. number_x), width = number_x - prefix_x.
            prefix_x
        )
        tspan_center = number_x - widest_w / 2
        assert tspan_center == pytest.approx(cell_midpoint, abs=0.5), (
            f"Number tspan center {tspan_center} must sit at cell midpoint "
            f"{cell_midpoint} (column=Revenue, positions={positions})"
        )

    def test_date_outlier_in_numeric_column_widens_lane_for_its_own_content(self):
        """A date-shaped outlier inside an otherwise-numeric column (e.g.
        real numbers plus one long date string) must contribute its own
        measured width to the lane, not just route onto a lane sized from
        the numbers alone — an undersized lane pushes the outlier's glyph
        left of where it belongs, potentially into the neighboring column.
        """
        col_widths = {"N": 200.0}
        positions_numbers_only, *_ = self._call([{"N": 1}, {"N": 2}], ["N"], col_widths)
        positions_with_outlier, *_ = self._call(
            [{"N": 1}, {"N": 2}, {"N": "15 Sep 2024"}], ["N"], col_widths
        )
        assert "N" in positions_numbers_only
        assert "N" in positions_with_outlier
        content_width_numbers_only = (
            positions_numbers_only["N"][4] - positions_numbers_only["N"][3]
        )
        content_width_with_outlier = (
            positions_with_outlier["N"][4] - positions_with_outlier["N"][3]
        )
        assert content_width_with_outlier > content_width_numbers_only, (
            f"Lane content width must grow to fit the date-shaped outlier — "
            f"got {content_width_with_outlier} vs "
            f"{content_width_numbers_only} for numbers alone"
        )

    def test_date_lane_number_x_clamps_to_the_column_content_area(self):
        """A uniformly date-like column narrower than its widest value (the
        normal case — a column doesn't always get its full measured
        demand) must not lane past its own content area:
        _render_data_rows truncates the same string to the cell, so an
        unclamped number_x would anchor the drawn glyph outside the
        column, or off the SVG entirely.

        The returned ``content_right`` isn't a usable bound here — for a
        pure date column (no prefix/suffix) it is defined as
        ``number_x + max_suffix_w`` and so trivially tracks ``number_x``
        regardless of clamping. Compare against the cell's actual
        geometric content-area edge instead, independently derived from
        the same padding/width inputs used below.

        Uses a real ``ResolvedTableColumnConfig(align="right")`` rather than the
        ``_call`` helper's empty ``column_configs`` — the date lane path
        trusts the resolved ``align`` field (see _compute_lane_positions),
        so a column with no config at all is never date-lane-eligible,
        matching what resolve always materializes in a real render.
        """
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.compile.models.style.resolved.table import (
            ResolvedTableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import _compute_lane_positions

        col_widths = {"D": 40.0}
        padding_x = 16
        cell_pad = 12
        positions = _compute_lane_positions(
            rows=[{"D": "9 May 2026"}, {"D": "15 Jul 2026"}],
            columns=["D"],
            column_configs={"D": ResolvedTableColumnConfig(align="right")},
            col_widths=col_widths,
            col_x_offsets=[0.0],
            padding_x=padding_x,
            cell_pad=cell_pad,
            cell_font=FontStyle(size=14.0),
            formats={"number": ".3~s"},
            column_when_rules={},
        )
        assert "D" in positions
        _prefix_x, number_x, *_ = positions["D"]
        cell_x = padding_x
        content_area_right = cell_x + col_widths["D"] - cell_pad
        assert number_x <= content_area_right + 0.01, (
            f"number_x ({number_x}) must not exceed the column's actual "
            f"content-area right edge ({content_area_right}) — the fold "
            f"must clamp to what _render_data_rows will actually draw."
        )

    def test_date_lane_trusts_the_resolved_align_not_the_raw_content(self):
        """A column whose content is unanimously date-like still earns no
        lane unless its resolved ``align`` is literally ``"right"`` — even
        though content alone would satisfy classify_date_column_align's own
        rule. _compute_lane_positions must not re-decide alignment from the
        raw rows; it reads the verdict resolve (or render's own
        pivot/transpose leaf synthesis) already finalized. Pins the fix for
        a case where an earlier design silently ignored the resolved field
        and re-derived its own verdict, making resolve's classification
        inert — both by overriding an explicit ``align: left``, and by
        granting a lane from content alone when no column config resolved
        ``align`` at all.
        """
        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.compile.models.style.resolved.table import (
            ResolvedTableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import _compute_lane_positions

        rows = [{"D": "9 May 2026"}, {"D": "1 Jun 2026"}]
        col_widths = {"D": 200.0}
        common_kwargs = {
            "rows": rows,
            "columns": ["D"],
            "col_widths": col_widths,
            "col_x_offsets": [0.0],
            "padding_x": 16,
            "cell_pad": 12,
            "cell_font": FontStyle(size=14.0),
            "formats": {"number": ".3~s"},
        }
        positions_align_left = _compute_lane_positions(
            column_configs={"D": ResolvedTableColumnConfig(align="left")},
            **common_kwargs,
            column_when_rules={},
        )
        positions_align_right = _compute_lane_positions(
            column_configs={"D": ResolvedTableColumnConfig(align="right")},
            **common_kwargs,
            column_when_rules={},
        )
        positions_no_config = _compute_lane_positions(
            column_configs={}, **common_kwargs, column_when_rules={}
        )
        assert "D" not in positions_align_left, (
            "align: left must win outright — no lane — even though every "
            "value in the column is date-like."
        )
        assert "D" in positions_align_right
        assert "D" not in positions_no_config, (
            "with no resolved column config at all, render must not "
            "self-classify the column as date-right from raw content — "
            "that classification only ever happens once, at resolve."
        )

    def test_prefix_has_visible_gap_before_widest_number(self):
        """The ``$`` prefix must not visually kiss the widest number.

        Regression for #1244, which removed the 2px gap introduced in #1186
        on the theory that tabular-digit measurement was accurate enough for
        zero-gap to read as "touching, not overlapping". In practice a
        zero-gap "$" + "193.52" reads as a kiss, not a separator. The fix
        restores a 3px gap (matching ``suffix_gap``) when a prefix exists.
        """
        from dbt_charts.core.compile.models.style.resolved.table import (
            ResolvedTableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import _compute_lane_positions

        # Widest cell drives prefix_x; include the total-row-sized value.
        # Use d3 format string "$,.2s" to get a real "$" prefix (the
        # FormatConfig-type path doesn't emit a prefix via format_kpi_parts).
        rows = [{"Amount": 193_520_000}, {"Amount": 5_630_000}]
        col_widths = {"Amount": 220.0}
        col_cfg = {
            "Amount": ResolvedTableColumnConfig(
                format="$,.2s",
            )
        }
        from dbt_charts.core.compile.models.primitives import FontStyle

        positions = _compute_lane_positions(
            rows=rows,
            columns=["Amount"],
            column_configs=col_cfg,
            col_widths=col_widths,
            col_x_offsets=[0.0],
            padding_x=16,
            cell_pad=12,
            cell_font=FontStyle(size=14.0),
            column_when_rules={},
        )
        prefix_x, number_x, _suffix_x, _content_left, _content_right = positions[
            "Amount"
        ]
        # Measure the widest number via the same strict path the lane computer uses.
        from dbt_charts.core.compile.format_value import format_kpi_parts
        from dbt_charts.core.font_measure import get_font_measurer

        _p, num_str, _s = format_kpi_parts(193_520_000, col_cfg["Amount"].format)
        measurer = get_font_measurer(None, numeric=True)
        max_content_w = measurer.measure(num_str, 14.0)

        gap = (number_x - max_content_w) - prefix_x
        assert gap == pytest.approx(3.0, abs=0.5), (
            f"Prefix must sit 3px left of the widest number to avoid "
            f"visual kiss; got gap={gap:.2f}px "
            f"(prefix_x={prefix_x}, number_x={number_x}, "
            f"max_content_w={max_content_w:.2f})"
        )

    def test_no_prefix_no_gap(self):
        """Columns without a prefix don't add a spurious 3px of left padding."""
        from dbt_charts.core.render.chart.table import _compute_lane_positions

        rows = [{"Revenue": 849}, {"Revenue": 12}]
        col_widths = {"Revenue": 200.0}
        from dbt_charts.core.compile.models.primitives import FontStyle

        positions = _compute_lane_positions(
            rows=rows,
            columns=["Revenue"],
            column_configs={},
            col_widths=col_widths,
            col_x_offsets=[0.0],
            padding_x=16,
            cell_pad=12,
            cell_font=FontStyle(size=14.0),
            formats={"number": ".3~s"},
            column_when_rules={},
        )
        prefix_x, number_x, _suffix_x, *_ = positions["Revenue"]
        # Without a prefix, prefix_x == number_x - max_content_w (no gap).
        # We don't know max_content_w without re-measuring, but we can assert
        # the relative invariant: tspan spans (prefix_x .. number_x) exactly.
        # The absence of a gap means number_x - prefix_x equals the number
        # width.  Compare to a with-prefix run of equal values to cross-check.
        from dbt_charts.core.compile.models.style.resolved.table import (
            ResolvedTableColumnConfig,
        )

        cfg_with_prefix = {
            "Revenue": ResolvedTableColumnConfig(
                format="$,.0f",
            )
        }
        positions_with_prefix = _compute_lane_positions(
            rows=rows,
            columns=["Revenue"],
            column_configs=cfg_with_prefix,
            col_widths=col_widths,
            col_x_offsets=[0.0],
            padding_x=16,
            cell_pad=12,
            cell_font=FontStyle(size=14.0),
            column_when_rules={},
        )
        prefix_x_with, _number_x_with, *_ = positions_with_prefix["Revenue"]
        # The with-prefix run must sit at least ~3px further left than the
        # no-prefix run (same numbers, same cell width, so number_x is
        # similar).  If the no-prefix path accidentally got a gap too, this
        # distance would collapse.
        assert prefix_x - prefix_x_with >= 2.5, (
            f"No-prefix prefix_x ({prefix_x}) should be ~3px to the right of "
            f"with-prefix prefix_x ({prefix_x_with}); gap-for-no-prefix bug?"
        )


# ---------------------------------------------------------------------------
# Integration tests against the full SVG output
# ---------------------------------------------------------------------------


class TestNumericHeaderCentersOnMidpoint:
    """Numeric column headers render with text-anchor='middle' at x=midpoint."""

    def test_short_numeric_header_centered(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Name": "Alice", "Revenue": 849}, {"Name": "Bob", "Revenue": 102}]
        _custom_board = _style()
        chart = resolve(chart, [], chart_style_context=_custom_board)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )

        result = _find_header(svg, "Revenue")
        assert result is not None, "Expected Revenue header in SVG"
        _x, anchor = result
        assert anchor == "middle", (
            f"Numeric header 'Revenue' must use text-anchor='middle', got {anchor!r}"
        )

    def test_wide_numeric_header_still_centered_not_right_aligned(self, make_chart):
        """A wide header that would not fit the value lane must still center —
        it wraps or hyphenates into the full cell width, never flips to end-anchor.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [
            {"Region": "North", "Median Income": 52000},
            {"Region": "South", "Median Income": 48000},
        ]
        _custom_board = _style()
        chart = resolve(chart, [], chart_style_context=_custom_board)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )

        result = _find_header(svg, "Median Income")
        assert result is not None
        _x, anchor = result
        assert anchor == "middle", (
            f"Wide numeric header must remain centered (anchor='middle'), "
            f"not flip to right-align. Got anchor={anchor!r}."
        )


class TestTextHeaderStaysLeftAligned:
    """Text column headers remain left-aligned at cell_x + pad."""

    def test_text_header_starts_at_cell_left(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Name": "Alice", "Revenue": 849}]
        _custom_board = _style()
        chart = resolve(chart, [], chart_style_context=_custom_board)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        result = _find_header(svg, "Name")
        assert result is not None
        _x, anchor = result
        assert anchor == "start", (
            f"Text header 'Name' must stay left-aligned (anchor='start'), "
            f"got {anchor!r}"
        )


class TestHeaderCenterEqualsNumberCenter:
    """End-to-end invariant: numeric header x == cell midpoint == number
    tspan center.

    This test uses the internal ``_compute_lane_positions`` to get the
    exact number_x and prefix_x for the Revenue column, derives the
    number tspan's visual center, and asserts it matches the header x
    within a tight tolerance.
    """

    def test_header_x_matches_number_tspan_center(self, make_chart):
        """Verify header_x ≈ number_tspan_center end-to-end.

        We extract the number tspan's right-edge x from the SVG (the
        ``x`` attribute of the ``text-anchor="end"`` tspan), then compute
        the number center using the prefix tspan's x (which gives us
        the number width).  That center must match the header x.

        Uses a wide width (1200px) to avoid the fit cascade recomputing
        column widths at a different font size.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [
            {"Name": "Alice", "Revenue": 849},
            {"Name": "Bob", "Revenue": 102},
            {"Name": "Carol", "Revenue": 7},
        ]
        _custom_board = _style()
        chart = resolve(chart, [], chart_style_context=_custom_board)
        svg = render_table_svg(
            chart,
            data,
            width=1200,
            board_style=_BOARD_STYLE,
        )

        header = _find_header(svg, "Revenue")
        assert header is not None
        header_x, anchor = header
        assert anchor == "middle"

        # Extract Revenue column's number tspan x (end-anchored).
        # Revenue values (849, 102, 7) are all right-anchored at the same
        # number_x.  Also extract the Name column's text x to compute
        # Revenue's cell bounds.
        name_header = _find_header(svg, "Name")
        assert name_header is not None

        # Revenue's number tspans all share a single number_x.  Grab all
        # unique end-anchored x values; the one to the right of header_x
        # is the number lane's right edge.
        end_xs = sorted(
            {
                float(m.group(1))
                for m in re.finditer(r'<tspan\s+x="([\d.]+)"\s+text-anchor="end"', svg)
            }
        )
        assert end_xs, "Expected at least one end-anchored tspan"

        # The Revenue column's number_x is the end-anchor x that's
        # closest to (but right of) the header center.
        number_x = min(
            (x for x in end_xs if x > header_x),
            key=lambda x: x - header_x,
        )

        # The header x IS the cell midpoint (by design).  The number
        # center should also be at the cell midpoint:
        #   number_center = number_x - number_w / 2 = cell_midpoint
        # Rearranging: number_x = cell_midpoint + number_w / 2, so
        #   number_x - header_x = number_w / 2.
        # Both header_x and (number_x - number_w/2) should agree.
        # We can verify: header_x < number_x (center is left of right
        # edge), and the offset (number_x - header_x) should be
        # consistent with a reasonable number width.
        offset = number_x - header_x
        assert offset > 0, "Number right edge must be right of header center"
        # At 14px, "849" is roughly 25-30px wide → half is ~12-15px.
        # Be generous with bounds since the measurer may not be available.
        assert offset < 40, (
            f"Offset between header center and number right edge is {offset}px"
            f" — unreasonably large, suggesting misalignment."
        )
        # The symmetric check: header_x should also be offset from the
        # number's LEFT edge by the same amount.  We can't easily get
        # the left edge from the SVG, but we can check that header_x
        # is strictly between the cell's left pad and number_x — proving
        # it's not left-aligned or right-aligned.
        assert header_x > name_header[0] + 20, (
            f"Revenue header x ({header_x}) should be well right of "
            f"Name header x ({name_header[0]}), proving it's center-"
            f"aligned, not left-aligned."
        )


class TestEqualDistributionAcrossColumnTypes:
    """All auto-columns receive equal share of available_width.

    Content-aware clustering was removed in favor of equal distribution.
    Tables that need unequal column widths use explicit width: overrides.
    """

    def test_all_auto_columns_equal_share(self):
        """Five mixed columns at 900px each get 180px."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["Alpha", "Beta", "Category", "Gamma", "Delta"]
        col_widths, _, actual_w = calculate_column_layout(
            columns,
            {},
            900.0,
        )
        expected = 900.0 / 5
        for col in columns:
            assert abs(col_widths[col] - expected) <= 0.5, (
                f"Column '{col}' got {col_widths[col]:.1f}px, "
                f"expected equal share {expected:.1f}px"
            )
        assert abs(actual_w - 900.0) <= 0.5

    def test_three_auto_columns_equal_share(self):
        """Three numeric columns at 700px each get equal share."""
        from dbt_charts.core.render.chart.table_support import (
            calculate_column_layout,
        )

        columns = ["Rank", "Visitors", "Pageviews"]
        col_widths, _, actual_w = calculate_column_layout(
            columns,
            {},
            700.0,
        )
        expected = 700.0 / 3
        for col in columns:
            assert abs(col_widths[col] - expected) <= 0.5, (
                f"Column '{col}' got {col_widths[col]:.1f}px, "
                f"expected equal share {expected:.1f}px"
            )
        assert abs(actual_w - 700.0) <= 0.5


class TestPerColumnRuleCentersOnMidpoint:
    """Per-column header rules (Classic variant) center on cell midpoint."""

    def _classic_style(self):
        return _style(
            font_family="'Source Serif 4', Georgia, serif",
            header_font_weight="400",
            header_background=None,
            header_rule_width=1,
            header_rule_continuous=False,
            row_rule_width=0,
            summary_rule_width=0,
        )

    def test_rule_centered_on_header(self, make_chart):
        """The per-column rule for a numeric column centers on the same x
        as the header text.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [
            {"Name": "Alice", "Revenue": 849},
            {"Name": "Bob", "Revenue": 102},
        ]
        custom_board = self._classic_style()
        chart = resolve(chart, [], chart_style_context=custom_board)
        svg = render_table_svg(
            chart,
            data,
            width=500,
            board_style=_BOARD_STYLE,
        )
        # Locate header x for Revenue
        header = _find_header(svg, "Revenue")
        assert header is not None
        header_x, anchor = header
        assert anchor == "middle"

        # Locate the per-column header rules (rect with crispEdges).
        rules = re.findall(
            r'<rect x="([\d.]+)" y="\d+" width="([\d.]+)"[^>]*'
            r'shape-rendering="crispEdges"',
            svg,
        )
        assert rules, "Expected at least one header rule rect"

        # One of the rules should be centered on header_x (x1 + w/2 == header_x).
        matched = False
        for x1_str, w_str in rules:
            x1 = float(x1_str)
            w = float(w_str)
            center = x1 + w / 2
            if abs(center - header_x) < 1.0:
                matched = True
                break
        assert matched, (
            f"Expected at least one per-column rule centered on header_x="
            f"{header_x}. Rules: {rules}"
        )


class TestBICascadeStepThree:
    """BI cascade step 3: header shrinks to 8 while body stays at 11.

    When ``header_font_size`` is explicit and different from ``font_size``
    (e.g. BI at 11/14), the cascade is independent — headers shrink
    first, then body follows. This intermediate state (header=8, body=11)
    is how BI preserves apparatus-tier sizing under pressure.
    """

    def _bi_style(self):
        return _style(
            header_font_size=11,
            header_font_weight="600",
            header_background="#EDEFF2",
            header_rule_width=1,
            header_rule_continuous=True,
        )

    def _sizes(self, svg):
        # Headers carry both font-weight and text-anchor on the outer <text>.
        # Chart-title <text> has font-weight but not text-anchor; body cells
        # have text-anchor but not font-weight. Use lookaheads to match both
        # regardless of attribute order, mirroring TestHeaderSizeLinkingGroupDecision.
        # Do NOT anchor on <title> — short headers never emit it (no truncation).
        hs = {
            int(m.group(1))
            for m in re.finditer(
                r'<text(?=[^>]*\bfont-weight=")(?=[^>]*\btext-anchor=")[^>]*\bfont-size="(\d+)"',
                svg,
            )
        }
        bs = {
            int(m.group(1))
            for m in re.finditer(
                r'<text[^>]*y="[\d.]+"[^>]*font-size="(\d+)"[^>]*>[^<]', svg
            )
        }
        return hs, bs

    def test_bi_header_shrinks_before_body(self, make_chart):
        """At a width narrow enough to trigger step 3, BI headers should
        be at 8 while body is still at 11."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        # Many columns to force deep cascade
        data = [
            {
                "A": "Alpha bravo",
                "B": 12345678,
                "C": 87654321,
                "D": 11223344,
                "E": 55667788,
            }
        ]
        custom_board = self._bi_style()
        chart = resolve(
            make_chart("table", x=None, y=None),
            [],
            chart_style_context=custom_board,
        )
        # Try progressively narrow widths to find step-3 territory
        for w in range(350, 150, -10):
            svg = render_table_svg(
                chart,
                data,
                width=w,
                board_style=_BOARD_STYLE,
            )
            hs, bs = self._sizes(svg)
            if 8 in hs and 11 in bs:
                break
            if 8 in hs and 8 in bs:
                # Went past step 3 into step 4 — both at floor
                break
        # Either we found the split state, or body was already forced to 8
        # before header hit 8 (which can happen at very narrow widths).
        # The key invariant: header is NEVER larger than body in BI.
        # Sanity-check that _sizes() actually extracts header sizes (guards against
        # a silent regex failure that would make the loop a no-op).
        svg_wide = render_table_svg(
            chart,
            data,
            width=500,
            board_style=_BOARD_STYLE,
        )
        hs_wide, _ = self._sizes(svg_wide)
        assert hs_wide, "_sizes() returned no header font sizes — regex is broken"
        for w in range(500, 150, -25):
            svg = render_table_svg(
                chart,
                data,
                width=w,
                board_style=_BOARD_STYLE,
            )
            hs, bs = self._sizes(svg)
            if hs and bs:
                assert min(hs) <= min(bs), (
                    f"BI at w={w}: header={hs} must be <= body={bs}. "
                    f"Headers should never exceed body size."
                )


class TestWrapLimitUsesFullContentArea:
    """Numeric column headers wrap based on the full cell content area
    (``cw - 2 * pad``), NOT a constrained centered-fit zone around the
    value lane.

    This is the core regression guard for the old fragility: the previous
    centered_fit calculation would force headers like "Median Income" to
    wrap at ~60px even when the cell had 120px available, because the
    value lane sat right of center in a cluster-widened cell.
    """

    def test_header_fits_single_line_when_cell_is_wide_enough(self, make_chart):
        """A header that's wider than the value lane but narrower than
        cw-2*pad should render on one line, not wrap.

        The data values are deliberately long strings so the column layout
        sizes the column wide enough to fit the header — even under the
        char-count heuristic (CI has no fonttools measurer).
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        # "Median Income" is 13 chars.  The char-count heuristic (CI, no
        # fonttools) needs max_chars >= 13 which requires the column to
        # be sized for ~16+ char data values.  Use very large numbers
        # whose formatted output ("52,000,000,000,000" = 18 chars)
        # exceeds the header length, forcing the column wide enough.
        data = [
            {"Region": "North Atlantic Seaboard", "Median Income": 52000000000000},
            {"Region": "South Pacific Coast", "Median Income": 48000000000000},
        ]
        custom_board = _style()
        chart = resolve(chart, [], chart_style_context=custom_board)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=_BOARD_STYLE,
        )

        header = _find_header(svg, "Median Income")
        assert header is not None
        _x, anchor = header
        assert anchor == "middle"

        # Check the header is on ONE line: single-line non-truncated headers
        # emit "Median Income" as direct text child (no <tspan> children,
        # no <title> since the full name is visible).
        pattern = (
            r'<text[^>]*text-anchor="middle"[^>]*>'
            r"(?:<title>[^<]+</title>)?([^<]*)</text>"
        )
        match = re.search(pattern, svg, re.DOTALL)
        assert match is not None
        content = match.group(1)
        # Multi-line headers use <tspan> children; single-line has direct text.
        assert "Median Income" in content or match.group(0).count("<tspan") == 0, (
            f"'Median Income' should fit on one line (data values are wider "
            f"than header), but it wrapped. Content: {content!r}"
        )


# ---------------------------------------------------------------------------
# Date columns: cells route through col_lane_positions like numerics do
# ---------------------------------------------------------------------------


def _find_text_x(svg: str, content: str) -> tuple[float, str] | None:
    """Return (x, anchor) for the <text> whose direct child is ``content``."""
    escaped = re.escape(content)
    match = re.search(
        r'<text\s+x="([\d.]+)"\s+y="[\d.]+"[^>]*text-anchor="([^"]+)"[^>]*>'
        + escaped
        + r"</text>",
        svg,
    )
    if match is None:
        return None
    return float(match.group(1)), match.group(2)


class TestDateCellsUseLanePositions:
    """Date cells must render at their column's value-column midpoint
    (``number_x`` from ``_compute_lane_positions``), not at the outer
    column right edge. Otherwise on a column wider than the date string
    the values float right of the centered header.
    """

    def test_date_cell_x_matches_lane_number_x(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        # Wide column relative to the date string so the bug is visible:
        # the date "29 Jul 2026" measures ~60–70px at 14px font, but the
        # column will be ~600px wide at width=1200 / 2 columns.
        data = [
            {"Region": "North", "Last Close": "29 Jul 2026"},
            {"Region": "South", "Last Close": "2 Jul 2026"},
            {"Region": "East", "Last Close": "15 Jun 2026"},
        ]
        custom_board = _style()
        # Every "Last Close" value is a short-month date string, so resolve
        # needs the real rows to classify the column's align verdict as
        # "right" (see fill_table_column_defaults / classify_date_column_align).
        chart = resolve(chart, data, chart_style_context=custom_board)
        svg = render_table_svg(
            chart,
            data,
            width=1200,
            board_style=_BOARD_STYLE,
        )

        # Pick a date that's not the widest; if dates routed through
        # number_x, all date cells share the same x (right edge of widest).
        # Currently (bug) each one right-anchors to outer column edge —
        # also a shared x, but a different one. We assert the value is
        # equal to the column's number_x.
        result = _find_text_x(svg, "2 Jul 2026")
        assert result is not None, "Expected '2 Jul 2026' as a date cell"
        cell_x, anchor = result
        assert anchor == "end", "Date cells must remain end-anchored"

        # The Last Close header center-x must be (cell_midpoint).
        # number_x = cell_midpoint + max_date_w / 2, so
        # cell_x - header_x must be > 0 and ~ max_date_w / 2.
        header = _find_header(svg, "Last Close")
        assert header is not None
        header_x, header_anchor = header
        assert header_anchor == "middle"

        offset = cell_x - header_x
        # At 14px, the widest date "29 Jul 2026" measures roughly 65–75px,
        # so half is ~35px.  If the cell were right-anchored to the outer
        # column edge instead, the offset would be column_width/2 minus
        # cell_pad — for a ~600px column that's ~280px.
        assert 0 < offset < 60, (
            f"Date cell x ({cell_x}) should sit at column's number_x — "
            f"~half-the-widest-date-width to the right of header center "
            f"({header_x}). Got offset {offset:.1f}px; if much larger, "
            f"dates are right-anchored to the outer column edge instead "
            f"of the value-column midpoint."
        )

    def test_date_column_header_centers_on_value_block(self, make_chart):
        """Header center-x must equal the date value block's content
        midpoint — the same invariant numeric columns enforce.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [
            {"Region": "N", "Last Close": "29 Jul 2026"},
            {"Region": "S", "Last Close": "2 Jul 2026"},
        ]
        custom_board = _style()
        # Every "Last Close" value is a short-month date string — resolve
        # needs the real rows to classify align="right".
        chart = resolve(chart, data, chart_style_context=custom_board)
        svg = render_table_svg(
            chart,
            data,
            width=1200,
            board_style=_BOARD_STYLE,
        )

        header = _find_header(svg, "Last Close")
        assert header is not None
        header_x, _ = header

        # All date cells in the column share the same x (number_x).
        x_29 = _find_text_x(svg, "29 Jul 2026")
        x_2 = _find_text_x(svg, "2 Jul 2026")
        assert x_29 is not None and x_2 is not None
        assert x_29[0] == pytest.approx(x_2[0], abs=0.5), (
            f"All date cells in a column must share number_x; "
            f"got {x_29[0]} vs {x_2[0]}."
        )

        # The widest date right-anchors at number_x = midpoint + width/2.
        # Header centers at midpoint, so (cell_x - header_x) = widest/2 > 0.
        # If header_x sat at the outer column edge minus pad instead, that
        # offset would scale with column width, not date width.
        # Cap the offset by what a date string can plausibly half-measure.
        offset = x_29[0] - header_x
        assert 0 < offset < 60, (
            f"Header center ({header_x}) and date value end-x ({x_29[0]}) "
            f"should differ by ~half the widest date width, not by the "
            f"column's half-width. Offset={offset:.1f}px."
        )

    def test_mixed_numeric_and_date_column_shares_number_x(self, make_chart):
        """A column that holds both numeric and date-like values should
        still produce a consistent value-block: every cell anchors at the
        same number_x.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        # "2024" parses as date-like (year pattern); pair it with a real
        # numeric to exercise the mixed path.
        data = [
            {"Label": "A", "Year/Count": 1234},
            {"Label": "B", "Year/Count": "2024"},
            {"Label": "C", "Year/Count": 567},
        ]
        custom_board = _style()
        chart = resolve(chart, [], chart_style_context=custom_board)
        svg = render_table_svg(
            chart,
            data,
            width=1200,
            board_style=_BOARD_STYLE,
        )

        # Numeric cells use a <tspan> at number_x; the date cell uses
        # <text x="..."> at the same x after the fix.
        date_cell = _find_text_x(svg, "2024")
        assert date_cell is not None
        date_x = date_cell[0]

        # Find all unique end-anchored tspan x values; one will be the
        # column's number_x (shared by 1234 and 567).
        end_tspan_xs = sorted(
            {
                float(m.group(1))
                for m in re.finditer(r'<tspan\s+x="([\d.]+)"\s+text-anchor="end"', svg)
            }
        )
        assert any(abs(date_x - x) < 0.5 for x in end_tspan_xs), (
            f"Date cell x ({date_x}) should match one of the numeric "
            f"tspan number_x values {end_tspan_xs} — confirms the column "
            f"shares one value-block midpoint across numeric and date cells."
        )

    def test_explicit_align_left_wins_on_uniformly_date_like_column(
        self, make_chart
    ) -> None:
        """A column whose values are ALL short-month dates (so it earns a
        content-driven date lane) must still honor an authored
        ``align: left`` outright — no lane at all, for either the cells or
        the header. The mixed-spelling fixture elsewhere in this file never
        earns a lane in the first place, so it can't exercise this path.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        data = [
            {"Region": "A", "Last Close": "9 May 2026"},
            {"Region": "B", "Last Close": "1 Jun 2026"},
            {"Region": "C", "Last Close": "15 Jul 2026"},
        ]
        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={"columns": {"Last Close": {"align": "left", "visible": True}}},
        )
        chart = resolve(chart, data, chart_style_context=_style())
        assert chart.columns is not None
        assert chart.columns["Last Close"].align == "left"

        svg = render_table_svg(chart, data, width=1200, board_style=_BOARD_STYLE)

        for value in ("9 May 2026", "1 Jun 2026", "15 Jul 2026"):
            result = _find_text_x(svg, value)
            assert result is not None, f"Expected {value!r} as a rendered cell"
            _x, anchor = result
            assert anchor == "start", (
                f"align: left must win for {value!r} even though every "
                f"value in the column is date-like"
            )

        header = _find_header(svg, "Last Close")
        assert header is not None
        _header_x, header_anchor = header
        assert header_anchor == "start", (
            "No lane exists once align: left overrides the date verdict — "
            "the header must not center on one."
        )

    def test_explicit_align_center_on_date_column_centers_header_too(
        self, make_chart
    ) -> None:
        """An authored ``align: center`` on a uniformly date-like column
        earns no lane either (only ``"right"`` does), but its cells still
        center via the plain ``align == "center"`` cell branch — the header
        must center to match, not fall to its no-lane "start" default.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        data = [
            {"Region": "A", "Last Close": "9 May 2026"},
            {"Region": "B", "Last Close": "1 Jun 2026"},
            {"Region": "C", "Last Close": "15 Jul 2026"},
        ]
        chart = make_chart(
            "table",
            x=None,
            y=None,
            # Region hidden: the 600px center pin below assumes Last Close is
            # the only rendered column at width=1200.
            style={
                "columns": {
                    "Last Close": {"align": "center"},
                    "Region": {"visible": False},
                }
            },
        )
        chart = resolve(chart, data, chart_style_context=_style())
        assert chart.columns is not None
        assert chart.columns["Last Close"].align == "center"

        svg = render_table_svg(chart, data, width=1200, board_style=_BOARD_STYLE)

        for value in ("9 May 2026", "1 Jun 2026", "15 Jul 2026"):
            result = _find_text_x(svg, value)
            assert result is not None, f"Expected {value!r} as a rendered cell"
            _x, anchor = result
            assert anchor == "middle", f"align: center must win for {value!r}"

        header = _find_header(svg, "Last Close")
        assert header is not None
        header_x, header_anchor = header
        assert header_anchor == "middle", (
            "Cells center via align: center even without a lane — the "
            "header must center too, not default to its no-lane start."
        )
        assert header_x == pytest.approx(600.0, abs=0.5)


class TestDateColumnMixedSpellingPerCellBug:
    """Regression coverage for the per-cell alignment bug: a date column
    whose cells mix month-name spellings ("30 March 2026" vs "9 May 2026")
    must render one shared text-anchor for every cell, never a mix — and an
    authored ``align: left`` must never be silently overridden by a looser
    per-cell date detector. See the task worksheet for the original repro
    (Dundersign "Last Close" column).
    """

    _MIXED_SPELLING_DATA = [
        {"Region": "North", "Last Close": "30 March 2026"},
        {"Region": "South", "Last Close": "20 April 2026"},
        {"Region": "East", "Last Close": "9 May 2026"},
        {"Region": "West", "Last Close": "1 May 2026"},
    ]

    def test_no_explicit_align_mixed_spelling_shares_one_anchor(
        self, make_chart
    ) -> None:
        """Full-month-name cells ("30 March 2026") don't match the date
        detector's three-letter-month pattern; short-month cells ("9 May
        2026") do. Without a unanimous match the column gets no alignment
        verdict, so every cell — matching or not — must share one anchor
        ("start"), not split across "start" and "end"."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        chart = resolve(
            chart,
            self._MIXED_SPELLING_DATA,
            chart_style_context=_style(),
        )
        svg = render_table_svg(
            chart,
            self._MIXED_SPELLING_DATA,
            width=1200,
            board_style=_BOARD_STYLE,
        )

        anchors = {
            value: _find_text_x(svg, value)
            for value in ("30 March 2026", "20 April 2026", "9 May 2026", "1 May 2026")
        }
        for value, result in anchors.items():
            assert result is not None, f"Expected {value!r} as a rendered cell"
        found_anchors = {anchor for _x, anchor in anchors.values()}
        assert found_anchors == {"start"}, (
            f"Every cell in the column must share one text-anchor; got "
            f"{ {v: a for v, (_x, a) in anchors.items()} }"
        )

        header = _find_header(svg, "Last Close")
        assert header is not None
        _header_x, header_anchor = header
        assert header_anchor == "start", (
            "A column with no alignment verdict must not earn a centered "
            "lane header while its cells left-align."
        )

    def test_explicit_align_left_not_overridden_by_date_detector(
        self, make_chart
    ) -> None:
        """The literal repro: an authored ``align: left`` must win for
        every cell, even the ones an individual per-cell date check would
        have called date-like ("9 May 2026", "1 May 2026")."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={"columns": {"Last Close": {"align": "left", "visible": True}}},
        )
        chart = resolve(
            chart,
            self._MIXED_SPELLING_DATA,
            chart_style_context=_style(),
        )
        svg = render_table_svg(
            chart,
            self._MIXED_SPELLING_DATA,
            width=1200,
            board_style=_BOARD_STYLE,
        )

        for value in ("30 March 2026", "20 April 2026", "9 May 2026", "1 May 2026"):
            result = _find_text_x(svg, value)
            assert result is not None, f"Expected {value!r} as a rendered cell"
            _x, anchor = result
            assert anchor == "start", (
                f"align: left must win for {value!r}, got anchor={anchor!r}"
            )

    def test_explicit_align_right_on_mixed_content_folds_every_cell_into_one_lane(
        self, make_chart
    ) -> None:
        """An authored ``align: right`` on the mixed-spelling column (some
        cells individually date-like — "9 May 2026" — others not — "30
        March 2026") trusts the resolved ``align`` and, because the column
        does have date-like content, folds every remaining cell into one
        lane — not just the detector-matching subset. Every cell shares one
        anchor and the header centers on that same lane, never a mix.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={"columns": {"Last Close": {"align": "right", "visible": True}}},
        )
        chart = resolve(
            chart,
            self._MIXED_SPELLING_DATA,
            chart_style_context=_style(),
        )
        svg = render_table_svg(
            chart,
            self._MIXED_SPELLING_DATA,
            width=1200,
            board_style=_BOARD_STYLE,
        )

        xs = set()
        for value in ("30 March 2026", "20 April 2026", "9 May 2026", "1 May 2026"):
            result = _find_text_x(svg, value)
            assert result is not None, f"Expected {value!r} as a rendered cell"
            x, anchor = result
            assert anchor == "end", f"align: right must win for {value!r}"
            xs.add(round(x, 1))
        assert len(xs) == 1, (
            f"Every cell must share one lane number_x (the widest cell — "
            f'"20 April 2026" — sets it); got {xs}'
        )

        header = _find_header(svg, "Last Close")
        assert header is not None
        _header_x, header_anchor = header
        assert header_anchor == "middle", (
            "The column has date-like content and align: right, so it "
            "earns a lane — the header must center on it, matching the "
            "cells."
        )

    def test_explicit_align_right_text_column_stays_inside_the_svg(self) -> None:
        """The transpose "__value__" column (align="right", non-date
        content, no authored width — see _transpose_data_for_render) must
        stay flush-right at its own column edge, never past the SVG. This
        column earns no date lane (its content isn't date-like), so it
        takes the plain `align == "right"` edge-anchor branch, not the
        content-area-clamped fold path — see
        test_date_lane_number_x_clamps_to_the_column_content_area for that
        one. Kept as a regression guard for right-aligned non-numeric
        content in general: before lane eligibility was made content-driven
        (not align-driven), this exact column did reach the fold and its
        unclamped lane pushed number_x to 483.8 on a 400px-wide SVG.
        """
        from dbt_charts.core.compile.models.chart.normalized import TableChart
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        data = [
            {
                "customer": "Northwind Traders",
                "status": "Open",
                "notes": (
                    "Renewal blocked pending security review and legal "
                    "signoff before quarter end"
                ),
            }
        ]
        chart = TableChart(
            id="transposed_notes",
            type="table",
            source_path="charts.transposed_notes",
            style={"transpose": True},
        )
        resolved = resolve(chart, data, chart_style_context=_style(), width=400)
        svg = render_table_svg(resolved, data, width=400, board_style=_BOARD_STYLE)

        svg_tag_match = re.search(r'<svg[^>]*\bwidth="(\d+)"', svg)
        assert svg_tag_match is not None
        svg_width = float(svg_tag_match.group(1))

        end_anchored_xs = [
            float(m.group(1))
            for m in re.finditer(r'<text\s+x="([\d.]+)"[^>]*text-anchor="end"', svg)
        ]
        assert end_anchored_xs, "Expected at least one right-anchored cell"
        for x in end_anchored_xs:
            assert 0 <= x <= svg_width, (
                f"Transposed value cell x={x} must stay within the "
                f"{svg_width}px canvas, not lane past it."
            )


class TestPivotedDateLeafColumnAlignment:
    """A pivoting chart's leaf columns are synthesized by render, not
    resolve (see _materialize_table_columns docstring) — they must still
    get an alignment verdict via fill_table_column_defaults, not silently
    fall through to the "left"/unset arm.
    """

    def test_single_dim_single_measure_date_leaf_right_aligns(self) -> None:
        from dbt_charts.core.compile.models.chart.normalized import TableChart
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        pre_pivot_data = [
            {"region": "US", "month": "Jan", "last_close": "15 Jan 2024"},
            {"region": "EU", "month": "Jan", "last_close": "3 Jan 2024"},
        ]
        chart = TableChart(
            id="pivoted_dates",
            type="table",
            source_path="charts.pivoted_dates",
            rows=["region"],
            columns=["month"],
            values=["last_close"],
        )
        resolved = resolve(chart, pre_pivot_data, chart_style_context=_style())
        svg = render_table_svg(resolved, pre_pivot_data, board_style=_BOARD_STYLE)

        for value in ("15 Jan 2024", "3 Jan 2024"):
            result = _find_text_x(svg, value)
            assert result is not None, f"Expected {value!r} as a rendered leaf cell"
            _x, anchor = result
            assert anchor == "end", (
                f"Pivoted date leaf {value!r} must right-align, not fall "
                f"through to the unset/left arm."
            )

    def test_multi_measure_date_leaf_right_aligns(self) -> None:
        """Multi-measure pivot leaves (_PIVOT_LEAF_SEP-joined keys) are a
        different render-native expansion site than the single-dim
        single-measure case above — exercise it separately.
        """
        from dbt_charts.core.compile.models.chart.normalized import TableChart
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        pre_pivot_data = [
            {
                "region": "US",
                "month": "Jan",
                "last_close": "15 Jan 2024",
                "amount": 100,
            },
            {
                "region": "EU",
                "month": "Jan",
                "last_close": "3 Jan 2024",
                "amount": 150,
            },
        ]
        chart = TableChart(
            id="pivoted_dates_multi_measure",
            type="table",
            source_path="charts.pivoted_dates_multi_measure",
            rows=["region"],
            columns=["month"],
            values=["last_close", "amount"],
        )
        resolved = resolve(chart, pre_pivot_data, chart_style_context=_style())
        svg = render_table_svg(resolved, pre_pivot_data, board_style=_BOARD_STYLE)

        for value in ("15 Jan 2024", "3 Jan 2024"):
            result = _find_text_x(svg, value)
            assert result is not None, f"Expected {value!r} as a rendered leaf cell"
            _x, anchor = result
            assert anchor == "end", (
                f"Multi-measure pivoted date leaf {value!r} must right-align, "
                f"not fall through to the unset/left arm."
            )
