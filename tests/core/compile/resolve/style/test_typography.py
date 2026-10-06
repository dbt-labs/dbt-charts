"""Guard tests for the board-margin -> title-tier derivation in typography.py.

Object titles (chart/table/spark) pick font size AND family by outer card
width tier alone (``chart_title_spec``). The width thresholds that separate
tiers are derived once from the default board's column grid (board width,
margin, column count) rather than hand-picked pixels. If ``frame.margin`` (or
``frame.width``) ever changes without re-deriving the thresholds, cards get
narrower or wider and can silently drop a tier -- e.g. a 2-column card that
used to sit in the "medium" tier (title-slot family) sliding into "narrow"
(body family), quietly losing serif titles on themes like editorial/cream.

These tests assert relationships and behavior, never threshold pixel
literals or theme values (see ``dbt-charts/AGENTS.md``): sizes/weights/families
are always compared against each other or against the live theme, not
hardcoded numbers or strings.
"""

from __future__ import annotations

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.compile.resolve.style.typography import (
    _NARROW_MAX,
    _REFERENCE_CONTENT_WIDTH,
    _TINY_MAX,
    chart_title_spec,
)

from ...._board_utils import apply_static_layout

_TWO_COLUMN_BOARD_YAML = """
extends: clarity
title: Two Column Regression Check
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart_a:
    query: sales
    type: bar
    x: month
    y: revenue
    title: Chart A
  chart_b:
    query: sales
    type: bar
    x: month
    y: revenue
    title: Chart B
cols:
  - chart_a
  - chart_b
"""


class TestBoardMarginRegression:
    """Changing frame.margin moves every card's width; without re-deriving the
    tier thresholds, a 2-column card at the default board width can silently
    cross between the title-family and body-family tiers.
    """

    def test_reference_content_width_tracks_default_board_geometry(self) -> None:
        frame = resolve_style(get_theme_style("clarity")).frame

        assert frame.max_width - 2 * frame.margin == _REFERENCE_CONTENT_WIDTH

    def test_two_column_card_at_default_board_width_keeps_title_family(self) -> None:
        result = compile(_TWO_COLUMN_BOARD_YAML)
        assert result.success, f"Compile failed: {result.errors}"
        assert result.board is not None

        board = apply_static_layout(result.board)
        assert len(board.layout.items) == 2
        card_width = board.layout.items[0].width
        assert card_width == board.layout.items[1].width

        editorial = get_theme_style("clarity")
        charts_style = resolve_chart_style_context(editorial)
        _, _, family = chart_title_spec(card_width, chart_style_context=charts_style)

        title_family_first = (
            resolve_style(editorial).title.font.family.split(",")[0].strip()
        )
        body_family_first = charts_style.font_family.split(",")[0].strip()

        assert family.startswith(title_family_first), (
            "2-column card at the default board width dropped out of the "
            f"title-family tier at width={card_width}; got family={family!r}"
        )
        assert not family.startswith(body_family_first), (
            "2-column card at the default board width fell back to the body "
            f"family at width={card_width}; got family={family!r}"
        )


class TestTinyBoundary:
    """A card below the tiny/narrow line gets the tiny treatment (weight
    floor, smaller size); one above it uses the theme-configured weight.
    """

    def test_weight_floor_applies_only_below_the_tiny_boundary(self) -> None:
        charts_style = resolve_chart_style_context(get_theme_style("clarity"))

        # Read the theme's own configured weight from comfortably inside the
        # tier above tiny, rather than hardcoding it.
        _, configured_weight, _ = chart_title_spec(
            _NARROW_MAX + 1.0, chart_style_context=charts_style
        )
        below_size, below_weight, _ = chart_title_spec(
            _TINY_MAX - 1.0, chart_style_context=charts_style
        )
        above_size, above_weight, _ = chart_title_spec(
            _TINY_MAX + 1.0, chart_style_context=charts_style
        )

        assert below_weight != configured_weight, (
            "tiny tier must floor the weight above the theme's configured "
            "value, not pass it through unchanged"
        )
        assert above_weight == configured_weight, (
            "just above the tiny boundary the weight floor must not apply"
        )
        assert below_size <= above_size, "tiny tier must not exceed the tier above it"


class TestNarrowBoundary:
    """The narrow/medium line is the only boundary that switches font
    FAMILY (body -> title-slot). Size steps in the same direction.
    """

    def test_family_and_size_step_at_the_narrow_boundary(self) -> None:
        editorial = get_theme_style("clarity")
        charts_style = resolve_chart_style_context(editorial)

        below_size, _, below_family = chart_title_spec(
            _NARROW_MAX - 1.0, chart_style_context=charts_style
        )
        above_size, _, above_family = chart_title_spec(
            _NARROW_MAX + 1.0, chart_style_context=charts_style
        )

        title_family_first = (
            resolve_style(editorial).title.font.family.split(",")[0].strip()
        )
        body_family_first = charts_style.font_family.split(",")[0].strip()

        assert below_family.startswith(body_family_first), (
            "just below the narrow boundary the title must still use the body family"
        )
        assert above_family.startswith(title_family_first), (
            "just above the narrow boundary the title must switch to the "
            "title-slot family"
        )
        assert below_size <= above_size, (
            "size must not decrease when crossing from narrow into medium"
        )
