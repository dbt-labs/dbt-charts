"""`input: radio` and inline daterange presets draw as a button row of segments.

The button row is server-drawn SVG like every other control: segments are plain
links (no JS needed), geometry is identical at rest and interactive, and a
control too wide for its slot degrades to the menu form with a warning.
"""

from __future__ import annotations

import dataclasses
import datetime
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.variable.authored import Variable, VariableOptions
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.compile.template.daterange import (
    BoardClock,
    board_clock,
    parse_daterange_preset,
)
from dbt_charts.core.render.board_variables import daterange_presets
from dbt_charts.core.render.controls import compact_preset_label, interactive_controls
from dbt_charts.core.render.variables_buttons_overflow import collect_buttons_overflows
from dbt_charts.core.render.variables_layout import (
    WRAP_EPSILON,
    lay_out_variables,
)
from dbt_charts.core.render.variables_resolve import resolve_controls
from dbt_charts.core.render.variables_strip import render_variables_strip_svg

_CLOCK = BoardClock(
    datetime.date(2026, 10, 20), fiscal_start_month=1, week_start=1, time_zone="UTC"
)
_GROUP_BY = ["Region", "Sub Region", "Manager"]


class _NoQueries:
    """The render pass's executor: its presence settles `enabled`."""

    def execute_query(self, name, variables):
        raise AssertionError(f"no query expected, got {name!r}")


def _radio(required: bool = False, options: list[str] | None = None) -> Variable:
    return Variable(
        input="radio",
        options=VariableOptions(static=options or _GROUP_BY),
        required=required,
        default="Region" if required else None,
    )


def _draw(
    defs: dict[str, Variable],
    values: dict[str, object] | None = None,
    width: float = 1000.0,
    resolved=None,
    presets: dict[str, str] | None = None,
) -> str:
    resolved = resolved or resolve_style(get_theme_style())
    with board_clock(_CLOCK), daterange_presets(presets or {}):
        controls = resolve_controls(
            defs, values or {}, _NoQueries(), resolved.variables
        )
        svg, _ = render_variables_strip_svg(
            controls, width, resolved, variables_path="variables"
        )
    return svg


def _tree(svg: str) -> ET.Element:
    return ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")


def _segments(svg: str) -> list[ET.Element]:
    return [e for e in _tree(svg).iter() if e.get("data-dbt-segment") is not None]


def _href_params(segment: ET.Element) -> dict[str, list[str]]:
    href = segment.get("href")
    assert href is not None and href.startswith("?")
    return parse_qs(href[1:], keep_blank_values=True)


def test_radio_draws_one_link_segment_per_option_in_order() -> None:
    svg = _draw({"group_by": _radio()})

    segments = _segments(svg)
    assert [s.get("data-dbt-segment") for s in segments] == _GROUP_BY
    assert all(s.tag.endswith("}a") for s in segments)
    assert [_href_params(s)["group_by"] for s in segments] == [[o] for o in _GROUP_BY]


def test_the_selected_segment_is_marked_and_the_others_are_not() -> None:
    svg = _draw({"group_by": _radio()}, {"group_by": "Sub Region"})

    assert [s.get("aria-checked") for s in _segments(svg)] == [
        "false",
        "true",
        "false",
    ]


def test_an_unset_radio_selects_nothing_and_adds_no_all_segment() -> None:
    svg = _draw({"group_by": _radio()})

    segments = _segments(svg)
    assert len(segments) == len(_GROUP_BY)
    assert {s.get("aria-checked") for s in segments} == {"false"}


def test_the_selected_segment_unsets_when_the_variable_is_optional() -> None:
    svg = _draw({"group_by": _radio()}, {"group_by": "Region"})

    selected = _segments(svg)[0]
    assert _href_params(selected)["group_by"] == [""]


def test_a_required_variables_selected_segment_has_no_link() -> None:
    svg = _draw({"group_by": _radio(required=True)}, {"group_by": "Region"})

    selected, other, _ = _segments(svg)
    assert selected.get("href") is None
    assert other.get("href") is not None


def test_a_segment_link_keeps_the_boards_other_variables() -> None:
    defs = {"group_by": _radio(), "region": Variable(input="text")}
    svg = _draw(defs, {"region": "West"})

    assert _href_params(_segments(svg)[0])["region"] == ["West"]


def test_a_disabled_control_draws_its_segments_without_links() -> None:
    disabled = Variable(
        input="radio", options=VariableOptions(static=_GROUP_BY), enabled=False
    )

    svg = _draw({"group_by": disabled})

    assert [s.get("href") for s in _segments(svg)] == [None, None, None]


