"""Tests for compute_board_content_box — single owner of vertical stacking arithmetic.

TDD: tests written before implementation. All tests that exercise
compute_board_content_box/BoardContentBox fail until the symbol is added.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.project import Project
from dbt_charts.core.render import render
from dbt_charts.core.render.svg_utils import extract_svg_dimensions

from ..._run import Run


def _render_yaml(yaml: str, local_project: Callable[..., Project]) -> str:
    result = compile(yaml)
    assert result.success, result.errors
    assert result.board is not None
    executor = Run(
        result.board,
        env=build_exec_env(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    output = render(result.board, executor.ctx, executor.ledger, format="svg").output
    assert isinstance(output, str)
    return output


# ---------------------------------------------------------------------------
# Unit tests for compute_board_content_box gap arithmetic
# ---------------------------------------------------------------------------


class TestComputeBoardContentBox:
    """Gap arithmetic rules for the single-owner stacking function."""

    def _call(self, **kwargs):
        """``card_padding`` defaults to 0 so each gap assertion is about gaps.

        The band's own top inset is a separate rule with its own tests below.
        """
        from dbt_charts.core.render.sizing import compute_board_content_box

        return compute_board_content_box(
            **{
                "title_height": 0.0,
                "text_height": 0.0,
                "variables_height": 0.0,
                "inline_band_height": 0.0,
                "gap": 8.0,
                "variables_gap": 8.0,
                "has_layout_items": False,
                "opens_with_text": False,
                "card_padding": 0.0,
                **kwargs,
            }
        )

    def test_empty_all_zeros(self):
        box = self._call()
        assert box.non_layout_height == 0.0
        assert box.gap_before_layout == 0.0

    def test_title_only(self):
        box = self._call(title_height=40.0)
        assert box.non_layout_height == 40.0

    def test_text_only(self):
        box = self._call(text_height=20.0)
        assert box.non_layout_height == 20.0

    def test_variables_only(self):
        box = self._call(variables_height=30.0)
        assert box.non_layout_height == 30.0

    def test_title_then_text_uses_zero_gap(self):
        """heading_margin_bottom_px is already in title_height; 0 gap prevents double-spacing."""
        box = self._call(title_height=50.0, text_height=20.0, gap=8.0)
        assert box.non_layout_height == pytest.approx(50.0 + 20.0)  # 0 gap between them

    def test_title_then_leading_text_row_takes_back_the_row_inset(self):
        box = self._call(
            title_height=20.0,
            has_layout_items=True,
            opens_with_text=True,
            card_padding=16.0,
        )
        assert box.gap_before_layout == -16.0

    def test_text_then_variables_uses_gap(self):
        box = self._call(text_height=20.0, variables_height=30.0, gap=8.0)
        assert box.non_layout_height == pytest.approx(20.0 + 8.0 + 30.0)

    def test_title_text_variables_gap_rules(self):
        """title→text: 0 gap. text→variables: gap."""
        box = self._call(
            title_height=50.0, text_height=20.0, variables_height=30.0, gap=8.0
        )
        expected = 50.0 + 0.0 + 20.0 + 8.0 + 30.0
        assert box.non_layout_height == pytest.approx(expected)

    def test_gap_before_layout_when_has_items_and_content(self):
        box = self._call(title_height=40.0, has_layout_items=True, gap=8.0)
        assert box.gap_before_layout == pytest.approx(8.0)

    def test_no_gap_before_layout_when_no_content(self):
        box = self._call(has_layout_items=True, gap=8.0)
        assert box.gap_before_layout == 0.0

    def test_no_gap_before_layout_when_no_layout_items(self):
        box = self._call(title_height=40.0, has_layout_items=False, gap=8.0)
        assert box.gap_before_layout == 0.0

    def test_inline_band_replaces_title_and_variables(self):
        """When inline_band_height > 0, that replaces separate title + variables."""
        box = self._call(inline_band_height=60.0, gap=8.0)
        assert box.non_layout_height == pytest.approx(60.0)

    def test_inline_band_then_text_uses_gap(self):
        """inline_band → text: normal gap (not the 0-gap title special case)."""
        box = self._call(inline_band_height=60.0, text_height=20.0, gap=8.0)
        assert box.non_layout_height == pytest.approx(60.0 + 8.0 + 20.0)

    def test_the_band_is_inset_by_card_padding_before_its_first_element(self):
        """The inset a chart's ink already carries, so prose starts level with it."""
        box = self._call(title_height=50.0, text_height=20.0, card_padding=16.0)

        assert box.content_top == pytest.approx(16.0)
        assert box.non_layout_height == pytest.approx(16.0 + 50.0 + 20.0)

    def test_the_inset_is_claimed_once_for_the_band_not_once_per_element(self):
        """Title → text stays flush; only the band's own top edge is inset."""
        one = self._call(title_height=50.0, card_padding=16.0)
        three = self._call(
            title_height=50.0,
            text_height=20.0,
            variables_height=30.0,
            card_padding=16.0,
        )

        assert three.non_layout_height - one.non_layout_height == pytest.approx(
            20.0 + 8.0 + 30.0
        )

    def test_a_variables_only_band_is_inset_like_any_other(self):
        """It is the band that is inset, not prose specifically.

        A board with controls and no title or text still opens a band, and its
        controls line up with chart ink for the same reason a heading does.
        """
        box = self._call(variables_height=30.0, card_padding=16.0)

        assert box.content_top == pytest.approx(16.0)
        assert box.non_layout_height == pytest.approx(16.0 + 30.0)

    def test_a_board_with_no_band_is_not_inset(self):
        """Nothing to inset, and a chart-only board must not grow by a padding."""
        box = self._call(has_layout_items=True, card_padding=16.0)

        assert box.content_top == 0.0
        assert box.non_layout_height == 0.0


