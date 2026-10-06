"""Tests for table conditional formatting: scale-based color mapping.

Covers:
- ScaleTargetConfig / ColumnScaleConfig model validation
- Color interpolation across multi-stop palettes
- Scale domain inference from data vs explicit min/max
- Precedence: base column style -> scale -> when rules
- Null handling and out-of-range clamping
- Integration with table rendering pipeline

Threshold (``when``) rules are authored under the chart-level
``conditional_formatting`` block and covered in
``tests/core/test_conditional_formatting.py`` and
``tests/core/test_table_renderer_reads_cf_block.py``. This file focuses on
the continuous ``scale`` encoding plus the interaction between ``scale``
and block-sourced rules in ``resolve_cell_conditional_styles``.
"""

import dataclasses
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.authored import (
    ColumnScaleConfig,
    ConditionalRule,
    ScaleTargetConfig,
    TableColumnConfig,
)
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.table_support import (
    compute_scale_domain,
    interpolate_scale_color,
    resolve_cell_conditional_styles,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestScaleTargetConfigModel:
    def test_basic_palette(self) -> None:
        cfg = ScaleTargetConfig(palette=["#fff7d6", "#f59e0b", "#92400e"])
        assert len(cfg.palette) == 3
        assert cfg.domain == "data"
        assert cfg.min is None
        assert cfg.max is None
        assert cfg.null_color is None

    def test_explicit_domain(self) -> None:
        cfg = ScaleTargetConfig(
            palette=["#white", "#black"],
            min=0,
            max=100,
            null_color="#cccccc",
        )
        assert cfg.min == 0
        assert cfg.max == 100
        assert cfg.null_color == "#cccccc"

    def test_palette_required(self) -> None:
        with pytest.raises(ValidationError):
            ScaleTargetConfig()  # type: ignore[call-arg]


class TestColumnScaleConfigModel:
    def test_background_only(self) -> None:
        cfg = ColumnScaleConfig(background=ScaleTargetConfig(palette=["#fff", "#000"]))
        assert cfg.background is not None
        assert cfg.color is None

    def test_both_targets(self) -> None:
        cfg = ColumnScaleConfig(
            background=ScaleTargetConfig(palette=["#fff", "#000"]),
            color=ScaleTargetConfig(palette=["#000", "#fff"]),
        )
        assert cfg.background is not None
        assert cfg.color is not None


class TestTableColumnConfigScale:
    def test_scale_field(self) -> None:
        config = TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(palette=["#fff", "#000"])
            ),
        )
        assert config.scale is not None

    def test_from_dict_with_scale(self) -> None:
        """Parse from YAML-like dict."""
        data = {
            "format": "$,.0f",
            "scale": {
                "background": {
                    "palette": ["#fff7d6", "#f59e0b", "#92400e"],
                    "min": 0,
                    "max": 100000,
                    "null_color": "#f3f4f6",
                }
            },
        }
        config = TableColumnConfig(**data)
        assert config.scale is not None
        assert config.scale.background is not None
        assert config.scale.background.palette == ["#fff7d6", "#f59e0b", "#92400e"]
        assert config.scale.background.min == 0
        assert config.scale.background.max == 100000


# ---------------------------------------------------------------------------
# Color interpolation
# ---------------------------------------------------------------------------


class TestInterpolateScaleColor:
    def test_two_stop_midpoint(self) -> None:
        """Midpoint of two-stop palette gives 50/50 blend."""
        result = interpolate_scale_color(50, 0, 100, ["#000000", "#ffffff"])
        # Should be approximately #808080 (gray)
        assert result.startswith("#")
        r = int(result[1:3], 16)
        assert 126 <= r <= 130  # allow rounding

    def test_at_min_returns_first_stop(self) -> None:
        result = interpolate_scale_color(0, 0, 100, ["#ff0000", "#00ff00"])
        assert result == "#ff0000"

    def test_at_max_returns_last_stop(self) -> None:
        result = interpolate_scale_color(100, 0, 100, ["#ff0000", "#00ff00"])
        assert result == "#00ff00"

    def test_below_min_clamps_to_first(self) -> None:
        result = interpolate_scale_color(-50, 0, 100, ["#ff0000", "#00ff00"])
        assert result == "#ff0000"

    def test_above_max_clamps_to_last(self) -> None:
        result = interpolate_scale_color(200, 0, 100, ["#ff0000", "#00ff00"])
        assert result == "#00ff00"

    def test_three_stop_palette(self) -> None:
        """Value at 50% of a 3-stop palette hits the middle stop."""
        palette = ["#ff0000", "#00ff00", "#0000ff"]
        result = interpolate_scale_color(50, 0, 100, palette)
        assert result == "#00ff00"

    def test_three_stop_first_quarter(self) -> None:
        """Value at 25% blends between first and second stop."""
        palette = ["#000000", "#ff0000", "#ffffff"]
        result = interpolate_scale_color(25, 0, 100, palette)
        # 25% maps to midpoint of first segment (0→ff0000)
        r = int(result[1:3], 16)
        assert 126 <= r <= 130

    def test_equal_min_max_returns_middle_stop(self) -> None:
        result = interpolate_scale_color(5, 5, 5, ["#ff0000", "#00ff00", "#0000ff"])
        assert result == "#00ff00"

    def test_shorthand_hex(self) -> None:
        """3-char hex colors are expanded before interpolation."""
        result = interpolate_scale_color(0, 0, 100, ["#f00", "#0f0"])
        assert result == "#ff0000"


