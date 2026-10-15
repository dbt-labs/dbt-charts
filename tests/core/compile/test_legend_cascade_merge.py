"""Pin the legend-merge contract: legend patches merge onto compiled ResolvedLegendStyle.

Legend merging flows through the single merge engine (merge_onto_base) on the
ResolvedLegendStyle BaseModel. These guard against behavior drift in the cascade.

Chart type "line" is used because it has no per-family theme legend patch, so only
the chart-local patch is applied and base values come from the board-level resolved legend.
"""

from __future__ import annotations

import dataclasses

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedLegendStyle
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _board() -> ChartStyleContext:
    return resolve_chart_style_context(get_theme_style())


def _merge_line(
    patch_dict: dict[str, object], board: ChartStyleContext | None = None
) -> ResolvedLegendStyle:
    """Apply patch_dict as a legend sub-patch on a line chart (no family legend override)."""
    from dbt_charts.core.compile.models.chart.normalized import LineChart
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve.style.chart_context import (
        build_chart_style_context,
    )

    chart_patch = LineChartStylePatch.model_validate({"legend": patch_dict})
    chart = LineChart(id="c", type="line", style=chart_patch)
    effective_board = _board() if board is None else board
    result = build_chart_style_context(effective_board, chart)
    return result.legend


class TestLegendMergeContract:
    """These invariants must hold before and after the dataclass→BaseModel conversion."""

    def test_none_patch_leaves_legend_unchanged(self) -> None:
        """When no chart-local legend patch is authored, the resolved legend is the base."""
        from dbt_charts.core.compile.models.chart.normalized import LineChart
        from dbt_charts.core.compile.resolve.style.chart_context import (
            build_chart_style_context,
        )

        board = _board()
        chart = LineChart(id="c", type="line", style=None)
        result = build_chart_style_context(board, chart)
        # No legend override, no per-family legend patch for line → identical
        assert result.legend is board.legend

    def test_visible_patch_only_inherits_all_other_fields(self) -> None:
        """A patch with only symbol_limit inherits position, direction, label, title from base."""
        base = _board().legend
        merged = _merge_line({"symbol_limit": 3})

        assert merged.symbol_limit == 3
        assert merged.position == base.position
        assert merged.direction == base.direction
        # Nested element style fields inherited
        assert merged.label.padding == base.label.padding
        assert merged.label.font.family == base.label.font.family
        assert merged.title.padding == base.title.padding
        assert merged.title.font.family == base.title.font.family

    def test_label_font_color_patch_merges_without_clobbering_siblings(self) -> None:
        """label.font.color patch only changes color; family/size/weight are inherited."""
        base = _board().legend
        merged = _merge_line({"label": {"font": {"color": "#ff0000"}}})

        assert merged.label.font.color == "#ff0000"
        # Sibling font fields preserved
        assert merged.label.font.family == base.label.font.family
        assert merged.label.font.size == base.label.font.size
        assert merged.label.font.weight == base.label.font.weight
        # Other legend fields unchanged
        assert merged.position == base.position
        assert merged.direction == base.direction
        assert merged.visible == base.visible

    def test_symbol_limit_patch_is_applied(self) -> None:
        """symbol_limit override is applied; other fields inherited."""
        base = _board().legend
        merged = _merge_line({"symbol_limit": 10})

        assert merged.symbol_limit == 10
        assert merged.position == base.position
        assert merged.label.font.family == base.label.font.family

    def test_label_max_width_patch_preserves_padding(self) -> None:
        """label.max_width override is applied; label.padding is inherited."""
        base = _board().legend
        merged = _merge_line({"label": {"max_width": 120.0}})

        assert merged.label.max_width == 120.0
        assert merged.label.padding == base.label.padding
        assert merged.label.font.family == base.label.font.family

    def test_direction_patch_is_left_to_the_engine(self) -> None:
        """Direction is decided per chart from the edge, not merged here."""
        base = _board().legend
        merged = _merge_line({"direction": "horizontal"})

        assert merged.direction == base.direction
        assert merged.position == base.position

    def test_omitted_symbol_limit_inherits_family_value(self) -> None:
        board = _board()
        legend = board.legend.model_copy(update={"symbol_limit": 17})
        customized = dataclasses.replace(board, legend=legend)

        merged = _merge_line({}, customized)

        assert merged.symbol_limit == 17

    def test_explicit_none_clears_family_symbol_limit(self) -> None:
        board = _board()
        legend = board.legend.model_copy(update={"symbol_limit": 17})
        customized = dataclasses.replace(board, legend=legend)

        merged = _merge_line({"symbol_limit": None}, customized)

        assert merged.symbol_limit is None
