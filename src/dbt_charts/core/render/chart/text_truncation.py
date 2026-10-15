"""Render-time capture of user-visible text truncations.

Generalizes the axis-title seam to all truncation surfaces. The ContextVar
sink is opened by ``_collect_render_warnings`` in ``renderer.py`` before the
spec-generation/render pass; recording functions called from chart renderers
write into it as a side-effect, then detectors under ``render/warnings/``
read the collected dict via ``WarningContext.text_truncations``.

``surface`` discriminates which detector owns the record:
  "axis_title"   — cartesian x/y axis title (emitters/_cartesian.py)
  "chart_title"  — chart title or subtitle (title_overflow.py)
  "kpi_label"    — KPI card label (engine)
  "kpi_support"  — KPI support explainer (engine)
  "kpi_inline_fallback" — inline KPI variant fell back to stacked (engine)
  "kpi_align_overflow" — KPI value run wider than its card, so authored
    ``align`` was not applied (engine); no text was cut, ``authored_text`` is empty
  "table_cell"   — table header or body cell (engine)
  "callout_text" — callout title, message, or hint (callout family)
  "spark_label"  — spark-bar row label (engine)
  "support_table_cell_overflow" — strip cells wider than their band
    (support_table_attachment.py); not a cut but a collision, so
    ``authored_text`` carries the measured "needed vs available" detail

``authored_field`` is the YAML key or column name the squiggle should point
at, e.g. "x_label", "title", "label", or a column name for tables.
``authored_text`` is the full text before truncation — except on
"kpi_inline_fallback", where the record isn't a text cut but a whole-variant
degradation (inline -> stacked); there ``authored_field`` is "style.variant" and
``authored_text`` is the variant the author wrote ("inline").
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager
from typing import Literal

from pydantic import BaseModel, ConfigDict

TruncationSurface = Literal[
    "axis_title",
    "chart_title",
    "kpi_label",
    "kpi_support",
    "kpi_inline_fallback",
    "kpi_align_overflow",
    "table_cell",
    "callout_text",
    "spark_label",
    "support_table_cell_overflow",
]


class TextTruncation(BaseModel):
    """One text element that was cut with an ellipsis (or clipped) at render."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    surface: TruncationSurface
    authored_field: str
    authored_text: str


_sinks: contextvars.ContextVar[tuple[dict[str, list[TextTruncation]], ...]] = (
    contextvars.ContextVar("text_truncation_sinks", default=())
)


@contextmanager
def collect_text_truncations() -> Generator[dict[str, list[TextTruncation]]]:
    """Open a fresh sink; yield the collected map of chart_id → truncations.

    ``record_text_truncation`` writes into the innermost open sink while the
    context is open. Nested opens do not leak — each yields its own dict.
    """
    collected: dict[str, list[TextTruncation]] = {}
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def record_text_truncation(
    chart_id: str,
    surface: TruncationSurface,
    authored_text: str,
    authored_field: str,
) -> None:
    """Record one truncated text element into the innermost open sink, if any.

    No-op when no sink is open (e.g. during the main SVG render, where the
    warning-collection pass has not opened a sink yet).
    """
    sinks = _sinks.get()
    if not sinks:
        return
    sink = sinks[-1]
    if chart_id not in sink:
        sink[chart_id] = []
    sink[chart_id].append(
        TextTruncation(
            surface=surface,
            authored_field=authored_field,
            authored_text=authored_text,
        )
    )
