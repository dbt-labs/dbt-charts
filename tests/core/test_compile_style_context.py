"""Tests for board-scoped ResolvedStyle materialization during compile.

Proves:
1. Board carries a resolved_style: ResolvedStyle field after compile.
2. Board-level style: patches apply through StylePatch.
3. Render consumes the pre-compiled style instead of re-merging defaults.
4. Nested boards inherit parent resolved_style with their own patches applied.
"""

from __future__ import annotations

import textwrap

from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)


class TestMergedStyleOnBoard:
    """resolved_style is materialized during compile."""

    MINIMAL_YAML = textwrap.dedent(
        """\
        title: Test Board
        queries:
          q1:
            type: values
            rows:
              - {x: 1, y: 2}
        charts:
          c1:
            query: q1
            type: bar
            x: x
            y: y
        rows:
          - c1
    """
    )

    def test_compiled_board_has_resolved_style(self):
        """Board always carries a ResolvedStyle after compile."""
        result = compile(self.MINIMAL_YAML)
        assert result.success, result.errors
        board = result.board
        assert hasattr(board, "resolved_style")
        assert isinstance(board.resolved_style, ResolvedStyle)

    def test_resolved_style_has_chart_defaults(self):
        """ResolvedStyle.chart_defaults is filled with defaults even with no style block."""
        result = compile(self.MINIMAL_YAML)
        board = result.board
        rs = board.resolved_style
        assert rs.chart_defaults is not None
        assert rs.font is not None
        assert rs.background is not None  # theme-cascaded canvas color

    def test_board_style_background_overrides_default(self):
        """Board-level style.background patch overrides the base default."""
        yaml_with_style = textwrap.dedent(
            """\
            title: Styled Board
            style:
              background: "#ff0000"
            queries:
              q1:
                type: values
                rows:
                  - {x: 1, y: 2}
            charts:
              c1:
                query: q1
                type: bar
                x: x
                y: y
            rows:
              - c1
        """
        )
        result = compile(yaml_with_style)
        assert result.success, result.errors
        rs = result.board.resolved_style
        assert rs.background == "#ff0000"

    def test_board_style_font_overrides_default(self):
        """Board-level style.font patch overrides the base default."""
        yaml_with_style = textwrap.dedent(
            """\
            title: Styled Board
            style:
              font:
                family: "Arial"
            queries:
              q1:
                type: values
                rows:
                  - {x: 1, y: 2}
            charts:
              c1:
                query: q1
                type: bar
                x: x
                y: y
            rows:
              - c1
        """
        )
        result = compile(yaml_with_style)
        assert result.success, result.errors
        rs = result.board.resolved_style
        assert rs.font.family.startswith("Arial")
        # Cascade: font.family should flow to axis label
        assert result.board.chart_style_context.axis.labels.font.family.startswith(
            "Arial"
        )

    def test_nested_board_inherits_resolved_style(self):
        """Nested boards inherit parent resolved_style."""
        yaml_nested = textwrap.dedent(
            """\
            title: Parent Board
            style:
              font:
                family: "Arial"
            queries:
              q1:
                type: values
                rows:
                  - {x: 1, y: 2}
            charts:
              c1:
                query: q1
                type: bar
                x: x
                y: y
            rows:
              - rows:
                  - c1
        """
        )
        result = compile(yaml_nested)
        assert result.success, result.errors
        parent = result.board
        assert parent.resolved_style.font.family.startswith("Arial")

        # Nested board should also inherit
        nested = parent.layout.items[0].board
        assert nested is not None
        assert isinstance(nested.resolved_style, ResolvedStyle)
        assert nested.resolved_style.font.family.startswith("Arial")

    def test_nested_background_style_inherits_every_authored_parent_field(self):
        """A background-only nested style still inherits every field the parent
        explicitly authored."""
        yaml_nested = textwrap.dedent(
            """\
            title: Parent Board
            style:
              font:
                family: "Arial"
              fonts:
                secondary:
                  color: "#aabbcc"
              accent: "#334455"
            rows:
              - text: Child
                style:
                  background: "#ff0000"
        """
        )
        result = compile(yaml_nested)
        assert result.success, result.errors

        nested = result.board.layout.items[0].board
        assert nested is not None
        assert nested.resolved_style.background == "#ff0000"
        assert nested.resolved_style.secondary_color == "#aabbcc"
        assert nested.resolved_style.accent == "#334455"
        assert nested.resolved_style.font.family.startswith("Arial")

    def test_nested_background_fast_path_matches_slow_parent_cascade(self):
        """Background-only and background-plus-token child styles inherit alike."""
        fast_yaml = textwrap.dedent(
            """\
            title: Parent Board
            style:
              font:
                family: "Arial"
              fonts:
                secondary:
                  color: "#aabbcc"
            rows:
              - text: Child
                style:
                  background: "#ff0000"
        """
        )
        slow_yaml = textwrap.dedent(
            """\
            title: Parent Board
            style:
              font:
                family: "Arial"
              fonts:
                secondary:
                  color: "#aabbcc"
            rows:
              - text: Child
                style:
                  background: "#ff0000"
                  accent: "#334455"
        """
        )

        fast = compile(fast_yaml)
        slow = compile(slow_yaml)
        assert fast.success, fast.errors
        assert slow.success, slow.errors

        fast_nested = fast.board.layout.items[0].board
        slow_nested = slow.board.layout.items[0].board
        assert fast_nested is not None
        assert slow_nested is not None
        assert fast_nested.resolved_style.font.family == (
            slow_nested.resolved_style.font.family
        )
        assert (
            fast_nested.resolved_style.secondary_color
            == slow_nested.resolved_style.secondary_color
        )


