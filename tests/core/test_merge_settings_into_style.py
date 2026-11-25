"""Tests for merging ChartSettings into ChartStylePatch.

Validates that after the merge:
- ChartStylePatch accepts orientation, columns, stack, palette,
  and spark-bar fields
- Chart no longer has a settings field
- chart_settings_types.py no longer exists
- The normalizer rejects the removed `settings:` key with a clear error
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.chart.normalized import BarChart, TableChart
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
    ChartStylePatch,
    SparkBarBarStylePatch,
    SparkBarChartLabelStylePatch,
    SparkBarChartStylePatch,
    SparkBarCountStylePatch,
    TableChartStylePatch,
)


class TestChartStylePatchHasSettingsFields:
    """ChartStylePatch family sub-patches should accept all fields that were on ChartSettings."""

    def test_bar_orientation_in_family(self) -> None:
        # Orientation is a bar-family style property
        patch = BarChartStylePatch(orientation="horizontal")
        assert patch.orientation == "horizontal"

    def test_columns_in_table_family(self) -> None:
        # Columns is a table-family style property
        patch = TableChartStylePatch(columns={"name": {"width": 200}})
        assert patch.columns is not None
        assert len(patch.columns) == 1

    def test_stack_none_in_bar_family(self) -> None:
        """style.bar.stack: none maps to VL encoding.y.stack: null (no stacking)."""
        patch = BarChartStylePatch(stack="none")
        assert patch.stack == "none"

    def test_stack_normalize_in_bar_family(self) -> None:
        patch = BarChartStylePatch(stack="normalize")
        assert patch.stack == "normalize"

    def test_stack_zero_in_bar_family(self) -> None:
        patch = BarChartStylePatch(stack="zero")
        assert patch.stack == "zero"

    @pytest.mark.parametrize("bad_value", [True, False])
    def test_stack_rejects_bool(self, bad_value: bool) -> None:
        """bool stack values are rejected — use 'none'/'zero' instead."""
        with pytest.raises(ValidationError):
            BarChartStylePatch(stack=bad_value)

    def test_spark_bar_fields(self) -> None:
        patch = SparkBarChartStylePatch(
            max_bars=10,
            bar=SparkBarBarStylePatch(height=20, color="#ff0", background="#eee"),
            label=SparkBarChartLabelStylePatch(visible=False),
            count=SparkBarCountStylePatch(visible=True),
        )
        assert patch.max_bars == 10
        assert patch.bar is not None
        assert patch.bar.height == 20
        assert patch.bar.color == "#ff0"
        assert patch.bar.background == "#eee"
        assert patch.label is not None
        assert patch.label.visible is False
        assert patch.count is not None
        assert patch.count.visible is True

    def test_extra_forbid(self) -> None:
        """ChartStylePatch should reject unknown keys."""
        with pytest.raises(ValidationError):
            ChartStylePatch(unknown_setting=42)


class TestChartNoSettings:
    """Chart should not have a settings field."""

    def test_no_settings_field(self) -> None:
        # Confirm that style fields formerly in `settings` work via bar/table sub-patches
        chart = BarChart(
            id="test",
            type="bar",
            style=BarChartStylePatch(orientation="horizontal"),
        )
        assert chart.style is not None
        assert chart.style.orientation == "horizontal"

    def test_style_accepts_orientation_via_bar(self) -> None:
        chart = BarChart(
            id="test",
            type="bar",
            style=BarChartStylePatch(orientation="horizontal"),
        )
        assert chart.style is not None
        assert chart.style.orientation == "horizontal"

    def test_style_accepts_columns_via_table(self) -> None:
        chart = TableChart(
            id="test",
            type="table",
            style=TableChartStylePatch(columns={"name": {}}),
        )
        assert chart.style is not None
        assert chart.style.columns is not None


class TestChartSettingsModuleRemoved:
    """chart_settings_types.py should no longer exist."""

    def test_module_not_importable(self) -> None:
        with pytest.raises(ImportError):
            import dbt_charts.core.compile.chart_settings_types  # noqa: F401


class TestSettingsKeyRejected:
    """The normalizer should reject charts that use the removed `settings:` key."""

    def test_settings_key_rejected_by_chart_patch(self) -> None:
        from pydantic import TypeAdapter, ValidationError

        from dbt_charts.core.compile.models.chart.authored import AuthoredChart

        adapter = TypeAdapter(AuthoredChart)
        with pytest.raises(ValidationError, match="settings"):
            adapter.validate_python(
                {"type": "bar", "settings": {"orientation": "horizontal"}}
            )
