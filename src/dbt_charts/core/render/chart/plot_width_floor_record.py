"""Render-time capture of a support_table column block over-claiming card width.

Mirrors ``text_truncation.py``'s ContextVar-sink shape: the fact ("the column
block takes more than its reasonable share of the card") is only knowable
while the engine is building the column block's
geometry — a render concern, since it depends on font-measured column
widths computed from the executed query's own rows, not something knowable
at compile time the way ``plot_height_floor_px``'s height estimate is.

``_collect_render_warnings`` in ``renderer.py`` opens the sink before the
spec-generation pass; ``_apply_support_table_columns_post_pass`` records into
it as a side effect; ``render/warnings/plot_width_below_minimum.py`` reads the
collected dict via ``WarningContext.plot_width_share_warnings``.

This is the softer, earlier signal than the hard floor (checked in
``render/layout_sizing.py``'s width-correction re-render, which raises using
``SUPPORT_TABLE_PLOT_WIDTH_FLOOR_RATIO`` directly): a chart can trip this warning while its
plot still clears the hard floor by a comfortable margin, or it can trip
both.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict


class PlotWidthShareWarning(BaseModel):
    """The column block's width against the pre-axis-chrome footprint it shares
    with the plot, at the width the engine measured."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    column_block_width_px: float
    plot_width_px: float
    card_width_px: float


_sinks: contextvars.ContextVar[tuple[dict[str, PlotWidthShareWarning], ...]] = (
    contextvars.ContextVar("plot_width_share_warning_sinks", default=())
)


@contextmanager
def collect_plot_width_share_warnings() -> Generator[dict[str, PlotWidthShareWarning]]:
    """Open a fresh sink; yield the collected map of chart_id → warning.

    ``record_plot_width_share_warning`` writes into the innermost open sink
    while the context is open. Nested opens do not leak — each yields its
    own dict. One record per chart: a chart's column block is a single fact,
    not a list of them, so a later record for the same chart_id replaces the
    earlier one rather than accumulating.
    """
    collected: dict[str, PlotWidthShareWarning] = {}
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def record_plot_width_share_warning(
    chart_id: str,
    *,
    column_block_width_px: float,
    plot_width_px: float,
    card_width_px: float,
) -> None:
    """Record the column-block-share fact into the innermost open sink, if any.

    No-op when no sink is open (e.g. during the main SVG render, where the
    warning-collection pass has not opened a sink yet).
    """
    sinks = _sinks.get()
    if not sinks:
        return
    sinks[-1][chart_id] = PlotWidthShareWarning(
        column_block_width_px=column_block_width_px,
        plot_width_px=plot_width_px,
        card_width_px=card_width_px,
    )
