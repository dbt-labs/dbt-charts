"""Board-level category→color binding: one swatch per data value, board-wide."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from dbt_charts.core.colors import TOKEN_OR_SANITIZABLE_COLOR_PATTERN
from dbt_charts.core.compile.models.primitives import ErrorHint
from dbt_charts.core.compile.models.schema_names import CategorySlotToken
from dbt_charts.core.diagnostics.chart_data import ChartDataError

# Whether a well-formed token actually resolves needs the theme's palettes and
# roles, which arrive later in the cascade; an unresolvable token raises
# ``UnknownColorError`` there, like every other style token.
TokenOrSanitizableColor = Annotated[
    str,
    StringConstraints(pattern=TOKEN_OR_SANITIZABLE_COLOR_PATTERN),
    ErrorHint(
        "category_colors color {value!r} is neither a palette token (e.g. "
        "`category[1]`, `dbt-grays.8`) nor a CSS hex color (e.g. `#1f77b4`)."
    ),
]


class CategoryColorBinding(BaseModel):
    """Value→color assignments for one data field, shared by every chart.

    ``values`` is the whole authoring surface: authors pin a category by
    editing its entry directly. There is no second ``overrides:`` subkey —
    two places to say the same thing is two places to disagree.

    Mapping order is the scale domain order, so it also fixes which swatch a
    value lands on when the color is a palette token rather than a literal.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # CategorySlotToken names the `category[N]` slots so editors complete them
    # — the form worth reaching for, since a slot re-skins with the theme while
    # a hex does not. The pattern arm keeps dotted palette tokens and literal
    # hex legal.
    values: dict[str, CategorySlotToken | TokenOrSanitizableColor] = Field(
        description=(
            "Data value → palette token (`category[1]`, `dbt-grays.8`) "
            "or literal hex. Tokens resolve against the board's theme."
        ),
    )


@dataclass(frozen=True)
class CategoryColorScale:
    """One field's board-wide value→palette-slot assignment.

    A value's identity here is its **slot**, not a hex. Every palette a chart
    derives is indexed by that same slot: `endpoint_labels.py` takes a series'
    fill from `palette[i]` and its label ink from `dark_companion_palette[i]`
    on adjacent lines, and `pie.py` pairs wedge stops with dark stops the same
    way. Handing render a finished hex would fix the fill and leave every
    companion pointing at whatever used to occupy that position.

    Carrying the slot also lets a nested board keep the theme it resolved: both
    boards agree a value owns slot 2, and each paints slot 2 out of its own
    palette rather than being overpainted by the root's.

    ``overrides`` holds pins naming a color the palette does not contain. Such
    a value still owns a slot — that is what its companion ink indexes, and
    it may share one when the palette is full — but its fill is the literal
    the author wrote. Built by
    ``plan_category_colors``; display order is deliberately absent, because
    reordering a domain is what desynchronizes everything downstream.

    ``unseen_pins`` holds pins authored for this field whose value none of
    this render's rows actually draw — ``_seatable_pins``
    (``compile/resolve/style/category_colors.py``) drops such a pin rather
    than seating it (seating would claim a slot for a category nothing
    draws and push every real value along one). The drop stays silent for
    color assignment, but not for the author: the render-time
    ``WARN-CATEGORY-COLOR-PIN-UNSEEN`` detector reads this field to warn
    that the pin did nothing. Value → the authored color, in authored
    order. Empty for a scale whose pins all matched.
    """

    field: str
    slots: Mapping[str, int]
    overrides: Mapping[str, str]
    unseen_pins: Mapping[str, str] = dataclasses.field(default_factory=dict)


def _slot_for(scale: CategoryColorScale, value: str, palette: Sequence[str]) -> int:
    """The palette slot ``value`` owns under ``scale``, valid for ``palette``.

    ``scale.slots`` covers every value any chart on the board draws for
    ``scale.field`` (the planner unions across charts, and poisons the whole
    field out of the plan when a chart's own values can't be unioned — see
    ``execute/category_colors.py``). A value missing here means the caller
    passed a value outside that union — a real bug upstream.

    The slot is planned against the board's palette, but a caller may index
    it against a chart-local override that is SHORTER (``style.color.
    categorical.palette``, or a nested board's shorter theme) — wrapping that
    via ``% len(palette)`` would silently collapse two categories onto one
    swatch, which ``CategoryColorScale`` guarantees never happens to a fill. Both failure modes raise the same ``ChartDataError`` naming the
    field and value, never a bare ``KeyError`` or a silent wrap.
    """
    try:
        slot = scale.slots[value]
    except KeyError:
        raise ChartDataError(
            f"`{value}` has no palette slot in the board's `{scale.field}` "
            "category-color scale."
        ) from None
    if slot >= len(palette):
        raise ChartDataError(
            f"`{value}` needs palette slot {slot} for the board's `{scale.field}` "
            f"category-color scale, but this chart's palette has only "
            f"{len(palette)} swatches."
        )
    return slot


def color_at(scale: CategoryColorScale, value: str, palette: Sequence[str]) -> str:
    """The FILL ``value`` paints under ``scale``: its override, else its palette slot."""
    if value in scale.overrides:
        return scale.overrides[value]
    return palette[_slot_for(scale, value, palette)]


def series_colors(
    series: Sequence[str], palette: Sequence[str], scale: CategoryColorScale | None
) -> dict[str, str]:
    """The fill each series paints: its board-wide slot under ``scale``, else
    the palette slot of its position in ``series``."""
    if scale is not None:
        return {s: color_at(scale, s, palette) for s in series}
    return {s: palette[i % len(palette)] for i, s in enumerate(series)}


def category_scale_for(
    category_colors: Sequence[CategoryColorScale], field: str
) -> CategoryColorScale | None:
    """Return the board-wide scale for ``field`` in ``category_colors``, or None when unbound.

    ``category_colors`` is a chart's own board-wide binding, already narrowed
    to the fields that chart draws (baked at resolve time — see
    ``compile/resolve/style/category_colors.py``). Emitters and features both
    need to find the one scale for a given color field; this is the shared
    lookup so neither package reaches into the other to get it.
    """
    for scale in category_colors:
        if scale.field == field:
            return scale
    return None
