"""Tests for Vega-Lite table sizing and per-chart error handling.

Split from test_vega_lite.py — table and error handling tests.
"""

import re

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.render.svg_utils import escape_attr

_BOARD_STYLE = resolve_style(get_theme_style())
_BOARD_CONTEXT = resolve_chart_style_context(get_theme_style())


class TestTableProportionalSizing:
    """Test that table columns are sized proportionally to content."""

    def test_proportional_sizing_prevents_truncation(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [
            {
                "Rep": "Alice",
                "Revenue": 2148000,
                "Quota": 2000000,
                "Attainment": 107.4,
                "Operating Margin": 0.25,
                "Contribution Margin": 0.45,
                "Customer Count": 500,
                "New Logos": 12,
                "Churn Rate": 0.03,
            }
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=1200,
            board_style=resolve_style(get_theme_style()),
        )
        assert "Contribution M..." not in svg
        # Auto-wrap may split across tspans; both words should be present
        assert "Contribution" in svg
        assert "Margin" in svg

    def test_table_title_uses_width_aware_font(self, make_chart):
        """Table title font is width-aware: narrow tier uses style.font.family,
        medium/wide use style.title.font.family. Under default theme both are
        sans; under editorial the title family is serif."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        body_family = get_theme_style().font.family.split(",")[0].strip()
        title_family = get_theme_style().title.font.family.split(",")[0].strip()
        raw_chart = make_chart("table", x=None, y=None, title="Revenue Table")
        data = [{"Rep": "Alice", "Revenue": 2148000}]
        board = resolve_style(get_theme_style())
        # width=400 → narrow tier → body sans
        chart_narrow = resolve(
            raw_chart, data, chart_style_context=_BOARD_CONTEXT, width=400.0
        )
        svg_narrow = render_table_svg(
            chart_narrow,
            data,
            width=400,
            board_style=board,
        )
        assert f'font-family="{escape_attr(body_family)},' in svg_narrow
        # width=700 → medium tier → title family (sans on default, serif on editorial)
        chart_medium = resolve(
            raw_chart, data, chart_style_context=_BOARD_CONTEXT, width=700.0
        )
        svg_medium = render_table_svg(
            chart_medium,
            data,
            width=700,
            board_style=board,
        )
        assert escape_attr(title_family) in svg_medium

    def test_table_title_can_use_dbt_oldstyle_tabular_family(self, make_chart):
        """dbt oldstyle tabular font is used at medium/wide widths (serif tier)."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        # Build a modified style with the dbt serif font without mutating global state.
        original_style = get_theme_style()
        new_title = original_style.title.model_copy(
            update={
                "font": original_style.title.font.model_copy(
                    update={
                        "family": "'dbt Serif Oldstyle Tabular', 'Source Serif 4', Georgia, serif"
                    }
                )
            }
        )
        modified_style = original_style.model_copy(update={"title": new_title})
        # build_chart_style_context falls back to get_theme_style() when board_style is
        # None, so pass the modified resolved style explicitly to both resolve()
        # and render_table_svg to inject the custom font without touching global state.
        modified_resolved, modified_context = resolve_style_and_context(modified_style)

        chart = make_chart("table", x=None, y=None, title="Revenue Table")
        data = [{"Rep": "Alice", "Revenue": 2148000}]
        # width=700 → medium tier → serif (uses title_font_family)
        chart = resolve(chart, data, chart_style_context=modified_context)
        svg = render_table_svg(chart, data, width=700, board_style=modified_resolved)

        assert "dbt Serif Oldstyle Tabular" in svg

    def test_table_subtitle_uses_body_font(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            title="Revenue Table",
            subtitle="Current quarter only",
        )
        data = [{"Rep": "Alice", "Revenue": 2148000}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert "Current quarter only" in svg
        assert "Inter Variable" in svg

    def test_table_subtitle_baseline_clears_large_title_block(self, make_chart):
        """Subtitle baseline clears the wrapped title block (a layout invariant).

        Resolved against `stark` so the Inter font-family regex below matches —
        the shipped editorial `default` uses Source Serif for titles and would
        emit a different font-family on the subtitle.
        """
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        stark_style = get_theme_style("stark")
        chart = make_chart(
            "table",
            x=None,
            y=None,
            title="Current table body behavior with long text cells",
            subtitle="This exposes whether body cells wrap, truncate, or use the wrong width budget.",
        )
        data = [
            {
                "Executive Review Summary And Follow Up Owner": "North America enterprise account review with follow-up blockers",
                "Customer Expansion Program Status And Blockers": "Awaiting revised commercial language from procurement",
                "Renewal Risk And Stakeholder Alignment Notes": "Primary sponsor is supportive but wants a joint memo first",
            }
        ]

        chart = resolve(
            chart, data, chart_style_context=resolve_chart_style_context(stark_style)
        )
        svg = render_table_svg(
            chart, data, width=1118, board_style=resolve_style(stark_style)
        )

        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        # Width 1118 is wide tier (>=1100); object titles are width-only.
        wide_size, _, _ = chart_title_spec(
            1118,
            chart_style_context=resolve_chart_style_context(stark_style),
        )
        title_block = re.search(
            rf'<text x="0" y="([0-9.]+)" font-size="{wide_size}"[^>]*>(.*?)</text>',
            svg,
            re.DOTALL,
        )
        # Subtitle fill comes from board_style.muted, not title.subtitle.font.color.
        subtitle_fill = resolve_style(stark_style).muted
        # The subtitle wraps, so its baseline lives on the first tspan.
        subtitle = re.search(
            rf'<text x="0" font-size="([0-9.]+)" fill="{re.escape(subtitle_fill)}" '
            # `[^>]*` tolerates any attributes (e.g. data-authored-kind) between
            # font-family and the tag close — same tolerant style as
            # `subtitle_el` below and `_svg_render.py`'s helpers, so a new
            # attribute on this element doesn't silently miss this regex.
            r'font-family="[^"]*Inter[^"]*"[^>]*><tspan x="0" y="([0-9.]+)">This exposes',
            svg,
        )

        assert title_block is not None
        assert subtitle is not None
        # The regex anchors on the first tspan for the baseline; separately
        # assert the subtitle still renders in full across its wrapped lines.
        subtitle_el = re.search(
            rf'<text x="0" font-size="[0-9.]+" fill="{re.escape(subtitle_fill)}"'
            r"[^>]*>(.*?)</text>",
            svg,
            re.DOTALL,
        )
        assert subtitle_el is not None
        joined = " ".join(
            re.findall(r"<tspan[^>]*>([^<]*)</tspan>", subtitle_el.group(1))
        )
        assert joined == (
            "This exposes whether body cells wrap, truncate, or use the wrong "
            "width budget."
        ), f"subtitle text lost or reflowed unexpectedly: {joined!r}"

        title_line_ys = [
            float(value)
            for value in re.findall(
                r'<tspan x="0" y="([0-9.]+)">', title_block.group(2)
            )
        ]
        assert title_line_ys

        last_title_baseline = max(title_line_ys)
        subtitle_font_size = float(subtitle.group(1))
        subtitle_baseline = float(subtitle.group(2))

        assert subtitle_baseline - last_title_baseline >= subtitle_font_size * 1.2

    def test_table_title_wrap_two_is_default(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        title = (
            "Revenue performance by enterprise segment and partner region "
            "covering all global accounts in fiscal quarter four of 2025"
        )
        chart = make_chart(
            "table",
            x=None,
            y=None,
            title=title,
        )
        data = [{"Rep": "Alice", "Revenue": 2148000}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=220,
            board_style=resolve_style(get_theme_style()),
        )

        assert "<tspan" in svg
        # Wrap-two at 220px truncates this long title; wrap confirms it is split
        # across tspans and the inner <title> carries the full text for hover access.
        assert "Revenue performance" in svg
        assert "…" in svg, "title should be truncated at this narrow width"
        assert f"<title>{title}</title>" in svg

    def test_table_header_inherits_chart_title_overflow_wrap_two(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [
            {
                "Opportunities Subscription Start Date": "2026-01-01",
                "Opportunities Next Steps": "Call customer",
            }
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=260,
            board_style=resolve_style(get_theme_style()),
        )

        assert "Subscription" in svg
        # At narrower widths the cascade may shrink headers enough to fit
        # "Start Date" without truncation; at wider widths it truncates.
        assert "Start" in svg
        assert "Next Steps" in svg or "Next" in svg

    def test_table_column_header_overflow_override_beats_inherited_default(
        self, make_chart
    ):
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "Opportunities Subscription Start Date": TableColumnConfig(
                        header_overflow="clip",
                        width=110,
                    )
                }
            },
        )
        data = [{"Opportunities Subscription Start Date": "2026-01-01"}]
        # Width narrow enough that the header cannot fit at the minimum
        # cascade font size (8px) — forces clip mode to truncate.
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=130,
            board_style=resolve_style(get_theme_style()),
        )

        assert "<title>Opportunities Subscription Start Date</title>" in svg
        # Clip mode truncates without ellipsis; exact chars fitting depend
        # on the cascaded font size.  Verify clip occurred (start visible,
        # end not) without asserting character count.
        assert "Opportunities" in svg
        assert "Start Date</text>" not in svg
        assert "…" not in svg  # no ellipsis in clip mode
        assert "<tspan" not in svg

    def test_table_column_header_truncate_uses_ellipsis(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "Opportunities Subscription Start Date": TableColumnConfig(
                        header_overflow="truncate",
                        width=110,
                    )
                }
            },
        )
        data = [{"Opportunities Subscription Start Date": "2026-01-01"}]
        # Width narrow enough that the header cannot fit at 8px (cascade
        # floor) — forces truncate mode to add ellipsis.
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=130,
            board_style=resolve_style(get_theme_style()),
        )

        assert "<title>Opportunities Subscription Start Date</title>" in svg
        # Truncate ends with ellipsis; exact char count depends on font size.
        assert "…" in svg
        assert "Opportunities" in svg
        assert "<tspan" not in svg

    def test_table_uses_default_pagination_for_auto_height(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )
        from dbt_charts.core.render.controls import interactive_controls

        chart = make_chart("table", x=None, y=None)
        data = [{"name": f"row{i}"} for i in range(25)]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        # Interactive-host contract: a single page renders. A static export
        # instead pre-renders every page into a JS-toggled group — see
        # TestStaticMultiPagePagination in test_table_pagination_controls.py.
        with interactive_controls(True):
            svg = render_table_svg(
                chart,
                data,
                width=400,
                board_style=resolve_style(get_theme_style()),
            )

        assert "row19" in svg
        assert "row20" not in svg
        # Default pagination (page_rows=20) shows the interactive paginator.
        assert "dbt-paginator" in svg

    def test_small_table_does_not_paginate_by_default(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"name": f"row{i}"} for i in range(5)]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert "row4" in svg
        assert "dbt-paginator" not in svg
        assert "more rows" not in svg

    def test_table_chart_pagination_override_limits_visible_rows(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )
        from dbt_charts.core.render.controls import interactive_controls

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={"table": {"pagination": {"enabled": True, "page_rows": 5}}},
        )
        data = [{"name": f"row{i}"} for i in range(10)]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        with interactive_controls(True):
            svg = render_table_svg(
                chart,
                data,
                width=400,
                board_style=resolve_style(get_theme_style()),
            )

        assert "row4" in svg
        assert "row5" not in svg
        # Pagination active: paginator <g> replaces the static "+N more rows".
        assert "dbt-paginator" in svg
        assert "+ " not in svg or "more rows" not in svg

    def test_table_chart_enabled_pagination_inherits_default_page_rows(
        self, make_chart
    ):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )
        from dbt_charts.core.render.controls import interactive_controls

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={"table": {"pagination": {"enabled": True}}},
        )
        data = [{"name": f"row{i}"} for i in range(25)]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        with interactive_controls(True):
            svg = render_table_svg(
                chart,
                data,
                width=400,
                board_style=resolve_style(get_theme_style()),
            )

        assert "row19" in svg
        assert "row20" not in svg
        # Paginator <g> shown instead of "+N more rows".
        assert "dbt-paginator" in svg

    def test_table_explicit_height_respects_bottom_padding_in_row_count(
        self, make_chart
    ):
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )
        from dbt_charts.core.render.controls import interactive_controls

        tc = resolve_style(get_theme_style()).chart_defaults.table
        row_height = int(tc.row.height)
        # Dynamic header_height fallback (matches renderer):
        # header_font_size * 2 + header_rule_width + 4
        if tc.header.height is not None:
            header_height = int(tc.header.height)
        else:
            _hfs = int(tc.header.font.size or tc.font.size)
            _hrw = int(tc.header.rule.width or 0)
            header_height = _hfs * 2 + _hrw + 4
        header_body_gap = int(row_height * 0.25)
        padding_y = int(tc.outer_padding)
        bottom_padding = int(tc.bottom_padding)
        # Height must include header_body_gap so content is not clipped. Also
        # include pagination_control_height — with 5 rows and a 2-row budget,
        # pagination fires; height-limited pagination now properly reserves
        # its control band so the controls land inside the viewBox.
        from dbt_charts.core.render.chart.table import _PAGINATION_CONTROL_HEIGHT

        explicit_height = (
            header_height
            + header_body_gap
            + padding_y
            + bottom_padding
            + (2 * row_height)
            + _PAGINATION_CONTROL_HEIGHT
        )

        chart = make_chart("table", x=None, y=None)
        # Data values must be longer than the header ("Name" = 4 chars)
        # so the column is wide enough for the header at 14pt under the
        # char-count heuristic (CI has no fonttools). At 14pt, max_chars
        # for a 4-char header needs data of 5+ chars.
        data = [{"name": f"row_{i:02d}"} for i in range(5)]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        with interactive_controls(True):
            svg = render_table_svg(
                chart,
                data,
                width=800,
                height=explicit_height,
                board_style=resolve_style(get_theme_style()),
            )

        assert "row_01" in svg
        assert "row_02" not in svg