class TestMergedStyleVegaConfig:
    """style_to_vega_lite on the resolved charts style matches effective config."""

    def test_resolved_charts_style_maps_axis_to_vega_config(self):
        """style_to_vega_lite on resolved charts style produces valid Vega-Lite config."""
        from dbt_charts.core.compile.vega_lite.mapping import style_to_vega_lite

        result = compile(
            textwrap.dedent(
                """\
                title: Vega Config Test
                queries:
                  q1:
                    type: values
                    rows:
                      - {x: 1, y: 2}
                charts:
                  c1:
                    query: q1
                    type: bar
                    x: x
                    y: y
                rows:
                  - c1
            """
            )
        )
        assert result.success, result.errors
        charts = result.board.chart_style_context
        vlc = style_to_vega_lite(charts)
        # axis moved to encoding level; view stays at config
        assert vlc.view is not None
        # axis* removed from VegaLiteConfig — emitted at encoding level by
        # axis_to_vl (vl_field_maps.py)


class TestRenderConsumesResolvedStyle:
    """Render pipeline should consume resolved_style instead of re-merging."""

    def test_apply_presentation_defaults_accepts_style_compiled(self):
        """apply_presentation_defaults can use pre-compiled vega config."""
        from dbt_charts.core.compile.vega_lite.mapping import style_to_vega_lite
        from dbt_charts.core.render.chart.presentation import (
            apply_presentation_defaults,
        )

        ctx = resolve_chart_style_context(get_theme_style())
        sc_dict = style_to_vega_lite(ctx).model_dump(exclude_none=True)
        spec = {"data": {"values": []}, "mark": "bar"}
        result = apply_presentation_defaults(
            spec, ctx.background, effective_vega_config=sc_dict
        )
        assert "config" in result


class TestResolvedChartDefaultsGeoshapeTableTypes:
    """ResolvedChartDefaults.geoshape/.table hold the pre-bake authored style types.

    ResolvedGeoshapeStyle.geoshape is ResolvedGeoshapeChartStyle (post-bake);
    ResolvedChartDefaults.geoshape is GeoshapeChartStyle (pre-bake cascade only).
    This distinguishes the two levels and prevents accidental type narrowing."""

    def test_geoshape_field_is_pre_bake_type(self) -> None:
        from dbt_charts.core.compile.models.style.resolved.geoshape import (
            ResolvedGeoshapeChartStyle,
        )
        from dbt_charts.core.compile.models.style.theme import GeoshapeChartStyle

        rs = resolve_style(get_theme_style())
        geoshape = rs.chart_defaults.geoshape
        assert isinstance(geoshape, GeoshapeChartStyle)
        assert not isinstance(geoshape, ResolvedGeoshapeChartStyle)

    def test_table_field_is_pre_bake_type(self) -> None:
        from dbt_charts.core.compile.models.style.theme import TableChartStyle

        rs = resolve_style(get_theme_style())
        table = rs.chart_defaults.table
        assert isinstance(table, TableChartStyle)
        assert table is not None
