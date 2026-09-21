"""The server draws the variable controls, so the board owns their geometry.

Every control is SVG the renderer places from the layout engine's boxes — not
text that a host later covers with differently-sized HTML. That is what makes
the band's height and the controls inside it the same answer instead of two
estimates that drift apart.
"""

from __future__ import annotations

import datetime
import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.variable.authored import Variable, VariableOptions
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.controls import controls_stylesheet, interactive_controls
from dbt_charts.core.render.variables_layout import lay_out_variables
from dbt_charts.core.render.variables_resolve import (
    UNSET_DATE_LABEL,
    format_date_label,
    resolve_controls,
)
from dbt_charts.core.render.variables_strip import (
    _committed_value,
    render_variables_strip_svg,
)

_WIDE = 1000.0


def _rs():
    return resolve_style(get_theme_style())


class _NoQueries:
    """A render-pass executor for boards whose variables need no query.

    Its presence is the signal, not its rows: `resolve_controls` only settles
    the data-dependent parts of a control — refined widget, options, `enabled` —
    when a caller has an executor to answer with.
    """

    cache_hit_ats: list = []

    def execute_query(self, name, variables):
        raise AssertionError(f"no query expected, got {name!r}")


def _strip(
    variable_defs,
    current_values=None,
    width=_WIDE,
    align="start",
    executor=None,
    variables_path="variables",
):
    controls = resolve_controls(
        variable_defs, current_values or {}, executor, _rs().variables
    )
    return render_variables_strip_svg(
        controls, width, _rs(), align, variables_path=variables_path
    )


def _drawn_text(svg: str) -> list[str]:
    """Every string the strip actually puts on the board, in order."""
    root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")
    return [e.text or "" for e in root.iter() if e.tag.endswith("text")]


def _groups(svg: str) -> list[ET.Element]:
    root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")
    return [g for g in root.iter() if g.get("data-dbt-variable")]


def test_each_control_is_its_own_group_naming_its_variable() -> None:
    svg, _ = _strip(
        {"region": Variable(input="text"), "segment": Variable(input="text")}
    )

    assert [g.get("data-dbt-variable") for g in _groups(svg)] == ["region", "segment"]


def test_a_control_carries_the_authoring_handle_for_its_variable() -> None:
    """Clicking a filter has to select the filter, not the board behind it.

    Every other authored block on a board carries this pair; the strip carried
    none, so a click on it resolved up to the board and the design panel showed
    board settings.
    """
    svg, _ = _strip({"region": Variable(input="text")})

    group = _groups(svg)[0]
    assert group.get("data-authored-path") == "variables.region"
    assert group.get("data-authored-kind") == "variable"


def test_a_band_with_no_path_carries_no_handle() -> None:
    """A board imported from another file has no coordinates in this one.

    Same rule the chart wrapper already follows: no path means the coordinates
    would name whatever happens to sit there in the importing file, so the
    block offers nothing to click rather than something that lands wrong.
    """
    svg, _ = _strip({"region": Variable(input="text")}, variables_path="")

    group = _groups(svg)[0]
    assert group.get("data-authored-path") is None
    assert group.get("data-authored-kind") is None


def test_a_nested_boards_band_is_stamped_where_that_board_sits() -> None:
    """The third answer a boolean could not give.

    An inline nested board declares its variables in the file being edited, at
    its own coordinates — so the handle is that board's path plus the key, and
    the strip composes it rather than assuming the board's.
    """
    svg, _ = _strip(
        {"region": Variable(input="text")}, variables_path="rows.0.variables"
    )

    assert _groups(svg)[0].get("data-authored-path") == "rows.0.variables.region"


def test_a_group_names_the_widget_it_was_resolved_to() -> None:
    """The refined widget, not the compiled one — a host binds against this."""
    svg, _ = _strip({"since": Variable(input="date")})

    assert _groups(svg)[0].get("data-dbt-input") == "date"


def test_a_group_carries_the_box_the_layout_engine_gave_it() -> None:
    """Geometry is published, not inferred: a host reads it instead of measuring."""
    defs = {"region": Variable(input="text", label="Region")}
    svg, _ = _strip(defs)
    layout = lay_out_variables(
        [c.spec for c in resolve_controls(defs, {}, None, _rs().variables)],
        _WIDE,
        get_theme_style().variables,
    )

    group = _groups(svg)[0]
    box = layout.boxes[0]
    # Published at the SVG's own coordinate precision — sub-pixel rounding is
    # the only difference allowed between the reserved box and the drawn one.
    assert float(group.get("data-dbt-x")) == pytest.approx(box.x, abs=0.5)
    assert float(group.get("data-dbt-y")) == pytest.approx(box.y, abs=0.5)
    assert float(group.get("data-dbt-width")) == pytest.approx(box.width, abs=0.5)
    assert float(group.get("data-dbt-height")) == pytest.approx(box.height, abs=0.5)


