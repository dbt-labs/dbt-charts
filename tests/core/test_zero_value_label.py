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

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

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


def _categorical_axis_order(svg: str, axis: str) -> list[str]:
    """Rendered category order along the given VL axis ('x' or 'y'), read
    from the axis-label text nodes in DOM order (paint order — left-to-right
    for x, top-to-bottom for y).

    Scoped to ``role-axis-label`` groups specifically, not every text node
    under the axis group — the axis also carries a ``role-axis-title`` text
    node (the axis title, visible by default) that is not a category tick
    and must not be picked up as one.
    """
    root = ET.fromstring(svg)
    prefix = "X-axis" if axis == "x" else "Y-axis"
    for g in root.iter(f"{{{_SVG_NS}}}g"):
        if (
            g.get("aria-label", "").startswith(prefix)
            and g.get("aria-roledescription") == "axis"
        ):
            labels: list[str] = []
            for label_g in g.iter(f"{{{_SVG_NS}}}g"):
                if "role-axis-label" not in label_g.get("class", ""):
                    continue
                labels.extend(
                    t.text for t in label_g.iter(f"{{{_SVG_NS}}}text") if t.text
                )
            return labels
    raise AssertionError(f"no {axis}-axis found in svg")


@pytest.mark.parametrize("width", _WIDTH_TIERS)
def test_vertical_bar_zero_gets_direct_label_no_mark(width: int) -> None:
    svg = _render_svg(_bar_board(orientation="vertical", width=width), _ROWS_ZERO_NULL)
    bars = dict(_bar_marks(svg))
    zero_bar = next(d for label, d in bars.items() if "v: 0" in label)
    assert _is_zero_extent(zero_bar), zero_bar
    assert any("v: 12" in label for label in bars), bars
    assert any("v: 7" in label for label in bars), bars
    labels = _value_label_texts(svg)
    assert labels == ["0"], labels


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


@pytest.mark.parametrize("width", _WIDTH_TIERS)
def test_horizontal_bar_zero_gets_direct_label_no_mark(width: int) -> None:
    svg = _render_svg(
        _bar_board(orientation="horizontal", width=width), _ROWS_ZERO_NULL
    )
    bars = dict(_bar_marks(svg))
    zero_bar = next(d for label, d in bars.items() if "v: 0" in label)
    assert _is_zero_extent(zero_bar), zero_bar
    assert any("v: 12" in label for label in bars), bars
    assert any("v: 7" in label for label in bars), bars
    labels = _value_label_texts(svg)
    assert labels == ["0"], labels


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


def test_all_zero_values_all_get_labels() -> None:
    rows = [{"k": "A", "v": 0}, {"k": "B", "v": 0}, {"k": "C", "v": 0}]
    svg = _render_svg(_bar_board(orientation="vertical", width=600), rows)
    bars = _bar_marks(svg)
    assert len(bars) == 3, bars
    assert all(_is_zero_extent(d) for _, d in bars), bars
    assert _value_label_texts(svg) == ["0", "0", "0"]


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


def test_negative_value_alongside_zero() -> None:
    rows = [{"k": "A", "v": -5}, {"k": "B", "v": 0}, {"k": "C", "v": 7}]
    svg = _render_svg(_bar_board(orientation="vertical", width=600), rows)
    bars = dict(_bar_marks(svg))
    assert any(label.startswith("⁡A;") for label in bars), bars
    assert any("v: 7" in label for label in bars), bars
    zero_bar = next(d for label, d in bars.items() if "v: 0" in label)
    assert _is_zero_extent(zero_bar), zero_bar
    assert _value_label_texts(svg) == ["0"]


def test_horizontal_bar_default_sort_survives_a_zero_row() -> None:
    """Regression: an earlier revision of this feature suppressed the zero
    row's mark via a per-layer VL `filter` transform. A `transform` array on
    ANY sub-layer — even one that changes nothing about what other layers
    draw — makes vl-convert silently discard the shared categorical axis's
    `sort` and fall back to alphabetical order. Horizontal bar's default
    sort (descending by measure) is the most common case this would break."""
    no_zero = _render_svg(
        _bar_board(orientation="horizontal", width=600),
        [
            {"k": "D", "v": 3},
            {"k": "B", "v": 12},
            {"k": "A", "v": 5},
            {"k": "C", "v": 7},
        ],
    )
    with_zero = _render_svg(
        _bar_board(orientation="horizontal", width=600),
        [
            {"k": "D", "v": 3},
            {"k": "B", "v": 12},
            {"k": "A", "v": 0},
            {"k": "C", "v": 7},
        ],
    )
    assert _categorical_axis_order(no_zero, "y") == ["B", "C", "A", "D"]
    # A's value dropped from 5 to 0 — still the smallest, so it moves to last;
    # B/C/D keep their same relative descending order either way.
    assert _categorical_axis_order(with_zero, "y") == ["B", "C", "D", "A"]


