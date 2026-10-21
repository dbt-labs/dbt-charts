"""Tests for data-aware variable input type refinement.

After options/column queries execute, the system inspects query results to
refine the compile-time input type guess. These tests verify each data-shape
→ input-type mapping.
"""

from dbt_charts.core.compile.models.variable.authored import (
    Variable,
    VariableOptions,
)
from dbt_charts.core.compile.normalize.variable_input_refinement import (
    refine_input_type_from_data,
)


class TestRefineSkipsExplicitInput:
    """Data-aware refinement must not override explicitly authored input types."""

    def test_explicit_select_not_refined(self):
        var = Variable(input="select", options=VariableOptions(static=["A", "B"]))
        result = refine_input_type_from_data(var, ["A", "B"])
        assert result.input_type == "select"

    def test_explicit_text_not_refined(self):
        var = Variable(input="text")
        result = refine_input_type_from_data(var, ["2024-01-01", "2024-02-01"])
        assert result.input_type == "text"

    def test_explicit_slider_not_refined(self):
        var = Variable(input="slider", min=0, max=100)
        result = refine_input_type_from_data(var, ["1", "2", "3"])
        assert result.input_type == "slider"


class TestCardinalityDetection:
    """Cardinality-based: all non-type-detected options stay as select."""

    def test_low_cardinality_stays_select(self):
        var = Variable(input="select", input_auto_detected=True)
        options = [f"val_{i}" for i in range(10)]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "select"

    def test_at_threshold_stays_select(self):
        var = Variable(input="select", input_auto_detected=True)
        options = [f"val_{i}" for i in range(20)]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "select"

    def test_medium_cardinality_stays_select(self):
        var = Variable(input="select", input_auto_detected=True)
        options = [f"val_{i}" for i in range(100)]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "select"

    def test_high_cardinality_stays_select(self):
        """High cardinality stays select until server-side search is built."""
        var = Variable(input="select", input_auto_detected=True)
        options = [f"val_{i}" for i in range(250)]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "select"


