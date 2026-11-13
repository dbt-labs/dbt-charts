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
`number`, `checkbox`, `slider`, `daterange`) keeps at least its theme width, so
``variables.input.widths.*`` stays load-bearing rather than silently orphaned.
A multiselect holds the text width whatever is ticked, and its text gives way
instead: a field that grew with each tick moved every control beside it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    segments: Literal["options", "presets", "none"]


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
    "auto": InputTraits("text", "none", "none", "none", "none"),
    "select": InputTraits("content", "arrow", "chooser", "none", "none"),
    "multiselect": InputTraits("summary", "arrow", "chooser", "none", "none"),
    "radio": InputTraits("content", "arrow", "chooser", "none", "options"),
    "date": InputTraits("content", "calendar", "date", "none", "none"),
    "datepicker": InputTraits("content", "calendar", "date", "none", "none"),
    "daterange": InputTraits("daterange", "calendar", "range", "none", "presets"),
    "input": InputTraits("text", "none", "none", "none", "none"),
    "text": InputTraits("text", "none", "none", "none", "none"),
    "textarea": InputTraits("text", "none", "none", "none", "none"),
    "number": InputTraits("number", "none", "none", "authored", "none"),
    "slider": InputTraits("slider", "none", "none", "slider", "none"),
    "range": InputTraits("slider", "none", "none", "slider", "none"),
    "checkbox": InputTraits("checkbox", "none", "none", "none", "none"),
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
    # The button row's segment texts; empty for a control drawn as a field.
    segment_labels: tuple[str, ...] = ()
    # Whether the buttons form draws the control's label. A timespan button row does
    # not: its buttons say what they span.
    segments_labeled: bool = True