# ---------------------------------------------------------------------------
# Domain computation
# ---------------------------------------------------------------------------


class TestComputeScaleDomain:
    def test_infer_from_data(self) -> None:
        data = [{"amount": 10}, {"amount": 50}, {"amount": 100}]
        cfg = ScaleTargetConfig(palette=["#fff", "#000"])
        lo, hi = compute_scale_domain(data, "amount", cfg)
        assert lo == 10.0
        assert hi == 100.0

    def test_explicit_overrides(self) -> None:
        data = [{"amount": 10}, {"amount": 50}]
        cfg = ScaleTargetConfig(palette=["#fff", "#000"], min=0, max=200)
        lo, hi = compute_scale_domain(data, "amount", cfg)
        assert lo == 0.0
        assert hi == 200.0

    def test_partial_override_min_only(self) -> None:
        data = [{"amount": 10}, {"amount": 50}]
        cfg = ScaleTargetConfig(palette=["#fff", "#000"], min=0)
        lo, hi = compute_scale_domain(data, "amount", cfg)
        assert lo == 0.0
        assert hi == 50.0

    def test_nulls_excluded(self) -> None:
        data = [{"amount": None}, {"amount": 10}, {"amount": 50}]
        cfg = ScaleTargetConfig(palette=["#fff", "#000"])
        lo, hi = compute_scale_domain(data, "amount", cfg)
        assert lo == 10.0
        assert hi == 50.0

    def test_no_numeric_values_fallback(self) -> None:
        data = [{"amount": None}, {"amount": "N/A"}]
        cfg = ScaleTargetConfig(palette=["#fff", "#000"])
        lo, hi = compute_scale_domain(data, "amount", cfg)
        assert lo == 0.0
        assert hi == 1.0

    def test_booleans_excluded(self) -> None:
        data = [{"v": True}, {"v": False}, {"v": 5}, {"v": 10}]
        cfg = ScaleTargetConfig(palette=["#fff", "#000"])
        lo, hi = compute_scale_domain(data, "v", cfg)
        assert lo == 5.0
        assert hi == 10.0


# ---------------------------------------------------------------------------
# Full cell style resolution (precedence: base -> scale -> when)
# ---------------------------------------------------------------------------


class TestResolveCellConditionalStyles:
    def test_base_only(self) -> None:
        from dbt_charts.core.compile.models.primitives import FontStyle

        col = TableColumnConfig(background="#eee", font=FontStyle(color="#111"))
        bg, color, _fw, *_ = resolve_cell_conditional_styles(col, 50, [])
        assert bg == "#eee"
        assert color == "#111"

    def test_scale_overrides_base(self) -> None:
        col = TableColumnConfig(
            background="#eee",
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(
                    palette=["#000000", "#ffffff"], min=0, max=100
                )
            ),
        )
        data = [{"x": 0}, {"x": 100}]
        bg, _color, _fw, *_ = resolve_cell_conditional_styles(col, 50, data)
        # Scale background should override base — approximately #808080
        assert bg is not None
        assert bg != "#eee"
        r = int(bg[1:3], 16)
        assert 126 <= r <= 130

    def test_when_overrides_scale(self) -> None:
        col = TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(
                    palette=["#000000", "#ffffff"], min=0, max=100
                )
            ),
        )
        data = [{"x": 0}, {"x": 100}]
        bg, _color, _fw, *_ = resolve_cell_conditional_styles(
            col,
            5,
            data,
            when_rules=[ConditionalRule(lt=10, background="#ff0000")],
        )
        assert bg == "#ff0000"  # when rule overrides scale

    def test_scale_null_color(self) -> None:
        col = TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(
                    palette=["#000", "#fff"], min=0, max=100, null_color="#cccccc"
                )
            ),
        )
        bg, *_ = resolve_cell_conditional_styles(col, None, [])
        assert bg == "#cccccc"

    def test_scale_null_no_null_color(self) -> None:
        """Null value with no null_color configured -> no scale background."""
        col = TableColumnConfig(
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(palette=["#000", "#fff"], min=0, max=100)
            ),
        )
        bg, *_ = resolve_cell_conditional_styles(col, None, [])
        assert bg is None

    def test_scale_color_target(self) -> None:
        col = TableColumnConfig(
            scale=ColumnScaleConfig(
                color=ScaleTargetConfig(palette=["#000000", "#ffffff"], min=0, max=100)
            ),
        )
        _, color, *_ = resolve_cell_conditional_styles(col, 100, [])
        assert color == "#ffffff"

    def test_full_spec_from_task(self) -> None:
        """Scale + chart-level ``when`` rules interact per precedence."""
        col = TableColumnConfig(
            format="$,.0f",
            scale=ColumnScaleConfig(
                background=ScaleTargetConfig(
                    palette=["#fff7d6", "#f59e0b", "#92400e"],
                    min=0,
                    max=100000,
                    null_color="#f3f4f6",
                )
            ),
        )
        when_rules = [
            ConditionalRule(
                lt=0,
                background="#fee2e2",
                font=FontStyle(color="#991b1b"),
            ),
        ]
        data = [{"amount": 0}, {"amount": 100000}]

        # Positive value: scale applies, when doesn't match
        bg, _color, *_ = resolve_cell_conditional_styles(
            col, 50000, data, when_rules=when_rules
        )
        assert bg is not None
        assert bg != "#fee2e2"  # not the when-rule color

        # Negative value: when rule overrides scale
        bg2, color2, *_ = resolve_cell_conditional_styles(
            col, -100, data, when_rules=when_rules
        )
        assert bg2 == "#fee2e2"
        assert color2 == "#991b1b"

        # Null value: scale null_color
        bg3, *_ = resolve_cell_conditional_styles(
            col, None, data, when_rules=when_rules
        )
        assert bg3 == "#f3f4f6"


