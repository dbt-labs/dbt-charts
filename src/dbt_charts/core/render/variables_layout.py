"""Where every variable control sits — the strip's one layout authority.

Stage: RENDER
Purpose: Decide each control's box in board user units, and the height of the
band that holds them.

The chrome renderer draws from these boxes and the band takes its height from
the same result, so the two cannot disagree. That is the point: the code this
replaces had a sizer estimating widths from a character count while the browser
laid out something else, and the two answers drifted by up to 155% on a single
control.

Widths are *measured*, not guessed. A control whose width is content-driven —
a select showing ``All`` versus ``Latin America and the Caribbean`` — is sized
from the text it will actually draw. A control the theme fixes (`text`,
`number`, `checkbox`, `slider`, `daterange`) keeps its theme width, so
``variables.input.widths.*`` stays load-bearing rather than silently orphaned.
A multiselect holds the text width whatever is ticked, and its text gives way
instead: a field that grew with each tick moved every control beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from dbt_charts.core.compile.models.variable.authored import VariableInputType
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.text.format_d3 import board_word
from mdsvg.fonts import truncate_text_precise

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dbt_charts.core.compile.models.style.theme.variables import VariablesStyle


@dataclass(frozen=True)
class InputTraits:
    """What the strip needs to know about one input kind, in one row.

    ``sizing`` is where the field's width comes from: ``content`` measures the
    text it will draw, ``summary`` holds the theme's text width and cuts the text
    to fit, the rest name a theme width or a shape that has its own.
    ``ornament`` is the glyph the chrome draws inside the field's right edge.
    ``unset`` is which "no value" affordance the control offers, and whose label
    vocabulary it reads. ``bounds`` is whether ``min``/``max``/``step`` reach the
    control: ``slider`` falls back to the theme's range, ``authored`` reads only
    what the board sets.

    One row rather than four subsets because the four questions have always
    been asked of the same fourteen values, and answering them apart is what let
    them disagree.
    """

    sizing: Literal[
        "content", "summary", "text", "number", "daterange", "checkbox", "slider"
    ]
    ornament: Literal["arrow", "calendar", "none"]
    unset: Literal["chooser", "range", "date", "none"]
    bounds: Literal["slider", "authored", "none"]


# Every member of `VariableInputType`, and the gate is that it *is* every member
# — `test_every_variable_input_type_is_classified` compares this table's keys
# against the enum, so a new input kind cannot render until someone has decided
# these four things about it.
#
# That gate is the point. The three hand-written subsets this replaces shared a
# silent fall-through: an unclassified kind was sized as text, drew no glyph, and
# offered no unset, with nothing anywhere observing the omission. Dropping
# `radio` from two of them passed 3,382 tests.
#
# `auto` is carried rather than skipped. `detect_variable_input_type` always
# resolves it before layout, so its row is unreachable — but a table with a
# deliberate hole in it is the shape this exists to prevent, and "unreachable"
# is a claim that stops being true quietly.
_INPUT_TRAITS: dict[VariableInputType, InputTraits] = {
    "auto": InputTraits("text", "none", "none", "none"),
    "select": InputTraits("content", "arrow", "chooser", "none"),
    "multiselect": InputTraits("summary", "arrow", "chooser", "none"),
    "radio": InputTraits("content", "arrow", "chooser", "none"),
    "date": InputTraits("content", "calendar", "date", "none"),
    "datepicker": InputTraits("content", "calendar", "date", "none"),
    "daterange": InputTraits("daterange", "calendar", "range", "none"),
    "input": InputTraits("text", "none", "none", "none"),
    "text": InputTraits("text", "none", "none", "none"),
    "textarea": InputTraits("text", "none", "none", "none"),
    "number": InputTraits("number", "none", "none", "authored"),
    "slider": InputTraits("slider", "none", "none", "slider"),
    "range": InputTraits("slider", "none", "none", "slider"),
    "checkbox": InputTraits("checkbox", "none", "none", "none"),
}


def traits_of(input_type: VariableInputType) -> InputTraits:
    """The row for one input kind. Raises rather than guessing at an absent one."""
    return _INPUT_TRAITS[input_type]


# Widths reach here through several subtractions, so a row that fits exactly can
# read as overflowing by a fraction of a pixel. Sub-pixel slop gets the benefit
# of the doubt rather than costing a whole row. Public because the title-inline
# fit test asks the same question of the same numbers and has to answer it the
# same way — two epsilons kept in sync by comment is one epsilon that drifts.
WRAP_EPSILON = 0.5

# A lone filter is reset by clearing it; the item earns its room from two up.
MIN_CONTROLS_FOR_RESET = 2


@dataclass(frozen=True)
class ControlSpec:
    """One control to lay out, with its widget already resolved.

    ``input`` is the *refined* widget — the one the chrome will actually draw.
    Refinement needs option values, so it resolves once upstream and arrives
    here settled; laying out one widget while drawing another is the exact
    divergence this module exists to remove.
    """

    name: str
    input: VariableInputType
    label: str
    value: str


@dataclass(frozen=True)
class ControlBox:
    """One control's placed geometry, in board user units.

    ``label_width`` splits the box: the label draws in it, the field takes the
    rest after ``control_gap``. Published rather than re-measured downstream, so
    the width that was reserved is the width that gets drawn.
    """

    name: str
    input: VariableInputType
    label: str
    value: str
    x: float
    y: float
    width: float
    height: float
    label_width: float


@dataclass(frozen=True)
class ResetBox:
    """The reset item's placed geometry, in board user units."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class VariablesLayout:
    """Every control's box plus the band that contains them.

    ``reset`` is kept out of ``boxes``: hosts bind controls by index into it, and
    the reset item is not a control.
    """

    boxes: tuple[ControlBox, ...]
    height: float
    rows: int
    reset: ResetBox | None


