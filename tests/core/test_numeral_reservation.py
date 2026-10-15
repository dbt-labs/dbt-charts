"""Tests for the font-measured suffix reservation on ``font_measure``.

Lives there (not ``compile/``): the ruler consumer is compile-stage (ticks
are baked at resolve time) but the ledger consumer runs over query rows at
render time, and ``font_measure`` is the leaf both sides already import
directly.

Cases are derived from ``SharedScale`` itself (every tier x every mode)
rather than a hand-typed suffix table, so the test cannot pin a suffix
the en-US narrative words never produce (the narrative-trillions suffix is
``"trn"``).

``compose_suffix_reservation`` replaced the earlier whole-digit-unit
``suffix_reservation_slots``: ``dbt Sans Tabular`` is tabular, not
monospace — only the ten digits and the minus sign share one fixed
advance, so a suffix like ``" K"`` (~1.45 digit-widths) is never an
integer number of digits wide, and no count of one-digit U+2007 FIGURE
SPACEs can express that without a visible residual (ceil overshoots by
more than half a digit; floor undershoots by nearly as much). Composing a
short run of the Unicode space vocabulary — each occupying a different,
measured fraction of a digit's advance — reaches sub-pixel error instead.
See "the reservation is exact in slots, approximate in width" in
``ai_notes/charts/jul26-02-numeral-system-design.md`` (corrected 2026-08-06).
"""

from __future__ import annotations

from typing import get_args

import pytest

from dbt_charts.core.font_measure import (
    RESERVATION_GUARD,
    _measurer_for_composition,
    compose_suffix_reservation,
)
from dbt_charts.core.fonts import (
    DBT_SANS_TABULAR_FONT_FAMILY,
    DBT_SERIF_OLDSTYLE_TABULAR_FONT_FAMILY,
)
from dbt_charts.core.text.format_d3 import NumberLocaleName
from dbt_charts.core.text.magnitude_words import magnitude_words
from dbt_charts.core.text.numeral_scale import SharedScale, SuffixMode

# Every (exponent, mode) the resolver's SuffixMode/exponent tables can
# actually produce — same coverage the old slot-count test pinned.
_ALL_SUFFIX_CASES: list[tuple[int, SuffixMode]] = [
    (3, SuffixMode.ANCHOR),  # " K"
    (3, SuffixMode.REPEAT),  # "k"
    (6, SuffixMode.ANCHOR),  # " M"
    (6, SuffixMode.REPEAT),  # "mn"
    (9, SuffixMode.ANCHOR),  # " B"
    (9, SuffixMode.REPEAT),  # "bn"
    (12, SuffixMode.ANCHOR),  # " T"
    (12, SuffixMode.REPEAT),  # "trn"
]

# Sub-pixel at any plausible axis-label font size (matches
# font_measure._SUBPIXEL_RESIDUAL_TOLERANCE_EM: 0.05em is under a pixel up
# to a 20px label). Measured in em, not digit-widths -- fonts with a
# narrower digit (dbt Serif Oldstyle Tabular's digit is 0.5em against Sans
# Tabular's 0.648em) would otherwise need a different digit-width
# tolerance per font for the same real pixel error. Tight enough that a
# regression back to whole-digit quantization (a residual of half a digit
# or more, i.e. 0.25-0.32em) fails it loudly.
_TOLERANCE_EM = 0.05

# Every vendored family the tabular-figures guarantee can resolve to.
# dbt Serif Oldstyle Tabular is missing two of the ten candidate space
# characters (PUNCTUATION SPACE, MEDIUM MATHEMATICAL SPACE) -- covering it
# here alongside Sans Tabular is what actually exercises the
# candidate-exclusion path, not just the common case where all ten exist.
_ALL_TABULAR_FAMILIES = (
    DBT_SANS_TABULAR_FONT_FAMILY,
    DBT_SERIF_OLDSTYLE_TABULAR_FONT_FAMILY,
)


@pytest.mark.parametrize("font_family", _ALL_TABULAR_FAMILIES)
@pytest.mark.parametrize("name", get_args(NumberLocaleName))
def test_every_locale_word_composes_a_reservation(
    name: NumberLocaleName, font_family: str
) -> None:
    measurer = _measurer_for_composition(font_family)
    analytic, narrative = magnitude_words(name)
    for word in {*analytic, *narrative}:
        padding = compose_suffix_reservation(word, font_family)
        error_em = abs(
            measurer.measure(padding.removesuffix(RESERVATION_GUARD), 1.0)
            - measurer.measure(word, 1.0)
        )
        assert error_em < _TOLERANCE_EM, (word, error_em)


