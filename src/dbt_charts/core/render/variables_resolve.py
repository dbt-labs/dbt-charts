"""What each variable control actually *is*, decided once.

Stage: RENDER (post-query)
Purpose: Turn compiled variable definitions plus committed values into settled
controls — refined widget, label, display text, options, ticked state, slider
bounds.

The widget a variable renders as is not knowable until its options have been
fetched: an auto-detected select over ``2024-01-01, 2024-02-01`` is really a
datepicker. Every consumer needs that answer — the layout engine to size the
control, the chrome renderer to draw it, the host's control layer to bind it —
and each one resolving it independently is how a sizer and a renderer end up
disagreeing about which control is on the board. So it resolves here, once, and
the result is passed along rather than recomputed.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.variable.authored import VariableInputType
from dbt_charts.core.compile.resolve.style.board import resolve_cascaded_font
from dbt_charts.core.compile.template.variables import (
    UNSET_MULTISELECT,
    coerce_multiselect,
    variable_value_is_absent,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.render.conditions import eval_bool_condition
from dbt_charts.core.render.variable_input_refinement import (
    RefinedType,
    refine_input_type_from_data,
)
from dbt_charts.core.render.variables_layout import ControlSpec
from dbt_charts.core.text.case import inferred_display_name

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import VariableValues
    from dbt_charts.core.compile.models.style.theme.variables import VariablesStyle
    from dbt_charts.core.compile.models.variable.authored import Variable
    from dbt_charts.core.execute.chart_data_provider import ChartDataProvider

logger = logging.getLogger(__name__)

UNSET_SELECT_LABEL = "All"
UNSET_DATERANGE_LABEL = "All dates"
UNSET_DATE_LABEL = "Any date"


# The inputs a user types free text into: they show a placeholder while empty,
# and number carries authored bounds.
FREE_ENTRY_INPUTS = frozenset({"text", "input", "textarea", "number"})


@dataclass(frozen=True)
class ResolvedControl:
    """One variable, settled: the widget it is and everything drawn from it.

    ``input`` is the refined widget, not the compiled one — read it rather than
    ``var_def.input`` anywhere the answer must match what is on screen. Every
    field below is derived from it for the same reason.

    ``enabled`` is settled here too, but never affects geometry: a disabled
    control occupies the same box as an operable one. That is what lets the
    sizing pass — which arrives with no executor — take the safe answer for a
    query-backed condition and leave the real one to the render pass.
    """

    name: str
    var_def: Variable
    input: VariableInputType
    label: str
    display_value: str
    # The committed value, narrowed to the shape its widget commits: a list for
    # a multiselect however it arrived, a float for a slider. Narrow here, not
    # at each reader — the display text, the published `data-dbt-value`, and the
    # query path all state one selection, and a second narrowing downstream is a
    # second reading of it.
    current: Any
    option_values: tuple[str, ...]
    # Ticked state and slider bounds are settled here rather than re-derived by
    # whoever draws them: the chrome and the control mounted over it have to
    # agree, and a display string is not state.
    checked: bool
    # False only for a control the author gated off, or whose gate has not
    # opened yet. The chrome dims it and the runtime declines to bind it, so
    # `enabled: false` cannot render as an operable control.
    enabled: bool
    slider_min: float
    slider_max: float
    slider_step: float
    # Whether an empty/unset value is a legal state to land in — false for a
    # required variable, since `variable_value_is_absent` counts an empty list
    # as absent and the next render raises MissingRequiredVariablesError with no
    # control layer left to recover from. `required` decides this alone: a
    # default is not consulted once an empty value is committed, so it rescues
    # nothing here, and on a non-required variable it is a value to return to
    # rather than a reason to forbid empty. Settled once so every unset-offering
    # affordance (the select's blank "All" option, the multiselect's Clear
    # button and its per-toggle guard) reads the same fact rather than each
    # re-deriving it from var_def.required independently.
    can_unset: bool
    # The authored hint an empty free-entry field shows in place of a value;
    # None when the field holds a value or has no hint. The one place that
    # decides whether the drawn text is a hint.
    placeholder: str | None = None

    @property
    def spec(self) -> ControlSpec:
        """The layout engine's view of this control."""
        return ControlSpec(
            name=self.name,
            input=self.input,
            label=self.label,
            value=self.display_value,
        )


