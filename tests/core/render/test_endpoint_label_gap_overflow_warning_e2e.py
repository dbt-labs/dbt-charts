"""End-to-end: endpoint-label rail degradations fire their warnings through
the real render pipeline, instead of piling every label onto the domain
edges or silently dropping some with no signal.

Proves the full seam — compile -> render() -> RenderResult.warnings — not a
hand-built WarningContext, matching the pattern in
``test_table_overflow_warning_e2e.py``.
"""

from __future__ import annotations

import re
from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render

_CODE = "WARN-ENDPOINT-LABEL-GAP-OVERFLOW"

# Eight series, endpoints packed into a 1.0-wide band — (n-1) * 18px cannot
# possibly fit into an 80px-tall card, forcing the overflow degradation.
_SERIES = [f"s{i}" for i in range(8)]


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i, s in enumerate(_SERIES):
        rows.append({"date": "2024-01-01", "value": 100.0, "series": s})
        rows.append({"date": "2024-02-01", "value": 100.0 + i * 0.1, "series": s})
    return rows


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


_CRAMPED = """
title: Cramped rail
charts:
  trend:
    query: q
    type: line
    x: date
    y: value
    color: series
    style:
      endpoint_labels:
        visible: true
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - height: 80
    cols:
      - trend
"""

_ROOMY = """
title: Roomy rail
charts:
  trend:
    query: q
    type: line
    x: date
    y: value
    color: series
    style:
      endpoint_labels:
        visible: true
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - height: 600
    cols:
      - trend
"""

# Same fixture as _CRAMPED, but sized via chart-root `height:` instead of
# `rows: - height:`. Chart-root height is fully deterministic at compile
# time (see sizing.py's get_chart_content_height, step 1), so the main SVG
# pass computes the identical (chart_id, width, height) the render-first
# sizing pass already rendered and cached, and reuses that cached SVG
# instead of re-rendering — the seam that let this overflow go unwarned.
_CRAMPED_CHART_ROOT_HEIGHT = """
title: Cramped rail (chart-root height)
charts:
  trend:
    query: q
    type: line
    x: date
    y: value
    color: series
    height: 80
    style:
      endpoint_labels:
        visible: true
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - cols:
      - trend
"""


def test_cramped_rail_warns_and_distributes_evenly() -> None:
    result = compile(_CRAMPED)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _rows())

    render_result = render(result.board, executor, format="svg")

    codes = {w.code for w in render_result.warnings}
    assert _CODE in codes, (
        "eight series crammed into an 80px rail cannot fit (n-1)*18px of "
        "intended gap — the overflow warning must fire"
    )
    warning = next(w for w in render_result.warnings if w.code == _CODE)
    assert warning.chart == "trend"
    assert warning.fix

    assert render_result.output is not None
    ys = [
        float(y)
        for y in re.findall(
            r'concat_1_marks"[^>]*><text x="0" y="([\-0-9.]+)"', render_result.output
        )
    ]
    assert ys
    ys.sort()
    gaps = [b - a for a, b in zip(ys, ys[1:], strict=False)]
    # Even distribution: every successive gap is the same, within rounding —
    # the pre-fix pile-up produces wildly unequal (near-zero) gaps instead.
    assert max(gaps) - min(gaps) < 0.5, (
        f"overflowed rail must distribute labels evenly across the plot; "
        f"got gaps {gaps}"
    )


def test_roomy_rail_silent() -> None:
    result = compile(_ROOMY)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _rows())

    render_result = render(result.board, executor, format="svg")

    codes = {w.code for w in render_result.warnings}
    assert _CODE not in codes


def test_cramped_rail_warns_via_chart_root_height() -> None:
    """A chart-root `height:` overflow must warn exactly like a row-height one.

    The main SVG pass reuses the sizing pass's cached render for this shape
    (see module docstring on ``_CRAMPED_CHART_ROOT_HEIGHT``), so
    ``record_endpoint_label_gap_overflow`` is only ever called during the
    sizing pass — a sink open only around the main pass would silently
    drop this warning even though the identical overflow occurred.
    """
    result = compile(_CRAMPED_CHART_ROOT_HEIGHT)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _rows())

    render_result = render(result.board, executor, format="svg")

    codes = {w.code for w in render_result.warnings}
    assert _CODE in codes, (
        "eight series crammed into an 80px chart-root height cannot fit "
        "(n-1)*18px of intended gap — the overflow warning must fire "
        "regardless of whether the height was authored on the row or the "
        "chart root"
    )
    warning = next(w for w in render_result.warnings if w.code == _CODE)
    assert warning.chart == "trend"
    assert warning.fix


_OVERFLOW_CODE = "WARN-ENDPOINT-LABEL-RAIL-OVERFLOW"

