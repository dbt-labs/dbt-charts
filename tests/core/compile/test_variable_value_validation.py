"""Tests for variable VALUE validation in the normalizer.

Tests that variable default values are validated against their input types
at compile time, following the "validate at the boundary" pattern.
"""

import pytest

from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.normalize.dispatch import validate_variable_value


class TestValidateVariableValue:
    """Tests for the validate_variable_value function."""

    # Valid values by input type
    @pytest.mark.parametrize(
        ("input_type", "value"),
        [
            ("daterange", ["2024-01-01", "2024-12-31"]),
            ("daterange", ("2024-01-01", "2024-12-31")),
            ("daterange", [None, "2024-12-31"]),
            ("daterange", ["2024-01-01", None]),
            ("daterange", "last_90_days"),
            ("number", 42),
            ("number", 3.14),
            ("slider", 50),
            ("range", 50),
            ("checkbox", True),
            ("checkbox", False),
            ("multiselect", ["a", "b"]),
            ("multiselect", []),
            ("date", "2024-01-15"),
            ("datepicker", "2024-01-15"),
            ("text", "hello"),
            ("select", "option1"),
            ("radio", "option1"),
            ("input", 42),  # text types accept any scalar
        ],
    )
    def test_valid_values(self, input_type: str, value):
        """Test that valid values pass validation without raising and return None."""
        var = Variable(input=input_type)
        result = validate_variable_value("test", var, value)
        assert result is None

    # Invalid values with expected error substring
    @pytest.mark.parametrize(
        ("input_type", "value", "error_contains"),
        [
            ("daterange", "2024-01-01", "must be [start, end]"),
            ("daterange", "last_week", "last_<N>_<unit>"),
            ("daterange", 7, "must be [start, end]"),
            ("daterange", ["2024-01-01"], "must be [start, end]"),
            ("daterange", ["a", "b", "c"], "must be [start, end]"),
            ("daterange", [20240101, 20241231], "must be string"),
            ("number", "42", "must be a number"),
            ("number", [1, 2], "must be a number"),
            ("slider", "50", "must be a number"),
            ("range", "50", "must be a number"),
            ("checkbox", "true", "must be a boolean"),
            ("checkbox", 1, "must be a boolean"),
            ("multiselect", "option1", "must be a list"),
            # A list-shaped default on a scalar select/radio used to pass here
            # and silently stringify a Python repr at render instead — reject
            # it at the boundary that already type-checks every other input's
            # default, with a variable name to point at.
            ("select", ["a", "b"], "must be a scalar"),
            ("radio", ["a", "b"], "must be a scalar"),
            ("date", ["2024-01-01", "2024-12-31"], "must be a string"),
            ("datepicker", 20240115, "must be a string"),
            # A date string that isn't a real date fails at compile, not at query time.
            ("date", "not-a-date", "must be an ISO date"),
            ("date", "2024-13-01", "must be an ISO date"),
            ("datepicker", "01/15/2024", "must be an ISO date"),
            ("daterange", ["2024-01-01", "nope"], "must be an ISO date"),
        ],
    )
    def test_invalid_values(self, input_type: str, value, error_contains: str):
        """Test that invalid values raise CompilationError with descriptive message."""
        var = Variable(input=input_type)
        with pytest.raises(CompilationError) as exc:
            validate_variable_value("my_var", var, value)
        assert error_contains in str(exc.value)
        assert "my_var" in str(exc.value)

    def test_daterange_shape_error_carries_the_registered_code(self):
        """A mis-shaped daterange default is an authoring mistake, so it must
        carry a registered code — `dbt-charts/AGENTS.md` treats ERR-INTERNAL as a
        defect signal, not an accepted tier."""
        var = Variable(input="daterange")
        with pytest.raises(CompilationError) as exc:
            validate_variable_value("my_var", var, "2024-01-01")

        assert exc.value.code is not None
        assert exc.value.code.code == "ERR-VALIDATION-FIELD"
        # field_path mirrors onto the diagnostic — it is what places an editor
        # squiggle, and nothing else asserts it.
        assert exc.value.to_diagnostic().path == "variables.my_var.default"

    # Slider/range min/max bounds
    @pytest.mark.parametrize(
        ("value", "min_val", "max_val", "error_contains"),
        [
            (5, 10, 100, "< min"),
            (150, 0, 100, "> max"),
        ],
    )
    def test_slider_bounds(self, value, min_val, max_val, error_contains):
        """Test slider/range min/max validation."""
        var = Variable(input="slider", min=min_val, max=max_val)
        with pytest.raises(CompilationError) as exc:
            validate_variable_value("volume", var, value)
        assert error_contains in str(exc.value)

    def test_slider_within_bounds(self):
        """Test slider accepts value within bounds."""
        var = Variable(input="slider", min=0, max=100)
        validate_variable_value("test", var, 50)  # Should not raise

    # Native date objects (unquoted YAML dates) are valid date/datepicker defaults.
    @pytest.mark.parametrize("input_type", ["date", "datepicker"])
    def test_native_date_object_accepted(self, input_type: str) -> None:
        from datetime import date

        var = Variable(input=input_type)
        assert validate_variable_value("d", var, date(2024, 1, 1)) is None

    def test_daterange_native_date_objects_accepted(self) -> None:
        from datetime import date

        var = Variable(input="daterange")
        assert (
            validate_variable_value("r", var, [date(2024, 1, 1), date(2024, 12, 31)])
            is None
        )

    # A datetime (unquoted YAML `2024-01-01 10:00:00`) carries a time and must be
    # rejected — a date variable is date-only, else _to_sql_literal emits TIMESTAMP.
    @pytest.mark.parametrize("input_type", ["date", "datepicker"])
    def test_datetime_default_rejected(self, input_type: str) -> None:
        from datetime import datetime

        var = Variable(input=input_type)
        with pytest.raises(CompilationError) as exc:
            validate_variable_value("d", var, datetime(2024, 1, 1, 10, 0))
        assert "not a datetime" in str(exc.value)

    def test_daterange_datetime_endpoint_rejected(self) -> None:
        from datetime import date, datetime

        var = Variable(input="daterange")
        with pytest.raises(CompilationError) as exc:
            validate_variable_value(
                "r", var, [datetime(2024, 1, 1, 9), date(2024, 2, 1)]
            )
        assert "not a datetime" in str(exc.value)

    # None is always valid
    @pytest.mark.parametrize(
        "input_type",
        ["daterange", "number", "slider", "checkbox", "multiselect", "date", "text"],
    )
    def test_none_always_valid(self, input_type: str):
        """Test None is valid for all types (represents 'no default')."""
        var = Variable(input=input_type)
        validate_variable_value("test", var, None)  # Should not raise


