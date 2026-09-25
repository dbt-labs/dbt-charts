"""Palette resolver — role indirection over typed palette records.

Stage: COMPILE / RENDER
Purpose: Resolve palette names and color tokens to sRGB hex stops.

Entry points
------------
    palette(name, *, surface=None, steps=None, reverse=False) -> list[str]
    color(token) -> str
    color_from_theme(token, *, palettes, roles=None) -> str
    resolve_alias_chain(key, aliases, *, colors) -> str
    palette_metadata(name) -> dict
    list_palettes(family=None) -> list[str]
    select_default_palette(data_shape) -> str

YAML palette data is loaded from ``dbt_charts/core/defaults/palettes/<directory>/``.
Sequential and diverging palettes use a hand-authored ``<name>.yml`` spine
with an 11-stop ``colors:`` array. Downsampling operates directly on the spine.

Palette YAML shape (unified):
    name: str
    colors: list[str] | None  — ordered hex stops for [N] bracket access
    aliases: dict[str, str|int] | None — terminal hex, alias chain, or 1-indexed int
    extends: str | None  — inherit alias graph from a parent palette
    description: str | None

Role-indirection grammar:
    chrome.ink         → theme.palettes[chrome] → palette → resolve alias "ink"
    category[1]        → theme.palettes[category] → palette → colors[0] (1-indexed)
    single_series[1]   → theme's single_series_palette[0] (1-indexed; not a
                          palettes: role — the list is authored directly)
    ink                → theme.roles[ink] → recurse as role.alias

surface="table" (sequential and diverging only)
-----------------------------------------------
    Produces a WCAG AA–safe sub-palette for use as table cell backgrounds.
    The algorithm:
      1. Binary-search the OKLCH-interpolated spine for the exact t where
         contrast vs #222222 crosses 4.5:1 (no dense LUT required).
      2. Generate ``steps`` stops evenly from the light end of the spine to
         the boundary t.
    The boundary is found by evaluating the continuous OKLCH curve, so it
    does not snap to a spine index.
"""

from __future__ import annotations

import difflib
import functools
import re
from collections.abc import Mapping, Sequence
from importlib.resources import files
from typing import Any, Literal

import yaml

from dbt_charts.core.colors import (
    InvalidColorError,
    composite_over,
    css_named_color_to_hex,
    ensure_readable_ink,
    hex_to_oklch,
    is_deferred_color_reference,
    is_light_canvas,
    is_sanitizable_color,
    oklch_to_hex,
    parse_css_color,
    relative_luminance,
    rgb01_to_hex,
    wcag_contrast,
)
from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.config import ChartRenderingConfig
from dbt_charts.core.compile.models.palette import Palette

# ============================================================================
# Exceptions
# ============================================================================


class UnknownPaletteError(ValueError):
    """Raised when a palette name doesn't match any shipped palette."""


class UnknownColorError(ValueError):
    """Raised when ``color()`` token doesn't resolve."""


class CategoricalOverrequestError(ValueError):
    """Raised when ``steps=N`` exceeds ``len(stops)`` for categorical/scaffold."""


class SurfaceUnsupportedError(ValueError):
    """Raised when ``surface=`` is passed for a family that doesn't carve."""


class ToneAsPaletteError(ValueError):
    """Raised when a tone palette name is passed to ``palette()`` instead
    of ``color()``."""


# ============================================================================
# Module state
# ============================================================================

_PALETTES_DIR = files("dbt_charts.core") / "defaults" / "palettes"
_FAMILIES: tuple[str, ...] = (
    "sequential",
    "diverging",
    "categorical",
    "scaffold",
    "tone",
)
# Index: {name: {"family": ..., "path": Path}}
_index: dict[str, dict[str, Any]] | None = None

# Cache for loaded spine YAMLs (keyed by palette name).
_spine_cache: dict[str, Palette] = {}


# Known anti-patterns (§10).
_HARD_FAIL_NAMES: frozenset[str] = frozenset({"jet", "rainbow", "hsv"})

# RdYlGn/parula resolve (for migration paths) but emit a warning.
# The mapped substitute is the nearest dbt charts palette so dashboards don't crash
# when users encounter these names from prior tools. Warning text names
# the substitution explicitly.
_WARN_ALIASES: dict[str, str] = {
    "RdYlGn": "dbt-div-crimson-green",
    "parula": "dbt-seq-blue",
}


# ============================================================================
# OKLCH math + WCAG helpers for surface="table" carving
# ============================================================================

# dbt charts body text ink — used as the contrast reference for table cell backgrounds.
WCAG_TABLE_BODY = "#222222"
# WCAG AA minimum contrast ratio for normal text.
_WCAG_TABLE_MIN = 4.5


# A table cell's body text is either clearly dark (light theme) or clearly
# light (dark theme); this threshold sits safely in the gap between the two.
_DARK_CANVAS_TEXT_LUMA = 0.5


def _is_dark_canvas_text(text_color: str) -> bool:
    """True when the table body text is light — i.e. it sits on a dark canvas.

    The dark-canvas signal for the table palette swap, read from the same
    relative luminance the WCAG carve already computes — no ResolvedStyle flag.
    """
    return relative_luminance(text_color) >= _DARK_CANVAS_TEXT_LUMA


