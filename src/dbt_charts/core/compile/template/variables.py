"""Variable utilities for dbt charts.

Stage: COMPILE (utilities used by both execute and render)
Purpose: Shared variable processing functions.

This module provides utilities for working with variables that are needed
by both the execute and render stages.
"""

import json
import math
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime
from typing import Any

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.template.daterange import (
    BoardClock,
    is_daterange_preset,
    parse_daterange_preset,
    resolve_daterange_preset,
    resolve_daterange_preset_iso,
)
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_UNKNOWN_VARIABLE_OVERRIDE,
    ERR_VARIABLE_VALUE_NOT_IN_OPTIONS,
)
from dbt_charts.core.diagnostics.execution import ExecutionError


def variables_from_query_pairs(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Variable values from a query string's ``(key, value)`` pairs, in order.

    A list-valued variable travels as a repeated key — ``?region=west&region=east``
    — the way an HTML form sends a ``<select multiple>``: a link is hand-writable,
    carries no JSON, and each value is its own param so a member may contain any
    delimiter. ``dict(params)`` kept only the last repeat, so the standard form
    silently dropped every value but one.

    A repeat lands as the JSON list string a list value has always been carried
    as past this boundary (``parse_variable_json_strings`` narrows it), so the
    render key, the session store and every ``dict[str, str]`` downstream see
    exactly what they saw before; only the URL changed. A key that appears once
    is a scalar, and ``?region=`` is an explicit empty, distinct from absent.
    """
    values: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    for key, value in pairs:
        if key in members:
            members[key].append(value)
        elif key in values:
            members[key] = [values[key], value]
        else:
            values[key] = value
    for key, seen in members.items():
        values[key] = json.dumps(seen)
    return values


def parse_variable_json_strings(variables: VariableValues) -> VariableValues:
    """Parse JSON strings in variables (e.g., from URL parameters).

    When variables come from URL query parameters, complex values like
    date ranges are often serialized as JSON strings. This function
    parses them back to their native Python types.

    This function is idempotent - calling it on already-parsed values
    returns them unchanged.

    Args:
        variables: Variable values that may contain JSON strings

    Returns:
        Variables with JSON strings parsed to their actual types

    Example:
        >>> parse_variable_json_strings({"date_range": '["2024-01-01", "2024-01-31"]'})
        {"date_range": ["2024-01-01", "2024-01-31"]}
    """
    parsed: dict[str, Any] = {}
    for key, value in variables.items():
        if isinstance(value, str):
            # Try to parse as JSON (for date ranges, arrays, objects)
            if value.startswith("[") or value.startswith("{"):
                try:
                    parsed[key] = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    # Not valid JSON, keep as string
                    parsed[key] = value
            else:
                parsed[key] = value
        else:
            parsed[key] = value
    return parsed


# Input kinds whose SQL type follows from the kind alone. A choice input
# carries whatever its options are (`choice_member_type`); select and radio
# are handled below as scalars, multiselect above as a list, and text stays
# a string.
_DATE_INPUTS = frozenset({"date", "datepicker"})
_NUMBER_INPUTS = frozenset({"number", "slider", "range"})
_CHOICE_INPUTS = frozenset({"select", "radio"})
# Scalar typed inputs where an empty string means "unset" (→ None), like the
# renderer's absent-check. daterange is excluded — its own branch handles both
# a fully-empty container (variable_value_is_absent) and per-endpoint blanks.
_SCALAR_TYPED_INPUTS = _DATE_INPUTS | _NUMBER_INPUTS | frozenset({"checkbox"})
_BOOL_TRUE = frozenset({"true", "1", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "0", "no", "off"})

# The scalar runtime domain a single-value coercer may receive. Spelled out (not
# `Any`/`object`) so the coercers stay typed for pyright without tripping the
# dbt_charts/core type-state gate, which blocks both; broad enough that every
# isinstance below is meaningful and the trailing type-error raise stays reachable.
# Container inputs (daterange lists) are dispatched inline in the loop, not
# through these helpers.
_Coercible = str | int | float | bool | date

# What a multiselect value can arrive as, once unset has been mapped to an empty
# sequence by the caller. Spelled out for the same reason `_Coercible` is: the
# core type-state gate counts explicit `Any` and `| None` nodes, and a public
# coercer should not be the one exception to the rule this module follows.
_MultiselectRaw = _Coercible | list[_Coercible] | tuple[_Coercible, ...]

# Unset, in the shape `coerce_multiselect` accepts. Callers map `None` and a
# missing key onto this rather than the coercer widening its own domain.
UNSET_MULTISELECT: tuple[_Coercible, ...] = ()


def variable_value_is_absent(value: Any) -> bool:
    """Whether a variable holds no value, by the rule `required:` is checked with.

    None, a blank string, and an empty collection all count: URL params arrive as
    strings, so `?region=` is as unscoped as no value at all. Falsiness is not the
    same test — `default: false` on a checkbox and `default: 0` on a slider are
    values, and reading them as absent is the difference between a control the
    panel withholds and one it offers.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set)):
        return len(value) == 0
    return False


