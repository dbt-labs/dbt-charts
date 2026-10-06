"""Tests for P4 table style variants (minimal / bi / classic).

These tests exercise the renderer with TableChartStyle configurations that
mirror each preset's table settings. The preset files themselves are
validated via test_style_preset_loading.py elsewhere.
"""

import dataclasses
import re

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


def _style_with(**overrides):
    """Build a ResolvedChartsStyle with defaults plus TableChartStyle overrides.

    Maps old flat TableChartStyle key names to nested TableChartStyle paths.
    """

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

            row_updates["stripe"] = (
                TableRowStripeStyle(color=value) if value is not None else None
            )
        elif key == "row_rule_width":
            row_rule_updates.update(_divider_width(value))
        elif key == "summary_rule_width":
            summary_updates["divider"] = tc.row.roles.summary.divider.model_copy(
                update=_divider_width(value)
            )
        elif key == "summary_font_weight":
            from dbt_charts.core.compile.models.primitives import FontStyle

            if tc.row.roles.summary.font is None:
                summary_updates["font"] = FontStyle(weight=value)
            else:
                summary_updates["font"] = tc.row.roles.summary.font.model_copy(
                    update={"weight": value}
                )
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


class TestMinimalVariant:
    """Minimal: no weight variation, no rules, no backgrounds."""

    def _minimal_style(self):
        return _style_with(
            header_font_weight="400",
            header_color="#222222",
            header_background=None,
            header_rule_width=0,
            row_rule_width=0,
            summary_rule_width=0,
            summary_font_weight="400",
            symbol_mode="anchors",
        )

    def test_no_rules(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Name": "Alice", "Score": 95}, {"Name": "Bob", "Score": 87}]
        _custom_ctx = self._minimal_style()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        assert 'shape-rendering="crispEdges"' not in svg

    def test_no_weight_variation_on_headers(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Name": "Alice", "Score": 95}]
        _custom_ctx = self._minimal_style()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        # Header renders with font-weight 400, not 600
        headers = re.findall(
            r'<text[^>]*font-weight="(\d+)"[^>]*>[^<]*(?:Name|Score)', svg
        )
        if headers:
            assert all(w == "400" for w in headers), (
                f"Expected all headers at 400, got {headers}"
            )


class TestBIVariant:
    """BI (default): small semibold header, gray fill, continuous rule."""

    def _bi_style(self):
        return _style_with(
            header_font_size=11,
            header_font_weight="600",
            header_font_compact="500",
            header_color="#222222",
            header_background="#EDEFF2",
            header_rule_width=1,
            header_rule_continuous=True,
            summary_rule_width=1,
            symbol_mode="anchors",
        )

    def test_header_background_present(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Name": "Alice", "Score": 95}]
        _custom_ctx = self._bi_style()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        assert "#EDEFF2" in svg

    def test_header_size_11(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Name": "Alice", "Score": 95}]
        _custom_ctx = self._bi_style()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        # Header text at font-size 11
        headers = re.findall(
            r'<text[^>]*font-size="(\d+)"[^>]*>[^<]*(?:Name|Score)', svg
        )
        if headers:
            assert "11" in headers

    def test_summary_rule_renders_without_row_rules(self, make_chart):
        """Core P4 feature: summary rule above without body row rules."""

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = self._bi_style()
        tc = es.table
        es = dataclasses.replace(
            es,
            table=tc.model_copy(
                update={"row": tc.row.model_copy(update={"role": "row_role"})}
            ),
        )
        data = [
            {"Name": "Alice", "Score": 95, "row_role": "value"},
            {"Name": "Bob", "Score": 87, "row_role": "value"},
            {"Name": "Total", "Score": 182, "row_role": "total"},
        ]
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        rules = re.findall(r'<rect [^>]*shape-rendering="crispEdges"', svg)
        # 1 header rule + 2 double rule rects above total = 3
        # (no body row rules because row_rule_width = 0)
        assert len(rules) == 3, (
            f"Expected 3 rules (header + double above total), got {len(rules)}"
        )

    def test_compact_body_uses_compact_header_weight(self, make_chart):
        """When body=11, header.font_compact.weight (500) applies instead of 600."""

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = self._bi_style()
        tc = es.table
        es = dataclasses.replace(
            es,
            table=tc.model_copy(
                update={"font": tc.font.model_copy(update={"size": 11})}
            ),
        )  # compact body
        data = [{"Name": "Alice", "Score": 95}]
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        # Header should render at 500 (compact override), not 600
        headers = re.findall(r'<text[^>]*font-weight="(\d+)"[^>]*>[^<]*Name', svg)
        if headers:
            assert headers[0] == "500", (
                f"Expected header weight 500 at body=11, got {headers[0]}"
            )


class TestClassicVariant:
    """Classic: Source Serif font, no weight variation, per-column header rules."""

    def _classic_style(self):
        return _style_with(
            font_family="'Source Serif 4', Georgia, 'Times New Roman', serif",
            header_font_weight="400",
            header_color="#222222",
            header_background=None,
            header_rule_width=1,
            header_rule_continuous=False,
            row_rule_width=0,
            summary_rule_width=1,
            summary_font_weight="400",
            symbol_mode="anchors",
        )

    def test_uses_source_serif_font(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Name": "Alice", "Score": 95}]
        _custom_ctx = self._classic_style()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        assert "Source Serif 4" in svg

    def test_per_column_header_rules(self, make_chart):
        """Classic uses per-column header rules (not continuous)."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Name": "Alice", "Score": 95}, {"Name": "Bob", "Score": 87}]
        _custom_ctx = self._classic_style()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        import re

        # Two columns → two separate header rule rects (per-column gaps)
        rules = re.findall(r'<rect [^>]*shape-rendering="crispEdges"', svg)
        assert len(rules) >= 2, (
            f"Expected >=2 per-column header rules, got {len(rules)}"
        )

    def test_double_rule_above_total(self, make_chart):

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = self._classic_style()
        tc = es.table
        es = dataclasses.replace(
            es,
            table=tc.model_copy(
                update={"row": tc.row.model_copy(update={"role": "row_role"})}
            ),
        )
        data = [
            {"Name": "Alice", "Score": 95, "row_role": "value"},
            {"Name": "Total", "Score": 95, "row_role": "total"},
        ]
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        rules = re.findall(r'<rect [^>]*shape-rendering="crispEdges"', svg)
        # Per-column header rules (2 cols = 2 rects) + 2 double-rule rects
        # above total = 4 minimum
        assert len(rules) >= 4, f"Expected >=4 rules, got {len(rules)}"

    def test_no_summary_weight_variation(self, make_chart):
        """Classic summary rows use same weight as body (400), not medium."""

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = self._classic_style()
        tc = es.table
        es = dataclasses.replace(
            es,
            table=tc.model_copy(
                update={"row": tc.row.model_copy(update={"role": "row_role"})}
            ),
        )
        data = [
            {"Name": "Alice", "Score": 95, "row_role": "value"},
            {"Name": "Total", "Score": 95, "row_role": "total"},
        ]
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        # Summary row "Total" should render with font-weight 400, not 500
        total_with_weight = re.findall(
            r'<text[^>]*font-weight="(\d+)"[^>]*>[^<]*Total', svg
        )
        for w in total_with_weight:
            assert w == "400", f"Expected Total row weight 400 in Classic, got {w}"


class TestClassicSerifNumerals:
    """Classic variant numeric cells use the table's serif font, not sans."""

    def _classic_serif_style(self):

        es = _style_with(
            font_family="'Source Serif 4', Georgia, 'Times New Roman', serif",
            header_font_weight="400",
            header_color="#222222",
            header_background=None,
            header_rule_width=1,
            header_rule_continuous=False,
            row_rule_width=0,
            summary_rule_width=1,
            summary_font_weight="400",
            symbol_mode="anchors",
        )
        return es

    def test_classic_variant_uses_serif_for_numerics(self, make_chart):
        """Classic variant: numeric cell font-family contains 'Source Serif'."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Company": "Apex Technologies", "Revenue": 24730000}]
        _custom_ctx = self._classic_serif_style()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        assert "Source Serif" in svg, (
            "Expected Source Serif in numeric cell font-family"
        )
        assert "dbt Sans Tabular" not in svg, (
            "Classic numeric cells must not use the dbt Sans Tabular stack"
        )

    def test_classic_tabular_nums_css_present(self, make_chart):
        """Classic variant: tabular-nums CSS is still emitted for digit alignment."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Company": "Apex Technologies", "Revenue": 24730000}]
        _custom_ctx = self._classic_serif_style()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        assert "font-variant-numeric: tabular-nums lining-nums" in svg, (
            "tabular-nums CSS must be emitted so Source Serif digits align in columns"
        )

    def test_default_variant_still_uses_sans_tabular(self, make_chart):
        """Default (Inter) variant: numeric cells still use the curated sans stack."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Company": "Apex Technologies", "Revenue": 24730000}]
        es = _style_with()  # default font — Inter
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        assert "dbt Sans Tabular" in svg, (
            "Default-font numeric cells must use the curated dbt Sans Tabular stack"
        )

    def test_numeric_cells_use_table_font_family(self, make_chart):
        """Numeric cells should use Source Serif when font_family is overridden."""

        from dbt_charts.core.compile.models.primitives import FontStyle
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Name": "Alice", "Score": 95}]
        es = _style_with()
        tc = es.table
        es = dataclasses.replace(
            es,
            table=tc.model_copy(
                update={
                    "font": FontStyle(
                        family="'Source Serif 4', Georgia, serif",
                        size=tc.font.size,
                    )
                }
            ),
        )
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        # Numeric cell "95" should use Source Serif, not dbt Sans Tabular
        assert "dbt Sans Tabular" not in svg, (
            "Numeric cells should use the table's serif font, not the sans stack"
        )
        assert "Source Serif 4" in svg

    def test_default_font_still_uses_sans_numeric_stack(self, make_chart):
        """When font_family is default (Inter), numeric cells use dbt Sans Tabular."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Name": "Alice", "Score": 95}]
        es = _style_with()  # default font
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=_BOARD_STYLE,
        )
        assert "dbt Sans Tabular" in svg


