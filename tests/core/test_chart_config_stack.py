"""Tests for runtime config defaults after config consolidation."""

from __future__ import annotations

from dbt_charts.core.compile.config import (
    get_config,
    get_default_theme_name,
    reset_config,
)
from dbt_charts.core.compile.vega_lite import VEGA_LITE_SCHEMA_URL

from .chart_default_expectations import shipped_default_theme_name


class TestChartConfigStack:
    def setup_method(self) -> None:
        reset_config()

    def teardown_method(self) -> None:
        reset_config()

    def test_runtime_config_defaults_are_available(self) -> None:
        config = get_config()

        assert config.vega.schema == VEGA_LITE_SCHEMA_URL
        # vega.default_theme was removed from config; theme name lives in the
        # config module constant _default_theme_name, via get_default_theme_name().
        assert get_default_theme_name() == shipped_default_theme_name()