def parse_iso_date(value: str) -> date:
    """Parse an ISO ``YYYY-MM-DD`` string to a ``date``, raising ``ValueError``.

    The shared date parser for both boundaries: the compile-time default check
    (wraps ValueError as CompilationError) and runtime coercion (wraps it as
    ExecutionError). Keeping one parser means both agree on what a date is.
    """
    return date.fromisoformat(value.strip())


def coerce_variable_values(
    values: VariableValues, registry: dict[str, Variable]
) -> VariableValues:
    """Coerce runtime variable values to their declared semantic Python types.

    Runs at the runtime boundary (query execution), after defaults and user
    values have merged. URL params, CLI, and API values arrive as strings; a
    ``date`` variable's ``"2024-01-01"`` must reach SQL as a ``date`` object so
    strict engines get the dialect's date literal (inline path) or a typed bound
    parameter — not a varchar that Trino/BigQuery refuse to compare to a date
    column. The semantic type comes from the variable's ``input`` kind; values
    that don't match it raise ``ExecutionError`` (fail fast, no silent fixups).

    Names absent from ``registry`` (builtins, dir-navigation context) have no
    declared type and pass through untouched. An empty string is treated as
    unset (``None``), matching the renderer's absent-check.
    """
    result: VariableValues = {}
    for name, value in values.items():
        var = registry.get(name)
        if var is None:
            result[name] = value
            continue
        kind = var.input
        if kind == "multiselect":
            # Ahead of the None check below: an unset multiselect is [], not None.
            members = coerce_multiselect(UNSET_MULTISELECT if value is None else value)
            member_type = choice_member_type(var)
            result[name] = [_coerce_member(name, m, member_type) for m in members]
            continue
        if value is None:
            result[name] = value
            continue
        if (
            isinstance(value, str)
            and not value.strip()
            and (kind in _SCALAR_TYPED_INPUTS or kind in _CHOICE_INPUTS)
        ):
            result[name] = None  # empty scalar typed input = unset
        elif kind in _CHOICE_INPUTS:
            result[name] = _coerce_member(name, value, choice_member_type(var))
        elif kind in _DATE_INPUTS:
            result[name] = _coerce_date(name, value)
        elif kind == "daterange":
            if variable_value_is_absent(value):
                result[name] = None
                continue
            if isinstance(value, str):
                # Dated later, on the board clock (`resolve_daterange_presets`).
                _require_preset(name, value)
                result[name] = value
                continue
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ExecutionError(
                    f"Variable '{name}': daterange value must be [start, end], "
                    f"got {value!r}"
                )
            # Each endpoint is None or an empty string (both unset), or a
            # date/ISO-string. Inlined so the element type is inferred from the
            # comprehension rather than a separately-written `list[str | None]`
            # annotation — the type-state counter's `optional` category would
            # still count that annotation (report-only, not gate-blocking), but
            # inference reads cleaner here regardless.
            result[name] = [
                (
                    None
                    if endpoint is None
                    or (isinstance(endpoint, str) and not endpoint.strip())
                    else _coerce_date(name, endpoint)
                )
                for endpoint in value
            ]
        elif kind in _NUMBER_INPUTS:
            result[name] = _coerce_number(name, value)
        elif kind == "checkbox":
            result[name] = _coerce_bool(name, value)
        else:
            result[name] = value
    return result


def static_option_values(var: Variable) -> list[str]:
    """A variable's authored ``options.static`` as strings; [] when it has none."""
    static = var.options.static if var.options else None
    return [str(option) for option in static] if static else []


def _is_option(value: _Coercible, options: Sequence[str]) -> bool:
    """Whether ``value`` is one of ``options``; a number matches by value, so
    ``--var n=1.0`` selects the option ``1``."""
    text = str(value)
    if text in options:
        return True
    try:
        number = float(text)
    except ValueError:
        return False
    return any(_float_or_none(option) == number for option in options)