def test_the_buttons_publishes_its_form_for_the_host() -> None:
    svg = _draw({"group_by": _radio()})

    group = next(
        e for e in _tree(svg).iter() if e.get("data-dbt-variable") == "group_by"
    )
    assert group.get("data-dbt-form") == "buttons"


def test_segments_are_hidden_at_rest_and_the_value_is_not() -> None:
    """An export shows the selected value; the host reveals the segments."""
    svg = _draw({"group_by": _radio()}, {"group_by": "Manager"})

    root = _tree(svg)
    buttons = next(e for e in root.iter() if e.get("data-dbt-buttons") is not None)
    at_rest = next(
        e for e in root.iter() if e.get("data-dbt-buttons-value") is not None
    )
    assert buttons.get("opacity") == "0"
    assert at_rest.get("opacity") is None
    assert "Manager" in [e.text for e in at_rest.iter() if e.tag.endswith("text")]


def test_the_drawing_does_not_depend_on_whether_a_host_is_listening() -> None:
    plain = _draw({"group_by": _radio()}, {"group_by": "Region"})
    with interactive_controls(True):
        live = _draw({"group_by": _radio()}, {"group_by": "Region"})

    # Behavior payload may differ; geometry never does.
    def geometry(svg: str) -> list[tuple[str, str, str, str]]:
        return [
            (
                e.get("x") or "",
                e.get("y") or "",
                e.get("width") or "",
                e.get("height") or "",
            )
            for e in _tree(svg).iter()
            if e.tag.endswith("rect")
        ]

    assert geometry(plain) == geometry(live)


def test_the_control_reserves_the_buttons_full_width_when_a_value_is_shown() -> None:
    resolved = resolve_style(get_theme_style())
    with board_clock(_CLOCK):
        controls = resolve_controls(
            {"group_by": _radio()}, {"group_by": "Region"}, None, resolved.variables
        )
        layout = lay_out_variables(
            [c.spec for c in controls], 1000.0, resolved.variables
        )
        short = resolve_controls(
            {"group_by": _radio(options=["A", "B"])},
            {"group_by": "A"},
            None,
            resolved.variables,
        )
        short_layout = lay_out_variables(
            [c.spec for c in short], 1000.0, resolved.variables
        )

    assert layout.boxes[0].width > short_layout.boxes[0].width
    box = layout.boxes[0]
    assert sum(box.segment_rows[0]) <= box.width - box.label_width


def test_a_buttons_wider_than_its_slot_draws_the_menu_and_reports_it() -> None:
    with collect_buttons_overflows() as overflows:
        svg = _draw({"group_by": _radio()}, width=120.0)

    assert _segments(svg) == []
    group = next(
        e for e in _tree(svg).iter() if e.get("data-dbt-variable") == "group_by"
    )
    assert group.get("data-dbt-form") is None
    assert [o.variable for o in overflows] == ["group_by"]


def test_the_overflow_warning_names_the_wrap_key() -> None:
    from dbt_charts.core.diagnostics import WARN_VARIABLE_BUTTONS_OVERFLOW

    assert "style.variables.overflow" in WARN_VARIABLE_BUTTONS_OVERFLOW.fix_template


def test_a_buttons_that_fits_reports_nothing() -> None:
    with collect_buttons_overflows() as overflows:
        _draw({"group_by": _radio()}, width=1000.0)

    assert overflows == []


def test_wrap_lets_the_buttons_flow_onto_further_rows() -> None:
    style = resolve_style(get_theme_style())
    wrapped = style.variables.model_copy(update={"overflow": "wrap"})
    with board_clock(_CLOCK):
        controls = resolve_controls({"g": _radio()}, {}, None, wrapped)
        layout = lay_out_variables([c.spec for c in controls], 200.0, wrapped)

    box = layout.boxes[0]
    assert len(box.segment_rows) > 1
    assert layout.rows == len(box.segment_rows)
    assert box.width <= 200.0 + WRAP_EPSILON
    assert all(len(row) > 0 for row in box.segment_rows)
    assert not box.overflowed


def test_wrap_reports_no_overflow() -> None:
    style = resolve_style(get_theme_style())
    wrapped = style.variables.model_copy(update={"overflow": "wrap"})
    with collect_buttons_overflows() as overflows:
        _draw({"g": _radio()}, width=200.0, resolved=_with(style, wrapped))

    assert overflows == []


def _with(resolved, variables):
    return dataclasses.replace(resolved, variables=variables)


def _presets_style(display: str):
    style = resolve_style(get_theme_style())
    return _with(style, style.variables.model_copy(update={"presets_display": display}))


