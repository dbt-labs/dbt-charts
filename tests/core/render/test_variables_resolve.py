"""One resolution of what each variable control *is*, shared by every consumer.

The widget a variable renders as isn't known until its options have been
fetched: an auto-detected select over ``2024-01-01, 2024-02-01`` is really a
datepicker. Resolving that twice — once to size the strip, once to draw it — is
how the sizer and the renderer end up disagreeing about which control is on the
board. So it resolves once, here, and the layout engine and the chrome renderer
both read the answer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.variable.authored import (
    SingleRowBoolProbe,
    Variable,
    VariableOptions,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render
from dbt_charts.core.render.variables_resolve import (
    format_variable_display_value,
    linked_variable_options,
    resolve_controls,
)

from ..._run import Run


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

    assert resolved[0].label == "Parent region name"


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
    executor = _OptionExecutor([])
    var = Variable(
        input="select",
        input_auto_detected=True,
        options=VariableOptions(static=[10, 20, 30, 40, 50]),
    )

    resolved = resolve_controls({"limit": var}, {}, executor, _vs())

    assert resolved[0].input in ("slider", "range")
    assert resolved[0].slider_min == 10
    assert resolved[0].slider_max == 50


def test_a_numeric_column_backed_variable_stays_an_unsettable_select() -> None:
    executor = _OptionExecutor([{"n": str(n)} for n in range(1970, 1976)])
    var = Variable(
        input="select",
        input_auto_detected=True,
        options=VariableOptions(query="limit_options"),
    )

    resolved = resolve_controls({"limit": var}, {}, executor, _vs())

    assert resolved[0].input == "select"
    assert resolved[0].can_unset


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


_LINKED_BOARD = """
variables:
  metric:
    input: select
    options:
      static: [strict, loose]
  regions:
    input: multiselect
    options:
      static: [east, west]
  note:
    input: select
    options:
      static: [a, b]
queries:
  rates:
    columns: [arm, metric, region, rate]
    values:
      - [ac, strict, east, 0.3]
charts:
  by_metric:
    query: rates
    type: bar
    x: arm
    y: rate
    color: metric
    link: "?metric={{ color }}"
  by_region:
    query: rates
    type: bar
    x: arm
    y: rate
    color: region
    link: "?regions={{ color }}"
"""


def test_a_variable_a_chart_link_sets_publishes_its_options() -> None:
    board = compile(_LINKED_BOARD).board
    assert board is not None
    options = linked_variable_options(board, None, {})
    assert options == {
        "metric": {"options": ["strict", "loose"], "multiple": False},
        "regions": {"options": ["east", "west"], "multiple": True},
    }


def test_a_chart_no_layout_places_publishes_no_options() -> None:
    board = compile(_LINKED_BOARD + "rows:\n  - by_metric\n").board
    assert board is not None
    assert list(linked_variable_options(board, None, {})) == ["metric"]


def test_options_travel_as_authored_not_as_text() -> None:
    numeric = """
variables:
  year:
    input: select
    options:
      static: [2023, 2024]
queries:
  q:
    columns: [arm, year, rate]
    values:
      - [ac, 2023, 0.3]
charts:
  c:
    query: q
    type: bar
    x: arm
    y: rate
    color: year
    link: "?year={{ color }}"
"""
    board = compile(numeric).board
    assert board is not None
    assert linked_variable_options(board, None, {}) == {
        "year": {"options": [2023, 2024], "multiple": False}
    }


@pytest.mark.parametrize(
    ("link", "published"),
    [
        ("?metric={{ color }}", True),
        ("?metric={{color}}", True),
        ("?metric={{ x }}", False),
        ("?metric={{ color }}&arm={{ x }}", False),
        ("?other={{ color }}", False),
    ],
)
def test_only_a_color_setter_link_publishes_its_options(
    link: str, published: bool
) -> None:
    board = compile(
        _LINKED_BOARD.replace('link: "?metric={{ color }}"', f'link: "{link}"')
    ).board
    assert board is not None
    assert ("metric" in linked_variable_options(board, None, {})) is published


def test_a_chart_in_a_nested_board_publishes_the_options_its_link_sets() -> None:
    nested = """
variables:
  metric:
    input: select
    options:
      static: [strict, loose]
queries:
  rates:
    columns: [arm, metric, rate]
    values:
      - [ac, strict, 0.3]
rows:
  - title: Section
    charts:
      by_metric:
        query: rates
        type: bar
        x: arm
        y: rate
        color: metric
        link: "?metric={{ color }}"
    rows:
      - by_metric
"""
    assert ">loose<" in _render_strict(nested)


def _render_strict(yaml_content: str) -> str:
    result = compile(yaml_content)
    assert result.success and result.board is not None, result.errors
    executor = Run(
        result.board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    rendered = render(
        result.board, executor.ctx, executor.ledger, variables={"metric": "strict"}
    )
    assert rendered.board_error is None, rendered.board_error
    assert isinstance(rendered.output, str)
    svg = rendered.output
    return svg


def test_a_filtered_linked_legend_renders_every_option_in_order() -> None:
    svg = _render_strict(_LINKED_BOARD)
    assert 'data-dbt-legend-var="metric"' in svg
    assert svg.index(">strict<") < svg.index(">loose<")


_VIEWED_BOARD = """
queries:
  rates:
    columns: [arm, metric, rate]
    values:
      - [ac, strict, 0.3]
      - [ac, loose, 0.5]
charts:
  c:
    query: rates
    type: bar
    x: arm
    y: rate
    color: metric
    style:
      legend:
        visible: false
