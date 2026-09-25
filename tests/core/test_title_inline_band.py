"""Tests for variables.position: title-inline (title + controls on one band)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render.board_resolve import (
    build_resolved_board_static as resolve_board,
)
from dbt_charts.core.render.boards import render_board_svg
from dbt_charts.core.render.sizing import (
    compute_title_variables_inline_band_height,
    compute_title_variables_inline_baseline_layout,
    compute_variable_controls_height,
    get_title_height,
    resolve_title_variables_inline_widths,
    should_use_title_inline_band,
    title_baseline_offset,
)

from ._board_utils import apply_static_layout
from ._control_utils import render_strip_for
from ._svg_render import authored_boxes, variables_box_x


class TestTitleInlineBand:
    def test_band_height_matches_max_of_title_and_variables_columns(self) -> None:
        yaml_content = """
title: "Short"
variables:
  a:
    input: text
    default: x
style:
  variables:
    position: title-inline
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, result.errors
        board = apply_static_layout(result.board)
        content_w = float(board.layout.content_width)
        band = compute_title_variables_inline_band_height(
            board, content_w, board.variable_defaults
        )
        vs = board.resolved_style.variables
        card_pad = float(board.resolved_style.frame.card_padding)
        inner = max(content_w - 2 * card_pad, 1.0)
        title_w, vars_w = resolve_title_variables_inline_widths(
            inner,
            board.resolved_style,
            board.visible_variables,
            board.title,
            board.variable_defaults,
            board.level,
        )
        assert board.title is not None
        title_h = max(
            get_title_height(board.title, title_w, board.variable_defaults),
            float(board.resolved_style.title.min_height),
        )
        var_h = compute_variable_controls_height(
            board.visible_variables, vars_w, board.variable_defaults, vs
        )
        # Baseline-aligned layout grows the band at least enough to fit both
        # columns; for typical title sizes one column shifts ~1px so band is
        # max(title_h, var_h) + a small delta. Assert ≥ max with a tolerance
        # against the helper that owns the math.
        _t_dy, _v_dy, expected = compute_title_variables_inline_baseline_layout(
            title_h,
            var_h,
            title_baseline_offset(board.resolved_style, board.level),
            float(vs.font.size),
            vs.font.family,
            float(vs.title_inline_band_bottom_pad),
        )
        assert band == expected
        assert band >= max(title_h, var_h)

    def test_baseline_alignment_translates_one_column_off_zero(self) -> None:
        """Title-inline band shifts at least one column down so text baselines
        align — distinct from the legacy top-anchored layout where both
        columns translated at y=0. Pin the property on the deterministic
        helper output rather than scanning the rendered SVG (where every
        layout container has a translate and the assertion can't tell the
        band's columns apart from the rest)."""
        # For typical board titles at h1 (~24px Inter) and a single-row 36px
        # variables container with 11px labels, the variables column shifts
        # ~9px down so the label baseline meets the title baseline. The
        # exact delta is owned by the helper; we just pin that one of the
        # two translates is strictly positive.
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        style = resolve_style(get_theme_style())
        title_dy, vars_dy, band_h = compute_title_variables_inline_baseline_layout(
            title_h=38.4,  # mdsvg's natural block height for 24px Inter at line-height 1.3
            vars_h=36.0,  # default variables container_height
            title_baseline=title_baseline_offset(style, 1),
            label_font_size=11.0,  # default variables font size
            label_font_family=style.variables.font.family,
            pad=24.0,  # default title_inline_band_bottom_pad from _base.yaml
        )
        assert title_dy > 0 or vars_dy > 0, (
            f"expected at least one column shifted off zero for baseline alignment, "
            f"got title_dy={title_dy}, vars_dy={vars_dy}"
        )
        # And the band height fits both columns with their shifts.
        assert band_h >= max(title_dy + 38.4, vars_dy + 36.0)

    def test_render_inlines_variables_band_beside_title(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        yaml_content = """
title: "# Dashboard"
variables:
  region:
    input: text
    default: west
style:
  variables:
    position: title-inline
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, result.errors
        apply_static_layout(result.board)
        resolved = resolve_board(result.board)
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg = render_board_svg(
            resolved,
            executor,
            result.board.variable_defaults,
            background=None,
            render_cache={},
        )
        # Two columns in one band: title group + variables group, with the
        # strip packed against the far edge opposite the title.
        assert svg.count('transform="translate(') >= 2
        assert 'data-dbt-align="end"' in svg

    def test_long_title_gets_reasonable_column_with_daterange_filters(self) -> None:
        """Title-inline must not squeeze the title to min width when filters fit."""
        yaml_content = """
title: "Dundersign Company Overview"
variables:
  date_range:
    input: daterange
    label: Date Range
  signup_source:
    input: select
    label: Signup Source
    options:
      static: [a, b]
  plan:
    input: select
    label: Plan
    options:
      static: [free, pro]
style:
  frame:
    width: 1440
  variables:
    position: title-inline
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, result.errors
        board = apply_static_layout(result.board)
        inner = max(
            float(board.layout.content_width)
            - 2 * float(board.resolved_style.frame.card_padding),
            1.0,
        )
        title_w, _vars_w = resolve_title_variables_inline_widths(
            inner,
            board.resolved_style,
            board.visible_variables,
            board.title,
            board.variable_defaults,
            board.level,
        )
        assert title_w >= 280.0

    def test_inline_band_falls_back_when_variables_need_more_than_two_rows(
        self,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Title-inline is only for compact one/two-row variable strips.

        If the controls need a third row beside the title, the renderer should
        use the less compact full-width variables band under the title instead.
        """
        yaml_content = """
title: "Pipeline"
variables:
  date_range:
    input: daterange
    label: Date Range Across Every Reporting Region
  signup_source:
    input: select
    label: Signup Source Across Every Reporting Region
    options:
      static: [a, b]
  plan:
    input: select
    label: Plan Across Every Reporting Region
    default: free
    options:
      static: [free, pro]
  segment:
    input: select
    label: Segment Across Every Reporting Region
    options:
      static: [smb, enterprise]
  owner:
    input: select
    label: Owner Across Every Reporting Region
    options:
      static: [alex, blair]
  customer_tier:
    input: select
    label: Customer Tier Across Every Reporting Region
    options:
      static: [gold, silver]
  region:
    input: select
    label: Region Across Every Reporting Region
    options:
      static: [east, west]
  channel:
    input: select
    label: Channel Across Every Reporting Region
    options:
      static: [direct, partner]
  lifecycle:
    input: select
    label: Lifecycle Across Every Reporting Region
    options:
      static: [new, renewal]
  pipeline_stage:
    input: select
    label: Pipeline Stage Across Every Reporting Region
    options:
      static: [open, won]
style:
  variables:
    position: title-inline
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, result.errors
        apply_static_layout(result.board)
        resolved = resolve_board(result.board)
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg = render_board_svg(
            resolved,
            executor,
            result.board.variable_defaults,
            background=None,
            render_cache={},
        )

        # In fallback mode the title and variables render as separate groups,
        # so the strip keeps normal leading-edge flow instead of title-inline
        # far-edge packing.
        assert "data-dbt-variables-box" in svg
        assert 'data-dbt-align="start"' in svg
        assert 'data-dbt-align="end"' not in svg


class TestTitleSelectionBoxDoesNotOverhangVariablesColumn:
    """Regression: the title band's selection box claims card_padding on both
    horizontal sides, but its right neighbor in the band is the variables
    column, not the card edge — so the box overhangs the column by
    card_padding minus variables.gap whenever the gap is narrower than the
    padding. Computed from the rendered geometry (not the theme's literal
    16/10 defaults), so the assertion tracks whatever the theme actually
    produces rather than a value pinned here. Its regression power still
    depends on the current defaults keeping variables.gap narrower than
    frame.card_padding — if a theme edit widened the gap past the padding,
    the assertion would pass trivially regardless of the fix.
    """

    def test_title_box_right_edge_does_not_cross_variables_column_origin(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        yaml_content = """
title: "Sales"
variables:
  region:
    input: text
    default: west
style:
  variables:
    position: title-inline
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, result.errors
        apply_static_layout(result.board)
        resolved = resolve_board(result.board)
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg = render_board_svg(
            resolved,
            executor,
            result.board.variable_defaults,
            background=None,
            render_cache={},
        )

        title_x, _y, title_w, _h = authored_boxes(svg, "dbt-box-outer")["title"]
        title_right_edge = title_x + title_w
        variables_x = variables_box_x(svg)

        assert title_right_edge <= variables_x, (
            f"title selection box right edge {title_right_edge} overhangs the "
            f"variables column starting at {variables_x} by "
            f"{title_right_edge - variables_x}px"
        )


class TestPaintTitleSvgFillRegression:
    """Regression tests for the title-color paint helper.

    The helper switched from `fill="..."` (presentation attribute) to inline
    `style="fill: ..."` because SVG/CSS specificity lets a class rule
    (`md-heading`) beat the presentation attribute, silently swallowing
    user-set title colors. These tests pin that contract.
    """

    def test_paints_inline_style_not_presentation_attribute(self) -> None:
        from dbt_charts.core.render.boards import _paint_title_svg_fill

        svg = '<text x="0" y="20" class="md-heading">Hello</text>'
        result = _paint_title_svg_fill(svg, "#494A4C")
        assert 'style="fill: #494A4C"' in result, result
        # Must NOT use the presentation attribute (class rule would override).
        assert 'fill="#494A4C"' not in result, result

    def test_paints_every_text_element_not_just_first(self) -> None:
        from dbt_charts.core.render.boards import _paint_title_svg_fill

        svg = '<text class="md-heading">A</text><text class="md-heading">B</text>'
        result = _paint_title_svg_fill(svg, "#494A4C")
        assert result.count('style="fill: #494A4C"') == 2, result

    def test_repeat_paint_does_not_produce_duplicate_style_attribute(self) -> None:
        from dbt_charts.core.render.boards import _paint_title_svg_fill

        svg = '<text class="md-heading">Hi</text>'
        once = _paint_title_svg_fill(svg, "#494A4C")
        twice = _paint_title_svg_fill(once, "#626366")
        # Re-paint rewrites the existing fill — no second style="…" attr.
        assert twice.count('style="') == 1, twice
        assert "fill: #626366" in twice, twice
        assert "fill: #494A4C" not in twice, twice

    def test_repaint_preserves_sibling_style_declarations(self) -> None:
        from dbt_charts.core.render.boards import _paint_title_svg_fill

        # mdsvg today only writes class-styled <text>, but if upstream ever
        # ships <text style="font-weight: 500; fill: red">…</text>, a re-paint
        # must not delete font-weight while rewriting fill.
        svg = '<text style="font-weight: 500; fill: red">X</text>'
        result = _paint_title_svg_fill(svg, "#494A4C")
        assert "font-weight: 500" in result, result
        assert "fill: #494A4C" in result, result
        assert "fill: red" not in result, result

    def test_mixed_content_title_paints_all_text_and_tspan_uniformly(self) -> None:
        """A title with a code or link span renders as `<text class="md-heading">`
        wrapping `<tspan style="…; fill: red">` for the styled run. Repaint
        must hit BOTH the parent <text> (via injected style attribute) AND
        the child <tspan> (via fill-rewrite) so the title paints uniformly
        in the override color — not half override / half theme."""
        from dbt_charts.core.render.boards import _paint_title_svg_fill

        svg = (
            '<text class="md-heading">Sales '
            '<tspan style="font-family: mono; fill: red">Q3</tspan>'
            "</text>"
        )
        result = _paint_title_svg_fill(svg, "#494A4C")
        # <text> got an inline style with the override color.
        assert 'style="fill: #494A4C"' in result, result
        # <tspan>'s existing fill was rewritten to the override.
        assert "fill: #494A4C" in result, result
        # No surviving "fill: red" anywhere.
        assert "fill: red" not in result, result
        # Sibling style declarations in the <tspan> are preserved.
        assert "font-family: mono" in result, result

    def test_repaint_does_not_touch_fills_inside_style_blocks(self) -> None:
        """`<style>` CSS blocks define class-level fills (e.g. `.md-text {
        fill: #1a1a1a }`) — repaint must only rewrite inline `style="..."`
        attribute fills, not CSS rule fills, or it would collapse every body-
        text color to the title color on the second pass."""
        from dbt_charts.core.render.boards import _paint_title_svg_fill

        svg = (
            "<style>"
            ".md-text { fill: #1a1a1a; }"
            ".md-heading { fill: #111111; }"
            "</style>"
            '<text class="md-heading" style="fill: red">Hi</text>'
        )
        result = _paint_title_svg_fill(svg, "#494A4C")
        # The inline attribute's fill is rewritten…
        assert 'style="fill: #494A4C"' in result, result
        # …but the CSS-rule fills stay where they are.
        assert ".md-text { fill: #1a1a1a; }" in result, result
        assert ".md-heading { fill: #111111; }" in result, result


class TestTitleColorCascadeSymmetry:
    """Inline-band path and non-inline title path must produce the same title
    color for the same board style. Asymmetry would mean toggling
    `variables.position` visibly changes the title color, which is a silent
    layout-dependent rendering quirk."""

    _YAML = """
title: "Sales"
variables:
  region:
    input: text
    default: west
style:
  variables:
    position: {position}
  title:
    font:
      color: "#7a7a7a"
queries:
  q:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
      - {{month: Feb, revenue: 150}}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""

    def _render(
        self, position: str, local_project: Callable[..., FilesystemProject]
    ) -> str:
        result = compile(self._YAML.format(position=position))
        assert result.success, result.errors
        apply_static_layout(result.board)
        resolved = resolve_board(result.board)
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        return render_board_svg(
            resolved,
            executor,
            result.board.variable_defaults,
            background=None,
            render_cache={},
        )

    def test_title_font_color_applies_in_both_layout_modes(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        inline = self._render("title-inline", local_project)
        top = self._render("top", local_project)
        # Both modes must produce a <text> carrying the board's title color.
        assert 'style="fill: #7a7a7a"' in inline, inline[:500]
        assert 'style="fill: #7a7a7a"' in top, top[:500]


class TestTitleFontSizeCascadeSymmetry:
    """The title-inline band must size the title against the board's inner
    width, not the narrow title column it shares with the variable controls.
    Otherwise a short title in a wide board renders one tier smaller (H2 18px
    instead of H1 24px) the moment a filter is added, because the title's
    cramped column falls in the narrow width tier (≥360 < 560 → offset +1).
    Toggling `variables.position` must not silently change the title size.
    """

    _YAML = """
title: "Sales"
variables:
  date_range:
    input: daterange
    label: Date Range
  signup_source:
    input: select
    label: Signup Source
    options:
      static: [a, b]
style:
  frame:
    width: 1440
  variables:
    position: {position}
queries:
  q:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
      - {{month: Feb, revenue: 150}}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""

    def _title_font_size(
        self, position: str, local_project: Callable[..., FilesystemProject]
    ) -> str:
        import re

        result = compile(self._YAML.format(position=position))
        assert result.success, result.errors
        apply_static_layout(result.board)
        resolved = resolve_board(result.board)
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg = render_board_svg(
            resolved,
            executor,
            result.board.variable_defaults,
            background=None,
            render_cache={},
        )
        m = re.search(
            r'<text[^>]*\bfont-size="(\d+(?:\.\d+)?)"[^>]*class="md-[0-9a-f]{8}-heading"',
            svg,
        )
        assert m is not None, f"no md-heading <text> in {position} render"
        return m.group(1)

    def test_title_font_size_matches_in_both_layout_modes(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        inline = self._title_font_size("title-inline", local_project)
        top = self._title_font_size("top", local_project)
        assert inline == top, (
            f"title font-size differs across position modes: "
            f"title-inline={inline}, top={top}"
        )


class TestTitleInlineVariablesJustifyContent:
    """Variable controls in a title-inline band must pack against the right
    edge of the band, beside the title. With the default `flex-start` they
    float mid-band against the title column when the title is short, which
    looks wrong — the band's whole point is title left / controls right.
    """

    def test_title_inline_variables_container_justifies_to_flex_end(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        yaml_content = """
title: "Sales"
variables:
  date_range:
    input: daterange
    label: Date Range
style:
  frame:
    width: 1440
  variables:
    position: title-inline
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, result.errors
        apply_static_layout(result.board)
        resolved = resolve_board(result.board)
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg = render_board_svg(
            resolved,
            executor,
            result.board.variable_defaults,
            background=None,
            render_cache={},
        )
        # The runtime reads the packing edge off the anchor box, so that is
        # where the inline band's far-edge packing has to show up.
        assert 'data-dbt-align="end"' in svg

    def test_non_inline_variables_container_uses_default_justify(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """`position: top` (and other non-inline positions) keep the default
        leading-edge packing so variables flow naturally from the content edge.
        Only the title-inline band packs against the far edge. Asserted via a
        direct call to the strip renderer since the root board-render path skips
        the strip when no defaults are bound."""

        yaml_content = """
title: "Sales"
variables:
  date_range:
    input: daterange
    label: Date Range
style:
  frame:
    width: 1440
  variables:
    position: top
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, result.errors
        apply_static_layout(result.board)
        resolved = resolve_board(result.board)
        svg, _h = render_strip_for(
            result.board.visible_variables,
            result.board.variable_defaults,
            1000.0,
            None,
            resolved.style,
            variables_path="variables",
        )
        assert 'data-dbt-align="start"' in svg
        assert 'data-dbt-align="end"' not in svg


class TestMeasureTitleSingleLineWidth:
    """Regression tests for the title natural-width measurement helper.

    The PIL-backed measurer was introduced because mdsvg's `measure()`
    returns the constraint width when asked for an unbounded layout, not the
    natural content extent. The heading-prefix strip uses an anchored regex
    rather than `lstrip("# ")` to avoid mangling titles like "#1 Product"
    or "## Q3 ##".
    """

    def _measure(self, title: str | None) -> float:
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.sizing import _measure_title_single_line_width

        return _measure_title_single_line_width(
            title, {}, resolve_style(get_theme_style()), 1
        )

    def test_empty_title_returns_zero(self) -> None:
        assert self._measure(None) == 0.0
        assert self._measure("") == 0.0

    def test_longer_title_returns_proportionally_more_width(self) -> None:
        short = self._measure("Hi")
        long = self._measure("Hi there friends, hello")
        assert long > short * 5, (short, long)

    def test_markdown_heading_prefix_is_stripped(self) -> None:
        plain = self._measure("Sales Dashboard")
        with_prefix = self._measure("# Sales Dashboard")
        # Width should match (the `# ` is markdown noise, not glyphs to render).
        assert abs(plain - with_prefix) < 1.0, (plain, with_prefix)

    def test_standalone_hash_is_preserved(self) -> None:
        """`#1 Product` is a legitimate title, not a heading. The `#1` must
        survive measurement so the title doesn't under-reserve its column."""
        with_hash = self._measure("#1 Product")
        no_hash = self._measure("1 Product")
        # `#` adds visible glyph width; the with-hash measurement must be wider.
        assert with_hash > no_hash, (with_hash, no_hash)


class TestComputeVariableControlsHeightWrapEpsilon:
    """Regression test for the float-precision flex-wrap bug.

    Two controls that sum exactly to the available width used to be
    misallocated to two rows because `total > avail` returned True under
    sub-pixel float drift (e.g. 465.8 vs 465.79999999999995). A 0.5 px
    tolerance now gives sub-pixel-precise widths the benefit of the doubt.
    """

    def test_controls_that_fit_exactly_stay_on_one_row(self) -> None:
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.sizing import compute_variable_controls_height
        from dbt_charts.core.render.variables_layout import lay_out_variables
        from dbt_charts.core.render.variables_resolve import resolve_controls

        vs = resolve_style(get_theme_style()).variables
        container_h = float(vs.container_height)
        variable_defs = {
            "date_range": Variable(input="daterange", label="Date Range"),
            "lead_source": Variable(input="select", label="Lead Source"),
        }

        # The exact width the pair occupies, to the last sub-pixel — the width
        # that used to read as an overflow and cost a second row.
        wide = lay_out_variables(
            [c.spec for c in resolve_controls(variable_defs, {}, None, vs)], 4000.0, vs
        )
        exact = wide.boxes[-1].x + wide.boxes[-1].width
        height = compute_variable_controls_height(variable_defs, exact, {}, vs)

        assert height == container_h, (
            f"controls that fit on one row at sub-pixel-precise widths must "
            f"stay on one row, got height={height} (expected {container_h})"
        )


class TestTitleInlineBandFitsWhatItDraws:
    """The band is decided from the board that will be drawn, not a stand-in.

    Both halves of the split are measured: the title column from the board's own
    resolved title typography, the variables column from the values the request
    committed. Measuring either against something else reserves one width and
    draws another, and the two columns land on top of each other.
    """

    _MULTISELECT_BOARD = """
title: "Multi-select chart example"
style:
  variables:
    position: title-inline
variables:
  categories:
    input: multiselect
    label: Categories
    options:
      static: [Analytics, Collaboration, Security, Support]
    default: [Analytics, Security]
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""

    def _board(self, yaml_content: str) -> Board:
        result = compile(yaml_content)
        assert result.success, result.errors
        return result.board

    def _render(
        self,
        yaml_content: str,
        values: dict[str, object],
        local_project: Callable[..., FilesystemProject],
    ) -> str:
        """Render through the data-aware pass, the way a served board is built.

        Not `apply_static_layout`: it takes the container box from
        `get_theme_style()` rather than the board, so a board that sets its own
        `frame.width` is laid out at the theme's — and this suite's whole
        subject is a band that only misbehaves at a width where the title and
        the controls compete.
        """
        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        result = compile(yaml_content)
        assert result.success, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        board, cache = calculate_data_aware_layout(
            result.board, executor, values, render_first=True
        )
        return render_board_svg(
            resolve_board(board), executor, values, background=None, render_cache=cache
        )

    def test_title_column_grows_with_the_boards_own_title_size(self) -> None:
        """A board that renders a bigger title reserves a wider title column.

        The measurement used to read the *default* theme's title tier through
        `get_theme_style()`, so a board whose style enlarges its title reserved
        the small theme's column and drew the large board's title — straight
        under the right-packed controls.
        """
        big = self._MULTISELECT_BOARD.replace(
            "style:\n", "style:\n  title:\n    sizes: [48, 18, 14, 14, 11, 11]\n", 1
        )
        widths = []
        for yaml_content in (self._MULTISELECT_BOARD, big):
            board = self._board(yaml_content)
            title_w, _vars_w = resolve_title_variables_inline_widths(
                1096.0,
                board.resolved_style,
                board.visible_variables,
                title=board.title,
                variable_values=board.variable_defaults,
                level=board.level,
            )
            widths.append(title_w)

        assert widths[1] > widths[0], (
            "the title column must be measured at the size the board renders its "
            f"title, got {widths[1]} for a 48px title and {widths[0]} for 24px"
        )

    def test_title_column_is_measured_in_the_family_the_title_is_drawn_in(
        self,
    ) -> None:
        """A board title is always drawn as a heading, prose or not, and the
        measurement has to follow the render either way.

        Pinned through the one function both sides ask, so a change to that
        rule cannot silently reserve a column in one family and paint the
        title in another. A non-prose title used to measure and paint in the
        *body* family instead of style.title.font.family — invisible on the
        shipped themes, which set both to the same string, but wrong for any
        theme that doesn't. There is no longer a prose/non-prose split to
        pin either side of: a board title always resolves to its own title
        family, full stop.
        """
        import dataclasses

        from dbt_charts.core.render.sizing import (
            _measure_title_single_line_width,
            title_font_family,
        )

        style = self._board(self._MULTISELECT_BOARD).resolved_style
        # A distinctive family, not the theme's: the shipped default sets the
        # title and body families to the same string, so asserting against the
        # style would pass whichever branch the code took.
        in_a_different_family = dataclasses.replace(
            style,
            title=style.title.model_copy(
                update={"font": style.title.font.model_copy(update={"family": "Inter"})}
            ),
        )

        # The rule itself: asserted on the selector rather than by re-measuring,
        # because an expected width computed here would be a second copy of the
        # production formula.
        assert title_font_family(style) == style.title.font.family
        assert title_font_family(in_a_different_family) == "Inter"

        # Not the board's own title: the serif and Inter measure that one identically by
        # coincidence, which would make the inequality below vacuous. Checked, not
        # assumed — that coincidence is exactly what a re-vendored board can introduce.
        title = "Quarterly revenue by segment"
        widths = [
            _measure_title_single_line_width(title, {}, s, 1)
            for s in (style, in_a_different_family)
        ]
        assert widths[0] != widths[1], (
            f"{title!r} reserves {widths[0]} in both families, so this assertion "
            "cannot tell which one the column was measured in — pick a title whose "
            "two families differ"
        )

    def test_band_is_rejected_when_the_committed_value_outgrows_its_column(
        self,
    ) -> None:
        """Every option selected is wider than the default two, and must not
        be squeezed into a column measured for the default."""
        board = self._board(self._MULTISELECT_BOARD)
        card_pad = float(board.resolved_style.frame.card_padding)
        content_width = 600.0 + 2 * card_pad

        fits_at_defaults = should_use_title_inline_band(
            board.title,
            board.visible_variables,
            content_width,
            board.resolved_style,
            card_pad,
            board.variable_defaults,
            level=board.level,
        )
        fits_with_everything_selected = should_use_title_inline_band(
            board.title,
            board.visible_variables,
            content_width,
            board.resolved_style,
            card_pad,
            {"categories": ["Analytics", "Collaboration", "Security", "Support"]},
            level=board.level,
        )

        assert fits_at_defaults, "the default two-member value fits its column"
        assert not fits_with_everything_selected, (
            "a committed value too wide for the variables column must fall back "
            "to the full-width band under the title"
        )

    def test_a_single_over_wide_control_does_not_pass_as_one_row(self) -> None:
        """Rows are not a fit test.

        The layout engine places the first control of a row whatever its width,
        so an over-wide single control reports one row. Counting rows therefore
        accepts exactly the control that cannot fit.
        """
        board = self._board(self._MULTISELECT_BOARD)
        card_pad = float(board.resolved_style.frame.card_padding)

        assert not should_use_title_inline_band(
            board.title,
            board.visible_variables,
            120.0 + 2 * card_pad,
            board.resolved_style,
            card_pad,
            board.variable_defaults,
            level=board.level,
        )

    _JINJA_TITLE_BOARD = """
title: "{{ region }}"
variables:
  region:
    input: select
    label: Region
    options:
      static: [NA, Latin America and the Caribbean]
    default: NA
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""

    def test_a_jinja_title_is_measured_at_the_value_it_interpolates(
        self,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """The title's *height* is measured too, and against the same values.

        A committed value long enough to wrap makes the title two lines where
        the default was one. Measure that against the default and the board
        reserves a slot one line short of what it draws — the title's second
        line lands on whatever follows it.
        """
        short = self._render(self._JINJA_TITLE_BOARD, {"region": "NA"}, local_project)
        wrapped = self._render(
            self._JINJA_TITLE_BOARD,
            {"region": "Latin America and the Caribbean " * 6},
            local_project,
        )

        def board_height(svg: str) -> float:
            return float(ET.fromstring(svg).get("height"))

        assert board_height(wrapped) > board_height(short), (
            "a title that wraps further must be reserved more room, which only "
            "happens if the measurement sees the value being drawn"
        )

    def test_the_render_decides_the_band_from_the_values_it_draws(
        self,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """The wiring, end to end and through the render entry point.

        The unit tests above call the fit test with values handed to them, so
        they stay green even if the render goes back to asking about the
        compiled defaults. This one asks the renderer itself, and the only
        difference between the two calls is the committed value — which is
        exactly the input the defect ignored.

        Asserted on `data-dbt-align`, not on a coordinate: the strip's
        `data-dbt-x` is strip-local, so it says nothing about where the band
        sits on the board. Which strip the render chose does.
        """
        narrow = self._MULTISELECT_BOARD.replace(
            "style:\n", "style:\n  frame:\n    width: 704\n", 1
        )

        at_defaults = self._render(narrow, {}, local_project)
        every_category = self._render(
            narrow,
            {"categories": ["Analytics", "Collaboration", "Security", "Support"]},
            local_project,
        )

        assert 'data-dbt-align="end"' in at_defaults, (
            "the two-member default fits beside the title and takes the band"
        )
        assert 'data-dbt-align="start"' in every_category, (
            "every category selected does not fit beside the title, so the "
            "render must fall back to the full-width strip under it"
        )


class TestInlineBandTitleHeightUsesDrawWidth:
    """Regression: measured title height must use the draw width (inner), not title_w.

    The draw path calls _render_title_svg at `inner` (full band inner width).
    The sizing path was calling get_title_height at `title_w` (the narrower
    title column). After a case transform makes the title-cased string wider,
    it wraps at `title_w` but not at `inner`, over-reserving ~25px of blank
    space under the board title on both affected corpus boards.
    """

    _YAML = """
title: "dbt charts quick start cheat sheet"
style:
  frame:
    width: 1200
  variables:
    position: title-inline
variables:
  date_range:
    input: daterange
    label: Date Range
  signup_source:
    input: select
    label: Signup Source
    options:
      static: [a, b]
  plan:
    input: select
    label: Plan
    options:
      static: [free, pro]
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""

    def test_band_height_uses_inner_width_not_column_width(self) -> None:
        from dbt_charts.core.render.sizing import (
            compute_title_variables_inline_band_height,
            compute_title_variables_inline_baseline_layout,
            compute_variable_controls_height,
            get_title_height,
            resolve_title_variables_inline_widths,
        )

        result = compile(self._YAML)
        assert result.success, result.errors
        board = apply_static_layout(result.board)

        content_w = float(board.layout.content_width)
        card_pad = float(board.resolved_style.frame.card_padding)
        inner = max(content_w - 2 * card_pad, 1.0)
        vs = board.resolved_style.variables

        title_w, vars_w = resolve_title_variables_inline_widths(
            inner,
            board.resolved_style,
            board.visible_variables,
            board.title,
            board.variable_defaults,
            board.level,
        )

        assert board.title is not None
        h_at_inner = max(
            get_title_height(
                board.title,
                inner,
                board.variable_defaults,
                level=board.level,
                resolved_style=board.resolved_style,
            ),
            float(board.resolved_style.title.min_height),
        )
        h_at_title_w = max(
            get_title_height(
                board.title,
                title_w,
                board.variable_defaults,
                level=board.level,
                resolved_style=board.resolved_style,
            ),
            float(board.resolved_style.title.min_height),
        )

        # The fixture must discriminate the two widths for the test to be meaningful.
        assert h_at_inner != h_at_title_w, (
            f"Fixture title must produce different heights at inner={inner:.1f} and "
            f"title_w={title_w:.1f} after case transform. "
            f"Got h_at_inner={h_at_inner}, h_at_title_w={h_at_title_w}. "
            "Adjust the fixture (title, frame.width, or variable count)."
        )

        # Expected band height using inner (the width the draw uses).
        var_h = compute_variable_controls_height(
            board.visible_variables, vars_w, board.variable_defaults, vs
        )
        assert vs.font.size is not None
        _, _, expected_band_h = compute_title_variables_inline_baseline_layout(
            h_at_inner,
            var_h,
            title_baseline_offset(board.resolved_style, board.level),
            float(vs.font.size),
            vs.font.family,
            float(vs.title_inline_band_bottom_pad),
        )

        actual_band_h = compute_title_variables_inline_band_height(
            board, content_w, board.variable_defaults
        )

        assert actual_band_h == expected_band_h, (
            f"Band height must be measured at inner={inner:.1f} (draw width), "
            f"not title_w={title_w:.1f} (column width). "
            f"Got {actual_band_h}, expected {expected_band_h} (from h_inner={h_at_inner}). "
            f"Measured at title_w would give {h_at_title_w}."
        )
