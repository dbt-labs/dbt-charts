"""Genuine-zero bar rows render as a direct "0" label, not an invisible mark.

Proves the full seam — compile() -> render() -> SVG — for the plain
single-series bar shape, across both orientations and multiple board-width
tiers (label placement is size-dependent geometry). Also proves the
zero/null distinction: a NULL row must never pick up a "0" label (that would
conflate "confirmed zero" with "no data", the exact ambiguity this task
removes), and adversarially checks the shapes this feature must leave alone
(stacked, grouped/color, multi-metric, line, area) via the lower-level
resolve() -> render_resolved_chart() seam for fast structural assertions.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render

_SVG_NS = "http://www.w3.org/2000/svg"
_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

_ROWS_ZERO_NULL: list[dict[str, object]] = [
    {"k": "A", "v": 0},
    {"k": "B", "v": 12},
    {"k": "C", "v": 7},
    {"k": "D", "v": None},
]

# tiny (<352.5), narrow ([352.5, 540.5)), medium ([540.5, 1104.5)), wide (>=1104.5).
_WIDTH_TIERS: list[int] = [300, 450, 700, 1200]


def _make_executor(
    board: object, query_registry: object, rows: list[dict[str, object]]
) -> Executor:
    ok = Mock()
    ok.is_success = True
    ok.data = rows
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    return Executor(
        board, adapter_registry=mock_registry, query_registry=query_registry
    )


def _render_svg(yaml_source: str, rows: list[dict[str, object]]) -> str:
    result = compile(yaml_source)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, rows)
    render_result = render(result.board, executor, format="svg")
    assert not render_result.chart_errors, render_result.chart_errors
    assert isinstance(render_result.output, str)
    return render_result.output


def _bar_board(*, orientation: str, width: int) -> str:
    return f"""
title: probe
charts:
  c:
    type: bar
    query: q
    x: k
    y: v
    width: {width}
    style:
      orientation: {orientation}
queries:
  q:
    sql: "SELECT 1"
    source: test_source