def test_the_band_is_exactly_as_tall_as_the_layout() -> None:
    """No second opinion about the band's height, and none to disagree with."""
    defs = {f"v{i}": Variable(input="text", label=f"Var {i}") for i in range(8)}
    _, height = _strip(defs, width=400.0)
    layout = lay_out_variables(
        [c.spec for c in resolve_controls(defs, {}, None, _rs().variables)],
        400.0,
        get_theme_style().variables,
    )

    assert layout.rows > 1
    assert height == pytest.approx(layout.height)


def test_every_control_draws_its_label_and_value() -> None:
    svg, _ = _strip(
        {"region": Variable(input="select", label="Region")}, {"region": "EMEA"}
    )

    assert "Region:" in svg
    assert "EMEA" in svg


def test_an_unset_select_draws_its_unset_label() -> None:
    svg, _ = _strip({"region": Variable(input="select", label="Region")})

    assert "All" in svg


def test_a_value_is_escaped_into_the_svg() -> None:
    svg, _ = _strip({"q": Variable(input="text")}, {"q": "<script>x</script>"})

    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_a_dropdown_draws_an_arrow_and_a_plain_field_does_not() -> None:
    dropdown, _ = _strip({"region": Variable(input="select", label="Region")})
    field, _ = _strip({"region": Variable(input="text", label="Region")})

    assert 'data-dbt-ornament="arrow"' in dropdown
    assert 'data-dbt-ornament="arrow"' not in field
    # Whether it is *visible* at rest is a separate contract, pinned below.


def test_type_ornaments_are_hidden_until_a_host_turns_them_on() -> None:
    """A chevron promises an interaction a downloaded PNG cannot perform.

    Same contract as the field borders beside them: drawn, so the runtime can
    reveal them from the stylesheet, but invisible at rest — on a borderless
    field a lone glyph reads as a stray mark next to the value.
    """
    dropdown, _ = _strip({"region": Variable(input="select", label="Region")})
    picker, _ = _strip({"since": Variable(input="date", label="Since")})

    for svg, ornament in ((dropdown, "arrow"), (picker, "calendar")):
        root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")
        glyph = next(e for e in root.iter() if e.get("data-dbt-ornament") == ornament)
        assert glyph.get("opacity") == "0"


def test_the_stylesheet_reveals_the_ornaments_on_bind() -> None:
    """The reveal rule has to exist, or the glyphs are dead weight in every host.

    In the host's stylesheet: the reveal only happens under the runtime, and a
    board is a picture that ships no interaction of its own.
    """
    css = controls_stylesheet()

    assert '.dbt-interactive [data-dbt-ornament="arrow"]' in css
    assert '.dbt-interactive [data-dbt-ornament="calendar"]' in css


def test_a_checkbox_draws_a_box_not_a_text_field() -> None:
    svg, _ = _strip({"active": Variable(input="checkbox", label="Active")})

    assert 'data-dbt-ornament="checkbox"' in svg


def test_an_unticked_checkbox_is_visible_in_a_static_export() -> None:
    """The one control whose box is its value, so the box cannot be withheld.

    Its fill is transparent on every theme but neon. Without a stroke it draws
    nothing at all, and an export gives the reader no way to tell an unticked
    box from a variable that was never rendered.
    """
    svg, _ = _strip({"active": Variable(input="checkbox", label="Active")}, {})
    root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")

    box = next(e for e in root.iter() if e.get("data-dbt-ornament") == "checkbox")
    assert box.get("stroke") not in (None, "none")


def test_the_checkbox_outline_does_not_change_color_when_a_host_binds() -> None:
    """It is the one field drawn stroked, so it is the one that can disagree.

    The interactivity rule paints every field from --dbt-variable-field-border.
    A checkbox stroked from a different token would be one color in a downloaded
    PNG and another the instant the runtime added the class.
    """
    svg, _ = _strip({"active": Variable(input="checkbox", label="Active")})
    root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")

    box = next(e for e in root.iter() if e.get("data-dbt-ornament") == "checkbox")
    assert f"--dbt-variable-field-border: {box.get('stroke')}" in svg


def test_a_slider_draws_a_track_and_a_thumb() -> None:
    svg, _ = _strip({"limit": Variable(input="slider", min=0, max=10)}, {"limit": 5})

    assert 'data-dbt-ornament="slider"' in svg


def test_input_borders_are_off_until_a_host_turns_them_on() -> None:
    """At rest the strip states values; it does not pretend to be a form.

    Suppressed with a presentation attribute rather than omitted, so the runtime
    that binds these controls can turn borders on from a stylesheet — stroke
    takes no part in SVG layout, so that cannot move anything.
    """
    svg, _ = _strip({"region": Variable(input="select", label="Region")})
    root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")

    fields = [e for e in root.iter() if e.get("data-dbt-field") is not None]
    assert fields
    assert all(field.get("stroke") == "none" for field in fields)