class TestAdjacentPerColumnRuleGap:
    """Per-column rules maintain minimum gap at narrow widths."""

    def _classic_style(self):
        return _style_with(
            font_family="'Source Serif 4', Georgia, 'Times New Roman', serif",
            header_font_weight="400",
            header_color="#222222",
            header_background=None,
            header_rule_width=1,
            header_rule_continuous=False,
            row_rule_width=0,
            summary_rule_width=1,
            summary_font_weight="400",
            symbol_mode="anchors",
        )

    def _extract_rule_rects(self, svg: str) -> list[dict[str, float]]:
        """Extract crispEdges rect positions from SVG."""
        rects = []
        for m in re.finditer(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" '
            r'height="([\d.]+)"[^>]*shape-rendering="crispEdges"',
            svg,
        ):
            x = float(m.group(1))
            w = float(m.group(3))
            rects.append({"x1": x, "x2": x + w})
        return rects

    def test_adjacent_per_column_rules_maintain_min_gap(self, make_chart):
        """Adjacent numeric column rules must not touch at any tested width.

        Minimum gap between adjacent rules: 4px (2 * _RULE_GAP=2 per side).
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        data = [
            {"Revenue": 24730000, "Profit": 8120000, "Margin": 0.328},
            {"Revenue": 31450000, "Profit": 10200000, "Margin": 0.324},
        ]
        _custom_ctx = self._classic_style()
        chart = resolve(
            make_chart("table", x=None, y=None),
            [],
            chart_style_context=_custom_ctx,
        )
        # Test at multiple widths including pathologically narrow cases
        for w in (400, 350, 300, 250, 200):
            svg = render_table_svg(
                chart,
                data,
                width=w,
                board_style=_BOARD_STYLE,
            )
            rects = self._extract_rule_rects(svg)
            if len(rects) < 2:
                continue  # not enough rules at this width to test adjacency
            # Sort by x1 to find adjacent pairs
            rects.sort(key=lambda r: r["x1"])
            for i in range(len(rects) - 1):
                gap = rects[i + 1]["x1"] - rects[i]["x2"]
                assert gap >= 4.0, (
                    f"At width={w}: adjacent rule gap {gap:.1f}px < 4px "
                    f"(rules at x=[{rects[i]['x1']:.1f},{rects[i]['x2']:.1f}] "
                    f"and [{rects[i + 1]['x1']:.1f},{rects[i + 1]['x2']:.1f}])"
                )


class TestHeaderSizeLinkingGroupDecision:
    """Header font size is a GROUP DECISION with body font size.

    When a style leaves ``header_font_size`` unset (or explicitly equal
    to ``font_size``), header and body scale together through the fit
    cascade — one shared size at every step. Only a style that
    *explicitly* sets a different ``header_font_size`` (e.g. BI at 11px
    with body at 14px) opts into the independent apparatus-tier behavior.

    This guards against the bug where BI's ``header_font_size: 11`` leaks
    into Minimal/Classic via preset inheritance and produces body=14
    with header=11 — a visual regression the author never asked for.
    """

    def _sizes(self, svg):
        # Headers always carry explicit font-weight AND text-anchor on the
        # outer <text> element.  The chart-title <text> carries font-weight
        # but NOT text-anchor; body cells carry text-anchor but NOT
        # font-weight (no conditional formatting in these test datasets).
        # Use lookahead to match both attributes regardless of order.
        header_sizes = {
            int(m.group(1))
            for m in re.finditer(
                r'<text(?=[^>]*\bfont-weight=")(?=[^>]*\btext-anchor=")[^>]*\bfont-size="(\d+)"',
                svg,
            )
        }
        body_sizes = {
            int(m.group(1))
            for m in re.finditer(
                r'<text[^>]*y="[\d.]+"[^>]*font-size="(\d+)"[^>]*>[^<]',
                svg,
            )
        }
        return header_sizes, body_sizes

    def _minimal_style(self):
        """Minimal-style config. Clears header_font_size so header tracks body."""
        return _style_with(
            header_font_size=None,  # clear theme default to enable body-tracking
            header_font_weight="400",
            header_color="#222222",
            header_background="transparent",
            stripe_color="transparent",
            header_rule_width=0,
            row_rule_width=0,
            summary_rule_width=0,
            summary_font_weight="400",
            symbol_mode="anchors",
        )

    def _classic_style(self):
        """Classic-style config. Clears header_font_size so header tracks body."""
        return _style_with(
            header_font_size=None,  # clear theme default to enable body-tracking
            font_family="'Source Serif 4', Georgia, serif",
            header_font_weight="400",
            header_background="transparent",
            stripe_color="transparent",
            header_rule_width=1,
            header_rule_continuous=False,
            row_rule_width=0,
            summary_rule_width=1,
            summary_font_weight="400",
        )

    def _bi_style(self):
        """BI-style config. EXPLICITLY sets header_font_size=11 to opt
        into apparatus-tier behavior."""
        return _style_with(
            header_font_size=11,
            header_font_weight="600",
            header_background="#EDEFF2",
            header_rule_width=1,
            header_rule_continuous=True,
        )

    def test_minimal_header_body_linked_at_wide_width(self, make_chart):
        """Minimal: when content fits at the default body size, header must
        track body — not stay at the BI-inherited 11px apparatus tier.

        This is the exact user-reported bug: Minimal specimens were
        rendering with body=13 and header=11 at comfortable widths.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        # Wider data values so columns get enough room that body stays at 14
        data = [
            {"Description": "Acme Corporation", "Value": 849123456},
            {"Description": "Beta LLC", "Value": 102345678},
        ]
        _custom_ctx = self._minimal_style()
        chart = resolve(
            make_chart("table", x=None, y=None),
            [],
            chart_style_context=_custom_ctx,
        )
        for w in (1200, 1000, 800):
            svg = render_table_svg(
                chart,
                data,
                width=w,
                board_style=_BOARD_STYLE,
            )
            hs, bs = self._sizes(svg)
            assert hs == bs, (
                f"Minimal at w={w}: header sizes {hs} must equal body "
                f"sizes {bs} — header must track body, not stay at 11."
            )

    def test_minimal_header_body_linked_at_narrow_width(self, make_chart):
        """Minimal: header and body stay linked through the cascade."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        data = [
            {
                "Company": "Acme Corp",
                "Revenue": 849000000,
                "Growth": 0.23,
                "Margin": 0.28,
            },
            {
                "Company": "Beta Inc",
                "Revenue": 456000000,
                "Growth": -0.08,
                "Margin": 0.22,
            },
        ]
        _custom_ctx = self._minimal_style()
        chart = resolve(
            make_chart("table", x=None, y=None),
            [],
            chart_style_context=_custom_ctx,
        )
        for w in (600, 500, 400, 300, 250):
            svg = render_table_svg(
                chart,
                data,
                width=w,
                board_style=_BOARD_STYLE,
            )
            hs, bs = self._sizes(svg)
            assert hs == bs, f"Minimal at w={w}: header {hs} must equal body {bs}"
            assert len(hs) == 1, (
                f"Minimal at w={w}: expected single header size, got {hs}"
            )

    def test_classic_header_body_linked_at_wide_width(self, make_chart):
        """Classic (same bug surface as Minimal): header must track body."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        data = [
            {"Description": "Acme Corporation", "Value": 849123456},
            {"Description": "Beta LLC", "Value": 102345678},
        ]
        _custom_ctx = self._classic_style()
        chart = resolve(
            make_chart("table", x=None, y=None),
            [],
            chart_style_context=_custom_ctx,
        )
        for w in (1200, 1000, 800):
            svg = render_table_svg(
                chart,
                data,
                width=w,
                board_style=_BOARD_STYLE,
            )
            hs, bs = self._sizes(svg)
            assert hs == bs, f"Classic at w={w}: header {hs} must equal body {bs}"

    def test_classic_header_body_linked_at_narrow_width(self, make_chart):
        """Classic: header and body stay linked through cascade."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        data = [
            {
                "Company": "Acme Corp",
                "Revenue": 849000000,
                "Growth": 0.23,
                "Margin": 0.28,
            },
            {
                "Company": "Beta Inc",
                "Revenue": 456000000,
                "Growth": -0.08,
                "Margin": 0.22,
            },
        ]
        _custom_ctx = self._classic_style()
        chart = resolve(
            make_chart("table", x=None, y=None),
            [],
            chart_style_context=_custom_ctx,
        )
        for w in (600, 500, 400, 300, 250):
            svg = render_table_svg(
                chart,
                data,
                width=w,
                board_style=_BOARD_STYLE,
            )
            hs, bs = self._sizes(svg)
            assert hs == bs, f"Classic at w={w}: header {hs} must equal body {bs}"

    def test_bi_keeps_apparatus_tier_independent_of_body(self, make_chart):
        """BI explicitly sets header=11; body inherits root font.size from
        the cascade. They must NOT be locked together — BI's apparatus tier
        is independent by design."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        data = [{"Company": "Acme", "Revenue": 849000000}]
        _custom_ctx = self._bi_style()
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=1000,
            board_style=_BOARD_STYLE,
        )
        hs, bs = self._sizes(svg)
        expected_body = int(get_theme_style().font.size)
        assert hs == {11}, f"BI header must render at 11, got {hs}"
        assert expected_body in bs, (
            f"BI body must include root font.size ({expected_body}), got body={bs}"
        )


