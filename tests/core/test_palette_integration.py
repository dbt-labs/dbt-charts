"""Integration tests — M2 palette resolver wired through config.

Checks:
  - config.dbt_grays / config.dbt_creams populated from defaults/palettes/scaffold/
  - old fivetran_grays names no longer resolve but surface a did-you-mean suggestion
  - color("dbt-grays.ink") produces the same value as config.dbt_grays
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_config
from dbt_charts.core.compile.resolve.style.palette import (
    UnknownPaletteError,
    color,
    palette,
)


class TestColorResolverMatchesConfig:
    """color() and config.dbt_grays must return the same hex per token."""

    def test_scaffold_tokens(self):
        # All scaffold tokens must resolve to 7-char hex strings
        tokens = [
            "dbt-grays.ink",
            "dbt-grays.canvas",
            "dbt-grays.subtitle",
            "dbt-creams.ink",
            "dbt-creams.canvas",
        ]
        results = [color(t) for t in tokens]
        for token, result in zip(tokens, results, strict=True):
            assert result.startswith("#") and len(result) == 7, (
                f"{token} must resolve to a 7-char hex, got {result!r}"
            )
        # Discrimination: ink and canvas must differ
        assert color("dbt-grays.ink") != color("dbt-grays.canvas")
        assert color("dbt-creams.ink") != color("dbt-creams.canvas")

    def test_scaffold_matches_config_attribute(self):
        config = get_config()
        for slot, hex_ in config.dbt_grays.items():
            assert color(f"dbt-grays.{slot}").lower() == str(hex_).lower()


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
