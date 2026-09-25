"""Strict font measurement for text layout."""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence

from dbt_charts.core.fonts import (
    SOURCE_CODE_PRO_FONT_FAMILY,
    get_face,
    get_font_path,
    registry_family,
)
from mdsvg import Style as MdsvgStyle
from mdsvg.fonts import FontFace, FontFaces, FontMeasurer

_font_measurers: dict[str, FontMeasurer] = {}

# The full CSS font-weight keyword vocabulary (CSS Fonts Module Level 4 §
# font-weight-prop): normal/bold are absolute, bolder/lighter are declared
# *relative* to the inherited weight -- which resolve does not track here.
# Treated as a fixed approximation (700/300) for measurement purposes only
# (which weight instance to measure a variable font against); this never
# feeds the painted weight, only how wide the measured glyphs are assumed
# to be. Every other legal value is numeric (100-900) and reaches the
# float() fallback below unchanged.
_CSS_WEIGHT_KEYWORDS = {
    "normal": 400.0,
    "bold": 700.0,
    "bolder": 700.0,
    "lighter": 300.0,
}

# ZERO WIDTH SPACE, appended after a trailing suffix-field reservation so
# vl_convert's SVG serialization treats the run as interior rather than
# trailing whitespace to trim (vl_convert strips a bare trailing run of
# space characters before the SVG is even written — the reservation is
# always trailing, never leading or interior, so it always needs this).
# Measures 0 advance in every vendored face, so it never perturbs a
# composed width. Written as the escape sequence, not the raw character,
# so it stays visible in a diff.
RESERVATION_GUARD = "​"

# The Unicode space vocabulary a suffix-field reservation composes from, by
# codepoint. In a tabular face only the ten digits and the minus sign share
# one fixed advance — every other character, including these spaces, is
# proportional, so each occupies a different (measurable, font-specific)
# fraction of the digit advance. Composing two or three of them approximates
# a suffix's real advance far more closely than padding in whole U+2007
# FIGURE SPACE units ever can: U+2007 is defined to be exactly one digit
# wide, but a suffix like " K" is not an integer number of digits wide, so
# no count of FIGURE SPACEs alone can match it (ceil overshoots, floor
# undershoots, both by a visible fraction of a digit).
_RESERVATION_SPACE_CANDIDATES: tuple[str, ...] = (
    " ",  # EN SPACE
    " ",  # EM SPACE
    " ",  # THREE-PER-EM SPACE
    " ",  # FOUR-PER-EM SPACE
    " ",  # SIX-PER-EM SPACE
    " ",  # FIGURE SPACE
    " ",  # PUNCTUATION SPACE
    " ",  # THIN SPACE
    " ",  # HAIR SPACE
    " ",  # MEDIUM MATHEMATICAL SPACE
)

# Longest composition the search considers. Three characters already reaches
# sub-pixel error against every suffix the register tables emit; a longer
# search would only add more candidate space characters to a label's
# rendered text for a precision gain nobody could see.
_MAX_RESERVATION_COMPOSITION_LENGTH = 3

# The residual (in em, i.e. measure(..., font_size=1.0) units) below which a
# composed reservation counts as "sub-pixel" -- comfortably under a pixel at
# any axis-label size this codebase actually ships (0.05em is under a pixel
# up to a 20px label, far larger than any axis label renders at). The search
# reaches well under this against every suffix on every vendored tabular
# face measured so far (worst observed: ~0.016em, dbt Serif Oldstyle
# Tabular, which is missing two of the ten candidates); a face that cannot
# reach it genuinely cannot support the tabular-figures reservation
# guarantee, which is the one condition this module raises on.
_SUBPIXEL_RESIDUAL_TOLERANCE_EM = 0.05