def test_the_anchor_box_still_covers_the_band() -> None:
    """Hosts mount against this rect; it must span what the chrome occupies."""
    svg, height = _strip(
        {f"v{i}": Variable(input="text") for i in range(6)}, width=400.0
    )
    root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")

    anchor = next(e for e in root.iter() if e.get("data-dbt-variables-box"))
    assert float(anchor.get("height")) == pytest.approx(height)
    assert float(anchor.get("width")) == pytest.approx(400.0)


def test_end_alignment_packs_the_controls_against_the_right_edge() -> None:
    """The title-inline band puts its controls opposite the title."""
    defs = {"region": Variable(input="text", label="Region")}
    start, _ = _strip(defs, align="start")
    end, _ = _strip(defs, align="end")

    assert float(_groups(start)[0].get("data-dbt-x")) == pytest.approx(0.0)
    assert float(_groups(end)[0].get("data-dbt-x")) > 0.0


def test_end_alignment_keeps_every_control_on_the_board() -> None:
    defs = {f"v{i}": Variable(input="text", label=f"Var {i}") for i in range(3)}
    svg, _ = _strip(defs, width=600.0, align="end")

    for group in _groups(svg):
        x = float(group.get("data-dbt-x"))
        assert x >= 0.0
        assert x + float(group.get("data-dbt-width")) <= 600.0 + 0.5


def test_end_alignment_never_shifts_a_row_left_of_its_column() -> None:
    """An overrun runs off the far edge, never back across the near one.

    A control wider than the column it packs into makes ``width - end``
    negative, and translating a row by it walks the control out of the
    variables column and into whatever sits before it — the board title, on the
    band this alignment exists for. The row can overflow; it cannot reverse.
    """
    defs = {
        "categories": Variable(
            input="multiselect",
            label="Categories",
            options=VariableOptions(
                static=["Analytics", "Collaboration", "Security", "Support"]
            ),
        )
    }
    values = {"categories": ["Analytics", "Collaboration", "Security", "Support"]}
    svg, _ = _strip(defs, current_values=values, width=120.0, align="end")

    group = _groups(svg)[0]
    assert float(group.get("data-dbt-width")) > 120.0, "control must overrun to test"
    assert float(group.get("data-dbt-x")) == pytest.approx(0.0)


def test_no_variables_draws_nothing() -> None:
    svg, height = _strip({})

    assert svg == ""
    assert height == 0.0


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
def test_every_input_type_draws_a_group(input_type: str) -> None:
    svg, height = _strip({"v": Variable(input=input_type, label="Var")})

    assert len(_groups(svg)) == 1
    assert height > 0


def test_a_query_backed_select_draws_the_widget_its_data_resolved_to() -> None:
    """End to end: options decide the widget, and the chrome draws that one."""

    class _Executor:
        def execute_query(self, name, variables):
            return [{"d": "2024-01-01"}, {"d": "2024-02-01"}]

    defs = {
        "month": Variable(
            input="select",
            input_auto_detected=True,
            options=VariableOptions(query="month_options"),
        )
    }
    controls = resolve_controls(defs, {}, _Executor(), _rs().variables)
    svg, _ = render_variables_strip_svg(
        controls, _WIDE, _rs(), variables_path="variables"
    )

    assert _groups(svg)[0].get("data-dbt-input") == "datepicker"


