"""Engine decisions for the legend placement leaves an author left unset.

``legend.position`` carries three independent leaves (``edge``, ``align``,
``overlay``). No theme populates them: a leaf nobody authored is decided here,
once, and the concrete placement is baked into ``ResolvedLegendStyle``. Render
reads only the baked value.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedLegendPosition,
    ResolvedLegendStyle,
)
from dbt_charts.core.compile.models.style.theme import (
    LegendEdge,
    LegendPositionStyle,
    LegendStyle,
    PieLegendStyle,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_LEGEND_POSITION_UNSUPPORTED

_Patch = TypeVar("_Patch", bound=BaseModel)

SUPPORTED_PLACEMENTS = (
    "any edge, `align: start|center`, `overlay: false`",
    "`edge: top|bottom`, `align: start|end`, `overlay: true`",
    "any edge, `align: center`, `overlay: true`",
)


def merge_authored_position(
    board: LegendPositionStyle, patch: LegendStyle | PieLegendStyle | None
) -> LegendPositionStyle:
    """The authored leaves after layering a family or chart legend patch on the board's.

    ``patch`` is the legend patch itself; a pie patch has no ``overlay``.
    """
    return merge_onto_base(board, None if patch is None else patch.position)


def without_position(patch: _Patch | None) -> _Patch | None:
    """*patch* minus its ``position``; ``decide_legend_position`` owns placement."""
    if patch is None:
        return None
    fields = patch.model_fields_set - {"position"}
    return patch.model_construct(
        _fields_set=fields, **{field: getattr(patch, field) for field in fields}
    )


def merge_legend(
    base: ResolvedLegendStyle, patch: LegendStyle | PieLegendStyle | None
) -> ResolvedLegendStyle:
    """*patch* folded onto the resolved legend, placement left to the engine."""
    return merge_onto_base(base, without_position(patch))


def decide_legend_position(
    authored: LegendPositionStyle, *, top_strip: bool, radial: bool = False
) -> ResolvedLegendPosition:
    """Bake concrete placement from the authored leaves.

    ``top_strip`` is the top-legend ladder's verdict (``cartesian_series_naming``):
    the legend rides the strip above the plot, whatever edge was authored or not.

    ``radial`` families (pie, donut) never overlay (``PiePositionStyle``): an
    authored ``overlay`` is ignored.
    """
    overlay = False if radial or authored.overlay is None else authored.overlay
    align = "start" if authored.align is None else authored.align
    edge: LegendEdge
    if top_strip:
        edge = "top"
    elif authored.edge is not None:
        edge = authored.edge
    elif overlay:
        # A floating legend needs a top or bottom edge to anchor a corner to.
        edge = "top"
    else:
        edge = "right"
    return ResolvedLegendPosition(edge=edge, align=align, overlay=overlay)


def validate_cartesian_position(
    position: ResolvedLegendPosition, chart_id: str
) -> None:
    """Raise when no placement draws ``position``; never move it to a nearby one."""
    if not position.overlay:
        if position.align in ("start", "center"):
            return
    elif position.align == "center" or (
        position.edge in ("top", "bottom") and position.align in ("start", "end")
    ):
        return
    raise ChartDataError.from_code(
        ERR_LEGEND_POSITION_UNSUPPORTED,
        chart_id=chart_id,
        edge=position.edge,
        align=position.align,
        overlay=str(position.overlay).lower(),
        supported="; ".join(SUPPORTED_PLACEMENTS),
    )