# 10 series clustered near the domain floor plus one isolated anchor (TOP)
# at the domain's own top edge: the global (n-1)*gap <= domain_span check
# passes, but the greedy cascade still runs out of *local* room within the
# cluster — the shape that used to pile several labels onto one pixel, or
# (pre this task's cascade fix) drop the isolated TOP anchor just because
# cascade order put it last. TOP has genuine room and must always be drawn.
_CLUSTERED_SERIES = [f"s{i:02d}" for i in range(10)] + ["TOP"]


def _clustered_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i, s in enumerate(_CLUSTERED_SERIES[:-1]):
        rows.append({"date": "2024-01-01", "value": i * 0.1, "series": s})
        rows.append({"date": "2024-02-01", "value": i * 0.1, "series": s})
    rows.append({"date": "2024-01-01", "value": 50.0, "series": "TOP"})
    rows.append({"date": "2024-02-01", "value": 50.0, "series": "TOP"})
    return rows


_LOCAL_OVERFLOW = """
title: Locally overflowed rail
charts:
  trend:
    query: q
    type: line
    x: date
    y: value
    color: series
    style:
      endpoint_labels:
        visible: true
      axis_y:
        scale:
          continuous:
            domain: [-5, 55]
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - height: 250
    cols:
      - trend
"""


def test_local_overflow_warns_and_drops_only_what_does_not_fit() -> None:
    result = compile(_LOCAL_OVERFLOW)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _clustered_rows())

    render_result = render(result.board, executor, format="svg")

    codes = {w.code for w in render_result.warnings}
    assert _OVERFLOW_CODE in codes, (
        "11 anchors (a dense cluster plus an isolated one) that pass the "
        "global fit check but overrun locally must report the narrower "
        "rail_overflow code"
    )
    assert _CODE not in codes, "not the global gap_did_not_fit degradation"
    warning = next(w for w in render_result.warnings if w.code == _OVERFLOW_CODE)
    assert warning.chart == "trend"
    assert warning.fix
    assert "1 of 11" in warning.message

    assert render_result.output is not None
    match = re.search(
        r"concat_1_marks[^>]*>(.*?)</g></g>", render_result.output, re.DOTALL
    )
    assert match is not None
    label_block = match.group(1)
    # A rail label reads "{value} {name}".
    drawn_names = {
        text.split(" ", 1)[1] for text in re.findall(r">([^<]+)</text>", label_block)
    }

    # A regression here (e.g. the warning's dropped count going stale while
    # the pane still drew everyone, or the wrong series getting dropped)
    # would pass every assertion above and only get caught by checking what
    # the pane actually painted.
    assert "TOP" in drawn_names, (
        "the isolated anchor with genuine room must still be drawn, not "
        f"discarded for a distant cluster's overflow; drawn={sorted(drawn_names)}"
    )
    assert len(drawn_names) == 10, (
        f"expected 10 series drawn (11 - 1 dropped), got {len(drawn_names)}: "
        f"{sorted(drawn_names)}"
    )


# 21 series, no domain override: the render-first sizing pass's early trials
# (aspect-ratio estimate, then the row's own slot-fixed height) are cramped
# for this many series, but cols-alignment then stretches `trend` to match
# `filler`'s explicit 900px chart-root height, and the LAST recascade at that
# final height genuinely fits. A sink that only ever assigns (never clears
# on a later `fit`) keeps the discarded cramped trial's record — a false
# WARN-ENDPOINT-LABEL-GAP-OVERFLOW for a rail that shipped with room to
# spare.
_STRETCHED_SERIES = [f"s{i}" for i in range(21)]


def _stretched_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i, s in enumerate(_STRETCHED_SERIES):
        rows.append({"date": "2024-01-01", "value": float(i), "series": s})
        rows.append({"date": "2024-02-01", "value": float(i) + 0.1, "series": s})
    return rows


_COLS_ALIGNMENT_STRETCHES_TO_FIT = """
title: Cols alignment stretches a cramped trial into a fitting final render
charts:
  trend:
    query: q
    type: line
    x: date
    y: value
    color: series
    style:
      endpoint_labels:
        visible: true
  filler:
    query: q
    type: line
    x: date
    y: value
    color: series
    height: 900
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
cols:
  - trend
  - filler
"""


def test_cols_alignment_stretch_does_not_leave_a_stale_overflow() -> None:
    """A chart cols-alignment stretches to a fitting height must stay silent.

    The discarded, cramped sizing-pass trial that ran before cols-alignment
    stretched `trend` to match `filler`'s 900px must not leak into
    RenderResult.warnings once the final, shipped render actually fits.
    """
    result = compile(_COLS_ALIGNMENT_STRETCHES_TO_FIT)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _stretched_rows())

    render_result = render(result.board, executor, format="svg")

    codes = {w.code for w in render_result.warnings}
    assert _CODE not in codes, (
        "cols-alignment stretched this chart to a height where all 21 "
        "endpoints fit — a warning here means a discarded, cramped sizing "
        "trial leaked through instead of the render that actually shipped"
    )