def _float_or_none(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def check_option_values(
    registry: dict[str, Variable],
    overrides: VariableValues,
    options_of: Callable[[str, Variable], Sequence[str]],
) -> None:
    """Reject a choice-input value outside the variable's options.

    A value outside a select's options filters every dependent query to
    nothing, which looks like a healthy empty result. ``options_of`` returns
    the options the caller can vouch for; an empty sequence means they are
    unknown and the value is not checked. Unset values (``None``, blank, ``[]``)
    are legal, and names the registry lacks belong to
    ``check_variable_overrides``.
    """
    for name, value in parse_variable_json_strings(overrides).items():
        var = registry.get(name)
        if (
            var is None
            or var.input not in (*_CHOICE_INPUTS, "multiselect")
            or variable_value_is_absent(value)
        ):
            continue
        options = options_of(name, var)
        if not options:
            continue
        members = coerce_multiselect(value) if var.input == "multiselect" else [value]
        for member in members:
            if not _is_option(member, options):
                raise ExecutionError.from_code(
                    ERR_VARIABLE_VALUE_NOT_IN_OPTIONS,
                    variable=name,
                    value=member,
                    options=options,
                )


def check_variable_overrides(
    registry: dict[str, Variable], overrides: VariableValues
) -> None:
    """Reject caller-supplied variable values the board cannot have meant.

    Runs at the runtime boundary on the values a caller set (``--var``, API and
    MCP bindings), before defaults merge in. A name the board does not declare
    would otherwise run every query as if it were never set, and a value
    outside a choice input's *static* options (the only options known without
    running a query) would filter to nothing; both look like a healthy empty
    result.
    """
    for name in overrides:
        if name not in registry:
            raise ExecutionError.from_code(
                ERR_UNKNOWN_VARIABLE_OVERRIDE,
                variable=name,
                available=sorted(registry),
            )
    check_option_values(
        registry, overrides, lambda _name, var: static_option_values(var)
    )


def _require_preset(name: str, value: str) -> None:
    try:
        parse_daterange_preset(value)
    except ValueError as e:
        raise ExecutionError(f"Variable '{name}': {e}") from None


def resolve_daterange_presets(
    values: VariableValues, registry: dict[str, Variable], clock: BoardClock
) -> VariableValues:
    """Each daterange preset id in ``values`` as its ``[start, end]`` dates on
    the board's clock."""
    return {
        **values,
        **{
            name: list(resolve_daterange_preset(preset, clock))
            for name, preset in daterange_preset_ids(values, registry).items()
        },
    }


def normalize_render_values(
    values: VariableValues, registry: dict[str, Variable], clock: BoardClock
) -> VariableValues:
    """Narrow multiselects to lists and resolve daterange presets for render.

    The render boundary's counterpart to the coercion `coerce_variable_values`
    does for queries — same narrowing rule, applied where render merges defaults
    with runtime values. Render does not run the full coercion (date/number
    narrowing raises, and a bad date should fail at query time, not while
    drawing), but a multiselect must be a list on both paths or a heading
    template safe in one is a crash in the other.

    Absent names matter as much as present ones: a multiselect with no
    ``default:`` never reaches ``variable_defaults`` at all, so a template would
    see Jinja's Undefined and ``| join`` would raise — the failure that took
    down whole boards.

    A daterange preset id becomes its ISO ``[start, end]`` on the board's
    clock; any other string raises, since a template indexing it would read
    characters, not dates.
    """
    normalized = dict(values)
    for name, var in registry.items():
        raw = values.get(name)
        if var.input == "multiselect":
            normalized[name] = coerce_multiselect(
                UNSET_MULTISELECT if raw is None else raw
            )
        elif (
            var.input == "daterange"
            and isinstance(raw, str)
            and not variable_value_is_absent(raw)
        ):
            _require_preset(name, raw)
            normalized[name] = resolve_daterange_preset_iso(raw, clock)
    return normalized


def daterange_preset_ids(
    values: VariableValues, registry: dict[str, Variable]
) -> dict[str, str]:
    """The daterange variables whose value is a preset id, mapped to that id."""
    return {
        name: value
        for name, var in registry.items()
        if var.input == "daterange"
        and isinstance(value := values.get(name), str)
        and is_daterange_preset(value)
    }


def coerce_multiselect(value: _MultiselectRaw) -> list[_Coercible]:
    """Narrow any runtime shape of a `multiselect` value to `list[str]`.

    One declared input type used to reach templates as three shapes — `list`
    from a `default:`, `str` after an interaction (the control degrades to a
    single value and the runtime writes it back as a string), `None` with no
    default — so every author had to guard `join` by hand, and an unguarded one
    took down a whole board. This is the single place that shape is decided.

    Unset is `[]`, never `None`: an empty selection means "no filter", and a
    list is the only shape that makes `{{ v | join(', ') }}` safe unconditionally.
    Callers map an absent or `None` value onto `UNSET_MULTISELECT` before calling
    in.

    Members keep their own types. The shape being fixed here is the *container*,
    not the element: re-typing an integer option to a string would bind a varchar
    where the column is numeric, which is the silent-stringification failure the
    surrounding coercers exist to prevent. Display sites stringify for display;
    the SQL path must not.
    """
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str) and not value.strip():
        return []
    return [value]