class TestGeometryNeverEvaluatesBehavior:
    """Laying a control out must not ask a question only the render can answer.

    The sizing pass runs with no executor and with defaults rather than
    committed values. Anything it evaluates there it evaluates wrongly or not at
    all — so it must evaluate nothing beyond the control's own geometry.
    """

    def test_a_query_backed_enabled_condition_does_not_break_the_board(self) -> None:
        """Regression: `enabled: {query:, column:}` is a documented surface.

        Sizing reached it through the title-inline band decision — which every
        titled board with a variable runs — and raised for want of an executor.
        """
        from dbt_charts.core.compile import compile
        from dbt_charts.core.compile.models.variable.authored import SingleRowBoolProbe
        from dbt_charts.core.render.board_resolve import (
            build_resolved_board_static as resolve_board,
        )
        from dbt_charts.core.render.boards import render_board_svg

        from .._board_utils import apply_static_layout

        result = compile(
            """
title: Pipeline
variables:
  region:
    input: select
    label: Region
    options:
      static: [North, South]
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
        )
        assert result.success, result.errors
        board = result.board
        board.variable_registry["region"].enabled = SingleRowBoolProbe(
            query="enable_check", column="is_enabled"
        )
        board.variables["region"].enabled = SingleRowBoolProbe(
            query="enable_check", column="is_enabled"
        )
        apply_static_layout(board)

        class _Executor:
            cache_hit_ats: list = []

            def execute_query(self, name, variables):
                if name == "enable_check":
                    return [{"is_enabled": True}]
                return [{"month": "Jan", "revenue": 100}]

        svg = render_board_svg(
            resolve_board(board),
            _Executor(),
            board.variable_defaults,
            background=None,
            render_cache={},
        )

        assert "data-dbt-variables-box" in svg

    def test_a_jinja_enabled_condition_on_a_default_less_variable_is_not_evaluated(
        self,
    ) -> None:
        """Sizing sees only defaults, so a strict-Jinja `enabled` would blow up."""
        from dbt_charts.core.render.sizing import compute_variable_controls_height

        variable_defs = {
            "pattern": Variable(input="text"),
            "cells": Variable(input="text", enabled="{{ pattern == 'Random' }}"),
        }

        height = compute_variable_controls_height(
            variable_defs, 800.0, {}, _rs().variables
        )

        assert height > 0


class TestDrawnStateMatchesTheResolvedControl:
    def test_a_slider_thumb_honors_the_authored_range(self) -> None:
        """Regression: the thumb read theme defaults (0–100) and pinned right.

        An authored 1000–5000 slider at 2000 sits a quarter along, and the HTML
        control mounted over the same box agrees because both read one field.
        """
        svg, _ = _strip(
            {"temperature": Variable(input="slider", min=1000, max=5000)},
            {"temperature": 2000},
        )
        root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")

        track = next(e for e in root.iter() if e.get("data-dbt-ornament") == "slider")
        thumb = next(e for e in root.iter() if e.tag.endswith("circle"))
        fraction = (float(thumb.get("cx")) - float(track.get("x"))) / float(
            track.get("width")
        )
        assert fraction == pytest.approx(0.25, abs=0.02)

    def test_a_refined_checkbox_draws_the_state_the_control_binds(self) -> None:
        """A 0/1 select refines to a checkbox — and `1` means ticked.

        Regression: the tick was read out of a display string produced for the
        widget the variable *stopped* being, so the picture and the live control
        stated opposite things.
        """

        class _Executor:
            def execute_query(self, name, variables):
                return [{"v": "0"}, {"v": "1"}]

        defs = {
            "active": Variable(
                input="select",
                input_auto_detected=True,
                options=VariableOptions(query="flag_options"),
            )
        }
        controls = resolve_controls(defs, {"active": "1"}, _Executor(), _rs().variables)
        svg, _ = render_variables_strip_svg(
            controls, _WIDE, _rs(), variables_path="variables"
        )

        assert controls[0].input == "checkbox"
        assert controls[0].checked is True
        root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")
        assert any(e.tag.endswith("path") for e in root.iter()), "tick not drawn"

    def test_an_unset_field_keeps_its_ornament_inside_itself(self) -> None:
        """A date with nothing committed still has a field to put its glyph in."""
        svg, _ = _strip({"since": Variable(input="date", label="Since")})
        root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")

        field = next(e for e in root.iter() if e.get("data-dbt-field") == "input")
        calendar = next(
            e for e in root.iter() if e.get("data-dbt-ornament") == "calendar"
        )
        glyph = next(iter(calendar))
        left, right = (
            float(field.get("x")),
            float(field.get("x")) + float(field.get("width")),
        )
        assert left <= float(glyph.get("x"))
        assert float(glyph.get("x")) + float(glyph.get("width")) <= right + 0.5


def _ornament_left(root: ET.Element) -> float:
    """Left edge of whatever glyph the strip drew, read off the drawn geometry.

    The two ornaments publish it differently -- the chevron is a bare ``path``
    whose ``d`` opens on its leftmost point, the calendar is a ``g`` whose first
    child rect carries ``x``. Reading both here rather than recomputing either
    is the point: a test that re-derives the position from the layout cannot
    catch the chrome being re-anchored.
    """
    for element in root.iter():
        kind = element.get("data-dbt-ornament")
        if kind == "arrow":
            return float(element.get("d").split()[0][1:])
        if kind == "calendar":
            return float(next(iter(element)).get("x"))
    raise AssertionError("no ornament drawn")


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
def test_the_drawn_value_never_reaches_its_drawn_ornament(
    input_type: str, value: str
) -> None:
    """Every content-sized ornamented field draws breathing room before its glyph.

    The layout used to reserve ``text + padding + ornament`` exactly, which puts
    the glyph's left edge on the text's right edge -- a zero gap at every
    viewport and every value length, for all five cases here. Zero is not a
    rounding error to absorb: the width is measured in Python and drawn by
    Chromium, so any variance between the two measurers renders as the chevron
    sitting on the final letters.

    Read off the emitted SVG rather than recomputed from ``lay_out_variables``,
    so that re-anchoring ``_draw_arrow`` from the field's *left* edge -- which
    would bring the collision straight back -- reddens this.

    ``daterange`` is deliberately absent: it is theme-sized with ~107 units of
    slack at any value that fits, so it never collided and asserting on it here
    would pass on unfixed code.
    """
    from dbt_charts.core.font_measure import get_font_measurer

    committed = None if value in ("All", "Any date") else {"v": value}
    svg, _ = _strip({"v": Variable(input=input_type, label="When")}, committed)
    root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")

    # A committed date draws its human label (format_date_label), not the raw
    # ISO string committed above — the unset case ("Any date") is unaffected.
    drawn_value = (
        format_date_label(value) if input_type == "date" and committed else value
    )

    text = next(e for e in root.iter() if (e.text or "") == drawn_value)
    font_size = float(text.get("font-size"))
    text_right = float(text.get("x")) + get_font_measurer().measure(
        drawn_value, font_size
    )

    # Bare non-overlap is too weak to be a regression pin: with the gap deleted
    # the glyph lands 0.08 units clear of the text here, which passes `>` while
    # rendering as a collision. Demand room proportional to the type instead --
    # a fraction of what the layout reserves, so this survives a retune of the
    # gap without pinning its exact size.
    assert _ornament_left(root) - text_right >= font_size * 0.2, (
        f"{input_type} {value!r}: the glyph is drawn on top of the value"
    )


def test_a_long_value_stays_inside_the_box_the_layout_published() -> None:
    """The reserved width is the drawn width, for every input kind.

    Regression: theme-sized fields (`text`, `number`, `daterange`) drew their
    value at full length inside a fixed-width box, so a long committed value
    overran the field and the control beside it.
    """
    value = "customer_lifetime_value_by_cohort_2026"
    svg, _ = _strip(
        {"note": Variable(input="text", label="Note"), "b": Variable(input="text")},
        {"note": value},
    )
    root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")

    group = _groups(svg)[0]
    right = float(group.get("data-dbt-x")) + float(group.get("data-dbt-width"))
    text = next(e for e in root.iter() if (e.text or "") == value)
    from dbt_charts.core.font_measure import get_font_measurer

    drawn_right = float(text.get("x")) + get_font_measurer().measure(
        value, float(text.get("font-size"))
    )
    assert drawn_right <= right + 0.5


def test_a_checkbox_publishes_whether_it_is_ticked() -> None:
    """State the runtime binds against is published, not inferred from a glyph.

    Toggling needs the current value, and reading it back off the drawn tick
    would make the runtime parse its own chrome.
    """
    on, _ = _strip({"active": Variable(input="checkbox")}, {"active": True})
    off, _ = _strip({"active": Variable(input="checkbox")}, {"active": False})

    assert _groups(on)[0].get("data-dbt-checked") == "true"
    assert _groups(off)[0].get("data-dbt-checked") == "false"


def test_only_a_checkbox_publishes_a_ticked_state() -> None:
    """A text field has no ticked state; publishing one would be a lie."""
    svg, _ = _strip({"note": Variable(input="text")})

    assert _groups(svg)[0].get("data-dbt-checked") is None


def test_a_dropdown_publishes_the_options_it_resolved_to() -> None:
    """The options travel with the control, so a host needs no second request.

    The drawn chrome shows only the committed value; without this the popover
    would have nothing to open. Baking them keeps a live board and a chat
    artifact self-contained, and survives a host that sanitizes to `data-*`.
    """
    with interactive_controls(True):
        svg, _ = _strip(
            {
                "region": Variable(
                    input="select", options=VariableOptions(static=["US", "EMEA"])
                )
            }
        )

    assert json.loads(_groups(svg)[0].get("data-dbt-options")) == ["US", "EMEA"]


def test_options_are_published_as_json_not_a_delimited_string() -> None:
    """A value containing the delimiter is why this is JSON."""
    with interactive_controls(True):
        svg, _ = _strip(
            {
                "region": Variable(
                    input="select", options=VariableOptions(static=["A, B", "C"])
                )
            }
        )

    assert json.loads(_groups(svg)[0].get("data-dbt-options")) == ["A, B", "C"]


def test_an_artifact_render_does_not_carry_the_option_list() -> None:
    """The full domain of a filter is data the board fetched and did not draw.

    A live host needs it to open the popover. A static export has no runtime to
    open one, so all the attribute does there is put every value of the column —
    every customer, every email — into a file that shows ten of them. Only the
    payload is gated; the drawing is identical either way, because a picture
    must not depend on who is looking at it.
    """
    variables = {
        "region": Variable(input="select", options=VariableOptions(static=["US", "EU"]))
    }

    artifact, _ = _strip(variables)
    with interactive_controls(True):
        live, _ = _strip(variables)

    assert _groups(artifact)[0].get("data-dbt-options") is None
    assert _groups(live)[0].get("data-dbt-options") is not None
    assert _drawn_text(artifact) == _drawn_text(live)


def test_a_control_with_no_options_publishes_none() -> None:
    """A text field has no option list; an empty attribute would imply one."""
    with interactive_controls(True):
        svg, _ = _strip({"note": Variable(input="text")})

    assert _groups(svg)[0].get("data-dbt-options") is None


def test_a_control_publishes_the_committed_value_not_its_display_form() -> None:
    """`All` and `Yes` are labels; a host committing them would filter on a word."""
    unset, _ = _strip({"region": Variable(input="select")})
    ticked, _ = _strip({"on": Variable(input="checkbox")}, {"on": True})

    assert "All" in unset
    assert _groups(unset)[0].get("data-dbt-value") == ""
    assert _groups(ticked)[0].get("data-dbt-value") == "True"


def test_a_list_valued_control_publishes_json_not_a_python_repr() -> None:
    """Regression: `str(['a','b'])` is `['a', 'b']` — a host would commit that."""
    svg, _ = _strip(
        {"span": Variable(input="daterange")}, {"span": ["2026-01-01", "2026-02-01"]}
    )

    assert json.loads(_groups(svg)[0].get("data-dbt-value")) == [
        "2026-01-01",
        "2026-02-01",
    ]
    assert "['" not in svg


def test_a_list_of_dates_publishes_iso_string_json() -> None:
    control = SimpleNamespace(
        current=[datetime.date(2026, 1, 1), datetime.date(2026, 2, 1)]
    )

    assert json.loads(_committed_value(control)) == ["2026-01-01", "2026-02-01"]


def test_a_board_with_a_bare_date_daterange_default_renders() -> None:
    from dbt_charts.core.compile import compile
    from dbt_charts.core.render.board_resolve import (
        build_resolved_board_static as resolve_board,
    )
    from dbt_charts.core.render.boards import render_board_svg

    from .._board_utils import apply_static_layout

    result = compile(
        """
variables:
  date_range:
    input: daterange
    default:
      - 2026-01-01
      - 2026-02-01
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
    )
    assert result.success, result.errors
    board = apply_static_layout(result.board)

    svg = render_board_svg(
        resolve_board(board),
        _NoQueries(),
        board.variable_defaults,
        background=None,
        render_cache={},
    )

    assert json.loads(_groups(svg)[0].get("data-dbt-value")) == [
        "2026-01-01",
        "2026-02-01",
    ]