def _interpolate_oklch_at_t(
    spine_oklch: list[tuple[float, float, float]], t: float
) -> str:
    """Evaluate the OKLCH spine at parameter t ∈ [0, 1] and return sRGB hex.

    Uses shortest-arc hue interpolation between each pair of adjacent spine stops.
    """
    n = len(spine_oklch)
    seg_f = t * (n - 1)
    seg_i = min(n - 2, int(seg_f))
    seg_t = seg_f - seg_i
    L0, C0, H0 = spine_oklch[seg_i]
    L1, C1, H1 = spine_oklch[seg_i + 1]
    L = L0 + seg_t * (L1 - L0)
    C = C0 + seg_t * (C1 - C0)
    d = ((H1 - H0 + 540) % 360) - 180
    H = (H0 + seg_t * d) % 360
    return oklch_to_hex(L, C, H)


def _wcag_boundary_t(
    spine_oklch: list[tuple[float, float, float]],
    t_pass: float,
    t_fail: float,
    text_color: str,
    iters: int = 50,
) -> float:
    """Binary-search for the WCAG-contrast boundary along the OKLCH spine.

    ``t_pass`` must be a position where contrast vs ``text_color`` ≥
    _WCAG_TABLE_MIN; ``t_fail`` must be a position where it is below.  Returns
    the last-passing t (precision ~1/2^50 in t, well below any visible color
    difference).
    """
    for _ in range(iters):
        t_mid = (t_pass + t_fail) / 2
        if (
            wcag_contrast(text_color, _interpolate_oklch_at_t(spine_oklch, t_mid))
            >= _WCAG_TABLE_MIN
        ):
            t_pass = t_mid
        else:
            t_fail = t_mid
    return t_pass


def _table_surface_seq(
    source: list[str], steps: int, text_color: str = WCAG_TABLE_BODY
) -> list[str]:
    """Return a WCAG-safe table palette for a sequential source spine.

    The cell text sits on the fill, so the fills are carved to the sub-range of
    the spine that contrasts with ``text_color``. Which end is safe depends on
    the text: dark text (light themes) keeps the light end and truncates the
    dark end; light text (dark themes) keeps the dark end and truncates the
    light end — the symmetric mirror. Either way the pop end that can't hold the
    text is dropped, exactly as light themes already drop their dark stops.

    Output stays in source order (index 0 = domain low). Raises ``ValueError``
    if neither end of the spine meets the threshold against ``text_color``.
    """
    spine_oklch = [hex_to_oklch(h) for h in source]
    c0 = wcag_contrast(text_color, _interpolate_oklch_at_t(spine_oklch, 0.0))
    c1 = wcag_contrast(text_color, _interpolate_oklch_at_t(spine_oklch, 1.0))
    if max(c0, c1) < _WCAG_TABLE_MIN:
        raise ValueError(
            f"No stop of sequential palette meets WCAG {_WCAG_TABLE_MIN}:1 "
            f"against table text {text_color!r}."
        )

    # Anchor on the higher-contrast end; extend toward the other until it fails.
    if c0 >= c1:
        t_lo = 0.0
        t_hi = (
            1.0
            if c1 >= _WCAG_TABLE_MIN
            else _wcag_boundary_t(spine_oklch, 0.0, 1.0, text_color)
        )
    else:
        t_hi = 1.0
        t_lo = (
            0.0
            if c0 >= _WCAG_TABLE_MIN
            else _wcag_boundary_t(spine_oklch, 1.0, 0.0, text_color)
        )

    if steps == 1:
        return [_interpolate_oklch_at_t(spine_oklch, (t_lo + t_hi) / 2.0)]
    return [
        _interpolate_oklch_at_t(spine_oklch, t_lo + i * (t_hi - t_lo) / (steps - 1))
        for i in range(steps)
    ]


def _table_surface_div(
    source: list[str], steps: int, text_color: str = WCAG_TABLE_BODY
) -> list[str]:
    """Return a WCAG-safe table palette for a diverging source spine.

    Anchored on the neutral midpoint (t=0.5), each arm is truncated toward its
    pole until the fill can no longer hold ``text_color``. Light themes (dark
    text) need a light midpoint + truncate the dark poles; dark themes (light
    text) need a dark midpoint + truncate the bright poles — same algorithm,
    the text color decides which stops survive.

    For even ``steps`` the midpoint is excluded so stops flank it symmetrically.
    Raises ``ValueError`` if the midpoint fails the threshold against ``text_color``.
    """
    spine_oklch = [hex_to_oklch(h) for h in source]

    mid_hex = _interpolate_oklch_at_t(spine_oklch, 0.5)
    if wcag_contrast(text_color, mid_hex) < _WCAG_TABLE_MIN:
        raise ValueError(
            f"Midpoint of diverging palette fails WCAG {_WCAG_TABLE_MIN}:1 "
            f"against table text {text_color!r}."
        )

    # Each arm's pole may fail; the midpoint (t=0.5) passes by the guard above.
    left_hex = _interpolate_oklch_at_t(spine_oklch, 0.0)
    t_left = (
        0.0
        if wcag_contrast(text_color, left_hex) >= _WCAG_TABLE_MIN
        else _wcag_boundary_t(spine_oklch, 0.5, 0.0, text_color)
    )

    right_hex = _interpolate_oklch_at_t(spine_oklch, 1.0)
    t_right = (
        1.0
        if wcag_contrast(text_color, right_hex) >= _WCAG_TABLE_MIN
        else _wcag_boundary_t(spine_oklch, 0.5, 1.0, text_color)
    )

    if steps == 1:
        return [_interpolate_oklch_at_t(spine_oklch, 0.5)]

    if steps % 2 == 0:
        # Even: place n//2 stops on each arm, excluding the midpoint.
        half = steps // 2
        left_ts = [t_left + i * (0.5 - t_left) / half for i in range(half)]
        right_ts = [0.5 + (i + 1) * (t_right - 0.5) / half for i in range(half)]
        ts = left_ts + right_ts
    else:
        ts = [t_left + i * (t_right - t_left) / (steps - 1) for i in range(steps)]

    return [_interpolate_oklch_at_t(spine_oklch, t) for t in ts]