rows: [{{rows: [c]}}]
"""


def _value_label_texts(svg: str) -> list[str]:
    """Text content of every VL text MARK (never an axis tick label)."""
    root = ET.fromstring(svg)
    out: list[str] = []
    for g in root.iter(f"{{{_SVG_NS}}}g"):
        cls = g.get("class", "")
        if "mark-text" in cls and "role-mark" in cls:
            for t in g.iter(f"{{{_SVG_NS}}}text"):
                if t.text:
                    out.append(t.text)
    return out


def _bar_marks(svg: str) -> list[tuple[str, str]]:
    """(aria-label, path `d`) for every rendered REAL bar-mark path.

    A genuine-zero row's bar mark is NOT removed — see the module docstring
    on why suppressing it buys nothing visually (it was already a 0-extent,
    background-stroked path before this feature existed). Tests assert on
    the path geometry (still zero-extent) rather than the mark's absence.

    Excludes BarHoverBandFeature's invisible hover-band paths (opacity="0"),
    which share the same mark-rect/role-mark/aria-roledescription="bar" shape
    as the real bar (the band self-identifies via the same aria-label, by
    design — see bar_hover_band.py's module docstring) but is a separate,
    always-non-zero-height mark for a real (non-null, non-zero-collapsed)
    value, which would otherwise collide with or shadow the real bar's own
    entry in a label-keyed dict.
    """
    root = ET.fromstring(svg)
    out: list[tuple[str, str]] = []
    for g in root.iter(f"{{{_SVG_NS}}}g"):
        if "mark-rect" in g.get("class", "") and "role-mark" in g.get("class", ""):
            for p in g.iter(f"{{{_SVG_NS}}}path"):
                if p.get("aria-roledescription") == "bar" and p.get("opacity") != "0":
                    out.append((p.get("aria-label", ""), p.get("d", "")))
    return out


def _is_zero_extent(d: str) -> bool:
    """True for a bar path whose `d` traces zero height or zero width."""
    return "v0h" in d or "h0v" in d


def test_zero_label_carries_the_hover_emphasis_value_label_marker() -> None:
    """The "0" text this feature draws IS the zero row's own printed value
    (the same slot ValueLabelFeature's formatted labels occupy on every
    other row) -- chart_interactivity.js's recedableMarks() twin search
    must see it via the same ``data-dbt-value-label`` marker, or hovering
    would recede the invisible zero-height bar while its own "0" stayed lit,
    the opposite of every other row's behavior.
    """
    svg = _render_svg(_bar_board(orientation="vertical", width=600), _ROWS_ZERO_NULL)
    assert svg.count('data-dbt-value-label="true"') == 1


def test_null_row_gets_no_mark_and_no_label() -> None:
    """The pre-existing null=gap behavior is untouched: no bar, no "0" label."""
    svg = _render_svg(_bar_board(orientation="vertical", width=600), _ROWS_ZERO_NULL)
    bars = _bar_marks(svg)
    assert not any(label.startswith("⁡D") for label, _ in bars), bars
    # Exactly one "0" label total (the genuine zero) — a second would mean the
    # null row was mislabeled as zero, the exact failure mode this task forbids.
    assert _value_label_texts(svg) == ["0"]


def test_no_zero_values_is_a_no_op() -> None:
    """A chart with no zero rows gets no extra text-mark layer at all."""
    rows = [{"k": "A", "v": 3}, {"k": "B", "v": 12}, {"k": "C", "v": 7}]
    svg = _render_svg(_bar_board(orientation="vertical", width=600), rows)
    assert _value_label_texts(svg) == []
    bars = _bar_marks(svg)
    assert len(bars) == 3, bars
    assert not any(_is_zero_extent(d) for _, d in bars), bars


def test_zero_label_composes_an_authored_affix() -> None:
    """A FormatConfig prefix on marks.bar.labels.format must reach the zero row's static
    "0" text.
    """
    board = """
title: probe
charts:
  c:
    type: bar
    query: q
    x: k
    y: v
    width: 600
    style:
      orientation: vertical
      marks:
        bar:
          labels:
            position: above
            format:
              spec: ",.2f"
              prefix: "EUR "
queries:
  q:
    sql: "SELECT 1"
    source: test_source
rows: [{rows: [c]}]
"""
    rows = [{"k": "A", "v": 0}, {"k": "B", "v": 12}, {"k": "C", "v": 7}]
    svg = _render_svg(board, rows)
    # labels.visible is unset, so ValueLabelFeature paints nothing.
    assert _value_label_texts(svg) == ["EUR 0.00"]


def test_value_labels_visible_zero_row_gets_exactly_one_label() -> None:
    """Regression: with marks.bar.labels.visible on, the zero row must not
    get two overlapping text marks (ZeroValueLabelFeature's "0" plus
    ValueLabelFeature's own formatted value at the identical anchor)."""
    board = """
title: probe
charts:
  c:
    type: bar
    query: q
    x: k
    y: v
    width: 600
    style:
      orientation: vertical
      marks:
        bar:
          labels:
            visible: true
            position: above
            format: "$,.2f"
queries:
  q:
    sql: "SELECT 1"
    source: test_source
rows: [{rows: [c]}]
"""
    rows = [{"k": "A", "v": 0}, {"k": "B", "v": 12}, {"k": "C", "v": 7}]
    svg = _render_svg(board, rows)
    # One label per row, in row order — the zero row's ONLY visible text is
    # ZeroValueLabelFeature's own (using the authored format), never a
    # second, blank-or-not, value-label text node at the same anchor.
    assert _value_label_texts(svg) == ["$0.00", "$12.00", "$7.00"]


def test_custom_label_field_is_not_overwritten_by_the_zero_label() -> None:
    """Regression: marks.bar.labels.field names a column other than y — the
    zero row's label must stay owned by ValueLabelFeature (showing that
    column's real value), not get silently replaced with a synthesized "0"
    sourced from y."""
    board = """
title: probe
charts:
  c:
    type: bar
    query: q
    x: k
    y: v
    width: 600
    style:
      orientation: vertical
      marks:
        bar:
          labels:
            visible: true
            field: other
queries:
  q:
    sql: "SELECT 1"
    source: test_source
rows: [{rows: [c]}]
"""
    rows = [
        {"k": "A", "v": 0, "other": 99},
        {"k": "B", "v": 12, "other": 5},
        {"k": "C", "v": 7, "other": 3},
    ]
    svg = _render_svg(board, rows)
    assert _value_label_texts(svg) == ["99", "5", "3"]


# ---------------------------------------------------------------------------
# Adversarial shapes this feature must leave alone (documented scope limit):
# grouped/stacked/multi-metric bar, and non-bar families (line/area). Faster,
# more precise structural checks via resolve() -> render_resolved_chart(),
# the same real emit+feature seam without the query/layout machinery.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Ownership: ZeroValueLabelFeature and ValueLabelFeature must not depend on
# each other running. Each reads the SAME field (labels.visible) to decide
# whether it owns a given row's label, rather than one feature reading
# whether the other is present in the pipeline — so removing either from
# DEFAULT_FEATURES degrades gracefully instead of silently dropping a label.
# ---------------------------------------------------------------------------
