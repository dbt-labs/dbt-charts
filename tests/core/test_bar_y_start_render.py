"""A bar with ``y_start`` spans from that column to ``y`` instead of from zero.

Covers the resolve and Vega-Lite halves: the span channel in both
orientations, no stacking, no forced zero, query order kept, signed change
labels, the shared domain with a bar layer, and the data rules that need rows.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_style(get_theme_style())
_BOARD_CTX = resolve_chart_style_context(get_theme_style())

# Deliberately out of value order, so a value sort would reorder them.
_RANGES = [
    {"category": "B", "low": 120, "high": 148, "mid": 130},
    {"category": "A", "low": 150, "high": 190, "mid": 170},
    {"category": "C", "low": 110, "high": 125, "mid": 118},
]

_BRIDGE = [
    {"step": "Start", "start_value": 0, "end_value": 120, "direction": "Total"},
    {"step": "Gain", "start_value": 120, "end_value": 148, "direction": "Increase"},
    {"step": "Loss", "start_value": 148, "end_value": 126, "direction": "Decrease"},
    {"step": "End", "start_value": 0, "end_value": 126, "direction": "Total"},
]


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _spec(
    data: list[dict[str, Any]],
    *,
    x: str = "category",
    y: str = "high",
    y_start: str | None = "low",
    style: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": x,
            "y": y,
            "y_start": y_start,
            "style": {"orientation": "vertical", **(style or {})},
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            **fields,
        }
    )
    return generate_vega_lite_spec(
        chart,
        data,
        width=400,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CTX,
    )


def _bar_encodings(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Every encoding that draws a bar mark, merged with its parents' encodings."""
    found: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], inherited: dict[str, Any]) -> None:
        enc = {**inherited, **node.get("encoding", {})}
        mark = node.get("mark")
        mark_type = mark.get("type") if isinstance(mark, dict) else mark
        if mark_type == "bar":
            found.append(enc)
        for child in node.get("layer", []):
            walk(child, enc)

    walk(spec, {})
    return found