def lay_out_variables(
    specs: Sequence[ControlSpec], width: float, variables_style: VariablesStyle
) -> VariablesLayout:
    """Place each control into rows no wider than ``width``.

    Returns boxes in the order given — document order is what a host binds
    against, so it is not reordered to pack rows more tightly. Two or more
    controls also reserve a reset item after the last one.
    """
    if not specs:
        return VariablesLayout(boxes=(), height=0.0, rows=0, reset=None)

    gap = float(variables_style.gap)
    row_height = float(variables_style.container_height)
    available = max(width, 1.0)

    boxes: list[ControlBox] = []
    x = 0.0
    row = 0
    font_size = variables_style.font.size
    assert font_size is not None, "style.variables.font.size must be configured"
    for spec in specs:
        label_width = _text_width(
            board_word("controls.field_label", label=spec.label), float(font_size)
        )
        # The ornament is added here, not inside `_input_width`, so a
        # theme-sized field reserves room for its glyph exactly like a measured
        # one — `daterange` is theme-sized and still draws a calendar.
        control_width = (
            label_width
            + float(variables_style.control_gap)
            + _input_width(spec, variables_style, float(font_size))
            + ornament_gap(spec.input, float(font_size))
            + ornament_width(spec.input, float(font_size))
        )
        # First control on a row is placed wherever it lands, even when it is
        # wider than the strip: an overrun is visible and debuggable, a dropped
        # filter is not.
        if boxes and x + control_width > available + WRAP_EPSILON:
            row += 1
            x = 0.0
        boxes.append(
            ControlBox(
                name=spec.name,
                input=spec.input,
                label=spec.label,
                value=_drawn_value(spec, variables_style, float(font_size)),
                x=x,
                y=row * (row_height + gap),
                width=control_width,
                height=row_height,
                label_width=label_width,
            )
        )
        x += control_width + gap

    reset = None
    if len(specs) >= MIN_CONTROLS_FOR_RESET:
        padding = variables_style.input.padding
        reset_width = (
            _text_width(board_word("controls.reset"), float(font_size))
            + float(padding.left)
            + float(padding.right)
        )
        if x + reset_width > available + WRAP_EPSILON:
            row += 1
            x = 0.0
        reset = ResetBox(
            x=x, y=row * (row_height + gap), width=reset_width, height=row_height
        )

    rows = row + 1
    return VariablesLayout(
        boxes=tuple(boxes),
        height=rows * row_height + (rows - 1) * gap,
        rows=rows,
        reset=reset,
    )


