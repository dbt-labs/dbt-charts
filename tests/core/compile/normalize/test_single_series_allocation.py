"""Tests for the single-series rhythm-palette allocator.

The pass walks each board's direct layout in reading order and assigns
``rhythm_slot`` to single-series-eligible charts. Cycle wrap, override
slot consumption, and multi-series skip are all pinned here.
"""

from __future__ import annotations

from dbt_charts.core.compile import compile as compile_yaml
from dbt_charts.core.compile.normalize.single_series_allocation import (
    single_series_slots,
)
from dbt_charts.core.compile.resolve import resolve


def _slots(board) -> list[int]:
    """Slot values for direct chart items in this board's layout order.

    Charts default to slot 0; the allocator assigns non-zero slots to
    single-series-eligible charts in board reading order. An ineligible
    chart shows up as 0 (the default) — the test that an ineligible
    chart didn't consume a slot is "the next eligible chart still gets
    the expected sequential slot."
    """
    out: list[int] = []
    for item in board.layout.items:
        if item.type == "chart" and item.chart is not None:
            out.append(item.chart.rhythm_slot)
    return out


_QUERY_FRAGMENT = "query: {sql: 'SELECT 1 AS month, 1 AS revenue', source: duckdb}"


def _compile(yaml_text: str):
    result = compile_yaml(yaml_text)
    assert result.success, result.errors
    assert result.board is not None
    return result.board


def test_three_eligible_charts_get_slots_0_1_2():
    board = _compile(
        f"""\
title: T
rows:
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: line, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: area, x: month, y: revenue, {_QUERY_FRAGMENT}}}
"""
    )
    assert _slots(board) == [0, 1, 2]


def test_slots_wrap_past_palette_length():
    # 5 charts, palette is 3 stops on editorial — allocator assigns 0..4;
    # the modulo happens at render. The allocator just counts up.
    board = _compile(
        f"""\
title: T
rows:
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
"""
    )
    assert _slots(board) == [0, 1, 2, 3, 4]


def test_multi_series_chart_does_not_consume_slot():
    # color: <field> = multi-series — no slot, counter doesn't advance.
    board = _compile(
        f"""\
title: T
rows:
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, color: category, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
"""
    )
    # Chart 0 → slot 0, chart 1 → no slot, chart 2 → slot 1.
    assert _slots(board) == [0, 0, 1]


def test_multi_metric_y_list_does_not_consume_slot():
    # y as a list (multi-metric) → layered/multi-series — no slot.
    board = _compile(
        f"""\
title: T
rows:
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: [revenue, target], query: {{sql: 'SELECT 1 AS month, 1 AS revenue, 1 AS target', source: duckdb}}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
"""
    )
    assert _slots(board) == [0, 0, 1]


def test_scatter_and_circle_eligible_for_rhythm():
    # `scatter` and `circle` paint via the single-ink path at render —
    # both must participate in the rhythm. (`circle` is a valid authored
    # type alongside `scatter`; covered here by direct eligibility check
    # because the YAML scatter row is enough to exercise allocation in
    # the compile path.)
    from dbt_charts.core.compile.models.chart.normalized import ScatterChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.normalize.single_series_allocation import (
        SINGLE_INK_CHART_TYPES,
        is_single_series_eligible,
    )

    assert "circle" in SINGLE_INK_CHART_TYPES
    assert "scatter" in SINGLE_INK_CHART_TYPES

    q = SqlQuery(sql="SELECT 1", source="test")
    # "circle" is a VL-level alias, not an authored type — check set membership only.
    # "scatter" is the authored Chart family; verify eligibility via a real model.
    c = ScatterChart(
        id="scatter", type="scatter", query=q, query_name="q", x="x", y="y"
    )
    assert is_single_series_eligible(c) is True, "scatter should be eligible"

    # End-to-end: a scatter row mixed with bars cycles slots correctly.
    board = _compile(
        f"""\
title: T
rows:
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: scatter, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
"""
    )
    assert _slots(board) == [0, 1, 2]