def _load_measurer(font_path: str) -> FontMeasurer:
    """Load and cache a FontMeasurer for the given font file."""
    cached = _font_measurers.get(font_path)
    if cached is not None:
        return cached

    try:
        measurer = FontMeasurer(font_path)
        measurer.measure("A", 14.0)  # Probe for real availability.
    except RuntimeError as exc:
        raise RuntimeError(
            f"Precise font measurer unavailable for render-time wrapping ({font_path})"
        ) from exc

    _font_measurers[font_path] = measurer
    return measurer


_face_measurers: dict[str, FontMeasurer] = {}


def measurer_for_face(face: FontFace) -> FontMeasurer:
    """Return the measurer for an mdsvg face, instanced at its weight.

    :func:`_load_measurer` keys on path alone, which is wrong for a variable
    file: the same bytes instanced at two weights have different advances.
    Callers that already hold the face mdsvg will paint from use this, so the
    width a column is sized to comes from the same instance the browser draws.
    """
    key = f"{face.path}|{face.weight}"
    cached = _face_measurers.get(key)
    if cached is not None:
        return cached
    measurer = FontMeasurer(face.path, weight=face.weight)
    _face_measurers[key] = measurer
    return measurer


def get_font_measurer(
    font_family: str | None = None,
    *,
    numeric: bool = False,
) -> FontMeasurer:
    """Return the strict FontMeasurer matching the rendered font family."""
    return _load_measurer(get_font_path(font_family, numeric=numeric))


def get_weighted_font_measurer(font_family: str | None, weight: int) -> FontMeasurer:
    """Return the measurer for ``font_family`` instanced at ``weight``.

    A variable file measured at two weights gives different advances, so a run
    painted at 600 must be measured at 600 or whatever is positioned against it
    will overlap. Keyed on path *and* weight for the same reason
    :func:`measurer_for_face` is — :func:`_load_measurer` keys on path alone.
    """
    path = get_font_path(font_family)
    key = f"{path}|{weight}"
    cached = _face_measurers.get(key)
    if cached is not None:
        return cached
    measurer = FontMeasurer(path, weight=weight)
    measurer.measure("A", 14.0)  # Probe for real availability, as _load_measurer does.
    _face_measurers[key] = measurer
    return measurer


def centered_baseline_offset(font_family: str | None, font_size: float) -> float:
    """How far below a container's vertical center a centered line's baseline sits.

    Centering a line puts its content box — ``ascent + descent`` — astride the
    middle, so the baseline lands ``(ascent - descent) / 2`` below it. The ratio
    is the face's, not a constant: a face with a deep descender carries its
    baseline higher, and the ``0.35`` this replaced was one family's value spread
    across every other by hand.
    """
    measurer = get_font_measurer(font_family)
    return (measurer.ascent_em - measurer.descent_em) / 2 * font_size


def _measurer_for_composition(font_family: str) -> FontMeasurer:
    """The measurer a suffix-field reservation composes and validates
    against: ``font_family``'s own face when it is vendored, the numeric
    stand-in (``get_font_measurer(..., numeric=True)``) otherwise.

    Deliberately does not call ``get_font_measurer(font_family,
    numeric=True)`` for a vendored family. ``get_font_path``'s ``numeric``
    branch always resolves to dbt Sans Tabular (Source Serif aside)
    regardless of the family asked for — correct for the render call sites
    that use it (they want "the face real digits paint from", which the
    shipped registry pins to Sans Tabular today, and must keep pinning
    there — do not "fix" that branch to look at ``font_family``; an earlier
    attempt at that broke those call sites and was reverted). This
    composition is different: it needs to measure the *specific* family the
    tabular-figures guarantee already promised is vendored and tabular, or
    it silently composes against the wrong face's advances (sans widths,
    painted in serif). A vendored family always has a file to measure
    directly; only an unvendored one falls back to the stand-in.
    """
    try:
        face = get_face(font_family)
    except KeyError:
        return get_font_measurer(font_family, numeric=True)
    return _load_measurer(str(face.measure_path))


