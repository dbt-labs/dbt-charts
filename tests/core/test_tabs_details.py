"""Tests for tabs and details (collapsible sections) features.

Tests variable-controlled tabs and details sections:
- Tab normalization: auto-generated invisible variables, slugs, variable names
- Details normalization: invisible boolean variables, summary metadata
- Tab rendering: clickable SVG links, active tab from variable
- Details rendering: summary bar with disclosure triangle, toggle URL
"""

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.normalize.dispatch import slugify
from dbt_charts.core.compile.resolve.style.board import resolve_style

from ._board_utils import apply_static_layout


def _compile_and_build_executor(yaml_content, local_project):
    from pathlib import Path

    from dbt_charts.core.execute import Executor
    from dbt_charts.core.execute.adapters import build_adapter_registry

    result = compile(yaml_content)
    assert result.success, f"Compile failed: {result.errors}"
    board = result.board
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    return board, executor


def _rs():
    return resolve_style(get_theme_style())


class TestSlugify:
    """Tests for the slugify helper."""

    def test_basic(self):
        assert slugify("Overview") == "overview"

    def test_spaces(self):
        assert slugify("Raw Data") == "raw_data"

    def test_special_chars(self):
        assert slugify("My Tab!") == "my_tab"

    def test_hyphens(self):
        assert slugify("some-tab") == "some_tab"

    def test_mixed(self):
        assert slugify("Tab 1: Charts & Data") == "tab_1_charts_data"

    def test_leading_trailing_hyphens_stripped(self):
        # Regression: titles with leading/trailing punctuation must not produce
        # leading/trailing underscores in board IDs or tab slugs.
        assert slugify("-My Dashboard-") == "my_dashboard"

    def test_leading_trailing_spaces_stripped(self):
        assert slugify("  Overview  ") == "overview"


class TestTabNormalization:
    """Tests for tab layout normalization with variable generation."""

    def test_tabs_generate_hidden_variable(self):
        """Tabs should auto-generate an invisible select variable with _ prefix."""
        yaml_content = """
title: Test
tabs:
  items:
    - title: Overview
      text: "Overview content"
    - title: Details
      text: "Details content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        board = result.board
        # Should have a hidden "_tab_{board_id}" variable (board_id="test" from title)
        assert "_tab_test" in board.variable_registry
        var = board.variable_registry["_tab_test"]
        assert var.visible is False
        assert var.input == "select"
        assert var.default == "overview"

    def test_tabs_with_custom_id(self):
        """Tabs with id should use that as the variable name."""
        yaml_content = """
title: Test
tabs:
  id: view
  default: details
  items:
    - title: Overview
      text: "Overview content"
    - title: Details
      text: "Details content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        board = result.board
        # Should use "view" as variable name
        assert "view" in board.variable_registry
        var = board.variable_registry["view"]
        assert var.visible is False
        assert var.default == "details"

    def test_tabs_slugs_on_layout(self):
        """Layout should have tab_slugs for URL values."""
        yaml_content = """
title: Test
tabs:
  items:
    - title: Overview
      text: "Overview content"
    - title: Raw Data
      text: "Raw content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        layout = result.board.layout
        assert layout.tab_slugs == ["overview", "raw_data"]
        assert layout.tab_variable == "_tab_test"
        assert layout.tab_titles == ["Overview", "Raw Data"]

    def test_tabs_default_first_tab(self):
        """Default tab should be first tab if not specified."""
        yaml_content = """
title: Test
tabs:
  items:
    - title: First
      text: "First content"
    - title: Second
      text: "Second content"
"""
        result = compile(yaml_content)
        assert result.success

        layout = result.board.layout
        assert layout.default_tab == 0

    def test_tabs_default_by_name(self):
        """Default tab can be specified by title."""
        yaml_content = """
title: Test
tabs:
  default: Second
  items:
    - title: First
      text: "First content"
    - title: Second
      text: "Second content"
"""
        result = compile(yaml_content)
        assert result.success

        layout = result.board.layout
        assert layout.default_tab == 1

    def test_tabs_variable_options(self):
        """Tab variable should have slugified titles as options."""
        yaml_content = """
title: Test
tabs:
  id: section
  items:
    - title: Summary View
      text: "Summary"
    - title: Detail View
      text: "Detail"
"""
        result = compile(yaml_content)
        assert result.success

        var = result.board.variable_registry["section"]
        assert var.options.static == ["summary_view", "detail_view"]


class TestDetailsNormalization:
    """Tests for details (collapsible section) normalization."""

    def test_details_generates_hidden_variable(self):
        """Details should auto-generate an invisible checkbox variable."""
        yaml_content = """
title: Test
rows:
  - details: "Show More"
    text: "Hidden content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        board = result.board
        # Should have a hidden details variable in the registry
        # The variable name is based on the item id
        registry = board.variable_registry
        details_vars = {k: v for k, v in registry.items() if "details" in k}
        assert len(details_vars) >= 1
        var = next(iter(details_vars.values()))
        assert var.visible is False
        assert var.input == "checkbox"
        assert var.default is False  # Collapsed by default

    def test_details_expanded_default(self):
        """Details with expanded: true inside the block should default to open."""
        yaml_content = """
title: Test
rows:
  - details:
      summary: "Show More"
      expanded: true
    text: "Visible content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        registry = result.board.variable_registry
        details_vars = {k: v for k, v in registry.items() if "details" in k}
        assert len(details_vars) >= 1
        var = next(iter(details_vars.values()))
        assert var.default is True

    def test_details_metadata_on_layout_item(self):
        """Details metadata should be stored on the LayoutItem."""
        yaml_content = """
title: Test
rows:
  - details:
      summary: "Revenue Breakdown"
      expanded_title: "Hide Breakdown"
    text: "Breakdown content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        layout = result.board.layout
        assert len(layout.items) >= 1
        item = layout.items[0]
        assert item.details_summary == "Revenue Breakdown"
        assert item.details_expanded_summary == "Hide Breakdown"
        assert item.details_variable is not None

    def test_details_with_custom_id(self):
        """Details with explicit id should use it for the variable name."""
        yaml_content = """
title: Test
rows:
  - details: "Show More"
    id: breakdown
    text: "Hidden content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        # The variable name should be "breakdown"
        assert "breakdown" in result.board.variable_registry

    def test_multiple_details_get_unique_variables(self):
        """Multiple details sections should each get a unique variable."""
        yaml_content = """
title: Test
rows:
  - details: "Section A"
    text: "Content A"
  - details: "Section B"
    text: "Content B"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        registry = result.board.variable_registry
        details_vars = {k: v for k, v in registry.items() if "details" in k}
        # Should have 2 distinct details variables
        assert len(details_vars) >= 2