class TestFitCascadeFloor:
    """Fit cascade stops at 11px — truncation handles remaining overflow."""

    def test_narrow_table_stops_at_11px(self, make_chart):
        """At very narrow widths, cascade stops at 11px; no sub-11 fonts."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        # Many columns in a narrow space to force cascade to engage fully
        data = [
            {
                "A": "Alpha",
                "B": 12345,
                "C": 67890,
                "D": 11111,
                "E": 22222,
                "F": 33333,
            }
        ]
        # wrap disabled: text overflow drives the cascade (wrap mode would
        # wrap "Alpha" instead of truncating, so the cascade wouldn't fire).

        es = _style_with()
        tc = es.table
        es = dataclasses.replace(es, table=tc.model_copy(update={"wrap": False}))
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=200,
            board_style=_BOARD_STYLE,
        )
        assert 'font-size="8"' not in svg, (
            "Cascade should stop at 11px — 8px text found at narrow width."
        )
        assert 'font-size="11"' in svg, (
            "Expected cascade to engage and set font-size to 11px."
        )


class TestAnchorsKeepsMagnitudeSuffix:
    """`symbol_mode: anchors` strips a repeated currency prefix / unit suffix on
    non-anchor rows, but must keep the SI magnitude suffix (K/M/B): it varies
    per row and carries the value, so dropping it renders 3000 as a bare "3".
    Regression for the default-SI-format interaction with anchors.
    """

    def test_magnitude_suffix_present_on_every_kilo_row(self, make_chart):
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        # Two kilo-range rows with no explicit format → SI default (.3~s):
        # 1500 -> "1.5" + "K", 3000 -> "3" + "K".
        data = [{"revenue": 1500}, {"revenue": 3000}]
        _custom_ctx = _style_with(symbol_mode="anchors")
        chart = resolve(chart, [], chart_style_context=_custom_ctx)
        svg = render_table_svg(chart, data, width=400, board_style=_BOARD_STYLE)

        # Both rows keep their "K" suffix tspan — the anchor row AND the middle
        # row. Without the fix the middle row's "K" is stripped (count == 1).
        assert svg.count(">K</tspan>") == 2, (
            "Every kilo-range row must keep its magnitude suffix under anchors; "
            f"got {svg.count('>K</tspan>')} K-suffixes in: {svg}"
        )
