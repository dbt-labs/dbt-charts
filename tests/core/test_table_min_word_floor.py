"""Tests for _measure_min_word_width: the per-column min-word-floor helper.

The min-word floor is the pixel width of the widest whitespace-delimited token
across sampled cell values and the header label.  Two filters apply:
  - Skip URL tokens (^https?://)
  - Skip tokens longer than 40 characters

These filters prevent pathological tokens (URLs, opaque IDs) from setting
an unreachable floor.
"""

from __future__ import annotations

import pytest


class TestMeasureMinWordWidth:
    """Direct unit tests for _measure_min_word_width."""

    @pytest.fixture
    def measurer(self):
        from dbt_charts.core.font_measure import get_font_measurer

        return get_font_measurer()

    def test_header_label_is_measured_with_the_header_measurer(self, measurer):
        from dbt_charts.core.font_measure import get_font_measurer
        from dbt_charts.core.render.chart.table_support import _measure_min_word_width

        header_measurer = get_font_measurer("'Source Serif 4', Georgia, serif")
        result = _measure_min_word_width(
            [],
            "Illinois",
            measurer,
            header_measurer,
            font_size=13.0,
            header_font_size=13.0,
        )
        assert result == pytest.approx(
            header_measurer.measure("Illinois", 13.0), abs=0.01
        )
        assert result != pytest.approx(measurer.measure("Illinois", 13.0), abs=1.0)

    def test_empty_cells_returns_header_floor(self, measurer):
        """With no cell values, floor equals the header label width."""
        from dbt_charts.core.render.chart.table_support import _measure_min_word_width

        result = _measure_min_word_width(
            [], "Revenue", measurer, measurer, font_size=13.0, header_font_size=13.0
        )
        header_w = measurer.measure("Revenue", 13.0)
        assert result == pytest.approx(header_w, abs=1.0)

    def test_single_short_word(self, measurer):
        """A single short word: floor equals max(header_w, word_w)."""
        from dbt_charts.core.render.chart.table_support import _measure_min_word_width

        result = _measure_min_word_width(
            ["Hello"], "Hi", measurer, measurer, font_size=13.0, header_font_size=13.0
        )
        expected = max(
            measurer.measure("Hello", 13.0),
            measurer.measure("Hi", 13.0),
        )
        assert result == pytest.approx(expected, abs=1.0)

    def test_multi_word_cell_uses_longest_token(self, measurer):
        """For a multi-word cell, only the longest individual token counts."""
        from dbt_charts.core.render.chart.table_support import _measure_min_word_width

        # "Opportunity" is the longest token in the phrase
        result = _measure_min_word_width(
            ["Next steps to close"],
            "Notes",
            measurer,
            measurer,
            font_size=13.0,
            header_font_size=13.0,
        )
        # Max token is "steps" or "close" or "Next" — but importantly narrower
        # than the full string width.
        full_string_w = measurer.measure("Next steps to close", 13.0)
        assert result < full_string_w

    def test_url_token_is_skipped(self, measurer):
        """URL tokens (^https?://) are excluded from floor measurement."""
        from dbt_charts.core.render.chart.table_support import _measure_min_word_width

        long_url = "https://docs.google.com/spreadsheets/d/1tqWJ_very_long_id/edit"
        values = [f"See {long_url} for details"]
        result = _measure_min_word_width(
            values, "Link", measurer, measurer, font_size=13.0, header_font_size=13.0
        )

        # Floor should not be driven by the URL — just by "See", "for", "details", "Link"
        url_w = measurer.measure(long_url, 13.0)
        assert result < url_w, (
            f"URL token should not set the floor; got {result:.1f}px "
            f"vs URL width {url_w:.1f}px"
        )

    def test_token_over_40_chars_is_skipped(self, measurer):
        """Tokens longer than 40 characters are excluded from floor measurement."""
        from dbt_charts.core.render.chart.table_support import _measure_min_word_width

        long_token = "a" * 41  # 41 chars — over threshold
        short_token = "short"
        values = [f"{long_token} {short_token}"]
        result = _measure_min_word_width(
            values, "Col", measurer, measurer, font_size=13.0, header_font_size=13.0
        )

        long_w = measurer.measure(long_token, 13.0)
        assert result < long_w, (
            f">40-char token should not set the floor; got {result:.1f}px "
            f"vs token width {long_w:.1f}px"
        )

    def test_header_label_wider_than_cell_tokens(self, measurer):
        """Header label tokens are included — a wide header drives the floor."""
        from dbt_charts.core.render.chart.table_support import _measure_min_word_width

        # Very short cell, but header "CustomerSegmentation" is wide
        result = _measure_min_word_width(
            ["Yes", "No"],
            "CustomerSegmentation",
            measurer,
            measurer,
            font_size=13.0,
            header_font_size=13.0,
        )
        header_token_w = measurer.measure("CustomerSegmentation", 13.0)
        assert result == pytest.approx(header_token_w, abs=1.0)

    def test_multi_paragraph_cell_uses_longest_token(self, measurer):
        """Multi-paragraph / multi-line text: floor is still per-token, not per-line."""
        from dbt_charts.core.render.chart.table_support import _measure_min_word_width

        paragraph = (
            "This is a very long note.\n"
            "It contains multiple sentences.\n"
            "The longest individual word might be 'Responsibilities'.\n"
        )
        result = _measure_min_word_width(
            [paragraph],
            "Notes",
            measurer,
            measurer,
            font_size=13.0,
            header_font_size=13.0,
        )
        # Floor must be narrower than the full paragraph
        full_w = measurer.measure(paragraph, 13.0)
        assert result < full_w
        # Floor must be positive
        assert result > 0

    def test_exactly_40_char_token_is_included(self, measurer):
        """A token of exactly 40 characters is included (threshold is > 40, not >= 40)."""
        from dbt_charts.core.render.chart.table_support import _measure_min_word_width

        token_40 = "a" * 40  # exactly 40 — included
        token_41 = "b" * 41  # 41 — excluded
        values = [f"{token_41} {token_40}"]
        result = _measure_min_word_width(
            values, "H", measurer, measurer, font_size=13.0, header_font_size=13.0
        )
        expected = measurer.measure(token_40, 13.0)
        assert result == pytest.approx(expected, abs=1.0)

    def test_multiple_values_takes_max_token_across_all(self, measurer):
        """Floor is the max token width across all sampled values."""
        from dbt_charts.core.render.chart.table_support import _measure_min_word_width

        values = ["short text", "medium length text here", "tiny"]
        result = _measure_min_word_width(
            values, "Notes", measurer, measurer, font_size=13.0, header_font_size=13.0
        )

        # Manually compute expected: max token across all cells + header
        all_tokens = []
        for val in values:
            for tok in str(val).split():
                if not tok.startswith(("http://", "https://")) and len(tok) <= 40:
                    all_tokens.append(measurer.measure(tok, 13.0))
        all_tokens.append(measurer.measure("Notes", 13.0))
        expected = max(all_tokens)
        assert result == pytest.approx(expected, abs=1.0)