class TestBoardDetailsModel:
    """Tests for the BoardDetails block (finding 2.4)."""

    def test_str_shorthand_still_compiles(self):
        """details: "text" string shorthand must still work after BoardDetails introduction."""
        yaml_content = """
title: Test
rows:
  - details: "Show More"
    text: "Hidden content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"
        item = result.board.layout.items[0]
        assert item.details_summary == "Show More"

    def test_block_form_compiles(self):
        """details: {summary: ..., expanded_title: ..., expanded: ...} block form must compile."""
        yaml_content = """
title: Test
rows:
  - details:
      summary: "Show More"
      expanded_title: "Hide Content"
      expanded: true
    text: "Content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"
        item = result.board.layout.items[0]
        assert item.details_summary == "Show More"
        assert item.details_expanded_summary == "Hide Content"
        # expanded: true → variable default is True
        registry = result.board.variable_registry
        details_vars = {k: v for k, v in registry.items() if "details" in k}
        assert len(details_vars) >= 1
        var = next(iter(details_vars.values()))
        assert var.default is True

    def test_block_form_without_expanded_title_defaults_to_summary(self):
        """details block without expanded_title falls back to summary as expanded label."""
        yaml_content = """
title: Test
rows:
  - details:
      summary: "Show More"
    text: "Content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"
        item = result.board.layout.items[0]
        assert item.details_summary == "Show More"
        assert item.details_expanded_summary == "Show More"

    def test_old_flat_expanded_title_on_inline_board_raises(self):
        """expanded_title at the inline item level (outside details block) must raise."""
        yaml_content = """
title: Test
rows:
  - details: "Show More"
    expanded_title: "Hide"
    text: "Content"
"""
        result = compile(yaml_content)
        assert not result.success
        assert any("expanded_title" in str(e) for e in result.errors)

    def test_old_flat_expanded_on_inline_board_raises(self):
        """expanded at the inline item level (outside details block) must raise."""
        yaml_content = """
title: Test
rows:
  - details: "Show More"
    expanded: true
    text: "Content"
"""
        result = compile(yaml_content)
        assert not result.success
        assert any("expanded" in str(e) for e in result.errors)


class TestStylePatchTextAlignDeletion:
    """Tests for deletion of flat text_align key from styled surfaces (finding 2.6).

    The correct path is style.text.align; flat text_align/text-align keys
    are rejected by StylePatch's extra="forbid".
    """

    def test_flat_text_align_in_style_patch_raises(self):
        """style: {text_align: center} must raise — extra key rejected."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.authored import StylePatch

        with pytest.raises(ValidationError):
            StylePatch.model_validate({"text_align": "center"})

    def test_css_text_align_in_style_patch_raises(self):
        """style: {text-align: center} must raise — extra key rejected."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.authored import StylePatch

        with pytest.raises(ValidationError):
            StylePatch.model_validate({"text-align": "center"})

    def test_text_align_via_text_block_works(self):
        """style: {text: {align: center}} must work through StylePatch."""
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"text": {"align": "center"}})
        assert patch.text is not None
        assert patch.text.align == "center"


class TestTabRendering:
    """Tests for tab SVG rendering with clickable links."""

    def test_tab_bar_has_links(self):
        """Tab bar should render with <a href> links."""
        from unittest.mock import Mock

        from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
        from dbt_charts.core.render.layouts import render_tabs_layout

        mock_executor = Mock()
        mock_executor.execute.return_value = Mock(data=[], columns=[])

        items = tuple(
            ResolvedLayoutItem(
                type="board",
                chart=None,
                board=None,
                x=0.0,
                y=0.0,
                width=400.0,
                height=200.0,
            )
            for _ in range(2)
        )

        svg, _height = render_tabs_layout(
            items=items,
            executor=mock_executor,
            variables={"view": "overview"},
            available_width=800.0,
            available_height=600.0,
            tab_titles=["Overview", "Details"],
            tab_slugs=["overview", "details"],
            tab_variable="view",
            active_tab=0,
            resolved_style=_rs(),
            render_cache={},
            painted_canvas=None,
        )

        # Active tab should NOT be a link (already selected)
        # Inactive tab SHOULD be a link
        assert '<a href="' in svg
        assert "details" in svg  # The slug should appear in href
        assert "Overview" in svg
        assert "Details" in svg

    def test_active_tab_from_variable(self):
        """Active tab should be determined by variable value."""
        from unittest.mock import Mock

        from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
        from dbt_charts.core.render.layouts import render_tabs_layout

        mock_executor = Mock()

        items = (
            ResolvedLayoutItem(
                type="board",
                chart=None,
                board=None,
                x=0.0,
                y=0.0,
                width=400.0,
                height=200.0,
            ),
            ResolvedLayoutItem(
                type="board",
                chart=None,
                board=None,
                x=0.0,
                y=0.0,
                width=400.0,
                height=200.0,
            ),
        )

        # Pass view=details in variables — should activate second tab
        svg, _height = render_tabs_layout(
            items=items,
            executor=mock_executor,
            variables={"view": "details"},
            available_width=800.0,
            available_height=600.0,
            tab_titles=["Overview", "Details"],
            tab_slugs=["overview", "details"],
            tab_variable="view",
            active_tab=0,
            resolved_style=_rs(),
            render_cache={},
            painted_canvas=None,
        )

        # "overview" should appear as a link (inactive)
        # "Details" tab should be active (not a link)
        assert "overview" in svg

    def test_tab_bar_border_dash_array_emits_svg_dasharray(self):
        """tabs.border.dash_array reaches the tab-bar rect's stroke attributes."""
        import dataclasses
        from unittest.mock import Mock

        from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
        from dbt_charts.core.render.layouts import render_tabs_layout

        rs = _rs()
        dashed_border = rs.layout.tabs.border.model_copy(
            update={"dash_array": [4, 4], "line_cap": "round"}
        )
        dashed_tabs = rs.layout.tabs.model_copy(update={"border": dashed_border})
        dashed_layout = rs.layout.model_copy(update={"tabs": dashed_tabs})
        rs = dataclasses.replace(rs, layout=dashed_layout)

        mock_executor = Mock()
        mock_executor.execute.return_value = Mock(data=[], columns=[])

        items = tuple(
            ResolvedLayoutItem(
                type="board",
                chart=None,
                board=None,
                x=0.0,
                y=0.0,
                width=400.0,
                height=200.0,
            )
            for _ in range(2)
        )

        svg, _height = render_tabs_layout(
            items=items,
            executor=mock_executor,
            variables={"view": "overview"},
            available_width=800.0,
            available_height=600.0,
            tab_titles=["Overview", "Details"],
            tab_slugs=["overview", "details"],
            tab_variable="view",
            active_tab=0,
            resolved_style=rs,
            render_cache={},
            painted_canvas=None,
        )

        assert 'stroke-dasharray="4,4"' in svg
        assert 'stroke-linecap="round"' in svg


