"""One resolution of what each variable control *is*, shared by every consumer.

The widget a variable renders as isn't known until its options have been
fetched: an auto-detected select over ``2024-01-01, 2024-02-01`` is really a
datepicker. Resolving that twice — once to size the strip, once to draw it — is
how the sizer and the renderer end up disagreeing about which control is on the
board. So it resolves once, here, and the layout engine and the chrome renderer
both read the answer.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.variable.authored import (
    SingleRowBoolProbe,
    Variable,
    VariableOptions,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.variables_resolve import resolve_controls


def _vs():
    """The resolved variables style — all `resolve_controls` needs."""
    return resolve_style(get_theme_style()).variables


class _OptionExecutor:
    """Minimal ChartDataProvider stand-in that serves one option query."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[str] = []

    def execute_query(self, name: str, variables: dict[str, Any]) -> list[dict]:
        self.calls.append(name)
        return self._rows


def test_a_control_resolves_to_its_refined_widget() -> None:
    """Options that are all dates make an auto-detected select a datepicker."""
    executor = _OptionExecutor([{"d": "2024-01-01"}, {"d": "2024-02-01"}])
    var = Variable(
        input="select",
        input_auto_detected=True,
        options=VariableOptions(query="month_options"),
    )

    resolved = resolve_controls({"month": var}, {}, executor, _vs())

    assert resolved[0].input == "datepicker"


def test_an_authored_widget_is_never_refined_away() -> None:
    executor = _OptionExecutor([{"d": "2024-01-01"}, {"d": "2024-02-01"}])
    var = Variable(input="select", options=VariableOptions(query="month_options"))

    resolved = resolve_controls({"month": var}, {}, executor, _vs())

    assert resolved[0].input == "select"


def test_options_are_fetched_once_per_control() -> None:
    """The whole point: one resolution, not one per consumer.

    Layout and chrome read the same result, so a control cannot be sized as one
    widget and drawn as another.
    """
    executor = _OptionExecutor([{"r": "US"}, {"r": "EMEA"}])
    var = Variable(input="select", options=VariableOptions(query="region_options"))

    resolve_controls({"region": var}, {}, executor, _vs())

    assert executor.calls == ["region_options"]


def test_a_hidden_variable_is_not_resolved() -> None:
    """`visible: false` variables filter queries but never reach the board."""
    resolved = resolve_controls(
        {
            "shown": Variable(input="text"),
            "hidden": Variable(input="text", visible=False),
        },
        {},
        None,
        _vs(),
    )

    assert [control.name for control in resolved] == ["shown"]


def test_resolution_keeps_document_order() -> None:
    defs = {name: Variable(input="text") for name in ("c", "a", "b")}

    resolved = resolve_controls(defs, {}, None, _vs())

    assert [control.name for control in resolved] == ["c", "a", "b"]


def test_the_display_value_is_the_committed_value() -> None:
    resolved = resolve_controls(
        {"region": Variable(input="select", options=VariableOptions(static=["US"]))},
        {"region": "US"},
        None,
        _vs(),
    )

    assert resolved[0].display_value == "US"


def test_an_unset_select_displays_its_unset_label() -> None:
    resolved = resolve_controls(
        {"region": Variable(input="select", options=VariableOptions(static=["US"]))},
        {},
        None,
        _vs(),
    )

    assert resolved[0].display_value == "All"


def test_a_label_falls_back_to_the_variable_name() -> None:
    resolved = resolve_controls(
        {"parent_region_name": Variable(input="text")}, {}, None, _vs()
    )

    assert resolved[0].label == "Parent Region Name"


def test_an_authored_label_wins() -> None:
    resolved = resolve_controls(
        {"region": Variable(input="text", label="Sales Region")}, {}, None, _vs()
    )

    assert resolved[0].label == "Sales Region"


def test_refined_slider_bounds_travel_with_the_control() -> None:
    """A numeric option set becomes a slider, and its bounds come along.

    Two consumers need them — the chrome that draws the track and the HTML
    control that binds it — and neither may re-derive them.
    """
    executor = _OptionExecutor([{"n": str(n)} for n in range(10, 60, 10)])
    var = Variable(
        input="select",
        input_auto_detected=True,
        options=VariableOptions(query="limit_options"),
    )

    resolved = resolve_controls({"limit": var}, {}, executor, _vs())

    assert resolved[0].input in ("slider", "range")
    assert resolved[0].slider_min == 10
    assert resolved[0].slider_max == 50


def test_authored_slider_bounds_beat_the_theme_defaults() -> None:
    """One answer for the range, so the drawn thumb and the control agree."""
    resolved = resolve_controls(
        {"temperature": Variable(input="slider", min=1000, max=5000)}, {}, None, _vs()
    )

    assert (resolved[0].slider_min, resolved[0].slider_max) == (1000.0, 5000.0)