class TestHeaderLink:
    """Test that header_link produces clickable SVG <a> tags."""

    def test_header_link_produces_anchor_tag(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "table": {
                    "columns": {
                        "name": TableColumnConfig(
                            header_link="/inspect/string_column/?column=name",
                        ),
                    }
                }
            },
        )
        data = [{"name": "Alice"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert '<a href="/inspect/string_column/?column=name">' in svg
        assert "</a>" in svg

    def test_no_header_link_produces_no_anchor(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"name": "Alice"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert "<a " not in svg

    def test_header_link_escapes_special_chars(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "table": {
                    "columns": {
                        "name": TableColumnConfig(
                            header_link='/inspect/col/?x=1&y="2"',
                        ),
                    }
                }
            },
        )
        data = [{"name": "Alice"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert "&amp;" in svg
        assert "&#x27;" in svg or "&quot;" in svg


class TestTableColumnConfig:
    """Test implementation-backed table column configuration behavior."""

    def test_column_format_applies_to_numeric_cells(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "table": {
                    "columns": {
                        "revenue": TableColumnConfig(format="currency_full"),
                    }
                }
            },
        )
        data = [{"revenue": 1234.5}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        # Three-lane rendering splits prefix ($) and number into separate tspans
        assert "$</tspan>" in svg
        assert "1,234.50</tspan>" in svg

    def test_column_align_overrides_default_alignment(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "table": {
                    "columns": {
                        "name": TableColumnConfig(align="right"),
                    }
                }
            },
        )
        data = [{"name": "Alice"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        assert 'text-anchor="end"' in svg
        assert ">Alice</text>" in svg

    def test_column_width_hint_changes_following_column_position(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        data = [{"name": "Alice", "city": "Seattle"}]
        default_chart = make_chart("table", x=None, y=None)
        default_chart = resolve(default_chart, data, chart_style_context=_BOARD_CONTEXT)
        default_svg = render_table_svg(
            default_chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        wide_chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "table": {
                    "columns": {
                        "name": TableColumnConfig(width=220),
                        "city": TableColumnConfig(),
                    }
                }
            },
        )
        wide_chart = resolve(wide_chart, data, chart_style_context=_BOARD_CONTEXT)
        wide_svg = render_table_svg(
            wide_chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        default_match = re.search(
            r'<text x="([^"]+)" y="[^"]+" [^>]*>City</text>',
            default_svg,
        )
        wide_match = re.search(
            r'<text x="([^"]+)" y="[^"]+" [^>]*>City</text>',
            wide_svg,
        )

        assert default_match is not None
        assert wide_match is not None
        assert float(wide_match.group(1)) > float(default_match.group(1))

    def test_dynamic_table_style_columns_apply_and_hide_helper_columns(
        self, make_chart
    ):
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "table": {
                    "columns": {
                        "orders_region": TableColumnConfig(),
                        "orders_total_revenue": TableColumnConfig(
                            background="_df_table_background_orders_total_revenue",
                            font={
                                "color": "_df_table_color_orders_total_revenue",
                                "weight": "_df_table_font_weight_orders_total_revenue",
                            },
                        ),
                    }
                }
            },
        )
        data = [
            {
                "orders_region": "East",
                "orders_total_revenue": 125,
                "_df_table_background_orders_total_revenue": "#4FBC89",
                "_df_table_color_orders_total_revenue": "#14532d",
                "_df_table_font_weight_orders_total_revenue": "bold",
            }
        ]

        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=500,
            board_style=resolve_style(get_theme_style()),
        )

        assert "_df_table_background_orders_total_revenue" not in svg
        assert "_df_table_color_orders_total_revenue" not in svg
        assert "_df_table_font_weight_orders_total_revenue" not in svg
        assert 'fill="#4FBC89"' in svg
        assert 'fill="#14532d"' in svg
        assert 'font-weight="bold"' in svg


class TestCellLink:
    """Test that column-level `link` produces clickable cell <a> tags."""

    def test_static_link_wraps_cell_in_anchor(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "table": {
                    "columns": {
                        "name": TableColumnConfig(
                            link="https://example.com/details",
                        ),
                    }
                }
            },
        )
        data = [{"name": "Alice"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert '<a href="https://example.com/details">' in svg
        assert ">Alice</text>" in svg

    def test_column_id_link_uses_row_value(self, make_chart):
        """When link equals a column ID, use that column's per-row value as the URL."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "name": TableColumnConfig(link="profile_url"),
                    "profile_url": TableColumnConfig(visible=False),
                }
            },
        )
        data = [
            {"name": "Alice", "profile_url": "https://example.com/alice"},
            {"name": "Bob", "profile_url": "https://example.com/bob"},
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert '<a href="https://example.com/alice">' in svg
        assert '<a href="https://example.com/bob">' in svg

    def test_template_link_substitutes_column_values(self, make_chart):
        """Link templates with {{ col }} placeholders get per-row substitution."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "name": TableColumnConfig(
                        link="https://app.example/u/{{ user_id }}",
                    ),
                    "user_id": TableColumnConfig(visible=False),
                }
            },
        )
        data = [{"name": "Alice", "user_id": "42"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert '<a href="https://app.example/u/42">' in svg

    def test_template_with_none_placeholder_renders_plain_text(self, make_chart):
        """When a template placeholder resolves to None, render plain text (no link)."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "name": TableColumnConfig(
                        link="https://app.example/u/{{ user_id }}",
                    ),
                    "user_id": TableColumnConfig(visible=False),
                }
            },
        )
        data = [{"name": "Alice", "user_id": None}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert svg.count("<a ") == 0
        assert ">Alice</text>" in svg

    def test_null_link_value_renders_plain_text(self, make_chart):
        """When column-ID link resolves to None/null, render plain text (no anchor)."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "name": TableColumnConfig(link="profile_url"),
                    "profile_url": TableColumnConfig(visible=False),
                }
            },
        )
        data = [{"name": "Alice", "profile_url": None}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert ">Alice</text>" in svg
        # No anchor tag for this row
        assert svg.count("<a ") == 0

    def test_link_escapes_special_chars(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "name": TableColumnConfig(
                        link='https://example.com/?a=1&b="2"',
                    ),
                }
            },
        )
        data = [{"name": "Alice"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert "&amp;" in svg

    def test_no_link_produces_no_cell_anchor(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"name": "Alice"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert "<a " not in svg


class TestColumnIdStyleResolution:
    """`background`/`font.color`/`font.weight` resolve column-ID-first, like `link`.

    ``TestTableColumnConfig::test_dynamic_table_style_columns_apply_and_hide_helper_columns``
    already pins the single-row mechanism for all three fields at once. This
    test covers the increment that a single-row fixture can't: two rows
    resolving to *different* per-row values, with assertions scoped to the
    SVG element each field actually paints (``background`` on the cell
    ``<rect>``, ``font.color`` on the cell ``<text>``) so a background/color
    swap in the renderer would be caught.
    """

    def test_column_id_style_varies_per_row_by_element(self, make_chart):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.chart.authored import (
            FontStyle,
            TableColumnConfig,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "name": TableColumnConfig(
                        background="row_bg",
                        font=FontStyle(color="row_fg", weight="row_weight"),
                    ),
                }
            },
        )
        data = [
            {
                "name": "Alice",
                "row_bg": "#166534",
                "row_fg": "#f0fdf4",
                "row_weight": "bold",
            },
            {
                "name": "Bob",
                "row_bg": "#991b1b",
                "row_fg": "#fef2f2",
                "row_weight": "normal",
            },
        ]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

        # background -> the cell <rect>, per row.
        assert re.search(r'<rect[^>]*fill="#166534"', svg)
        assert re.search(r'<rect[^>]*fill="#991b1b"', svg)
        # font.color -> the cell <text>, per row.
        assert re.search(r'<text[^>]*fill="#f0fdf4"', svg)
        assert re.search(r'<text[^>]*fill="#fef2f2"', svg)
        # font.weight -> per row, both resolved values present.
        assert 'font-weight="bold"' in svg
        assert 'font-weight="normal"' in svg


class TestFitCascade:
    """Test that the fit cascade reduces padding and font size when content overflows."""

    def test_narrow_table_reduces_font_size(self, make_chart):
        """When columns are too narrow for content, font size should step down."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [
            {
                "Company": "Apex Technologies International",
                "Revenue": 24730000,
                "Growth Rate": 12.5,
                "Operating Margin": 38.2,
            }
        ]
        # Very narrow width forces overflow → cascade should kick in.
        # May reach 11px or go further to 8px depending on content width.
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=300,
            board_style=resolve_style(get_theme_style()),
        )
        # Font should be below the default 13px
        assert 'font-size="13"' not in svg

    def test_wide_table_keeps_default_font_size(self, make_chart):
        """When there's plenty of room, body font size stays at the theme's
        default — i.e. the cascade does NOT engage at wide widths."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Name": "Alice", "Score": 95}]
        chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
        svg = render_table_svg(
            chart,
            data,
            width=800,
            board_style=resolve_style(get_theme_style()),
        )
        # Body text stays at the resolved theme default (which inherits
        # root style.font.size after the apparatus-sizing refactor).
        expected = int(get_theme_style().font.size)
        assert f'font-size="{expected}"' in svg


