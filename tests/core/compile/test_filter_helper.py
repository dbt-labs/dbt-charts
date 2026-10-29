"""Tests for the filter() Jinja macro helper — the parameterized path
(the make_filter_helper closure in parameterized.py, over a collector emitter).

The legacy unparameterized _filter_helper from jinja.py raises CompilationError
on any call — it uses string interpolation and is no longer supported.
"""

from datetime import date, datetime

import pytest

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.template.daterange import BoardClock, board_clock
from dbt_charts.core.compile.template.jinja import (
    _filter_date_range_helper,
    _filter_helper,
)
from dbt_charts.core.compile.template.parameterized import (
    _NullValue,
    _ParameterCollector,
    _ParameterizedValue,
    make_filter_date_range_helper,
    make_filter_helper,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.dialects.postgres import PostgresDialect

# ---------------------------------------------------------------------------
# Helpers for the parameterized path
# ---------------------------------------------------------------------------


def _collector() -> _ParameterCollector:
    return _ParameterCollector(variables={}, dialect=PostgresDialect())


def _make_helper(collector: _ParameterCollector | None = None):
    if collector is None:
        collector = _collector()
    return make_filter_helper(collector.add_param, get_dialect("postgres"))


# ===========================================================================
# _filter_helper (jinja.py) — legacy path now raises
# ===========================================================================


class TestFilterHelperJinja:
    def test_raises_compilation_error(self):
        """The legacy string-interpolation filter() helper must raise on any call."""
        with pytest.raises(CompilationError, match="filter.*no longer supported"):
            _filter_helper("col", "value")

    def test_date_range_raises_compilation_error(self):
        """The legacy string-interpolation filter_date_range() must raise on any call."""
        with pytest.raises(
            CompilationError, match="filter_date_range.*no longer supported"
        ):
            _filter_date_range_helper("col", ["2025-01-01", "2025-03-31"])


# ===========================================================================
# parameterized filter helper (parameterized.py)
# ===========================================================================


class TestFilterHelperParameterized:
    # --- Backwards-compat ---

    def test_none_gives_allow_by_default(self):
        fn = _make_helper()
        assert fn("col", None) == "1=1"

    def test_empty_string_gives_allow_by_default(self):
        fn = _make_helper()
        assert fn("col", "") == "1=1"

    def test_empty_list_gives_allow_by_default(self):
        fn = _make_helper()
        assert fn("col", []) == "1=1"

    def test_null_value_sentinel_gives_allow_by_default(self):
        fn = _make_helper()
        assert fn("col", _NullValue()) == "1=1"

    def test_string_value_returns_predicate(self):
        fn = _make_helper()
        result = fn("col", "x")
        assert result == "col = $1"

    def test_falsy_integer_zero_passes_through(self):
        fn = _make_helper()
        result = fn("col", 0)
        assert result == "col = $1"

    # --- none='deny' new behavior ---

    def test_none_with_deny_gives_deny(self):
        fn = _make_helper()
        assert fn("col", None, none="deny") == "1=0"

    def test_empty_string_with_deny_gives_deny(self):
        fn = _make_helper()
        assert fn("col", "", none="deny") == "1=0"

    def test_empty_list_with_deny_gives_deny(self):
        fn = _make_helper()
        assert fn("col", [], none="deny") == "1=0"

    def test_null_value_sentinel_with_deny_gives_deny(self):
        fn = _make_helper()
        assert fn("col", _NullValue(), none="deny") == "1=0"

    def test_string_value_with_deny_returns_predicate(self):
        fn = _make_helper()
        result = fn("col", "x", none="deny")
        assert result == "col = $1"

    def test_falsy_integer_zero_with_deny_passes_through(self):
        fn = _make_helper()
        result = fn("col", 0, none="deny")
        assert result == "col = $1"

    def test_explicit_allow_same_as_default(self):
        fn = _make_helper()
        assert fn("col", None, none="allow") == "1=1"

    # --- Operator + deny combo ---

    def test_operator_and_deny_on_null_sentinel_gives_deny(self):
        fn = _make_helper()
        assert fn("col", _NullValue(), "!=", none="deny") == "1=0"

    def test_operator_and_deny_on_value_returns_predicate(self):
        fn = _make_helper()
        result = fn("col", "x", "!=", none="deny")
        assert result == "col != $1"

    # --- Kwarg validation ---

    def test_bogus_none_kwarg_raises_value_error(self):
        fn = _make_helper()
        with pytest.raises(ValueError, match="none"):
            fn("col", None, none="bogus")

    # --- _validate_operator non-string regression ---

    def test_list_operator_raises_value_error_not_attribute_error(self):
        """A non-string operator must raise ValueError naming the allowlist, not AttributeError."""
        fn = _make_helper()
        with pytest.raises(ValueError, match="operator"):
            fn("col", "value", ["="])

    def test_int_operator_raises_value_error_not_attribute_error(self):
        """An integer operator must raise ValueError naming the allowlist, not AttributeError."""
        fn = _make_helper()
        with pytest.raises(ValueError, match="operator"):
            fn("col", "value", 1)


# ===========================================================================
# date values and list operators
# ===========================================================================


class TestFilterHelperDatesAndLists:
    """What `filter()` emits for the value shapes the cross-dialect matrix
    (tests/core/test_variable_sql_cross_dialect.py) relies on."""

    def test_date_value_compares_the_column_as_a_date(self) -> None:
        assert _make_helper()("ts", date(2024, 1, 15), ">=") == "CAST(ts AS DATE) >= $1"

    def test_date_list_compares_the_column_as_a_date(self) -> None:
        helper = _make_helper()
        sql = helper("ts", [date(2024, 1, 1), date(2024, 1, 15)])
        assert sql == "CAST(ts AS DATE) IN ($1, $2)"

    def test_datetime_value_leaves_the_column_alone(self) -> None:
        """A datetime carries a time of day; truncating the column would
        discard the comparison the author asked for."""
        assert _make_helper()("ts", datetime(2024, 1, 15, 13, 0), ">=") == "ts >= $1"

    def test_wrapped_list_items_are_unwrapped(self) -> None:
        """A list built in the template from two variables holds
        `_ParameterizedValue` wrappers; the values, not the wrappers, bind."""
        collector = _collector()
        a = _ParameterizedValue("a", date(2024, 1, 1), collector)
        b = _ParameterizedValue("b", date(2024, 1, 15), collector)
        sql = _make_helper(collector)("ts", [a, b])
        assert sql == "CAST(ts AS DATE) IN ($1, $2)"
        assert collector.params == [date(2024, 1, 1), date(2024, 1, 15)]

    def test_mixed_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="mixes dates"):
            _make_helper()("ts", [date(2024, 1, 1), "2024-01-15"])

    @pytest.mark.parametrize("operator", ["=", "IN", "in", " In "])
    def test_membership_operator_is_in(self, operator: str) -> None:
        assert _make_helper()("s", ["a", "b"], operator) == "s IN ($1, $2)"

    @pytest.mark.parametrize("operator", ["!=", "<>", "NOT IN", "not in"])
    def test_inequality_operator_is_not_in(self, operator: str) -> None:
        assert _make_helper()("s", ["a", "b"], operator) == "s NOT IN ($1, $2)"

    @pytest.mark.parametrize("value", [[1, 2], []])
    def test_ordering_operator_on_a_list_is_refused(self, value: list[int]) -> None:
        """A list only has membership semantics — and the refusal cannot
        depend on whether the viewer happened to select anything."""
        with pytest.raises(ValueError, match="cannot apply to a list"):
            _make_helper()("n", value, ">=")

    def test_null_in_a_list_is_refused(self) -> None:
        """IN never matches NULL and NOT IN would match nothing — neither is
        what an author meant by putting NULL in a selection."""
        with pytest.raises(ValueError, match="cannot contain NULL"):
            _make_helper()("s", ["a", None])


