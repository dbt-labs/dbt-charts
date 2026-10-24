"""The ink each layer of a layered chart paints, decided once at resolve.

A layered chart draws its base and layers on one shared color scale. An
unauthored layer takes the first palette color the board, the base and the
layers before it have not taken. Annotations, the VL overlay emitter and the
engine all read the result off `ResolvedLayer`; none picks a slot itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from dbt_charts.core.compile.models.chart.authored._layer import CartesianLayer
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
    color_at,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import ChartRows
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_LAYERED_NO_PALETTE
from dbt_charts.core.utils import vega_infers_quantitative, vega_infers_temporal


@dataclass(frozen=True)
class LayerInk:
    """What one layer paints: one color, or one per value of its own `color`."""

    ink: str | None
    series: Mapping[str, str]
    # A numeric or date `color`: a gradient, not a set of series.
    gradient: bool


@dataclass(frozen=True)
class LayerBase:
    """What the base chart puts on the shared scale ahead of its layers.

    ``series`` maps each color the base paints to its series value (a colorless
    base's one fill is keyed by its y column). ``shared`` is False when the base
    has a color channel the layers cannot join (a gradient or conditional
    scale): its layers then paint the base's single-series fill.
    """

    shared: bool
    series: Mapping[str, str]


def layer_inks(
    layers: Sequence[CartesianLayer],
    authored: Sequence[str | None],
    base: LayerBase,
    *,
    chart_id: str,
    palette: Sequence[str],
    board_scales: Sequence[CategoryColorScale],
    single_series_fill: str,
    rows_of: Callable[[CartesianLayer], ChartRows],
) -> tuple[LayerInk, ...]:
    """Each layer's ink, in authored order; ``authored[i]`` is layer i's own color.

    A color the board binds to a category is never taken, drawn by this chart
    or not. Once the palette is exhausted, picks rotate through it.
    """
    board = (
        {
            color_at(scale, value, palette)
            for scale in board_scales
            for value in scale.slots
        }
        if palette
        else set()
    )
    painted = list(base.series.values())
    claimed = dict(base.series)

    def free() -> str:
        used = {c.casefold() for c in (*board, *painted)}
        return next(
            (c for c in palette if c.casefold() not in used),
            palette[len(painted) % len(palette)],
        )

    inks: list[LayerInk] = []
    for layer, own in zip(layers, authored, strict=True):
        if layer.y is None:
            inks.append(LayerInk(single_series_fill, {}, False))
            continue
        rows = [] if layer.color is None else rows_of(layer)
        # A numeric or date `color` is a gradient with a color scale of its own:
        # it takes no slot on the shared scale.
        if layer.color is not None and (
            vega_infers_quantitative(rows, layer.color)
            or vega_infers_temporal(rows, layer.color)
        ):
            inks.append(LayerInk(own or single_series_fill, {}, True))
            continue
        if layer.color is None:
            ink = own or (free() if base.shared and palette else single_series_fill)
            painted.append(ink)
            inks.append(LayerInk(ink, {}, False))
            continue
        if not palette:
            raise ChartDataError.from_code(ERR_LAYERED_NO_PALETTE, chart_id=chart_id)
        values = sorted(
            {str(row[layer.color]) for row in rows if row.get(layer.color) is not None}
        )
        for value in values:
            if value not in claimed:
                claimed[value] = free()
                painted.append(claimed[value])
        inks.append(LayerInk(None, {value: claimed[value] for value in values}, False))
    return tuple(inks)