@pytest.mark.parametrize(
    ("committed", "expected"),
    [
        ("US", ["US"]),
        (["US", "EMEA"], ["US", "EMEA"]),
        ("", []),
        (None, []),
    ],
)
def test_a_multiselect_publishes_json_whatever_shape_its_value_arrived_in(
    committed, expected
) -> None:
    """A multiselect reaches here as a list, a bare scalar, or nothing.

    Regression: only the list branch was JSON-encoded, so `?region=US` published
    `data-dbt-value="US"`. The runtime JSON-parses this attribute for a
    multiselect and falls back to `[]` on a parse error, so the board's own
    committed filter came back empty the next time anything read the variable
    snapshot — a tab link or a commit on a neighboring control silently cleared
    it. `display_value` narrowed the same value and the published one did not,
    which is the two-readings drift `coerce_multiselect` exists to prevent.
    """
    svg, _ = _strip(
        {
            "region": Variable(
                input="multiselect", options=VariableOptions(static=["US", "EMEA"])
            )
        },
        {"region": committed},
    )

    assert json.loads(_groups(svg)[0].get("data-dbt-value")) == expected


def test_a_slider_publishes_the_bounds_it_was_drawn_against() -> None:
    """The runtime maps a pointer to a value; the drawn thumb maps it back.

    Both must use the same bounds — reading theme defaults on either side puts
    the thumb and the committed value in different places for any authored range.
    """
    svg, _ = _strip(
        {"limit": Variable(input="slider", min=10, max=50, step=5)}, {"limit": 25}
    )

    group = _groups(svg)[0]
    assert float(group.get("data-dbt-min")) == 10
    assert float(group.get("data-dbt-max")) == 50
    assert float(group.get("data-dbt-step")) == 5