def _search_space_combo(
    target: float, available: Sequence[str], widths: dict[str, float]
) -> tuple[tuple[str, ...], float]:
    """The composition search shared by ``compose_suffix_reservation`` (a
    trailing pad, sized against a suffix's advance): the combination of up
    to ``_MAX_RESERVATION_COMPOSITION_LENGTH`` characters from ``available``
    whose summed advance is closest to ``target``. Ties prefer fewer
    characters, then combination order, then character order within the
    winning combination -- the loop order below makes that deterministic
    without a separate tie-break step. Returns the winning combo and its
    error (em), letting each caller decide its own tolerance/degrade policy.
    """
    best: tuple[str, ...] = ()
    best_error = math.inf
    for length in range(1, _MAX_RESERVATION_COMPOSITION_LENGTH + 1):
        for combo in itertools.combinations_with_replacement(available, length):
            error = abs(sum(widths[c] for c in combo) - target)
            if error < best_error:
                best_error = error
                best = combo
    return best, best_error


def compose_suffix_reservation(suffix: str, font_family: str) -> str:
    """The trailing padding a non-suffix tick reserves, so its digits still
    line up with a tick that carries ``suffix``.

    Composes up to ``_MAX_RESERVATION_COMPOSITION_LENGTH`` characters from
    ``_RESERVATION_SPACE_CANDIDATES``, searching for the combination whose
    summed advance is closest to ``suffix``'s own advance — not a whole
    number of digit-width (U+2007) units, which cannot express a suffix
    advance that isn't itself a whole number of digits (e.g. " K" measures
    ~1.45 digits; no integer count of one-digit spaces approximates that
    without a visible, non-sub-pixel residual). Ties prefer fewer
    characters, and the search — combination order, then character order
    within the winning combination — is fully deterministic, so the same
    (suffix, font_family) always composes the same string and goldens do
    not churn between runs.

    Candidates are checked via ``FontMeasurer.has_glyph`` rather than
    trusting ``measure()`` — which silently substitutes a placeholder width
    for an unmapped codepoint, exactly the kind of quiet mismeasurement
    this composition exists to eliminate — and a candidate the face does
    not have is simply excluded from the search rather than treated as an
    error: a font can legitimately ship only some of the ten (dbt Serif
    Oldstyle Tabular is missing two). What *does* raise is the search
    itself failing to reach ``_SUBPIXEL_RESIDUAL_TOLERANCE_EM`` with
    whatever candidates the face does have — that is a genuine "this face
    cannot support the reservation guarantee" condition, not an incidental
    glyph gap, and every family the tabular-figures guarantee accepts is
    vendored and under our control, so it is a font-registry defect to fix,
    not a condition to degrade around.

    Returns ``""`` for an empty suffix (nothing to pad against).
    """
    if not suffix:
        return ""
    measurer = _measurer_for_composition(font_family)
    available = [c for c in _RESERVATION_SPACE_CANDIDATES if measurer.has_glyph(c)]
    widths = {c: measurer.measure(c, 1.0) for c in available}
    target = measurer.measure(suffix, 1.0)

    best, best_error = _search_space_combo(target, available, widths)

    if best_error > _SUBPIXEL_RESIDUAL_TOLERANCE_EM:
        raise RuntimeError(
            f"{font_family!r} cannot compose a sub-pixel suffix-field "
            f"reservation for {suffix!r}: the best achievable combination "
            f"from its available space characters is {best_error:.4f}em "
            f"off target (tolerance {_SUBPIXEL_RESIDUAL_TOLERANCE_EM}em). "
            "This face cannot support the tabular-figures reservation "
            "guarantee — a font-registry defect, not something a caller "
            "can work around."
        )

    return "".join(sorted(best)) + RESERVATION_GUARD