# ---------------------------------------------------------------------------
# TableColumnConfig.scale parsing from an authored dict
# ---------------------------------------------------------------------------


class TestParseColumnConfigsWithConditionals:
    def test_scale_from_dict(self) -> None:
        """An authored scale config survives resolution unchanged."""
        from dbt_charts.core.compile.models.chart.normalized import TableChart

        chart = TableChart(
            id="t",
            type="table",
            style={
                "columns": {
                    "amount": {
                        "scale": {
                            "background": {
                                "palette": ["#fff7d6", "#f59e0b", "#92400e"],
                                "min": 0,
                                "max": 100000,
                                "null_color": "#f3f4f6",
                            }
                        },
                    }
                }
            },
        )
        resolved = resolve(chart, [{"amount": 1}], chart_style_context=_BOARD_STYLE)
        assert resolved.columns is not None
        col = resolved.columns["amount"]
        assert col.scale is not None
        assert col.scale.background is not None
        assert col.scale.background.palette == ["#fff7d6", "#f59e0b", "#92400e"]
        assert col.scale.background.min == 0
        assert col.scale.background.max == 100000
        assert col.scale.background.null_color == "#f3f4f6"
        assert col.scale.color is None

    def test_parse_rejects_when_on_column_entry(self) -> None:
        """Authoring ``when:`` under a column entry raises via extra='forbid'.

        With typed columns, rejection happens at parse time (TableColumnConfig
        extra='forbid'), not deferred to parse_table_column_configs.
        """
        from dbt_charts.core.compile.models.style.authored import (
            ChartStylePatch,
            TableChartStylePatch,
        )

        with pytest.raises(ValidationError):
            ChartStylePatch(
                table=TableChartStylePatch.model_validate(
                    {
                        "columns": {
                            "val": {"when": [{"lt": 0, "font": {"color": "#991b1b"}}]},
                        }
                    }
                )
            )


# ---------------------------------------------------------------------------
# SVG rendering integration
# ---------------------------------------------------------------------------


class TestTableSVGWithConditionalFormatting:
    def test_scale_background_in_svg(self, make_chart) -> None:
        """Scale config produces fill rects in the rendered SVG."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            title="Heatmap",
            style={
                "table": {
                    "columns": {
                        "val": {
                            "scale": {
                                "background": {
                                    "palette": ["#ffffff", "#ff0000"],
                                    "min": 0,
                                    "max": 100,
                                }
                            },
                        }
                    }
                }
            },
        )
        data = [{"val": 0}, {"val": 50}, {"val": 100}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert svg.startswith("<svg")
        # The max-value row should have a red-ish fill
        assert "#ff0000" in svg

    def test_when_rule_in_svg(self, make_chart) -> None:
        """When rule from the chart-level block paints the expected background."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            title="Alerts",
            conditional_formatting={
                "status": {
                    "when": [{"eq": "Critical", "background": "#fee2e2"}],
                }
            },
        )
        data = [{"status": "OK"}, {"status": "Critical"}]
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        svg = render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )
        assert "#fee2e2" in svg


# ---------------------------------------------------------------------------
# Z-order, fill-height, and content-span alignment (row-rule z-order task)
# ---------------------------------------------------------------------------


def _resolved_style_with_row_rule(width: float = 1.0) -> Any:
    """Build a ResolvedChartsStyle with row_rule_width set.

    The chart-local style patch does not auto-merge into resolved_style, so
    tests that need row rules active must build the effective style directly.
    """
    import dataclasses

    es = resolve_chart_style_context(get_theme_style())
    tc = es.table
    base_row = tc.row
    new_table = tc.model_copy(
        update={
            "row": base_row.model_copy(
                update={
                    "divider": base_row.divider.model_copy(
                        update={
                            "visible": width > 0,
                            **({"width": width} if width > 0 else {}),
                        }
                    )
                }
            ),
            # Zero out header rule to exclude it from rect-order assertions.
            "header": tc.header.model_copy(
                update={
                    "divider": tc.header.divider.model_copy(
                        update={"visible": False, "width": 1}
                    )
                }
            ),
        }
    )
    return dataclasses.replace(es, table=new_table)