def test_histogram_eligible_for_rhythm():
    # A histogram is a BarChart with type="histogram" — it already passes
    # the isinstance(_CartesianChartFields) gate; only the type-string
    # frozenset gate excludes it. Mirrors test_scatter_and_circle_eligible_for_rhythm.
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.normalize.single_series_allocation import (
        SINGLE_INK_CHART_TYPES,
        is_single_series_eligible,
    )

    assert "histogram" in SINGLE_INK_CHART_TYPES

    q = SqlQuery(sql="SELECT 1", source="test")
    c = BarChart(id="hist", type="histogram", query=q, query_name="q", x="x")
    assert is_single_series_eligible(c) is True, "histogram should be eligible"


def test_histogram_shifts_downstream_chart_slot():
    # Real shape from resolution-time.yml / pay-bands.yml: histogram first,
    # then a bar downstream. The histogram must both take a slot and
    # advance the counter for the chart after it.
    board = _compile(
        f"""\
title: T
rows:
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: histogram, x: month, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
"""
    )
    assert _slots(board) == [0, 1, 2]


def test_histogram_with_color_stays_ineligible():
    # Mirrors recipients.yml's real shape: a histogram with an authored
    # color: channel stays excluded — the chart.color gate applies
    # generically regardless of the type-string frozenset.
    board = _compile(
        f"""\
title: T
rows:
  - {{type: histogram, x: month, color: category, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
"""
    )
    assert _slots(board) == [0, 0]


def test_geo_chart_types_stay_out_of_single_ink_types():
    # Pins the "geo stays out" decision as a checkable constraint. Geo
    # fails independently via isinstance(_CartesianChartFields) — this
    # frozenset membership is a second, unrelated gate.
    from dbt_charts.core.compile.normalize.single_series_allocation import (
        SINGLE_INK_CHART_TYPES,
    )

    assert "point_map" not in SINGLE_INK_CHART_TYPES
    assert "bubble_map" not in SINGLE_INK_CHART_TYPES


def test_non_single_series_types_do_not_consume_slot():
    # KPI, table, pie — none paint via single_series_palette.
    board = _compile(
        f"""\
title: T
rows:
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: kpi, value: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
"""
    )
    assert _slots(board) == [0, 0, 1]


def test_charts_v2_rhythm_slot_synced_after_allocation():
    """charts_v2 must carry the same rhythm_slot as the V1 charts after allocation.

    Regression: charts_v2 was baked in STEP 7 of normalize_board, before
    allocate_single_series_slots ran. The allocator mutated V1 Chart objects
    but never touched charts_v2 — all charts stayed at rhythm_slot=0, so
    every single-series chart on a multi-chart board rendered with palette[0].
    """
    board = _compile(
        f"""\
title: T
rows:
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
"""
    )
    chart_ids = [
        item.chart.id
        for item in board.layout.items
        if item.type == "chart" and item.chart is not None
    ]
    assert [board.charts[cid].rhythm_slot for cid in chart_ids] == [0, 1, 2]


def test_charts_v2_rhythm_slot_synced_across_cols_wrappers():
    """charts_v2 must carry the allocator's slot for charts nested under cols:.

    Regression (the common real-world shape — mirrors
    examples/playground/charts/general/products-overview.yml): for a flat
    `rows:` layout, the layout-tree Chart instances are the same objects as
    `compiled_board.charts[name]`, so syncing rhythm_slot by iterating the
    flat `.charts` dict happens to work. `rows:` containing `cols:` builds
    each `cols:` group as a nested sub-board normalized independently — its
    layout-tree Chart instances are DIFFERENT objects from
    `compiled_board.charts[name]`. The allocator assigned slots 0,1,2 to the
    tree instances, but the flat-dict instances (and thus charts_v2, synced
    from the flat dict) stayed at the default 0 — every single-series chart
    under a cols: wrapper rendered with the same color.

    Uses ``theme: paper`` — the default theme's single_series_palette is one
    fixed ink (no rotation), so distinct rhythm slots would resolve to the
    same color regardless of this bug; paper keeps a multi-ink rotation.
    """
    board = _compile(
        f"""\
title: T
theme: paper
charts:
  a: {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  b: {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  c: {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
rows:
  - cols: [a, b]
  - cols: [c]
"""
    )
    assert [board.charts[cid].rhythm_slot for cid in ("a", "b", "c")] == [0, 1, 2]

    fills = [
        resolve(
            board.charts[cid], [], chart_style_context=board.chart_style_context
        ).style.single_series_fill
        for cid in ("a", "b", "c")
    ]
    assert len(set(fills)) == 3, f"expected 3 distinct colors, got {fills}"


