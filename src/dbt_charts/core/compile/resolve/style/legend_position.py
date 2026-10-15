"""Engine decisions for the legend placement leaves an author left unset.

``legend.position`` carries three independent leaves (``edge``, ``align``,
``overlay``), and ``legend.direction`` is inferred from the edge. No theme
populates them: a leaf nobody authored is decided here, once, and the concrete
placement is baked into ``ResolvedLegendStyle``. Render reads only the baked
value.
"""

from __future__ import annotations

from typing import Literal, TypeVar

from pydantic import BaseModel

from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedLegendPosition,
    ResolvedLegendStyle,
)
from dbt_charts.core.compile.models.style.theme import (
    LegendAlign,
    LegendDirection,
    LegendEdge,
    LegendLabelStyle,
    LegendPositionStyle,
    LegendStyle,
    LegendTitleStyle,
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


def merge_authored_direction(
    board: LegendStyle, patch: LegendStyle | PieLegendStyle | None
) -> LegendDirection | None:
    return (
        board.direction if patch is None or patch.direction is None else patch.direction
    )


def merge_authored_visible(
    board: LegendStyle, patch: LegendStyle | PieLegendStyle | None
) -> bool | Literal["auto"]:
    """The legend's ``visible`` after layering a family or chart patch on the board's.

    ``auto`` survives here: the baked ``ResolvedLegendStyle`` holds a bool, so the
    cascade's own answer is read from the sparse styles.
    """
    visible = board.visible if patch is None or patch.visible is None else patch.visible
    if visible is None:
        raise ValueError("style.charts.legend.visible is unset after the theme cascade")
    return visible


def merge_authored_title_visible(
    board: LegendStyle, patch: LegendStyle | PieLegendStyle | None
) -> bool | Literal["auto"] | None:
    """The legend title's ``visible`` after layering a family or chart patch."""
    # A sparse patch leaves `title` None when unauthored, though the patch
    # model's static stub declares it required.
    patch_title: LegendTitleStyle | None = None if patch is None else patch.title
    if patch_title is None or patch_title.visible is None:
        return board.title.visible
    return patch_title.visible


def _without(model: _Patch, dropped: set[str]) -> _Patch:
    fields = model.model_fields_set - dropped
    return model.model_construct(
        _fields_set=fields, **{f: getattr(model, f) for f in fields}
    )


def _element_without(
    element: LegendLabelStyle | LegendTitleStyle | None, dropped: set[str]
) -> LegendLabelStyle | LegendTitleStyle | None:
    """A legend label or title patch minus *dropped* leaves and the font's
    ``extends``, which the resolved font has no field for. ``None`` if nothing
    is left."""
    if element is None:
        return None
    font = element.font if "font" in element.model_fields_set else None
    if font is not None and "extends" in font.model_fields_set:
        element = element.model_construct(
            _fields_set=element.model_fields_set,
            **{
                **{f: getattr(element, f) for f in element.model_fields_set},
                "font": _without(font, {"extends"}),
            },
        )
    element = _without(element, dropped)
    return element if element.model_fields_set else None


def without_engine_decided(patch: _Patch | None) -> _Patch | None:
    """*patch* minus the leaves the engine decides per chart: ``position``,
    ``direction``, ``visible`` and ``title.visible``. They may read ``auto``,
    which the baked ``ResolvedLegendStyle`` cannot hold."""
    if patch is None:
        return None
    fields = patch.model_fields_set - {"position", "direction", "visible"}
    values = {field: getattr(patch, field) for field in fields}
    for name, dropped in (("title", {"visible"}), ("label", set())):
        if name not in values:
            continue
        element = _element_without(values[name], dropped)
        if element is None:
            fields = fields - {name}
            del values[name]
        else:
            values[name] = element
    return patch.model_construct(_fields_set=fields, **values)


def merge_legend(
    base: ResolvedLegendStyle, patch: LegendStyle | PieLegendStyle | None
) -> ResolvedLegendStyle:
    """*patch* folded onto the resolved legend, decided leaves left to the engine."""
    return merge_onto_base(base, without_engine_decided(patch))


def placed_edge(leaf: LegendEdge | Literal["auto"] | None) -> LegendEdge | None:
    """An edge someone set; ``auto`` and unset are the engine's call."""
    return None if leaf == "auto" else leaf


def placed_align(leaf: LegendAlign | Literal["auto"] | None) -> LegendAlign | None:
    return None if leaf == "auto" else leaf


def placed_overlay(leaf: bool | Literal["auto"] | None) -> bool | None:
    return None if leaf == "auto" else leaf


def decide_legend_position(
    authored: LegendPositionStyle, *, top_strip: bool, radial: bool = False
) -> ResolvedLegendPosition:
    """Bake concrete placement from the authored leaves.

    ``top_strip`` is the top-legend ladder's verdict (``cartesian_series_naming``):
    the legend rides the strip above the plot, whatever edge was authored or not.

    ``radial`` families (pie, donut) never overlay (``PiePositionStyle``): an
    authored ``overlay`` is ignored.
    """
    authored_overlay = placed_overlay(authored.overlay)
    authored_align = placed_align(authored.align)
    authored_edge = placed_edge(authored.edge)
    overlay = False if radial or authored_overlay is None else authored_overlay
    align = "start" if authored_align is None else authored_align
    edge: LegendEdge
    if top_strip:
        edge = "top"
    elif authored_edge is not None:
        edge = authored_edge
    elif overlay:
        # A floating legend needs a top or bottom edge to anchor a corner to.
        edge = "top"
    else:
        edge = "right"
    return ResolvedLegendPosition(edge=edge, align=align, overlay=overlay)


def decide_legend_direction(
    authored: LegendDirection | None, edge: LegendEdge
) -> LegendDirection:
    """The authored direction, else horizontal on top/bottom and vertical on a side."""
    if authored is not None:
        return authored
    return "horizontal" if edge in ("top", "bottom") else "vertical"


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