def test_only_a_slider_publishes_bounds() -> None:
    svg, _ = _strip({"note": Variable(input="text")})

    assert _groups(svg)[0].get("data-dbt-min") is None


def test_fractional_slider_bounds_survive_publication() -> None:
    """Regression: the bounds went out through `px`, an integer snapper.

    `step: 0.1` published as `0`, which the runtime reads as "no step", while
    the drawn thumb still used the real float — the exact disagreement these
    attributes exist to prevent.
    """
    svg, _ = _strip(
        {"r": Variable(input="slider", min=0.2, max=0.8, step=0.05)}, {"r": 0.5}
    )

    group = _groups(svg)[0]
    assert float(group.get("data-dbt-min")) == pytest.approx(0.2)
    assert float(group.get("data-dbt-max")) == pytest.approx(0.8)
    assert float(group.get("data-dbt-step")) == pytest.approx(0.05)


def test_a_control_that_can_be_unset_says_so_and_names_the_state() -> None:
    """Otherwise a user can filter and never unfilter.

    The popover's rows come from the published options; without this the
    delete-the-param branch of a commit is unreachable from the UI.
    """
    svg, _ = _strip(
        {"region": Variable(input="select", options=VariableOptions(static=["US"]))}
    )

    group = _groups(svg)[0]
    assert group.get("data-dbt-can-unset") == "true"
    assert group.get("data-dbt-unset-label") == "All"


def test_a_required_control_says_it_cannot_be_unset() -> None:
    """Emptying it renders the next page with no board — and no way back."""
    svg, _ = _strip(
        {
            "region": Variable(
                input="multiselect",
                required=True,
                options=VariableOptions(static=["US"]),
            )
        }
    )

    assert _groups(svg)[0].get("data-dbt-can-unset") == "false"


