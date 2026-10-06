"""Hint generators for error codes using difflib close-match suggestions.

`available` is `Sequence[str]` — a raise site writes `available=sorted(...)`,
the expression it naturally reaches for. `DbtChartsError.from_code` joins any
`Sequence[str]` for display at message-format time; `_suggest_close_match`
consumes the sequence directly.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence

# Names this release retired, each pointing at the successor that means what it
# meant. Diagnostic data, not vocabulary: nothing in the format system reads
# this, and a board using one of these still fails with ERR-FORMAT-INVALID.
#
# It exists because a value rename cannot be expressed as a schema migration — a
# `value_map` is declared total over its field's domain and `format:` is an open
# string, so every literal d3 spec an author may write would hard-fail a
# migration that has no business touching it. This hint is the whole migration
# path instead.
#
# Fuzzy matching is actively wrong for a rename: the nearest string to
# `currency_compact` is `currency_whole`, which compiles clean and silently
# drops compaction, so an author who followed that hint would get a changed
# render and no error at all.
#
# Drop an entry once boards predating its rename are no longer in the wild.
RETIRED_FORMAT_SUCCESSORS: dict[str, str] = {
    "currency_compact": "currency",
    "compact": "number",
    "number_default": "number",
}

# The categorical-variant companion spellings one release retired (a shipped
# `<family>-<suffix>.yml` file, or a `style.palettes.category_<suffix>` role)
# -> the literal `Variant` word (core/colors.py) that replaced them: `dark`
# and `light` keep their name, `ghost` becomes `pale`, `ink` becomes `deep`.
# Shared by palette.py's live "was retired" hint and
# migrations/versions/v0_9_0.py's migration value-map, so the two can't
# drift out of step with each other -- each import this rather than keep
# its own copy.
RETIRED_VARIANT_SUFFIX_SUCCESSORS: dict[str, str] = {
    "dark": "dark",
    "light": "light",
    "ghost": "pale",
    "ink": "deep",
}

# The two families that shipped literal `<family>-<suffix>.yml` companion
# files, and the one role prefix (`category_dark`, ... in `style.palettes`)
# that named them by role -- both retired in the same release. Also shared
# by palette.py and v0_9_0.py; see `RETIRED_VARIANT_SUFFIX_SUCCESSORS`.
RETIRED_VARIANT_FAMILIES: tuple[str, ...] = ("vivid-10", "editorial-10")
RETIRED_VARIANT_ROLE_PREFIX = "category"

_RETIRED_SUFFIXES = "|".join(RETIRED_VARIANT_SUFFIX_SUCCESSORS)
# One pattern per retired *whole-token* shape, each capturing (suffix, tail)
# with `tail` being the segment that carries across unchanged. The alias and
# slot tails are a single segment on purpose: a retired role followed by two
# dotted segments (`category_dark.blue.dark`) was never a spelling the old
# grammar accepted, and respelling it would name a token that cannot resolve.
_RETIRED_TOKEN_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    # `category_dark[2]` -> `category[2].dark`
    (
        re.compile(rf"^{RETIRED_VARIANT_ROLE_PREFIX}_({_RETIRED_SUFFIXES})\[(\d+)\]$"),
        RETIRED_VARIANT_ROLE_PREFIX + "[{tail}].{variant}",
    ),
    # `category_dark.blue` -> `category.blue.dark`
    (
        re.compile(
            rf"^{RETIRED_VARIANT_ROLE_PREFIX}_({_RETIRED_SUFFIXES})"
            r"\.([A-Za-z][A-Za-z0-9_-]*)$"
        ),
        RETIRED_VARIANT_ROLE_PREFIX + ".{tail}.{variant}",
    ),
    # `category_dark` -> `category.dark` (a whole-list role reference)
    (
        re.compile(rf"^{RETIRED_VARIANT_ROLE_PREFIX}_({_RETIRED_SUFFIXES})()$"),
        RETIRED_VARIANT_ROLE_PREFIX + ".{variant}",
    ),
)
_RETIRED_FAMILY_NAMES = "|".join(re.escape(f) for f in RETIRED_VARIANT_FAMILIES)
_RETIRED_FAMILY_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    # `vivid-10-dark.3` -> `vivid-10.3.dark`
    (
        re.compile(rf"^({_RETIRED_FAMILY_NAMES})-({_RETIRED_SUFFIXES})\.(\d+)$"),
        "{family}.{tail}.{variant}",
    ),
    # `vivid-10-dark:4` / `vivid-10-dark:4_r` -> `vivid-10:4.dark` -- the
    # whole-list `:N`/`_r` shorthand rides along, after the family and
    # before the variant, the order `palette()` parses.
    (
        re.compile(
            rf"^({_RETIRED_FAMILY_NAMES})-({_RETIRED_SUFFIXES})(:\d+(?:_r)?|_r)$"
        ),
        "{family}{tail}.{variant}",
    ),
    # `vivid-10-dark` -> `vivid-10.dark` (a whole-list palette name)
    (
        re.compile(rf"^({_RETIRED_FAMILY_NAMES})-({_RETIRED_SUFFIXES})()$"),
        "{family}.{variant}",
    ),
)


def respell_retired_variant_token(token: str) -> str | None:
    """The third-segment spelling that replaced retired *token*, or ``None``.

    The single transformation both consumers of the retired companion
    spellings share: ``versions/v0_9_0.py`` builds its migration value-map
    from it, and ``palette.py``'s live "was retired" hint names its result --
    so the replacement the migration writes and the one the error message
    suggests can never differ. ``None`` means *token* is not one of the
    closed set of retired shapes (a current spelling, a continuous palette's
    own live ``-dark`` fork, or a mixed old/new spelling the old grammar never
    accepted) and gets no successor.
    """
    for pattern, template in _RETIRED_TOKEN_SHAPES:
        match = pattern.match(token)
        if match:
            suffix, tail = match.groups()
            return template.format(
                tail=tail, variant=RETIRED_VARIANT_SUFFIX_SUCCESSORS[suffix]
            )
    for pattern, template in _RETIRED_FAMILY_SHAPES:
        match = pattern.match(token)
        if match:
            family, suffix, tail = match.groups()
            return template.format(
                family=family,
                tail=tail,
                variant=RETIRED_VARIANT_SUFFIX_SUCCESSORS[suffix],
            )
    return None


def _suggest_close_match(value: str, available: Sequence[str]) -> str | None:
    if isinstance(available, str):
        raise TypeError(
            "available must be a Sequence[str] of candidate names, not a bare "
            "str (which is itself a Sequence[str] of characters and would "
            "silently score character-by-character) — pass "
            "available=['a', 'b'] instead of a joined string."
        )
    candidates = [c.strip() for c in available if c.strip()]
    if not candidates:
        return None
    matches = difflib.get_close_matches(value, candidates, n=1, cutoff=0.5)
    if matches:
        return f"Did you mean {matches[0]!r}?"
    return None


def suggest_close_source(
    source: str,
    available: Sequence[str] = (),
    **_kwargs: object,
) -> str | None:
    """Return a 'Did you mean X?' hint for unknown source names."""
    return _suggest_close_match(source, available)


def suggest_close_theme(
    theme: str,
    available: Sequence[str] = (),
    **_kwargs: object,
) -> str | None:
    """Return a 'Did you mean X?' hint for an unknown theme name."""
    return _suggest_close_match(theme, available)


def suggest_close_extends(
    entry: str,
    available: Sequence[str] = (),
    **_kwargs: object,  # type-state: object_annotation — hint_generator is called with every field of the code; the rest are heterogeneous and unread
) -> str | None:
    """Return a 'Did you mean X?' hint for an unresolvable `extends:` entry."""
    return _suggest_close_match(entry, available)


def suggest_close_format(
    spec: str,
    available: Sequence[str] = (),
    **_kwargs: object,
) -> str | None:
    """Return a 'Did you mean X?' hint for an unresolvable format spec.

    A retired name gets its recorded successor rather than a fuzzy match — see
    ``RETIRED_FORMAT_SUCCESSORS`` for why the nearest string is the wrong answer
    for a rename.
    """
    successor = RETIRED_FORMAT_SUCCESSORS.get(spec)
    # Only when the successor is legal *here*: `available` is scoped to the
    # slot's own half of the vocabulary, so offering a number name to a
    # `time_format:` slot would trade a fuzzy wrong answer for a confident one.
    # An empty pool means no name is legal — the `style.formats` alias-target
    # check passes one, and a predefined successor there re-raises on the very
    # next compile.
    if successor is not None and successor in available:
        return f"{spec!r} was renamed to {successor!r}."
    return _suggest_close_match(spec, available)


def suggest_close_palette(
    name: str,
    available: Sequence[str] = (),
    # ERR-PALETTE-UNKNOWN's remaining field is `field_path`, a str.
    **_kwargs: str,
) -> str | None:
    """Return a hint for an unknown palette or palette role.

    A retired companion spelling gets its recorded successor rather than a
    fuzzy match -- the same refusal ``suggest_close_format`` makes, for the
    same reason: the nearest shipped name to ``vivid-10-dark`` is
    ``vivid-10``, which compiles clean and silently drops the darkening the
    author wrote it for. This is the compile-time diagnostic's only route to
    that successor for a slot no migration reaches (a KPI ``background``
    scale palette), so silence here would be the failure mode.
    """
    successor = respell_retired_variant_token(name)
    if successor is not None:
        return f"{name!r} was retired; use {successor!r} instead."
    return _suggest_close_match(name, available)


def suggest_close_column(
    column_name: str,
    available: Sequence[str] = (),
    **_kwargs: object,  # type-state: object_annotation — hint_generator is called with every field of the code; the rest are heterogeneous and unread
) -> str | None:
    """Return a 'Did you mean X?' hint for a column a model no longer produces."""
    return _suggest_close_match(column_name, available)


def suggest_close_ref(
    ref_name: str,
    available: Sequence[str] = (),
    **_kwargs: object,
) -> str | None:
    """Return a 'Did you mean X?' hint for an unknown dbt ref() target."""
    return _suggest_close_match(ref_name, available)


def suggest_close_source_table(
    source_name: str,
    table_name: str,
    available: Sequence[str] = (),
    **_kwargs: object,
) -> str | None:
    """Return a 'Did you mean X?' hint for an unknown dbt source() reference."""
    return _suggest_close_match(f"{source_name}.{table_name}", available)
