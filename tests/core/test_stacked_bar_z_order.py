"""Regression: stacked bar z-order must be consistent across vertical and horizontal,
and must default to value-based ordering (largest aggregate at baseline) rather than
alphabetical ordering by color field name.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    BarChartStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

from .conftest import chart_pane

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

# Data where alphabetical ("A" < "B") and value ("B" > "A") orderings diverge.
# "B" has total=20, "A" has total=4 — value ordering puts B at baseline, A on top.
_ENDPOINT_LABEL_DATA = [
    {"cat": "X", "series": "A", "val": 2},
    {"cat": "X", "series": "B", "val": 10},
    {"cat": "Y", "series": "A", "val": 2},
    {"cat": "Y", "series": "B", "val": 10},
]


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


_STACKED_DATA = [
    {"priority": "high", "status": "new", "ticket_count": 10},
    {"priority": "high", "status": "solved", "ticket_count": 8},
    {"priority": "low", "status": "new", "ticket_count": 5},
    {"priority": "low", "status": "solved", "ticket_count": 3},
]


def _stacked_bar_spec(
    orientation: str | None = None,
    stack: str | None = None,
    stack_order: str | None = None,
) -> dict:
    bar_patch_kwargs: dict = {}
    if orientation is not None:
        bar_patch_kwargs["orientation"] = orientation
    if stack is not None:
        bar_patch_kwargs["stack"] = stack
    if stack_order is not None:
        bar_patch_kwargs["stack_order"] = stack_order
    style = BarChartStylePatch(**bar_patch_kwargs) if bar_patch_kwargs else None
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="priority",
        y="ticket_count",
        color="status",
        style=style,
    )
    return generate_vega_lite_spec(
        chart, _STACKED_DATA, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )


class TestStackedBarZOrderConsistency:
    """Default stacked bar z-order uses value-based ordering (largest aggregate at
    baseline) and is consistent across vertical and horizontal orientations.

    Regression: the old implementation sorted alphabetically by color field name,
    which meant the alphabetically-first series sat at the baseline regardless of
    its actual magnitude — wrong default behavior.

    The ordering is implemented by computing ONE Python order (`sorted_series_by_
    stack_order` — the SAME order authority the legend and endpoint labels use)
    and baking it into a `calculate` transform that maps each series to its
    index in that order. `encoding.order` references this pre-computed field (not
    an aggregate), so VL evaluates it per-datum consistently across all bars, and
    the mark-draw order can never drift from the legend/tooltip/label order.
    """

    # --- Default (value-based) behavior ---

    def test_vertical_stacked_emits_precomputed_order_field(self):
        # _STACKED_DATA: status="new" total=15, status="solved" total=11.
        # Value order descending puts "new" (largest) at baseline -> index 0.
        spec = _stacked_bar_spec(stack="zero")
        order = chart_pane(spec)["encoding"].get("order", {})
        assert order.get("field") == "__df_series_order"
        assert order.get("sort") == "ascending"
        assert "aggregate" not in order, (
            "must not use aggregate — VL re-aggregates per bar and causes shuffles"
        )

    def test_vertical_stacked_data_rows_carry_sort_key(self):
        # The sort key is NOT embedded in data rows — spec["transform"] carries a
        # calculate expression that computes it in VL's pipeline from the
        # precomputed Python order. Data (unlike encoding/transform) is hoisted
        # to the spec root, not wrapped, once endpoint labels split the chart
        # into a pane.
        spec = _stacked_bar_spec(stack="zero")
        # Data rows must NOT contain the sort key field (clean source data)
        for row in spec["data"]["values"]:
            assert "__df_series_order" not in row
        transforms = chart_pane(spec).get("transform", [])
        calc = next((t for t in transforms if "calculate" in t), None)
        assert calc is not None, "expected a calculate transform in spec['transform']"
        assert calc["as"] == "__df_series_order"
        # "new" (largest sum) is baseline -> index 0; "solved" -> index 1.
        assert '"new" ? 0' in calc["calculate"]
        assert '"solved" ? 1' in calc["calculate"]

    def test_horizontal_stacked_emits_precomputed_order_field(self):
        spec = _stacked_bar_spec(orientation="horizontal", stack="zero")
        order = chart_pane(spec)["encoding"].get("order", {})
        assert order.get("field") == "__df_series_order"
        assert order.get("sort") == "ascending"
        assert "aggregate" not in order

    def test_vertical_and_horizontal_share_same_order_spec(self):
        v = _stacked_bar_spec(stack="zero")
        h = _stacked_bar_spec(orientation="horizontal", stack="zero")
        assert chart_pane(v)["encoding"].get("order") == chart_pane(h)["encoding"].get(
            "order"
        ), (
            "vertical and horizontal stacked bars must emit the same order spec "
            "so both orientations place the same series at the baseline"
        )

    def test_normalize_stack_also_pins_order(self):
        spec = _stacked_bar_spec(stack="normalize")
        order = chart_pane(spec)["encoding"].get("order", {})
        assert order.get("field") == "__df_series_order"
        assert order.get("sort") == "ascending"

    # --- stack_order knob ---

    def test_stack_order_value_emits_precomputed_field(self):
        spec = _stacked_bar_spec(stack="zero", stack_order="value")
        order = chart_pane(spec)["encoding"].get("order", {})
        assert order.get("field") == "__df_series_order"
        assert order.get("sort") == "ascending"
        assert "aggregate" not in order

    def test_stack_order_alphabetical_emits_precomputed_index_form(self):
        spec = _stacked_bar_spec(stack="zero", stack_order="alphabetical")
        pane = chart_pane(spec)
        order = pane["encoding"].get("order", {})
        # Alphabetical stack_order still routes through the ONE shared order
        # authority — no more field-based nominal ascending-sort special case.
        assert order.get("field") == "__df_series_order"
        assert order.get("sort") == "ascending"
        transforms = pane.get("transform", [])
        calc = next((t for t in transforms if "calculate" in t), None)
        assert calc is not None
        # Alphabetical: "new" at baseline (index 0), "solved" on top (index 1).
        assert '"new" ? 0' in calc["calculate"]
        assert '"solved" ? 1' in calc["calculate"]

    def test_stack_order_data_emits_calculate_transform(self):
        # stack_order='data': VL without encoding.order uses alphabetical domain order
        # (not data row order) for nominal fields. We emit a calculate transform that
        # assigns each series its global first-encounter index so VL stacks in data order.
        # _STACKED_DATA: status "new" appears before "solved" in data rows.
        spec = _stacked_bar_spec(stack="zero", stack_order="data")
        pane = chart_pane(spec)
        order = pane["encoding"].get("order", {})
        assert order.get("field") == "__df_series_order"
        assert order.get("sort") == "ascending"
        assert "aggregate" not in order
        transforms = pane.get("transform", [])
        calc = next((t for t in transforms if "calculate" in t), None)
        assert calc is not None, "expected a calculate transform for stack_order='data'"
        assert calc["as"] == "__df_series_order"
        # "new" appears first in _STACKED_DATA → index 0 in the calculate expression
        assert '"new" ? 0' in calc["calculate"]
        assert '"solved" ? 1' in calc["calculate"]

    # --- Gate conditions (order and color.sort must NOT be emitted) ---

    def test_stack_none_does_not_add_order_or_color_sort(self):
        chart = BarChart(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="priority",
            y="ticket_count",
            color="status",
            stack="none",
        )
        spec = generate_vega_lite_spec(chart, _STACKED_DATA)
        assert "order" not in spec["encoding"]
        # No sort key injected into data rows for non-stacked charts
        for row in spec["data"]["values"]:
            assert "__df_series_order" not in row

    def test_no_color_field_does_not_add_order(self):
        data = [{"priority": "high", "ticket_count": 10}]
        chart = BarChart(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="priority",
            y="ticket_count",
        )
        spec = generate_vega_lite_spec(chart, data)
        assert "order" not in spec["encoding"]


class TestEndpointLabelStackOrderPlumbing:
    """Regression: endpoint label anchors must respect stack_order.

    When stack_order='value' (default), the series with the largest aggregate
    sits at the baseline. The endpoint label resolver must compute midpoints in
    the same order as _apply_stacked_bar_z_order, otherwise labels anchor on
    the wrong segments when alphabetical and value orderings diverge.

    Data: series "A" total=4, "B" total=20 (across two x-categories).
    Value order → B at baseline, A on top.
    Alphabetical order → A at baseline, B on top.

    In a vertical bar chart, y=0 is the baseline and higher y values are
    higher on the chart. For value ordering:
      - B (larger) at baseline: midpoint of [0, 10] = 5.0
      - A (smaller) on top: midpoint of [10, 12] = 11.0
      - Therefore A_y (11.0) > B_y (5.0)

    For alphabetical ordering (the bug):
      - A (alpha-first) at baseline: midpoint = [0, 2] = 1.0
      - B on top: midpoint = [2, 12] = 7.0
      - Therefore B_y (7.0) > A_y (1.0) — labels on wrong segments
    """

    def _make_spec(self, stack_order: str | None = None) -> dict:
        bar_kwargs: dict = {
            "orientation": "vertical",
            "stack": "zero",
            "endpoint_labels": {"visible": True},
        }
        if stack_order is not None:
            bar_kwargs["stack_order"] = stack_order
        chart = BarChart(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="cat",
            y="val",
            color="series",
            style=BarChartStylePatch(**bar_kwargs),
        )
        return generate_vega_lite_spec(
            chart,
            _ENDPOINT_LABEL_DATA,
            width=400,
            height=300,
            board_style=_BOARD_STYLE,
            chart_style_context=_BOARD_CTX,
        )

    def _label_y_by_series(self, spec: dict) -> dict[str, float]:
        """Extract {series: __y} from the hconcat label pane."""
        assert "hconcat" in spec, f"expected hconcat wrapper, got keys: {list(spec)}"
        label_pane = spec["hconcat"][1]
        rows = label_pane["data"]["values"]
        return {row["series"]: row["__y"] for row in rows}

    def test_default_stack_order_value_labels_match_value_based_stacking(self):
        # Default (stack_order=None → "value"): B has larger sum (20 vs 4),
        # so B anchors at the baseline. Baseline is y=0; the midpoint of B's
        # segment [0, 10] is 5.0. A sits on top; its midpoint is [10, 12] = 11.0.
        # Therefore A_y (11.0) > B_y (5.0).
        spec = self._make_spec(stack_order=None)
        label_y = self._label_y_by_series(spec)
        assert "A" in label_y and "B" in label_y, (
            f"missing series in label data: {label_y}"
        )
        assert label_y["A"] > label_y["B"], (
            f"value ordering: A (smaller series) should be on top (higher y) than B "
            f"(larger series at baseline). Got A_y={label_y['A']}, B_y={label_y['B']}"
        )

    def test_explicit_stack_order_value_labels_match_value_based_stacking(self):
        # Explicit stack_order='value' must behave identically to default.
        spec = self._make_spec(stack_order="value")
        label_y = self._label_y_by_series(spec)
        assert label_y["A"] > label_y["B"], (
            f"stack_order='value': A should be on top (higher y). "
            f"Got A_y={label_y['A']}, B_y={label_y['B']}"
        )

    def test_stack_order_alphabetical_labels_match_alphabetical_stacking(self):
        # stack_order='alphabetical': A comes first alphabetically → A at baseline.
        # A's midpoint = [0, 2] midpoint = 1.0. B's midpoint = [2, 12] midpoint = 7.0.
        # Therefore B_y (7.0) > A_y (1.0).
        spec = self._make_spec(stack_order="alphabetical")
        label_y = self._label_y_by_series(spec)
        assert label_y["B"] > label_y["A"], (
            f"stack_order='alphabetical': B should be on top (higher y) than A "
            f"(A at baseline). Got A_y={label_y['A']}, B_y={label_y['B']}"
        )

    def test_stack_order_data_labels_follow_row_insertion_order(self):
        # Adversarial: globally A comes first (X-A is row 0), but at last_x=Y,
        # B appears before A. The old list(values_at_last) code would pick B first
        # (wrong baseline), flipping labels. Correct fix: use global first-encounter.
        # VL domain: [A, B] → A at baseline, B on top.
        # Midpoints: A=[0,2]=1.0, B=[2,12]=7.0 → B_y > A_y.
        adversarial_data = [
            {"cat": "X", "series": "A", "val": 2},
            {"cat": "X", "series": "B", "val": 10},
            # At last_x=Y, B comes before A — flipped from global order
            {"cat": "Y", "series": "B", "val": 10},
            {"cat": "Y", "series": "A", "val": 2},
        ]
        chart = BarChart(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="cat",
            y="val",
            color="series",
            style=BarChartStylePatch(
                orientation="vertical",
                stack="zero",
                stack_order="data",
                endpoint_labels={"visible": True},
            ),
        )
        spec = generate_vega_lite_spec(
            chart,
            adversarial_data,
            width=400,
            height=300,
            board_style=_BOARD_STYLE,
            chart_style_context=_BOARD_CTX,
        )
        label_y = self._label_y_by_series(spec)
        assert label_y["B"] > label_y["A"], (
            f"stack_order='data': A is first encountered globally so it's at baseline, "
            f"B on top → B_y should exceed A_y. "
            f"Got A_y={label_y['A']}, B_y={label_y['B']}. "
            f"Using last_x row order (B before A at Y) would flip this."
        )

    def test_mark_tooltip_true_not_data_content(self):
        """mark.tooltip must be True (encoding-content), never {content: data}.

        VL's mark.tooltip=True generates encoding-based tooltips that explicitly
        exclude the 'order' channel, so __df_series_order never surfaces to users.
        If this flips to {content: data}, all data fields (including __df_series_order)
        appear in hover tooltips — a UX regression.
        """
        spec = self._make_spec()
        # Spec structure: hconcat[0] is chart pane (layer), hconcat[1] is label pane.
        chart_pane = spec.get("hconcat", [spec])[0]
        # Bar mark is the first layer.
        layers = chart_pane.get("layer", [chart_pane])
        bar_layer = next(
            (lay for lay in layers if lay.get("mark", {}).get("type") == "bar"),
            layers[0],
        )
        mark = bar_layer.get("mark", {})
        tooltip = mark.get("tooltip") if isinstance(mark, dict) else None
        assert tooltip is True or tooltip == {"content": "encoding"}, (
            f"mark.tooltip must be True (encoding-content) so __df_series_order "
            f"never appears in hover tooltips. Got: {tooltip!r}"
        )


class TestGlobalColorDomainSort:
    """The stack order sort key must be global, not per-bar.

    Regression for the encoding.order aggregate bug: the OLD (pre-single-
    authority) implementation risked VL evaluating encoding.order with
    aggregate on the measure field per stack-group (per bar), not globally.
    When the globally-largest series is NOT the locally-largest at some bar,
    a per-bar aggregate would flip the order — segments shuffle month-to-month.

    The fix computes the global sum per color group ONCE in Python
    (`sorted_series_by_stack_order` — the same order authority the legend and
    endpoint labels use) and bakes the resulting order into a `calculate`
    transform (no VL-side aggregate), so it's evaluated per-datum
    consistently: B is always ranked ahead of A, in every bar.
    """

    # Data crafted so the globally-largest series (B, total=15) is NOT the
    # locally-largest at the first bar (Jan: A=10 > B=5), but IS globally largest.
    # With a broken per-bar aggregate, Jan would put A at baseline and Feb would
    # put B at baseline — shuffling. With the precomputed global order, B is
    # always ranked first (baseline) in every bar.
    _TIME_DATA = [
        {"month": "Jan", "segment": "A", "amount": 10},
        {"month": "Jan", "segment": "B", "amount": 5},
        {"month": "Feb", "segment": "A", "amount": 3},
        {"month": "Feb", "segment": "B", "amount": 10},
    ]

    def _spec(self) -> dict:
        chart = BarChart(
            id="t",
            query=SqlQuery(sql="SELECT 1", source="t"),
            query_name="q",
            type="bar",
            x="month",
            y="amount",
            color="segment",
            style=BarChartStylePatch(stack="zero"),
        )
        return generate_vega_lite_spec(
            chart,
            self._TIME_DATA,
            board_style=_BOARD_STYLE,
            chart_style_context=_BOARD_CTX,
        )

    def test_order_field_is_precomputed_not_aggregate(self):
        """encoding.order must reference a pre-computed field, not an aggregate."""
        spec = self._spec()
        order = chart_pane(spec)["encoding"].get("order", {})
        assert order.get("field") == "__df_series_order", (
            f"expected encoding.order.field == '__df_series_order'; got {order!r}. "
            "aggregate-based encoding.order evaluates per-bar, causing shuffles."
        )
        assert "aggregate" not in order, (
            "aggregate in encoding.order causes per-bar re-ranking; must be absent"
        )

    def test_sort_key_reflects_global_sum(self):
        """The calculate transform must rank B (globally-largest) ahead of A."""
        spec = self._spec()
        # Data rows must NOT contain the sort key (clean source data). Data
        # (unlike encoding/transform) is hoisted to the spec root, not
        # wrapped, once endpoint labels split the chart into a pane.
        for row in spec["data"]["values"]:
            assert "__df_series_order" not in row
        transforms = chart_pane(spec).get("transform", [])
        calc = next((t for t in transforms if "calculate" in t), None)
        assert calc is not None, "expected a calculate transform in spec['transform']"
        assert calc["as"] == "__df_series_order"
        # B (global sum 15) is ranked ahead of A (global sum 13) -> index 0.
        assert '"B" ? 0' in calc["calculate"]
        assert '"A" ? 1' in calc["calculate"]


_TEMPORAL_MONTHS = ("2024-01-01", "2024-02-01", "2024-03-01")
# First-encounter order [zeta, mid, alpha] differs from alphabetical
# [alpha, mid, zeta] and from value order [mid, alpha, zeta].
_SHUFFLED_ROWS = [
    {"month": "2024-02-01", "s": "zeta", "v": 1},
    {"month": "2024-01-01", "s": "zeta", "v": 1},
    {"month": "2024-03-01", "s": "mid", "v": 10},
    {"month": "2024-01-01", "s": "alpha", "v": 5},
    {"month": "2024-02-01", "s": "mid", "v": 10},
    {"month": "2024-03-01", "s": "alpha", "v": 5},
    {"month": "2024-01-01", "s": "mid", "v": 10},
    {"month": "2024-03-01", "s": "zeta", "v": 1},
    {"month": "2024-02-01", "s": "alpha", "v": 5},
]
_FIRST_PERIOD_GAP_ROWS = [
    row for row in _SHUFFLED_ROWS if (row["month"], row["s"]) != ("2024-01-01", "mid")
]


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (_SHUFFLED_ROWS, ("zeta", "mid", "alpha")),
        (_FIRST_PERIOD_GAP_ROWS, ("zeta", "mid", "alpha")),
    ],
    ids=["shuffled", "series-missing-from-first-period"],
)
def test_stack_order_data_on_temporal_x_follows_first_appearance(
    rows: list[dict], expected: tuple[str, ...], orientation: str
):
    """Gap-fill rebuilds temporal rows as buckets x sorted(series); the stack
    order must still come from the query's own row order."""
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="v",
        color="s",
        style=BarChartStylePatch(
            stack="zero", stack_order="data", orientation=orientation
        ),
    )
    spec = generate_vega_lite_spec(
        chart, rows, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    pane = chart_pane(spec)
    field = pane["encoding"]["order"]["field"]
    calc = next(t["calculate"] for t in pane["transform"] if t.get("as") == field)
    ranks = {name: calc.index(f'"{name}" ?') for name in expected}
    assert sorted(ranks, key=ranks.__getitem__) == list(expected), calc


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_wide_stack_order_data_on_temporal_x_follows_first_appearance(
    orientation: str,
):
    """Wide ``y: [...]`` with ``color:`` ranks the dimension over the query's own
    rows; gap-fill's buckets x sorted(dimension) rebuild would make it alphabetical."""
    rows = [
        {"month": month, "region": region, "a": 1, "b": 2}
        for month in ("2024-01-01", "2024-02-01", "2024-04-01")
        for region in ("west", "east")
    ]
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y=["a", "b"],
        color="region",
        style=BarChartStylePatch(
            stack="zero",
            stack_order="data",
            orientation=orientation,
            axis_x={"time_unit": "yearmonth"},
        ),
    )
    spec = generate_vega_lite_spec(
        chart, rows, board_style=_BOARD_STYLE, chart_style_context=_BOARD_CTX
    )
    pane = chart_pane(spec)
    field = pane["encoding"]["order"]["field"]
    calc = next(t["calculate"] for t in pane["transform"] if t.get("as") == field)
    assert calc.index('"west') < calc.index('"east'), calc