"""


def _render_with(yaml_content: str, variables: dict[str, Any]) -> str:
    result = compile(yaml_content)
    assert result.success and result.board is not None, result.errors
    executor = Run(
        result.board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    rendered = render(result.board, executor.ctx, executor.ledger, variables=variables)
    assert rendered.board_error is None, rendered.board_error
    assert isinstance(rendered.output, str)
    return rendered.output


def test_a_chart_view_rides_on_its_chart_even_when_it_draws_no_entry() -> None:
    svg = _render_with(_VIEWED_BOARD, {"_dct_legend_c": ["strict"]})

    chart = re.search(r'<g class="dbt-chart[^>]*>', svg)
    assert chart is not None
    assert 'data-dbt-legend-view="_dct_legend_c"' in chart.group()
    assert 'data-dbt-legend-active="[&quot;strict&quot;]"' in chart.group()


@pytest.mark.parametrize("cleared", ["", [], None])
def test_a_cleared_chart_view_is_not_stamped(cleared: object) -> None:
    svg = _render_with(_VIEWED_BOARD, {"_dct_legend_c": cleared})

    assert "data-dbt-legend-view" not in svg


def _zoom_board(chart_type: str, regions: tuple[str, ...]) -> str:
    values = {
        "EMEA": [10, 90, 130, 80],
        "AMER": [150, 190, 210, 242],
        "APAC": [110, 160, 120, 205],
    }
    rows = "\n".join(
        f"      - [m{i}, {region}, {value}]"
        for region in regions
        for i, value in enumerate(values[region])
    )
    orientation = (
        "\n    style:\n      orientation: vertical" if chart_type == "bar" else ""
    )
    return f"""
queries:
  q:
    columns: [month, region, revenue]
    values:
{rows}
charts:
  c:
    query: q
    type: {chart_type}
    x: month
    y: revenue
    color: region{orientation}
"""


def _value_axis(svg: str) -> tuple[str, list[str]]:
    """The y axis's description and its tick labels."""
    at = svg.index('data-dct-role="y-axis"')
    axis = svg[at:]
    axis = axis[
        : axis.find("data-dct-role=", 10)
        if "data-dct-role=" in axis[10:]
        else len(axis)
    ]
    description = re.search(r"values from (\S+) to (\S+?)\"", axis)
    assert description is not None, axis[:300]
    labels = re.findall(r"<text[^>]*>([^<]*)</text>", axis)
    return description.group(0), labels


@pytest.mark.parametrize("chart_type", ["bar", "line"])
def test_a_zoomed_axis_is_the_axis_of_a_chart_holding_only_the_shown_rows(
    chart_type: str,
) -> None:
    zoomed = _render_with(
        _zoom_board(chart_type, ("EMEA", "AMER", "APAC")),
        {"_dct_legend_c": ["EMEA"]},
    )
    alone = _render_with(_zoom_board(chart_type, ("EMEA",)), {})

    assert _value_axis(zoomed) == _value_axis(alone)


def _ranged_board(regions: tuple[str, ...]) -> str:
    bars = {
        "EMEA": [(40, 90), (55, 130)],
        "AMER": [(150, 190), (160, 242)],
    }
    rows = "\n".join(
        f"      - [m{i}, {region}, {low}, {high}]"
        for region in regions
        for i, (low, high) in enumerate(bars[region])
    )
    return f"""
queries:
  q:
    columns: [month, region, low, high]
    values:
{rows}
charts:
  c:
    query: q
    type: bar
    x: month
    y: high
    y_start: low
    color: region
    style:
      orientation: vertical
rows:
  - c
"""


def _axis_ticks(svg: str) -> tuple[str, list[str]]:
    description, labels = _value_axis(svg)
    return description, [label for label in labels if re.match(r"[\d\u2212$]", label)][
        :9
    ]


def test_a_zoomed_ranged_bar_fits_its_start_values_too() -> None:
    zoomed = _render_with(_ranged_board(("EMEA", "AMER")), {"_dct_legend_c": ["EMEA"]})
    alone = _render_with(_ranged_board(("EMEA",)), {})

    assert _axis_ticks(zoomed) == _axis_ticks(alone)


def _mixed_sign_board(regions: tuple[str, ...]) -> str:
    values = {
        "EMEA": [-410000, -520000, -300000],
        "AMER": [1500000, 3100000, 2400000],
    }
    rows = "\n".join(
        f"      - [m{i}, {region}, {value}]"
        for region in regions
        for i, value in enumerate(values[region])
    )
    return f"""
queries:
  q:
    columns: [month, region, revenue]
    values:
{rows}
charts:
  c:
    query: q
    type: line
    x: month
    y: revenue
    color: region
    style:
      legend:
        position: right
      endpoint_labels:
        visible: false
rows:
  - c
"""


def test_a_zoom_to_negative_series_anchors_its_ruler_as_those_rows_alone_would() -> (
    None
):
    zoomed = _render_with(
        _mixed_sign_board(("EMEA", "AMER")), {"_dct_legend_c": ["EMEA"]}
    )
    alone = _render_with(_mixed_sign_board(("EMEA",)), {})

    assert _axis_ticks(zoomed) == _axis_ticks(alone)


def test_a_multiselect_with_several_values_shows_a_count_and_the_first() -> None:
    """The field shows a summary, not the list: the list is what the menu's
    "Show only selected" is for, and a joined list is what grew the field. The
    count leads, so cutting a long first value never cuts the count."""
    var = Variable(input="multiselect")

    assert (
        format_variable_display_value(var, ["South", "North", "East"], "multiselect")
        == "(3) South"
    )


def test_a_multiselect_with_one_value_shows_that_value() -> None:
    var = Variable(input="multiselect")

    assert format_variable_display_value(var, ["North"], "multiselect") == "North"