def resolve_controls(
    variable_defs: dict[str, Variable],
    current_values: VariableValues,
    executor: ChartDataProvider | None,
    variables_style: VariablesStyle,
) -> tuple[ResolvedControl, ...]:
    """Settle every visible variable into a control, in document order.

    Order is the compiled order: a host binds the i-th control to the i-th box,
    so nothing here may reorder them.
    """
    label_font = resolve_cascaded_font(
        variables_style.label.font, "variables.label.font"
    )
    controls: list[ResolvedControl] = []
    for name, var_def in variable_defs.items():
        if not var_def.visible:
            continue
        option_values = resolve_option_values(var_def, executor, current_values)
        refined = _refine(var_def, option_values)
        low, high, step = _slider_bounds(var_def, refined, variables_style)
        current = current_values.get(name, var_def.default)
        if refined.input_type in ("slider", "range") and current is None:
            # A slider always shows a position; with nothing committed that is
            # the low end, which is where the mounted control shows it too.
            current = low
        elif refined.input_type == "multiselect":
            # One selection, narrowed once. A multiselect legitimately arrives
            # as a list, as a bare scalar from a URL param written before it was
            # one, or as nothing — and the display text, the published value,
            # and the query path each have to state the same selection. Narrowing
            # per reader is how they drift: `display_value` coerced and
            # `data-dbt-value` did not, so `?region=US` drew "US" and published
            # the unparseable `US`, which the runtime read back as no filter.
            current = coerce_multiselect(
                UNSET_MULTISELECT if current is None else current
            )
        controls.append(
            ResolvedControl(
                name=name,
                var_def=var_def,
                input=refined.input_type,
                label=var_def.label or inferred_display_name(name, font=label_font),
                display_value=format_variable_display_value(
                    var_def, current, refined.input_type
                ),
                current=current,
                option_values=tuple(option_values),
                # Ticked state off the value, never off the label drawn for it.
                checked=(
                    current.strip().lower() in ("true", "yes", "1")
                    if isinstance(current, str)
                    else bool(current)
                ),
                slider_min=low,
                slider_max=high,
                slider_step=step,
                can_unset=not var_def.required,
                enabled=_is_enabled(var_def, current_values, executor),
                placeholder=(
                    var_def.placeholder
                    if refined.input_type in FREE_ENTRY_INPUTS
                    and current in (None, "")
                    and var_def.placeholder
                    else None
                ),
            )
        )
    return tuple(controls)


def _is_enabled(
    var_def: Variable,
    current_values: VariableValues,
    executor: ChartDataProvider | None,
) -> bool:
    """Whether this control is operable right now.

    Only the render pass can answer this: a ``{query, column}`` condition needs
    an executor, and the sizing pass has none. Skipping it is not a fallback:
    ``enabled`` moves no box, so geometry never has to know, and the render
    pass settles it before anything is drawn.
    """
    if executor is None:
        return True
    return eval_bool_condition(
        var_def.enabled, True, dict(current_values), executor, "Variable enabled"
    )


def _slider_bounds(
    var_def: Variable, refined: RefinedType, variables_style: VariablesStyle
) -> tuple[float, float, float]:
    """A slider's range: refined from data, else authored, else the theme's.

    Settled once so the drawn thumb and the control bound over it cannot land in
    different places. The refined bounds are all-or-nothing, matching how
    ``RefinedType`` produces them.
    """
    defaults = variables_style.input.range
    if refined.slider_min is not None:
        assert refined.slider_max is not None and refined.slider_step is not None
        return (
            float(refined.slider_min),
            float(refined.slider_max),
            float(refined.slider_step),
        )
    return (
        float(var_def.min if var_def.min is not None else defaults.default_min),
        float(var_def.max if var_def.max is not None else defaults.default_max),
        float(var_def.step if var_def.step is not None else defaults.default_step),
    )


