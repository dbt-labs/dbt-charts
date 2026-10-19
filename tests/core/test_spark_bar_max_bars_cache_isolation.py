"""Regression test: a spark_bar failure cached by chart id alone must not
poison a sibling placement of the same chart under a different resolved style.

``layout_sizing._require_resolved``'s ``resolve_errors`` cache is keyed by
chart id alone, on the documented assumption that resolve-time validation
never reads width or style. spark_bar's numeric check used to be an exception
to that: it read a cascaded style value (``style.spark_bar.max_bars``) to
decide which rows to validate. Two nested boards placing the SAME chart id
under two different ``max_bars`` values could then have the first placement's
failure poison the second, even though the second would have rendered fine on
its own.

Fixing the validator to read the full query result (not the max_bars slice)
makes the numeric verdict style-independent, which removes the poisoning
opportunity structurally: both placements now agree, so there is nothing for
one to wrongly cache onto the other.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem
from dbt_charts.core.compile.models.chart.normalized import SparkBarChart
from dbt_charts.core.compile.models.primitives import BoardLinks
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.layout_sizing import (
    SizingRenderCtx,
    _make_data_aware_height_provider,
)
from dbt_charts.core.render.sizing import calculate_layout_height

_TEST_QUERY = SqlQuery(sql="SELECT 1", source="test")

# First 10 rows NULL, last 5 numeric: at the theme-default max_bars (10) the
# visible slice is all-NULL; at max_bars 15 the slice includes the 5 numeric
# rows. Same underlying column, same chart id, two different style patches.
_ROWS = [{"cat": f"R{i}", "val": None} for i in range(10)] + [
    {"cat": f"R{i}", "val": float(15 - i)} for i in range(10, 15)
]


def _spark_bar_chart(chart_id: str) -> SparkBarChart:
    return SparkBarChart(
        id=chart_id,
        query=_TEST_QUERY,
        query_name="q",
        type="spark_bar",
        x="val",
        y="cat",
        style=None,
    )


def test_second_placement_not_poisoned_by_first_placements_max_bars_failure() -> None:
    """Same chart id, two nested boards with different max_bars, one render_ctx.

    Before the fix: the low-max_bars placement (processed first) raised
    ERR-SPARK-BAR-VALUE-NOT-NUMERIC and cached the failure under the chart's
    id alone; the high-max_bars placement (processed second) never got a
    chance to resolve on its own — ``_require_resolved`` short-circuits on
    the cached id, so it inherited the first placement's failure even though
    its own max_bars would have made the exact same data pass. Both must now
    render, and resolve_errors must stay empty.
    """
    low_patch = StylePatch.model_validate({"charts": {"spark_bar": {"max_bars": 10}}})
    high_patch = StylePatch.model_validate({"charts": {"spark_bar": {"max_bars": 15}}})
    low_rs, low_ctx = resolve_style_and_context(get_theme_style(), low_patch)
    high_rs, high_ctx = resolve_style_and_context(get_theme_style(), high_patch)

    # Same chart id in both placements — this is what makes resolve_errors's
    # id-only cache able to collide the two placements at all.
    low_chart = _spark_bar_chart("bad")
    high_chart = _spark_bar_chart("bad")
    low_item = LayoutItem(type="chart", width=300.0, height=0.0, chart=low_chart)
    high_item = LayoutItem(type="chart", width=310.0, height=0.0, chart=high_chart)

    low_board = MagicMock()
    low_board.resolved_style = low_rs
    low_board.layout = Layout.model_validate({"type": "rows", "items": [low_item]})
    low_board.title = ""
    low_board.visible_variables = {}
    low_board.text = None

    high_board = MagicMock()
    high_board.resolved_style = high_rs
    high_board.layout = Layout.model_validate({"type": "rows", "items": [high_item]})
    high_board.title = ""
    high_board.visible_variables = {}
    high_board.text = None

    low_board_item = LayoutItem.model_construct(
        type="board", width=300.0, height=0.0, board=low_board, chart=None
    )
    high_board_item = LayoutItem.model_construct(
        type="board", width=310.0, height=0.0, board=high_board, chart=None
    )
    # Order matters: the low-max_bars (failing, pre-fix) placement runs first.
    outer_layout = Layout.model_validate(
        {"type": "cols", "items": [low_board_item, high_board_item]}
    )

    executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    executor.execute_query.return_value = _ROWS
    render_ctx = SizingRenderCtx(
        executor=executor,
        resolved_style=low_rs,
        chart_style_context=low_ctx,
    )
    style_contexts = {id(low_rs): low_ctx, id(high_rs): high_ctx}

    provider = _make_data_aware_height_provider(
        render_ctx,
        executor,
        variables={},
        card_padding=0.0,
        style_contexts=style_contexts,
    )
    calculate_layout_height(
        outer_layout,
        card_gap=0.0,
        gap=0.0,
        min_height=0.0,
        available_width=700.0,
        height_provider=provider,
        resolved_style=low_rs,
    )

    assert render_ctx.resolve_errors == {}, (
        "same data, same chart id, two max_bars values: both placements must "
        f"resolve cleanly with no cross-placement poisoning. Got: "
        f"{render_ctx.resolve_errors}"
    )