def test_a_can_unset_daterange_publishes_unset_attributes() -> None:
    """A no-default, non-required daterange must offer a path back to unset.

    Without these attributes the popover cannot offer Clear, and a user can
    pick a date range and never return to the unset state.
    """
    svg, _ = _strip({"span": Variable(input="daterange")})
    group = _groups(svg)[0]
    assert group.get("data-dbt-can-unset") == "true"
    assert group.get("data-dbt-unset-label") == "All dates"


def test_a_required_daterange_says_it_cannot_be_unset() -> None:
    """A required daterange has no legal empty state.

    The popover reads this attribute to decide whether to render Clear.
    Publishing `false` (rather than omitting the attribute) matches the
    required-multiselect contract: no silent omission.
    """
    svg, _ = _strip(
        {
            "span": Variable(
                input="daterange",
                required=True,
                default=["2026-01-01", "2026-01-31"],
            )
        }
    )
    group = _groups(svg)[0]
    assert group.get("data-dbt-can-unset") == "false"
    assert group.get("data-dbt-unset-label") is None


def test_a_disabled_control_says_so_and_is_dimmed() -> None:
    """`enabled: false` has to reach the drawing, or it means nothing.

    Regression: the deleted HTML layer was this field's only reader, so a
    disabled variable rendered as a fully operable control.
    """
    off, _ = _strip(
        {"region": Variable(input="select", enabled=False)}, executor=_NoQueries()
    )
    on, _ = _strip({"region": Variable(input="select")}, executor=_NoQueries())

    assert _groups(off)[0].get("data-dbt-enabled") == "false"
    assert _groups(on)[0].get("data-dbt-enabled") is None
    assert float(_groups(off)[0].get("opacity")) < 1.0


def test_an_unset_date_still_states_itself_in_a_static_export() -> None:
    """Otherwise the control is a label and blank space in a downloaded PNG.

    `select` and `daterange` both caption their empty state; `date` did not, and
    leaned on the calendar glyph to mark where the field was. Once the glyph
    became an affordance the bound board reveals, that left nothing behind.
    """
    svg, _ = _strip({"since": Variable(input="date", label="On or after")})

    drawn = [t for t in _drawn_text(svg) if t.strip()]
    assert drawn != ["On or after:"], "the field states nothing at all"