def _apply_charts_table_style(resolved: Any, charts_style: Any) -> Any:
    """Return a copy of resolved table chart with style.table from charts_style.table."""
    from dbt_charts.core.compile.models.style.resolved.table import ResolvedTableStyle

    return resolved.model_copy(
        update={
            "style": ResolvedTableStyle(
                table=charts_style.table,
                title=resolved.style.title,
                formats=resolved.style.formats,
                title_font=resolved.style.title_font,
                pagination=resolved.style.pagination,
            )
        }
    )


def _scale_chart(make_chart: Any) -> Any:
    """Single-column income table with a sequential blue heatmap fill."""
    return make_chart(
        "table",
        title="Income Heatmap",
        style={
            "table": {
                "columns": {
                    "income": {
                        "scale": {
                            "background": {
                                "palette": ["#ffffff", "#2563eb"],
                                "min": 40000,
                                "max": 80000,
                            }
                        },
                    }
                }
            },
        },
    )


class TestRowRuleZOrderAndFillAlignment:
    """Row rules must paint above fills; fills must reserve rule band; fills must inset."""

    def test_row_rules_emitted_after_scale_fills(self, make_chart: Any) -> None:
        """Row rule rects appear AFTER scale-fill rects in SVG document order.

        SVG paints in document order; later elements appear on top. Rules must
        come after fills so rules are never occluded by fills.
        """
        import re

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        es = _resolved_style_with_row_rule(width=1.0)
        data = [
            {"income": 40000},
            {"income": 55000},
            {"income": 70000},
            {"income": 80000},
            {"income": 60000},
        ]
        chart = _apply_charts_table_style(
            resolve(
                _scale_chart(make_chart),
                [],
                chart_style_context=_BOARD_STYLE,
            ),
            es,
        )
        svg = render_table_svg(
            chart,
            data,
            width=500,
            board_style=resolve_style(get_theme_style()),
        )

        # Extract all <rect ...> tags in document order
        rect_tags = re.findall(r"<rect\s[^>]*/?>", svg)

        # Scale-fill rects: contain the palette color (#2563eb at max or a blend).
        fill_positions = [i for i, tag in enumerate(rect_tags) if "#2563eb" in tag]
        # Row-rule rects: integer height (e.g. height="1") with crispEdges.
        # Integer height distinguishes row rules from body fills (height="28")
        # and header rules emitted with float heights like height="1.0".
        rule_positions = [
            i
            for i, tag in enumerate(rect_tags)
            if "crispEdges" in tag and "#2563eb" not in tag and "#ffffff" not in tag
        ]

        assert fill_positions, "expected at least one scale-fill rect"
        assert rule_positions, "expected at least one row-rule rect"

        # Every rule rect must appear after every fill rect it separates.
        # Sufficient check: the last fill position < the last rule position.
        # Rules are deferred and flushed after the full row loop, so they follow all fills.
        assert max(fill_positions) < max(rule_positions), (
            "Row rules must appear after scale fills in SVG document order, "
            "but a rule rect was found before the last fill rect. "
            f"fill_positions={fill_positions}, rule_positions={rule_positions}"
        )

    def test_scale_fill_height_reserves_rule_band(self, make_chart: Any) -> None:
        """Scale-fill rect height is per_row_height - rule_reserve, not per_row_height.

        With a 1px row rule, each fill should be row_height-1 so the rule
        band at the bottom of each row stays clear and the fill can't bleed
        past the grid line.
        """
        import re

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        es = _resolved_style_with_row_rule(width=1.0)
        row_height = int(es.table.row.height)
        data = [
            {"income": 50000},
            {"income": 60000},
            {"income": 70000},
            {"income": 80000},
            {"income": 55000},
        ]
        chart = _apply_charts_table_style(
            resolve(
                _scale_chart(make_chart),
                [],
                chart_style_context=_BOARD_STYLE,
            ),
            es,
        )
        svg = render_table_svg(
            chart,
            data,
            width=500,
            board_style=resolve_style(get_theme_style()),
        )

        rect_tags = re.findall(r"<rect\s[^>]*/?>", svg)
        scale_fill_rects = [tag for tag in rect_tags if "#2563eb" in tag]
        assert scale_fill_rects, "expected scale-fill rects with max palette color"
        for tag in scale_fill_rects:
            m = re.search(r'height="([^"]+)"', tag)
            assert m, f"scale fill rect has no height: {tag}"
            h = float(m.group(1))
            # With 1px rule reserve, fill height must be row_height - 1.
            assert h < row_height, (
                f"Scale fill height {h} must be less than row_height {row_height} "
                f"to reserve space for the row rule. rect: {tag}"
            )

    def test_scale_fill_inset_matches_content_span(self, make_chart: Any) -> None:
        """Scale-fill x and x+width must equal stripe x and x+width exactly.

        Stripes use _compute_content_span which is the canonical content-span
        contract. The scale fill uses fill_x = cell_x + cell_pad. For a
        single-column table they resolve to the same value. A byte-for-byte
        match proves the fill is inset to exactly cell_pad, not just "narrower
        than the SVG width."
        """
        import re

        # Force a stripe so we get a reference rect from _compute_content_span.
        from dbt_charts.core.compile.models.style.theme import TableRowStripeStyle
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        base_row = tc.row
        new_table = tc.model_copy(
            update={
                "row": base_row.model_copy(
                    update={
                        "divider": base_row.divider.model_copy(
                            update={"visible": True, "width": 1.0}
                        ),
                        "stripe": TableRowStripeStyle(color="#f0f0f0"),
                    }
                ),
                "header": tc.header.model_copy(
                    update={
                        "divider": tc.header.divider.model_copy(
                            update={"visible": False, "width": 1}
                        )
                    }
                ),
            }
        )
        es = dataclasses.replace(es, table=new_table)
        # Two rows: row 0 = value row (no stripe), row 1 = max value → scale fill + stripe.
        data = [{"income": 40000}, {"income": 80000}]
        chart = _apply_charts_table_style(
            resolve(
                _scale_chart(make_chart),
                [],
                chart_style_context=_BOARD_STYLE,
            ),
            es,
        )
        svg = render_table_svg(
            chart,
            data,
            width=800,
            board_style=resolve_style(get_theme_style()),
        )

        rect_tags = re.findall(r"<rect\s[^>]*/?>", svg)

        # Stripe rects: emitted for odd rows (row_idx=1 here) with the stripe color.
        stripe_rects = [t for t in rect_tags if "#f0f0f0" in t]
        # Scale-fill rects: max value → full blue.
        fill_rects = [t for t in rect_tags if "#2563eb" in t]

        assert stripe_rects, "expected a stripe rect on the odd row"
        assert fill_rects, "expected a scale-fill rect at max palette color"

        def _x(tag: str) -> float:
            m = re.search(r'x="([^"]+)"', tag)
            assert m, f"no x in rect: {tag}"
            return float(m.group(1))

        def _right(tag: str) -> float:
            xm = re.search(r'x="([^"]+)"', tag)
            wm = re.search(r'width="([^"]+)"', tag)
            assert xm and wm, f"no x/width in rect: {tag}"
            return float(xm.group(1)) + float(wm.group(1))

        # The scale fill is on the same row as the stripe; their left and right
        # edges must be identical — both are derived from cell_pad inset.
        stripe_x = _x(stripe_rects[0])
        stripe_right = _right(stripe_rects[0])
        fill_x = _x(fill_rects[0])
        fill_right = _right(fill_rects[0])

        assert fill_x == stripe_x, (
            f"Scale fill x={fill_x} != stripe x={stripe_x}. "
            "Fill must be inset to exactly cell_pad, matching the content span."
        )
        assert fill_right == stripe_right, (
            f"Scale fill right={fill_right} != stripe right={stripe_right}. "
            "Fill right edge must match the content span right edge."
        )

    def test_static_column_background_not_inset(self, make_chart: Any) -> None:
        """Static col_config.background stays full-width (x=cell_x, width=cw).

        The inset contract applies only to scale/when conditional fills — they
        represent "this value's colored region" and should align with the
        content span. A plain column.background is a column-wide color band
        and must remain edge-to-edge so it reaches the cell boundary.

        We verify by checking x == cell_x (no cell_pad added) and
        x + width == cell_x + cw (no cell_pad subtracted).
        """
        import re

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        # x=None, y=None: only the explicitly listed column exists.
        # This avoids auto-added x_field/y_field columns shifting col offsets.
        chart = make_chart(
            "table",
            x=None,
            y=None,
            title="Static BG",
            style={
                "table": {
                    "columns": {
                        "val": {"background": "#abcdef"},
                    }
                }
            },
        )
        es = _resolved_style_with_row_rule(width=1.0)
        data = [{"val": 42}]
        chart = _apply_charts_table_style(
            resolve(chart, [], chart_style_context=_BOARD_STYLE),
            es,
        )
        svg = render_table_svg(
            chart,
            data,
            width=800,
            board_style=resolve_style(get_theme_style()),
        )

        static_bg_rects = re.findall(r"<rect[^>]*#abcdef[^>]*/>", svg)
        assert static_bg_rects, "expected a static background rect with #abcdef"

        cell_pad = int(es.table.column_layout.cell_padding)
        # tc.padding = 0 (TableChartStyle default), so padding_x = 0.
        # Single-column table: cell_x = padding_x + col_x_offsets[0] = 0 + 0 = 0.
        # Full-width rect must have x = cell_x = 0, not cell_x + cell_pad.
        expected_x = 0.0
        for tag in static_bg_rects:
            x_m = re.search(r'x="([^"]+)"', tag)
            assert x_m, f"no x in rect: {tag}"
            x = float(x_m.group(1))
            assert x == expected_x, (
                f"Static column background x={x} should equal cell_x={expected_x} "
                f"(full-width, NOT inset by cell_pad={cell_pad}). rect: {tag}"
            )

    def test_m6_scale_with_total_rule_ordering(self, make_chart: Any) -> None:
        """Value→summary→total transition: fills, rule ordering, and heights.

        Checks three contracts for the m6 (scale-filled body + summary rule) case:
        1. No row rule is drawn below the last data row (the summary rule takes over).
        2. The summary rule rect appears after the fill rects in SVG document order.
        3. The last data row's fill has full per_row_height (no rule reserve), because
           _will_draw_row_rule=False when the next row is summary.
        """
        import re

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        # Chart with scale fill on income, row_role column drives summary/total.
        chart = make_chart(
            "table",
            title="M6 Scale With Total",
            style={
                "table": {
                    "columns": {
                        "income": {
                            "scale": {
                                "background": {
                                    "palette": ["#ffffff", "#2563eb"],
                                    "min": 0,
                                    "max": 100000,
                                }
                            },
                        }
                    },
                    "row": {"role": "row_role"},
                }
            },
        )

        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        base_row = tc.row
        new_table = tc.model_copy(
            update={
                "row": base_row.model_copy(
                    update={
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
                ),
                "header": tc.header.model_copy(
                    update={
                        "divider": tc.header.divider.model_copy(
                            update={"visible": False, "width": 1}
                        )
                    }
                ),
            }
        )
        es = dataclasses.replace(es, table=new_table)

        row_height = int(es.table.row.height)

        data = [
            {"income": 40000, "row_role": "value"},
            {"income": 60000, "row_role": "value"},
            {"income": 80000, "row_role": "value"},  # last data row — no row rule below
            {"income": 100000, "row_role": "summary"},
            {"income": 100000, "row_role": "total"},
        ]
        chart = _apply_charts_table_style(
            resolve(chart, [], chart_style_context=_BOARD_STYLE),
            es,
        )
        svg = render_table_svg(
            chart,
            data,
            width=800,
            board_style=resolve_style(get_theme_style()),
        )

        rect_tags = re.findall(r"<rect\s[^>]*/?>", svg)

        # Scale-fill rects from the three value rows.
        fill_rects = [t for t in rect_tags if "#2563eb" in t]
        # Row-rule rects: crispEdges, integer height, not a fill color.
        rule_rects = [
            t
            for t in rect_tags
            if "crispEdges" in t and "#2563eb" not in t and "#ffffff" not in t
        ]
        # Summary-rule rects appear before total rows — same crispEdges marker.
        # There must be at least one (the summary rule above the summary row).
        assert rule_rects, "expected at least one rule rect (summary rule)"

        # Contract 1: row rules only between value rows — 2 rules for 3 value rows
        # (rules after rows 0 and 1; row 2's next row is summary → no row rule).
        # The summary rule above the summary row is separate, so total rules = 3
        # (2 row rules + 1 summary rule) assuming summary_rule_width > 0.
        # We can't easily distinguish row rules from summary rules by geometry alone,
        # but we CAN assert no extra rule lands at the bottom of the last value row.
        # The last fill rect's y + height must not be followed immediately by a crispEdges rule
        # at that exact y. Extract the last fill's bottom y.
        fill_positions = [i for i, t in enumerate(rect_tags) if "#2563eb" in t]
        rule_positions = [i for i, t in enumerate(rect_tags) if t in rule_rects]

        assert fill_positions, "expected scale-fill rects"
        # Contract 2: every rule rect appears after the last fill rect.
        assert max(fill_positions) < max(rule_positions), (
            "Summary rule must appear after all fill rects in SVG document order. "
            f"fill_positions={fill_positions}, rule_positions={rule_positions}"
        )

        # Contract 3: the last value row (row_idx=2, next=summary) has full row_height fill.
        # Since _will_draw_row_rule=False for that row, fill_height = per_row_height.
        # The last fill rect among the data rows should have height == row_height (no reserve).
        # Data-row fills are the ones with #2563eb. Find the one at the largest y.
        def _y(tag: str) -> float:
            m = re.search(r'\by="([^"]+)"', tag)
            assert m, f"no y in rect: {tag}"
            return float(m.group(1))

        def _h(tag: str) -> float:
            m = re.search(r'height="([^"]+)"', tag)
            assert m, f"no height in rect: {tag}"
            return float(m.group(1))

        last_data_fill = max(fill_rects, key=_y)
        h = _h(last_data_fill)
        assert h == row_height, (
            f"Last data row fill height={h} must equal row_height={row_height} "
            "(no rule reserve when next row is summary). rect: {last_data_fill}"
        )


# ---------------------------------------------------------------------------
# Fix 1 regression: named string palette must not be char-iterated
# ---------------------------------------------------------------------------


class TestNamedPaletteCallerWiring:
    """Verify resolve_cell_conditional_styles routes named palette strings through
    resolve_palette_stops, not a raw char-iteration over the string."""

    def test_named_palette_produces_valid_hex_in_resolve_cell_styles(self) -> None:
        # A named palette string carries valid hex stops via
        # ScaleTargetConfig.resolved_stops, baked at resolve time by
        # compile/resolve/chart/_table.py's _with_resolved_scale_stops — this test
        # fabricates that same baked value directly, since it exercises
        # resolve_cell_conditional_styles below the resolver.
        from dbt_charts.core.compile.models.primitives import (
            ResolvedNamedPaletteScaleTargetConfig,
        )
        from dbt_charts.core.compile.models.style.resolved import (
            ResolvedColumnScaleConfig,
            ResolvedTableColumnConfig,
        )
        from dbt_charts.core.compile.resolve.style.palette import (
            palette as resolve_named_palette,
        )

        col = ResolvedTableColumnConfig(
            scale=ResolvedColumnScaleConfig(
                background=ResolvedNamedPaletteScaleTargetConfig(
                    palette="dbt-seq-blue",
                    resolved_stops=tuple(resolve_named_palette("dbt-seq-blue")),
                    min=0,
                    max=100,
                )
            ),
        )
        data = [{"val": 0}, {"val": 100}]
        bg_at_min, *_ = resolve_cell_conditional_styles(col, 0, data)
        bg_at_max, *_ = resolve_cell_conditional_styles(col, 100, data)

        # Both must be valid 7-char hex colors, not single-letter garbage.
        assert bg_at_min is not None
        assert bg_at_max is not None
        assert bg_at_min.startswith("#") and len(bg_at_min) == 7, (
            f"Expected 7-char hex, got {bg_at_min!r} — likely char-iteration bug"
        )
        assert bg_at_max.startswith("#") and len(bg_at_max) == 7, (
            f"Expected 7-char hex, got {bg_at_max!r} — likely char-iteration bug"
        )
        # The two extremes should produce different colors (palette varies).
        assert bg_at_min != bg_at_max


class TestWcagTableInkCarve:
    """The table WCAG surface="table" carve must key off the *effective* cell
    ink — table.color.static when authored, else table.font.color — not
    unconditionally table.font.color. Cell body text sits on the scale fill,
    so getting this wrong produces contrast-unsafe fills (e.g. near-white
    text on a near-white background) that look fine to every non-visual test.
    """

    def test_static_color_override_changes_baked_stops(self, make_chart) -> None:
        chart_with_static = make_chart(
            "table",
            style={
                "table": {
                    "columns": {
                        "v": {"scale": {"background": {"palette": "dbt-seq-blue"}}}
                    },
                    "color": {"static": "#ffffff"},
                }
            },
        )
        chart_default = make_chart(
            "table",
            style={
                "table": {
                    "columns": {
                        "v": {"scale": {"background": {"palette": "dbt-seq-blue"}}}
                    },
                }
            },
        )
        data = [{"v": 0}, {"v": 100}]
        resolved_with_static = resolve(
            chart_with_static, data, chart_style_context=_BOARD_STYLE
        )
        resolved_default = resolve(
            chart_default, data, chart_style_context=_BOARD_STYLE
        )

        static_stops = resolved_with_static.columns["v"].scale.background.resolved_stops
        default_stops = resolved_default.columns["v"].scale.background.resolved_stops
        assert static_stops is not None
        assert default_stops is not None
        assert static_stops != default_stops

    def test_invalid_static_color_falls_back_to_font_color(self, make_chart) -> None:
        """An invalid/empty style.color.static must fall back to
        table.font.color rather than reach the WCAG carve as an unparseable
        string. Regression: passing it through raw (no sanitize_color gate)
        crashed on `_relative_luminance("")` for `color.static: ""`."""
        chart = make_chart(
            "table",
            style={
                "table": {
                    "columns": {
                        "v": {"scale": {"background": {"palette": "dbt-seq-blue"}}}
                    },
                    "color": {"static": ""},
                }
            },
        )
        data = [{"v": 0}, {"v": 100}]
        resolved = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        assert resolved.columns["v"].scale.background.resolved_stops is not None


# ---------------------------------------------------------------------------
# Pivot column totals — bottom total row reuses row-role total styling
# ---------------------------------------------------------------------------


class TestPivotColumnTotalRowStyling:
    """A `_df_row_role='total'` tidy row under a pivot renders with the SAME
    double-rule total styling as a flat table's total row. pivot_table_data
    reshapes it into the bottom grid row and carries the role field through;
    render applies zero pivot-specific styling code — this is the whole point
    of Solution A (render reshapes, it does not aggregate or restyle)."""

    def test_pivot_total_row_gets_double_rule_treatment(self, make_chart: Any) -> None:
        import re

        from dbt_charts.core.render.chart.table import render_table_svg

        chart = make_chart(
            "table",
            title="Pivot With Totals",
            rows=["region"],
            columns=["quarter"],
            values=["revenue"],
        )

        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        base_row = tc.row
        # Distinctive width on the total role's divider: it sets the width of
        # the double rule above a total row.
        new_table = tc.model_copy(
            update={
                "row": base_row.model_copy(
                    update={
                        "role": "_df_row_role",
                        "roles": base_row.roles.model_copy(
                            update={
                                "total": base_row.roles.total.model_copy(
                                    update={
                                        "divider": base_row.roles.total.divider.model_copy(
                                            update={"visible": True, "width": 3.0}
                                        )
                                    }
                                ),
                            }
                        ),
                    }
                ),
            }
        )
        es = dataclasses.replace(es, table=new_table)

        # The grand total's two tidy rows are scattered (not contiguous at the
        # end) but share one rows-dim label ("Total"), so they bucket together
        # and move to the bottom as ONE row — a single double-rule pair, not one
        # per scattered source row.
        data = [
            {"region": "US", "quarter": "Q1", "revenue": 100, "_df_row_role": "value"},
            {
                "region": "Total",
                "quarter": "Q1",
                "revenue": 190,
                "_df_row_role": "total",
            },
            {"region": "US", "quarter": "Q2", "revenue": 200, "_df_row_role": "value"},
            {"region": "EU", "quarter": "Q1", "revenue": 90, "_df_row_role": "value"},
            {"region": "EU", "quarter": "Q2", "revenue": 180, "_df_row_role": "value"},
            {
                "region": "Total",
                "quarter": "Q2",
                "revenue": 380,
                "_df_row_role": "total",
            },
        ]
        chart = _apply_charts_table_style(
            resolve(chart, [], chart_style_context=_BOARD_STYLE),
            es,
        )
        svg = render_table_svg(
            chart,
            data,
            width=800,
            board_style=resolve_style(get_theme_style()),
        )

        # Assert on the merged row's cell values (190 = Q1 total, 380 = Q2
        # total), NOT the "Total" rows-dim label — the chart title ("Pivot With
        # Totals") contains "Total", so an `"Total" in svg` check is vacuous.
        assert "190" in svg and "380" in svg, "merged total row's cells must render"
        # The role marker drives placement/styling only — it must never leak
        # into the rendered grid as its own header/data column (title-cased
        # "Df Row Role" header + literal "value"/"total" cells).
        assert "Row Role" not in svg, "role marker column must not render"

        # The distinctive rule_width=3.0 must produce the double-rule pair
        # (two crispEdges rects of height=3) reserved for is_total_role rows —
        # proves the role field survived pivot_table_data's reshape and
        # _render_data_rows resolved the bottom row as "total".
        double_rule_rects = re.findall(
            r'<rect[^>]*height="3"[^>]*crispEdges[^>]*/>', svg
        )
        assert len(double_rule_rects) == 2, (
            "expected 2 double-rule rects (height=3) above the pivot's bottom "
            f"total row, got {len(double_rule_rects)}: {double_rule_rects}"
        )

    def test_all_totals_role_column_never_renders(self, make_chart: Any) -> None:
        """The role-strip guard (table.py's `columns = [c for c in columns if
        c != row_role_spec]`) must keep the role column out of the rendered
        header. Every wide row carries the marker, so this all-totals result —
        which collapses to a single merged wide row — is the tightest case: the
        role column is present on the only row there is, and the guard is what
        keeps it from rendering as a "Row Role" header."""
        from dbt_charts.core.render.chart.table import render_table_svg

        chart = make_chart(
            "table",
            title="All Totals",
            rows=["region"],
            columns=["quarter"],
            values=["revenue"],
        )

        es = resolve_chart_style_context(get_theme_style())
        new_table = es.table.model_copy(
            update={"row": es.table.row.model_copy(update={"role": "_df_row_role"})}
        )
        es = dataclasses.replace(es, table=new_table)

        data = [
            {
                "region": "Total",
                "quarter": "Q1",
                "revenue": 190,
                "_df_row_role": "total",
            },
            {
                "region": "Total",
                "quarter": "Q2",
                "revenue": 380,
                "_df_row_role": "total",
            },
        ]
        chart = _apply_charts_table_style(
            resolve(chart, [], chart_style_context=_BOARD_STYLE),
            es,
        )
        svg = render_table_svg(
            chart,
            data,
            width=800,
            board_style=resolve_style(get_theme_style()),
        )

        assert "190" in svg and "380" in svg, "the merged total row's cells must render"
        # The whole point of the test: the role marker never renders as its own
        # ("Df Row Role") header column, even in the all-totals result where the
        # marker sits on the only wide row there is.
        assert "Row Role" not in svg, "role marker column must not render"

    def test_flat_table_role_column_never_renders(self, make_chart: Any) -> None:
        """The role-strip is NOT pivot-only: `row.role` cascades from the
        theme/board, so a flat table (no `columns:`) with a board-wide
        `row.role: _df_row_role` must also drop the role column from its derived
        headers, not render it as a "Row Role" column — pivot and flat must not
        diverge here."""
        from dbt_charts.core.render.chart.table import render_table_svg

        chart = make_chart("table", title="Flat With Roles")

        es = resolve_chart_style_context(get_theme_style())
        new_table = es.table.model_copy(
            update={"row": es.table.row.model_copy(update={"role": "_df_row_role"})}
        )
        es = dataclasses.replace(es, table=new_table)

        data = [
            {"region": "US", "revenue": 100, "_df_row_role": "value"},
            {"region": "EU", "revenue": 90, "_df_row_role": "value"},
            {"region": "Total", "revenue": 190, "_df_row_role": "total"},
        ]
        chart = _apply_charts_table_style(
            resolve(chart, [], chart_style_context=_BOARD_STYLE),
            es,
        )
        svg = render_table_svg(
            chart,
            data,
            width=800,
            board_style=resolve_style(get_theme_style()),
        )

        assert "100" in svg and "190" in svg, "data cells must still render"
        assert "Row Role" not in svg, "role marker column must not render"
