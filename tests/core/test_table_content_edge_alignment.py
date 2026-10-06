"""Geometry pins for table chrome rectangles.

Two surface classes:
- **Edge-to-edge fills** (header bg, row stripe, per-role row bg,
  scale/when cell fills): paint to the full table or cell extent —
  no cell_pad inset. Tests in TestEdgeToEdgeFills.
- **Inset rules** (continuous header rule, summary rule, row rule):
  paint at the cell-content span (cell_x + cell_pad) — line accents
  inside the chrome surface. Tests in TestContentEdgeAlignment.

The two classes intentionally disagree by exactly cell_pad on each
side — fills extend past the rule's right edge by cell_pad. Prior
to PR #1650 fills and rules disagreed in unprincipled ways across
multiple lane-based extents; PR #1650 unified them at the content
span; this round adopts edge-to-edge for fills only and leaves rules
on the content span.
"""

from __future__ import annotations

import dataclasses
import re

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)

_RS, _CTX = resolve_style_and_context(get_theme_style())

# ---------------------------------------------------------------------------
# SVG extraction helpers
# ---------------------------------------------------------------------------


def _rects_with_width(svg: str) -> list[tuple[float, float, float, float, str]]:
    """Return (x, y, width, height, fill) for every rect in the SVG."""
    return [
        (
            float(m.group(1)),
            float(m.group(2)),
            float(m.group(3)),
            float(m.group(4)),
            m.group(5).lower(),
        )
        for m in re.finditer(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"'
            r'(?:[^/]*?)fill="(#[0-9a-fA-F]{6,8}|[a-zA-Z]+)"',
            svg,
        )
    ]


def _render_with_summary(make_chart, width: int = 600) -> str:
    """Render a table with a summary row, header fill, stripes, and summary rule."""

    from dbt_charts.core.compile.models.style.theme import TableRowStripeStyle
    from dbt_charts.core.render.chart.table import render_table_svg as render_table_svg

    es = resolve_chart_style_context(get_theme_style())
    tc = es.table
    new_table = tc.model_copy(
        update={
            "header": tc.header.model_copy(update={"background": "#e0e0e0"}),
            "row": tc.row.model_copy(
                update={
                    "stripe": TableRowStripeStyle(color="#f0f0f0"),
                    "role": "kind",
                }
            ),
        }
    )
    custom_ctx = dataclasses.replace(es, table=new_table)
    custom_rs = dataclasses.replace(
        _RS,
        chart_defaults=dataclasses.replace(_RS.chart_defaults, table=new_table),
    )

    chart = make_chart(
        "table",
        style={"table": {"columns": {"label": {}, "amount": {}}}},
    )
    data = [
        {"label": "Alpha", "amount": 100, "kind": "data"},
        {"label": "Beta", "amount": 200, "kind": "data"},
        {"label": "Total", "amount": 300, "kind": "summary"},
    ]
    chart = resolve(chart, [], chart_style_context=custom_ctx)
    return render_table_svg(
        chart,
        data,
        width=width,
        board_style=custom_rs,
    )


# ---------------------------------------------------------------------------
# The tolerance used throughout — 0.5px covers sub-pixel rounding without
# allowing meaningful misalignment.
# ---------------------------------------------------------------------------
_TOL = 0.5