class TestDateDetection:
    """Date/timestamp columns should become datepickers."""

    def test_iso_dates_become_datepicker(self):
        var = Variable(input="select", input_auto_detected=True)
        options = ["2024-01-01", "2024-02-01", "2024-03-01"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "datepicker"

    def test_datetime_strings_become_datepicker(self):
        var = Variable(input="select", input_auto_detected=True)
        options = ["2024-01-01 10:00:00", "2024-02-15 14:30:00"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "datepicker"

    def test_iso_datetime_with_t_separator_becomes_datepicker(self):
        var = Variable(input="select", input_auto_detected=True)
        options = ["2024-01-01T10:00:00", "2024-02-15T14:30:00"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "datepicker"

    def test_mixed_dates_and_strings_stay_select(self):
        """If not mostly dates, don't switch to datepicker."""
        var = Variable(input="select", input_auto_detected=True)
        options = ["2024-01-01", "hello", "world", "foo", "bar"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "select"

    def test_empty_options_no_refinement(self):
        var = Variable(input="select", input_auto_detected=True)
        result = refine_input_type_from_data(var, [])
        assert result.input_type == "select"

    def test_slug_like_strings_not_detected_as_dates(self):
        """Strings like '2024-03-15-beta' should not trigger date detection."""
        var = Variable(input="select", input_auto_detected=True)
        options = ["2024-03-15-beta", "2024-04-01-release"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "select"

    def test_invalid_month_day_not_detected_as_dates(self):
        var = Variable(input="select", input_auto_detected=True)
        options = ["9999-99-99", "0000-00-00"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "select"

    def test_partial_dates_not_detected(self):
        """Month-only strings like '2024-01' should not trigger date detection."""
        var = Variable(input="select", input_auto_detected=True)
        options = ["2024-01", "2024-02", "2024-03"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "select"


class TestNumericSliderDetection:
    """Numeric columns with tight ranges should become sliders with auto bounds."""

    def test_integer_range_becomes_slider(self):
        var = Variable(input="select", input_auto_detected=True)
        options = ["1", "2", "3", "4", "5"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "slider"
        assert result.slider_min == 1
        assert result.slider_max == 5
        assert result.slider_step == 1

    def test_query_backed_numeric_options_stay_an_unsettable_select(self):
        """A column-backed range bound must be able to be "All": a slider cannot."""
        var = Variable(
            input="select",
            input_auto_detected=True,
            options=VariableOptions(query="_var_options_first_year"),
        )
        result = refine_input_type_from_data(var, ["1970", "1971", "1972"])
        assert result.input_type == "select"

    def test_float_range_becomes_slider(self):
        var = Variable(input="select", input_auto_detected=True)
        options = ["0.5", "1.0", "1.5", "2.0"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "slider"
        assert result.slider_min == 0.5
        assert result.slider_max == 2.0

    def test_slider_does_not_mutate_var_def(self):
        """Refinement must not mutate the compile-time Variable object."""
        var = Variable(input="select", input_auto_detected=True)
        refine_input_type_from_data(var, ["1", "2", "3"])
        assert var.min is None
        assert var.max is None
        assert var.step is None

    def test_wide_numeric_range_stays_select(self):
        """Very wide ranges aren't useful as sliders — fall through to cardinality."""
        var = Variable(input="select", input_auto_detected=True)
        options = [str(i) for i in range(0, 100001, 1000)]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "select"

    def test_nan_inf_not_treated_as_numbers(self):
        var = Variable(input="select", input_auto_detected=True)
        result = refine_input_type_from_data(var, ["1", "nan", "3"])
        assert result.input_type == "select"

    def test_inf_not_treated_as_number(self):
        var = Variable(input="select", input_auto_detected=True)
        result = refine_input_type_from_data(var, ["1", "inf", "3"])
        assert result.input_type == "select"

    def test_numeric_with_non_numeric_mixed_stays_select(self):
        var = Variable(input="select", input_auto_detected=True)
        options = ["1", "2", "three", "4"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "select"


class TestBooleanDetection:
    """Boolean-ish columns should become checkboxes."""

    def test_true_false_becomes_checkbox(self):
        var = Variable(input="select", input_auto_detected=True)
        options = ["true", "false"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "checkbox"

    def test_yes_no_becomes_checkbox(self):
        var = Variable(input="select", input_auto_detected=True)
        options = ["yes", "no"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "checkbox"

    def test_zero_one_becomes_checkbox(self):
        var = Variable(input="select", input_auto_detected=True)
        options = ["0", "1"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "checkbox"

    def test_three_values_not_checkbox(self):
        """More than 2 values can't be boolean."""
        var = Variable(input="select", input_auto_detected=True)
        options = ["true", "false", "maybe"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "select"


class TestEdgeCases:
    """Edge cases and single-value scenarios."""

    def test_single_value_stays_select(self):
        var = Variable(input="select", input_auto_detected=True)
        result = refine_input_type_from_data(var, ["only_one"])
        assert result.input_type == "select"

    def test_identical_numeric_values_stay_select(self):
        """All same number → range is 0 → not a useful slider."""
        var = Variable(input="select", input_auto_detected=True)
        result = refine_input_type_from_data(var, ["5", "5", "5"])
        assert result.input_type == "select"

    def test_non_date_non_boolean_returns_no_slider_bounds(self):
        var = Variable(input="select", input_auto_detected=True)
        result = refine_input_type_from_data(var, ["A", "B", "C"])
        assert result.slider_min is None
        assert result.slider_max is None
        assert result.slider_step is None


class TestTypePriority:
    """Type detection has priority: date > boolean > numeric > select."""

    def test_two_numeric_dates_prefer_date(self):
        """Values that look like both dates and numbers: date wins."""
        var = Variable(input="select", input_auto_detected=True)
        options = ["2024-01-01", "2024-06-15"]
        result = refine_input_type_from_data(var, options)
        assert result.input_type == "datepicker"
