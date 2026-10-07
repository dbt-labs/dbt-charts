"""Cross-field board rules: one error message per rule, pinned verbatim."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.authored import (
    AreaChart,
    BarChart,
    ConditionalRule,
    HeatmapChart,
    KpiSupportConfig,
    MultiplesConfig,
    PointMapChart,
    ScatterChart,
    SparkBarChart,
)
from dbt_charts.core.compile.models.style.authored import TableColumnConfig
from dbt_charts.core.compile.models.style.theme import (
    AxisMirrorStyle,
    ScaleContinuousStyle,
)

_BAR: dict[str, Any] = {"type": "bar", "x": "a", "y": "b"}
_POINT_MAP: dict[str, Any] = {
    "type": "point_map",
    "latitude": "a",
    "longitude": "b",
    "collapse": True,
}
_SUPPORT_TYPES = "area, bar, line"

CASES: list[tuple[str, type[BaseModel], dict[str, Any], str]] = [
    (
        "board-two-layouts",
        AuthoredBoard,
        {"rows": ["c"], "cols": ["c"]},
        "Board can only have one layout type (rows, cols, grid, or tabs)",
    ),
    (
        "board-notes-only",
        AuthoredBoard,
        {"notes": "n"},
        "Board must have at least one layout type, text, title, or chart",
    ),
    (
        "rule-two-predicates",
        ConditionalRule,
        {"gt": 1, "lt": 5, "background": "red"},
        "ConditionalRule requires exactly one condition operator, got: ['lt', 'gt']",
    ),
    (
        "rule-predicates-named-by-yaml-key",
        ConditionalRule,
        {"gt": 1, "in": [1], "background": "red"},
        "ConditionalRule requires exactly one condition operator, got: ['gt', 'in']",
    ),
    (
        "rule-no-predicate",
        ConditionalRule,
        {"background": "red"},
        "ConditionalRule requires exactly one condition operator",
    ),
    (
        "rule-glyph-color-without-glyph",
        ConditionalRule,
        {"gt": 1, "background": "red", "glyph_color": "red"},
        "ConditionalRule glyph_color requires glyph to be set",
    ),
    (
        "rule-tone-without-glyph",
        ConditionalRule,
        {"gt": 1, "tone": "positive"},
        "ConditionalRule tone requires glyph to be set",
    ),
    (
        "rule-no-override",
        ConditionalRule,
        {"gt": 1, "font": {"size": 12}},
        "ConditionalRule requires at least one style override (background, "
        "font.color, font.weight, font.style, font.decoration, or glyph)",
    ),
    (
        "multiples-empty",
        MultiplesConfig,
        {},
        "multiples requires at least one of `rows` or `columns`.",
    ),
    (
        "kpi-support-empty",
        KpiSupportConfig,
        {},
        "Empty `support:` block. Set at least one of `value`, `label`, or "
        "`glyph`, or omit the block entirely.",
    ),
    (
        "point-map-collapse-size",
        PointMapChart,
        {**_POINT_MAP, "size": "a"},
        "point_map: `collapse: true` and `size: <column>` both bind the mark's "
        "size channel — they cannot be combined. Drop `size` to size collapsed "
        "points by count, or drop `collapse` to keep `size` as a bubble_map "
        "measure encoding.",
    ),
    (
        "point-map-collapse-color",
        PointMapChart,
        {**_POINT_MAP, "type": "bubble_map", "color": "a"},
        "bubble_map: `collapse: true` and `color: <column>` cannot be combined "
        "— VL's size:{aggregate: count} groups by every other non-aggregated "
        "encoded field, so an authored `color` reintroduces the exact pile "
        "`collapse` exists to remove. Drop `color` to keep collapsed counts, or "
        "drop `collapse` and aggregate per-category counts in SQL (`GROUP BY "
        "<lat>, <lon>, <category>` producing a count column), then use "
        "`bubble_map` with `size: <count column>, color: <category column>`.",
    ),
    (
        "histogram-layers",
        BarChart,
        {"type": "histogram", "x": "a", "layers": [{"type": "line", "y": "b"}]},
        "chart.layers is not supported for chart type 'histogram'. Supported "
        "chart types: area, bar, line, scatter.",
    ),
    (
        "histogram-y-start",
        BarChart,
        {"type": "histogram", "x": "a", "y_start": "a"},
        "y_start is not supported on type: histogram — a histogram's bars count "
        "from zero, so it takes no y_start.",
    ),
    (
        "bar-y-start-stacked",
        BarChart,
        {**_BAR, "y_start": "a", "style": {"stack": "zero"}},
        "y_start sets each bar's start, so it can't be stacked; remove "
        "style.stack or y_start.",
    ),
    (
        "bar-y-start-y-list",
        BarChart,
        {**_BAR, "y": ["b"], "y_start": "a"},
        "y_start sets one start per bar, so y must name a single column, not a list.",
    ),
    (
        "bar-multi-y-layers",
        BarChart,
        {**_BAR, "y": ["a", "b"], "layers": [{"type": "line", "y": "b"}]},
        "Bar chart: layers are not supported with multi-metric (y: [...]) "
        "charts. Use a long-form query with one y field.",
    ),
    (
        "bar-multi-y-without-x",
        BarChart,
        {"type": "bar", "y": ["a", "b"]},
        "Bar chart: multi-metric (y: [...]) charts require an x field.",
    ),
    (
        "bar-x-without-y",
        BarChart,
        {"type": "bar", "x": "a"},
        "Bar chart: x requires a y field — a bar with categories but no measure "
        "has nothing to draw.",
    ),
    (
        "area-multi-y-layers",
        AreaChart,
        {
            "type": "area",
            "x": "a",
            "y": ["a", "b"],
            "layers": [{"type": "line", "y": "b"}],
        },
        "Area chart: layers are not supported with multi-metric (y: [...]) "
        "charts. Use a long-form query with one y field.",
    ),
    (
        "scatter-support-table",
        ScatterChart,
        {"type": "scatter", "x": "a", "y": "b", "support_table": ["a"]},
        "chart.support_table is not supported for chart type 'scatter' in v1. "
        f"Supported chart types: {_SUPPORT_TYPES}.",
    ),
    (
        "heatmap-support-table",
        HeatmapChart,
        {"type": "heatmap", "x": "a", "y": "b", "support_table": ["a"]},
        "chart.support_table is not supported for chart type 'heatmap' in v1. "
        f"Supported chart types: {_SUPPORT_TYPES}.",
    ),
    (
        "histogram-support-table",
        BarChart,
        {"type": "histogram", "x": "a", "support_table": ["a"]},
        "chart.support_table is not supported for chart type 'histogram' in v1. "
        f"Supported chart types: {_SUPPORT_TYPES}.",
    ),
    (
        "column-width-and-max-width",
        TableColumnConfig,
        {"width": 10, "max_width": 20},
        "TableColumnConfig: 'width' and 'max_width' are mutually exclusive. Use "
        "'width' for a hard pin, or 'max_width' to cap auto-sizing.",
    ),
    (
        "column-glyph-color-without-glyph",
        TableColumnConfig,
        {"glyph_color": "red"},
        "TableColumnConfig glyph_color requires glyph to be set",
    ),
    (
        "mirror-format-and-expr",
        AxisMirrorStyle,
        {"format": "d", "expr": "datum.label"},
        "mirror takes format or expr, not both — expr fully controls the label "
        "text (call format() inside it if needed).",
    ),
    (
        "mirror-empty",
        AxisMirrorStyle,
        {},
        "empty mirror override — set format or expr to relabel the mirrored "
        "edge, or use `mirror: true` to mirror the primary axis's label "
        "verbatim.",
    ),
    (
        "temporal-domain-not-iso",
        ScaleContinuousStyle,
        {"type": "temporal", "domain": ["2024-01-01", "x"]},
        "scale.domain element 'x' is not an ISO-8601 date/datetime string, but "
        "scale.type: temporal requires both domain bounds to be dates (e.g. "
        "'1955-01-01').",
    ),
    (
        "temporal-domain-impossible-date",
        ScaleContinuousStyle,
        {"type": "temporal", "domain": ["2024-02-30", "2024-03-01"]},
        "scale.domain element '2024-02-30' is not an ISO-8601 date/datetime "
        "string, but scale.type: temporal requires both domain bounds to be "
        "dates (e.g. '1955-01-01').",
    ),
]


@pytest.mark.parametrize(
    ("model", "payload", "message"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_rule_message(
    model: type[BaseModel], payload: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError) as exc:
        model.model_validate(payload)
    assert [e["msg"] for e in exc.value.errors()] == [f"Value error, {message}"]


@pytest.mark.parametrize(
    ("model", "payload", "loc", "message"),
    [
        (
            ConditionalRule,
            {"between": [1, 2, 3], "background": "red"},
            ("between",),
            "List should have at most 2 items after validation, not 3",
        ),
        (
            ConditionalRule,
            {"in": [], "background": "red"},
            ("in",),
            "List should have at least 1 item after validation, not 0",
        ),
        (
            SparkBarChart,
            {"type": "spark_bar", "x": "a", "y": ["a", "b"]},
            ("y", "list[str]"),
            "List should have at most 1 item after validation, not 2",
        ),
        (
            ScaleContinuousStyle,
            {"type": "temporal", "domain": {1955, 2026}},
            ("domain",),
            "Input should be a valid tuple",
        ),
    ],
    ids=[
        "rule-between-three",
        "rule-in-empty",
        "spark-bar-two-y",
        "temporal-domain-not-a-list",
    ],
)
def test_length_rule_message(
    model: type[BaseModel],
    payload: dict[str, Any],
    loc: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(ValidationError) as exc:
        model.model_validate(payload)
    assert (loc, message) in [(e["loc"], e["msg"]) for e in exc.value.errors()]


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (ConditionalRule, {"eq": "", "background": "red"}),
        (ConditionalRule, {"is_null": False, "font": {"color": "red"}}),
        (AuthoredBoard, {"title": "t"}),
        (AuthoredBoard, {"text": " ", "width": "28px"}),
        (BarChart, {**_BAR, "y": ["a", "b"], "layers": []}),
        (PointMapChart, {**_POINT_MAP, "collapse": False, "size": "a"}),
        (
            ScaleContinuousStyle,
            {"type": "temporal", "domain": ["2024-01-01", "2024-02-01T10:00:00Z"]},
        ),
        (ScaleContinuousStyle, {"type": "linear", "domain": [0, 10]}),
        (ScaleContinuousStyle, {"type": "linear", "domain": ["2020-02-30", "x"]}),
    ],
    ids=[
        "rule-eq-empty-string",
        "rule-is-null-false",
        "board-title-only",
        "board-blank-text",
        "bar-multi-y-empty-layers",
        "point-map-no-collapse",
        "temporal-domain-iso",
        "linear-domain",
        "linear-domain-impossible-date",
    ],
)
def test_rule_accepts(model: type[BaseModel], payload: dict[str, Any]) -> None:
    model.model_validate(payload)
