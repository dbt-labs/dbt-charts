"""Tests for style.table.row — nested row presentation model."""

import dataclasses

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)

_BOARD_STYLE = resolve_style(get_theme_style())

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_table_style_accepts_row():
    """TableChartStylePatch.row accepts row style overrides."""
    from dbt_charts.core.compile.models.style.authored import TableChartStylePatch

    ts = TableChartStylePatch.model_validate({"row": {"role": "kind", "height": 20}})
    assert ts.row is not None
    assert ts.row.role == "kind"
    assert ts.row.height == 20


# ---------------------------------------------------------------------------
# Renderer integration tests
# ---------------------------------------------------------------------------


class TestTableRowStyleRendering:
    """Integration tests: nested row style fields affect SVG output."""

    def _data_with_roles(self):
        return [
            {"company": "Apex", "revenue": 1000, "kind": "value"},
            {"company": "Bright", "revenue": 2000, "kind": "value"},
            {"company": "Subtotal", "revenue": 3000, "kind": "summary"},
            {"company": "Total", "revenue": 3000, "kind": "total"},
        ]

    def _style_with_row(self, **row_kwargs):

        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        base_row = tc.row
        rule_data = row_kwargs.pop("rule", None)
        roles_data = row_kwargs.pop("roles", None)

        row_updates: dict = dict(row_kwargs)
        if rule_data is not None:
            row_updates["divider"] = base_row.divider.model_copy(
                update={"visible": True, **rule_data}
            )
        if roles_data is not None:
            summary_data = roles_data.get("summary", {})
            total_data = roles_data.get("total", {})
            summary_base = base_row.roles.summary.model_dump()
            summary_base.update(summary_data)
            total_base = base_row.roles.total.model_dump()
            total_base.update(total_data)
            from dbt_charts.core.compile.models.style.theme import (
                TableRowRolesStyle,
                TableRowRoleStyle,
            )

            row_updates["roles"] = TableRowRolesStyle(
                summary=TableRowRoleStyle.model_validate(summary_base),
                total=TableRowRoleStyle.model_validate(total_base),
            )

        # Zero out variant defaults to isolate test
        new_row = base_row.model_copy(update=row_updates)
        new_header = tc.header.model_copy(
            update={
                "divider": tc.header.divider.model_copy(
                    update={"visible": False, "width": 1}
                )
            }
        )
        # summary divider override: build fresh row with that field set
        summary_with_rule = new_row.roles.summary.model_copy(
            update={
                "divider": new_row.roles.summary.divider.model_copy(
                    update={"visible": True, "width": 0.5}
                )
            }
        )
        new_row = new_row.model_copy(
            update={
                "roles": new_row.roles.model_copy(update={"summary": summary_with_rule})
            }
        )
        new_table = tc.model_copy(update={"row": new_row, "header": new_header})
        return dataclasses.replace(es, table=new_table)

    def test_nested_role_resolves_in_renderer(self, make_chart):
        """row.role column is used for role resolution — summary/total rows get medium weight."""
        import re

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = self._style_with_row(role="kind")
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            self._data_with_roles(),
            width=600,
            board_style=_BOARD_STYLE,
        )
        # Summary and total rows should get font-weight="500" (default medium).
        # Find text elements with font-weight="500" that contain "Subtotal" or "Total".
        summary_texts = re.findall(
            r'<text[^>]*font-weight="500"[^>]*>[^<]*(?:Subtotal|Total)', svg
        )
        assert len(summary_texts) >= 1, f"Expected summary/total weight, got {svg}"

    def test_roles_total_font_weight(self, make_chart):
        """roles.total.font.weight overrides default summary weight for total rows."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = self._style_with_row(
            role="kind",
            roles={
                "summary": {"font": {"weight": "500"}},
                "total": {"font": {"weight": "700"}},
            },
        )
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            self._data_with_roles(),
            width=600,
            board_style=_BOARD_STYLE,
        )
        # Total row cells should have font-weight="700"
        assert 'font-weight="700"' in svg
        # Summary row cells should have font-weight="500"
        assert 'font-weight="500"' in svg

    def test_roles_total_background(self, make_chart):
        """roles.total.background renders a fill rect behind the total row."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = self._style_with_row(
            role="kind",
            roles={
                "total": {"background": "#F0F0F0"},
            },
        )
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            self._data_with_roles(),
            width=600,
            board_style=_BOARD_STYLE,
        )
        assert "#F0F0F0" in svg

    def test_roles_summary_background(self, make_chart):
        """roles.summary.background renders a fill rect behind summary rows."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = self._style_with_row(
            role="kind",
            roles={
                "summary": {"background": "#E0E0FF"},
            },
        )
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            self._data_with_roles(),
            width=600,
            board_style=_BOARD_STYLE,
        )
        assert "#E0E0FF" in svg

    def test_no_row_style_no_change(self, make_chart):
        """When row is omitted, rendering behaves identically to before."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = resolve_chart_style_context(get_theme_style())
        data = [
            {"company": "Apex", "revenue": 1000},
            {"company": "Bright", "revenue": 2000},
        ]
        # Should not crash; no role-based styling applied
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=_BOARD_STYLE,
        )
        assert "<svg" in svg

    def test_row_rule_from_nested_style(self, make_chart):
        """row.divider.width and row.divider.color are used for body row dividers."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = self._style_with_row(rule={"width": 1, "color": "#FF0000"})
        data = [
            {"company": "Apex", "revenue": 1000},
            {"company": "Bright", "revenue": 2000},
            {"company": "Cedar", "revenue": 3000},
        ]
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=_BOARD_STYLE,
        )
        # Row rules should use the specified color
        assert "#FF0000" in svg

    def test_invalid_role_background_raises(self, make_chart):
        """Invalid (non-hex) role background raises InvalidColorError, not silently dropped."""
        import pytest

        from dbt_charts.core.colors import InvalidColorError
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = self._style_with_row(
            role="kind",
            roles={"total": {"background": "not-a-color"}},
        )
        chart = resolve(chart, [], chart_style_context=es)
        with pytest.raises(InvalidColorError, match="Invalid color value"):
            render_table_svg(
                chart,
                self._data_with_roles(),
                width=600,
                board_style=_BOARD_STYLE,
            )

    def test_invalid_role_font_weight_falls_back(self, make_chart):
        """Invalid font.weight in roles falls back to default 500."""
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = self._style_with_row(
            role="kind",
            roles={"total": {"font": {"weight": "bold-invalid"}}},
        )
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            self._data_with_roles(),
            width=600,
            board_style=_BOARD_STYLE,
        )
        # Should fall through to default "500", not interpolate "bold-invalid"
        assert "bold-invalid" not in svg
        assert 'font-weight="500"' in svg


# ---------------------------------------------------------------------------
# Merge precedence tests — both flat and nested set, nested wins
# ---------------------------------------------------------------------------


class TestMergePrecedence:
    """When both flat and nested fields are set, nested wins in SVG output."""

    def test_nested_role_wins_over_flat_row_role(self, make_chart):
        """row.role is the sole role selector (flat row_role no longer exists)."""
        import re

        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        new_row = tc.row.model_copy(
            update={
                "role": "kind",
                "roles": tc.row.roles.model_copy(
                    update={
                        "summary": tc.row.roles.summary.model_copy(
                            update={
                                "divider": tc.row.roles.summary.divider.model_copy(
                                    update={"visible": True, "width": 0.5}
                                )
                            }
                        )
                    }
                ),
            }
        )
        new_header = tc.header.model_copy(
            update={
                "divider": tc.header.divider.model_copy(
                    update={"visible": False, "width": 1}
                )
            }
        )
        es = dataclasses.replace(
            es, table=tc.model_copy(update={"row": new_row, "header": new_header})
        )

        data = [
            {"company": "Apex", "revenue": 1000, "kind": "value"},
            {"company": "Total", "revenue": 1000, "kind": "total"},
        ]
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=_BOARD_STYLE,
        )
        # Total row should get font-weight="500"
        total_weighted = re.findall(r'<text[^>]*font-weight="500"[^>]*>[^<]*Total', svg)
        assert len(total_weighted) >= 1, "row.role should drive role resolution"

    def test_nested_height_wins_over_flat_row_height(self, make_chart):
        """row.height is the sole height source in TableChartStyle."""
        import re

        from dbt_charts.core.compile.models.style.theme import TableRowStripeStyle
        from dbt_charts.core.render.chart.table import (
            render_table_svg as render_table_svg,
        )

        chart = make_chart("table", x=None, y=None)
        es = resolve_chart_style_context(get_theme_style())
        tc = es.table
        # nested: height=20, opt into stripes so row_height visible as rect height
        new_row = tc.row.model_copy(
            update={"height": 20, "stripe": TableRowStripeStyle(color="#eeeeee")}
        )
        es = dataclasses.replace(es, table=tc.model_copy(update={"row": new_row}))

        data = [
            {"company": "Apex", "revenue": 1000},
            {"company": "Bright", "revenue": 2000},
        ]
        chart = resolve(chart, [], chart_style_context=es)
        svg = render_table_svg(
            chart,
            data,
            width=600,
            board_style=_BOARD_STYLE,
        )
        # Stripe rects use row_height. With nested=20, stripe height should be 20.
        stripe_heights = re.findall(r'height="(\d+)"', svg)
        assert "20" in stripe_heights, (
            f"Expected row height 20 from nested style, got heights: {stripe_heights}"
        )
        assert "40" not in stripe_heights, (
            f"Flat row_height 40 should not appear when nested height=20 wins: {stripe_heights}"
        )
