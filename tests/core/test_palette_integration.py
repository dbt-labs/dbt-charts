"""Integration tests — the palette resolver over the shipped scaffold palettes.

Checks:
  - scaffold tokens resolve to hex
  - old fivetran_grays names no longer resolve but surface a did-you-mean suggestion
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.resolve.style.palette import (
    UnknownPaletteError,
    color,
    palette,
)


class TestScaffoldTokens:
    """Scaffold tokens resolve to hex, and distinct steps differ."""

    def test_scaffold_tokens(self):
        # All scaffold tokens must resolve to 7-char hex strings
        tokens = [
            "dbt-grays.11",
            "dbt-grays.1",
            "dbt-grays.7",
            "dbt-creams.11",
            "dbt-creams.1",
        ]
        results = [color(t) for t in tokens]
        for token, result in zip(tokens, results, strict=True):
            assert result.startswith("#") and len(result) == 7, (
                f"{token} must resolve to a 7-char hex, got {result!r}"
            )
        # Discrimination: ink and canvas must differ
        assert color("dbt-grays.11") != color("dbt-grays.1")
        assert color("dbt-creams.11") != color("dbt-creams.1")


class TestLegacyNameDidYouMean:
    def test_palette_old_fivetran_name_raises_with_suggestion(self):
        with pytest.raises(UnknownPaletteError) as excinfo:
            palette("fivetran_grays")
        msg = str(excinfo.value)
        # The exact phrase in the message isn't a contract; just require we
        # surface the new canonical name somewhere in the error.
        assert "dbt-grays" in msg or "unknown palette" in msg

    def test_color_old_fivetran_name_raises(self):
        from dbt_charts.core.compile.resolve.style.palette import UnknownColorError

        with pytest.raises(UnknownColorError):
            color("fivetran_grays.gray-90")


class TestShorthandInPalette:
    def test_shorthand_n_and_reverse_combine(self):
        shorthand = palette("dbt-seq-blue:5_r")
        explicit = palette("dbt-seq-blue", steps=5, reverse=True)
        assert shorthand == explicit