_FINANCE = [
    "today",
    "last_5_days",
    "last_1_months",
    "last_3_months",
    "last_6_months",
    "year_to_date",
    "last_12_months",
    "last_5_years",
]


def _span() -> Variable:
    return Variable(input="daterange", presets=_FINANCE)


def test_menu_display_keeps_the_daterange_a_field() -> None:
    svg = _draw({"span": _span()}, resolved=_presets_style("menu"))

    assert _segments(svg) == []


def test_buttons_display_draws_compact_preset_segments_that_link_by_name() -> None:
    svg = _draw({"span": _span()}, resolved=_presets_style("buttons"))

    segments = _segments(svg)
    assert [s.get("data-dbt-segment") for s in segments] == _FINANCE
    assert [_href_params(s)["span"] for s in segments] == [[n] for n in _FINANCE]
    labels = [
        "".join(t.text or "" for t in s.iter() if t.tag.endswith("text"))
        for s in segments
    ]
    assert labels == ["1D", "5D", "MTD", "3M", "6M", "YTD", "12M", "5Y"]


def test_a_preset_segment_never_carries_absolute_dates() -> None:
    svg = _draw(
        {"span": _span()},
        {"span": [datetime.date(2026, 4, 20), datetime.date(2026, 10, 20)]},
        resolved=_presets_style("buttons"),
        presets={"span": "last_6_months"},
    )

    hrefs = [s.get("href") or "" for s in _segments(svg)]
    assert not any("2026" in href for href in hrefs)
    selected = [s for s in _segments(svg) if s.get("aria-checked") == "true"]
    assert [s.get("data-dbt-segment") for s in selected] == ["last_6_months"]
    assert _href_params(selected[0])["span"] == [""]


def test_the_timespan_buttons_draws_no_label() -> None:
    svg = _draw({"span": _span()}, resolved=_presets_style("buttons"))

    group = next(e for e in _tree(svg).iter() if e.get("data-dbt-variable") == "span")
    own_text = [
        e
        for e in group.findall("{http://www.w3.org/2000/svg}text")
        if (e.text or "").strip()
    ]
    assert own_text == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("today", "1D"),
        ("last_5_days", "5D"),
        ("last_2_weeks", "2W"),
        ("last_1_months", "MTD"),
        ("last_1_weeks", "WTD"),
        ("last_1_quarters", "QTD"),
        ("last_1_years", "YTD"),
        ("last_1_days", "1D"),
        ("last_12_months", "12M"),
        ("last_3_quarters", "3Q"),
        ("last_5_years", "5Y"),
        ("year_to_date", "YTD"),
        ("month_to_date", "MTD"),
    ],
)
def test_compact_labels_follow_the_preset_grammar(name: str, expected: str) -> None:
    assert compact_preset_label(parse_daterange_preset(name)) == expected


@pytest.mark.parametrize(
    "name",
    ["previous_3_months", "this_fiscal_quarter", "last_4_fiscal_quarters", "yesterday"],
)
def test_a_form_with_no_compact_spelling_keeps_its_long_label(name: str) -> None:
    from dbt_charts.core.render.controls import preset_label

    preset = parse_daterange_preset(name)
    assert compact_preset_label(preset) == preset_label(preset)


def test_the_host_reveals_the_buttons_and_the_board_carries_no_stylesheet_for_it() -> (
    None
):
    from dbt_charts.core.render.controls import (
        controls_runtime_source,
        controls_stylesheet,
    )

    sheet = controls_stylesheet()
    assert ".dbt-interactive [data-dbt-buttons]" in sheet
    assert ".dbt-interactive [data-dbt-buttons-value]" in sheet
    assert "radiogroup" in controls_runtime_source()
    assert "<style" not in _draw({"group_by": _radio()})


def _selected_fill_and_text(svg: str) -> tuple[ET.Element, ET.Element]:
    selected = next(s for s in _segments(svg) if s.get("aria-checked") == "true")
    fill = next(e for e in selected.iter() if e.tag.endswith("}path"))
    text = next(e for e in selected.iter() if e.tag.endswith("}text"))
    return fill, text


def _with_input(resolved, **changes):
    variables = resolved.variables
    return _with(
        resolved,
        variables.model_copy(
            update={"input": variables.input.model_copy(update=changes)}
        ),
    )


def test_the_selected_segment_is_a_tint_unless_the_theme_sets_a_fill() -> None:
    resolved = _with_input(
        resolve_style(get_theme_style()), selected_background=None, selected_tint=0.3
    )

    svg = _draw({"g": _radio()}, {"g": "Region"}, resolved=resolved)

    fill, text = _selected_fill_and_text(svg)
    assert float(fill.get("fill-opacity")) == 0.3
    assert text.get("fill") == resolved.variables.input.selected_color


