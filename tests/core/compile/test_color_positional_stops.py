"""`color("<palette>.<n>")` resolves positionally for every stop-list family.

Categorical already worked; sequential and diverging raised, which is why the
palette reference page reached for a loop construct instead of naming stops the
way the tone strips do. The families all carry the same ordered stop list, so
the positional form means the same thing in each.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.resolve.style.palette import (
    UnknownColorError,
    color,
    palette,
)

STOP_LIST_PALETTES = ["vivid-10", "dbt-seq-blue", "dbt-div-blue-red"]


@pytest.mark.parametrize("name", STOP_LIST_PALETTES)
def test_every_stop_resolves_to_its_palette_entry(name: str):
    """The n-th token is the n-th stop, 1-indexed, for every family."""
    stops = palette(name)
    assert [color(f"{name}.{i + 1}") for i in range(len(stops))] == list(stops)


@pytest.mark.parametrize("name", STOP_LIST_PALETTES)
def test_index_past_the_end_raises(name: str):
    """Out-of-range is an error, not a clamp or a wrap."""
    n = len(palette(name))
    with pytest.raises(
        UnknownColorError, match=rf"no stop {n + 1}; valid stops: 1-{n}"
    ):
        color(f"{name}.{n + 1}")


@pytest.mark.parametrize("name", STOP_LIST_PALETTES)
def test_zero_and_negative_are_rejected(name: str):
    """A data palette's stops start at 1; 0 is a mistake worth naming."""
    with pytest.raises(UnknownColorError, match="no stop 0; valid stops: 1-"):
        color(f"{name}.0")


@pytest.mark.parametrize("name", STOP_LIST_PALETTES)
def test_non_integer_slot_is_rejected(name: str):
    """A stop-list palette has no named slots — only positions."""
    with pytest.raises(UnknownColorError, match="integer"):
        color(f"{name}.solid")


class TestScaffoldAddressesBothWays:
    """Scaffold palettes layer named aliases over an ordered stop list, so they
    answer both forms. The slot's type picks which — names are never integers."""

    def test_alias_name_resolves(self):
        assert color("dbt-grays.black") == "#000000"

    def test_integer_slot_resolves_positionally(self):
        stops = palette("dbt-grays")
        assert [color(f"dbt-grays.{i + 1}") for i in range(len(stops))] == list(stops)

    def test_unknown_name_still_reports_aliases(self):
        with pytest.raises(UnknownColorError, match="Known aliases"):
            color("dbt-grays.nope")

    def test_tone_has_no_positional_form(self):
        """Tone carries names only — an integer is a mistake, not a position."""
        with pytest.raises(UnknownColorError, match="Known aliases"):
            color("positive.1")