def _refine(var_def: Variable, option_values: list[str]) -> RefinedType:
    """Data-aware widget refinement, gated to the case it can decide.

    Only a dropdown's own options carry the signal (dates, booleans, a numeric
    range), so nothing else is inspected.
    """
    if var_def.input in ("select", "multiselect") and option_values:
        return refine_input_type_from_data(var_def, option_values)
    return RefinedType(input_type=var_def.input)


def resolve_option_values(
    var_def: Variable,
    executor: ChartDataProvider | None,
    current_values: VariableValues,
) -> list[str]:
    """Resolve option values from a static list or a query, de-duplicated.

    Column-bound options (``options.column`` / ``variable.column``) are promoted
    to synthetic named queries at compile time, so they arrive here via the
    ``options.query`` branch — no separate column-resolution path needed.
    """
    if var_def.options and var_def.options.static:
        values = [str(opt) for opt in var_def.options.static]
    elif var_def.options and var_def.options.query and executor:
        values = resolve_query_options(
            var_def.options.query, executor, dict(current_values)
        )
    elif isinstance(var_def.query, str) and executor:
        values = resolve_query_options(var_def.query, executor, dict(current_values))
    else:
        return []
    return list(dict.fromkeys(values))


def resolve_query_options(
    query_ref: str, executor: ChartDataProvider, variables: dict[str, Any]
) -> list[str]:
    """Resolve a select's options from a named query.

    Best-effort: a failed or absent option query degrades the dropdown to empty
    rather than aborting the render. This includes pre-execution failures stored
    on the executor (raised as ``QueryError`` by ``execute_query``).

    Args:
        query_ref: Query name reference (e.g., "queries.city_options" or "city_options")
        executor: ChartDataProvider for running queries
        variables: Current variable values for query execution

    Returns:
        List of option values from the query, or [] when the query failed/is absent.
    """
    try:
        query_name = query_ref.replace("queries.", "")
        result = executor.execute_query(query_name, variables)

        if result and len(result) > 0:
            # Look for 'value' column first, then 'label', then first column
            first_row = result[0]
            value_key = None
            for key in ["value", "label", "name", "id"]:
                if key in first_row:
                    value_key = key
                    break
            if value_key is None:
                value_key = list(first_row.keys())[0]

            return [
                str(row[value_key]) for row in result if row.get(value_key) is not None
            ]

        return []
    except (ValueError, KeyError, TypeError, RuntimeError, QueryError):
        # Log as warning with context to make failures visible for debugging.
        # Best-effort policy: a failed option query renders the control empty,
        # not an error page. QueryError covers pre-execution failures stored in
        # the executor (e.g. a column-option query that hit a missing table).
        logger.warning(
            "Failed to resolve query options for a variable control",
            exc_info=True,
        )
        return []


def read_only_unset_label(var_def: Variable, default: str) -> str:
    """Label shown for a variable holding no value — placeholder, else default."""
    if var_def.placeholder:
        return var_def.placeholder
    return default


def _select_value_is_unset(value: Any) -> bool:
    return value is None or value == ""


def _daterange_is_unset(value: Any) -> bool:
    if variable_value_is_absent(value):
        return True
    if not isinstance(value, (list, tuple)):
        return False
    if len(value) != 2:
        return False
    return value[0] in (None, "") or value[1] in (None, "")