class TestContentEdgeAlignment:
    """Header bg, header rule, stripes, and summary rule share one content edge."""

    def test_bg_rule_stripe_right_edges_agree(self, make_chart):
        """Header bg, row stripes, and summary rule right edges all agree."""
        svg = _render_with_summary(make_chart, width=600)
        rects = _rects_with_width(svg)

        # Header bg rect: tall rect near y=0
        header_bg = [r for r in rects if r[1] < 5 and r[3] > 20 and r[4] == "#e0e0e0"]
        assert len(header_bg) == 1, f"Expected 1 header bg rect, got {header_bg}"

        # Stripe rects: row-height rects with stripe fill (not summary row)
        stripe_rects = [r for r in rects if r[4] == "#f0f0f0"]
        assert len(stripe_rects) >= 1, (
            f"Expected at least 1 stripe rect, got {stripe_rects}"
        )

        bg_right = header_bg[0][0] + header_bg[0][2]
        stripe_right = max(r[0] + r[2] for r in stripe_rects)

        assert abs(bg_right - stripe_right) <= _TOL, (
            f"Header bg right edge ({bg_right}) != stripe right edge ({stripe_right}); "
            f"diff={abs(bg_right - stripe_right):.2f}px"
        )

    def test_bg_rule_stripe_left_edges_agree(self, make_chart):
        """Header bg and row stripes left edges all agree."""
        svg = _render_with_summary(make_chart, width=600)
        rects = _rects_with_width(svg)

        header_bg = [r for r in rects if r[1] < 5 and r[3] > 20 and r[4] == "#e0e0e0"]
        stripe_rects = [r for r in rects if r[4] == "#f0f0f0"]

        bg_left = header_bg[0][0]
        stripe_left = min(r[0] for r in stripe_rects)

        assert abs(bg_left - stripe_left) <= _TOL, (
            f"Header bg left edge ({bg_left}) != stripe left edge ({stripe_left}); "
            f"diff={abs(bg_left - stripe_left):.2f}px"
        )

    def test_right_edges_agree_at_multiple_widths(self, make_chart):
        """Edges agree at 400, 600, 800, and 1120px widths."""
        for width in (400, 600, 800, 1120):
            svg = _render_with_summary(make_chart, width=width)
            rects = _rects_with_width(svg)

            header_bg = [
                r for r in rects if r[1] < 5 and r[3] > 20 and r[4] == "#e0e0e0"
            ]
            stripe_rects = [r for r in rects if r[4] == "#f0f0f0"]

            assert header_bg, f"width={width}: no header bg rect found"
            assert stripe_rects, f"width={width}: no stripe rects found"

            bg_right = header_bg[0][0] + header_bg[0][2]
            stripe_right = max(r[0] + r[2] for r in stripe_rects)

            assert abs(bg_right - stripe_right) <= _TOL, (
                f"width={width}: bg_right={bg_right} != stripe_right={stripe_right}; "
                f"diff={abs(bg_right - stripe_right):.2f}px"
            )

    def test_right_edges_agree_with_numeric_last_column(self, make_chart):
        """With a numeric last column, lane-based extent used to be narrower.

        After the fix both the header bg and any stripes/rules extend to
        cell_pad from the right edge of the last cell — not to the numeric
        lane boundary.
        """

        from dbt_charts.core.compile.models.style.theme import TableRowStripeStyle
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        new_table = tc.model_copy(
            update={
                "header": tc.header.model_copy(update={"background": "#d0d0d0"}),
                "row": tc.row.model_copy(
                    update={"stripe": TableRowStripeStyle(color="#ebebeb")}
                ),
            }
        )
        custom_ctx = dataclasses.replace(es, table=new_table)
        custom_rs = dataclasses.replace(
            _RS,
            chart_defaults=dataclasses.replace(_RS.chart_defaults, table=new_table),
        )

        # last column is numeric so it gets a lane position
        chart = make_chart(
            "table",
            style={"table": {"columns": {"label": {}, "amount": {}}}},
        )
        data = [
            {"label": "Alpha", "amount": 1234.5},
            {"label": "Beta", "amount": 5678.9},
            {"label": "Gamma", "amount": 0.0},
            {"label": "Delta", "amount": -50.0},
        ]
        chart = resolve(chart, [], chart_style_context=custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=custom_rs,
        )
        rects = _rects_with_width(svg)

        header_bg = [r for r in rects if r[1] < 5 and r[3] > 20 and r[4] == "#d0d0d0"]
        stripe_rects = [r for r in rects if r[4] == "#ebebeb"]

        assert header_bg, "Expected header bg rect"
        assert stripe_rects, "Expected at least one stripe rect"

        bg_right = header_bg[0][0] + header_bg[0][2]
        stripe_right = max(r[0] + r[2] for r in stripe_rects)

        assert abs(bg_right - stripe_right) <= _TOL, (
            f"Numeric last col: bg_right={bg_right} != stripe_right={stripe_right}; "
            f"diff={abs(bg_right - stripe_right):.2f}px"
        )

    def test_continuous_header_rule_narrower_than_bg(self, make_chart):
        """Header bg is edge-to-edge; continuous rule stays inset (rules are out of scope).

        After adopting C+, fills paint edge-to-edge but line accents (rules)
        retain the cell_pad inset. The bg right edge must be strictly right of
        the rule right edge.
        """

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        new_table = tc.model_copy(
            update={
                "header": tc.header.model_copy(
                    update={
                        "background": "#c8c8c8",
                        "divider": tc.header.divider.model_copy(
                            update={"continuous": True, "width": 2.0}
                        ),
                    }
                )
            }
        )
        custom_ctx = dataclasses.replace(es, table=new_table)
        custom_rs = dataclasses.replace(
            _RS,
            chart_defaults=dataclasses.replace(_RS.chart_defaults, table=new_table),
        )

        chart = make_chart(
            "table",
            style={"table": {"columns": {"label": {}, "amount": {}}}},
        )
        data = [
            {"label": "X", "amount": 10},
            {"label": "Y", "amount": 20},
        ]
        chart = resolve(chart, [], chart_style_context=custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=custom_rs,
        )
        rects = _rects_with_width(svg)

        header_bg = [r for r in rects if r[1] < 5 and r[3] > 20 and r[4] == "#c8c8c8"]
        # Continuous header rule is a thin rect (height == rule_width) just below header
        header_rules = [r for r in rects if abs(r[3] - 2.0) < 0.5 and r[1] > 20]

        assert header_bg, "Expected header bg rect"
        assert header_rules, f"Expected continuous header rule rect; got rects={rects}"

        bg_right = header_bg[0][0] + header_bg[0][2]
        rule_right = max(r[0] + r[2] for r in header_rules)

        # bg is edge-to-edge (wider); rule is inset (narrower).
        # Exact delta depends on render-time cell_pad (subject to fit-cascade
        # and font-size scaling); just pin the strict inequality.
        assert bg_right > rule_right, (
            f"Header bg right ({bg_right}) must exceed rule right ({rule_right}); "
            f"bg is edge-to-edge, rule stays inset."
        )


