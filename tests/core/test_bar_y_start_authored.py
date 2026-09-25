"""TDD tests for authored-model `y_start` validation rules on bar charts/layers.

Covers the authored-stage rules that need no query data:
- y_start rejected as an extra field on non-bar chart types and non-bar layers
- y_start rejected on type: histogram (bars count from zero)
- y_start incompatible with style.stack in zero/normalize/center
- y_start requires a single-column (non-list) y
- y_start present but blank (None or whitespace string) is rejected
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.chart.authored import AuthoredChart

_chart_adapter = TypeAdapter(AuthoredChart)

_BAR_BARE = {
    "type": "bar",
    "x": "category",
    "y": "value",
    "query": "q",
}


def _patch(**overrides):
    data = {**_BAR_BARE, **overrides}
    return _chart_adapter.validate_python(data)


def _compile_yaml(yaml_content: str):
    from dbt_charts.core.compile import compile as df_compile

    return df_compile(yaml_content)


# ---------------------------------------------------------------------------
# 1. y_start rejected on non-bar chart types
# ---------------------------------------------------------------------------


class TestYStartRejectedOnNonBar:
    def test_y_start_on_line_raises(self):
        with pytest.raises(ValidationError, match="y_start"):
            _chart_adapter.validate_python(
                {
                    "type": "line",
                    "x": "date",
                    "y": "value",
                    "query": "q",
                    "y_start": "low",
                }
            )

    def test_y_start_on_area_raises(self):
        with pytest.raises(ValidationError, match="y_start"):
            _chart_adapter.validate_python(
                {
                    "type": "area",
                    "x": "date",
                    "y": "value",
                    "query": "q",
                    "y_start": "low",
                }
            )

    def test_y_start_on_scatter_raises(self):
        with pytest.raises(ValidationError, match="y_start"):
            _chart_adapter.validate_python(
                {
                    "type": "scatter",
                    "x": "date",
                    "y": "value",
                    "query": "q",
                    "y_start": "low",
                }
            )

    def test_y_start_on_line_surfaces_err_extra_field(self):
        result = _compile_yaml(
            """
queries:
  q:
    columns: [date, value, low]
    values: [[1, 10, 5]]
charts:
  c:
    query: q
    type: line
    x: date
    y: value
    y_start: low
rows:
  - c
"""
        )
        assert not result.success
        assert any(e.code == "ERR-EXTRA-FIELD" for e in result.errors), result.errors


# ---------------------------------------------------------------------------
# 2. y_start rejected on type: histogram
# ---------------------------------------------------------------------------


class TestYStartRejectedOnHistogram:
    def test_y_start_on_histogram_raises(self):
        with pytest.raises(ValidationError, match="histogram"):
            _chart_adapter.validate_python(
                {"type": "histogram", "x": "value", "query": "q", "y_start": "low"}
            )


# ---------------------------------------------------------------------------
# 3. y_start incompatible with style.stack
# ---------------------------------------------------------------------------


class TestYStartRejectedWithStack:
    @pytest.mark.parametrize("stack_mode", ["zero", "normalize", "center"])
    def test_y_start_with_stack_raises(self, stack_mode):
        with pytest.raises(ValidationError, match="stacked"):
            _patch(y_start="low", style={"stack": stack_mode})

    def test_y_start_with_stack_none_is_accepted(self):
        _patch(y_start="low", style={"stack": "none"})  # must not raise

    def test_y_start_without_style_is_accepted(self):
        _patch(y_start="low")  # must not raise


# ---------------------------------------------------------------------------
# 4. y_start requires a single-column y
# ---------------------------------------------------------------------------


class TestYStartRejectedWithListY:
    def test_y_start_with_list_y_raises(self):
        with pytest.raises(ValidationError, match="y_start"):
            _chart_adapter.validate_python(
                {
                    "type": "bar",
                    "x": "category",
                    "y": ["value_a", "value_b"],
                    "query": "q",
                    "y_start": "low",
                }
            )


# ---------------------------------------------------------------------------
# 5. y_start rejected on non-bar layers
# ---------------------------------------------------------------------------


class TestYStartRejectedOnNonBarLayer:
    def test_y_start_on_line_layer_raises(self):
        with pytest.raises(ValidationError, match="y_start"):
            _patch(layers=[{"type": "line", "y": "value", "y_start": "low"}])

    def test_y_start_on_area_layer_raises(self):
        with pytest.raises(ValidationError, match="y_start"):
            _patch(layers=[{"type": "area", "y": "value", "y_start": "low"}])

    def test_y_start_on_scatter_layer_raises(self):
        with pytest.raises(ValidationError, match="y_start"):
            _patch(layers=[{"type": "scatter", "y": "value", "y_start": "low"}])

    def test_y_start_on_bar_layer_is_accepted(self):
        _patch(
            layers=[{"type": "bar", "y": "close", "y_start": "open"}]
        )  # must not raise


# ---------------------------------------------------------------------------
# 6. y_start present but blank
# ---------------------------------------------------------------------------


class TestYStartBlankRejected:
    def test_y_start_null_on_chart_raises(self):
        with pytest.raises(ValidationError, match="names no column"):
            _chart_adapter.validate_python({**_BAR_BARE, "y_start": None})

    def test_y_start_empty_string_on_chart_raises(self):
        with pytest.raises(ValidationError, match="names no column"):
            _patch(y_start="")

    def test_y_start_whitespace_string_on_chart_raises(self):
        with pytest.raises(ValidationError, match="names no column"):
            _patch(y_start="   ")

    def test_y_start_absent_on_chart_is_accepted(self):
        _patch()  # no y_start key at all — must not raise

    def test_y_start_null_on_bar_layer_raises(self):
        with pytest.raises(ValidationError, match="names no column"):
            _patch(layers=[{"type": "bar", "y": "close", "y_start": None}])

    def test_y_start_blank_string_on_bar_layer_raises(self):
        with pytest.raises(ValidationError, match="names no column"):
            _patch(layers=[{"type": "bar", "y": "close", "y_start": "  "}])


def test_a_bar_layer_start_is_refused_on_a_non_bar_chart():
    from pydantic import TypeAdapter, ValidationError

    from dbt_charts.core.compile.models.chart.authored import AuthoredChart

    with pytest.raises(ValidationError, match="y_start"):
        TypeAdapter(AuthoredChart).validate_python(
            {
                "type": "line",
                "x": "d",
                "y": "v",
                "layers": [{"type": "bar", "y": "hi", "y_start": "lo"}],
            }
        )
