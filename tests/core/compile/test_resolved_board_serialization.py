"""ResolvedBoard serialization contract — Phase A of the resolved-board-artifact work.

Covers:
- TypeAdapter(ResolvedBoard).dump_json(..., warnings="error") succeeds for a
  statically-resolved board carrying every AnyQuery variant, and round-trips
  byte-for-byte through validate_json.
- The same guarantee on the data-aware path (build_resolved_board), which is
  what actually runs a real render — both paths construct ResolvedChart via
  resolve(), which bakes layout_padding through to_padding_style(), so a
  PaddingStyle/PaddingStylePatch mismatch would surface on either path.
- ResolvedLayoutItem.visible (bool | str | SingleRowBoolProbe | None) has no
  shared discriminator tag; each variant is round-tripped individually
  rather than restructured speculatively (see dbt-charts/AGENTS.md).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from pydantic import TypeAdapter

from dbt_charts.core.compile.models.board.resolved import (
    ResolvedBoard,
    ResolvedLayoutItem,
)
from dbt_charts.core.compile.models.variable.authored import SingleRowBoolProbe

_board_adapter = TypeAdapter(ResolvedBoard)
_item_adapter = TypeAdapter(ResolvedLayoutItem)


def _compile_static_board(yaml_content: str) -> ResolvedBoard:
    from dbt_charts.core.compile import compile
    from dbt_charts.core.render.board_resolve import build_resolved_board_static

    result = compile(yaml_content)
    assert result.success, result.errors
    return build_resolved_board_static(result.board)


def _compile_data_aware_board(
    yaml_content: str, rows_by_query: dict[str, list[dict[str, Any]]]
) -> ResolvedBoard:
    """Compile and fully resolve a board through the data-aware path.

    Uses a MagicMock executor keyed by query name — no database required.
    A ``values:`` query board renders real data without any adapter, so this
    exercises the actual render entrypoint (build_resolved_board), not just
    the data-free static shortcut.
    """
    from dbt_charts.core.compile import compile
    from dbt_charts.core.render.board_resolve import build_resolved_board

    result = compile(yaml_content)
    assert result.success, result.errors
    board = result.board

    executor = MagicMock()
    executor.render_with_range_filters.return_value = None
    executor.execute_query.side_effect = lambda name, variables: rows_by_query.get(
        name, []
    )
    executor.execute_chart.return_value = []
    executor.execute_batch.return_value = {}

    rf, _ = build_resolved_board(board, executor, {}, render_first=False)
    return rf


class TestResolvedBoardDumpJsonWarningsError:
    def test_board_with_values_and_schema_queries_dumps_and_round_trips(self) -> None:
        rf = _compile_static_board(
            """
title: T
queries:
  qv:
    type: values
    rows:
      - {month: Jan, revenue: 100}
  qs:
    type: schema
charts:
  c:
    query: qv
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
        )
        # Mirror the real pipeline: duckdb_cache.compute_query_hash reads
        # source_description to build the persistent-cache key, so a
        # representative board has already memoized it by the time it's
        # serialized.
        for query in rf.queries.values():
            _ = query.source_description

        out = _board_adapter.dump_json(rf, warnings="error")
        back = _board_adapter.validate_json(out)
        assert back == rf

    def test_board_with_auto_detected_variable_input_round_trips(self) -> None:
        """Regression: Variable.input_auto_detected/variable_dependencies were
        ``exclude=True``, so a board replayed from the published artifact lost
        ``input_auto_detected`` — render gates variable-control behavior on it
        (controls.py, variable_input_refinement.py), so the replayed board
        showed different controls than the live one.
        """
        rf = _compile_static_board(
            """
title: T
variables:
  category:
    options:
      static: [a, b, c]
queries:
  qv:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: qv
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
        )
        # Sanity: the bug only manifests when this is actually True.
        assert rf.variables["category"].input_auto_detected is True

        out = _board_adapter.dump_json(rf, warnings="error")
        back = _board_adapter.validate_json(out)
        assert back == rf

    def test_data_aware_board_with_values_query_dumps_and_round_trips(self) -> None:
        rf = _compile_data_aware_board(
            """
title: T
queries:
  qv:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: qv
    type: bar
    x: month
    y: revenue
rows:
  - c
""",
            {"qv": [{"month": "Jan", "revenue": 100}]},
        )
        for query in rf.queries.values():
            _ = query.source_description

        out = _board_adapter.dump_json(rf, warnings="error")
        back = _board_adapter.validate_json(out)
        assert back == rf


class TestResolvedLayoutItemVisibleRoundTrip:
    """visible has no shared discriminator across its bool | str |
    SingleRowBoolProbe | None variants — each is verified independently.
    """

    @staticmethod
    def _item(visible: bool | str | SingleRowBoolProbe | None) -> ResolvedLayoutItem:
        return ResolvedLayoutItem(
            type="chart",
            chart=None,
            board=None,
            x=0.0,
            y=0.0,
            width=100.0,
            height=100.0,
            visible=visible,
        )

    def test_visible_true_round_trips(self) -> None:
        item = self._item(True)
        back = _item_adapter.validate_json(
            _item_adapter.dump_json(item, warnings="error")
        )
        assert back == item

    def test_visible_string_expression_round_trips(self) -> None:
        item = self._item("row_count > 0")
        back = _item_adapter.validate_json(
            _item_adapter.dump_json(item, warnings="error")
        )
        assert back == item

    def test_visible_single_row_bool_probe_round_trips(self) -> None:
        probe = SingleRowBoolProbe(query="layout_flags", column="show_panel")
        item = self._item(probe)
        back = _item_adapter.validate_json(
            _item_adapter.dump_json(item, warnings="error")
        )
        assert back == item

    def test_visible_none_round_trips(self) -> None:
        item = self._item(None)
        back = _item_adapter.validate_json(
            _item_adapter.dump_json(item, warnings="error")
        )
        assert back == item