def _build_index() -> dict[str, dict[str, Any]]:
    """Scan the palettes directory and build ``name → (family, path)`` map."""
    idx: dict[str, dict[str, Any]] = {}
    for family in _FAMILIES:
        fam_dir = _PALETTES_DIR / family
        if not fam_dir.is_dir():
            continue
        for yml in sorted(
            (p for p in fam_dir.iterdir() if p.name.endswith(".yml")),
            key=lambda p: p.name,
        ):
            data = yaml.safe_load(yml.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "name" not in data:
                continue
            idx[data["name"]] = {"family": family, "path": yml}
    return idx


def _get_index() -> dict[str, dict[str, Any]]:
    global _index
    if _index is None:
        _index = _build_index()
    return _index


def _load_spine_raw(name: str) -> Palette:
    """Load and validate the YAML for ``name`` without resolving ``extends:``."""
    entry = _get_index().get(name)
    if entry is None:
        raise UnknownPaletteError(_unknown_palette_message(name))
    data = yaml.safe_load(entry["path"].read_text(encoding="utf-8"))
    return Palette.model_validate(data)


def _load_spine_merged(name: str, _seen: frozenset[str] | None = None) -> Palette:
    """Load palette ``name`` with ``extends:`` inheritance applied.

    Merge rules:
    - ``colors:`` — child replaces parent array wholesale.
    - ``aliases:`` — deep-merge; child keys override parent keys.
      Alias values are kept as-is (integers stay integers) so that integer
      refs resolve against the *child's* colors array at resolve time.
    - ``name``, ``description``, ``type`` — child wins.
    - ``extends`` key is stripped from the merged result.
    - Cycle → ValueError with clear message.
    - Unknown parent → UnknownPaletteError with clear message.
    """
    seen = _seen or frozenset()
    if name in seen:
        chain = " → ".join(sorted(seen) + [name])
        raise ValueError(f"palette extends cycle detected: {chain}")

    child = _load_spine_raw(name)
    if child.extends is None:
        return child

    parent_name = child.extends
    parent = _load_spine_merged(parent_name, seen | {name})

    # Merge: parent aliases as base, child aliases override.
    merged_aliases: dict[str, str | int] = dict(parent.aliases or {})
    merged_aliases.update(child.aliases or {})

    # child.colors replaces parent wholesale (or is None if child omits it).
    merged = Palette(
        name=child.name,
        extends=None,  # stripped
        colors=child.colors if child.colors is not None else parent.colors,
        aliases=merged_aliases if merged_aliases else None,
        description=(
            child.description if child.description is not None else parent.description
        ),
        design_notes=(
            child.design_notes
            if child.design_notes is not None
            else parent.design_notes
        ),
        r8_validation=child.r8_validation,
    )
    return merged


def _load_spine(name: str) -> Palette:
    if name in _spine_cache:
        return _spine_cache[name]
    spine = _load_spine_merged(name)
    _spine_cache[name] = spine
    return spine


def _unknown_palette_message(name: str) -> str:
    idx = _get_index()
    candidates = list(idx.keys())
    suggestions = difflib.get_close_matches(name, candidates, n=1, cutoff=0.6)
    if suggestions:
        return f"unknown palette '{name}'. Did you mean '{suggestions[0]}'?"
    return f"unknown palette '{name}'"


# ============================================================================
# Parsing shorthand — "name:N_r"
# ============================================================================


def _parse_palette_reference(ref: str) -> tuple[str, int | None, bool]:
    """Parse ``name:N_r`` shorthand into (name, steps, reverse).

    Examples:
        "dbt-seq-blue"      → ("dbt-seq-blue", None, False)
        "dbt-seq-blue:5"    → ("dbt-seq-blue", 5, False)
        "dbt-seq-blue_r"    → ("dbt-seq-blue", None, True)
        "dbt-seq-blue:5_r"  → ("dbt-seq-blue", 5, True)
    """
    if not ref:
        raise ValueError("palette reference must be a non-empty string")
    body = ref
    reverse = False
    if body.endswith("_r"):
        reverse = True
        body = body[:-2]
    steps: int | None = None
    if ":" in body:
        name, _, steps_str = body.rpartition(":")
        if not steps_str or not steps_str.lstrip("-").isdigit():
            raise ValueError(
                f"palette reference steps must be a positive integer, got {ref!r}"
            )
        steps = int(steps_str)
        if steps <= 0:
            raise ValueError(f"steps must be positive, got {steps}")
    else:
        name = body
    if not name:
        raise ValueError(f"palette reference is missing name: {ref!r}")
    return (name, steps, reverse)


# ============================================================================
# Downsample
# ============================================================================


def _downsample(
    stops: list[str], n: int, skip_midpoint_on_even: bool = False
) -> list[str]:
    """Pick ``n`` evenly-spaced stops from the input list.

    For diverging palettes with even ``n`` and ``skip_midpoint_on_even=True``,
    the midpoint (index ``len/2``) is skipped so stops flank it symmetrically.
    """
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    L = len(stops)
    if n >= L:
        return list(stops)
    if n == 1:
        return [stops[L // 2]]

    if skip_midpoint_on_even and n % 2 == 0 and L % 2 == 1:
        midpoint = L // 2
        half = n // 2
        if half == 1:
            # Only two stops requested — just the endpoints flanking the midpoint.
            return [stops[0], stops[-1]]
        # Split range into [0..midpoint-1] and [midpoint+1..L-1]; pick `half`
        # evenly-spaced from each, preserving endpoints.
        left = [round(i * (midpoint - 1) / (half - 1)) for i in range(half)]
        right = [
            midpoint + 1 + round(i * (L - 1 - midpoint - 1) / (half - 1))
            for i in range(half)
        ]
        return [stops[i] for i in left + right]

    # Even spacing with endpoints preserved.
    indices = [round(i * (L - 1) / (n - 1)) for i in range(n)]
    return [stops[i] for i in indices]


# ============================================================================
# Public API — palette()
# ============================================================================


def palette(
    name: str,
    *,
    surface: Literal["default", "table"] | None = None,
    steps: int | None = None,
    reverse: bool = False,
    text_color: str = WCAG_TABLE_BODY,
) -> list[str]:
    """Resolve a palette name to a list of sRGB hex stops.

    See module docstring for full parameter reference. Raises
    ``UnknownPaletteError``, ``CategoricalOverrequestError``,
    ``SurfaceUnsupportedError``, or ``ToneAsPaletteError`` as appropriate.

    ``text_color`` applies only to ``surface="table"``: the carve keeps stops
    that hold that body-text color (defaults to the dark ink). It has no effect
    on chart-fill (``surface`` default) resolution.
    """
    # Shorthand parsing from strings.
    if ":" in name or name.endswith("_r"):
        parsed_name, parsed_steps, parsed_rev = _parse_palette_reference(name)
        name = parsed_name
        if steps is None:
            steps = parsed_steps
        reverse = reverse or parsed_rev

    # Anti-patterns.
    if name in _HARD_FAIL_NAMES:
        raise UnknownPaletteError(
            f"palette '{name}' is a known perceptual anti-pattern and is not "
            "shipped. See docs/guides/palette-resolver.md#anti-patterns"
        )
    if name in _WARN_ALIASES:
        name = _WARN_ALIASES[name]

    entry = _get_index().get(name)
    if entry is None:
        raise UnknownPaletteError(_unknown_palette_message(name))

    family = entry["family"]

    if family == "tone":
        raise ToneAsPaletteError(
            f"'{name}' is a tone palette. Use color('{name}.solid') etc., "
            "not palette()."
        )

    if family in ("categorical", "scaffold"):
        return _resolve_discrete(name, family, surface, steps, reverse)

    # Dark-canvas table swap: on a dark canvas (light cell text) a pinned
    # sequential/diverging table palette resolves to its <name>-dark twin, so the
    # carve has a dark neutral that holds the light text — otherwise the light
    # palette's near-white neutral fails WCAG and the whole chart drops out.
    # Mirrors the categorical light/dark <name>-dark convention; the trigger is
    # the text_color already threaded here, so no dark-canvas flag is needed.
    # Light themes (dark text) never trigger; scoped to surface="table".
    if (
        surface == "table"
        and not name.endswith("-dark")
        and _is_dark_canvas_text(text_color)
        and _has_continuous_dark_companion(name)
    ):
        name = f"{name}-dark"
        family = _get_index()[name]["family"]

    # sequential or diverging.
    return _resolve_continuous(name, family, surface, steps, reverse, text_color)


def resolve_palette_ref(ref: str, palettes: Mapping[str, str]) -> str:
    """Substitute a leading theme palette role in a palette reference.

    ``palette: category`` follows ``palettes[category]`` to whatever file the
    active theme binds, so a value tracks a theme switch instead of pinning one
    palette. The whole-palette counterpart to ``color_from_theme()``'s
    ``category[2]``, which indirects a single color through the same map.

    Only the name is substituted; the ``:N``/``_r`` shorthand rides along. A ref
    naming no role is returned unchanged, so a catalog name still means itself.
    """
    name, _, _ = _parse_palette_reference(ref)
    target = palettes.get(name)
    if target is None:
        return ref
    # `_parse_palette_reference` only strips suffixes, so `name` prefixes `ref`.
    return target + ref[len(name) :]


def resolve_palette_alias(name: str) -> tuple[list[str], str | None]:
    """Resolve a palette name, reporting the anti-pattern name it replaced.

    ``palette()`` itself resolves anti-pattern aliases (see ``_WARN_ALIASES``)
    silently — the WARN-PALETTE-UNSUPPORTED nudge is a render-stage detector
    over the compiled board, not an inline emit. This is the single place
    that detects the alias so a compile-time model (``CategoricalColorStyle``)
    can retain the originally-authored name for that detector.

    Returns ``(resolved_stops, requested_name)``: ``requested_name`` is the
    shorthand-stripped ``name`` when it is a known anti-pattern alias, else
    ``None``.
    """
    parsed_name = name
    if ":" in parsed_name or parsed_name.endswith("_r"):
        parsed_name, _, _ = _parse_palette_reference(parsed_name)
    requested = parsed_name if parsed_name in _WARN_ALIASES else None
    return palette(name), requested


def is_hard_fail_name(name: str) -> bool:
    """True for a perceptual anti-pattern this resolver refuses outright.

    Public so the authored-model validators can tell "unknown, so possibly a
    theme role" apart from "known-bad, never a role" — deferring the latter
    would silently swallow a deliberate gate.
    """
    return name in _HARD_FAIL_NAMES


def is_warn_alias(name: str) -> bool:
    """True when ``name`` is a known anti-pattern alias.

    Every value ``resolve_palette_alias`` reports is by construction one of
    these, so this is what separates provenance the compiler computed from a
    value a board author fabricated.
    """
    return name in _WARN_ALIASES


def substitute_for_alias(requested_alias_palette: str) -> str:
    """Return the palette name substituted for a known anti-pattern alias.

    ``requested_alias_palette`` is one of ``_WARN_ALIASES``'s keys (the name
    ``resolve_palette_alias`` stripped down to before substitution). Used by
    the WARN-PALETTE-UNSUPPORTED render detector to name the substitute
    palette, not its resolved hex stops, in the user-facing message.
    """
    return _WARN_ALIASES[requested_alias_palette]


def _resolve_discrete(
    name: str,
    family: str,
    surface: str | None,
    steps: int | None,
    reverse: bool,
) -> list[str]:
    if surface is not None:
        raise SurfaceUnsupportedError(
            f"palette '{name}' (family={family}) does not support surface variants"
        )
    spine = _load_spine(name)

    if family == "categorical":
        stops = list(spine.colors)  # type: ignore[arg-type]
    else:  # scaffold — new format: colors: + aliases:
        if spine.colors is None:
            raise UnknownPaletteError(
                f"scaffold palette '{name}' is missing 'colors:' array. "
                "Ensure the YAML uses the unified colors:/aliases: shape."
            )
        stops = list(spine.colors)

    if steps is not None:
        if steps > len(stops):
            raise CategoricalOverrequestError(
                f"palette '{name}' has {len(stops)} stops; cannot return {steps}. "
                "Pick a different palette or reduce steps."
            )
        stops = stops[:steps]

    if reverse:
        stops = list(reversed(stops))
    return stops


def _resolve_continuous(
    name: str,
    family: str,
    surface: str | None,
    steps: int | None,
    reverse: bool,
    text_color: str = WCAG_TABLE_BODY,
) -> list[str]:
    spine = _load_spine(name)

    if surface not in (None, "default", "table"):
        raise SurfaceUnsupportedError(
            f"unknown surface variant '{surface}' for palette '{name}'"
        )

    if spine.colors is None:
        raise UnknownPaletteError(
            f"palette '{name}' (family={family}) is missing 'colors:' array"
        )
    source = list(spine.colors)

    if steps is None:
        steps = 11

    if surface == "table":
        if family == "sequential":
            stops = _table_surface_seq(source, steps, text_color)
        else:  # diverging
            stops = _table_surface_div(source, steps, text_color)
    else:
        skip_mid = family == "diverging"
        stops = _downsample(source, steps, skip_midpoint_on_even=skip_mid)

    if reverse:
        stops = list(reversed(stops))
    return stops


# ============================================================================
# Public API — color()
# ============================================================================


def resolve_alias_chain(
    key: str,
    aliases: dict[str, str | int],
    *,
    colors: list[str] | None,
) -> str:
    """Resolve an alias name through the chain to a terminal hex string.

    Alias values may be:
    - A hex string (``#rrggbb``) — terminal; return it.
    - A 1-indexed integer — index into ``colors`` (``colors[n-1]``).
    - Another alias name — recurse with cycle detection.

    Raises ``UnknownColorError`` on unknown alias, out-of-range index,
    missing colors array when an integer index is encountered, or cycle.
    """
    visited: list[str] = []
    current = key
    while True:
        if current in visited:
            raise UnknownColorError(
                f"alias cycle detected: {' → '.join(visited + [current])}"
            )
        visited.append(current)
        if current not in aliases:
            raise UnknownColorError(
                f"alias '{key}' → '{current}' not found in palette aliases. "
                f"Known aliases: {sorted(aliases)}"
            )
        value = aliases[current]
        if isinstance(value, int):
            if colors is None:
                raise UnknownColorError(
                    f"alias '{current}' resolves to slot index {value} "
                    "but palette has no 'colors:' array"
                )
            if value < 1 or value > len(colors):
                raise UnknownColorError(
                    f"alias '{current}' slot index {value} out of range "
                    f"(palette has {len(colors)} stops; 1-indexed)"
                )
            return colors[value - 1]
        if value.startswith("#"):
            return value
        # Must be another alias name — continue walking
        current = value


# Families stored as an ordered stop list, addressable by 1-indexed position.
# Scaffold is here too: it layers named aliases over a stop list, so it answers
# both forms. Tone is excluded — it has names only, no stops.
_STOP_LIST_FAMILIES = frozenset({"categorical", "sequential", "diverging", "scaffold"})


def color(token: str) -> str:
    """Resolve a single color token (``palette.slot``) to an sRGB hex string.

    Supports tone and scaffold (aliases: format) plus every stop-list family
    (categorical, sequential, diverging), which is addressed by 1-indexed
    position.
    For role-indirected tokens (``chrome.ink`` where ``chrome`` is a theme
    palette role), use ``color_from_theme()`` instead.
    """
    if "." not in token:
        raise UnknownColorError(
            f"color token must be dotted 'palette.slot', got {token!r}"
        )
    palette_name, _, slot = token.partition(".")
    entry = _get_index().get(palette_name)
    if entry is None:
        raise UnknownColorError(_unknown_palette_message(palette_name))
    family = entry["family"]
    spine = _load_spine(palette_name)

    # Unified resolver: tone and scaffold both use aliases: dict now.
    if family in ("tone", "scaffold"):
        aliases = spine.aliases or {}
        if slot in aliases:
            return resolve_alias_chain(slot, aliases, colors=spine.colors)
        # Scaffold palettes are ordered stop lists with names layered on top, so
        # an integer slot addresses the stop itself — the same meaning it has in
        # every other stop-list family. Tone has no stop list, only names.
        if family == "tone" or not slot.lstrip("-").isdigit():
            raise UnknownColorError(
                f"palette '{palette_name}' has no alias '{slot}'. "
                f"Known aliases: {sorted(aliases)}"
            )

    if family in _STOP_LIST_FAMILIES:
        # Positional slot by integer, 1-indexed (vivid-10.1 is the first
        # stop) — matches the bracket form and scaffold/tone alias integers.
        try:
            idx = int(slot)
        except ValueError as e:
            raise UnknownColorError(
                f"{family} palette '{palette_name}' indexed by integer; got '{slot}'"
            ) from e
        stops = list(spine.colors)  # type: ignore[arg-type]
        if idx < 1:
            raise UnknownColorError(
                f"{family} palette '{palette_name}' slot {idx} must be 1-indexed (≥ 1)"
            )
        if idx > len(stops):
            raise UnknownColorError(
                f"{family} palette '{palette_name}' index {idx} out of range "
                f"[1, {len(stops)}]"
            )
        return stops[idx - 1]

    raise UnknownColorError(
        f"palette '{palette_name}' (family={family}) does not support color() access"
    )


# ============================================================================
# Public API — color_from_theme() role indirection
# ============================================================================


def color_from_theme(
    token: str,
    *,
    palettes: Mapping[str, str],
    roles: dict[str, str] | None = None,
    single_series_palette: Sequence[str] | None = None,
    _visited_roles: frozenset[str] | None = None,
) -> str:
    """Resolve a role-indirected color token against theme palettes and roles.

    Token grammar:
        ``chrome.ink``        — role.alias: look up palettes[role], resolve alias
        ``category[1]``       — role[N]: look up palettes[role], colors[N-1] (1-indexed)
        ``single_series[1]``  — the active theme's single-series ink list, N-1
            (1-indexed); not a ``palettes:`` role — themes author
            ``single_series_palette`` as a literal, already-cascaded list
            (``style.charts.color.categorical.single_series_palette``), so
            this indexes the caller-supplied list directly rather than a
            palette file
        ``ink``                — bare name: look up roles[ink], recurse

    Args:
        token: Color token string in one of the four grammar forms.
        palettes: Theme ``palettes:`` block — maps role names to palette names.
        roles: Theme ``roles:`` block — maps bare alias names to role.alias tokens.
        single_series_palette: The active theme's resolved
            ``single_series_palette`` (a plain one-series chart's ink list),
            for the ``single_series[N]`` form. ``None`` when the caller has no
            such context (e.g. theme-load time, before the cascade exists).
        _visited_roles: Internal cycle-detection set (do not pass from call sites).

    Returns:
        Resolved sRGB hex string.

    Raises:
        UnknownColorError: Token is unresolvable (unknown role, missing alias,
            out-of-range index, cycle, or missing colors array).
    """
    # Bracket form: role[N] / single_series[N]
    bracket_match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\[(\d+)\]$", token)
    if bracket_match:
        role, n_str = bracket_match.group(1), bracket_match.group(2)
        n = int(n_str)
        if n < 1:
            raise UnknownColorError(
                f"bracket index must be 1-indexed (≥ 1), got {token!r}"
            )
        if role == "single_series":
            if single_series_palette is None:
                raise UnknownColorError(
                    f"no single-series palette context available for {token!r}"
                )
            if n > len(single_series_palette):
                raise UnknownColorError(
                    f"single-series palette has {len(single_series_palette)} slot(s); "
                    f"requested slot {n} (1-indexed)"
                )
            return single_series_palette[n - 1]
        palette_name = palettes.get(role)
        if palette_name is None:
            raise UnknownColorError(
                f"theme has no palette assigned to role '{role}'. "
                f"Defined roles: {sorted(palettes)}"
            )
        entry = _get_index().get(palette_name)
        if entry is None:
            raise UnknownColorError(
                f"palette '{palette_name}' (role '{role}') not found in catalog"
            )
        spine = _load_spine(palette_name)
        if spine.colors is None:
            raise UnknownColorError(
                f"palette '{palette_name}' (role '{role}') has no 'colors:' array; "
                "bracket form requires a colors array"
            )
        if n > len(spine.colors):
            raise UnknownColorError(
                f"palette '{palette_name}' has {len(spine.colors)} slot(s); "
                f"requested slot {n} (1-indexed)"
            )
        return spine.colors[n - 1]

    # Dotted form: role.alias
    if "." in token:
        role, _, alias = token.partition(".")
        palette_name = palettes.get(role)
        if palette_name is None:
            raise UnknownColorError(
                f"theme has no palette assigned to role '{role}'. "
                f"Defined roles: {sorted(palettes)}"
            )
        entry = _get_index().get(palette_name)
        if entry is None:
            raise UnknownColorError(
                f"palette '{palette_name}' (role '{role}') not found in catalog"
            )
        spine = _load_spine(palette_name)
        aliases_raw = spine.aliases or {}
        if alias not in aliases_raw:
            raise UnknownColorError(
                f"palette '{palette_name}' (role '{role}') has no alias '{alias}'. "
                f"Known aliases: {sorted(aliases_raw)}"
            )
        return resolve_alias_chain(alias, aliases_raw, colors=spine.colors)

    # Bare name: look up in roles
    effective_roles = roles or {}
    target = effective_roles.get(token)
    if target is None:
        raise UnknownColorError(
            f"unknown color token '{token}': not a dotted palette address, "
            "bracket form, or theme role. "
            f"Defined theme roles: {sorted(effective_roles)}"
        )
    # Cycle detection for bare-name role recursion.
    visited = _visited_roles or frozenset()
    if token in visited:
        raise UnknownColorError(f"theme.roles cycle detected involving '{token}'")
    # Recurse on the resolved target (which must be a dotted or bracket form)
    return color_from_theme(
        target,
        palettes=palettes,
        roles=effective_roles,
        single_series_palette=single_series_palette,
        _visited_roles=visited | {token},
    )


# ============================================================================
# Public API — variant() / label_ink() — literal tiers, canvas-aware ink
# ============================================================================

# A literal tier of a base color: `dark` is never lighter than its base and
# `light` never darker, on every theme. Which variant a role uses on a dark
# canvas is the theme's decision, not the engine's -- see label_ink() for the
# one automatic, canvas-aware derivation.
Variant = Literal["dark", "light", "pale", "deep"]


@functools.lru_cache(maxsize=4096)
def _variant_cached(
    color: str, kind: Variant, config: ChartRenderingConfig.ColorVariantsConfig
) -> str:
    L, C, H = hex_to_oklch(color)
    if kind == "light":
        Ln = L + (1.0 - L) * config.light_k
        # Two competing clamps: capped under the pale band (light_pale_gap),
        # floored at least light_min_gap above base. Above base L 0.80 (at
        # the shipped constants) the two conflict -- the floor wins (max is
        # outermost), so `light` can end up level with or above `pale` for a
        # very light base rather than strictly under it. The floor itself
        # stops at white: an OKLCH L above 1.0 is outside sRGB at every
        # chroma.
        Ln = min(
            1.0,
            max(
                min(Ln, config.pale_l - config.light_pale_gap), L + config.light_min_gap
            ),
        )
        return oklch_to_hex(Ln, C * config.light_chroma, H)
    if kind == "dark":
        # min(L, ...) clamps to darkening only -- unclamped, a base below the
        # pole would move up toward it.
        return oklch_to_hex(min(L, L + (config.dark_pole - L) * config.dark_k), C, H)
    if kind == "pale":
        # k = 1: the band itself is the pole, uniform regardless of base L.
        return oklch_to_hex(config.pale_l, C * config.pale_chroma, H)
    if kind == "deep":
        return oklch_to_hex(min(L, L + (config.deep_pole - L) * config.deep_k), C, H)
    raise ValueError(
        f"unknown variant {kind!r}; expected one of dark, light, pale, deep"
    )


def variant(color: str, kind: Variant) -> str:
    """A literal tier of `color`, no canvas.

    Moves OKLCH lightness a fraction of the way toward a fixed pole (hue
    held, chroma scaled, gamut-clipped): `light` toward white, `dark` toward
    a near-black pole, `pale` onto a flat pale band, `deep` toward a near-
    black pole further than `dark`. `dark` and `deep` are clamped so they
    never come out lighter than `color` itself. Constants live in
    `chart_rendering.color_variants` (`ColorVariantsConfig`).
    """
    return _variant_cached(color, kind, get_chart_rendering().color_variants)


def _label_ink_step(
    color: str, canvas: str, config: ChartRenderingConfig.ColorVariantsConfig
) -> str:
    """The pre-floor move `label_ink` makes: `color`'s dark step, away from
    `canvas`, before the legibility floor is applied. Exposed as its own
    function so callers (including tests) can inspect the move without
    duplicating the arithmetic.

    On a light canvas a base already darker than `dark_pole` would step
    *up*, toward the canvas, so the step is clamped to the base there;
    the floor (`ensure_readable_ink`) is then the only thing that can
    move it. The white pole needs no clamp: no base sits above L 1.0.
    """
    L, C, H = hex_to_oklch(color)
    if is_light_canvas(canvas):
        return oklch_to_hex(min(L, L + (config.dark_pole - L) * config.dark_k), C, H)
    return oklch_to_hex(L + (1.0 - L) * config.dark_k, C, H)


@functools.lru_cache(maxsize=4096)
def _label_ink_cached(
    color: str, canvas: str, config: ChartRenderingConfig.ColorVariantsConfig
) -> str:
    stepped = _label_ink_step(color, canvas, config)
    return ensure_readable_ink(stepped, canvas, config.label_ink_min_contrast)


def label_ink(color: str, canvas: str) -> str:
    """Ink for text painted on `canvas` about a mark of `color`.

    The dark move away from `canvas` (toward `dark_pole` on a light canvas,
    toward white on a dark one), then nudged the rest of the way to legible
    against `canvas` if the move alone doesn't clear the floor
    (`ensure_readable_ink`, floor is `label_ink_min_contrast`). Unlike
    `variant(..., "dark")`, this is the engine's one automatic, canvas-aware
    color derivation -- the contrast floor costs hue separation, which
    belongs on ink an author never sees as a literal color, not on `dark`
    itself.
    """
    return _label_ink_cached(color, canvas, get_chart_rendering().color_variants)


def _try_parse_color(value: str) -> tuple[float, float, float, float] | None:
    """parse_css_color(value), or None when dbt Charts cannot read it.

    No breaking change: an authored color the engine cannot parse (an
    ``oklch()``, a typo, a browser-only form) is never an error here --
    every caller of this falls back to treating the value as if it
    contributed no ink of its own, the same contract the pre-live-derivation
    companion lookup had for a color with no ``-dark`` twin.
    """
    try:
        return parse_css_color(value)
    except InvalidColorError:
        return None


def ink_canvas(*layers: str) -> str:
    """Composite ``layers`` top-down into a single opaque hex canvas.

    ``layers[0]`` paints on top, ``layers[-1]`` is the base -- each layer
    can be hex, a CSS keyword name, ``transparent``/``none``, or an
    ``rgb()``/``rgba()``/``hsl()``/``hsla()`` function. A layer dbt Charts
    cannot parse is skipped -- it contributes nothing, the same as a fully
    transparent one -- so the result falls through to the next layer down
    and ultimately the theme's own canvas.

    Raises ``ValueError`` ("theme canvas must be opaque") when the fully
    composited result is still not opaque -- unreachable through normal
    authoring, since every caller's bottom layer is the theme's own canvas
    and a layer above it can only raise the composited alpha, never lower
    it below the base.
    """
    composited: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    for layer in reversed(layers):
        parsed = _try_parse_color(layer)
        if parsed is None:
            continue
        composited = composite_over(parsed, composited)
    if composited[3] < 1.0 - 1e-9:
        raise ValueError("theme canvas must be opaque")
    return rgb01_to_hex(*composited[:3])


def mark_ink(color: str, canvas: str) -> str:
    """label_ink() for one mark color.

    A color token or bare identifier that is NOT already a real, parseable
    color defers to ``validate_board_palette_specs()`` instead of resolving
    here -- checked in that order because a CSS keyword name like
    ``white`` is also bare-identifier-shaped. Everything else routes
    through ``parse_css_color``; a fully transparent mark, or one dbt
    Charts cannot read at all, has no ink to derive -- the render paints
    it as authored; there is no ink to derive -- and passes through
    unchanged.
    """
    lowered = color.strip().lower()
    is_real_color = (
        lowered in {"transparent", "none"}
        or is_sanitizable_color(color)
        or css_named_color_to_hex(color) is not None
    )
    if not is_real_color and is_deferred_color_reference(color):
        return color
    parsed = _try_parse_color(color)
    if parsed is None:
        return color
    r, g, b, a = parsed
    if a <= 0.0:
        # Fully transparent: no ink to derive against any canvas.
        return color
    return label_ink(rgb01_to_hex(r, g, b), canvas)


# ============================================================================
# Public API — discovery
# ============================================================================


def palette_metadata(name: str) -> dict[str, Any]:
    """Return palette metadata without loading the full stops."""
    entry = _get_index().get(name)
    if entry is None:
        raise UnknownPaletteError(_unknown_palette_message(name))
    spine = _load_spine(name)
    return {
        "name": spine.name,
        "family": entry["family"],
        "description": spine.description or "",
        "design_notes": spine.design_notes or "",
    }


def list_palettes(
    family: (
        Literal["sequential", "diverging", "categorical", "scaffold", "tone"] | None
    ) = None,
) -> list[str]:
    """List palette names, optionally filtered by family."""
    idx = _get_index()
    if family is None:
        return sorted(idx)
    return sorted(n for n, e in idx.items() if e["family"] == family)


# ============================================================================
# Smart default selection
# ============================================================================


def select_default_palette(
    data_shape: Literal[
        "continuous_numeric", "signed_numeric", "discrete_enum", "status_semantic"
    ],
) -> str:
    """Pick a palette name based on inferred data shape. See Session 1 §A4."""
    if data_shape == "continuous_numeric":
        return "dbt-seq-blue"
    if data_shape == "signed_numeric":
        return "dbt-div-blue-red"
    if data_shape == "discrete_enum":
        return "vivid-10"
    if data_shape == "status_semantic":
        # Upstream caller looks up the specific role (negative/warning/etc.)
        # after this returns the tone sentinel. For now, return "negative" as
        # the canonical default (caller should override based on field value).
        return "negative"
    raise ValueError(f"unknown data_shape: {data_shape}")


# ── Continuous dark-companion lookup (sequential/diverging table swap) ──────
#
# Categorical label ink is now derived live (label_ink()); this lookup is the
# lone survivor, for the continuous-table dark-canvas swap only -- a
# sequential/diverging palette has no single "mark color" to derive live ink
# against, so the swap still needs a real -dark palette file to pin to.


@functools.lru_cache(maxsize=32)
def _has_continuous_dark_companion(name: str) -> bool:
    """Whether ``<name>-dark`` is a registered sequential/diverging palette.

    The dark-canvas table swap uses it to decide whether a pinned continuous
    palette has a -dark twin.
    """
    twin = _get_index().get(f"{name}-dark")
    return twin is not None and twin["family"] in ("sequential", "diverging")