def test_a_query_backed_enabled_condition_is_never_evaluated_here() -> None:
    """Geometry must not ask a question that needs an executor to answer.

    Regression: resolving a control evaluated `enabled`, so the sizing pass —
    which has no executor — raised on a documented authored surface.
    """
    from dbt_charts.core.compile.models.variable.authored import SingleRowBoolProbe

    resolved = resolve_controls(
        {
            "region": Variable(
                input="text",
                enabled=SingleRowBoolProbe(query="enable_check", column="is_enabled"),
            )
        },
        {},
        None,
        _vs(),
    )

    assert resolved[0].name == "region"


def test_a_resolved_control_lays_out_directly() -> None:
    """The layout engine consumes resolved controls without a second lookup."""
    from dbt_charts.core.render.variables_layout import lay_out_variables

    resolved = resolve_controls(
        {"region": Variable(input="text", label="Region")}, {}, None, _vs()
    )

    layout = lay_out_variables(
        [control.spec for control in resolved],
        800.0,
        resolve_style(get_theme_style()).variables,
    )

    assert layout.rows == 1
    assert layout.boxes[0].name == "region"
    assert layout.boxes[0].input == "text"


def test_no_variables_resolve_to_nothing() -> None:
    assert resolve_controls({}, {}, None, _vs()) == ()


@pytest.mark.parametrize(
    "input_type",
    ["select", "multiselect", "text", "number", "slider", "checkbox", "daterange"],
)
def test_every_input_type_resolves(input_type: str) -> None:
    resolved = resolve_controls({"v": Variable(input=input_type)}, {}, None, _vs())

    assert resolved[0].input == input_type
    assert resolved[0].spec.input == input_type


def test_a_disabled_variable_resolves_as_disabled() -> None:
    """`enabled: false` must reach the drawn control.

    Regression: the deleted HTML layer was the only reader of this field, so
    after it went a variable authored `enabled: false` rendered fully operable.
    """
    resolved = resolve_controls(
        {"region": Variable(input="text", enabled=False)},
        {},
        _OptionExecutor([]),
        _vs(),
    )

    assert resolved[0].enabled is False


@pytest.mark.parametrize(
    "condition",
    [
        SingleRowBoolProbe(query="q", column="c"),
        "some_other_variable",
        "{{ pattern == 'Random' }}",
    ],
)
def test_a_conditional_enabled_is_not_evaluated_without_an_executor(
    condition: Any,
) -> None:
    """The sizing pass reaches here with no executor and must not raise.

    It has neither a way to run the probe query nor the committed values a Jinja
    condition reads — under strict Jinja, naming an unset variable is an error,
    not a false. Geometry cannot depend on `enabled` anyway (a disabled control
    occupies the same box), so with no executor the answer is "enabled" and the
    render pass, which has one, settles it for real.
    """
    var = Variable(input="text", enabled=condition)

    resolved = resolve_controls({"region": var}, {}, None, _vs())

    assert resolved[0].enabled is True


def test_a_jinja_enabled_condition_reads_the_committed_values() -> None:
    """A bare variable name gates the control on that variable's value.

    Note the coercion contract this rides on: `eval_bool_condition` renders the
    expression and coerces the result, so the gating variable has to hold
    something bool-shaped. An empty string raises rather than reading as false —
    which makes the obvious `enabled: country` spelling unusable while `country`
    is unset. Tracked separately; this pins the path that works today.
    """
    off = resolve_controls(
        {"city": Variable(input="text", enabled="has_country")},
        {"has_country": "false"},
        _OptionExecutor([]),
        _vs(),
    )
    on = resolve_controls(
        {"city": Variable(input="text", enabled="has_country")},
        {"has_country": "true"},
        _OptionExecutor([]),
        _vs(),
    )

    assert off[0].enabled is False
    assert on[0].enabled is True


@pytest.mark.parametrize(
    ("committed", "expected"),
    [
        (["US", "EMEA"], ["US", "EMEA"]),
        ("US", ["US"]),
        ("", []),
        (None, []),
    ],
)
def test_a_multiselect_current_is_narrowed_to_a_list_at_resolution(
    committed: Any, expected: list[str]
) -> None:
    """One narrowing, here, so no reader downstream has to repeat it.

    A multiselect reaches resolution as a list from a `default:`, as a bare
    scalar from a URL param written before the variable became one, or as
    nothing. `display_value` already coerced; `current` did not, so the strip
    published a Python scalar where the runtime expected JSON and read the
    board's own filter back as empty.
    """
    resolved = resolve_controls(
        {"region": Variable(input="multiselect", options=VariableOptions(static=[]))},
        {"region": committed},
        _OptionExecutor([]),
        _vs(),
    )

    assert resolved[0].current == expected
