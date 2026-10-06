"""Regression tests for the universal default table scaffold.

The design rule: a bare table (no style overrides, default theme) renders
with header rule + row stripe on, header fill off. Editorial (the shipped
default theme) sets the stripe via a single scaffold-token
(`charts.table.row.stripe.color: dbt-grays.surface-subtle` in
editorial.yaml) — one step off the canvas, faint enough to read as
row-tracking texture rather than value encoding. Summary and total rules
still fire via row typing when `row.role` is configured.

Background context: cell backgrounds are reserved for value encoding
(heatmap fills). Header fills compete with that signal, so header fill
stays off by default.
"""

from __future__ import annotations

import re

from dbt_charts.core.compile.config import (
    get_config,
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def _rects(svg: str) -> list[tuple[float, float, float, str]]:
    """Return (x, y, height, fill) for every rect in the SVG."""
    return [
        (
            float(m.group(1)),
            float(m.group(2)),
            float(m.group(3)),
            m.group(4).lower(),
        )
        for m in re.finditer(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="[\d.]+" height="([\d.]+)" '
            r'fill="(#[0-9a-fA-F]{6,8})"',
            svg,
        )
    ]


class TestDefaultTableScaffold:
    """Bare table with no style overrides: header rule and row stripe render, header fill does not."""

    def _render_bare(self, make_chart, data=None, role=None):
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart(
            "table",
            style={"columns": {"name": {}, "value": {}}},
        )
        data = data or [
            {"name": "A", "value": 10},
            {"name": "B", "value": 20},
            {"name": "C", "value": 30},
            {"name": "D", "value": 40},
        ]

        if role is not None:
            from dbt_charts.core.compile.models.style.resolved.table import (
                ResolvedTableStyle,
            )

            charts = resolve_chart_style_context(get_theme_style())
            new_row = charts.table.row.model_copy(update={"role": role})
            new_table = charts.table.model_copy(update={"row": new_row})
            resolved_chart = resolve(chart, [], chart_style_context=_BOARD_STYLE)
            chart = resolved_chart.model_copy(
                update={
                    "style": ResolvedTableStyle(
                        table=new_table,
                        title=resolved_chart.style.title,
                        formats=resolved_chart.style.formats,
                        title_font=resolved_chart.style.title_font,
                        pagination=resolved_chart.style.pagination,
                    )
                }
            )
            return render_table_svg(
                chart,
                data,
                width=400,
                board_style=resolve_style(get_theme_style()),
            )
        chart = resolve(chart, data, chart_style_context=_BOARD_STYLE)
        return render_table_svg(
            chart,
            data,
            width=400,
            board_style=resolve_style(get_theme_style()),
        )

    def test_no_header_background_fill(self, make_chart):
        """Header region has no colored fill rect — only the page/canvas white."""
        svg = self._render_bare(make_chart)
        rects = _rects(svg)
        # Header sits at y=0 with header_height (36). A full-width rect at y=0
        # with a fill other than white/transparent would be the header bg.
        # Non-scaffold fills at y≈0, h≈36 indicate a header background leak.
        header_bg_like = [
            (x, y, h, fill)
            for x, y, h, fill in rects
            if y < 5 and 30 < h < 50 and fill not in {"#ffffff", "#fafafa"}
        ]
        assert not header_bg_like, (
            f"Default table must have no header background fill; found {header_bg_like}"
        )

    def test_stripe_fills_present(self, make_chart):
        """Body renders alternating-row stripe rects — editorial's default."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        svg = self._render_bare(make_chart)
        rects = _rects(svg)
        # Stripes render as row-height rects with a fill that isn't pure
        # white. Read the row height from config rather than pinning a
        # literal — it has drifted before (32px -> 24px) and will again.
        row_h = get_theme_style().charts.table.row.height
        stripe_like = [
            (x, y, h, fill)
            for x, y, h, fill in rects
            if abs(h - row_h) < 1 and fill not in {"#ffffff"}
        ]
        assert stripe_like, (
            f"Default table must render row stripe fills at height {row_h}; "
            f"found none among {rects}"
        )

    def test_header_rule_renders(self, make_chart):
        """Header rule (1px line under header) renders by default."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        svg = self._render_bare(make_chart)
        rects = _rects(svg)
        # Header rule is a thin rect (height ~ 1px) positioned at the bottom
        # of the header row. Read header height from config rather than pinning
        # a literal — densification tweaks header.height frequently.
        header_h = get_theme_style().charts.table.header.height
        rule_like = [
            (x, y, h, fill)
            for x, y, h, fill in rects
            if (header_h - 6) < y < (header_h + 6) and h <= 1.5
        ]
        assert rule_like, (
            f"Default table must render header rule; "
            f"no thin horizontal rect found near y={header_h}. Rects: {rects[:10]}"
        )

    def test_summary_row_rule_renders(self, make_chart):
        """Summary-role rows still get their rule — row typing is unaffected."""
        svg = self._render_bare(
            make_chart,
            data=[
                {"name": "A", "value": 10, "row_role": "value"},
                {"name": "B", "value": 20, "row_role": "value"},
                {"name": "Total", "value": 30, "row_role": "summary"},
            ],
            role="row_role",
        )
        # At least one thin rule above the summary row should render.
        # Summary rule_width default is 1.0 in TableRowRoleStyle.
        rects = _rects(svg)
        thin_rules = [(x, y, h, fill) for x, y, h, fill in rects if h <= 1.5 and y > 40]
        assert thin_rules, (
            f"Summary row must render its rule; no thin rect found below "
            f"header. Rects: {rects[:15]}"
        )


class TestPydanticDefaults:
    """Theme-pipeline values verify the 'header rule only' universal default."""

    def test_header_background_default_is_none(self):

        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        assert get_theme_style().charts.table.header.background is None

    def test_stripe_default_is_on(self):

        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.palette import (
            color as resolve_palette_color,
        )

        # Editorial (the shipped default theme) opts into a subtle stripe —
        # one scaffold step off canvas. See row.stripe.color in editorial.yaml.
        stripe = get_theme_style().charts.table.row.stripe
        assert stripe is not None
        assert stripe.color == resolve_palette_color("dbt-grays.surface-subtle")

    def test_header_rule_default_is_on(self):
        """Header rule is on — verify via theme pipeline."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        header = get_theme_style().charts.table.header
        assert header.divider.visible
        assert header.divider.continuous is True

    def test_summary_rule_is_on(self):
        """Summary row rule is on — verify via theme pipeline."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        roles = get_theme_style().charts.table.row.roles
        assert isinstance(roles.summary.divider.width, float)
        assert roles.summary.divider.visible


class TestApparatusRefactor:
    """Apparatus sizing (small semibold header) must not leak into the universal default.

    Invariants:
    - get_theme_style().model_copy(deep=True) defaults produce table body=14 (inherits root font.size),
      header inherits from body (same size, same weight).
    - stark.yaml no longer hard-codes header font size/weight/color.
    """

    def test_resolved_table_body_inherits_root_font_size(self):
        """After resolve_style, table body inherits root style.font.size (no
        hardcoded 13px)."""

        get_config()  # ensure settings initialized
        ctx = resolve_chart_style_context(get_theme_style())
        assert ctx.table.font.size == get_theme_style().font.size, (
            f"Expected table body size {get_theme_style().font.size}, got "
            f"{ctx.table.font.size}. Table body must inherit "
            "root font.size after apparatus refactor."
        )

    def test_resolved_header_inherits_body_size(self):
        """After resolve_style, header.font.size == table.font.size (both cascade from root)."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        ctx = resolve_chart_style_context(get_theme_style())
        assert ctx.table.header.font.size == ctx.table.font.size, (
            f"Header size {ctx.table.header.font.size} must equal "
            f"body size {ctx.table.font.size} in universal default."
        )

    def test_resolved_header_is_heavier_than_body(self):
        """After resolve_style, header.font.weight is bolder than table body weight.

        Headers carry more weight than body cells by design — the cascade
        sets `style.charts.table.header.font.weight` explicitly in
        stark.yaml so themes can tune it without relying on the
        render-side fallback. Test the *relationship* (heavier than body),
        not a specific weight value, per the don't-pin-theme-values rule.
        """
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        ctx = resolve_chart_style_context(get_theme_style())
        header = int(ctx.table.header.font.weight)
        body = int(ctx.table.font.weight)
        assert header > body, (
            f"Header weight {header} must be heavier than body weight {body} "
            "in the universal default (headers carry more visual weight than cells)."
        )

    def test_dbt_charts_default_theme_has_no_apparatus_header_size(self):
        """stark theme no longer hard-codes apparatus header.font.size."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        theme = get_theme_style()
        # Either the YAML no longer sets it (None) or it equals the body/root default (14).
        # Either way, it must NOT be 11 (apparatus sizing).
        assert theme.charts.table.header.font.size != 11, (
            "stark must not hard-code apparatus header.font.size=11 "
            "(apparatus sizing is not part of the universal default)."
        )