class TestFilterDateRangeBounds:
    def test_datetime_bounds_leave_the_column_alone(self) -> None:
        """Bounds with a time of day are the author asking for a precise
        window; truncating the column would widen it to whole days."""
        collector = _collector()
        helper = make_filter_date_range_helper(
            collector.add_param, get_dialect("postgres")
        )
        sql = helper("ts", [datetime(2024, 1, 1, 9, 0), datetime(2024, 1, 1, 17, 0)])
        assert sql == "ts BETWEEN $1 AND $2"

    def test_a_half_datetime_range_is_refused(self) -> None:
        collector = _collector()
        helper = make_filter_date_range_helper(
            collector.add_param, get_dialect("postgres")
        )
        with pytest.raises(ValueError, match="mixes a date with a datetime"):
            helper("ts", [date(2024, 1, 1), datetime(2024, 1, 1, 17, 0)])


class TestFilterDateRangePresetName:
    def _helper(self, collector: _ParameterCollector):
        return make_filter_date_range_helper(
            collector.add_param, get_dialect("postgres")
        )

    def test_a_preset_name_resolves_on_the_board_clock(self) -> None:
        collector = _collector()
        with board_clock(
            BoardClock(date(2026, 10, 20), fiscal_start_month=7, week_start=1)
        ):
            sql = self._helper(collector)("order_ts", "this_fiscal_quarter")
        assert sql == "CAST(order_ts AS DATE) BETWEEN $1 AND $2"
        assert collector.params == [date(2026, 10, 1), date(2026, 12, 31)]

    def test_a_preset_name_outside_a_board_clock_is_refused(self) -> None:
        with pytest.raises(ExecutionError, match="board_clock"):
            self._helper(_collector())("d", "last_7_days")

    def test_a_name_outside_the_grammar_states_the_grammar(self) -> None:
        with (
            board_clock(
                BoardClock(date(2026, 10, 20), fiscal_start_month=1, week_start=1)
            ),
            pytest.raises(ValueError, match=r"last_<N>_<unit>.*got 'last_week'"),
        ):
            self._helper(_collector())("d", "last_week")
