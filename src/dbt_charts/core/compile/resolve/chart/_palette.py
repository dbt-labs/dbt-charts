"""Palette and series-label resolution shared across chart families."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.format import resolve_format_for_values
from dbt_charts.core.compile.models.primitives import (
    ColorStyle,
    StaticGradientColorStyle,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAxisStyle,
    ResolvedSeriesLabelStyle,
)
from dbt_charts.core.compile.models.style.theme import (
    font_weight_as_css,
)
from dbt_charts.core.compile.resolve.style.palette import mark_ink
from dbt_charts.core.compile.resolve.style.tokens import _resolve_color_tokens
from dbt_charts.core.compile.resolve.style.typography import width_tier

__all__ = [
    "_cartesian_style_tail",
    "_effective_palette",
    "_effective_requested_alias_palette",
    "_effective_single_series_fill",
    "_resolved_series_label",
    "_with_color_tokens",
]


class _HasColor(Protocol):
    """Structural contract for style patches that carry color config."""

    color: ColorStyle | StaticGradientColorStyle | None


def _resolve_patch_categorical(
    primary: _HasColor | None,
) -> tuple[list[str] | None, str | None]:
    """Return (chart-local categorical palette override, alias provenance).

    Returns (None, None) when the chart has no color.categorical override so
    callers fall through to chart_style_context.palette / chart_style_context.requested_alias_palette.
    The second element is the originally-requested name when the override is a
    known WARN-PALETTE-UNSUPPORTED anti-pattern alias (e.g. "RdYlGn"), else None.

    """
    if primary is None or primary.color is None:
        return None, None
    _cat = getattr(primary.color, "categorical", None)
    if _cat is None or _cat.palette is None:
        return None, None
    from dbt_charts.core.compile.resolve.style.palette import (
        resolve_palette_alias,  # noqa: PLC0415
    )

    _pal = _cat.palette
    if isinstance(_pal, str):
        return resolve_palette_alias(_pal)
    return list(_pal), None


def _effective_palette(
    chart_style_context: ChartStyleContext, primary: _HasColor | None
) -> list[str]:
    """Return the effective palette: chart-local override or board default.

    Call from cartesian or radial (pie) family resolvers; see
    ``_resolve_patch_categorical``.
    """
    override, _ = _resolve_patch_categorical(primary)
    return override if override is not None else chart_style_context.palette


def _effective_requested_alias_palette(
    chart_style_context: ChartStyleContext, primary: _HasColor | None
) -> str | None:
    """Return the effective WARN-PALETTE-UNSUPPORTED provenance for this chart.

    Mirrors ``_effective_palette``: a chart-local ``color.categorical.palette``
    override — alias or not — always wins over the board value; only the
    absence of any chart-local override falls through to
    ``chart_style_context.requested_alias_palette``.
    """
    override, requested = _resolve_patch_categorical(primary)
    return (
        requested
        if override is not None
        else chart_style_context.requested_alias_palette
    )


def _effective_single_series_fill(
    chart_style_context: ChartStyleContext,
    primary: _HasColor | None,
    rhythm_slot: int = 0,
    has_layers: bool = False,
) -> str:
    """Return the base fill color for a cartesian family chart.

    A chart with ``layers`` is a multi-series chart in disguise: its base must
    share the category palette with its overlay layers (slot 0), matching the
    pre-migration ``type: layered`` behavior where every series — base
    included — read from one implicit categorical scale. Only a chart with NO
    layers is a true single series, whose ink is overridden ONLY by
    ``style.single_series_palette`` (indexed by the chart's rhythm slot).
    ``style.color.categorical`` / ``style.range.category`` configure the multi-series
    *color channel* and must NOT recolor a genuine single-series mark.
    """
    # per-chart static color override (style.<family>.color.static) takes
    # highest precedence for single-series ink — it is an explicit hex value
    # chosen by the author for this specific chart.
    if (
        primary is not None
        and primary.color is not None
        and primary.color.static is not None
    ):
        return primary.color.static
    # authored chart-local categorical palette wins over the board single_series_palette.
    chart_palette, _ = _resolve_patch_categorical(primary)
    if chart_palette:
        return chart_palette[0]
    if has_layers:
        return chart_style_context.palette[0]
    ink = chart_style_context.single_series_palette
    if primary is not None and primary.color is not None:
        _cat = getattr(primary.color, "categorical", None)
        if _cat is not None and _cat.single_series_palette is not None:
            authored = _cat.single_series_palette
            if isinstance(authored, str):
                from dbt_charts.core.compile.resolve.style.palette import (
                    palette as resolve_palette,
                )  # noqa: PLC0415

                authored = resolve_palette(authored)
            if authored:
                ink = list(authored)
    if not ink:
        raise ValueError(
            "single_series_palette must be non-empty after cascade; got []"
        )
    return ink[rhythm_slot % len(ink)]


def _resolved_series_label(
    chart_style_context: ChartStyleContext,
    primary: _HasColor | None,
    width: float,
    canvas: str,
) -> ResolvedSeriesLabelStyle:
    """Bake series-label typography + dark-companion ink at compile time.

    The render layer is barred from compile.palette, so the dark-companion
    ink for the chart's effective palette is resolved here (full palette; the
    renderer slices ``[:n_series]``). ``canvas`` is the chart's own effective,
    already-opaque ink canvas -- the caller's own
    ``chart_local_style_context.ink_canvas`` (chart_context.py recomputes it
    alongside a chart-local ``style.background`` override, composited over
    the board's own), not ``chart_style_context`` itself, which stays the
    board-level cascade bag for the palette/font lookups above. Font fields
    are required post-cascade.
    """
    font = chart_style_context.series_label.font
    if (
        font.family is None
        or font.size is None
        or font.weight is None
        or font.style is None
    ):
        raise ValueError(
            "series_label.font.{family,size,weight,style} must be non-None after cascade"
        )
    eff_palette = _effective_palette(chart_style_context, primary)
    compact = width_tier(width) in ("tiny", "narrow")
    font_size = font.compact_size if compact else font.size
    multiplier = get_chart_rendering().endpoint_labels.line_height_multiplier
    return ResolvedSeriesLabelStyle(
        font_family=font.family,
        font_size=font_size,
        font_weight=font_weight_as_css(font.compact_weight if compact else font.weight),
        font_style=font.style,
        dark_companion_palette=tuple(mark_ink(c, canvas) for c in eff_palette),
        gap_px=font_size * multiplier,
    )


def _with_color_tokens(primary: Any, chart_style_context: ChartStyleContext) -> Any:
    """Apply color-token resolution to a family-level style patch; pass None through."""
    if primary is None:
        return None
    return _resolve_color_tokens(
        primary, chart_style_context.palettes, chart_style_context.roles
    )


def _cartesian_style_tail(
    chart_style_context: ChartStyleContext,
    ax: ResolvedAxisStyle,
    ay: ResolvedAxisStyle,
    tooltip_format_values: Iterable[float | None],
) -> dict[str, Any]:
    """Common ResolvedXxxStyle kwargs shared by every cartesian family.

    Canvas background (halo strokes, inside-mark value-label knockout) lives
    on the resolved chart envelope (``chart.background``) instead — emitters
    read it from there so it always agrees with the VL spec root, rather
    than duplicating it here as a second, independently-populated field.

    ``tooltip_format_values`` is every value the chart's own quantitative
    measure(s) will paint — passed to resolve_format_for_values so the
    board-default tooltip format (this function's fallback candidate)
    floors the same way the chart-authored candidate in
    ``_measure_tooltip_format`` does.
    """
    return {
        "tooltip_format": resolve_format_for_values(
            chart_style_context.tooltip.format,
            chart_style_context.formats,
            tooltip_format_values,
        ),
        "axis_x": ax,
        "axis_y": ay,
    }