@pytest.mark.parametrize("font_family", _ALL_TABULAR_FAMILIES)
@pytest.mark.parametrize(("exponent", "mode"), _ALL_SUFFIX_CASES)
def test_composed_reservation_matches_the_suffix_advance_within_tolerance(
    exponent, mode, font_family
):
    suffix = SharedScale(exponent=exponent, mode=mode).suffix_string
    composed = compose_suffix_reservation(suffix, font_family)

    measurer = _measurer_for_composition(font_family)
    digit_advance = measurer.measure("0", 1.0)
    suffix_advance = measurer.measure(suffix, 1.0)
    padding = composed.removesuffix(RESERVATION_GUARD)
    padding_advance = measurer.measure(padding, 1.0)

    error_em = abs(padding_advance - suffix_advance)
    assert error_em < _TOLERANCE_EM, (
        f"suffix {suffix!r} (exponent={exponent}, mode={mode}, "
        f"font_family={font_family!r}): composed {composed!r} measures "
        f"{error_em:.4f}em ({error_em / digit_advance:.4f} digit-widths) off "
        "the suffix's own advance"
    )


def test_serif_oldstyle_tabular_composes_differently_from_sans_tabular():
    """Regression: compose_suffix_reservation used to resolve its measurer
    via get_font_measurer(family, numeric=True), whose numeric branch
    ignores family and always resolves to dbt Sans Tabular (Source Serif
    aside) -- so a Serif Oldstyle Tabular axis silently composed against
    Sans advances and painted the result in serif, misaligning by however
    much the two boards' space characters differ. The composed string must
    now be measured against -- and therefore generally differ between --
    each family's own board.
    """
    suffix = SharedScale(exponent=3, mode=SuffixMode.ANCHOR).suffix_string
    sans = compose_suffix_reservation(suffix, DBT_SANS_TABULAR_FONT_FAMILY)
    serif = compose_suffix_reservation(suffix, DBT_SERIF_OLDSTYLE_TABULAR_FONT_FAMILY)
    assert sans != serif


def test_composed_reservation_ends_with_the_guard():
    suffix = SharedScale(exponent=3, mode=SuffixMode.ANCHOR).suffix_string
    composed = compose_suffix_reservation(suffix, DBT_SANS_TABULAR_FONT_FAMILY)
    assert composed.endswith(RESERVATION_GUARD)


def test_composition_is_deterministic_across_calls():
    """Same (suffix, font_family) must always compose the same string, so
    goldens do not churn between runs."""
    suffix = SharedScale(exponent=6, mode=SuffixMode.REPEAT).suffix_string
    first = compose_suffix_reservation(suffix, DBT_SANS_TABULAR_FONT_FAMILY)
    second = compose_suffix_reservation(suffix, DBT_SANS_TABULAR_FONT_FAMILY)
    assert first == second


def test_no_suffix_reserves_no_padding():
    assert compose_suffix_reservation("", DBT_SANS_TABULAR_FONT_FAMILY) == ""


def test_unvendored_font_family_degrades_to_sans_tabular():
    """An unvendored (but legal, authored) family degrades to dbt Sans
    Tabular, same as every other numeral measurement in the codebase — it
    must not raise, since the browser resolves such a family itself.
    """
    suffix = SharedScale(exponent=6, mode=SuffixMode.ANCHOR).suffix_string
    assert compose_suffix_reservation(
        suffix, "Courier New"
    ) == compose_suffix_reservation(suffix, DBT_SANS_TABULAR_FONT_FAMILY)


def test_missing_candidate_is_excluded_not_fatal(monkeypatch):
    """A font can legitimately ship only some of the ten candidates (dbt
    Serif Oldstyle Tabular is missing two) — a missing candidate must not
    by itself raise; the search simply excludes it and composes from
    whatever the board does have.
    """
    from dbt_charts.core import font_measure

    real_measurer = font_measure._measurer_for_composition(DBT_SANS_TABULAR_FONT_FAMILY)

    class _MissingPunctuationSpace:
        """Sans Tabular's own widths, minus PUNCTUATION SPACE."""

        def has_glyph(self, char: str) -> bool:
            return char != "\u2008" and real_measurer.has_glyph(char)

        def measure(self, text: str, font_size: float) -> float:
            return real_measurer.measure(text, font_size)

    monkeypatch.setattr(
        font_measure,
        "_measurer_for_composition",
        lambda family: _MissingPunctuationSpace(),
    )
    # Must not raise, and must not use the excluded character.
    composed = font_measure.compose_suffix_reservation(
        " K", DBT_SANS_TABULAR_FONT_FAMILY
    )
    assert "\u2008" not in composed


def test_no_achievable_subpixel_composition_raises_loudly(monkeypatch):
    """When NO candidate is available, the search cannot compose anything
    -- the resulting residual (the full suffix advance) can never be
    sub-pixel, and that must surface as a loud failure: every family the
    tabular-figures guarantee accepts is vendored and under our control,
    so a board this deficient is a font-registry defect, not something a
    caller can work around.
    """
    from dbt_charts.core import font_measure

    class _NoCandidatesAvailable:
        def has_glyph(self, char: str) -> bool:
            return False

        def measure(self, text: str, font_size: float) -> float:
            return 1.0

    monkeypatch.setattr(
        font_measure,
        "_measurer_for_composition",
        lambda family: _NoCandidatesAvailable(),
    )
    with pytest.raises(RuntimeError, match="cannot support the tabular-figures"):
        font_measure.compose_suffix_reservation(" K", DBT_SANS_TABULAR_FONT_FAMILY)