class TestTabInteractiveScript:
    """The canonical SVG embeds variables.js for tab navigation."""

    def test_variables_script_embedded_for_tabs_without_variable_controls(self):
        """Tab-only board (no variables: section) must still embed variables.js.

        Variables.js intercepts SVG <a href="?..."> clicks and posts them to
        the parent playground iframe.  Without it, tab clicks do nothing in
        blob-URL iframes.
        """
        yaml_content = """
title: Tab Test
tabs:
  items:
    - title: "Alpha"
      text: "Content A"
    - title: "Beta"
      text: "Content B"
"""
        from unittest.mock import Mock

        from dbt_charts.core.compile import compile
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )
        from dbt_charts.core.render.boards import render_board_svg
        from dbt_charts.core.render.converters import to_html

        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        mock_executor = Mock()
        mock_executor.execute.return_value = Mock(data=[], columns=[])
        # No cache in play here — the real Executor.cache_hit_ats contract is
        # an empty list until a persistent-cache hit occurs, which never
        # happens against this mock's adapter stand-in.
        mock_executor.cache_hit_ats = []

        reset_config()
        resolved = resolve_board(result.board)
        svg = render_board_svg(
            resolved,
            mock_executor,
            {},
            background=None,
            render_cache={},
        )

        # The artifact stays script-free apart from the chart hover runtime...
        assert "__dfVariablesInitialized" not in svg

        # ...but a live host still ships the controls runtime for a board with
        # no variables at all: it also intercepts the <a href="?..."> clicks a
        # tabbed board renders.
        page = to_html(svg, controls=True)
        assert "__dfVariablesInitialized" in page


class TestLayoutNotes:
    """Tests for layout-level notes metadata."""

    def test_layout_item_notes_from_chart_wrapper(self):
        """rows/cols chart wrappers should preserve notes on LayoutItem."""
        yaml_content = """
title: Test
queries:
  q:
    type: values
    rows:
      - {value: 1}
charts:
  c:
    query: q
    type: table
rows:
  - notes: "Primary table used for quick QA checks"
    rows:
      - c
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        item = result.board.layout.items[0]
        assert item.notes == "Primary table used for quick QA checks"

    def test_layout_item_notes_from_grid_item(self):
        """grid.items[].notes should be copied to LayoutItem."""
        yaml_content = """
title: Test
queries:
  q:
    type: values
    rows:
      - {value: 1}
charts:
  c:
    query: q
    type: table
grid:
  columns: 24
  items:
    - item: c
      notes: "Grid cell for executive summary table"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        item = result.board.layout.items[0]
        assert item.notes == "Grid cell for executive summary table"

    def test_layout_item_notes_from_tab_item(self):
        """tabs.items[].notes should be copied to LayoutItem and tab board."""
        yaml_content = """
title: Test
tabs:
  items:
    - title: Overview
      notes: "High-level KPI overview"
      text: "Hello"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        item = result.board.layout.items[0]
        assert item.notes == "High-level KPI overview"
        assert item.board.notes == "High-level KPI overview"


class TestDetailsRendering:
    """Tests for details summary bar SVG rendering."""

    def test_collapsed_summary(self):
        """Collapsed details should show disclosure triangle and summary text."""
        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.render.layouts import render_details_summary

        item = LayoutItem(
            type="board",
            board=None,
            details_variable="breakdown",
            details_summary="Revenue Breakdown",
            details_expanded_summary="Hide Breakdown",
        )

        svg = render_details_summary(
            item=item,
            variables={"breakdown": "false"},
            available_width=800.0,
            resolved_style=_rs(),
        )

        assert "▶" in svg  # Collapsed triangle
        assert "Revenue Breakdown" in svg
        assert "true" in svg  # Link should toggle to expanded
        assert '<a href="' in svg

    def test_expanded_summary(self):
        """Expanded details should show different triangle and expanded_title."""
        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.render.layouts import render_details_summary

        item = LayoutItem(
            type="board",
            board=None,
            details_variable="breakdown",
            details_summary="Revenue Breakdown",
            details_expanded_summary="Hide Breakdown",
        )

        svg = render_details_summary(
            item=item,
            variables={"breakdown": "true"},
            available_width=800.0,
            resolved_style=_rs(),
        )

        assert "▼" in svg  # Expanded triangle
        assert "Hide Breakdown" in svg
        assert "false" in svg  # Link should toggle to collapsed

    def test_summary_without_variable_raises(self):
        """Rendering a details summary without a backing variable is invalid."""
        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.render.layouts import render_details_summary

        item = LayoutItem(
            type="board",
            board=None,
            details_summary="Revenue Breakdown",
            details_expanded_summary="Hide Breakdown",
        )

        with pytest.raises(
            ValueError,
            match="details_variable",
        ):
            render_details_summary(
                item=item,
                variables={},
                available_width=800.0,
                resolved_style=_rs(),
            )

    def test_details_border_dash_array_emits_svg_dasharray(self):
        """details.border.dash_array reaches the summary bar's stroke attributes."""
        import dataclasses

        from dbt_charts.core.compile.models.board.normalized import LayoutItem
        from dbt_charts.core.render.layouts import render_details_summary

        rs = _rs()
        dashed_border = rs.layout.details.border.model_copy(
            update={"dash_array": [4, 4], "line_cap": "round"}
        )
        dashed_details = rs.layout.details.model_copy(update={"border": dashed_border})
        dashed_layout = rs.layout.model_copy(update={"details": dashed_details})
        rs = dataclasses.replace(rs, layout=dashed_layout)

        item = LayoutItem(
            type="board",
            board=None,
            details_variable="breakdown",
            details_summary="Revenue Breakdown",
            details_expanded_summary="Hide Breakdown",
        )

        svg = render_details_summary(
            item=item,
            variables={"breakdown": "false"},
            available_width=800.0,
            resolved_style=rs,
        )

        assert 'stroke-dasharray="4,4"' in svg
        assert 'stroke-linecap="round"' in svg


