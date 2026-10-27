"""Tests for runtime variable coercion in compile/template/variables.py.

coerce_variable_values runs at the runtime boundary (query execution): it turns
string inputs from URL params / CLI / API into the typed Python objects the SQL
layer needs, so strict engines (Trino, BigQuery) receive DATE '...'/typed params
instead of varchar. See the "Two validation boundaries" note in the core AGENTS docs.
"""

from datetime import date, datetime
from typing import Any

import pytest

from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.template.jinja import resolve_jinja_template
from dbt_charts.core.compile.template.variables import (
    coerce_variable_values,
    daterange_preset_ids,
    normalize_render_values,
    resolve_daterange_presets,
)
from dbt_charts.core.diagnostics.execution import ExecutionError

_TODAY = date(2026, 10, 20)


def _registry(**inputs: str) -> dict[str, Variable]:
    return {name: Variable(input=input_type) for name, input_type in inputs.items()}


class TestCoerceDate:
    def test_iso_string_becomes_date(self) -> None:
        reg = _registry(d="date")
        result = coerce_variable_values({"d": "2024-01-01"}, reg)
        assert result["d"] == date(2024, 1, 1)
        assert isinstance(result["d"], date)

    def test_datepicker_input_also_coerces(self) -> None:
        reg = _registry(d="datepicker")
        assert coerce_variable_values({"d": "2024-06-15"}, reg)["d"] == date(
            2024, 6, 15
        )

    def test_already_typed_date_passes_through(self) -> None:
        reg = _registry(d="date")
        assert coerce_variable_values({"d": date(2024, 1, 1)}, reg)["d"] == date(
            2024, 1, 1
        )

    def test_empty_string_is_unset(self) -> None:
        reg = _registry(d="date")
        assert coerce_variable_values({"d": "  "}, reg)["d"] is None

    def test_none_stays_none(self) -> None:
        reg = _registry(d="date")
        assert coerce_variable_values({"d": None}, reg)["d"] is None

    def test_invalid_date_raises(self) -> None:
        reg = _registry(d="date")
        with pytest.raises(ExecutionError) as exc:
            coerce_variable_values({"d": "not-a-date"}, reg)
        assert "d" in str(exc.value)
        assert "not-a-date" in str(exc.value)

    def test_datetime_rejected(self) -> None:
        # A datetime would emit TIMESTAMP '...' from _to_sql_literal — the exact
        # mismatch this coercion prevents. A date variable must be date-only.
        reg = _registry(d="date")
        with pytest.raises(ExecutionError) as exc:
            coerce_variable_values({"d": datetime(2024, 1, 1, 10, 30)}, reg)
        assert "datetime" in str(exc.value)

    def test_non_str_non_date_rejected(self) -> None:
        reg = _registry(d="date")
        with pytest.raises(ExecutionError):
            coerce_variable_values({"d": 20240101}, reg)


class TestCoerceDaterange:
    def test_empty_list_is_unset(self) -> None:
        # A cleared date picker publishes [] (variables.js's unset spelling for
        # every list-valued control), which must coerce to None like every
        # other unset typed input, not raise.
        reg = _registry(r="daterange")
        assert coerce_variable_values({"r": []}, reg)["r"] is None

    def test_blank_string_is_unset(self) -> None:
        # Pins the other half of variable_value_is_absent: a hand-written
        # `?date_range=` arrives as "", not []. A narrowing that only checks
        # container emptiness (e.g. `if isinstance(value, (list, tuple)) and
        # not value`) would miss this and restore the ExecutionError.
        reg = _registry(r="daterange")
        assert coerce_variable_values({"r": "  "}, reg)["r"] is None

    @pytest.mark.parametrize("raw", ["2024-01-01", 0])
    def test_non_absent_scalar_still_raises(self, raw: Any) -> None:
        # A bare scalar is malformed, not unset — absence is emptiness, not
        # falsiness. `0` is the case that pins the difference: collapsing the
        # check to `if not value:` reads it as unset and still passes on the
        # string alone.
        reg = _registry(r="daterange")
        with pytest.raises(ExecutionError):
            coerce_variable_values({"r": raw}, reg)

    def test_pair_of_strings_becomes_dates(self) -> None:
        reg = _registry(r="daterange")
        result = coerce_variable_values({"r": ["2024-01-01", "2024-12-31"]}, reg)
        assert result["r"] == [date(2024, 1, 1), date(2024, 12, 31)]

    def test_none_endpoints_preserved(self) -> None:
        reg = _registry(r="daterange")
        assert coerce_variable_values({"r": [None, "2024-12-31"]}, reg)["r"] == [
            None,
            date(2024, 12, 31),
        ]

    def test_wrong_shape_raises(self) -> None:
        reg = _registry(r="daterange")
        with pytest.raises(ExecutionError):
            coerce_variable_values({"r": ["2024-01-01"]}, reg)

    def test_tuple_accepted(self) -> None:
        reg = _registry(r="daterange")
        assert coerce_variable_values({"r": ("2024-01-01", "2024-12-31")}, reg)[
            "r"
        ] == [date(2024, 1, 1), date(2024, 12, 31)]

    def test_datetime_endpoint_rejected(self) -> None:
        reg = _registry(r="daterange")
        with pytest.raises(ExecutionError):
            coerce_variable_values({"r": [datetime(2024, 1, 1, 9), None]}, reg)

    def test_preset_stays_an_id(self) -> None:
        reg = _registry(r="daterange")
        assert coerce_variable_values({"r": "last_7_days"}, reg)["r"] == "last_7_days"

    def test_unknown_preset_names_the_presets(self) -> None:
        reg = _registry(r="daterange")
        with pytest.raises(ExecutionError, match="last_90_days"):
            coerce_variable_values({"r": "last_91_days"}, reg)


