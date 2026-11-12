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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.column_match import match_column
from dbt_charts.core.compile.models.chart.normalized import CalloutChart
from dbt_charts.core.compile.models.variable.authored import VariableInputType
from dbt_charts.core.compile.normalize.variable_input_refinement import (
    RefinedType,
    refine_input_type_from_data,
)
from dbt_charts.core.compile.normalize.variables import OPTION_COUNT_COLUMN
from dbt_charts.core.compile.resolve.style.board import resolve_cascaded_font
from dbt_charts.core.compile.template.daterange import (
    current_board_clock,
    is_daterange_preset,
    parse_daterange_preset,
    resolve_daterange_preset_iso,
)
from dbt_charts.core.compile.template.variables import (
    UNSET_MULTISELECT,
    coerce_multiselect,
    color_link_variable,
    variable_value_is_absent,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_VARIABLE_OPTIONS_FAILED
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.render.board_variables import (
    LinkedOptions,
    current_daterange_presets,
    variable_link,
)
from dbt_charts.core.render.conditions import eval_bool_condition
from dbt_charts.core.render.controls import compact_preset_label
from dbt_charts.core.render.variables_layout import ControlSpec, traits_of
from dbt_charts.core.text.case import inferred_display_name
from dbt_charts.core.text.format_d3 import active_time_locale, board_word

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
    from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
    from dbt_charts.core.compile.models.style.theme.variables import VariablesStyle
    from dbt_charts.core.compile.models.variable.authored import Variable
    from dbt_charts.core.execute.cache_backend import CacheRows
    from dbt_charts.core.execute.chart_data_provider import ChartDataProvider


UNSET_SELECT_MESSAGE = "controls.all"
UNSET_DATERANGE_MESSAGE = "controls.all_dates"
UNSET_DATE_MESSAGE = "controls.any_date"


# The inputs a user types free text into: they show a placeholder while empty,
# and number carries authored bounds.
FREE_ENTRY_INPUTS = frozenset({"text", "input", "textarea", "number"})


@dataclass(frozen=True)
class Segment:
    """One button of a button row: the value it commits, its text, and its link.

    ``href`` is None when the button does nothing — the selected button of a
    required variable, or any button of a disabled control.
    """

    value: str
    label: str
    selected: bool
    href: str | None


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
    # Each option's row count, aligned with `option_values`; empty when the
    # options carry none (static options, an authored query).
    option_counts: tuple[int, ...]
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
    # The preset id a daterange commits; `current` then holds its dates.
    preset: str | None = None
    # The preset menu a daterange offers; empty for every other input.
    presets: tuple[str, ...] = ()
    # The buttons of the buttons form; empty when the control draws as a field.
    segments: tuple[Segment, ...] = ()

    @property
    def spec(self) -> ControlSpec:
        """The layout engine's view of this control."""
        return ControlSpec(
            name=self.name,
            input=self.input,
            label=self.label,
            value=self.display_value,
            segment_labels=tuple(segment.label for segment in self.segments),
            segments_labeled=self.input != "daterange",
        )


def resolve_controls(
    variable_defs: dict[str, Variable],
    current_values: VariableValues,
    results: ChartDataProvider | None,
    variables_style: VariablesStyle,
) -> tuple[ResolvedControl, ...]:
    """Settle every visible variable into a control, in document order.

    Order is the compiled order: a host binds the i-th control to the i-th box,
    so nothing here may reorder them.
    """
    label_font = resolve_cascaded_font(variables_style.label.font)
    controls: list[ResolvedControl] = []
    for name, var_def in variable_defs.items():
        if not var_def.visible:
            continue
        option_values, option_counts = resolve_options(
            name, var_def, results, current_values
        )
        refined = _refine(var_def, option_values)
        low, high, step = _slider_bounds(var_def, refined, variables_style)
        current = current_values.get(name, var_def.default)
        preset: str | None = None
        if refined.input_type in ("slider", "range") and current is None:
            # A slider always shows a position; with nothing committed that is
            # the low end, which is where the mounted control shows it too.
            current = low
        elif refined.input_type == "daterange":
            preset = current_daterange_presets().get(name)
            if isinstance(current, str) and is_daterange_preset(current):
                # The fallback above is the raw `default:`, which no render
                # narrowing has seen.
                preset = current
                current = resolve_daterange_preset_iso(current, current_board_clock())
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
        enabled = _is_enabled(var_def, current_values, results)
        presets = (
            variables_style.presets if var_def.presets is None else var_def.presets
        )
        segments = _segments(
            name,
            refined.input_type,
            option_values,
            presets,
            current,
            preset,
            not var_def.required,
            enabled,
            current_values,
            variables_style.presets_display == "buttons",
            bool(var_def.options and var_def.options.static),
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
                option_counts=option_counts,
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
                enabled=enabled,
                preset=preset,
                presets=presets if refined.input_type == "daterange" else (),
                segments=segments,
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


def _segments(
    name: str,
    input_type: VariableInputType,
    option_values: list[str],
    presets: tuple[str, ...],
    current: Any,  # type-state: explicit_any — the committed value, whatever shape its widget commits (ResolvedControl.current)
    preset: str | None,
    can_unset: bool,
    linkable: bool,
    current_values: VariableValues,
    show_presets: bool,
    static_options: bool,
) -> tuple[Segment, ...]:
    """The buttons form's buttons, or none when the control draws as a field.

    A radio draws buttons only with static options: a query's options are known only
    after the query runs, which the sizing pass cannot do, and a button row sized as
    a field would overrun its band. A daterange draws its presets as buttons only when the
    style asks for it. Buttons link by name — a preset's link carries ``last_6_months``,
    never the dates it resolves to today — and the selected one links to the
    unset state, or to nothing when the variable cannot be unset.
    """
    kind = traits_of(input_type).segments
    if kind == "options" and not static_options:
        return ()
    if kind == "options":
        entries = [(value, value) for value in option_values]
        chosen = None if current in (None, "") else str(current)
    elif kind == "presets" and show_presets:
        entries = [
            (preset_name, compact_preset_label(parse_daterange_preset(preset_name)))
            for preset_name in presets
        ]
        chosen = preset
    else:
        return ()
    segments: list[Segment] = []
    for value, label in entries:
        selected = value == chosen
        if not linkable or (selected and not can_unset):
            href = None
        else:
            href = variable_link(current_values, name, "" if selected else value)
        segments.append(Segment(value, label, selected, href))
    return tuple(segments)


def _is_enabled(
    var_def: Variable,
    current_values: VariableValues,
    results: ChartDataProvider | None,
) -> bool:
    """Whether this control is operable right now.

    Only the render pass can answer this: a ``{query, column}`` condition needs
    an executor, and the sizing pass has none. Skipping it is not a fallback:
    ``enabled`` moves no box, so geometry never has to know, and the render
    pass settles it before anything is drawn.
    """
    if results is None:
        return True
    return eval_bool_condition(
        var_def.enabled, True, dict(current_values), results, "Variable enabled"
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
    name: str,
    var_def: Variable,
    results: ChartDataProvider | None,
    current_values: VariableValues,
) -> list[str]:
    """Resolve option values from a static list or a query, de-duplicated, as text."""
    return list(
        dict.fromkeys(
            str(cell)
            for cell in resolve_option_cells(name, var_def, results, current_values)
        )
    )


def resolve_option_cells(
    name: str,
    var_def: Variable,
    results: ChartDataProvider | None,
    current_values: VariableValues,
) -> list[Any]:  # type-state: explicit_any — raw option cells, as authored or queried
    """Resolve option values from a static list or a query, as authored or queried.

    Column-bound options (``options.column`` / ``variable.column``) are promoted
    to synthetic named queries at compile time, so they arrive here via the
    ``options.query`` branch — no separate column-resolution path needed.
    """
    query = _option_query(var_def)
    if query is not None and results is not None:
        return query_option_cells(name, query, results, dict(current_values))
    if var_def.options and var_def.options.static:
        return list(var_def.options.static)
    return []


def _option_query(var_def: Variable) -> str | None:
    """The query a variable's options come from; None for static or no options."""
    if var_def.options and var_def.options.static:
        return None
    if var_def.options and var_def.options.query:
        return var_def.options.query
    if isinstance(var_def.query, str):
        return var_def.query
    return None


def linked_variable_options(
    board: Board | ResolvedBoard,
    results: ChartDataProvider | None,
    values: VariableValues,
) -> dict[str, LinkedOptions]:
    """The options of each choice variable a chart's link sets from its color.

    Walks the whole board tree, so the sizing pass (compiled board) and the main
    pass (resolved board) agree, and only the charts a layout places: an
    undrawn chart's link must not run an options query. A variable whose options are unknown here is
    left out: its linked legend lists only the series the query returned.
    """
    boards: list[Board | ResolvedBoard] = [board]
    for each in boards:
        boards.extend(  # noqa: B909 — queue growth: BFS over the board tree
            item.board for item in each.layout.items if item.board is not None
        )
    set_by_links = {
        color_link_variable(item.chart.link)
        for each in boards
        for item in each.layout.items
        if item.chart is not None
        and not isinstance(item.chart, CalloutChart)
        and isinstance(item.chart.link, str)
    }
    linked: dict[str, LinkedOptions] = {}
    for each in boards:
        for name, var in each.variables.items():
            if name in linked or var.input not in ("select", "radio", "multiselect"):
                continue
            if name not in set_by_links:
                continue
            options = list(
                dict.fromkeys(resolve_option_cells(name, var, results, values))
            )
            if options:
                linked[name] = {
                    "options": options,
                    "multiple": var.input == "multiselect",
                }
    return linked


def resolve_query_options(
    name: str,
    query_ref: str,
    results: ChartDataProvider,
    variables: VariableValues,
) -> list[str]:
    """Resolve variable `name`'s select options from a named query, as text.

    See ``query_option_cells`` for the failure contract.
    """
    return [
        str(cell) for cell in query_option_cells(name, query_ref, results, variables)
    ]


def query_option_cells(
    name: str,
    query_ref: str,
    results: ChartDataProvider,
    variables: VariableValues,
) -> list[Any]:  # type-state: explicit_any — raw query cells
    """Resolve variable `name`'s select options from a named query, as queried.

    See ``query_option_rows`` for the failure contract.
    """
    return _option_cells(
        query_option_rows(name, query_ref, results, variables), query_ref
    )


def query_option_rows(
    name: str,
    query_ref: str,
    results: ChartDataProvider,
    variables: VariableValues,
) -> CacheRows:
    """Run variable `name`'s options query and return its rows.

    A failed options query raises rather than drawing an empty dropdown: an
    empty list is indistinguishable from a table with no rows, so a missing
    table would otherwise ship as a control that silently filters nothing.

    Args:
        name: The variable whose options these are, named in the error.
        query_ref: Query name reference (e.g., "queries.city_options" or "city_options")
        results: rows by query name
        variables: Current variable values for query execution

    Raises:
        QueryError: ERR-VARIABLE-OPTIONS-FAILED when the options query fails.
    """
    query_name = query_ref.replace("queries.", "")
    try:
        return results.execute_query(query_name, variables)
    except QueryError as e:
        raise QueryError.from_code(
            ERR_VARIABLE_OPTIONS_FAILED,
            variable=name,
            query_name=query_name,
            cause=str(e),
        ) from e


def _option_cells(
    rows: CacheRows,
    query: str,
) -> list[Any]:  # type-state: explicit_any — raw query cells
    if not rows:
        return []
    value_key = _option_value_key(rows, query)
    return [row[value_key] for row in rows if row.get(value_key) is not None]


def _option_value_key(rows: CacheRows, query: str) -> str:
    """The column an options query's values come from: 'value', 'label', 'name'
    or 'id' if the rows have one (``match_column``), else the first column."""
    columns = rows[0].keys()
    return next(
        (
            found
            for key in ("value", "label", "name", "id")
            if (found := match_column(key, columns, f"options query {query!r}"))
            is not None
        ),
        next(iter(rows[0])),
    )


def resolve_options(
    name: str,
    var_def: Variable,
    results: ChartDataProvider | None,
    current_values: VariableValues,
) -> tuple[list[str], tuple[int, ...]]:
    """The option values as text, de-duplicated, and each one's row count.

    One fetch serves both, so a control's values and counts come from the
    same rows. The counts are aligned with the values, and empty unless the
    options query returns ``OPTION_COUNT_COLUMN``: only a column-bound
    variable's generated query does.
    """
    query = _option_query(var_def)
    if query is None or results is None:
        return resolve_option_values(name, var_def, results, current_values), ()
    rows = query_option_rows(name, query, results, dict(current_values))
    values = list(dict.fromkeys(str(cell) for cell in _option_cells(rows, query)))
    if not rows or OPTION_COUNT_COLUMN not in rows[0]:
        return values, ()
    value_key = _option_value_key(rows, query)
    counts = {
        str(row[value_key]): int(row[OPTION_COUNT_COLUMN])
        for row in rows
        if row.get(value_key) is not None
    }
    return values, tuple(counts[value] for value in values)


def read_only_unset_label(var_def: Variable, default_id: str) -> str:
    """Label shown for a variable holding no value — placeholder, else the
    board language's word for ``default_id``."""
    if var_def.placeholder:
        return var_def.placeholder
    return board_word(default_id)


def _select_value_is_unset(value: Any) -> bool:
    return value is None or value == ""


def _daterange_is_unset(value: Any) -> bool:
    if variable_value_is_absent(value):
        return True
    if not isinstance(value, (list, tuple)):
        return False
    if len(value) != 2:
        return False
    return value[0] in (None, "") and value[1] in (None, "")


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
            return read_only_unset_label(var_def, UNSET_SELECT_MESSAGE)
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
            return read_only_unset_label(var_def, UNSET_SELECT_MESSAGE)
        # A summary, not the list: the field holds one width, and the menu's
        # "Show only selected" is where the whole selection is read.
        if len(selected) == 1:
            return str(selected[0])
        return board_word(
            "controls.selection_summary",
            first=str(selected[0]),
            n=str(len(selected)),
        )

    if input_type == "daterange":
        if _daterange_is_unset(value):
            return read_only_unset_label(var_def, UNSET_DATERANGE_MESSAGE)
        if not isinstance(value, (list, tuple)):
            raise ValueError(
                f"daterange variable expects a 2-element list, got {type(value).__name__}"
            )
        if len(value) != 2:
            raise ValueError(
                f"daterange variable expects exactly 2 elements, got {len(value)}"
            )
        # One open end filters on the other alone, so the label names that end.
        start, end = value[0], value[1]
        if start in (None, ""):
            return board_word("controls.until", date=format_date_label(str(end)))
        if end in (None, ""):
            return board_word("controls.from", date=format_date_label(str(start)))
        return format_daterange_label(str(start), str(end))

    if input_type == "checkbox":
        if isinstance(value, str):
            checked = value.lower().strip() in ("true", "yes", "1")
        else:
            checked = bool(value)
        return board_word("controls.yes" if checked else "controls.no")

    if input_type in ("date", "datepicker"):
        # Captioned like select and daterange rather than left blank. A date has
        # no glyph of its own in a static export — the calendar is an affordance
        # the bound board reveals — so an empty one would draw a label and
        # nothing else, and a reader could not tell it from a control that
        # failed to render.
        if value is None or value == "":
            return read_only_unset_label(var_def, UNSET_DATE_MESSAGE)
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


def _month(d: datetime.date) -> str:
    return active_time_locale()["shortMonths"][d.month - 1]


def _format_date(d: datetime.date) -> str:
    return board_word(
        "controls.date", day=str(d.day), month=_month(d), year=str(d.year)
    )


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

    Falls back to raw ISO on parse error.
    """
    try:
        s = datetime.date.fromisoformat(start_iso)
        e = datetime.date.fromisoformat(end_iso)
    except ValueError:
        return board_word("controls.range", start=start_iso, end=end_iso)

    def fmt_no_yr(d: datetime.date) -> str:
        return board_word("controls.date_no_year", day=str(d.day), month=_month(d))

    if s == e:
        return _format_date(s)
    if s.year == e.year and s.month == e.month:
        return board_word(
            "controls.range_same_month",
            start_day=str(s.day),
            end_day=str(e.day),
            month=_month(s),
            year=str(s.year),
        )
    if s.year == e.year:
        return board_word("controls.range", start=fmt_no_yr(s), end=_format_date(e))
    return board_word("controls.range", start=_format_date(s), end=_format_date(e))