def format_variable_display_value(
    var_def: Variable, value: Any, input_type: VariableInputType | None = None
) -> str:
    """Human-readable filter state — the text the control displays.

    ``input_type`` is the widget the value will be *shown* as, which is the
    refined one wherever data has settled it. Formatting against the compiled
    input instead would caption a checkbox with a dropdown's ``All``.
    """
    if input_type is None:
        input_type = var_def.input

    if input_type in ("select", "radio"):
        if _select_value_is_unset(value):
            return read_only_unset_label(var_def, UNSET_SELECT_LABEL)
        if isinstance(value, (list, tuple)):
            # Runtime-boundary input: a JSON-array URL param (the shape
            # multiselect/daterange legitimately carry) landing on a scalar
            # input. Reject rather than stringify — str(["a", "b"]) round-trips
            # a Python repr through data-value and back through
            # updateVariable as a single malformed value.
            raise ValueError(
                f"{input_type} variable expects a scalar value, got a list: {value!r}"
            )
        return str(value)

    if input_type == "multiselect":
        # Through the same narrowing the query path uses, so the display and the
        # queries state one selection rather than two readings of it. There is
        # no invalid shape left to reject.
        selected = coerce_multiselect(UNSET_MULTISELECT if value is None else value)
        if not selected:
            return read_only_unset_label(var_def, UNSET_SELECT_LABEL)
        return ", ".join(str(member) for member in selected)

    if input_type == "daterange":
        if _daterange_is_unset(value):
            return read_only_unset_label(var_def, UNSET_DATERANGE_LABEL)
        if not isinstance(value, (list, tuple)):
            raise ValueError(
                f"daterange variable expects a 2-element list, got {type(value).__name__}"
            )
        if len(value) != 2:
            raise ValueError(
                f"daterange variable expects exactly 2 elements, got {len(value)}"
            )
        start, end = value[0], value[1]
        if start in (None, "") or end in (None, ""):
            return read_only_unset_label(var_def, UNSET_DATERANGE_LABEL)
        return format_daterange_label(str(start), str(end))

    if input_type == "checkbox":
        if value is None:
            return "No"
        if isinstance(value, str):
            return "Yes" if value.lower().strip() in ("true", "yes", "1") else "No"
        return "Yes" if value else "No"

    if input_type in ("date", "datepicker"):
        # Captioned like select and daterange rather than left blank. A date has
        # no glyph of its own in a static export — the calendar is an affordance
        # the bound board reveals — so an empty one would draw a label and
        # nothing else, and a reader could not tell it from a control that
        # failed to render.
        if value is None or value == "":
            return read_only_unset_label(var_def, UNSET_DATE_LABEL)
        return format_date_label(str(value))

    if input_type in (
        "text",
        "input",
        "textarea",
        "number",
        "slider",
        "range",
    ):
        if value is None or value == "":
            return var_def.placeholder or ""
        return str(value)

    if input_type == "auto":
        raise ValueError("variable display requires a resolved input type, not 'auto'")

    raise ValueError(f"unsupported variable input type for display: {input_type!r}")


_MONTHS_SHORT = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def _format_date(d: datetime.date) -> str:
    return f"{d.day} {_MONTHS_SHORT[d.month - 1]} {d.year}"


def format_date_label(iso: str) -> str:
    """Human-readable single-date label from an ISO date string.

    Falls back to the raw ISO string on parse error.
    """
    try:
        d = datetime.date.fromisoformat(iso)
    except ValueError:
        return iso
    return _format_date(d)


def format_daterange_label(start_iso: str, end_iso: str) -> str:
    """Human-readable chip label from ISO date strings.

    Mirrors JS formatRange() so the server-rendered initial label matches
    what the client renders post-interaction. Falls back to raw ISO on parse error.
    """
    try:
        s = datetime.date.fromisoformat(start_iso)
        e = datetime.date.fromisoformat(end_iso)
    except ValueError:
        return f"{start_iso} – {end_iso}"

    def fmt_no_yr(d: datetime.date) -> str:
        return f"{d.day} {_MONTHS_SHORT[d.month - 1]}"

    if s == e:
        return _format_date(s)
    if s.year == e.year and s.month == e.month:
        return f"{s.day}–{e.day} {_MONTHS_SHORT[s.month - 1]} {s.year}"
    if s.year == e.year:
        return f"{fmt_no_yr(s)} – {_format_date(e)}"
    return f"{_format_date(s)} – {_format_date(e)}"
