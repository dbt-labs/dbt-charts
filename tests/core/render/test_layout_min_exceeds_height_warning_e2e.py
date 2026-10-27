"""End-to-end: LAYOUT_MIN_EXCEEDS_HEIGHT surfaces from render() for a
horizontal bar whose category-count floor exceeds its row's authored height.

Proves the full seam — compile -> render() -> RenderResult.warnings — not a
hand-built WarningContext. A hand-built context can pass while the real
pipeline stays silent (the detector's authored-height input is overwritten
by the layout sizing pass for `cols:`-wrapped charts; only an end-to-end
render exercises the real value the detector receives).
"""

from __future__ import annotations

from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render

_CODE = "WARN-LAYOUT-MIN-EXCEEDS-HEIGHT"


def _rows(n: int) -> list[dict[str, object]]:
    return [{"cat": f"cat{i:02d}", "val": i + 1} for i in range(n)]


def _make_executor(
    board: object, query_registry: object, rows: list[dict[str, object]]
):
    ok = Mock()
    ok.is_success = True
    ok.data = rows
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    return Executor(
        board, adapter_registry=mock_registry, query_registry=query_registry
    )


def _board_yaml(*, wrapper: str, authored_height: str) -> str:
    """A row authoring `height: {authored_height}` around a horizontal bar,
    via the given wrapper keyword (`rows` or `cols`) — both are valid ways to
    pin a tile's height per docs/boards/sizing.md.
    """
    return f"""
title: Probe
charts:
  hbar:
    query: q
    type: bar
    x: cat
    y: val
    style:
      orientation: horizontal
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - height: {authored_height}
    {wrapper}:
      - hbar
"""


def _render(yaml_source: str, n_categories: int) -> list[object]:
    result = compile(yaml_source)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _rows(n_categories))
    render_result = render(result.board, executor, format="svg")
    return list(render_result.warnings)


def test_fires_through_rows_wrapper() -> None:
    """17 categories need ~735px; a `rows:`-wrapped row authored only 280px."""
    warnings = _render(_board_yaml(wrapper="rows", authored_height="280"), 17)
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes
    w = next(w for w in warnings if w.code == _CODE)
    assert w.chart == "hbar"
    assert "280" in w.message
    assert w.fix


def test_fires_through_cols_wrapper() -> None:
    """Same violation, but through a `cols:` wrapper — the layout sizing
    pass's cols-alignment step re-expands the chart's resolved height to its
    natural (unconstrained) size afterward, so the detector must read its
    authored-height input from a snapshot taken before that happens.
    """
    warnings = _render(_board_yaml(wrapper="cols", authored_height="280"), 17)
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes


def test_silent_when_authored_height_is_comfortable() -> None:
    """3 categories need ~189px; the row authored 900px — nowhere near the floor."""
    warnings = _render(_board_yaml(wrapper="rows", authored_height="900"), 3)
    codes = {w.code for w in warnings}
    assert _CODE not in codes


def test_fires_through_nested_rows_split_budget() -> None:
    """A parent row authors 900px around two nested-row children; the sizing
    pass SPLITS that budget across them (450px each), not the full 900 each.
    Each child holds a 17-category bar (floor ~735px) — well past its real
    450px slot, but comfortably under the parent's 900px. The authored-height
    source must reflect the split, not the parent's raw value, or this stays
    silent.
    """
    yaml_source = """
title: Probe
charts:
  hbar:
    query: q
    type: bar
    x: cat
    y: val
    style:
      orientation: horizontal
  hbar2:
    query: q
    type: bar
    x: cat
    y: val
    style:
      orientation: horizontal
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - height: 900
    rows:
      - hbar
      - hbar2
"""
    warnings = _render(yaml_source, 17)
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes
    fired = {w.chart for w in warnings if w.code == _CODE}
    assert fired == {"hbar", "hbar2"}, fired


def test_silent_when_nothing_is_authored() -> None:
    """No row wrapper at all -> no authored height to violate."""
    yaml_source = """
title: Probe
charts:
  hbar:
    query: q
    type: bar
    x: cat
    y: val
    style:
      orientation: horizontal
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - hbar
"""
    warnings = _render(yaml_source, 17)
    codes = {w.code for w in warnings}
    assert _CODE not in codes


