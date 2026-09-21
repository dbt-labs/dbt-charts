"""The variables strip baked into every board SVG.

Stage: RENDER
Purpose: Draw every variable control as SVG, and mark the band a host runtime
can bind live behavior onto.

The server draws the controls because the server is the only party that knows
the board's coordinate system. A board is one fixed ``viewBox`` stretched to
whatever width it lands in, so anything a host lays *over* it is measured in
page pixels while the space reserved for it was measured in board units — the
two move in opposite directions as the board scales, which is exactly how a
strip ends up overlapping the title above it. Drawing the controls here puts
them in the same coordinate system as the box that holds them.

That also means converters (PNG/PDF), plain SVG viewers, and static exports get
the complete dashboard with its filter state spelled out — the same chrome, just
without anything wired to it.

What each control *is* is decided in ``variables_resolve``; where it sits is
decided in ``variables_layout``. This module only draws.
"""

from __future__ import annotations

import datetime
import html
import json
from typing import TYPE_CHECKING, Literal

from dbt_charts.core.compile.models.style.theme import font_weight_as_css
from dbt_charts.core.font_measure import centered_baseline_offset
from dbt_charts.core.render.controls import controls_are_interactive
from dbt_charts.core.render.svg_utils import authored_attrs, authored_kind_attr, px
from dbt_charts.core.render.variables_layout import (
    lay_out_variables,
    ornament_width,
    traits_of,
)
from dbt_charts.core.render.variables_resolve import (
    FREE_ENTRY_INPUTS,
    UNSET_DATE_LABEL,
    UNSET_DATERANGE_LABEL,
    UNSET_SELECT_LABEL,
    read_only_unset_label,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
    from dbt_charts.core.compile.models.style.theme.variables import VariablesStyle
    from dbt_charts.core.render.variables_layout import ControlBox, VariablesLayout
    from dbt_charts.core.render.variables_resolve import ResolvedControl

StripAlign = Literal["start", "end"]

# Every style field this module reads is filled by the cascade; a None is a
# broken theme, not a case to default around.
_CASCADE_GAP = "style.variables typography must be resolved before rendering"


def render_variables_strip_svg(
    controls: Sequence[ResolvedControl],
    width: float,
    resolved_style: ResolvedStyle,
    align: StripAlign = "start",
    *,
    variables_path: str,
) -> tuple[str, float]:
    """Draw the controls, plus the anchor box a host mounts against.

    Args:
        controls: Controls settled by ``resolve_controls``, in strip order.
        width: Available width for the strip.
        resolved_style: ResolvedStyle for color and font resolution.
        align: Which edge the strip packs against. The compact title-inline band
            packs its controls opposite the title ("end"); every other strip
            flows from the leading content edge.
        variables_path: Dotted path of the ``variables:`` mapping these controls
            come from, in the file being edited — ``"variables"`` for the root
            board, ``"rows.0.variables"`` for a board nested at ``rows.0``, and
            empty for a board imported from another file, whose keys are not in
            this document at all. Each control's handle is this plus its own
            name; empty means no handle, for the reason the chart wrapper
            carries none: coordinates from another file name whatever happens to
            sit at them in this one. Required, because the strip cannot know
            where it sits and a default would guess.

    Returns:
        Tuple of (SVG string, band height).
    """
    if not controls:
        return "", 0.0

    variables_style = resolved_style.variables
    layout = lay_out_variables(
        [control.spec for control in controls], width, variables_style
    )

    offsets = _row_offsets(layout, width, align)
    drawn = [
        _draw_control(control, box, offsets[box.y], variables_style, variables_path)
        for control, box in zip(controls, layout.boxes, strict=True)
    ]

    # The anchor sits outside the control group: a host hides the drawn chrome
    # when it swaps in its own, and a display:none element has no rect to
    # measure. The strip is transparent — only fields carry a background
    # (variables.input.background), never the container.
    anchor = (
        f'<rect data-dbt-variables-box="true" data-dbt-align="{align}" '
        f'class="dbt-pointer-inert" '
        f'x="0" y="0" width="{px(width)}" height="{px(layout.height)}" '
        'fill="none"/>'
    )
    # The border color rides on the group as a custom property: the board's
    # stylesheet names the property, the render supplies the theme's value, and
    # the two stay independent.
    return (
        f'<g data-dbt-variables="true" '
        f'style="--dbt-variable-field-border: {variables_style.border.color}">'
        f'{anchor}<g data-dbt-variables-static="true">{"".join(drawn)}</g></g>',
        layout.height,
    )


def _row_offsets(
    layout: VariablesLayout, width: float, align: StripAlign
) -> dict[float, float]:
    """How far each row shifts to pack against ``align``'s edge, keyed by row y.

    The layout engine flows every row from zero; packing right is a whole-row
    translation, so a control's position relative to its neighbors is decided
    in exactly one place.

    Never negative. A row wider than the strip has nowhere to pack against, and
    ``width - end`` would then walk it *backwards* out of the strip — into
    whatever precedes it, which on the title-inline band is the title. The
    layout engine already lets an over-wide control overrun the far edge on
    purpose (an overrun is visible and debuggable); reversing across the near
    edge just draws two things on top of each other.
    """
    if align != "end":
        return {box.y: 0.0 for box in layout.boxes}
    ends: dict[float, float] = {}
    for box in layout.boxes:
        ends[box.y] = box.x + box.width
    return {y: max(width - end, 0.0) for y, end in ends.items()}


def _draw_control(
    control: ResolvedControl,
    box: ControlBox,
    dx: float,
    variables_style: VariablesStyle,
    variables_path: str,
) -> str:
    """One control: its label, its field, and whatever ornament marks its type."""
    font_size = _font_size(variables_style)
    label_font = variables_style.label.font
    assert label_font.family is not None, _CASCADE_GAP
    assert label_font.weight is not None, _CASCADE_GAP
    family = html.escape(str(label_font.family))
    weight = font_weight_as_css(label_font.weight)

    x = box.x + dx
    field_x = x + box.label_width + float(variables_style.control_gap)
    field_width = max(
        box.width - box.label_width - float(variables_style.control_gap), 0.0
    )
    field_height = float(variables_style.input.height)
    field_y = box.y + (box.height - field_height) / 2
    baseline = (
        box.y + box.height / 2 + centered_baseline_offset(label_font.family, font_size)
    )

    # A variable is scoped to the board that declares it, so its handle is that
    # board's `variables:` key regardless of which chart reads it — the strip is
    # the one place a user can point at one. Unless the key only names it: a
    # cross-file reference has coordinates in this file but its definition in
    # another, and a handle to a reference string sends the click to the board.
    authored = variables_path and not control.var_def.defined_in_other_file
    handle = (
        authored_attrs(f"{variables_path}.{box.name}", "variable") if authored else ""
    )
    # The label run is the one part of a control that is not a value, which is
    # what makes it the control's handle as well as its one editable key: a
    # pointer aiming at the value surface belongs to the filter, and a host that
    # selected there would open a code panel on someone who was changing it.
    # Only when the control is authored here — no path, no keys, and the whole
    # control is value surface.
    label_kind = authored_kind_attr("label") if authored else ""

    parts = [
        f'<text{label_kind} x="{px(x)}" y="{px(baseline)}" '
        f'font-size="{px(font_size)}" '
        f'font-weight="{weight}" font-family="{family}" '
        f'fill="{variables_style.label.font.color}">{html.escape(box.label)}:</text>'
    ]
    parts.extend(
        _draw_field(
            control, box, field_x, field_y, field_width, field_height, variables_style
        )
    )

    # A gated-off control is drawn dimmed and says so, and the runtime declines
    # to bind it. Only the disabled case is published: the attribute marks a
    # departure from the norm rather than restating it on every control.
    disabled = ' data-dbt-enabled="false" opacity="0.55"' if not control.enabled else ""
    # A checkbox is the one widget whose state cannot be read back off its own
    # drawing, so it is published. The runtime toggles from this rather than
    # parsing the tick it drew.
    checked = (
        f' data-dbt-checked="{"true" if control.checked else "false"}"'
        if box.input == "checkbox"
        else ""
    )
    # The committed value, as the query sees it — not the drawn text, which is
    # a display form ("All" for an unset select, "Yes" for a ticked checkbox).
    # A host that reads the drawn text back would commit the label.
    value_attr = (
        f' data-dbt-value="{html.escape(_committed_value(control), quote=True)}"'
    )
    # A slider's bounds go out beside its value: the runtime maps a pointer to
    # a value and the drawn thumb maps that value back to a position, and the
    # two have to agree. Reading theme defaults on either side puts them in
    # different places for any authored range.
    # Not through `px`: that is an integer coordinate snapper, and these are the
    # authored bounds. `step: 0.1` through it becomes 0, which the runtime reads
    # as "no step" — and the drawn thumb, positioned from the raw floats, would
    # then disagree with the value a click commits.
    if traits_of(box.input).sizing == "slider":
        bounds = (
            f' data-dbt-min="{_number_attr(control.slider_min)}"'
            f' data-dbt-max="{_number_attr(control.slider_max)}"'
            f' data-dbt-step="{_number_attr(control.slider_step)}"'
        )
    elif box.input == "number":
        # Only what the author set: a number typed into a field is bounded by
        # nothing unless the board says so, never by the theme's slider range.
        bounds = "".join(
            f' data-dbt-{attr}="{_number_attr(value)}"'
            for attr, value in (
                ("min", control.var_def.min),
                ("max", control.var_def.max),
                ("step", control.var_def.step),
            )
            if value is not None
        )
    else:
        bounds = ""
    # The hint the lifted native input shows while empty — published whenever
    # one is authored, not only while the drawing shows it, since the field
    # can be emptied on the page.
    if box.input in FREE_ENTRY_INPUTS and control.var_def.placeholder:
        placeholder = html.escape(control.var_def.placeholder, quote=True)
        bounds += f' data-dbt-placeholder="{placeholder}"'
    # Whether an empty value is a legal state to land in, and what it reads as.
    # Without this a user can filter but never unfilter, and a required
    # multiselect emptied by unchecking its last member renders the next page
    # with no board — and so no control to recover from.
    unset = ""
    unset_style = traits_of(box.input).unset
    if unset_style != "none":
        default_label = (
            UNSET_SELECT_LABEL
            if unset_style == "chooser"
            else UNSET_DATE_LABEL
            if unset_style == "date"
            else UNSET_DATERANGE_LABEL
        )
        unset = f' data-dbt-can-unset="{"true" if control.can_unset else "false"}"'
        if control.can_unset:
            label = read_only_unset_label(control.var_def, default_label)
            unset += f' data-dbt-unset-label="{html.escape(label, quote=True)}"'
    # The chrome draws the committed value; the options a host opens travel
    # with it, so a live board needs no second round trip for something the
    # render already resolved. Only a live board: the full domain of a filter is
    # data the picture does not show, and an artifact has no runtime to open it
    # with. JSON, not a delimited string: an option may contain any delimiter.
    options = (
        f' data-dbt-options="{html.escape(json.dumps(list(control.option_values)), quote=True)}"'
        if control.option_values and controls_are_interactive()
        else ""
    )
    return (
        f'<g data-dbt-variable="{html.escape(box.name, quote=True)}"{handle} '
        f'data-dbt-input="{html.escape(box.input, quote=True)}"{checked}{disabled}{value_attr}{bounds}{unset}{options} '
        f'data-dbt-x="{px(x)}" data-dbt-y="{px(box.y)}" '
        f'data-dbt-width="{px(box.width)}" data-dbt-height="{px(box.height)}">'
        f"{''.join(parts)}</g>"
    )


def _number_attr(value: float) -> str:
    """A bound as the runtime must read it back: exact, never ``:g``'s six
    significant digits, which turned an authored 1234567 into 1.23457e+06."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _committed_value(control: ResolvedControl) -> str:
    """The value in the form a host commits it, never a Python repr.

    A multiselect or daterange holds a list; ``str()`` on one yields
    ``['a', 'b']``, which a host reading the attribute back would send as a
    literal filter value. Lists go out as JSON, matching how the options beside
    them are published.
    """
    current = control.current
    if current is None:
        return ""
    if isinstance(current, (list, tuple)):
        return json.dumps(
            [
                member.isoformat() if isinstance(member, datetime.date) else member
                for member in current
            ]
        )
    return str(current)


def _draw_field(
    control: ResolvedControl,
    box: ControlBox,
    x: float,
    y: float,
    width: float,
    height: float,
    variables_style: VariablesStyle,
) -> list[str]:
    """The input itself — a checkbox and a slider are shapes, the rest are fields."""
    sizing = traits_of(box.input).sizing
    if sizing == "checkbox":
        return _draw_checkbox(control, x, y, variables_style)
    if sizing == "slider":
        return _draw_slider(control, box, x, y, width, height, variables_style)
    return _draw_text_field(
        box, x, y, width, height, variables_style, control.placeholder is not None
    )


def _draw_text_field(
    box: ControlBox,
    x: float,
    y: float,
    width: float,
    height: float,
    variables_style: VariablesStyle,
    hint: bool,
) -> list[str]:
    """A rounded field showing the committed value (or, muted, the hint standing
    in for one), plus its type ornament."""
    font_size = _font_size(variables_style)
    value_font = variables_style.value.font
    assert value_font.family is not None, _CASCADE_GAP
    family = html.escape(str(value_font.family))
    input_style = variables_style.input
    text_x = x + float(input_style.padding.left)
    baseline = y + height / 2 + centered_baseline_offset(value_font.family, font_size)
    color = variables_style.placeholder.font.color if hint else value_font.color

    parts = [
        _field_rect(x, y, width, height, variables_style),
        f'<text x="{px(text_x)}" y="{px(baseline)}" font-size="{px(font_size)}" '
        f'font-family="{family}" fill="{color}">'
        f"{html.escape(box.value)}</text>",
    ]
    ornament = traits_of(box.input).ornament
    if ornament == "arrow":
        parts.append(_draw_arrow(x + width, y + height / 2, font_size, variables_style))
    elif ornament == "calendar":
        parts.append(
            _draw_calendar(x + width, y + height / 2, font_size, variables_style)
        )
    return parts


def _draw_checkbox(
    control: ResolvedControl, x: float, y: float, variables_style: VariablesStyle
) -> list[str]:
    """A square, ticked when the value reads as yes.

    The one field that keeps its outline at rest. Every other control states its
    value as text, so the box around it is decoration a static export can drop.
    An unticked checkbox has no text — with a transparent fill and no stroke it
    renders as nothing at all, and a reader cannot tell it from a variable that
    was never drawn. The box *is* the value here, so it is not an affordance to
    withhold.

    Stroked from the same source as ``--dbt-variable-field-border``, which is
    what the interactivity rule paints every field with — a different token here
    would repaint this one box the moment a host bound it. The width is stated
    rather than read from ``border.width``: that field is the *panel's* border
    and resolves to 0 on every theme, which is precisely the "no border" it is
    meant to express.
    """
    size = float(variables_style.input.widths.checkbox)
    top = y + (float(variables_style.input.height) - size) / 2
    parts = [
        f'<rect data-dbt-field="checkbox" data-dbt-ornament="checkbox" '
        f'x="{px(x)}" y="{px(top)}" width="{px(size)}" height="{px(size)}" '
        f'rx="{px(float(variables_style.input.border.radius))}" '
        f'fill="{variables_style.input.background}" '
        f'stroke="{variables_style.border.color}" stroke-width="1"/>'
    ]
    if control.checked:
        parts.append(
            f'<path d="M{px(x + size * 0.25)} {px(top + size * 0.5)} '
            f"L{px(x + size * 0.45)} {px(top + size * 0.7)} "
            f'L{px(x + size * 0.75)} {px(top + size * 0.3)}" '
            f'fill="none" stroke="{variables_style.value.font.color}" '
            'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    return parts


def _draw_slider(
    control: ResolvedControl,
    box: ControlBox,
    x: float,
    y: float,
    width: float,
    height: float,
    variables_style: VariablesStyle,
) -> list[str]:
    """A track with a thumb at the committed value, and the value beside it."""
    font_size = _font_size(variables_style)
    value_font = variables_style.value.font
    assert value_font.family is not None, _CASCADE_GAP
    family = html.escape(str(value_font.family))
    track_width = float(variables_style.input.widths.range)
    mid = y + height / 2
    thumb = x + track_width * _slider_fraction(control)

    return [
        f'<rect data-dbt-field="slider" data-dbt-ornament="slider" '
        f'x="{px(x)}" y="{px(mid - 2)}" width="{px(track_width)}" height="4" rx="2" '
        f'fill="{variables_style.input.background}" stroke="none"/>',
        f'<circle data-dbt-ornament="slider-thumb" cx="{px(thumb)}" cy="{px(mid)}" r="6" '
        f'fill="{variables_style.value.font.color}"/>',
        f'<text x="{px(x + track_width + float(variables_style.control_gap))}" '
        f'y="{px(mid + centered_baseline_offset(value_font.family, font_size))}" font-size="{px(font_size)}" '
        f'font-family="{family}" fill="{variables_style.value.font.color}">'
        f"{html.escape(box.value)}</text>",
    ]


def _slider_fraction(control: ResolvedControl) -> float:
    """Where the thumb sits, 0–1, against the bounds the control resolved to.

    Reading the theme's defaults here instead would put the drawn thumb and the
    control mounted over it in different places for any authored range.
    """
    try:
        value = float(control.current)
    except (TypeError, ValueError):
        return 0.0
    if control.slider_max <= control.slider_min:
        return 0.0
    span = control.slider_max - control.slider_min
    return min(max((value - control.slider_min) / span, 0.0), 1.0)


def _field_rect(
    x: float, y: float, width: float, height: float, variables_style: VariablesStyle
) -> str:
    """The field's background.

    Borderless at rest: the strip states the board's filter state, it does not
    pretend to be a form nobody can submit. ``stroke="none"`` is a presentation
    attribute rather than an omission, so the runtime that binds these controls
    can draw borders from a stylesheet without the server re-rendering — and
    since stroke takes no part in SVG layout, turning them on moves nothing.
    """
    input_style = variables_style.input
    return (
        f'<rect data-dbt-field="input" x="{px(x)}" y="{px(y)}" '
        f'width="{px(width)}" height="{px(height)}" '
        f'rx="{px(float(input_style.border.radius))}" '
        f'fill="{input_style.background}" stroke="none"/>'
    )


def _draw_arrow(
    right: float, mid: float, font_size: float, variables_style: VariablesStyle
) -> str:
    """The chevron that marks a field as a dropdown, in its reserved width.

    Hidden at rest for the same reason the field is borderless: a chevron says
    "open me", and an exported PNG cannot be opened. It is drawn rather than
    omitted so the runtime can reveal it from the stylesheet — opacity, like
    stroke, takes no part in SVG layout, so the toggle moves nothing.
    """
    size = ornament_width("select", font_size)
    x = right - float(variables_style.input.padding.right) - size
    drop = size * 0.32
    return (
        f'<path data-dbt-ornament="arrow" opacity="0" '
        f'd="M{px(x)} {px(mid - drop / 2)} L{px(x + size / 2)} {px(mid + drop / 2)} '
        f'L{px(x + size)} {px(mid - drop / 2)}" fill="none" '
        f'stroke="{variables_style.placeholder.font.color}" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    )


def _draw_calendar(
    right: float, mid: float, font_size: float, variables_style: VariablesStyle
) -> str:
    """The glyph that marks a field as a date picker, in its reserved width.

    Hidden at rest, revealed on bind — see :func:`_draw_arrow`.
    """
    size = ornament_width("date", font_size)
    x = right - float(variables_style.input.padding.right) - size
    top = mid - size / 2
    return (
        f'<g data-dbt-ornament="calendar" opacity="0" fill="none" '
        f'stroke="{variables_style.placeholder.font.color}" stroke-width="1.25">'
        f'<rect x="{px(x)}" y="{px(top + size * 0.12)}" width="{px(size)}" '
        f'height="{px(size * 0.88)}" rx="{px(size * 0.15)}"/>'
        f'<line x1="{px(x)}" y1="{px(top + size * 0.4)}" x2="{px(x + size)}" '
        f'y2="{px(top + size * 0.4)}"/></g>'
    )


def _font_size(variables_style: VariablesStyle) -> float:
    """The strip's base font size, which the cascade always fills."""
    assert variables_style.font.size is not None, _CASCADE_GAP
    return float(variables_style.font.size)