class TestTableRules:
    """Test table rule configuration (header rules, row rules)."""

    def test_minimal_preset_has_no_rules(self, make_chart):
        """Minimal preset renders with no rules at all — whitespace only."""
        import dataclasses

        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [
            {"Name": "Alice", "Score": 95},
            {"Name": "Bob", "Score": 87},
            {"Name": "Carol", "Score": 92},
        ]
        # Explicit no-rules style (simulates minimal variant after preset)
        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        new_table = tc.model_copy(
            update={
                "header": tc.header.model_copy(
                    update={"rule": tc.header.rule.model_copy(update={"width": 0})}
                ),
                "row": tc.row.model_copy(
                    update={
                        "rule": tc.row.rule.model_copy(update={"width": 0}),
                        "roles": tc.row.roles.model_copy(
                            update={
                                "summary": tc.row.roles.summary.model_copy(
                                    update={"rule_width": 0}
                                )
                            }
                        ),
                    }
                ),
            }
        )
        custom_context = dataclasses.replace(es, table=new_table)
        chart = resolve(chart, data, chart_style_context=custom_context)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        assert 'shape-rendering="crispEdges"' not in svg

    def test_row_rules_when_configured(self, make_chart):
        """When row_rule_width is set, row rules should render between rows."""
        import dataclasses

        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [
            {"Name": "Alice", "Score": 95},
            {"Name": "Bob", "Score": 87},
            {"Name": "Carol", "Score": 92},
        ]
        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        new_table = tc.model_copy(
            update={
                "header": tc.header.model_copy(
                    update={"rule": tc.header.rule.model_copy(update={"width": 0})}
                ),
                "row": tc.row.model_copy(
                    update={
                        "rule": tc.row.rule.model_copy(update={"width": 0.5}),
                        "roles": tc.row.roles.model_copy(
                            update={
                                "summary": tc.row.roles.summary.model_copy(
                                    update={"rule_width": 0.5}
                                )
                            }
                        ),
                    }
                ),
            }
        )
        custom_context = dataclasses.replace(es, table=new_table)
        # Zero out BI header/summary rules to test body rules alone — done above
        chart = resolve(chart, data, chart_style_context=custom_context)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        import re

        rules = re.findall(r'<rect [^>]*shape-rendering="crispEdges"', svg)
        # 2 row rules (after rows 0, 1; row 2's rule is suppressed because
        # it is the last row — nothing follows to separate from).
        assert len(rules) == 2, f"Expected 2 row rules, got {len(rules)}"
        # Sub-pixel rule widths (e.g. 0.5) are promoted to 1px so they remain
        # visible. A 0.5px rect is invisible on every display we ship to.
        for rule in rules:
            assert 'height="1"' in rule, (
                f"Row rule must have height='1' (sub-pixel widths promoted to 1px). "
                f"got: {rule}"
            )


class TestPerChartErrorHandling:
    """Test that ChartDataError renders inline error SVG instead of crashing."""

    def test_kpi_multi_row_renders_callout_svg(self):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.callout import render_callout_svg

        svg = render_callout_svg(
            message="expects exactly 1 row",
            width=400,
            height=100,
            callout_style=resolve_chart_style_context(get_theme_style()).callout,
        )
        assert "Callout" not in svg
        assert "revenue_kpi" not in svg
        assert "expects exactly 1 row" in svg
        assert "dbt-chart-callout" in svg