# ---------------------------------------------------------------------------
# Characterization tests: stacking arithmetic invariants
# ---------------------------------------------------------------------------

_QUERIES_AND_CHART = """
queries:
  q:
    columns: [month, revenue]
    values:
      - [Jan, 100]
      - [Feb, 140]
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
"""

_TEXT = "A short description paragraph."


class TestBoardStackingArithmeticInvariants:
    """Relationship tests: verify stacking arithmetic invariants without pinning
    absolute pixel values (which would break on theme/default changes).

    Core invariant: title→text transition uses 0 gap because heading_margin_bottom_px
    is already baked into the measured title height.
    """

    def test_nested_adding_text_after_title_is_shorter_than_text_standalone(
        self, local_project: Callable[..., Project]
    ):
        """0-gap invariant on the nested render path (_build_board_content_items).

        Adding text after a title costs only text_height (0 gap), while adding
        text as the first element costs text_height + gap_before_layout. So the
        title+text delta must be strictly less than the standalone text contribution.
        """
        title_chart = (
            _QUERIES_AND_CHART
            + "\nrows:\n  - title: Nested Section\n    cols:\n      - c\n"
        )
        text_chart = (
            _QUERIES_AND_CHART
            + f"\nrows:\n  - text: |\n      {_TEXT}\n    cols:\n      - c\n"
        )
        title_text_chart = (
            _QUERIES_AND_CHART
            + f"\nrows:\n  - title: Nested Section\n    text: |\n      {_TEXT}\n    cols:\n      - c\n"
        )
        chart_only = _QUERIES_AND_CHART + "\nrows:\n  - c\n"

        h_chart = extract_svg_dimensions(_render_yaml(chart_only, local_project)).height
        h_title = extract_svg_dimensions(
            _render_yaml(title_chart, local_project)
        ).height
        h_text = extract_svg_dimensions(_render_yaml(text_chart, local_project)).height
        h_title_text = extract_svg_dimensions(
            _render_yaml(title_text_chart, local_project)
        ).height

        assert h_title_text > h_title, "text must add height after title"
        # 0-gap: cost of adding text after title (text_h only) <=
        # cost of text as first element (text_h + gap_before_layout)
        assert (h_title_text - h_title) <= (h_text - h_chart)

    def test_root_adding_text_after_title_is_shorter_than_text_standalone(
        self, local_project: Callable[..., Project]
    ):
        """0-gap invariant on the root render path (render_board_svg).

        On the root path, standalone text adds text_h + gap (gap_before_layout is
        introduced). Adding text after title adds only text_h (gap_before_layout was
        already present from the title; 0 gap between title and text).
        """
        chart_only = _QUERIES_AND_CHART + "\nrows:\n  - c\n"
        title_chart = _QUERIES_AND_CHART + "\ntitle: Root Title\nrows:\n  - c\n"
        text_chart = _QUERIES_AND_CHART + f"\ntext: |\n  {_TEXT}\nrows:\n  - c\n"
        title_text_chart = (
            _QUERIES_AND_CHART
            + f"\ntitle: Root Title\ntext: |\n  {_TEXT}\nrows:\n  - c\n"
        )
        h_chart = extract_svg_dimensions(_render_yaml(chart_only, local_project)).height
        h_title = extract_svg_dimensions(
            _render_yaml(title_chart, local_project)
        ).height
        h_text = extract_svg_dimensions(_render_yaml(text_chart, local_project)).height
        h_title_text = extract_svg_dimensions(
            _render_yaml(title_text_chart, local_project)
        ).height

        assert h_title_text > h_title, "text must add height after title"
        # 0-gap: adding text after title costs less than standalone text
        # (standalone text adds text_h + gap_before_layout; title+text adds text_h)
        assert (h_title_text - h_title) < (h_text - h_chart)

    def test_title_text_taller_than_title_only_and_text_only(
        self, local_project: Callable[..., Project]
    ):
        """A board with both title and text is taller than either alone."""
        title_chart = (
            _QUERIES_AND_CHART
            + "\nrows:\n  - title: Nested Section\n    cols:\n      - c\n"
        )
        text_chart = (
            _QUERIES_AND_CHART
            + f"\nrows:\n  - text: |\n      {_TEXT}\n    cols:\n      - c\n"
        )
        title_text_chart = (
            _QUERIES_AND_CHART
            + f"\nrows:\n  - title: Nested Section\n    text: |\n      {_TEXT}\n    cols:\n      - c\n"
        )
        h_title = extract_svg_dimensions(
            _render_yaml(title_chart, local_project)
        ).height
        h_text = extract_svg_dimensions(_render_yaml(text_chart, local_project)).height
        h_title_text = extract_svg_dimensions(
            _render_yaml(title_text_chart, local_project)
        ).height

        assert h_title_text > h_title
        assert h_title_text > h_text


