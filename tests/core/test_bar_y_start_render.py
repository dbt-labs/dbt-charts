"""A bar with ``y_start`` spans from that column to ``y`` instead of from zero.

Covers the resolve and Vega-Lite halves: the span channel in both
orientations, no stacking, no forced zero, query order kept, signed change
labels, the shared domain with a bar layer, and the data rules that need rows.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

import pytest
import vl_convert
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
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
    context: ChartStyleContext = _BOARD_CTX,
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
        chart_style_context=context,
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
                "axis_y": {"labels": {"format": "$,.0f"}},
                "marks": {"bar": {"labels": {"visible": True}}},
            },
        )
        expr = self._label_expr(spec)
        assert "datum['end_value'] - datum['start_value']" in expr
        assert "'+'" in expr and "'\\u2212'" in expr
        # A row that starts at zero prints its plain value.
        assert "datum['start_value'] == 0" in expr

    def test_span_label_honors_an_authored_prefix_affix(self):
        """A FormatConfig prefix (outside d3's own $/# vocabulary) on a
        span's value labels must not crash (issue #27 widened labels.format
        to accept it) and must compose sign-first: the rise/fall indicator
        goes before the currency symbol, never split by it."""
        spec = _spec(
            _BRIDGE,
            x="step",
            y="end_value",
            y_start="start_value",
            style={
                "marks": {
                    "bar": {
                        "labels": {
                            "visible": True,
                            "format": {"spec": ",.0f", "prefix": "€"},
                        }
                    }
                }
            },
        )
        expr = self._label_expr(spec)
        prefix_json = json.dumps("€")
        # Sign-first, within the changed (start != 0) branch.
        sign_pos = expr.index("? '+' : '\\u2212'")
        prefix_pos = expr.index(prefix_json, sign_pos)
        digits_pos = expr.index("format(abs(", prefix_pos)
        assert sign_pos < prefix_pos < digits_pos

    def test_span_label_honors_an_authored_suffix_affix(self):
        """A FormatConfig suffix renders trailing (sign, digits, suffix), matching the
        pinned rule.
        """
        spec = _spec(
            _BRIDGE,
            x="step",
            y="end_value",
            y_start="start_value",
            style={
                "marks": {
                    "bar": {
                        "labels": {
                            "visible": True,
                            "format": {"spec": ",.0f", "suffix": " €"},
                        }
                    }
                }
            },
        )
        expr = self._label_expr(spec)
        suffix_json = json.dumps(" €")
        # Suffix trails the digits, within the changed (start != 0) branch --
        # it never sits between the sign and the digits it decorates.
        digits_pos = expr.index("format(abs(")
        suffix_pos = expr.index(suffix_json, digits_pos)
        assert digits_pos < suffix_pos

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

    def test_columns_named_without_a_date_word_still_draw_a_temporal_span(self):
        rows = [
            {
                "task": r["task"],
                "started": r["start_date"],
                "finished": r["finish_date"],
            }
            for r in _PLAN
        ]
        spec = _spec(
            rows,
            x="task",
            y="finished",
            y_start="started",
            style={"orientation": "horizontal"},
        )
        (enc,) = _bar_encodings(spec)
        assert enc["x"]["field"] == "finished"
        assert enc["x"]["type"] == "temporal"
        assert enc["x2"] == {"field": "started"}

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
        assert "' weeks'" in calc["calculate"]
        assert "' days'" not in calc["calculate"]


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
        assert "Start value: " in desc and "End value: " in desc
        assert "\\u0394: " in desc

    def test_row_starting_at_zero_reads_as_a_plain_bar(self):
        (desc,) = _descriptions(
            _spec(_BRIDGE, x="step", y="end_value", y_start="start_value")
        )
        assert desc.startswith('(datum["start_value"] == 0 ?')

    def test_bars_that_only_rise_report_a_range(self):
        (desc,) = _descriptions(_spec(_RANGES))
        assert "\\u0394: " in desc and "\\u2212" not in desc

    def test_span_change_honors_an_affixed_number_format(self):
        """A y_start bar with an affixed ``number_format`` must not crash the
        whole board (issue #27 widened ``tooltip_format`` to accept a
        ``FormatConfig``) and its Δ row must show the affix."""
        (desc,) = _descriptions(
            _spec(
                _BRIDGE,
                x="step",
                y="end_value",
                y_start="start_value",
                style={
                    "axis_y": {"labels": {"format": {"spec": ",.0f", "prefix": "EUR "}}}
                },
            )
        )
        assert "\\u0394: " in desc
        assert json.dumps("EUR ") in desc

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

    def test_date_span_reads_the_boards_redefined_date_short(self):
        context = resolve_chart_style_context(
            get_theme_style(),
            StylePatch.model_validate({"formats": {"date_short": "%Y"}}),
        )
        (desc,) = _descriptions(
            _spec(
                _PLAN,
                x="task",
                y="finish_date",
                y_start="start_date",
                style={"orientation": "horizontal"},
                context=context,
            )
        )
        assert 'utcFormat(toDate(datum["start_date"]), "%Y")' in desc

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
        assert "\\u0394: " in block

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
            block.index(f'"{name}: "') for name in ("Open", "High", "Low", "Close")
        ]
        assert order == sorted(order)
        assert "\\u0394: " in block

    def test_span_layer_series_is_named_for_its_two_ends(self):
        rows = [{"day": "Mon", "high": 104, "open": 100, "close": 103}]
        spec = _spec(
            rows,
            x="day",
            y_start=None,
            layers=[{"type": "bar", "y": "close", "y_start": "open"}],
        )
        (block,) = [d for d in set(_descriptions(spec)) if '"Open: "' in d]
        assert '("Open \\u2013 Close")' in block and " to " not in block


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
        # The plural is chosen by the number Vega prints, not the raw count:
        # 7.2 days print "1" week.
        spec = {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": [{"s": "2026-01-01", "e": "2026-01-08T05:00:00"}]},
            "transform": [{"calculate": expr, "as": "t"}],
            "mark": {"type": "text"},
            "encoding": {"text": {"field": "t"}},
        }
        assert ">1 week<" in vl_convert.vegalite_to_svg(spec)

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


def _layered_spec(
    chart_type: str, data: list[dict[str, Any]], layers: list[dict[str, Any]], **fields
) -> dict[str, Any]:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": chart_type,
            "x": "category",
            "y": "mid",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "q",
            "layers": layers,
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


@pytest.mark.parametrize("chart_type", ["line", "area", "scatter"])
class TestBarLayerSpanOnNonBarChart:
    def test_bar_layer_binds_y_start_to_y2(self, chart_type):
        spec = _layered_spec(
            chart_type, _RANGES, [{"type": "bar", "y": "high", "y_start": "low"}]
        )
        (enc,) = _bar_encodings(spec)
        assert enc["y"]["field"] == "high"
        assert enc["y2"] == {"field": "low"}

    def test_null_layer_start_is_refused(self, chart_type):
        rows = [{**_RANGES[0], "low": None}, *_RANGES[1:]]
        with pytest.raises(CompilationError) as err:
            _layered_spec(
                chart_type, rows, [{"type": "bar", "y": "high", "y_start": "low"}]
            )
        assert err.value.code is not None
        assert err.value.code.code == "ERR-BAR-Y-START-NULL"

    def test_date_layer_on_a_numeric_axis_is_refused(self, chart_type):
        rows = [
            {**r, "category": r["task"], "mid": 10 + i} for i, r in enumerate(_PLAN)
        ]
        with pytest.raises(CompilationError) as err:
            _layered_spec(
                chart_type,
                rows,
                [{"type": "bar", "y": "finish_date", "y_start": "start_date"}],
            )
        assert err.value.code is not None
        assert err.value.code.code == "ERR-BAR-Y-START-KIND"


@pytest.mark.parametrize("position", ["left", "right"])
def test_pinned_axis_layer_is_not_held_to_the_base_axis_kind(position):
    rows = [
        {
            "category": r["task"],
            "mid": r["finish_date"].isoformat(),
            "low": i,
            "high": i + 2,
        }
        for i, r in enumerate(_PLAN)
    ]
    spec = _layered_spec(
        "scatter",
        rows,
        [
            {
                "type": "bar",
                "y": "high",
                "y_start": "low",
                "axis_y": {"position": position},
            }
        ],
    )
    (enc,) = _bar_encodings(spec)
    assert enc["y2"] == {"field": "low"}


@pytest.mark.parametrize(
    ("chart_type", "code"),
    [("line", "ERR-LINE-Y-NOT-NUMERIC"), ("area", "ERR-AREA-ENCODING-SWAPPED")],
)
def test_family_y_diagnostic_wins_over_a_bad_span_layer(chart_type, code):
    rows = [{**r, "mid": r["category"], "low": None} for r in _RANGES]
    with pytest.raises(CompilationError) as err:
        _layered_spec(
            chart_type, rows, [{"type": "bar", "y": "high", "y_start": "low"}]
        )
    assert err.value.code is not None
    assert err.value.code.code == code


def test_dot_plot_takes_a_span_layer():
    rows = [{**r, "mid": r["category"], "category": r["mid"]} for r in _RANGES]
    spec = _layered_spec(
        "scatter", rows, [{"type": "bar", "y": "high", "y_start": "low"}]
    )
    (enc,) = _bar_encodings(spec)
    assert enc["y2"] == {"field": "low"}


def _painted(spec: dict[str, Any]) -> list[str]:
    import html
    import re

    import vl_convert as vlc

    svg = vlc.vegalite_to_svg(spec)
    texts = [html.unescape(t) for t in re.findall(r"<text[^>]*>([^<]*)</text>", svg)]
    labels = [html.unescape(a) for a in re.findall(r'aria-label="([^"]*)"', svg)]
    return texts + labels


@pytest.mark.parametrize(
    ("prefix", "gain", "loss"),
    [("EUR ", "EUR +28", "EUR −22"), ("€", "+€28", "−€22")],
)
def test_span_label_and_tooltip_place_the_sign_by_the_prefix(prefix, gain, loss):
    from dbt_charts.core.compile.format import resolve_format_parts
    from dbt_charts.core.compile.models.primitives import FormatConfig
    from dbt_charts.core.render.chart.features.value_labels import (
        painted_span_label_text,
    )

    fmt = {"spec": ",.0f", "prefix": prefix}
    spec = _spec(
        _BRIDGE,
        x="step",
        y="end_value",
        y_start="start_value",
        style={
            "axis_y": {"labels": {"format": fmt}},
            "marks": {"bar": {"labels": {"visible": True, "format": fmt}}},
        },
    )
    painted = _painted(spec)
    assert gain in painted and loss in painted, painted
    assert any(f"Δ: {loss}" in t for t in painted), painted
    resolved = resolve_format_parts(FormatConfig(**fmt), None)
    assert painted_span_label_text(126, 148, resolved, False) == loss


class TestNativeDateDimension:
    """A native DATE x is classified temporal whatever the column is named, so
    it takes the temporal axis arm and skips ``style.axis_band`` -- exactly as a
    column named ``order_date`` always has."""

    @staticmethod
    def _label_padding(rows: list[dict[str, Any]], x: str) -> float:
        spec = _spec(
            rows,
            x=x,
            y="revenue",
            y_start=None,
            style={"axis_band": {"labels": {"padding": 33}}},
        )
        return _bar_encodings(spec)[0]["x"]["axis"]["labelPadding"]

    @pytest.mark.parametrize("x", ["cohort", "order_date"])
    def test_native_date_x_skips_axis_band(self, x: str):
        rows = [{x: dt.date(2026, m, 1), "revenue": m} for m in (1, 2, 3, 4)]
        assert self._label_padding(rows, x) != 33

    def test_category_x_still_takes_axis_band(self):
        rows = [{"cohort": c, "revenue": i} for i, c in enumerate("abcd")]
        assert self._label_padding(rows, "cohort") == 33


def _plan_with_mid() -> list[dict[str, Any]]:
    return [
        {**r, "mid": r["start_date"] + (r["finish_date"] - r["start_date"]) / 2}
        for r in _PLAN
    ]


def _y_encodings(spec: dict[str, Any], mark: str) -> list[dict[str, Any]]:
    """The ``y`` encoding of every layer drawing ``mark``, parents merged in."""
    found: list[dict[str, Any]] = []

    def walk(node: Any, inherited: dict[str, Any]) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, inherited)
        elif isinstance(node, dict):
            enc = {**inherited, **node.get("encoding", {})}
            node_mark = node.get("mark")
            if (
                node_mark.get("type") if isinstance(node_mark, dict) else node_mark
            ) == mark:
                found.append(enc["y"])
            for key in ("layer", "spec"):
                walk(node.get(key, []), enc)

    walk(spec, {})
    return found


@pytest.mark.parametrize("layer_type", ["line", "scatter"])
def test_a_layer_on_a_date_measure_plots_dates_on_the_shared_scale(layer_type):
    spec = _spec(
        _plan_with_mid(),
        x="task",
        y="finish_date",
        y_start="start_date",
        layers=[{"type": layer_type, "y": "mid"}],
    )
    mark = "line" if layer_type == "line" else "point"
    (y_enc,) = _y_encodings(spec, mark)
    assert y_enc["type"] == "temporal"
    assert "scale" not in y_enc
    assert "format" not in y_enc["axis"]


@pytest.mark.parametrize(
    "layer",
    [{"type": "area", "y": "mid"}, {"type": "bar", "y": "mid"}],
    ids=["area", "bar-without-y-start"],
)
def test_a_baseline_layer_on_a_date_measure_is_refused(layer):
    with pytest.raises(CompilationError) as err:
        _spec(
            _plan_with_mid(),
            x="task",
            y="finish_date",
            y_start="start_date",
            layers=[layer],
        )
    assert err.value.code is not None
    assert err.value.code.code == "ERR-LAYER-BASELINE-ON-DATE-MEASURE"
    assert "charts.t.layers[0]" in str(err.value)


def test_a_bar_layer_with_its_own_y_start_spans_dates_on_a_date_measure():
    spec = _spec(
        _plan_with_mid(),
        x="task",
        y="finish_date",
        y_start="start_date",
        layers=[{"type": "bar", "y": "finish_date", "y_start": "mid"}],
    )
    assert all(enc["type"] == "temporal" for enc in _y_encodings(spec, "bar"))


def test_a_numeric_layer_on_a_date_measure_is_refused():
    rows = [{**r, "score": 5 + 10 * i} for i, r in enumerate(_plan_with_mid())]
    with pytest.raises(CompilationError) as err:
        _spec(
            rows,
            x="task",
            y="finish_date",
            y_start="start_date",
            layers=[{"type": "scatter", "y": "score"}],
        )
    assert err.value.code is not None
    assert err.value.code.code == "ERR-LAYER-Y-KIND-MISMATCH"
    assert "plots on the bar's date axis" in str(err.value)


def test_a_date_layer_pinned_to_a_side_is_refused():
    rows = _plan_with_mid()
    with pytest.raises(CompilationError) as err:
        _spec(
            rows,
            x="task",
            y="finish_date",
            y_start="start_date",
            layers=[{"type": "line", "y": "mid", "axis_y": {"position": "right"}}],
        )
    assert err.value.code is not None
    assert err.value.code.code == "ERR-LAYER-Y-KIND-MISMATCH"
    assert "pinned numeric axis" in str(err.value)


@pytest.mark.parametrize("layer_type", ["line", "scatter"])
def test_a_date_layer_over_a_numeric_bar_is_refused(layer_type):
    rows = [
        {"category": "A", "high": 10, "when": dt.date(2026, 1, 5)},
        {"category": "B", "high": 20, "when": dt.date(2026, 2, 5)},
    ]
    with pytest.raises(CompilationError) as err:
        _spec(rows, y_start=None, layers=[{"type": layer_type, "y": "when"}])
    assert err.value.code is not None
    assert err.value.code.code == "ERR-LAYER-Y-KIND-MISMATCH"
    assert "bar's numeric value axis" in str(err.value)


def test_pinned_and_unpinned_layers_over_a_date_measure_are_refused():
    rows = [{**r, "score": 5 + 10 * i} for i, r in enumerate(_plan_with_mid())]
    with pytest.raises(CompilationError) as err:
        _spec(
            rows,
            x="task",
            y="finish_date",
            y_start="start_date",
            layers=[
                {"type": "line", "y": "score", "axis_y": {"position": "right"}},
                {"type": "scatter", "y": "mid"},
            ],
        )
    assert err.value.code is not None
    assert err.value.code.code == "ERR-LAYER-MIXED-AXES-ON-DATE-MEASURE"


def test_a_pinned_layer_over_a_date_measure_keeps_its_numeric_scale():
    rows = [{**r, "score": 5 + 10 * i} for i, r in enumerate(_plan_with_mid())]
    spec = _spec(
        rows,
        x="task",
        y="finish_date",
        y_start="start_date",
        layers=[{"type": "line", "y": "score", "axis_y": {"position": "right"}}],
    )
    (y_enc,) = _y_encodings(spec, "line")
    assert y_enc["type"] == "quantitative"