def test_a_theme_selected_fill_replaces_the_tint() -> None:
    resolved = _with_input(
        resolve_style(get_theme_style()),
        selected_background="#123456",
        selected_color="#fedcba",
        selected_weight=700.0,
    )

    svg = _draw({"g": _radio()}, {"g": "Region"}, resolved=resolved)

    fill, text = _selected_fill_and_text(svg)
    assert fill.get("fill") == "#123456"
    assert fill.get("fill-opacity") is None
    assert text.get("fill") == "#fedcba"
    assert text.get("font-weight") == "700"
    selected = next(s for s in _segments(svg) if s.get("aria-checked") == "true")
    assert selected.get("data-dbt-inverse") == "true"


def test_the_host_stylesheet_paints_no_fill_on_a_segment() -> None:
    from dbt_charts.core.render.controls import controls_stylesheet

    rules = [
        block
        for block in controls_stylesheet().split("}")
        if "data-dbt-segment" in block.split("{")[0]
    ]
    assert rules
    assert not any("fill" in rule.split("{")[1] for rule in rules)


def _text_x(svg: str, name: str) -> list[float]:
    group = next(e for e in _tree(svg).iter() if e.get("data-dbt-variable") == name)
    return [
        float(e.get("x"))
        for e in group.iter()
        if e.tag.endswith("}text") and e.get("text-anchor") is None
    ]


def test_a_left_aligned_buttons_value_follows_its_label_by_one_word_space() -> None:
    from dbt_charts.core.font_measure import get_font_measurer

    resolved = resolve_style(get_theme_style())
    size = float(resolved.variables.font.size)
    svg = _draw({"s": _radio()}, {"s": "Region"})

    label_x, value_x = _text_x(svg, "s")[:2]
    measure = get_font_measurer().measure
    assert value_x - label_x == pytest.approx(
        measure("S:", size) + measure(" ", size), abs=0.6
    )


def test_a_right_aligned_buttons_shows_its_value_at_the_buttons_right_edge() -> None:
    resolved = resolve_style(get_theme_style())
    with board_clock(_CLOCK), daterange_presets({}):
        controls = resolve_controls(
            {"g": _radio()}, {"g": "Region"}, _NoQueries(), resolved.variables
        )
        svg, _ = render_variables_strip_svg(
            controls, 1000.0, resolved, "end", variables_path="variables"
        )

    root = _tree(svg)
    buttons_rect = next(e for e in root.iter() if e.get("data-dbt-field") == "buttons")
    buttons_right = float(buttons_rect.get("x")) + float(buttons_rect.get("width"))
    at_rest = next(
        e for e in root.iter() if e.get("data-dbt-buttons-value") is not None
    )
    value_rect = next(e for e in at_rest.iter() if e.get("data-dbt-field") == "input")
    assert float(value_rect.get("x")) + float(value_rect.get("width")) == buttons_right


def test_a_radio_with_query_options_keeps_the_menu_form() -> None:
    """Sizing runs before the query does, so a button row's width could not be known."""
    queried = Variable(input="radio", options=VariableOptions(query="options"))
    resolved = resolve_style(get_theme_style())

    class _Rows(_NoQueries):
        def execute_query(self, name, variables):
            return [{"value": "A"}, {"value": "B"}]

    controls = resolve_controls({"g": queried}, {}, _Rows(), resolved.variables)

    assert controls[0].option_values == ("A", "B")
    assert controls[0].segments == ()


def test_a_wrapped_buttons_label_and_value_sit_on_its_first_row() -> None:
    resolved = resolve_style(get_theme_style())
    wrapped = _with(
        resolved, resolved.variables.model_copy(update={"overflow": "wrap"})
    )
    svg = _draw({"g": _radio()}, {"g": "Region"}, width=200.0, resolved=wrapped)

    rows = {
        float(e.get("y"))
        for e in _tree(svg).iter()
        if e.get("data-dbt-field") == "buttons"
    }
    assert len(rows) > 1
    first_row_top = min(rows)
    group = next(e for e in _tree(svg).iter() if e.get("data-dbt-variable") == "g")
    texts = [
        e
        for e in group.iter()
        if e.tag.endswith("}text") and e.get("text-anchor") is None
    ]
    height = float(resolved.variables.input.height)
    assert all(
        first_row_top < float(t.get("y")) < first_row_top + height for t in texts
    )
