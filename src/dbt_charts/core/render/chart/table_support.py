"""Scale-to-color mapping and conditional-format rules the KPI and Vega-Lite
renderers share."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.chart.authored import (
    match_predicate,
)
from dbt_charts.core.compile.models.style.theme import (
    font_weight_as_css,
)
from dbt_charts.core.render.utils import resolve_tone_color
from dbt_charts.core.utils import coerce_numeric_cell

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.chart.authored import (
        ConditionalRule,
        ScaleTargetConfig,
    )
    from dbt_charts.core.compile.models.primitives import (
        ResolvedScaleTarget,
    )
    from dbt_charts.core.compile.models.style.theme import (
        KpiTonesStyle,
    )


def _apply_rule_outputs(
    rule: ConditionalRule,
    result: dict[str, Any],
    tones: KpiTonesStyle | None = None,
) -> None:
    """Merge a rule's style outputs into ``result`` (last-match-wins)."""
    if rule.background is not None:
        result["background"] = rule.background
    if rule.font is not None:
        f = rule.font
        if f.color is not None:
            result["color"] = f.color
        if f.weight is not None:
            result["weight"] = font_weight_as_css(f.weight)
        if f.style is not None:
            result["style"] = f.style
        if f.decoration is not None:
            result["decoration"] = f.decoration
    if rule.glyph is not None:
        result["glyph"] = rule.glyph
        # A later rule that swaps the glyph without specifying its color
        # must not inherit the previous rule's color — clear it so the
        # cell falls back to default ink.
        result.pop("glyph_color", None)
    if rule.glyph_color is not None:
        result["glyph_color"] = rule.glyph_color
    elif rule.tone is not None and tones is not None:
        # tones is None on the two callers that don't consume glyph_color:
        # the cell background/color path and resolve_cell_glyph (which discards
        # the color, keeping only the glyph char). The real cell-glyph render
        # path threads tones. So None here is a reachable, by-design skip.
        result["glyph_color"] = resolve_tone_color(rule.tone, tones)


def resolve_conditional_styles(
    when_rules: Sequence[ConditionalRule] | None,
    value: Any,
    tones: KpiTonesStyle | None = None,
) -> dict[str, Any]:
    """Evaluate a list of ``when`` rules and return merged style overrides.

    Non-default rules are evaluated in list order; all matching rules
    contribute their style keys with last-match-wins semantics.

    A trailing ``default: true`` rule is a catch-all — it only applies when
    no non-default rule matched. This lets authors write
    ``[{lt:0, ...}, {gt:100, ...}, {default: true, ...}]`` and have the
    default fire only for rows in (0, 100]. Position of the default rule is
    validated on FieldConditionalFormatting; evaluation here is tolerant of
    ordering.

    Keys: ``background`` (str), ``color`` (str), ``weight`` (str),
    ``style`` (str), ``decoration`` (str).
    """
    if not when_rules:
        return {}

    result: dict[str, Any] = {}
    matched_any = False
    default_rule: ConditionalRule | None = None
    for rule in when_rules:
        if rule.default is True:
            default_rule = rule
            continue
        if match_predicate(rule, value):
            matched_any = True
            _apply_rule_outputs(rule, result, tones)
    if not matched_any and default_rule is not None:
        _apply_rule_outputs(default_rule, result, tones)
    return result


def _expand_hex(c: str) -> str:
    """Expand 3-char hex (#RGB) to 6-char (#RRGGBB). Passes 6-char through."""
    h = c.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return f"#{h}"


def _parse_hex(c: str) -> tuple[int, int, int]:
    h = _expand_hex(c).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_color(c1: str, c2: str, t: float) -> str:
    """Linearly interpolate between two hex colors."""
    r1, g1, b1 = _parse_hex(c1)
    r2, g2, b2 = _parse_hex(c2)
    r = int(r1 + (r2 - r1) * t + 0.5)
    g = int(g1 + (g2 - g1) * t + 0.5)
    b = int(b1 + (b2 - b1) * t + 0.5)
    return f"#{r:02x}{g:02x}{b:02x}"