def test_resolve_daterange_presets_dates_only_preset_values() -> None:
    reg = _registry(r="daterange", s="daterange", t="text")
    values = {"r": "last_7_days", "s": [date(2024, 1, 1), None], "t": "today"}
    assert resolve_daterange_presets(values, reg, _TODAY) == {
        "r": [date(2026, 10, 14), _TODAY],
        "s": [date(2024, 1, 1), None],
        "t": "today",
    }


class TestNormalizeRenderValues:
    def test_preset_reaches_templates_as_iso_dates(self) -> None:
        values = normalize_render_values(
            {"r": "year_to_date"}, _registry(r="daterange"), _TODAY
        )
        assert values["r"] == ["2026-01-01", "2026-10-20"]
        assert type(values["r"]) is list
        assert resolve_jinja_template("{{ r[0] }}", values) == "2026-01-01"

    def test_date_pair_and_blank_pass_through(self) -> None:
        reg = _registry(r="daterange", s="daterange")
        values = normalize_render_values(
            {"r": ["2024-01-01", None], "s": ""}, reg, _TODAY
        )
        assert values == {"r": ["2024-01-01", None], "s": ""}

    def test_unknown_preset_raises_naming_the_presets(self) -> None:
        with pytest.raises(ExecutionError, match="last_90_days"):
            normalize_render_values(
                {"r": "last_91_days"}, _registry(r="daterange"), _TODAY
            )


def test_daterange_preset_ids_maps_only_preset_values() -> None:
    reg = _registry(a="daterange", b="daterange", c="text")
    values = {"a": "last_7_days", "b": ["2024-01-01", "2024-01-31"], "c": "today"}
    assert daterange_preset_ids(values, reg) == {"a": "last_7_days"}


class TestCoerceNumber:
    @pytest.mark.parametrize(
        ("input_type", "raw", "expected"),
        [
            ("number", "5", 5),
            ("number", "5.5", 5.5),
            ("slider", "42", 42),
            ("range", "3.14", 3.14),
            ("number", 7, 7),
            ("number", 7.0, 7.0),
        ],
    )
    def test_numeric_coercion(self, input_type: str, raw: Any, expected: Any) -> None:
        reg = _registry(n=input_type)
        result = coerce_variable_values({"n": raw}, reg)["n"]
        assert result == expected
        assert type(result) is type(expected)

    def test_empty_string_is_unset(self) -> None:
        reg = _registry(n="number")
        assert coerce_variable_values({"n": ""}, reg)["n"] is None

    def test_non_numeric_raises(self) -> None:
        reg = _registry(n="number")
        with pytest.raises(ExecutionError):
            coerce_variable_values({"n": "abc"}, reg)

    @pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
    def test_non_finite_rejected(self, raw: str) -> None:
        reg = _registry(n="number")
        with pytest.raises(ExecutionError):
            coerce_variable_values({"n": raw}, reg)

    def test_bool_rejected_for_number(self) -> None:
        # bool is an int subclass; a number input must not silently accept True→1.
        reg = _registry(n="number")
        with pytest.raises(ExecutionError):
            coerce_variable_values({"n": True}, reg)


class TestCoerceBool:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("true", True), ("True", True), ("1", True), ("false", False), ("0", False)],
    )
    def test_string_bool_coercion(self, raw: str, expected: bool) -> None:
        reg = _registry(b="checkbox")
        assert coerce_variable_values({"b": raw}, reg)["b"] is expected

    def test_actual_bool_passes_through(self) -> None:
        reg = _registry(b="checkbox")
        assert coerce_variable_values({"b": True}, reg)["b"] is True
        assert coerce_variable_values({"b": False}, reg)["b"] is False

    def test_empty_string_is_unset(self) -> None:
        # Consistent with date/number: empty means unset, not False.
        reg = _registry(b="checkbox")
        assert coerce_variable_values({"b": ""}, reg)["b"] is None

    def test_non_bool_string_raises(self) -> None:
        reg = _registry(b="checkbox")
        with pytest.raises(ExecutionError):
            coerce_variable_values({"b": "maybe"}, reg)


