"""Cross-field board rules: one error message per rule, pinned verbatim."""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, TypeAdapter, ValidationError

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
from dbt_charts.core.compile.models.chart.authored.table import TableChart
from dbt_charts.core.compile.models.config import ProjectCacheConfig, ProjectCacheInput
from dbt_charts.core.compile.models.constraints import declared_constraints
from dbt_charts.core.compile.models.primitives import CategoricalColorStyle
from dbt_charts.core.compile.models.query.normalized import SchemaQuery
from dbt_charts.core.compile.models.source import (
    BigQuerySourceConfig,
    CsvSourceConfig,
)
from dbt_charts.core.compile.models.style.authored import (
    ColumnScaleConfig,
    TableColumnConfig,
)
from dbt_charts.core.compile.models.style.theme import (
    AxisMirrorStyle,
    DimensionTicksStyle,
    FooterStyle,
    ScaleContinuousStyle,
    SparkEmptyStyle,
    TimestampStyle,
)
from dbt_charts.core.compile.schema.renderers.json_schema import constraint_schema

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
        "schema-query-schema-without-source",
        SchemaQuery,
        {"schema": "s"},
        "schema: 'schema' requires 'source' to be set",
    ),
    (
        "schema-query-table-without-schema",
        SchemaQuery,
        {"source": "src", "table": "t"},
        "schema: 'table' requires 'schema' to be set",
    ),
    (
        "schema-query-column-without-table",
        SchemaQuery,
        {"source": "src", "schema": "s", "column": "c"},
        "schema: 'column' requires 'table' (and 'schema') to be set",
    ),
    (
        "ticks-time-unit-and-count",
        DimensionTicksStyle,
        {"time_unit": "year", "count": 5},
        "ticks.time_unit and ticks.count cannot both be set — they are two "
        "different ways to express tick cadence. Use ticks.time_unit/step for a "
        "step-anchored cadence (every N years/quarters/months), or ticks.count "
        "for VL's advisory tick-count hint, not both.",
    ),
    (
        "table-spanners-with-pivot",
        TableChart,
        {
            "type": "table",
            "rows": ["a"],
            "spanners": [{"label": "L", "columns": ["b"]}],
        },
        "'spanners' cannot be combined with the pivot channels rows/columns/values: "
        "a pivot already draws its own spanners from its column dimensions.",
    ),
    (
        "table-empty-spanners-with-pivot",
        TableChart,
        {"type": "table", "rows": ["a"], "spanners": []},
        "'spanners' cannot be combined with the pivot channels rows/columns/values: "
        "a pivot already draws its own spanners from its column dimensions.",
    ),
    (
        "log-scale-with-zero",
        ScaleContinuousStyle,
        {"type": "log", "zero": True},
        "scale.continuous.type: log is incompatible with zero: true — a log "
        "domain cannot include 0. Remove zero or drop the log scale.",
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
        (
            CsvSourceConfig,
            {"type": "csv", "files": {}},
            ("files",),
            "Dictionary should have at least 1 item after validation, not 0",
        ),
        (
            CategoricalColorStyle,
            {"single_series_palette": []},
            ("single_series_palette",),
            "Value error, single_series_palette must be a palette name or a "
            "non-empty list of colors, got []",
        ),
        (
            ColumnScaleConfig,
            {"color": {"palette": [0.2, 1.0]}},
            ("color", "palette", "list[str]", 0),
            "Input should be a valid string",
        ),
    ],
    ids=[
        "rule-between-three",
        "rule-in-empty",
        "spark-bar-two-y",
        "temporal-domain-not-a-list",
        "csv-files-empty",
        "single-series-palette-empty",
        "column-scale-numeric-palette",
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


_SPARK_STROKE: dict[str, Any] = {"color": "red", "width": 1, "dasharray": "2"}


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (SchemaQuery, {"schema": "s"}),
        (SchemaQuery, {"source": "src", "table": "t"}),
        (SchemaQuery, {"source": "src", "schema": "s", "column": "c"}),
        (DimensionTicksStyle, {"time_unit": "year", "count": 5}),
        (ProjectCacheConfig, {"enabled": True}),
        (FooterStyle, {"font": {"color": "red"}}),
        (FooterStyle, {"font": {"size": 10}}),
        (TimestampStyle, {"font": {"color": "red"}}),
        (TimestampStyle, {"font": {"size": 10}}),
        (SparkEmptyStyle, {"stroke": {"color": "red", "width": 1}}),
        (ScaleContinuousStyle, {"log": {"base": 2}}),
        (ScaleContinuousStyle, {"type": "linear", "pow": {"exponent": 2}}),
        (ScaleContinuousStyle, {"type": "log", "symlog": {"constant": 1}}),
        (BigQuerySourceConfig, {"keyfile": "k.json", "keyfile_json": {"a": "b"}}),
        (TableChart, {"spanners": [{"label": "L"}], "values": ["v"]}),
        (TableChart, {"spanners": [], "rows": ["r"]}),
        (ScaleContinuousStyle, {"type": "log", "zero": True}),
    ],
    ids=[
        "schema-query-schema-without-source",
        "schema-query-table-without-schema",
        "schema-query-column-without-table",
        "ticks-time-unit-and-count",
        "cache-root-enabled-without-ttl",
        "footer-font-without-size",
        "footer-font-without-color",
        "timestamp-font-without-size",
        "timestamp-font-without-color",
        "spark-empty-stroke-without-dasharray",
        "log-base-without-log-type",
        "pow-exponent-without-pow-type",
        "symlog-constant-without-symlog-type",
        "bigquery-two-keyfiles",
        "table-spanners-with-values",
        "table-empty-spanners-with-rows",
        "log-scale-with-zero",
    ],
)
def test_rule_is_declared(model: type[BaseModel], payload: dict[str, Any]) -> None:
    schema = constraint_schema(declared_constraints(model))
    assert not Draft202012Validator(schema).is_valid(payload)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (SchemaQuery, {"source": "src", "schema": "s", "table": "t", "column": "c"}),
        (FooterStyle, {"font": {"extends": "body"}}),
        (FooterStyle, {"font": {"size": 10, "color": "red"}}),
        (SparkEmptyStyle, {"stroke": _SPARK_STROKE}),
        (ScaleContinuousStyle, {"type": "log", "log": {"base": 2}}),
        (ProjectCacheConfig, {"enabled": True, "ttl": "1h"}),
        (TableChart, {"spanners": [{"label": "L"}], "values": []}),
    ],
    ids=[
        "schema-query-full-path",
        "footer-font-extends",
        "footer-font-size-and-color",
        "spark-empty-full-stroke",
        "log-base-with-log-type",
        "cache-root-enabled-with-ttl",
        "table-spanners-with-empty-values",
    ],
)
def test_declared_rule_accepts(model: type[BaseModel], payload: dict[str, Any]) -> None:
    schema = constraint_schema(declared_constraints(model))
    assert Draft202012Validator(schema).is_valid(payload)


def test_cache_root_true_needs_a_ttl() -> None:
    with pytest.raises(ValidationError, match="the project cache root has to say"):
        TypeAdapter(ProjectCacheInput).validate_python(True)


@pytest.mark.parametrize("slot", ["footer", "timestamp"])
@pytest.mark.parametrize("key", ["size", "color"])
def test_page_chrome_font_requires_size_and_color(slot: str, key: str) -> None:
    from dbt_charts.core.compile.config import get_theme_style  # noqa: PLC0415

    model = FooterStyle if slot == "footer" else TimestampStyle
    base = getattr(get_theme_style(), slot).model_dump()
    font = {"size": 10, "color": "#000000", key: None}
    with pytest.raises(ValidationError) as exc:
        model.model_validate({**base, "font": font})
    assert [e["msg"] for e in exc.value.errors()] == [
        f"Value error, style.{slot}.font.{key} is required "
        + ("(float)" if key == "size" else "(color string)")
    ]