def compose_decimal_units(font_family: str) -> tuple[str, str]:
    """Measure the two padding units needed for decimal-point alignment.

    Returns ``(digit_unit, dot_unit)`` where each is the result of
    ``compose_suffix_reservation`` for the digit character ``"0"`` and the
    decimal separator ``"."`` respectively. Both are measured from the actual
    font glyphs -- never bare U+2007/U+2008 literals whose widths cannot be
    guaranteed across all vendored faces.

    Raises ``RuntimeError`` (propagated from ``compose_suffix_reservation``)
    when the font cannot compose a sub-pixel match for either character. This
    is a font-registry defect, not a condition to silently fall back from.
    """
    return (
        compose_suffix_reservation("0", font_family),
        compose_suffix_reservation(".", font_family),
    )


def css_weight_to_axis(weight: str | float) -> float:
    """Convert a CSS ``font-weight`` value to a variable-font ``wght`` axis value."""
    if isinstance(weight, (int, float)):
        return float(weight)
    keyword = _CSS_WEIGHT_KEYWORDS.get(weight.strip().lower())
    if keyword is not None:
        return keyword
    return float(weight)


def _face_for(family: str, style: str, weight: float) -> FontFace | None:
    """Build an mdsvg face for a registry row, instanced at *weight* if variable.

    Returns ``None`` when the family ships no such style — mdsvg then estimates that
    run from the regular face. That is a real loss of precision, but better than
    measuring against a file we do not have.
    """
    try:
        face = get_face(family, style)
    except KeyError:
        return None
    # Only variable faces carry a weight range, and asking a static file to instance
    # a wght axis it does not have is a caller error mdsvg raises on.
    return FontFace(
        path=str(face.measure_path),
        weight=weight if face.weight_range is not None else None,
    )


def markdown_font_faces(font_family: str, style: MdsvgStyle) -> FontFaces:
    """Build the set of faces mdsvg should measure for a render in *style*.

    Takes the whole style rather than loose weights so the measured faces cannot
    drift from the weights that style will paint with.

    Each run is measured against the file it will be painted from rather than scaled
    off the regular face by a constant. That matters more than it sounds: mdsvg's
    fallback ratios assume bold is ~21% wider and italic ~8% wider, while the real
    faces measure bold at ~3% wider and Source Serif italic at ~14% *narrower*.
    """
    primary = registry_family(font_family)
    # mdsvg spells "no explicit weight" as None, which leaves the browser on its
    # default of 400 — so that is the weight we measure.
    text_weight = style.font_weight
    text_axis = css_weight_to_axis(400.0 if text_weight is None else text_weight)
    bold_axis = css_weight_to_axis(style.bold_font_weight)

    regular = _face_for(primary, "normal", text_axis)
    if regular is None:
        # Family is not vendored, so the browser will paint it from a system font we
        # cannot read. get_font_path resolves the same stand-in measurement has always
        # used; there is no better answer available.
        regular = FontFace(path=get_font_path(font_family))

    # Empty heading_font_family means the heading rule inherits font_family (mdsvg's
    # own contract on the field), so the heading face is the same font file as
    # `regular` — but still instanced at its own heading_axis (heading_font_weight),
    # not text_axis, so it is not simply `regular` again. Passing it through anyway
    # keeps this branch symmetric with the family-override case rather than
    # special-casing "no override" as `heading=None`.
    heading_family = style.heading_font_family or font_family
    heading_axis = css_weight_to_axis(style.heading_font_weight)
    heading_primary = registry_family(heading_family)
    heading = _face_for(heading_primary, "normal", heading_axis)
    if heading is None:
        heading = FontFace(path=get_font_path(heading_family))

    return FontFaces(
        regular=regular,
        bold=_face_for(primary, "normal", bold_axis),
        italic=_face_for(primary, "italic", text_axis),
        bold_italic=_face_for(primary, "italic", bold_axis),
        mono=FontFace(path=str(get_face(SOURCE_CODE_PRO_FONT_FAMILY).measure_path)),
        heading=heading,
    )