@dataclass(frozen=True)
class ControlBox:
    """One control's placed geometry, in board user units.

    ``label_width`` splits the box: the label draws in it, the field takes the
    rest after ``control_gap``. Published rather than re-measured downstream, so
    the width that was reserved is the width that gets drawn.

    A button row publishes ``segment_rows`` (each row's segment widths; empty
    for a field). ``overflowed`` marks a button row that fell back to the field,
    ``buttons_width`` is what its buttons wanted on one row, and ``value_width``
    is the width of the value shown at rest in the button row's place.
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
    labeled: bool
    segment_rows: tuple[tuple[float, ...], ...]
    overflowed: bool
    value_width: float
    buttons_width: float


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
    """Place each control into rows no wider than ``width``, balanced.

    Returns boxes in the order given — document order is what a host binds
    against, so it is not reordered to pack rows more tightly. Two or more
    controls also reserve a reset item after the last one. A strip that wraps
    keeps the row count greedy packing needs and evens the rows out
    (:func:`_balanced_limit`), so it never strands one control on a last row.
    """
    if not specs:
        return VariablesLayout(boxes=(), height=0.0, rows=0, reset=None)

    gap = float(variables_style.gap)
    row_height = float(variables_style.container_height)
    available = max(width, 1.0)
    font_size = variables_style.font.size
    assert font_size is not None, "style.variables.font.size must be configured"
    padding = float(variables_style.input.padding.left) + float(
        variables_style.input.padding.right
    )
    measured: list[ControlBox] = []
    widths: list[float] = []
    spans: list[int] = []
    for spec in specs:
        segment_widths = tuple(
            _text_width(text, float(font_size)) + padding
            for text in spec.segment_labels
        )
        field_label_width = _text_width(
            board_word("controls.field_label", label=spec.label), float(font_size)
        )
        labeled = spec.segments_labeled
        label_width = field_label_width if labeled else 0.0
        lead = label_width + float(variables_style.control_gap) if labeled else 0.0
        value_width = (
            max(_text_width(spec.value, float(font_size)), float(font_size)) + padding
        )
        segment_rows: tuple[tuple[float, ...], ...] = ()
        overflowed = False
        button_rows = 1
        single_row = 0.0
        if segment_widths:
            single_row = lead + max(sum(segment_widths), value_width)
            if single_row <= available + WRAP_EPSILON:
                segment_rows = (segment_widths,)
            elif variables_style.overflow == "wrap":
                segment_rows = _wrap_segments(
                    segment_widths, max(available - lead, 1.0)
                )
            else:
                overflowed = True
        if segment_rows:
            button_rows = len(segment_rows)
            control_width = lead + max(max(sum(r) for r in segment_rows), value_width)
            box_height = button_rows * row_height + (button_rows - 1) * gap
        else:
            # The ornament is added here, not inside `_input_width`, so a
            # theme-sized field reserves room for its glyph exactly like a
            # measured one — `daterange` is theme-sized and still draws a calendar.
            label_width = field_label_width
            labeled = True
            control_width = (
                label_width
                + float(variables_style.control_gap)
                + _input_width(spec, variables_style, float(font_size))
                + ornament_gap(spec.input, float(font_size))
                + ornament_width(spec.input, float(font_size))
            )
            box_height = row_height
        measured.append(
            ControlBox(
                name=spec.name,
                input=spec.input,
                label=spec.label,
                value=_drawn_value(spec, variables_style, float(font_size)),
                x=0.0,
                y=0.0,
                width=control_width,
                height=box_height,
                label_width=label_width,
                labeled=labeled,
                segment_rows=segment_rows,
                overflowed=overflowed,
                value_width=value_width,
                buttons_width=single_row,
            )
        )
        widths.append(control_width)
        spans.append(button_rows)
    if len(specs) >= MIN_CONTROLS_FOR_RESET:
        reset_padding = variables_style.input.padding
        widths.append(
            _text_width(board_word("controls.reset"), float(font_size))
            + float(reset_padding.left)
            + float(reset_padding.right)
        )
        spans.append(1)
    limit = _balanced_limit(widths, spans, gap, available)
    places = _flow(widths, spans, gap, limit)

    boxes = tuple(
        replace(box, x=x, y=row * (row_height + gap))
        for box, (x, row) in zip(measured, places, strict=False)
    )
    reset = None
    if len(widths) > len(specs):
        x, row = places[-1]
        reset = ResetBox(
            x=x, y=row * (row_height + gap), width=widths[-1], height=row_height
        )

    rows = places[-1][1] + spans[-1]
    return VariablesLayout(
        boxes=boxes,
        height=rows * row_height + (rows - 1) * gap,
        rows=rows,
        reset=reset,
    )


def _wrap_segments(
    widths: tuple[float, ...], room: float
) -> tuple[tuple[float, ...], ...]:
    """Flow segment widths into rows no wider than ``room`` (one per row at least)."""
    rows: list[list[float]] = [[]]
    used = 0.0
    for width in widths:
        if rows[-1] and used + width > room + WRAP_EPSILON:
            rows.append([])
            used = 0.0
        rows[-1].append(width)
        used += width
    return tuple(tuple(r) for r in rows)


def _flow(
    widths: Sequence[float], spans: Sequence[int], gap: float, limit: float
) -> list[tuple[float, int]]:
    """Each item's ``(x, row)``, filling rows no wider than ``limit`` in order.

    The first item on a row is placed wherever it lands, even when it is wider
    than the strip: an overrun is visible and debuggable, a dropped filter is not.
    An item spanning several rows (wrapped buttons) owns them: whatever follows
    starts below it.
    """
    places: list[tuple[float, int]] = []
    x = 0.0
    row = 0
    for w, span in zip(widths, spans, strict=True):
        if places and x + w > limit + WRAP_EPSILON:
            row += 1
            x = 0.0
        places.append((x, row))
        x += w + gap
        if span > 1:
            row += span - 1
            x = limit + gap
    return places


def _row_count(
    widths: Sequence[float], spans: Sequence[int], gap: float, limit: float
) -> int:
    return _flow(widths, spans, gap, limit)[-1][1] + spans[-1]


def _balanced_limit(
    widths: Sequence[float], spans: Sequence[int], gap: float, available: float
) -> float:
    """The narrowest row limit that keeps greedy packing's row count.

    Every row :func:`_flow` can produce is a run of consecutive items, so the
    narrowest limit is one of those runs' widths (summed exactly as ``_flow``
    sums them); the first, ascending, that packs into as few rows as
    ``available`` does is the most even split.
    """
    rows = _row_count(widths, spans, gap, available)
    if rows == 1:
        return available
    runs: set[float] = set()
    for start in range(len(widths)):
        x = 0.0
        for w in widths[start:]:
            if x + w > available + WRAP_EPSILON:
                break
            runs.add(x + w)
            x += w + gap
    for limit in sorted(runs):
        if _row_count(widths, spans, gap, limit) == rows:
            return limit
    return available


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
        return max(float(widths.checkbox), _text_width(spec.value, font_size))
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