class TestPassthrough:
    def test_text_input_unchanged(self) -> None:
        reg = _registry(t="text")
        assert coerce_variable_values({"t": "hello"}, reg)["t"] == "hello"

    def test_select_input_unchanged(self) -> None:
        reg = _registry(s="select")
        assert coerce_variable_values({"s": "2024-01-01"}, reg)["s"] == "2024-01-01"

    def test_unknown_name_passes_through(self) -> None:
        # Builtin / undeclared variables have no type to enforce.
        assert (
            coerce_variable_values({"__dir__": "2024-01-01"}, {})["__dir__"]
            == "2024-01-01"
        )


class TestTypedSqlBinding:
    """The end-to-end reason this exists: coerced values reach SQL correctly typed."""

    def test_inline_literal_emits_typed_date(self) -> None:
        from dbt_charts.core.dialects import get_dialect
        from dbt_charts.core.execute.sql_literals import inline_params

        reg = _registry(d="date")
        coerced = coerce_variable_values({"d": "2024-01-01"}, reg)
        sql = inline_params(
            "SELECT * FROM t WHERE day <= $1", [coerced["d"]], get_dialect("postgres")
        )
        assert sql == "SELECT * FROM t WHERE day <= DATE '2024-01-01'"

    def test_parameterized_binds_date_object(self) -> None:
        from dbt_charts.core.compile.template.parameterized import render_parameterized

        reg = _registry(d="date")
        coerced = coerce_variable_values({"d": "2024-01-01"}, reg)
        result = render_parameterized(
            "SELECT * FROM t WHERE day <= '{{ d }}'", variables=coerced
        )
        assert result.params == [date(2024, 1, 1)]


class TestCoerceChoice:
    """select/radio/multiselect values arrive from a URL as strings; the option
    source says what they are. Static options typed as numbers, or an explicit
    `data_type`, turn them back into the type the column has."""

    @staticmethod
    def _var(input_type: str, **fields: Any) -> Variable:
        return Variable.model_validate({"input": input_type, **fields})

    def test_static_numeric_options_type_a_select(self) -> None:
        reg = {"y": self._var("select", options={"static": [2023, 2024]})}
        assert coerce_variable_values({"y": "2024"}, reg)["y"] == 2024

    def test_static_float_options_type_a_radio(self) -> None:
        reg = {"r": self._var("radio", options={"static": [0.5, 1.5]})}
        assert coerce_variable_values({"r": "1.5"}, reg)["r"] == 1.5

    def test_static_numeric_options_type_every_multiselect_member(self) -> None:
        reg = {"m": self._var("multiselect", options={"static": [1, 2, 3]})}
        assert coerce_variable_values({"m": ["1", "3"]}, reg)["m"] == [1, 3]

    def test_static_string_options_stay_strings(self) -> None:
        reg = {"s": self._var("select", options={"static": ["2023", "2024"]})}
        assert coerce_variable_values({"s": "2024"}, reg)["s"] == "2024"

    def test_data_type_number_types_a_query_driven_select(self) -> None:
        reg = {"y": self._var("select", options={"query": "years"}, data_type="number")}
        assert coerce_variable_values({"y": "2024"}, reg)["y"] == 2024

    def test_data_type_date_types_multiselect_members(self) -> None:
        reg = {
            "d": self._var("multiselect", options={"query": "days"}, data_type="date")
        }
        assert coerce_variable_values({"d": ["2024-01-01", "2024-01-15"]}, reg)[
            "d"
        ] == [
            date(2024, 1, 1),
            date(2024, 1, 15),
        ]

    def test_query_driven_select_without_data_type_stays_a_string(self) -> None:
        reg = {"y": self._var("select", options={"query": "years"})}
        assert coerce_variable_values({"y": "2024"}, reg)["y"] == "2024"

    def test_non_numeric_value_for_a_numeric_select_raises(self) -> None:
        reg = {"y": self._var("select", options={"static": [2023, 2024]})}
        with pytest.raises(ExecutionError, match="must be numeric"):
            coerce_variable_values({"y": "twenty"}, reg)

    def test_typed_default_passes_through(self) -> None:
        reg = {"y": self._var("select", options={"static": [2023, 2024]})}
        assert coerce_variable_values({"y": 2024}, reg)["y"] == 2024

    def test_empty_choice_is_unset(self) -> None:
        reg = {"y": self._var("select", options={"static": [2023, 2024]})}
        assert coerce_variable_values({"y": ""}, reg)["y"] is None