def ornament_width(input_type: VariableInputType, font_size: float) -> float:
    """Width the type ornament occupies inside a field, in board user units.

    Sized from the text beside it — a derivation, not a new theme field to
    author. The chrome draws exactly this; the layout reserves it plus
    :func:`ornament_gap`.
    """
    ornament = traits_of(input_type).ornament
    if ornament == "arrow":
        return font_size
    if ornament == "calendar":
        return font_size * 0.85
    return 0.0


def ornament_gap(input_type: VariableInputType, font_size: float) -> float:
    """Room between a field's value text and its ornament, in board user units.

    Reserved by the layout on top of ``ornament_width``; the chrome needs no
    say in it, because it places the glyph from the field's right edge and this
    is what moves that edge. Without it the glyph's left edge lands exactly on
    the text's right edge — these widths are measured in Python and drawn by
    Chromium, so at zero any variance between the two measurers renders as the
    chevron sitting on the value's final letters.

    Derived from the font like the glyph itself rather than authored: a theme
    field here would be one more knob nobody sets, and zero is not a value this
    may legally take. Font-derived rather than a fixed px because the glyph is
    too -- ``ornament_width`` is a multiple of the font size and every stroke
    inside it a ratio of that -- so a fixed gap would shrink against the glyph
    as type grows and bring the collision back at large sizes.

    Half an em, not a quarter: a quarter reads tight against the calendar,
    whose left edge is solid ink, where the chevron's leading stroke tip has
    optical air the metric does not show. Half clears both and still sits
    visibly inside the strip's structural spacing (``control_gap`` 8,
    inter-control ``gap`` 10), so the field reads as one object.
    """
    return font_size * 0.5 if ornament_width(input_type, font_size) > 0.0 else 0.0


def _input_width(
    spec: ControlSpec, variables_style: VariablesStyle, font_size: float
) -> float:
    """The input's rendered width — measured when content-driven, else themed."""
    widths = variables_style.input.widths
    padding = variables_style.input.padding

    sizing = traits_of(spec.input).sizing

    if sizing == "checkbox":
        return float(widths.checkbox)
    if sizing == "summary":
        return float(widths.text)
    if sizing == "slider":
        # The track is fixed; the value beside it is what varies.
        return (
            float(widths.range)
            + float(variables_style.control_gap)
            + max(_text_width(spec.value, font_size), float(widths.slider_value_min))
        )

    boxed = (
        max(_text_width(spec.value, font_size), font_size)
        + float(padding.left)
        + float(padding.right)
    )
    if sizing == "content":
        # Sized by what it displays. An unset control still needs a field to be
        # a field, so the text slot floors at one em (handled above).
        return boxed

    # Theme-sized. The theme width is a floor, not a ceiling: it sets the
    # resting size, but a value too long for it still gets a box that holds it —
    # the chrome draws the whole value, and the reserved width has to be the
    # drawn width or controls overlap in every static export.
    if sizing == "number":
        return max(float(widths.number), boxed)
    if sizing == "daterange":
        return max(float(widths.daterange), boxed)
    return max(float(widths.text), boxed)


def _drawn_value(
    spec: ControlSpec, variables_style: VariablesStyle, font_size: float
) -> str:
    """The text a field draws: a ``summary`` field's cut to its text slot."""
    if traits_of(spec.input).sizing != "summary":
        return spec.value
    padding = variables_style.input.padding
    slot = (
        float(variables_style.input.widths.text)
        - float(padding.left)
        - float(padding.right)
    )
    # The engine's `truncate_label_with` cuts the same way, so both renders draw
    # the same text. They differ only when not even "…" fits, a slot no theme
    # text width leaves.
    return truncate_text_precise(
        spec.value,
        slot,
        font_size,
        get_font_measurer(),
        ellipsis=True,
        normalize_whitespace=False,
    )


def _text_width(text: str, font_size: float) -> float:
    """Measured advance width of ``text`` at ``font_size``."""
    if not text:
        return 0.0
    return float(get_font_measurer().measure(text, font_size))