def test_vertical_bar_authored_sort_survives_a_zero_row() -> None:
    """Same regression as the horizontal-default case, for an explicit
    authored `sort:` on a vertical bar."""
    board = """
title: probe
charts:
  c:
    type: bar
    query: q
    x: k
    y: v
    width: 600
    sort: {by: v, order: desc}
    style:
      orientation: vertical
queries:
  q:
    sql: "SELECT 1"
    source: test_source
rows: [{rows: [c]}]
"""
    with_zero = _render_svg(
        board,
        [
            {"k": "D", "v": 3},
            {"k": "B", "v": 12},
            {"k": "A", "v": 0},
            {"k": "C", "v": 7},
        ],
    )
    assert _categorical_axis_order(with_zero, "x") == ["B", "C", "D", "A"]


# ---------------------------------------------------------------------------
# Adversarial shapes this feature must leave alone (documented scope limit):
# grouped/stacked/multi-metric bar, and non-bar families (line/area). Faster,
# more precise structural checks via resolve() -> render_resolved_chart(),
# the same real emit+feature seam without the query/layout machinery.
# ---------------------------------------------------------------------------


def _spec_layers(chart: object, data: list[dict[str, object]]) -> list[dict]:
    """The chart's own VL sub-layer list.

    A stacked/grouped bar with a color legend wraps the chart spec in a
    `vconcat` (legend row + chart row) rather than emitting `layer` at the
    top level — this digs into that wrapper so a stacked-bar fixture's
    layers are actually inspected, not silently read as `[]`.
    """
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    payload = render_resolved_chart(
        resolved, data, _BOARD_STYLE, width=600, height=300
    ).payload
    assert isinstance(payload, dict)
    if "layer" in payload:
        return payload["layer"]
    for sub in payload.get("vconcat", []):
        if "layer" in sub:
            return sub["layer"]
    raise AssertionError(f"no layer array found in payload keys: {sorted(payload)}")


def _has_zero_label_layer(layers: list[dict], measure_field: str = "y") -> bool:
    """True if any sub-layer is the ZeroValueLabelFeature's own text mark —
    identified by the ``text`` encoding condition it actually emits (blank
    every row except the zero one), not a ``transform``. The feature never
    carries a ``transform`` on its layer (see the module docstring in
    ``zero_value_label.py`` for why: any sub-layer transform makes vl-convert
    silently drop the shared categorical axis's sort)."""
    expected_test = f"datum['{measure_field}'] != 0"
    for layer in layers:
        mark = layer.get("mark")
        if not (isinstance(mark, dict) and mark.get("type") == "text"):
            continue
        encoding = layer.get("encoding", {})
        text_enc = encoding.get("text") if isinstance(encoding, dict) else None
        if not isinstance(text_enc, dict):
            continue
        condition = text_enc.get("condition")
        if isinstance(condition, dict) and condition.get("test") == expected_test:
            return True
    return False


def test_has_zero_label_layer_helper_detects_the_feature_firing() -> None:
    """Positive control for `_has_zero_label_layer`: the plain single-series
    bar shape the feature DOES fire for must be detected, so a future
    mechanism change breaks this helper loudly instead of silently
    disarming the adversarial-shape tests below that depend on it."""
    chart = BarChart(id="t", type="bar", x="cat", y="y", query_name="q")
    data = [{"cat": "A", "y": 0}, {"cat": "B", "y": 5}]
    layers = _spec_layers(chart, data)
    assert _has_zero_label_layer(layers), layers


