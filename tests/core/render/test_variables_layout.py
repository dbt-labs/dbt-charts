"""The variables strip's layout engine decides geometry; nothing else guesses it.

One function owns where every control sits. The chrome renderer draws from its
boxes and the band takes its height, so the two cannot disagree — which is the
whole defect this replaces: a sizer that estimated widths from a character count
while the browser laid out something else.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.variables_layout import ControlSpec, lay_out_variables

_WIDE = 1112.0


def _style():
    return resolve_style(get_theme_style()).variables


def _spec(name: str, input_type: str, label: str, value: str = "") -> ControlSpec:
    return ControlSpec(name=name, input=input_type, label=label, value=value)


def test_a_single_control_starts_at_the_origin() -> None:
    layout = lay_out_variables([_spec("region", "text", "Region")], _WIDE, _style())

    assert layout.rows == 1
    assert len(layout.boxes) == 1
    assert layout.boxes[0].x == 0.0
    assert layout.boxes[0].y == 0.0


def test_controls_that_fit_share_one_row() -> None:
    specs = [_spec(f"v{i}", "text", f"Var {i}") for i in range(3)]

    layout = lay_out_variables(specs, _WIDE, _style())

    assert layout.rows == 1
    assert {box.y for box in layout.boxes} == {0.0}
    xs = [box.x for box in layout.boxes]
    assert xs == sorted(xs), "controls advance left to right"


def test_controls_wrap_when_the_row_runs_out() -> None:
    """A width that fits one control forces every later one onto its own row."""
    specs = [_spec(f"v{i}", "text", f"Var {i}") for i in range(3)]
    one_control_wide = lay_out_variables(specs[:1], _WIDE, _style()).boxes[0].width

    layout = lay_out_variables(specs, one_control_wide + 1.0, _style())

    assert layout.rows == 3
    assert len({box.y for box in layout.boxes}) == 3


def test_no_two_boxes_overlap() -> None:
    """The invariant the whole initiative exists to guarantee."""
    specs = [
        _spec("segment", "select", "Segment (Current)", "All"),
        _spec("account", "text", "Account Name"),
        _spec("active", "checkbox", "Active"),
        _spec("since", "date", "Since", "2026-01-01"),
        _spec("limit", "slider", "Limit", "15"),
    ]

    layout = lay_out_variables(specs, 600.0, _style())

    for i, a in enumerate(layout.boxes):
        for b in layout.boxes[i + 1 :]:
            separated = (
                a.x + a.width <= b.x
                or b.x + b.width <= a.x
                or a.y + a.height <= b.y
                or b.y + b.height <= a.y
            )
            assert separated, f"{a.name} overlaps {b.name}"


def test_every_box_fits_inside_the_available_width() -> None:
    specs = [
        _spec("segment", "select", "Segment (Current)", "Strategic Named Accounts"),
        _spec(
            "region", "select", "Parent Region Name", "Latin America and the Caribbean"
        ),
    ]

    layout = lay_out_variables(specs, _WIDE, _style())

    for box in layout.boxes:
        assert box.x + box.width <= _WIDE + 0.5


def test_band_height_covers_every_row() -> None:
    specs = [_spec(f"v{i}", "text", f"Var {i}") for i in range(6)]

    layout = lay_out_variables(specs, 400.0, _style())

    assert layout.rows > 1
    for box in layout.boxes:
        assert box.y + box.height <= layout.height + 0.5


def test_a_longer_value_makes_a_content_sized_control_wider() -> None:
    """A select is sized by what it displays, not by a constant.

    The estimator this replaces used a fixed 120px for every select, which is
    where its +155% single-control error came from.
    """
    short = lay_out_variables([_spec("r", "select", "Region", "All")], _WIDE, _style())
    long = lay_out_variables(
        [_spec("r", "select", "Region", "Latin America and the Caribbean")],
        _WIDE,
        _style(),
    )

    assert long.boxes[0].width > short.boxes[0].width


def test_a_longer_label_makes_any_control_wider() -> None:
    """Labels are measured, not counted — for fixed-width inputs too."""
    short = lay_out_variables([_spec("a", "text", "ID")], _WIDE, _style())
    long = lay_out_variables(
        [_spec("a", "text", "CSM Owner Reports to Email")], _WIDE, _style()
    )

    assert long.boxes[0].width > short.boxes[0].width


def test_a_theme_sized_input_keeps_its_width_for_a_value_that_fits() -> None:
    """`text` is theme-sized, so its box must not twitch with every keystroke.

    Keeps the theme's `input.widths.*` load-bearing rather than silently
    orphaned by measuring everything.
    """
    empty = lay_out_variables([_spec("a", "text", "Note", "")], _WIDE, _style())
    filled = lay_out_variables([_spec("a", "text", "Note", "short")], _WIDE, _style())

    assert filled.boxes[0].width == empty.boxes[0].width


def test_a_theme_width_is_a_floor_not_a_ceiling() -> None:
    """A value too long for its themed field widens the box that holds it.

    Regression: theme-sized fields reserved a fixed width while the chrome drew
    the value at full length, so a long committed value ran across the gap and
    into the next control — in exactly the static exports this strip exists to
    serve, where no `<input>` clips it.
    """
    short = lay_out_variables([_spec("a", "text", "Note", "short")], _WIDE, _style())
    long = lay_out_variables(
        [_spec("a", "text", "Note", "customer_lifetime_value_by_cohort_2026")],
        _WIDE,
        _style(),
    )

    assert long.boxes[0].width > short.boxes[0].width


def test_layout_is_deterministic() -> None:
    """Same input, same boxes — the chrome renderer and the band share one call."""
    specs = [_spec("a", "select", "A", "x"), _spec("b", "text", "B")]

    first = lay_out_variables(specs, _WIDE, _style())
    second = lay_out_variables(specs, _WIDE, _style())

    assert first == second


def test_no_controls_is_an_empty_layout() -> None:
    layout = lay_out_variables([], _WIDE, _style())

    assert layout.boxes == ()
    assert layout.rows == 0
    assert layout.height == 0.0


def test_a_control_wider_than_the_strip_still_gets_a_box() -> None:
    """A single control too wide to fit is placed, not dropped or clipped.

    It overflows its row rather than disappearing — a visible overrun is
    debuggable, a silently dropped filter is not.
    """
    spec = _spec("r", "select", "Parent Region Name", "Latin America and the Caribbean")

    layout = lay_out_variables([spec], 50.0, _style())

    assert len(layout.boxes) == 1
    assert layout.rows == 1
    assert layout.boxes[0].width > 50.0


@pytest.mark.parametrize(
    "input_type",
    [
        "select",
        "multiselect",
        "text",
        "number",
        "slider",
        "checkbox",
        "date",
        "daterange",
    ],
)
def test_every_input_type_gets_a_positive_box(input_type: str) -> None:
    layout = lay_out_variables([_spec("v", input_type, "Var", "x")], _WIDE, _style())

    assert layout.boxes[0].width > 0
    assert layout.boxes[0].height > 0


def test_a_box_publishes_where_its_label_ends() -> None:
    """The chrome renderer draws label and field from one split, not two guesses."""
    style = _style()
    layout = lay_out_variables([_spec("r", "text", "Region")], _WIDE, style)

    box = layout.boxes[0]
    assert 0.0 < box.label_width < box.width
    assert box.label_width + style.control_gap < box.width


def test_a_row_that_fits_to_the_last_sub_pixel_stays_one_row() -> None:
    """Float drift must not cost a row.

    Widths accumulate through several subtractions before they get here, so a
    row that fits exactly can read as overflowing by ~1e-14. Regression: that
    pushed a two-control strip onto two rows.
    """
    specs = [_spec("a", "text", "A"), _spec("b", "text", "B")]
    exact = lay_out_variables(specs, _WIDE, _style())
    total = exact.boxes[-1].x + exact.boxes[-1].width

    layout = lay_out_variables(specs, total - 1e-9, _style())

    assert layout.rows == 1


def test_a_daterange_is_as_wide_as_the_theme_says() -> None:
    """Fixed-width inputs read `variables.input.widths.*`, never a constant."""
    from dbt_charts.core.render.variables_layout import ornament_gap, ornament_width

    style = _style()
    layout = lay_out_variables([_spec("d", "daterange", "Date Range")], _WIDE, style)

    box = layout.boxes[0]
    assert box.width == pytest.approx(
        box.label_width
        + style.control_gap
        + style.input.widths.daterange
        + ornament_gap("daterange", style.font.size)
        + ornament_width("daterange", style.font.size)
    )


def test_a_theme_sized_field_still_reserves_room_for_its_ornament() -> None:
    """Regression: `daterange` is theme-sized and draws a calendar.

    The ornament allowance used to live inside the content-sized branch only,
    so a theme-width field drew a glyph into space nobody had reserved — and
    raising `variables.font.size` ran the date text under it.
    """
    style = _style()
    layout = lay_out_variables([_spec("d", "daterange", "When")], _WIDE, style)

    box = layout.boxes[0]
    field = box.width - box.label_width - style.control_gap
    assert field > style.input.widths.daterange


@pytest.mark.parametrize(
    ("input_type", "has_ornament"),
    [
        ("select", True),
        ("multiselect", True),
        ("date", True),
        ("daterange", True),
        ("text", False),
        ("number", False),
        ("checkbox", False),
    ],
)
def test_one_table_decides_which_inputs_carry_an_ornament(
    input_type: str, has_ornament: bool
) -> None:
    """The width reserved and the glyph drawn come from the same answer."""
    from dbt_charts.core.render.variables_layout import ornament_width

    assert (ornament_width(input_type, 11.0) > 0) is has_ornament


@pytest.mark.parametrize(
    ("input_type", "value"),
    [
        ("select", "All"),
        ("select", "Latin America and the Caribbean"),
        ("multiselect", "North, South"),
        ("date", "2026-04-10"),
        ("date", "Any date"),
    ],
)
def test_the_value_text_never_reaches_its_ornament(input_type: str, value: str) -> None:
    """Every ornamented field reserves breathing room between text and glyph.

    The reserved width used to be ``text + padding + ornament`` exactly, which
    puts the glyph's left edge on the text's right edge — a zero gap, at every
    viewport and every value length. Zero is not a rounding error to absorb:
    the widths here are measured in Python and drawn by Chromium, so any
    variance between the two measurers shows up as the chevron sitting on the
    final letters. The arithmetic below is the chrome's own
    (``variables_strip._draw_text_field`` / ``_draw_arrow``), read through the
    layout's publics — if the two ever disagree, the drawn glyph is not where
    this says it is, which is why
    ``test_variables_chrome.test_the_drawn_value_never_reaches_its_drawn_ornament``
    asserts the same property off the emitted SVG instead.

    ``daterange`` is deliberately absent: it is theme-sized rather than
    content-sized, with ~107 units of slack at any value that fits, so it never
    collided and a parameter for it would pass on unfixed code.
    """
    from dbt_charts.core.font_measure import get_font_measurer
    from dbt_charts.core.render.variables_layout import ornament_width

    style = _style()
    font_size = float(style.font.size)
    padding = style.input.padding
    box = lay_out_variables(
        [_spec("v", input_type, "When", value)], _WIDE, style
    ).boxes[0]

    field_x = box.x + box.label_width + float(style.control_gap)
    field_width = box.width - box.label_width - float(style.control_gap)
    text_right = (
        field_x + float(padding.left) + get_font_measurer().measure(value, font_size)
    )
    ornament_left = (
        field_x
        + field_width
        - float(padding.right)
        - ornament_width(input_type, font_size)
    )

    assert ornament_left > text_right, (
        f"{input_type} {value!r}: ornament starts at {ornament_left:.2f}, text ends "
        f"at {text_right:.2f} — the glyph is drawn on top of the value"
    )


def test_an_unornamented_field_reserves_no_gap() -> None:
    """The gap is the ornament's, not every field's — a text input keeps its width."""
    from dbt_charts.core.render.variables_layout import ornament_gap

    assert ornament_gap("text", 11.0) == 0.0
    assert ornament_gap("checkbox", 11.0) == 0.0
    assert ornament_gap("select", 11.0) > 0.0


# --- Every input type is classified, and the classification is observable ---
#
# `VariableInputType` has fourteen members and the layout answers two questions
# of each: how the field is sized, and which glyph the chrome draws inside it.
# Both used to be hand-written subsets with a silent fall-through, so a new
# member landed on the text width with no ornament and nothing said so —
# dropping `radio` from two of those subsets passed 3,382 tests.


def test_every_variable_input_type_is_classified() -> None:
    """A new member of the enum has to be classified before it can render.

    The gate the three hand-written subsets never had. `auto` is carried
    explicitly rather than skipped: `detect_variable_input_type` always resolves
    it before layout, and a table with a hole in it is the thing this prevents.
    """
    from typing import get_args

    from dbt_charts.core.compile.models.variable.authored import VariableInputType
    from dbt_charts.core.render.variables_layout import _INPUT_TRAITS

    assert set(_INPUT_TRAITS) == set(get_args(VariableInputType))


@pytest.mark.parametrize("input_type", ["select", "multiselect", "radio"])
def test_a_chooser_reserves_room_for_its_arrow(input_type: str) -> None:
    """The arrow is drawn inside the field, so the field has to be measured to
    hold it. A chooser that loses its ornament silently narrows by one em."""
    from dbt_charts.core.render.variables_layout import ornament_width

    assert ornament_width(input_type, 10.0) == 10.0


@pytest.mark.parametrize("input_type", ["date", "datepicker", "daterange"])
def test_a_date_field_reserves_room_for_its_calendar(input_type: str) -> None:
    from dbt_charts.core.render.variables_layout import ornament_width

    assert ornament_width(input_type, 10.0) == pytest.approx(8.5)


@pytest.mark.parametrize(
    "input_type",
    ["auto", "input", "text", "number", "textarea", "slider", "range", "checkbox"],
)
def test_every_other_input_draws_no_ornament(input_type: str) -> None:
    """The mirror, and the half a fall-through gets right by accident. Stated so
    that a new member cannot join this row without someone choosing it."""
    from dbt_charts.core.render.variables_layout import ornament_width

    assert ornament_width(input_type, 10.0) == 0.0


def test_a_chooser_is_sized_by_what_it_displays_and_a_text_box_has_a_floor() -> None:
    """Content sizing versus theme sizing, pinned behaviorally.

    A chooser is measured from the text it will draw and nothing else. A
    theme-sized field takes its width from the theme instead — a floor, not a
    ceiling, so a long value still gets a box that holds it, but a short one
    does not shrink below the resting size. The observable difference is at the
    short end: same value, different width.
    """
    select = lay_out_variables(
        [_spec("r", "select", "R", "All")], _WIDE, _style()
    ).boxes[0]
    text = lay_out_variables([_spec("r", "text", "R", "All")], _WIDE, _style()).boxes[0]

    assert select.width < text.width, (
        "a chooser is measured from its value; a text field floors at the theme width"
    )

    long_value = "Latin America and the Caribbean"
    grown = lay_out_variables(
        [_spec("r", "select", "R", long_value)], _WIDE, _style()
    ).boxes[0]
    assert grown.width > select.width, "a chooser grows with its value"
