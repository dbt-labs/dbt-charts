"""Render-time capture of series labels the endpoint-label rail had to cut.

``measure_label_pane_width`` (in ``emitters/_endpoint_rail.py``) caps the rail
at a fraction of the chart's slot
(``constants.marks.ENDPOINT_LABEL_MAX_WIDTH_FRACTION``); Vega then ellipsizes
any name wider than that at the pane's mark limit. The cut itself is silent —
the rail just reads ``Enterprise Cloud Data Pl…`` — so this module is the seam
that lets the warning detector see it. The measure site records at the exact
point it decides to cap, mirroring ``axis_title_truncation``.

The sink is a ``ContextVar`` set around the spec-generation pass inside
``_collect_render_warnings``. When no sink is open, recording is a no-op.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager
from typing import Literal

from pydantic import BaseModel, ConfigDict

# The authored key the rail's labels came from: a series-color chart draws the
# values of `color:`, while a wide-form area draws the measure names in `y:`
# and has no color key at all.
SeriesLabelSource = Literal["color", "y"]


class SeriesLabelTruncation(BaseModel):
    """A series label the rail cap will ellipsize.

    ``authored_field`` is the YAML key the author wrote, so the squiggle lands
    on a line that exists. ``series_name`` is the full value before Vega cuts it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    authored_field: SeriesLabelSource
    series_name: str


# Stack of active collection sinks (innermost last); empty when no render pass
# is collecting.
_sinks: contextvars.ContextVar[tuple[dict[str, list[SeriesLabelTruncation]], ...]] = (
    contextvars.ContextVar("series_label_truncation_sinks", default=())
)


@contextmanager
def collect_series_label_truncations() -> Generator[
    dict[str, list[SeriesLabelTruncation]]
]:
    """Open a fresh sink; yield the collected map of chart_id → truncations.

    ``record_series_label_truncations`` writes into this map while the context
    is open. Pops the sink on exit so nested renders do not leak into each other.
    """
    collected: dict[str, list[SeriesLabelTruncation]] = {}
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def record_series_label_truncations(
    chart_id: str, authored_field: SeriesLabelSource, series_names: list[str]
) -> None:
    """Record the cut series labels into the innermost open sink, if any.

    No-op when no sink is open (e.g. during the main SVG render, where the
    vega-spec pass has not opened a sink yet).
    """
    sinks = _sinks.get()
    if not sinks or not series_names:
        return
    sink = sinks[-1]
    sink.setdefault(chart_id, []).extend(
        SeriesLabelTruncation(authored_field=authored_field, series_name=name)
        for name in series_names
    )