def _texts(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Every text-mark node, with the transforms visible to it."""
    found: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], transforms: list[dict[str, Any]]) -> None:
        seen = [*transforms, *node.get("transform", [])]
        mark = node.get("mark")
        mark_type = mark.get("type") if isinstance(mark, dict) else mark
        if mark_type == "text":
            found.append({**node, "transform": seen})
        for child in node.get("layer", []):
            walk(child, seen)

    walk(spec, [])
    return found


class TestSpanChannel:
    def test_vertical_bar_binds_y_start_to_y2(self):
        (enc,) = _bar_encodings(_spec(_RANGES))
        assert enc["y"]["field"] == "high"
        assert enc["y2"] == {"field": "low"}

    def test_horizontal_bar_binds_y_start_to_x2(self):
        (enc,) = _bar_encodings(_spec(_RANGES, style={"orientation": "horizontal"}))
        assert enc["x"]["field"] == "high"
        assert enc["x2"] == {"field": "low"}
        assert "y2" not in enc

    def test_bar_without_y_start_has_no_span_channel(self):
        (enc,) = _bar_encodings(_spec(_RANGES, y_start=None))
        assert "y2" not in enc and "x2" not in enc

    @pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
    def test_span_is_never_stacked(self, orientation):
        spec = _spec(
            _BRIDGE,
            x="step",
            y="end_value",
            y_start="start_value",
            color="direction",
            style={"orientation": orientation, "overlap": "full"},
        )
        measure = "y" if orientation == "vertical" else "x"
        for enc in _bar_encodings(spec):
            assert enc[measure].get("stack") is None

    def test_color_split_span_keeps_its_span_channel(self):
        spec = _spec(
            _BRIDGE,
            x="step",
            y="end_value",
            y_start="start_value",
            color="direction",
            style={"overlap": "full"},
        )
        for enc in _bar_encodings(spec):
            assert enc["y2"] == {"field": "start_value"}


class TestDomain:
    @pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
    def test_zero_is_not_forced(self, orientation):
        spec = _spec(_RANGES, style={"orientation": orientation})
        measure = "y" if orientation == "vertical" else "x"
        (enc,) = _bar_encodings(spec)
        scale = enc[measure].get("scale", {})
        assert scale.get("zero") is not True
        ticks = enc[measure]["axis"].get("values")
        assert ticks, "resolve bakes a tick ladder"
        assert min(ticks) > 0
        assert min(ticks) <= 110

    def test_authored_zero_pin_is_honored(self):
        spec = _spec(
            _RANGES, style={"axis_y": {"scale": {"continuous": {"zero": True}}}}
        )
        (enc,) = _bar_encodings(spec)
        assert 0 in enc["y"]["axis"]["values"]

    def test_bar_layer_span_joins_the_domain(self):
        spec = _spec(
            _RANGES,
            layers=[{"type": "bar", "y": "mid", "y_start": "low"}],
        )
        encs = _bar_encodings(spec)
        assert any(e.get("y2") == {"field": "low"} for e in encs[1:])
        ticks = encs[0]["y"]["axis"]["values"]
        assert min(ticks) > 0


class TestOrder:
    @pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
    def test_category_axis_keeps_query_order(self, orientation):
        spec = _spec(_RANGES, style={"orientation": orientation})
        category = "x" if orientation == "vertical" else "y"
        (enc,) = _bar_encodings(spec)
        sort = enc[category].get("sort")
        assert sort is None or sort == ["B", "A", "C"]

    def test_authored_sort_still_wins(self):
        spec = _spec(
            _RANGES,
            style={"orientation": "horizontal"},
            sort={"by": "high", "order": "desc"},
        )
        (enc,) = _bar_encodings(spec)
        assert enc["y"].get("sort") not in (None, ["B", "A", "C"])


class TestValueLabels:
    def _label_expr(self, spec: dict[str, Any]) -> str:
        (text,) = _texts(spec)
        field = text["encoding"]["text"]["field"]
        (calc,) = [t for t in text["transform"] if t.get("as") == field]
        return calc["calculate"]

    def test_span_label_is_the_signed_change(self):
        spec = _spec(
            _BRIDGE,
            x="step",
            y="end_value",
            y_start="start_value",
            style={
                "number_format": "$,.0f",
                "marks": {"bar": {"labels": {"visible": True}}},
            },
        )
        expr = self._label_expr(spec)
        assert "datum['end_value'] - datum['start_value']" in expr
        assert "'+'" in expr and "'\\u2212'" in expr
        # A row that starts at zero prints its plain value.
        assert "datum['start_value'] == 0" in expr

    def test_labels_field_still_overrides(self):
        spec = _spec(
            _BRIDGE,
            x="step",
            y="end_value",
            y_start="start_value",
            style={"marks": {"bar": {"labels": {"visible": True, "field": "step"}}}},
        )
        (text,) = _texts(spec)
        assert text["encoding"]["text"]["field"] == "step"


class TestDataRules:
    def test_null_start_raises_naming_the_column_and_row(self):
        rows = [*_RANGES, {"category": "D", "low": None, "high": 140, "mid": 1}]
        with pytest.raises(CompilationError, match=r"low.*row 4|row 4.*low"):
            _spec(rows)

    def test_start_must_be_numeric_when_y_is(self):
        rows = [{**r, "low": dt.date(2026, 1, 1)} for r in _RANGES]
        with pytest.raises(CompilationError, match="low"):
            _spec(rows)


_PLAN = [
    {
        "task": "Scope",
        "start_date": dt.date(2026, 1, 5),
        "finish_date": dt.date(2026, 1, 23),
    },
    {
        "task": "Design",
        "start_date": dt.date(2026, 1, 19),
        "finish_date": dt.date(2026, 2, 20),
    },
    {
        "task": "Build",
        "start_date": dt.date(2026, 2, 9),
        "finish_date": dt.date(2026, 4, 10),
    },
]


class TestDateSpans:
    def test_gantt_draws_a_temporal_span(self):
        spec = _spec(
            _PLAN,
            x="task",
            y="finish_date",
            y_start="start_date",
            style={"orientation": "horizontal"},
        )
        (enc,) = _bar_encodings(spec)
        assert enc["x"]["field"] == "finish_date"
        assert enc["x"]["type"] == "temporal"
        assert enc["x2"] == {"field": "start_date"}
        assert enc["y"].get("sort") in (None, ["Scope", "Design", "Build"])

    def test_date_y_without_y_start_still_refuses(self):
        with pytest.raises(CompilationError, match="ERR-BAR-Y-NOT-NUMERIC|numeric"):
            _spec(_PLAN, x="task", y="finish_date", y_start=None)

    def test_date_y_with_numeric_start_raises_naming_both(self):
        rows = [{**r, "start_date": 3} for r in _PLAN]
        with pytest.raises(
            CompilationError, match="finish_date.*start_date|start_date.*finish_date"
        ):
            _spec(rows, x="task", y="finish_date", y_start="start_date")

    def test_date_span_labels_print_the_duration(self):
        spec = _spec(
            _PLAN,
            x="task",
            y="finish_date",
            y_start="start_date",
            style={
                "orientation": "horizontal",
                "marks": {"bar": {"labels": {"visible": True}}},
            },
        )
        (text,) = _texts(spec)
        field = text["encoding"]["text"]["field"]
        (calc,) = [t for t in text["transform"] if t.get("as") == field]
        # One unit for the whole chart: the median task runs a few weeks.
        assert '"weeks"' in calc["calculate"]
        assert '"days"' not in calc["calculate"]


def _descriptions(spec: dict[str, Any]) -> list[str]:
    """Every structured-tooltip description expression in the spec."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            desc = node.get("encoding", {}).get("description")
            if isinstance(desc, dict):
                found.append(desc["value"]["expr"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(spec)
    return found


class TestTooltips:
    def test_span_lists_both_ends_and_a_signed_change(self):
        (desc,) = _descriptions(
            _spec(_BRIDGE, x="step", y="end_value", y_start="start_value")
        )
        assert "start value: " in desc and "end value: " in desc
        assert "Change: " in desc

    def test_row_starting_at_zero_reads_as_a_plain_bar(self):
        (desc,) = _descriptions(
            _spec(_BRIDGE, x="step", y="end_value", y_start="start_value")
        )
        assert desc.startswith('(datum["start_value"] == 0 ?')

    def test_bars_that_only_rise_report_a_range(self):
        (desc,) = _descriptions(_spec(_RANGES))
        assert "Range: " in desc and "Change: " not in desc

    def test_date_span_reports_readable_dates_and_a_duration(self):
        (desc,) = _descriptions(
            _spec(
                _PLAN,
                x="task",
                y="finish_date",
                y_start="start_date",
                style={"orientation": "horizontal"},
            )
        )
        assert 'utcFormat(toDate(datum["start_date"]), "%-d %b %Y")' in desc
        assert "Duration: " in desc

    def test_layers_on_the_same_rows_share_one_block(self):
        spec = _spec(
            _RANGES,
            layers=[
                {"type": "scatter", "y": "low", "label": "Before"},
                {"type": "scatter", "y": "high", "label": "After"},
            ],
        )
        descriptions = _descriptions(spec)
        assert len(set(descriptions)) == 1
        (block,) = set(descriptions)
        assert 'Before: "' in block and 'After: "' in block
        assert '"low: "' not in block and '"high: "' not in block
        assert "Range: " in block

    def test_nested_bar_layer_reads_open_high_low_close(self):
        rows = [
            {"day": "Mon", "low": 99, "high": 104, "open": 100, "close": 103},
            {"day": "Tue", "low": 101, "high": 109, "open": 106, "close": 103},
        ]
        spec = _spec(
            rows,
            x="day",
            layers=[{"type": "bar", "y": "close", "y_start": "open"}],
        )
        (block,) = set(_descriptions(spec))
        order = [
            block.index(f'"{name}: "') for name in ("open", "high", "low", "close")
        ]
        assert order == sorted(order)
        assert "Change: " in block


class TestSpanLabelPositions:
    def test_top_centers_change_rows_and_keeps_totals_at_the_tip(self):
        spec = _spec(
            _BRIDGE,
            x="step",
            y="end_value",
            y_start="start_value",
            style={"marks": {"bar": {"labels": {"visible": True, "position": "top"}}}},
        )
        (text,) = _texts(spec)
        baseline = text["mark"]["baseline"]["expr"]
        assert baseline.startswith("datum['start_value'] == 0 ?")
        assert '"middle"' in baseline


class TestBlockSwatches:
    def test_layer_rows_and_bar_rows_carry_their_marks_colors(self):
        spec = _spec(
            _RANGES,
            layers=[{"type": "scatter", "y": "mid", "label": "Estimate"}],
        )
        (block,) = set(_descriptions(spec))
        # Two distinct colors: the estimate's, and the bar's for its rows.
        swatches = set(re.findall(r"\\u200b(#[0-9a-fA-F]{6})\\u200b", block))
        assert len(swatches) == 2

    def test_nested_bars_keep_a_plain_block(self):
        rows = [{"day": "Mon", "low": 99, "high": 104, "open": 100, "close": 103}]
        spec = _spec(
            rows, x="day", layers=[{"type": "bar", "y": "close", "y_start": "open"}]
        )
        (block,) = set(_descriptions(spec))
        assert "\\u200b" not in block

    def test_an_uncolored_bar_borrows_the_layers_series_with_its_paint(self):
        rows = [
            {
                "day": "Mon",
                "low": 99,
                "high": 104,
                "open": 100,
                "close": 103,
                "s": "Up",
            },
            {
                "day": "Tue",
                "low": 101,
                "high": 109,
                "open": 106,
                "close": 103,
                "s": "Down",
            },
        ]
        spec = _spec(
            rows,
            x="day",
            layers=[{"type": "bar", "y": "close", "y_start": "open", "color": "s"}],
        )
        (block,) = set(_descriptions(spec))
        assert "__dct_layer_series" in block
        calcs = [t["calculate"] for t in spec.get("transform", [])]
        assert any("\\u200b#" in c or "​#" in c for c in calcs)


class TestReviewSeams:
    def test_colored_layer_on_an_uncolored_span_renders(self):
        rows = [{**r, "s": "x" if i % 2 else "y"} for i, r in enumerate(_RANGES)]
        spec = _spec(
            rows,
            layers=[{"type": "scatter", "y": "mid", "label": "Estimate", "color": "s"}],
        )
        assert _descriptions(spec)

    def test_a_layer_start_does_not_switch_off_the_base_y_check(self):
        rows = [
            {"category": "A", "label": "x", "open": 1, "close": 2},
            {"category": "B", "label": "y", "open": 2, "close": 3},
        ]
        with pytest.raises(CompilationError, match="ERR-BAR-Y-NOT-NUMERIC|not numeric"):
            _spec(
                rows,
                y="label",
                y_start=None,
                layers=[{"type": "bar", "y": "close", "y_start": "open"}],
            )

    def test_a_date_layer_on_a_numeric_bar_is_refused(self):
        rows = [{**r, "revenue": 10 + i} for i, r in enumerate(_PLAN)]
        with pytest.raises(CompilationError, match="finish_date"):
            _spec(
                rows,
                x="task",
                y="revenue",
                y_start=None,
                layers=[{"type": "bar", "y": "finish_date", "y_start": "start_date"}],
            )

    def test_middle_aligned_labels_on_a_span_carry_a_code(self):
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        with pytest.raises(ChartDataError) as err:
            _spec(
                _RANGES,
                style={
                    "marks": {
                        "bar": {
                            "labels": {"visible": True, "position": "middle_aligned"}
                        }
                    }
                },
            )
        assert err.value.code is not None
        assert err.value.code.code == "ERR-SPAN-MIDDLE-ALIGNED-LABELS"


class TestDurationText:
    def test_a_duration_that_rounds_to_one_is_singular(self):
        from dbt_charts.core.render.chart.emitters._tooltip import span_duration_expr

        expr = span_duration_expr("e", "s", "week")
        # The singular test compares the text Vega prints, not the raw count.
        assert "== '1'" in expr or '== "1"' in expr

    def test_string_dates_pick_the_unit_from_their_lengths(self):
        from dbt_charts.core.render.chart.emitters._tooltip import span_duration_unit

        rows = [
            {"s": "2026-01-01", "e": "2026-03-15"},
            {"s": "2026-02-01", "e": "2026-05-20"},
        ]
        assert span_duration_unit(rows, "e", "s") == "month"


def test_a_gantt_with_an_open_ended_task_renders():
    rows = [
        *_PLAN,
        {"task": "Later", "start_date": dt.date(2026, 5, 1), "finish_date": None},
    ]
    spec = _spec(
        rows,
        x="task",
        y="finish_date",
        y_start="start_date",
        style={
            "orientation": "horizontal",
            "marks": {"bar": {"labels": {"visible": True}}},
        },
    )
    assert _descriptions(spec)
