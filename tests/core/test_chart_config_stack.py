"""Tests for runtime config defaults after config consolidation."""

from __future__ import annotations

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    reset_config,
)

from .chart_default_expectations import shipped_default_theme_name


class TestChartConfigStack:
    def setup_method(self) -> None:
        reset_config()

    def teardown_method(self) -> None:
        reset_config()

    def test_runtime_config_defaults_are_available(self) -> None:
        # The theme name lives in the config module constant _default_theme_name,
        # via get_default_theme_name().
        assert get_default_theme_name() == shipped_default_theme_name()