def test_charts_v2_rhythm_slot_synced_in_tabs_layout():
    """Same sync bug, `tabs:` layout instead of `cols:` — both build nested
    sub-boards whose Chart instances differ from `compiled_board.charts`."""
    board = _compile(
        f"""\
title: T
charts:
  t1: {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  t2: {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  t3: {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
tabs:
  items:
    - {{title: One, rows: [t1, t2]}}
    - {{title: Two, rows: [t3]}}
"""
    )
    assert [board.charts[cid].rhythm_slot for cid in ("t1", "t2", "t3")] == [
        0,
        1,
        2,
    ]


def test_author_color_override_still_consumes_slot():
    # Override wins at render, but the slot is still assigned by position
    # so removing the override does not shift other charts' colors.
    board = _compile(
        f"""\
title: T
rows:
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, style: {{marks: {{bar: {{color: '#ff0000'}}}}}}, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
"""
    )
    assert _slots(board) == [0, 1, 2]


def test_board_charts_holds_same_instance_as_layout_tree_after_allocation():
    """board.charts[id] must be the SAME object as the chart in the layout tree.

    Regression: named charts in `board.charts` were compiled once at root level
    (instance A) and again inside nested tab/cols sub-boards (instance B). The
    allocator mutated B (the layout-tree instance), but board.charts still held A
    (rhythm_slot=0 for all). A post-hoc model_copy patchup fixed the slot value
    but created a third instance C — board.charts and the layout tree were STILL
    different objects. The fix ensures layout instances are stored in board.charts
    upfront so allocator mutations propagate directly (no patchup needed).
    """
    board = _compile(
        f"""\
title: T
charts:
  a: {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  b: {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
tabs:
  items:
    - title: Tab1
      rows: [a, b]
"""
    )
    # Collect the chart instances the layout tree actually holds.
    tab_board = next(
        item.board for item in board.layout.items if item.type == "board" and item.board
    )
    layout_charts = {
        item.chart.id: item.chart
        for item in tab_board.layout.items
        if item.type == "chart" and item.chart is not None
    }
    # board.charts must be the SAME Python objects, not copies.
    for cid, layout_chart in layout_charts.items():
        assert board.charts[cid] is layout_chart, (
            f"board.charts[{cid!r}] is a different instance from the layout tree's chart; "
            "rhythm_slot mutations on the layout copy won't propagate to board.charts"
        )


def test_rhythm_flows_continuously_through_nested_boards():
    # One continuous sequence across the whole board tree: the nested
    # board's charts pick up the slot counter where the outer left off.
    board = _compile(
        f"""\
title: Outer
rows:
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - title: Inner
    rows:
      - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
      - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
  - {{type: bar, x: month, y: revenue, {_QUERY_FRAGMENT}}}
"""
    )
    # Direct outer charts (rows[0] and rows[2]) get slots 0 and 3.
    # The two charts in the nested board take slots 1 and 2 — the rhythm
    # threads through the whole tree in reading order.
    assert _slots(board) == [0, 3]
    nested = next(
        item.board for item in board.layout.items if item.type == "board" and item.board
    )
    assert _slots(nested) == [1, 2]


def test_a_chart_placed_twice_ends_with_its_last_placements_slot() -> None:
    board = _compile(
        """\
queries:
  q: {sql: 'SELECT 1 AS month, 1 AS revenue', source: duckdb}
charts:
  a: {type: bar, query: q, x: month, y: revenue}
  b: {type: bar, query: q, x: month, y: revenue}
rows:
  - a
  - b
  - a
"""
    )
    slots = single_series_slots(board)
    assert {cid: slots[id(c)] for cid, c in board.charts.items()} == {"a": 2, "b": 1}
    assert {cid: c.rhythm_slot for cid, c in board.charts.items()} == {"a": 2, "b": 1}