def test_a_label_sits_on_the_baseline_its_own_face_centers_it_on() -> None:
    """The label is centered in its box, and the strip asks the face where that is.

    Wiring rather than the ratio itself: the strip snaps ``y`` to whole pixels,
    so at an 11px label the face's answer and the ``font_size * 0.35`` it
    replaced round to the same line. ``centered_baseline_offset``'s own tests
    pin the ratio; this pins that the strip is what asks for it, and fails if
    the label goes back to being centered on some other rule.
    """
    from dbt_charts.core.font_measure import centered_baseline_offset

    defs = {"region": Variable(input="text", label="Region")}
    svg, _ = _strip(defs)
    layout = lay_out_variables(
        [c.spec for c in resolve_controls(defs, {}, None, _rs().variables)],
        _WIDE,
        get_theme_style().variables,
    )

    label_font = _rs().variables.label.font
    box = layout.boxes[0]
    root = ET.fromstring(f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>")
    label = next(
        e
        for e in root.iter()
        if e.tag.endswith("text") and (e.text or "").startswith("Region")
    )

    expected = (
        box.y
        + box.height / 2
        + centered_baseline_offset(label_font.family, float(label_font.size))
    )
    assert float(label.get("y")) == pytest.approx(expected, abs=0.5)


@pytest.mark.parametrize(
    ("input_type", "label"),
    [("select", "All"), ("multiselect", "All"), ("radio", "All")],
)
def test_every_chooser_publishes_the_chooser_unset_label(
    input_type: str, label: str
) -> None:
    """`_INPUT_TRAITS`' third column, pinned by what the chrome draws.

    The table gates *membership* — a new `VariableInputType` cannot render until
    it has a row — but a row whose `unset` said `none` would still satisfy that,
    and the control would silently lose its path back to unfiltered. That is the
    shape of the bug the traits table was written for, so the column that decides
    it gets a behavioral test like the other two.
    """
    svg, _ = _strip(
        {"region": Variable(input=input_type, options=VariableOptions(static=["US"]))}
    )

    group = _groups(svg)[0]
    assert group.get("data-dbt-can-unset") == "true"
    assert group.get("data-dbt-unset-label") == label


@pytest.mark.parametrize("input_type", ["date", "datepicker"])
def test_a_non_required_date_publishes_the_date_unset_label(input_type: str) -> None:
    """`date`/`datepicker` offer a way back to unset too, same as a chooser.

    Regression: `_INPUT_TRAITS` classified both as `unset="none"`, so a
    non-required date/datepicker never published `data-dbt-can-unset` — the
    calendar popover's Clear footer never rendered, and it was the only way
    left to unset the control once the native lifted input (whose own
    empty-and-blur path did the same job) was replaced by that popover.
    """
    svg, _ = _strip({"since": Variable(input=input_type)})

    group = _groups(svg)[0]
    assert group.get("data-dbt-can-unset") == "true"
    assert group.get("data-dbt-unset-label") == UNSET_DATE_LABEL


@pytest.mark.parametrize("input_type", ["text", "number", "checkbox", "slider"])
def test_a_control_with_no_unset_affordance_publishes_none(input_type: str) -> None:
    """The mirror. A text box has no popover to offer Clear from, so publishing
    the attribute would promise an affordance the chrome never draws."""
    svg, _ = _strip({"region": Variable(input=input_type)})

    assert _groups(svg)[0].get("data-dbt-can-unset") is None


def test_a_defaulted_multiselect_can_still_be_unset() -> None:
    """A default is something to fall back to, not a reason to forbid empty.

    Regression: `can_unset` was `default is None and not required`, so every
    control carrying a default published `can-unset="false"`. The popover reads
    that to decide whether to offer Clear and whether to let the last member be
    unchecked, so a defaulted multiselect could be narrowed but never emptied —
    the user could uncheck every option except one and then get stuck.
    `required` alone decides this: an empty list reads as absent
    (`variable_value_is_absent`), so a required variable has no legal empty
    state whether it carries a default or not, and a non-required one always
    does.
    """
    svg, _ = _strip(
        {
            "ticket_types": Variable(
                input="multiselect",
                default=["problem", "incident"],
                options=VariableOptions(
                    static=["task", "problem", "question", "incident"]
                ),
            )
        }
    )

    group = _groups(svg)[0]
    assert group.get("data-dbt-can-unset") == "true"
    assert group.get("data-dbt-unset-label") == "All"


def test_a_required_multiselect_with_a_default_still_cannot_be_unset() -> None:
    """A default does not rescue a required variable from an empty selection.

    `variable_value_is_absent` counts `[]` as absent, so clearing writes an
    empty list the required check rejects — the next render raises
    MissingRequiredVariablesError with no board left to recover from. The
    default is never consulted, because the empty list *is* a committed value.
    """
    svg, _ = _strip(
        {
            "ticket_types": Variable(
                input="multiselect",
                required=True,
                default=["problem"],
                options=VariableOptions(static=["task", "problem"]),
            )
        }
    )

    assert _groups(svg)[0].get("data-dbt-can-unset") == "false"


def test_a_number_input_publishes_its_authored_bounds() -> None:
    """The lifted native input enforces min/max/step only if the chrome tells it
    what they are; a number with none authored constrains nothing (the theme's
    slider defaults are a slider's, not a number's)."""
    svg, _ = _strip({"age": Variable(input="number", min=0, max=100, step=5)})
    (group,) = _groups(svg)

    assert group.get("data-dbt-min") == "0"
    assert group.get("data-dbt-max") == "100"
    assert group.get("data-dbt-step") == "5"

    svg, _ = _strip({"age": Variable(input="number")})
    (group,) = _groups(svg)
    assert group.get("data-dbt-min") is None
    assert group.get("data-dbt-step") is None


@pytest.mark.parametrize("input_type", ["number", "slider"])
def test_published_bounds_are_exact(input_type: str) -> None:
    """The runtime clamps to what is published, so a bound must survive the
    trip byte-for-byte: no six-significant-digit rounding, no float noise."""
    svg, _ = _strip(
        {"n": Variable(input=input_type, min=1234567, max=99999999, step=0.1)}
    )
    (group,) = _groups(svg)

    assert group.get("data-dbt-min") == "1234567"
    assert group.get("data-dbt-max") == "99999999"
    assert group.get("data-dbt-step") == "0.1"


@pytest.mark.parametrize("input_type", ["text", "number"])
def test_an_empty_field_draws_its_placeholder_as_a_hint_not_a_value(
    input_type: str,
) -> None:
    """A placeholder reads as a hint: muted, and published for the lifted
    native input to show, never drawn in the value color where it passes
    for a committed value."""
    svg, _ = _strip({"q": Variable(input=input_type, placeholder="Type here")})
    (group,) = _groups(svg)
    hint = [e for e in group.iter() if e.tag.endswith("text") and e.text == "Type here"]

    assert len(hint) == 1
    assert hint[0].get("fill") == _rs().variables.placeholder.font.color
    assert hint[0].get("fill") != _rs().variables.value.font.color
    assert group.get("data-dbt-placeholder") == "Type here"
    assert group.get("data-dbt-value") == ""

    svg, _ = _strip(
        {"q": Variable(input=input_type, placeholder="Type here")},
        {"q": "42" if input_type == "number" else "hello"},
    )
    (group,) = _groups(svg)
    value = [e for e in group.iter() if e.tag.endswith("text")][-1]
    assert value.get("fill") == _rs().variables.value.font.color
    assert group.get("data-dbt-placeholder") == "Type here"
