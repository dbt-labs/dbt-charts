"""The `pattern:` channel: its field, scale, and ink rules, resolved once."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, TypeVar

from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    PieChart,
)
from dbt_charts.core.compile.models.primitives import (
    PatternStyle,
    ResolvedPattern,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve.style.palette import (
    list_palettes,
    palette,
    pattern_palette,
)
from dbt_charts.core.utils import vega_infers_quantitative

__all__ = ["inked_palette", "pattern_series", "resolve_pattern"]

_C = TypeVar("_C", BarChart, AreaChart, PieChart)
_DEFAULT_ROLE = {
    "category": "pattern_category",
    "sequential": "pattern_sequential",
}


class _HasInk(Protocol):
    @property
    def color(self) -> str | None: ...


class _HasPattern(Protocol):
    """A chart-local style patch that may carry a `style.pattern` block."""

    @property
    def pattern(self) -> _HasInk | None: ...


def pattern_series(chart: _C, *axes: str | Sequence[str] | None) -> _C:
    """``chart`` with its ``pattern`` field as the ``color`` channel when the
    pattern alone splits the series: it names none of the chart's ``axes``, which
    would make it the category of a single-series chart."""
    if chart.pattern is None or chart.color is not None:
        return chart
    drawn = {
        a for axis in axes if axis for a in ((axis,) if isinstance(axis, str) else axis)
    }
    if chart.pattern in drawn:
        return chart
    return chart.model_copy(update={"color": chart.pattern})


def _ground(authored: str) -> str | tuple[str, ...]:
    if authored in list_palettes("categorical"):
        return tuple(palette(authored))
    return authored


def resolve_pattern(
    field: str | None,
    color: str | None,
    data: list[dict[str, Any]],  # type-state: explicit_any — query-row boundary values
    chart_style_context: ChartStyleContext,
    style: PatternStyle,
    single_color: str | None,
    primary: _HasPattern | None,
) -> ResolvedPattern | None:
    """Resolve a chart's `pattern:` field against its merged `style.pattern`
    (theme under chart-local) and the field's data.

    ``color`` is the authored ``color:`` channel; ``single_color`` is the pattern
    color of a chart without one; ``primary`` is the chart-local style, whose
    ``style.pattern.color`` is the only ink that conflicts with ``color:``.

    A discrete field takes the category role, a quantitative one the sequential
    role; an authored palette decides the scale by its own kind and must suit
    the field.
    """
    if field is None:
        return None
    if color is not None and color != field:
        raise ValueError(
            f"chart.pattern ({field!r}) and chart.color ({color!r}) must name the "
            "same column: one column drives both the hue and the pattern of a series."
        )
    quantitative = vega_infers_quantitative(data, field)
    authored = style.palette
    if authored is None:
        authored = _DEFAULT_ROLE["sequential" if quantitative else "category"]
    palette = pattern_palette(authored, chart_style_context.palettes)
    scale = palette.kind
    assert scale is not None  # pattern_palette returns pattern-family palettes only
    if quantitative == (scale == "categorical"):
        raise ValueError(
            f"style.pattern.palette {authored!r} is a {scale} pattern palette, but "
            f"pattern field {field!r} is "
            f"{'quantitative' if quantitative else 'discrete'}: use a "
            f"{'sequential or diverging' if quantitative else 'categorical'} palette."
        )
    if scale == "categorical" and style.steps is not None:
        raise ValueError(
            f"style.pattern.steps is for a quantitative pattern field; "
            f"{field!r} is on a category scale, which has no steps."
        )
    local_ink = primary.pattern.color if primary and primary.pattern else None
    if color is not None and local_ink is not None:
        raise ValueError(
            f"style.pattern.color ({local_ink!r}) and `color: {color}` both "
            "paint the pattern: drop one. With a `color:` channel each series' "
            "pattern takes its hue."
        )
    ink = None if color is not None else style.color or single_color
    ground = _ground(style.ground)
    if isinstance(ground, tuple) and scale != "categorical":
        raise ValueError(
            f"style.pattern.ground names a color palette, which colors the series "
            f"of a category pattern; {field!r} is on a {scale} ramp."
        )
    return ResolvedPattern(
        field=field,
        scale="category" if scale == "categorical" else scale,
        motifs=palette.motifs,
        ramp=palette.ramp,
        steps=style.steps or palette.steps,
        color=ink,
        ground=ground,
        inverse=style.inverse,
    )


def inked_palette(palette: list[str], pattern: ResolvedPattern | None) -> list[str]:
    """``palette`` with every series in the pattern's one color, for a chart whose
    pattern alone splits its series: series-keyed text (end labels, wedge labels,
    the key) then reads the single color, not a rainbow the marks don't carry."""
    if pattern is None or pattern.color is None:
        return palette
    return [pattern.color] * len(palette)