def choice_member_type(var: Variable) -> str | None:
    """The type a choice input's values have — `"number"`, `"date"`,
    `"boolean"`, or None for strings.

    An explicit `data_type` wins; otherwise a static option list typed as
    numbers names it (the list is homogeneous by construction). A query-driven
    option source with no `data_type` is a string: the option query's column
    type is not known here, and guessing from the value's shape would turn an
    id like "007" into 7.
    """
    if var.data_type in ("number", "date", "boolean"):
        return var.data_type
    if var.data_type in ("string", "array"):
        return None
    static = var.options.static if var.options else None
    if static and all(isinstance(o, (int, float)) for o in static):
        return "number"
    return None


def _coerce_member(name: str, value: _Coercible, member_type: str | None) -> _Coercible:
    if member_type == "number":
        return _coerce_number(name, value)
    if member_type == "date":
        return _coerce_date(name, value)
    if member_type == "boolean":
        return _coerce_bool(name, value)
    return value


def _coerce_date(name: str, value: _Coercible) -> date:
    """Narrow one date/datepicker (or daterange endpoint) value to a ``date``.

    Accepts what the runtime boundary produces — an ISO string or a native
    ``date`` (from a compile default) — and rejects everything else, notably a
    ``datetime``: it is a ``date`` subclass, so ``_to_sql_literal`` would emit
    ``TIMESTAMP`` for it, the exact varchar↔date mismatch this coercion prevents.
    Callers handle the empty-string-is-unset case before reaching here.
    """
    if isinstance(value, datetime):
        raise ExecutionError(
            f"Variable '{name}': date value must be a calendar date, not a "
            f"datetime with a time component, got {value!r}"
        )
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return parse_iso_date(value)
        except ValueError:
            raise ExecutionError(
                f"Variable '{name}': date value must be an ISO date "
                f"(YYYY-MM-DD), got {value!r}"
            ) from None
    raise ExecutionError(
        f"Variable '{name}': date value must be a string, got {type(value).__name__}"
    )


def _coerce_number(name: str, value: _Coercible) -> int | float:
    if isinstance(value, bool):
        raise ExecutionError(
            f"Variable '{name}': number value must be numeric, got a boolean"
        )
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _require_finite(name, value)
    if isinstance(value, str):
        return _parse_number(name, value.strip())
    raise ExecutionError(
        f"Variable '{name}': number value must be numeric, got {type(value).__name__}"
    )


def _parse_number(name: str, text: str) -> int | float:
    try:
        return int(text)
    except ValueError:
        pass
    try:
        parsed = float(text)
    except ValueError:
        raise ExecutionError(
            f"Variable '{name}': number value must be numeric, got {text!r}"
        ) from None
    return _require_finite(name, parsed)


def _require_finite(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise ExecutionError(
            f"Variable '{name}': number value must be finite, got {value!r}"
        )
    return value


def _coerce_bool(name: str, value: _Coercible) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _BOOL_TRUE:
            return True
        if text in _BOOL_FALSE:
            return False
    raise ExecutionError(
        f"Variable '{name}': checkbox value must be a boolean, got {value!r}"
    )


__all__ = [
    "check_option_values",
    "check_variable_overrides",
    "static_option_values",
    "UNSET_MULTISELECT",
    "choice_member_type",
    "coerce_multiselect",
    "variables_from_query_pairs",
    "coerce_variable_values",
    "daterange_preset_ids",
    "normalize_render_values",
    "parse_iso_date",
    "parse_variable_json_strings",
    "variable_value_is_absent",
]