def resolve_palette_stops(target: ResolvedScaleTarget) -> list[str]:
    """Resolve a scale target's palette to a list of hex stops.

    Inline list passes through unchanged (new list object returned).

    A named palette string carries resolved_stops only on
    ResolvedNamedPaletteScaleTargetConfig — the structurally guaranteed form
    produced by bake_scale_target_stops and _with_resolved_scale_stops. A plain
    ResolvedScaleTargetConfig (Vega scheme or inline list) reaching this function
    with a string palette is a caller bug; it raises ValueError rather than
    asserting so the error is visible in production.
    """
    if isinstance(target.palette, list):
        if not target.palette:
            raise ValueError("palette must not be empty")
        return [str(c) for c in target.palette]
    from dbt_charts.core.compile.models.primitives import (  # noqa: PLC0415
        ResolvedNamedPaletteScaleTargetConfig,
    )

    if isinstance(target, ResolvedNamedPaletteScaleTargetConfig):
        return list(target.resolved_stops)
    raise ValueError(
        f"resolve_palette_stops called with a Vega scheme palette={target.palette!r} — "
        "scheme names are rendered directly and must not reach this function."
    )


def auto_hinge_pivot(lo: float, hi: float, col_format: str | None) -> float | None:
    """The neutral value ``hinge: auto`` picks over ``[lo, hi]``: 0 when the
    domain crosses zero, 1.0 when a percent format's domain crosses it, else
    ``None`` -- no neutral exists and the pivot is the domain midpoint.
    """
    if lo < 0.0 < hi or (lo < 0.0 and hi == 0.0):
        return 0.0
    is_percent = col_format is not None and "%" in col_format
    if is_percent and lo < 1.0 < hi:
        return 1.0
    return None


def resolve_hinge(
    config: ScaleTargetConfig,
    lo: float,
    hi: float,
    col_format: str | None,
) -> float | None:
    """Decide the diverging pivot from config + domain context.

    Returns ``None`` for sequential (``config.hinge is None``).
    Explicit float in ``config.hinge`` short-circuits the decision tree.
    ``"auto"`` runs the tree: zero-crossing → 0; percent-format crosses 1.0
    → 1.0; else midpoint.
    """
    if config.hinge is None:
        return None
    # Degenerate domain: diverging is meaningless; fall back to sequential.
    if lo == hi:
        return None
    if isinstance(config.hinge, (int, float)):
        return float(config.hinge)
    pivot = auto_hinge_pivot(lo, hi, col_format)
    return (lo + hi) / 2.0 if pivot is None else pivot


def _interpolate_arm(t: float, stops: list[str]) -> str:
    """Interpolate at normalized position t∈[0,1] along a palette arm.

    ``stops`` must already be expanded to 6-char hex. ``t`` is clamped to
    [0, 1] before indexing.
    """
    t = max(0.0, min(1.0, t))
    n_segments = len(stops) - 1
    segment = t * n_segments
    i = min(int(segment), n_segments - 1)
    return _lerp_color(stops[i], stops[i + 1], segment - i)


def diverging_arm_denominators(
    hinge: float, min_val: float, max_val: float, arm_mode: str
) -> tuple[float, float]:
    """(neg_denom, pos_denom): the interpolation-distance unit for each arm
    of a diverging scale pivoting at ``hinge`` over ``[min_val, max_val]``.

    Shared by ``interpolate_scale_color`` (table/KPI's direct color
    interpolation) and ``emitters._channels._diverging_domain`` (which
    places the same arm math's breakpoints on a Vega-Lite ``domain`` array
    instead) — one formula for what "asymmetric" vs "symmetric" means,
    never two.

    ``arm_mode="asymmetric"``: both arms share one denominator (the wider
    arm's width), so per-unit intensity from the pivot is consistent
    across both arms — the narrower arm never reaches full palette
    saturation within the real domain. ``arm_mode="symmetric"``: each arm
    uses its own width, so both extremes always reach full saturation
    exactly at ``min_val``/``max_val``.
    """
    neg_width = hinge - min_val
    pos_width = max_val - hinge
    if arm_mode == "asymmetric":
        denom = max(neg_width, pos_width, 1e-12)
        return denom, denom
    return max(neg_width, 1e-12), max(pos_width, 1e-12)