def test_fires_for_a_faceted_horizontal_bar_that_outgrows_its_slot() -> None:
    """`multiples: {rows: grp}` splits the authored height across 3 row
    panels, and each panel needs its own category-count floor — not a third
    of one shared floor. 5 categories need ~267px per panel; three panels
    need ~801px total. A 400px authored height clears the un-faceted
    (single-panel, ~267px) floor comfortably but not three panels' worth, so
    this only fires once the detector multiplies the floor by the baked row
    cardinality — the pre-fix comparison (min_h <= authored_height, 267 <=
    400) would have stayed silent here."""
    yaml_source = """
title: Probe
charts:
  hbar:
    query: q
    type: bar
    x: cat
    y: val
    style:
      orientation: horizontal
    multiples:
      rows: grp
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - height: 400
    rows:
      - hbar
"""
    rows = [
        {"cat": f"c{i:02d}", "grp": f"g{g}", "val": i + 1}
        for g in range(3)
        for i in range(5)
    ]
    result = compile(yaml_source)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, rows)
    warnings = list(render(result.board, executor, format="svg").warnings)
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes
    w = next(w for w in warnings if w.code == _CODE)
    assert w.chart == "hbar"
    assert "400" in w.message


def test_fires_for_a_faceted_horizontal_bar_with_the_full_domain_in_every_panel() -> (
    None
):
    """Every panel carries the SAME, full 15-category domain (no panel's own
    rows are a proper subset of anything) — the facet operator resolves the
    category ordinal scale as SHARED (only the measure channel ever gets
    `resolve.scale`, and only under `multiples.scale: independent`), and
    domain-subset narrowing (`facet_bound_position_channels`) never fires
    here either, since 15-of-15 is not a proper subset — so every panel
    needs room for all 15 categories. The floor is
    `count_horizontal_bar_categories` over the WHOLE dataset (15) multiplied
    by the row cardinality (3): an authored height of 1000px clears the
    too-small 5-categories-per-panel floor (~267px/panel -> ~801px for 3
    panels, which is what a BUGGY narrowing verdict would compute here) but
    not the correct whole-set floor (15 categories -> ~657px/panel ->
    ~1971px for 3 panels).

    The sibling below, `test_silent_for_a_faceted_horizontal_bar_whose_
    disjoint_panel_categories_narrow`, is the mirror-image fixture — panels
    that DO each hold a proper subset — where narrowing correctly applies
    and this same 1000px height turns out to be enough.
    """
    yaml_source = """
title: Probe
charts:
  hbar:
    query: q
    type: bar
    x: cat
    y: val
    style:
      orientation: horizontal
    multiples:
      rows: grp
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - height: 1000
    rows:
      - hbar
"""
    rows = [
        {"cat": f"c{i:02d}", "grp": f"g{g}", "val": i + 1}
        for g in range(3)
        for i in range(15)
    ]
    result = compile(yaml_source)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, rows)
    warnings = list(render(result.board, executor, format="svg").warnings)
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes


def test_silent_for_a_faceted_horizontal_bar_whose_disjoint_panel_categories_narrow() -> (
    None
):
    """Panels with DIFFERENT categories (the ordinary "top 5 per region"
    shape): 3 panels x 5 categories each, all 15 category values distinct
    across panels. Each panel's own 5 categories ARE a proper subset of the
    15-value whole-dataset domain, so `facet_bound_position_channels`
    narrows the category axis independently per panel — the height floor
    only needs the WIDEST panel's own count (5), not the whole-dataset
    union (15). 1000px comfortably clears the correct ~801px floor (5
    categories -> ~267px/panel -> 3 panels); the OLD, pre-domain-subset
    behavior would have wrongly demanded ~1971px here (see the sibling
    test above, whose fixture actually needs that)."""
    yaml_source = """
title: Probe
charts:
  hbar:
    query: q
    type: bar
    x: cat
    y: val
    style:
      orientation: horizontal
    multiples:
      rows: grp
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - height: 1000
    rows:
      - hbar
"""
    rows = [
        {"cat": f"g{g}_c{i:02d}", "grp": f"g{g}", "val": i + 1}
        for g in range(3)
        for i in range(5)
    ]
    result = compile(yaml_source)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, rows)
    warnings = list(render(result.board, executor, format="svg").warnings)
    codes = {w.code for w in warnings}
    assert _CODE not in codes, codes


def test_silent_for_a_faceted_horizontal_bar_that_fits_every_panel() -> None:
    """Same shape, but a 2500px authored height clears three panels' worth
    (~801px) comfortably."""
    yaml_source = """
title: Probe
charts:
  hbar:
    query: q
    type: bar
    x: cat
    y: val
    style:
      orientation: horizontal
    multiples:
      rows: grp
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - height: 2500
    rows:
      - hbar
"""
    rows = [
        {"cat": f"c{i:02d}", "grp": f"g{g}", "val": i + 1}
        for g in range(3)
        for i in range(5)
    ]
    result = compile(yaml_source)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, rows)
    warnings = list(render(result.board, executor, format="svg").warnings)
    codes = {w.code for w in warnings}
    assert _CODE not in codes