class TestTabSlugCollisions:
    """Tests for tab slug collision detection."""

    def test_duplicate_slugs_raise_error(self):
        """Tabs with titles that produce the same slug should fail."""
        yaml_content = """
title: Test
tabs:
  items:
    - title: "My Tab"
      text: "Content 1"
    - title: "my tab"
      text: "Content 2"
"""
        result = compile(yaml_content)
        assert not result.success
        assert any("Duplicate tab slug" in str(e) for e in result.errors)

    def test_empty_tab_title_raises_error(self):
        """Tabs with empty titles should fail (produces empty slug)."""
        yaml_content = """
title: Test
tabs:
  items:
    - title: ""
      text: "Content"
    - title: "Valid"
      text: "Content"
"""
        result = compile(yaml_content)
        assert not result.success
        assert any("empty slug" in str(e) for e in result.errors)


class TestAutoGeneratedVariablePrefix:
    """Tests that auto-generated variables use _ prefix to avoid collisions."""

    def test_auto_tab_variable_has_prefix(self):
        """Auto-generated tab variable should have _ prefix."""
        yaml_content = """
title: Test
tabs:
  items:
    - title: Overview
      text: "Content"
"""
        result = compile(yaml_content)
        assert result.success

        # _tab_{board_id}, not bare "tab"
        assert "_tab_test" in result.board.variable_registry
        assert "tab" not in result.board.variable_registry

    def test_custom_id_no_prefix(self):
        """User-provided tab id should NOT get _ prefix."""
        yaml_content = """
title: Test
tabs:
  id: view
  items:
    - title: Overview
      text: "Content"
"""
        result = compile(yaml_content)
        assert result.success

        assert "view" in result.board.variable_registry
        assert "_view" not in result.board.variable_registry

    def test_auto_details_variable_has_prefix(self):
        """Auto-generated details variable should have _ prefix."""
        yaml_content = """
title: Test
rows:
  - details: "Show More"
    text: "Hidden"
"""
        result = compile(yaml_content)
        assert result.success

        registry = result.board.variable_registry
        # All auto-generated details vars should start with _
        details_vars = [k for k in registry if "details" in k]
        assert all(k.startswith("_") for k in details_vars)


class TestNestedTabsNoCollision:
    """Tests that multiple tabs layouts in nested boards don't cause variable collisions."""

    def test_tabs_inside_rows_with_sibling_tabs(self):
        """Two tabs layouts in sibling rows should each get their own _tab variable."""
        yaml_content = """
title: Test
rows:
  - title: "Section 1"
    tabs:
      items:
        - title: Overview
          text: "Section 1 overview"
        - title: Details
          text: "Section 1 details"
  - title: "Section 2"
    tabs:
      items:
        - title: Tab A
          text: "Section 2 tab A"
        - title: Tab B
          text: "Section 2 tab B"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        # Both sections should have their own _tab variable without collision
        registry = result.board.variable_registry
        tab_vars = [k for k in registry if "tab" in k.lower()]
        assert len(tab_vars) >= 2, f"Expected 2+ tab variables, got {tab_vars}"

    def test_tabs_nested_inside_tab(self):
        """Tabs inside a tab item (deep nesting) should compile without collision."""
        yaml_content = """