class TestVariableValueValidationInCompile:
    """Tests that validation is applied during compile()."""

    @pytest.mark.parametrize(
        ("input_type", "bad_default", "error_contains"),
        [
            ("daterange", '"2024-01-01"', "must be [start, end]"),
            ("number", '"42"', "must be a number"),
            ("checkbox", '"yes"', "must be a boolean"),
        ],
    )
    def test_compile_rejects_invalid_defaults(
        self, input_type: str, bad_default: str, error_contains: str
    ):
        """Test compile() catches invalid variable defaults."""
        yaml_content = f"""
title: Test
variables:
  test_var:
    input: {input_type}
    default: {bad_default}
queries:
  test:
    type: values
    rows:
      - {{col: val}}
charts:
  chart1:
    query: test
    type: table
rows:
  - chart1
"""
        result = compile(yaml_content)
        assert not result.success
        assert any(error_contains in str(e) for e in result.errors)

    def test_compile_accepts_valid_daterange(self):
        """Test compile() accepts valid daterange default."""
        yaml_content = """
title: Test
variables:
  date_range:
    input: daterange
    default: ["2024-01-01", "2024-12-31"]
queries:
  test:
    type: values
    rows:
      - {col: val}
charts:
  chart1:
    query: test
    type: table
rows:
  - chart1
"""
        result = compile(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        assert result.board.variable_defaults["date_range"] == [
            "2024-01-01",
            "2024-12-31",
        ]

    def test_compile_validates_slider_bounds(self):
        """Test compile() validates slider min/max."""
        yaml_content = """
title: Test
variables:
  volume:
    input: slider
    min: 0
    max: 100
    default: 150
queries:
  test:
    type: values
    rows:
      - {col: val}
charts:
  chart1:
    query: test
    type: table
rows:
  - chart1
"""
        result = compile(yaml_content)
        assert not result.success
        assert any("> max" in str(e) for e in result.errors)

    def test_compile_validates_nested_board_variables(self):
        """Test validation applies to variables in nested boards."""
        yaml_content = """
title: Test
rows:
  - title: Nested Section
    variables:
      bad_daterange:
        input: daterange
        default: "not-a-list"
    text: "Some text"
"""
        result = compile(yaml_content)
        assert not result.success
        assert any("must be [start, end]" in str(e) for e in result.errors)


@pytest.mark.parametrize("bound", ["min", "max", "step"])
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_bound_is_rejected(bound: str, value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        Variable(input="number", **{bound: value})


def test_an_integer_bound_past_the_double_range_is_still_a_bound() -> None:
    assert Variable(input="number", max=10**400).max == 10**400