# ---------------------------------------------------------------------------
# Variable controls alignment: <g> translate must use content_x, not x_offset
# ---------------------------------------------------------------------------


def test_variable_controls_translate_uses_card_padding() -> None:
    """Variable controls <g> must translate by x_offset+card_padding, not bare x_offset.

    Regression: _build_board_content_items placed variables at x_offset while
    title/text used content_x = x_offset + card_padding, causing a card_padding
    px horizontal misalignment between the variable bar and chart content.

    Strategy: call _build_board_content_items with distinctive x_offset and
    card_padding values; assert the variables <g> translate contains content_x,
    not x_offset.
    """
    from dbt_charts.core.render.boards import _build_board_content_items

    x_offset = 10.0
    card_padding = 20.0  # distinctive — differs from x_offset so both show up
    content_x = x_offset + card_padding  # expected: 30.0

    items, _ = _build_board_content_items(
        x_offset=x_offset,
        y_offset=0.0,
        gap=8.0,
        variables_gap=8.0,
        title_svg="<text>title</text>",
        title_height=30.0,
        text_svg="",
        text_height=0.0,
        variables_svg='<foreignObject x="0" y="0" width="100" height="50"/>',
        variables_height=50.0,
        layout_content="",
        layout_content_height=0.0,
        opens_with_text=False,
        card_padding=card_padding,
        content_width=400.0,
    )

    var_items = [i for i in items if "foreignObject" in i]
    assert var_items, "No variable item found in content items"

    var_g = var_items[0]
    # _px strips trailing .0 for whole-number floats — match both formats
    expected_x_int = int(content_x)  # 30
    assert (
        f"translate({expected_x_int}," in var_g or f"translate({content_x}," in var_g
    ), (
        f"Variable controls <g> must translate by content_x={content_x} "
        f"(x_offset={x_offset} + card_padding={card_padding}), "
        f"not bare x_offset. Got: {var_g!r}"
    )