def interpolate_scale_color(
    value: float,
    min_val: float,
    max_val: float,
    palette: list[str],
    *,
    hinge: float | None = None,
    arm_mode: str = "asymmetric",
) -> str:
    """Map a numeric value to a color via linear interpolation across palette stops.

    Sequential mode (``hinge is None``):
      Values below min clamp to the first stop; above max clamp to the last.
      When min == max, returns the middle stop.

    Diverging mode (``hinge is not None``):
      Palette is split at the midpoint. Left half maps to [min_val, hinge];
      right half maps to [hinge, max_val]. An odd-length palette shares one
      neutral center stop between the two arms; an even-length one has no
      such stop, so the hinge sits at the boundary between the two center
      stops instead (blended 50/50 — see the even-length branch below).

      ``arm_mode="asymmetric"`` (default): each arm's t is computed relative
      to the actual arm width — per-unit intensity is consistent across the
      pivot regardless of arm length.

      ``arm_mode="symmetric"``: each arm stretches fully from neutral to
      its extreme regardless of absolute width — useful when visual parity
      between arms matters more than per-unit consistency.

      A ``hinge`` outside ``[min_val, max_val]`` EXTENDS the domain to
      include it rather than clamping the hinge into range — see the
      extension comment below for why, and for the VALUE clamp's
      deliberately different (unextended) domain.
    """
    palette = [_expand_hex(c) for c in palette]

    if hinge is None:
        # Sequential path — unchanged behavior.
        if min_val == max_val:
            return palette[len(palette) // 2]
        t = (value - min_val) / (max_val - min_val)
        t = max(0.0, min(1.0, t))
        n_segments = len(palette) - 1
        segment = t * n_segments
        i = min(int(segment), n_segments - 1)
        return _lerp_color(palette[i], palette[i + 1], segment - i)

    # Diverging path. An odd-length palette has one shared neutral stop; an
    # even-length one has none, so the hinge sits at the boundary between
    # the two center stops instead — see the even-length branch below.
    if len(palette) < 2:
        raise ValueError(
            f"diverging palette must have at least 2 stops, got {len(palette)}"
        )

    # Extend whichever bound doesn't already include hinge, rather than
    # clamping hinge into [min_val, max_val]: a diverging palette shared
    # across charts means "hinge is neutral" on every one of them, even a
    # chart whose own data never reaches it -- the same HINGE-EXTENSION
    # decision gradient_scale_to_vl makes for the VL-native emitters. The two
    # do diverge on the VALUE clamp, though: VL's own `clamp: true` clamps a
    # rendered value to the EXTENDED domain (gradient_scale_to_vl bakes the
    # extended [lo, hi] as the actual VL `domain`), while the VALUE clamp a
    # few lines down here deliberately stays against the ORIGINAL
    # (unextended) min_val/max_val -- a value that never occurs in this
    # column's own data still clamps to this column's own true edge, not to
    # wherever a hinge shared with a sibling chart happens to sit. A value
    # between the two bounds (never in this column's real data, but inside
    # the hinge-extended range) therefore reads a genuinely different color
    # in a table cell than in a chart's gradient -- a real, documented
    # tradeoff, not a bug to chase down further here.
    domain_min = min(min_val, hinge)
    domain_max = max(max_val, hinge)

    mid = len(palette) // 2
    if len(palette) % 2 == 0:
        # No shared center stop to pivot on — blend the two center stops
        # 50/50 and give BOTH arms that blend as their own nearest-to-hinge
        # stop, so each arm's curve reaches the exact same color at
        # distance 0. Without this, the two arms would independently reach
        # their own (unblended) center stop right at the hinge, a visible
        # color jump exactly where the gradient should read as continuous.
        neutral = _lerp_color(palette[mid - 1], palette[mid], 0.5)
        neg_stops = [neutral, *palette[:mid][::-1]]
        pos_stops = [neutral, *palette[mid:]]
    else:
        neg_stops = palette[: mid + 1][::-1]  # neutral → neg extreme
        pos_stops = palette[mid:]  # neutral → pos extreme

    # Clamp value to the TRUE (unextended) data domain — see comment above.
    clamped = max(min_val, min(max_val, value))

    neg_denom, pos_denom = diverging_arm_denominators(
        hinge, domain_min, domain_max, arm_mode
    )
    if clamped <= hinge:
        distance = hinge - clamped
        return _interpolate_arm(distance / neg_denom, neg_stops)
    else:
        distance = clamped - hinge
        return _interpolate_arm(distance / pos_denom, pos_stops)


def compute_scale_domain(
    data: list[dict[str, Any]], field: str, cfg: ScaleTargetConfig
) -> tuple[float, float]:
    """Compute the effective [min, max] domain for a scale target.

    Uses explicit overrides when set, otherwise infers from data.
    Falls back to (0, 1) when no numeric values are present.
    """

    has_min = cfg.min is not None
    has_max = cfg.max is not None

    if has_min and has_max:
        return (float(cfg.min), float(cfg.max))  # type: ignore[arg-type]

    values: list[float] = []
    for row in data:
        coerced = coerce_numeric_cell(row.get(field))
        if coerced is not None:
            values.append(coerced)

    if not values:
        return (
            float(cfg.min) if has_min else 0.0,  # type: ignore[arg-type]
            float(cfg.max) if has_max else 1.0,  # type: ignore[arg-type]
        )

    lo = float(cfg.min) if has_min else min(values)  # type: ignore[arg-type]
    hi = float(cfg.max) if has_max else max(values)  # type: ignore[arg-type]
    return (lo, hi)