class TestEdgeToEdgeFills:
    """Header bg, stripes, and scale fills all paint edge-to-edge (no cell_pad inset).

    After adopting C+, every fill surface paints from the cell/table edge,
    not from the inset content span. The key invariant: the fill's left edge
    must equal padding_x (not padding_x + cell_pad).
    """

    def _padding_x_and_cell_pad(self) -> tuple[int, int]:
        """Return (padding_x, cell_pad) from the compiled config."""

        es = resolve_chart_style_context(get_theme_style())
        padding_x = int(es.table.outer_padding)
        cell_pad = int(es.table.column_layout.cell_padding)
        return padding_x, cell_pad

    def test_header_bg_starts_at_padding_x_not_inset(self, make_chart):
        """Header bg left edge equals padding_x — no cell_pad inset.

        Old model: bg_left = padding_x + cell_pad.
        New model: bg_left = padding_x.
        This test fails under old code.
        """

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        padding_x, cell_pad = self._padding_x_and_cell_pad()
        es = resolve_chart_style_context(get_theme_style())
        new_table = es.table.model_copy(
            update={
                "header": es.table.header.model_copy(update={"background": "#b0b0b0"})
            }
        )
        custom_ctx = dataclasses.replace(es, table=new_table)
        custom_rs = dataclasses.replace(
            _RS,
            chart_defaults=dataclasses.replace(_RS.chart_defaults, table=new_table),
        )

        chart = make_chart(
            "table",
            style={"table": {"columns": {"label": {}, "amount": {}}}},
        )
        data = [
            {"label": "Alpha", "amount": 100},
            {"label": "Beta", "amount": 200},
        ]
        chart = resolve(chart, [], chart_style_context=custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=custom_rs,
        )
        rects = _rects_with_width(svg)

        header_bg = [r for r in rects if r[1] < 5 and r[3] > 20 and r[4] == "#b0b0b0"]
        assert len(header_bg) == 1, f"Expected 1 header bg rect, got {header_bg}"

        bg_left = header_bg[0][0]
        # Edge-to-edge: starts at padding_x, not padding_x + cell_pad.
        assert abs(bg_left - padding_x) <= _TOL, (
            f"Header bg left={bg_left} must equal padding_x={padding_x} "
            f"(edge-to-edge). Old inset would be {padding_x + cell_pad}. "
            f"diff={abs(bg_left - padding_x):.2f}px"
        )

    def test_stripe_starts_at_padding_x_not_inset(self, make_chart):
        """Stripe left edge equals padding_x — no cell_pad inset."""

        from dbt_charts.core.compile.models.style.theme import TableRowStripeStyle
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        padding_x, cell_pad = self._padding_x_and_cell_pad()
        es = resolve_chart_style_context(get_theme_style())
        new_table = es.table.model_copy(
            update={
                "row": es.table.row.model_copy(
                    update={"stripe": TableRowStripeStyle(color="#d8d8d8")}
                )
            }
        )
        custom_ctx = dataclasses.replace(es, table=new_table)
        custom_rs = dataclasses.replace(
            _RS,
            chart_defaults=dataclasses.replace(_RS.chart_defaults, table=new_table),
        )

        chart = make_chart(
            "table",
            style={"table": {"columns": {"label": {}, "amount": {}}}},
        )
        data = [
            {"label": "Alpha", "amount": 100},
            {"label": "Beta", "amount": 200},
        ]
        chart = resolve(chart, [], chart_style_context=custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=custom_rs,
        )
        rects = _rects_with_width(svg)

        stripe_rects = [r for r in rects if r[4] == "#d8d8d8"]
        assert stripe_rects, "Expected at least one stripe rect"

        stripe_left = min(r[0] for r in stripe_rects)
        assert abs(stripe_left - padding_x) <= _TOL, (
            f"Stripe left={stripe_left} must equal padding_x={padding_x} "
            f"(edge-to-edge). Old inset would be {padding_x + cell_pad}. "
            f"diff={abs(stripe_left - padding_x):.2f}px"
        )

    def test_scale_fill_matches_static_fill_left_edge(self, make_chart):
        """Scale (conditional) fill left edge equals static column.background left edge.

        Both must be edge-to-edge (cell_x). Under the old model, scale fills
        were inset by cell_pad while static fills were edge-to-edge — they
        differed by exactly cell_pad px. This test catches that regression.
        """
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        # Render with a scale fill on a single column (x=None, y=None avoids
        # auto-injected x_field/y_field columns shifting offsets).
        # Use the explicit scale syntax: {"background": {"palette": [...], "min": ..., "max": ...}}
        chart_scale = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "score": {
                        "scale": {
                            "background": {
                                "palette": ["#ffffff", "#ff0000"],
                                "min": 0,
                                "max": 100,
                            }
                        },
                    },
                }
            },
        )
        data = [
            {"score": 100},
            {"score": 0},
        ]  # row 0 = max → full red; row 1 = min → white
        chart_scale = resolve(chart_scale, [], chart_style_context=_CTX)
        svg_scale = render_table_svg(
            chart_scale,
            data,
            width=400,
            board_style=_RS,
        )
        rects_scale = _rects_with_width(svg_scale)

        # The max-value row (score=100) gets fill=#ff0000.
        scale_fills = [r for r in rects_scale if r[4] == "#ff0000"]
        assert scale_fills, (
            f"Expected scale fill rects with #ff0000; rects={rects_scale}"
        )
        scale_left = scale_fills[0][0]
        scale_right = scale_fills[0][0] + scale_fills[0][2]

        # Render with a static background on the same column — reference geometry.
        chart_static = make_chart(
            "table",
            x=None,
            y=None,
            style={
                "columns": {
                    "score": {"background": "#ccbbaa"},
                }
            },
        )
        chart_static = resolve(chart_static, [], chart_style_context=_CTX)
        svg_static = render_table_svg(
            chart_static,
            data,
            width=400,
            board_style=_RS,
        )
        rects_static = _rects_with_width(svg_static)

        static_fills = [r for r in rects_static if r[4] == "#ccbbaa"]
        assert static_fills, f"Expected static fill rects; rects={rects_static}"
        static_left = static_fills[0][0]
        static_right = static_fills[0][0] + static_fills[0][2]

        # Under C+, scale and static fills share the same geometry (edge-to-edge).
        # Under old code, scale fill was inset by cell_pad → scale_left > static_left.
        assert abs(scale_left - static_left) <= _TOL, (
            f"Scale fill left={scale_left} != static fill left={static_left}; "
            f"diff={abs(scale_left - static_left):.2f}px. "
            f"Under old code, scale fills were inset by cell_pad, static were not."
        )
        assert abs(scale_right - static_right) <= _TOL, (
            f"Scale fill right={scale_right} != static fill right={static_right}; "
            f"diff={abs(scale_right - static_right):.2f}px."
        )

    def test_role_bg_starts_at_padding_x_not_inset(self, make_chart):
        """Per-role row background left edge equals padding_x — no cell_pad inset."""

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        padding_x, cell_pad = self._padding_x_and_cell_pad()
        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        base_row = tc.row
        new_table = tc.model_copy(
            update={
                "row": base_row.model_copy(
                    update={
                        "role": "kind",
                        "roles": base_row.roles.model_copy(
                            update={
                                "summary": base_row.roles.summary.model_copy(
                                    update={"background": "#aabbcc"}
                                )
                            }
                        ),
                    }
                )
            }
        )
        custom_ctx = dataclasses.replace(es, table=new_table)
        custom_rs = dataclasses.replace(
            _RS,
            chart_defaults=dataclasses.replace(_RS.chart_defaults, table=new_table),
        )

        chart = make_chart(
            "table",
            style={"columns": {"label": {}, "amount": {}}},
        )
        data = [
            {"label": "Alpha", "amount": 100, "kind": "data"},
            {"label": "Total", "amount": 100, "kind": "summary"},
        ]
        chart = resolve(chart, [], chart_style_context=custom_ctx)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=custom_rs,
        )
        rects = _rects_with_width(svg)

        role_bg_rects = [r for r in rects if r[4] == "#aabbcc"]
        assert role_bg_rects, "Expected per-role background rect with #aabbcc"

        role_bg_left = role_bg_rects[0][0]
        assert abs(role_bg_left - padding_x) <= _TOL, (
            f"Role bg left={role_bg_left} must equal padding_x={padding_x} "
            f"(edge-to-edge). Old inset would be {padding_x + cell_pad}. "
            f"diff={abs(role_bg_left - padding_x):.2f}px"
        )