def test_stacked_bar_zero_segment_is_not_touched() -> None:
    """Stacked bar: this feature doesn't fire (mid-stack zero-label position
    is undefined) — the pre-existing 0-height segment mark still renders."""
    chart = BarChart(
        id="t",
        type="bar",
        x="cat",
        y="y",
        color="series",
        query_name="q",
        stack="zero",
    )
    data = [
        {"cat": "A", "y": 5, "series": "s1"},
        {"cat": "A", "y": 3, "series": "s2"},
        {"cat": "B", "y": 0, "series": "s1"},
        {"cat": "B", "y": 4, "series": "s2"},
    ]
    layers = _spec_layers(chart, data)
    assert not _has_zero_label_layer(layers), layers


def test_grouped_color_bar_zero_is_not_touched() -> None:
    chart = BarChart(
        id="t",
        type="bar",
        x="cat",
        y="y",
        color="series",
        query_name="q",
        stack="none",
    )
    data = [
        {"cat": "A", "y": 5, "series": "s1"},
        {"cat": "A", "y": 3, "series": "s2"},
        {"cat": "B", "y": 0, "series": "s1"},
        {"cat": "B", "y": 4, "series": "s2"},
    ]
    layers = _spec_layers(chart, data)
    assert not _has_zero_label_layer(layers), layers


def test_multi_metric_bar_zero_is_not_touched() -> None:
    chart = BarChart(id="t", type="bar", x="cat", y=["y1", "y2"], query_name="q")
    data = [
        {"cat": "A", "y1": 0, "y2": 4},
        {"cat": "B", "y1": 5, "y2": 2},
    ]
    layers = _spec_layers(chart, data)
    assert not _has_zero_label_layer(layers), layers


def test_line_chart_zero_value_is_not_touched() -> None:
    """Line/area don't share bar's 0-height-mark defect (a zero-value vertex
    still renders visibly), so this feature scopes to bar only."""
    from dbt_charts.core.render.chart.features.zero_value_label import (
        ZeroValueLabelFeature,
    )

    chart = LineChart(id="t", type="line", x="cat", y="y", query_name="q")
    data = [{"cat": "A", "y": 0}, {"cat": "B", "y": 5}]
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    assert ZeroValueLabelFeature().applies_to(resolved) is False


def test_area_chart_zero_value_is_not_touched() -> None:
    from dbt_charts.core.render.chart.features.zero_value_label import (
        ZeroValueLabelFeature,
    )

    chart = AreaChart(id="t", type="area", x="cat", y="y", query_name="q")
    data = [{"cat": "A", "y": 0}, {"cat": "B", "y": 5}]
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    assert ZeroValueLabelFeature().applies_to(resolved) is False


# ---------------------------------------------------------------------------
# Ownership: ZeroValueLabelFeature and ValueLabelFeature must not depend on
# each other running. Each reads the SAME field (labels.visible) to decide
# whether it owns a given row's label, rather than one feature reading
# whether the other is present in the pipeline — so removing either from
# DEFAULT_FEATURES degrades gracefully instead of silently dropping a label.
# ---------------------------------------------------------------------------


def test_disabling_zero_feature_does_not_delete_an_authored_value_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: with `marks.bar.labels.visible: true` authored, the zero
    row's own value label must render whether or not ZeroValueLabelFeature is
    in the pipeline. An earlier revision coupled the two features through a
    shared predicate that any feature reading it could satisfy independently
    of whether ZeroValueLabelFeature actually ran — removing it from
    DEFAULT_FEATURES silently deleted the zero row's ValueLabelFeature label
    too, instead of leaving ValueLabelFeature's own output untouched."""
    from dbt_charts.core.render.chart import session as session_module
    from dbt_charts.core.render.chart.features import DEFAULT_FEATURES
    from dbt_charts.core.render.chart.features.zero_value_label import (
        ZeroValueLabelFeature,
    )

    reduced = [f for f in DEFAULT_FEATURES if not isinstance(f, ZeroValueLabelFeature)]
    monkeypatch.setattr(session_module, "DEFAULT_FEATURES", reduced)

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
queries:
  q:
    sql: "SELECT 1"
    source: test_source
rows: [{rows: [c]}]
"""
    rows = [{"k": "A", "v": 0}, {"k": "B", "v": 12}, {"k": "C", "v": 7}]
    svg = _render_svg(board, rows)
    assert _value_label_texts(svg) == ["0", "12", "7"]