title: Test
tabs:
  items:
    - title: Outer A
      tabs:
        items:
          - title: Inner 1
            text: "Nested tab content"
          - title: Inner 2
            text: "Nested tab content"
    - title: Outer B
      text: "Simple content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

    def test_imported_board_with_tabs_no_collision(self):
        """A board containing tabs nested inside another board with tabs should compile."""
        yaml_content = """
title: Outer Dashboard
rows:
  - title: "Main Tabs"
    tabs:
      items:
        - title: Overview
          text: "Overview content"
        - title: Metrics
          text: "Metrics content"
  - title: "Nested Dashboard"
    rows:
      - title: "Inner Tabs"
        tabs:
          items:
            - title: Chart View
              text: "Chart content"
            - title: Table View
              text: "Table content"
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"

        # The variable registry should have separate tab variables
        registry = result.board.variable_registry
        tab_vars = [k for k in registry if "tab" in k.lower()]
        assert len(tab_vars) >= 2, f"Expected 2+ tab variables, got {tab_vars}"


class TestExpandedDetailsSectionBorder:
    """render/chart/rendering.py's expanded-details section_border rect."""

    def test_expanded_details_section_border_dash_array_emits_svg_dasharray(
        self, local_project
    ):
        """style.layout.details.border.dash_array reaches the expanded section's
        wrapping border rect (render_chart_item's section_border, distinct from
        render_details_summary's own summary-bar border)."""
        from pathlib import Path

        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render.renderer import render

        yaml_content = """
title: Test
style:
  layout:
    details:
      border:
        width: 2
        color: "#333333"
        radius: 0
        dash_array: [4, 4]
        line_cap: round
queries:
  q:
    type: values
    rows:
      - {a: 1}
charts:
  tbl:
    query: q
    type: table
rows:
  - details:
      summary: "Details"
      expanded: true
    rows:
      - tbl
"""
        result = compile(yaml_content)
        assert result.success, f"Compile failed: {result.errors}"
        board = result.board
        executor = Executor(
            board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        svg = render(board, executor, format="svg").output
        assert isinstance(svg, str)
        assert 'stroke-dasharray="4,4"' in svg
        assert 'stroke-linecap="round"' in svg


class TestDetailsExpandedSizing:
    """Regression tests for expanded details sizing drift.

    Bug: _calculate_nested_board_layout used item.height without subtracting
    the details chrome (summary_height + gap + card_gap), inflating the nested
    board's layout.content_height by exactly that amount.
    """

    _YAML = """
title: Test
queries:
  q:
    type: values
    rows:
      - {a: 1}
charts:
  tbl:
    query: q
    type: table
rows:
  - details:
      summary: "Details"
      expanded: true
    rows:
      - tbl
"""

    def test_expanded_details_nested_content_height_excludes_chrome(self):
        """Nested board layout.content_height must not include the details chrome.

        Before fix: content_height == item.height (inflated by summary_height + gap + card_gap).
        After fix:  content_height == item.height - chrome.
        """
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.compile.sizing import get_board_gap

        result = compile(self._YAML)
        assert result.success, f"Compile failed: {result.errors}"
        reset_config()
        board = apply_static_layout(result.board)

        details_item = board.layout.items[0]
        assert details_item.details_variable, "Expected a details item"

        summary_height = float(get_theme_style().layout.details.summary_height)
        gap = get_board_gap(board)
        card_gap = float(get_theme_style().frame.card_gap) if board.card_gap else 0.0
        chrome = summary_height + gap + card_gap

        expected_content_height = details_item.height - chrome
        assert details_item.board.layout.content_height == pytest.approx(
            expected_content_height, abs=1.0
        ), (
            f"Nested board content_height {details_item.board.layout.content_height} "
            f"should be item.height ({details_item.height}) - chrome ({chrome}) "
            f"= {expected_content_height}"
        )

    def test_collapsed_details_item_height_is_summary_height_only(self):
        """Collapsed details items must not include chrome or content — just summary bar."""
        from dbt_charts.core.compile.config import reset_config

        yaml_collapsed = """
title: Test
queries:
  q:
    type: values
    rows:
      - {a: 1}
charts:
  tbl:
    query: q
    type: table
rows:
  - details:
      summary: "Details"
      expanded: false
    rows:
      - tbl
"""
        result = compile(yaml_collapsed)
        assert result.success
        reset_config()
        board = apply_static_layout(result.board)

        details_item = board.layout.items[0]
        assert details_item.details_variable, "Expected a details item"

        summary_height = float(get_theme_style().layout.details.summary_height)
        assert details_item.height == pytest.approx(summary_height, abs=1.0), (
            f"Collapsed details item height {details_item.height} should equal "
            f"summary_height {summary_height}"
        )


class TestActiveTabSizing:
    """Regression: a tabbed board sizes to the active tab, not the union.

    The renderer already knows which tab is active — sizing must measure
    the same subtree, resolved the same way render_tabs_layout resolves it
    (runtime variable override > authored default > first tab).
    """

    _MANY_CHARTS_YAML = """
title: Test
queries:
  q:
    type: values
    rows:
      - {a: 1}
charts:
  c1: {query: q, type: table}
  c2: {query: q, type: table}
  c3: {query: q, type: table}
  c4: {query: q, type: table}
  c5: {query: q, type: table}
  c6: {query: q, type: table}
  c7: {query: q, type: table}
  c8: {query: q, type: table}
  c9: {query: q, type: table}
tabs:
  id: t
  default: One
  items:
    - title: One
      rows: [{cols: [c1]}]
    - title: Nine
      rows:
        - {cols: [c2, c3, c4]}
        - {cols: [c5, c6, c7]}
        - {cols: [c8, c9, c1]}
"""

    def _build_executor(self, yaml_content: str, local_project):
        return _compile_and_build_executor(yaml_content, local_project)

    def _render_height(self, board, executor, variables=None) -> float:
        from dbt_charts.core.render.renderer import render
        from dbt_charts.core.render.svg_utils import extract_svg_dimensions

        result = render(board, executor, format="svg", variables=variables)
        assert not result.warnings, f"Unexpected warnings: {result.warnings}"
        svg = result.output
        assert isinstance(svg, str)
        return extract_svg_dimensions(svg).height

    def test_default_and_overridden_tab_size_independently(self, local_project):
        """Regression pin: one chart vs nine, across a runtime tab override.

        Before the fix both tabs rendered at the SAME (union) height. The
        default tab must now be shorter, and switching the active tab via a
        runtime variable override (a URL param, in production) must size the
        board for the NEWLY active tab — not whichever was the compiled
        default.
        """
        board, executor = self._build_executor(self._MANY_CHARTS_YAML, local_project)

        one_height = self._render_height(board, executor)
        nine_height = self._render_height(board, executor, variables={"t": "nine"})

        assert one_height < nine_height, (
            f"one-chart tab ({one_height}px) should be shorter than the "
            f"nine-chart tab ({nine_height}px) — sizing must follow the "
            f"active tab, not the union of every tab"
        )
        assert one_height < 400.0, f"one-chart tab reserved {one_height}px"

    def test_single_tab_sizing(self, local_project):
        """A tabs layout with exactly one tab must still size correctly."""
        yaml_content = """
title: Test
queries:
  q:
    type: values
    rows:
      - {a: 1}
charts:
  c1: {query: q, type: table}
tabs:
  items:
    - title: Only
      rows: [{cols: [c1]}]
"""
        board, executor = self._build_executor(yaml_content, local_project)

        height = self._render_height(board, executor)
        assert height > 0

    def test_many_tabs_sizes_to_active_only(self, local_project):
        """With several differently-sized tabs, only the active one counts."""
        yaml_content = """
title: Test
queries:
  q:
    type: values
    rows:
      - {a: 1}
charts:
  c1: {query: q, type: table}
  c2: {query: q, type: table}
  c3: {query: q, type: table}
  c4: {query: q, type: table}
  c5: {query: q, type: table}
  c6: {query: q, type: table}
  c7: {query: q, type: table}
  c8: {query: q, type: table}
  c9: {query: q, type: table}
tabs:
  id: t
  default: Small
  items:
    - title: Empty
      text: "nothing here"
    - title: Small
      rows: [{cols: [c1]}]
    - title: Medium
      rows: [{cols: [c1, c2]}]
    - title: Large
      rows:
        - {cols: [c1, c2, c3]}
        - {cols: [c4, c5, c6]}
        - {cols: [c7, c8, c9]}
"""
        board, executor = self._build_executor(yaml_content, local_project)

        empty_height = self._render_height(board, executor, variables={"t": "empty"})
        small_height = self._render_height(board, executor)  # compiled default
        large_height = self._render_height(board, executor, variables={"t": "large"})

        assert empty_height < small_height < large_height, (
            f"empty={empty_height}px, small={small_height}px, "
            f"large={large_height}px — each active tab must size to its own "
            f"content"
        )


class TestTabsSizingSkipsInactiveTabsCharts:
    """Regression: sizing must not resolve/render charts in inactive tabs.

    #6963 fixed the reserved *height* (a tabbed board sizes to the active
    tab, not the union). But the recursive nested-board-sizing walk in
    ``calculate_layout_items`` (render/sizing.py) still descends into every
    tab's subtree unconditionally, so resolving a chart — and, for
    aspect-ratio chart types, rendering it via vl-convert for render-first
    height measurement — still happens once per chart in the WHOLE board,
    not once per chart in the active tab. Switching to a near-empty tab
    should cost O(that tab's own charts), not O(every chart in the board).

    Asserts on the set of chart ids the sizing pass actually resolved
    (``resolved_variants``, an out-param of ``calculate_data_aware_layout``)
    rather than wall-clock time, so the test cannot be flaky.
    """

    def _build(self, n_inactive_charts: int, local_project):
        inactive_charts = "\n".join(
            f"  c{i}:\n    query: q\n    type: bar\n    x: month\n    y: revenue"
            for i in range(n_inactive_charts)
        )
        inactive_refs = ", ".join(f"c{i}" for i in range(n_inactive_charts))
        yaml_content = f"""
title: Test
queries:
  q:
    type: values
    rows:
      - {{month: Jan, revenue: 100}}
charts:
  active_chart:
    query: q
    type: bar
    x: month
    y: revenue
{inactive_charts}
tabs:
  id: t
  default: Active
  items:
    - title: Active
      rows: [{{cols: [active_chart]}}]
    - title: ManyCharts
      rows: [{{cols: [{inactive_refs}]}}]
"""
        return _compile_and_build_executor(yaml_content, local_project)

    def test_sizing_resolves_only_active_tab_charts(self, local_project):
        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        board, executor = self._build(n_inactive_charts=40, local_project=local_project)
        resolved_variants: dict = {}
        calculate_data_aware_layout(
            board, executor, resolved_variants=resolved_variants
        )
        resolved_chart_ids = {key[0] for key in resolved_variants}

        assert resolved_chart_ids == {"active_chart"}, (
            f"sizing resolved {sorted(resolved_chart_ids)} — inactive tab's "
            "40 charts must not be processed while the Active tab is showing"
        )

    def test_resolved_chart_count_is_independent_of_board_size(self, local_project):
        from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

        counts = []
        for n in (0, 3, 40):
            board, executor = self._build(
                n_inactive_charts=n, local_project=local_project
            )
            resolved_variants: dict = {}
            calculate_data_aware_layout(
                board, executor, resolved_variants=resolved_variants
            )
            counts.append(len({key[0] for key in resolved_variants}))

        assert counts == [1, 1, 1], (
            f"resolved-chart count per board size was {counts} — a zero-chart "
            "or 40-chart inactive tab must cost the active tab's single chart, "
            "not grow with the total chart count in the board"
        )


class TestInactiveTabDataAwareResolution:
    """Regression: an inactive tab's charts must resolve against real data.

    The sizing pass (``calculate_data_aware_layout``) is scoped to the active
    tab only — that is a real, kept win, since it skips the expensive
    render-first vl-convert height measurement for tabs nobody is looking at.
    But ``_build_resolved_layout`` (board_resolve.py) still walks every layout
    item, because ``ResolvedBoard`` publishes one chart catalog for the whole
    board: when a chart isn't in the sizing pass's ``resolved_variants``
    cache, it falls back to ``_resolve_chart_data_aware`` — real,
    already-cached query rows, but no vl-convert. Each test below exercises
    a real consumer of ``ResolvedBoard`` that a data-free resolve used to
    break.
    """

    def _build(self, yaml_content: str, local_project):
        return _compile_and_build_executor(yaml_content, local_project)

    def test_inactive_tab_pie_survives_artifact_replay(self, local_project):
        """CRITICAL regression: artifact emit -> replay with a pie in an
        inactive tab.

        A data-free resolve bakes ``presentation_fingerprint = sha256("[]")``,
        which ``record_board``'s real query rows then contradict —
        ``board_replay`` raised ``ERR-RESOLVED-PIE-DATA-MISMATCH`` for the
        WHOLE board: ``dct artifact emit`` reported success, but
        ``dct artifact render`` produced no SVG at all.
        """
        from dbt_charts.core.execute.recording import record_board
        from dbt_charts.core.render.board_replay import render_board_from_artifact
        from dbt_charts.core.render.board_resolve import build_resolved_board

        yaml_content = """
title: Test
queries:
  shares_q:
    type: values
    rows:
      - {category: A, value: 40}
      - {category: B, value: 35}
      - {category: C, value: 25}
charts:
  active_chart:
    query: shares_q
    type: bar
    x: category
    y: value
  shares:
    query: shares_q
    type: pie
    theta: value
    color: category
tabs:
  id: t
  default: Active
  items:
    - title: Active
      rows: [{cols: [active_chart]}]
    - title: Hidden
      rows: [{cols: [shares]}]
"""
        board, executor = self._build(yaml_content, local_project)
        resolved, _ = build_resolved_board(board, executor, {})
        recording = record_board(resolved, executor, {})

        # Must not raise ERR-RESOLVED-PIE-DATA-MISMATCH: before the fix, the
        # inactive tab's pie was resolved against [] while record_board
        # recorded the query's real rows.
        svg = render_board_from_artifact(resolved, recording, {})
        assert svg

    def test_inactive_tab_pie_honors_authored_width(self, local_project):
        """HIGH regression: an inactive tab's pie falls back to
        ``_resolve_chart_data_aware`` (the sizing pass never measured it),
        whose unmeasured ``item.width`` of ``0.0`` must not be substituted
        for a board-wide default that discards an authored ``width:``.
        Pie is the one family whose ``preferred_chart_width`` honors an
        authored width first — a fallback that skips that branch silently
        overrides it with the theme default instead.
        """
        from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
        from dbt_charts.core.render.board_resolve import build_resolved_board

        yaml_content = """
title: Test
queries:
  shares_q:
    type: values
    rows:
      - {category: A, value: 40}
      - {category: B, value: 35}
      - {category: C, value: 25}
charts:
  active_chart:
    query: shares_q
    type: bar
    x: category
    y: value
  shares:
    query: shares_q
    type: pie
    theta: value
    color: category
    width: 300
tabs:
  id: t
  default: Active
  items:
    - title: Active
      rows: [{cols: [active_chart]}]
    - title: Hidden
      rows: [{cols: [shares]}]
"""
        board, executor = self._build(yaml_content, local_project)
        resolved, _ = build_resolved_board(board, executor, {})

        shares_resolved = resolved.charts["shares"]
        assert isinstance(shares_resolved, ResolvedPieChart)
        assert shares_resolved.resolution_width == 300.0, (
            "an inactive tab's authored pie width must survive the "
            f"data-aware fallback, got {shares_resolved.resolution_width}"
        )

    def test_inactive_tab_wide_chart_does_not_warn_at_width_zero(self, local_project):
        """HIGH regression: an inactive tab's chart must not be judged by
        warning detectors at width 0 — it is never laid out (no vl-convert
        measurement for a tab nobody is looking at), so its layout width
        stays 0.0. Uses ``bar``, not a ``NON_ASPECT_RATIO_TYPES`` family —
        table/kpi/spark_bar/callout skip the vega_spec warning path
        entirely, which would make this test pass for the wrong reason.

        Positive control: ``active_chart`` also has a placement in the
        Hidden tab (after ``wide_chart``), so the width map's tree walk
        overwrites its real active-tab width with the inactive placement's
        0.0 last — reproducing the last-write-wins/first-write-wins
        mismatch between ``_collect_layout_chart_widths`` and
        ``board.charts``. Its zero-row query must still warn despite that.
        An absence-only assertion on ``wide_chart`` alone would also pass if
        the width guard over-fired and silently dropped every detector for
        the active tab too — exactly the regression this pins.
        """
        from dbt_charts.core.diagnostics import WARN_QUERY_RETURNED_ZERO_ROWS
        from dbt_charts.core.render.renderer import render

        categories = "\n".join(f"      - {{cat: c{i}, value: {i}}}" for i in range(60))
        yaml_content = f"""
title: Test
queries:
  empty_q:
    type: values
    rows: []
  wide_q:
    type: values
    rows:
{categories}
charts:
  active_chart:
    query: empty_q
    type: bar
    x: cat
    y: value
  wide_chart:
    query: wide_q
    type: bar
    x: cat
    y: value
    title: "A very long axis title that will not fit at a narrow width whatsoever"
tabs:
  id: t
  default: Active
  items:
    - title: Active
      rows: [{{cols: [active_chart]}}]
    - title: Hidden
      rows: [{{cols: [wide_chart, active_chart]}}]
"""
        board, executor = self._build(yaml_content, local_project)
        result = render(board, executor, format="json")
        warning_keys = {(w.chart, w.code) for w in result.warnings}

        assert ("active_chart", WARN_QUERY_RETURNED_ZERO_ROWS.code) in warning_keys, (
            "positive control: active_chart's zero-row query must still warn "
            f"— the width guard silenced the active tab too: {result.warnings}"
        )
        assert not any(chart == "wide_chart" for chart, _ in warning_keys), (
            f"inactive tab's wide_chart must not warn at width 0: {result.warnings}"
        )

    def test_inactive_tab_broken_chart_recorded_in_resolve_errors(self, local_project):
        """HIGH regression: a broken chart in an inactive tab must not be
        silently swallowed. ``agent_api.board_artifact`` gates artifact
        publication on ``resolve_errors`` being empty ("Artifacts stay
        all-or-nothing") — before the fix, an inactive tab's failure
        recorded nothing there.
        """
        from dbt_charts.core.compile.models.board.resolved import ChartResolveFailure
        from dbt_charts.core.render.board_resolve import build_resolved_board

        yaml_content = """
title: Test
queries:
  ok_q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
  bad_q:
    type: values
    rows:
      - {month: Jan, revenue: hello}
charts:
  active_chart:
    query: ok_q
    type: bar
    x: month
    y: revenue
  bad:
    query: bad_q
    type: bar
    x: month
    y: revenue
tabs:
  id: t
  default: Active
  items:
    - title: Active
      rows: [{cols: [active_chart]}]
    - title: Hidden
      rows: [{cols: [bad]}]
"""
        board, executor = self._build(yaml_content, local_project)
        resolve_errors: dict[str, ChartResolveFailure] = {}
        resolved, _ = build_resolved_board(
            board, executor, {}, resolve_errors=resolve_errors
        )

        assert "bad" in resolve_errors, (
            "a broken chart in an inactive tab must be recorded in resolve_errors"
        )
        assert "bad" not in resolved.charts

    def test_chart_shared_by_inactive_and_active_tab_resolves_data_aware(
        self, local_project
    ):
        """HIGH regression: ``_collect_resolved_layout_charts`` uses
        ``setdefault``, so tree order decides which placement's resolution
        wins when one chart appears in both an inactive and the active
        tab — that placement must always be data-aware, never the hollow,
        data-free static resolution a data-aware/data-free split used to
        produce.

        ``orientation`` alone can't tell a fully data-aware resolve apart
        from a rows-only one (it's inferred from rows, the one input a
        rows-only resolve does supply) — the title's ``{{ region }}``
        template is only interpolated if the runtime ``variables`` input
        also reaches this placement, exactly like the active tab's does.
        """
        from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
        from dbt_charts.core.render.board_resolve import build_resolved_board

        yaml_content = """
title: Test
variables:
  region:
    default: West
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 200}
charts:
  shared:
    query: q
    type: bar
    x: month
    y: revenue
    title: "Rev {{ region }}"
  spacer:
    query: q
    type: bar
    x: month
    y: revenue
tabs:
  id: t
  default: Active
  items:
    - title: Inactive
      rows: [{cols: [shared]}]
    - title: Active
      rows: [{cols: [shared, spacer]}]
"""
        board, executor = self._build(yaml_content, local_project)
        resolved, _ = build_resolved_board(board, executor, {"region": "West"})

        shared_resolved = resolved.charts["shared"]
        assert isinstance(shared_resolved, ResolvedBarChart)
        assert shared_resolved.orientation is not None, (
            "the published 'shared' resolution must be data-aware (real "
            "orientation) — a data-free resolve never infers one"
        )
        assert shared_resolved.title == "Rev West", (
            "the inactive placement must interpolate runtime variables "
            f"like the active path does, got {shared_resolved.title!r}"
        )


class TestDetectorKindGate:
    """Regression: the width guard must not silence a chart's DATA detectors
    just because it has no GEOMETRY (unmeasured — an inactive tab or a
    collapsed ``details:`` section). WARN-QUERY-RETURNED-ZERO-ROWS is data —
    the query ran and returned nothing regardless of whether the chart is
    on-screen; WARN-CHART-TITLE-TRUNCATED and WARN-BAR-BAND-WIDTH-TOO-NARROW
    are geometry — they judge a rendered pixel width that never existed for
    an unmeasured chart. See ``render/warnings/registry.py``'s module
    docstring for the classification.
    """

    def _build(self, yaml_content, local_project):
        return _compile_and_build_executor(yaml_content, local_project)

    def test_collapsed_details_chart_keeps_data_loses_geometry(self, local_project):
        """A collapsed `details:` chart is present but never laid out — its
        DATA detector must still fire; its GEOMETRY detector must not."""
        from dbt_charts.core.diagnostics import (
            WARN_CHART_TITLE_TRUNCATED,
            WARN_QUERY_RETURNED_ZERO_ROWS,
        )
        from dbt_charts.core.render.renderer import render

        yaml_content = """
title: Test
queries:
  empty_q:
    type: values
    rows: []
charts:
  collapsed_chart:
    query: empty_q
    type: bar
    x: cat
    y: value
    title: "A very long chart title that would truncate if this chart were ever actually laid out and measured at any real width"
rows:
  - details:
      summary: "Details"
      expanded: false
    rows:
      - collapsed_chart
"""
        board, executor = self._build(yaml_content, local_project)
        result = render(board, executor, format="json")
        warning_keys = {(w.chart, w.code) for w in result.warnings}

        assert (
            "collapsed_chart",
            WARN_QUERY_RETURNED_ZERO_ROWS.code,
        ) in warning_keys, (
            "a collapsed details chart's zero-row query is a real authoring "
            f"defect regardless of visibility: {result.warnings}"
        )
        assert not any(
            chart == "collapsed_chart" and code == WARN_CHART_TITLE_TRUNCATED.code
            for chart, code in warning_keys
        ), (
            "a collapsed details chart was never laid out at any width — "
            f"title truncation must not fire: {result.warnings}"
        )

    def test_inactive_tab_chart_keeps_data_loses_geometry(self, local_project):
        """An inactive-tab-only chart's DATA detector must still fire; its
        GEOMETRY detector must not — mirrors the collapsed-details case
        above for the other unmeasured shape."""
        from dbt_charts.core.diagnostics import (
            WARN_CHART_TITLE_TRUNCATED,
            WARN_QUERY_RETURNED_ZERO_ROWS,
        )
        from dbt_charts.core.render.renderer import render

        yaml_content = """
title: Test
queries:
  ok_q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
  empty_q:
    type: values
    rows: []
charts:
  active_chart:
    query: ok_q
    type: bar
    x: month
    y: revenue
  hidden_chart:
    query: empty_q
    type: bar
    x: month
    y: revenue
    title: "A very long chart title that would truncate if this chart were ever actually laid out and measured at any real width"
tabs:
  id: t
  default: Active
  items:
    - title: Active
      rows: [{cols: [active_chart]}]
    - title: Hidden
      rows: [{cols: [hidden_chart]}]
"""
        board, executor = self._build(yaml_content, local_project)
        result = render(board, executor, format="json")
        warning_keys = {(w.chart, w.code) for w in result.warnings}

        assert (
            "hidden_chart",
            WARN_QUERY_RETURNED_ZERO_ROWS.code,
        ) in warning_keys, (
            "an inactive tab's zero-row query is a real authoring defect "
            f"regardless of visibility: {result.warnings}"
        )
        assert not any(
            chart == "hidden_chart" and code == WARN_CHART_TITLE_TRUNCATED.code
            for chart, code in warning_keys
        ), (
            f"an inactive tab's chart was never laid out — title truncation "
            f"must not fire: {result.warnings}"
        )

    def test_chart_shared_by_active_and_inactive_tab_keeps_both_kinds(
        self, local_project
    ):
        """A chart placed in both the active tab (narrow — band width below
        the readability floor) and an inactive tab (full width — comfortably
        above it) must: keep its DATA detector (a sibling's zero-row query,
        proving the width gate isn't over-silencing the whole render), and
        fire its own GEOMETRY detector judged at the ACTIVE width, not the
        wide inactive placement's."""
        from dbt_charts.core.diagnostics import (
            WARN_BAR_BAND_WIDTH_TOO_NARROW,
            WARN_QUERY_RETURNED_ZERO_ROWS,
        )
        from dbt_charts.core.render.renderer import render

        categories = "\n".join(f"      - {{cat: c{i}, value: {i}}}" for i in range(200))
        yaml_content = f"""
title: Test
queries:
  empty_q:
    type: values
    rows: []
  many_q:
    type: values
    rows:
{categories}
charts:
  active_chart:
    query: empty_q
    type: bar
    x: cat
    y: value
  shared:
    query: many_q
    type: bar
    x: cat
    y: value
    style:
      orientation: vertical
tabs:
  id: t
  default: Active
  items:
    - title: Active
      rows: [{{cols: [active_chart, shared]}}]
    - title: Hidden
      rows: [shared]
"""
        board, executor = self._build(yaml_content, local_project)
        result = render(board, executor, format="json")
        warning_keys = {(w.chart, w.code) for w in result.warnings}

        assert (
            "active_chart",
            WARN_QUERY_RETURNED_ZERO_ROWS.code,
        ) in warning_keys, (
            "active_chart's zero-row query must still warn — the width "
            f"guard must not over-fire on the active tab: {result.warnings}"
        )
        assert (
            "shared",
            WARN_BAR_BAND_WIDTH_TOO_NARROW.code,
        ) in warning_keys, (
            "shared's 200 categories at its narrow active-tab column width "
            "must warn — judged against the wide inactive placement "
            f"instead, it would not: {result.warnings}"
        )

    def test_pie_placed_twice_at_different_widths_keeps_title_truncation(
        self, local_project
    ):
        """A pie chart placed in both an inactive tab (full width) and the
        active tab (a narrow two-column split) must have its GEOMETRY
        judged at the active tab's own real width — read straight off that
        placement's ``ResolvedLayoutItem``, never off ``ResolvedBoard.charts``'
        single catalog entry, which can hold a different placement's baked
        width entirely. Judging geometry off the catalog either uses the
        wrong width (the original bug) or, if the mismatch is detected and
        skipped instead, silently drops the detection-pass VL render that
        ``WARN-CHART-TITLE-TRUNCATED`` depends on — both silence a warning
        for a pie the user is actually looking at."""
        from dbt_charts.core.diagnostics import (
            WARN_CHART_TITLE_TRUNCATED,
            WARN_PIE_TOO_MANY_SEGMENTS,
        )
        from dbt_charts.core.render.renderer import render

        segments = "\n".join(f"      - {{cat: c{i}, value: {i + 1}}}" for i in range(8))
        yaml_content = f"""
title: Test
queries:
  q:
    type: values
    rows:
{segments}
charts:
  shared_pie:
    query: q
    type: pie
    theta: value
    color: cat
    title: "A very long pie chart title that will not fit at a narrow two-column active-tab width whatsoever"
  spacer:
    query: q
    type: bar
    x: cat
    y: value
tabs:
  id: t
  default: Active
  items:
    - title: Inactive
      rows: [{{cols: [shared_pie]}}]
    - title: Active
      rows: [{{cols: [shared_pie, spacer]}}]
"""
        board, executor = self._build(yaml_content, local_project)
        result = render(board, executor, format="json")

        warning_keys = {(w.chart, w.code) for w in result.warnings}
        assert (
            "shared_pie",
            WARN_PIE_TOO_MANY_SEGMENTS.code,
        ) in warning_keys, (
            "pie's own DATA detector (segment count) must fire regardless "
            f"of which placement is judged: {result.warnings}"
        )
        assert (
            "shared_pie",
            WARN_CHART_TITLE_TRUNCATED.code,
        ) in warning_keys, (
            "a pie placed twice at different widths must still have its "
            f"title truncation judged at a real, laid-out width: {result.warnings}"
        )
