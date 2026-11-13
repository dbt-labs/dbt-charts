"""Tests for table pagination configuration.

Proves:
1. PaginationConfig model validates correctly (full form and shorthand).
2. ChartStylePatch accepts pagination field.
3. Pagination merges through the style system.
4. Extra fields on PaginationConfig are rejected.
5. Config-loaded defaults provide enabled=True, page_rows=20.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import BarChart, TableChart


class TestPaginationConfig:
    """Tests for the PaginationConfig model."""

    def test_pagination_config_model_defaults(self) -> None:
        """PaginationConfig model defaults to enabled=True."""
        from dbt_charts.core.compile.models.style.authored import PaginationConfig

        config = PaginationConfig()
        assert config.enabled is True

    def test_pagination_config_with_page_rows(self) -> None:
        """PaginationConfig accepts page_rows."""
        from dbt_charts.core.compile.models.style.authored import PaginationConfig

        config = PaginationConfig(page_rows=25)
        assert config.enabled is True
        assert config.page_rows == 25

    def test_pagination_config_disabled(self) -> None:
        """PaginationConfig can be explicitly disabled."""
        from dbt_charts.core.compile.models.style.authored import PaginationConfig

        config = PaginationConfig(enabled=False)
        assert config.enabled is False

    def test_rows_scroll_by_default(self) -> None:
        from dbt_charts.core.compile.models.style.authored import PaginationConfig

        assert PaginationConfig().mode == "scroll"
        assert get_theme_style().charts.table.pagination.mode == "scroll"

    def test_pagination_config_rejects_extra_fields(self) -> None:
        """PaginationConfig forbids unknown fields."""
        from dbt_charts.core.compile.models.style.authored import PaginationConfig

        with pytest.raises(ValidationError):
            PaginationConfig(bogus="nope")  # type: ignore[call-arg]

    def test_pagination_config_page_rows_must_be_positive(self) -> None:
        """PaginationConfig rejects zero or negative page_rows."""
        from dbt_charts.core.compile.models.style.authored import PaginationConfig

        with pytest.raises(ValidationError):
            PaginationConfig(page_rows=0)
        with pytest.raises(ValidationError):
            PaginationConfig(page_rows=-5)


class TestPaginationInChartStylePatch:
    """Tests that pagination integrates into ChartStylePatch."""

    def test_chart_style_patch_accepts_pagination(self) -> None:
        """ChartStylePatch validates pagination as a nested object."""
        from dbt_charts.core.compile.models.style.authored import ChartStylePatch

        patch = ChartStylePatch.model_validate(
            {"table": {"pagination": {"enabled": True, "page_rows": 25}}}
        )
        assert patch.table is not None
        assert patch.table.pagination is not None
        assert patch.table.pagination.page_rows == 25

    def test_chart_style_patch_pagination_default_none(self) -> None:
        """ChartStylePatch.table defaults to None (patch semantics)."""
        from dbt_charts.core.compile.models.style.authored import ChartStylePatch

        patch = ChartStylePatch()
        assert patch.table is None

    def test_chart_style_patch_pagination_shorthand_bool_true(self) -> None:
        """ChartStylePatch accepts pagination: true (enable, page_rows from config)."""
        from dbt_charts.core.compile.models.style.authored import ChartStylePatch

        patch = ChartStylePatch.model_validate(
            {"table": {"pagination": {"enabled": True}}}
        )
        assert patch.table is not None
        assert patch.table.pagination is not None
        assert patch.table.pagination.enabled is True
        assert patch.table.pagination.page_rows is None

    def test_chart_style_patch_pagination_shorthand_bool_false(self) -> None:
        """ChartStylePatch accepts pagination: false (explicitly disabled)."""
        from dbt_charts.core.compile.models.style.authored import ChartStylePatch

        patch = ChartStylePatch.model_validate(
            {"table": {"pagination": {"enabled": False}}}
        )
        assert patch.table is not None
        assert patch.table.pagination is not None
        assert patch.table.pagination.enabled is False


class TestPaginationInStyle:
    """Tests pagination defaults in Style (resolved table config)."""

    def test_compiled_table_style_has_pagination_defaults(self) -> None:
        """TableChartStyle.pagination is a PaginationConfig with enabled=True and a positive page_rows."""
        style = get_theme_style()
        assert style.charts.table.pagination.enabled is True
        assert style.charts.table.pagination.page_rows is not None
        assert style.charts.table.pagination.page_rows > 0


class TestPaginationCascadeMerge:
    """Pagination resolution flows through build_chart_style_context — chart-local
    style.pagination wins over the board default field-by-field, with page_rows
    inheriting from the board-level value when the chart didn't author one."""

    def test_chart_pagination_disabled_returns_no_page_rows(self) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (
            build_chart_style_context,
        )

        merged = build_chart_style_context(
            resolve_chart_style_context(get_theme_style()),
            TableChart(
                id="t",
                type="table",
                style=TableChartStylePatch(pagination={"enabled": False}),
            ),
        )
        assert merged.pagination is not None
        assert merged.pagination.enabled is False

    def test_chart_page_rows_overrides_board_default(self) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (
            build_chart_style_context,
        )

        merged = build_chart_style_context(
            resolve_chart_style_context(get_theme_style()),
            TableChart(
                id="t",
                type="table",
                style=TableChartStylePatch(
                    pagination={"enabled": True, "page_rows": 5}
                ),
            ),
        )
        assert merged.pagination is not None
        assert merged.pagination.page_rows == 5

    def test_enabled_chart_without_page_rows_inherits_board_default(self) -> None:
        from dbt_charts.core.compile.models.style.authored import (
            TableChartStylePatch,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (
            build_chart_style_context,
        )

        merged = build_chart_style_context(
            resolve_chart_style_context(get_theme_style()),
            TableChart(
                id="t",
                type="table",
                style=TableChartStylePatch(pagination={"enabled": True}),
            ),
        )
        assert merged.pagination is not None
        # Board default page_rows is inherited (non-None, positive)
        assert merged.pagination.page_rows is not None

    def test_no_chart_pagination_returns_board_default(self) -> None:
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
        from dbt_charts.core.compile.resolve.style.chart_context import (
            build_chart_style_context,
        )

        merged = build_chart_style_context(
            resolve_chart_style_context(get_theme_style()),
            BarChart(id="t", type="bar"),
        )
        assert merged.pagination is not None
        assert merged.pagination.enabled is True
        assert merged.pagination.page_rows is not None
        assert merged.pagination.page_rows > 0
