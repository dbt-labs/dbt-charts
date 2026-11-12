"""A chart's constant paint lives on its primary mark, not on ``style.color``.

Precedence: color channel > mark paint > ``single_series_palette`` > palette.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    prepare_board_mapping,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

PAINT = "#12ab34"

_ROWS: list[dict[str, Any]] = [
    {"m": "Jan", "v": 1, "seg": "a", "n": 3},
    {"m": "Feb", "v": 2, "seg": "b", "n": 5},
    {"m": "Mar", "v": 3, "seg": "a", "n": 4},
]

# chart type -> (chart fields, style.marks path to the primary mark's paint)
_FAMILIES: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {
    "bar": ({"x": "m", "y": "v"}, ("bar", "color")),
    "histogram": ({"x": "v", "y": "n"}, ("bar", "color")),
    "area": ({"x": "m", "y": "v"}, ("area", "color")),
    "line": ({"x": "m", "y": "v"}, ("line", "stroke", "color")),
    "scatter": ({"x": "v", "y": "n"}, ("point", "color")),
    "pie": ({"theta": "v"}, ("slice", "color")),
    "heatmap": ({"x": "m", "y": "seg"}, ("rect", "color")),
}


def _nested(path: tuple[str, ...], value: str) -> dict[str, Any]:
    for key in reversed(path):
        value = {key: value}  # type: ignore[assignment]
    return value  # type: ignore[return-value]


def _board_context() -> ChartStyleContext:
    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _resolved(chart_type: str, extra: dict[str, Any], paint: str | None) -> Any:
    fields, path = _FAMILIES[chart_type]
    chart: dict[str, Any] = {
        "id": "c",
        "type": chart_type,
        "query_name": "q",
        **fields,
        **extra,
    }
    if paint is not None:
        chart["style"] = {"marks": _nested(path, paint)}
    model = TypeAdapter(Chart).validate_python(chart)
    return resolve(model, _ROWS, _board_context())


@pytest.mark.parametrize("chart_type", list(_FAMILIES))
def test_mark_paint_colors_a_single_series_chart(chart_type: str) -> None:
    resolved = _resolved(chart_type, {}, PAINT)
    assert resolved.style.single_series_fill == PAINT


@pytest.mark.parametrize("chart_type", list(_FAMILIES))
def test_without_mark_paint_the_palette_applies(
    chart_type: str,
) -> None:
    resolved = _resolved(chart_type, {}, None)
    context = _board_context()
    expected = (
        context.palette
        if chart_type in {"pie", "heatmap"}
        else context.single_series_palette
    )
    assert resolved.style.single_series_fill in expected


@pytest.mark.parametrize("chart_type", ["bar", "area", "line", "scatter"])
def test_a_color_channel_wins_over_mark_paint(chart_type: str) -> None:
    resolved = _resolved(chart_type, {"color": "seg"}, PAINT)
    assert PAINT not in resolved.palette


@pytest.mark.parametrize("chart_type", ["bar", "area", "line", "scatter"])
def test_auto_mark_paint_is_unset(chart_type: str) -> None:
    resolved = _resolved(chart_type, {}, "auto")
    assert resolved.style.single_series_fill in _board_context().single_series_palette


@pytest.mark.parametrize("chart_type", list(_FAMILIES))
def test_style_color_static_is_rejected(chart_type: str) -> None:
    fields, _ = _FAMILIES[chart_type]
    chart = {
        "id": "c",
        "type": chart_type,
        "query_name": "q",
        **fields,
        "style": {"color": {"static": PAINT}},
    }
    with pytest.raises(ValidationError, match="static"):
        TypeAdapter(Chart).validate_python(chart)


# --- migration -------------------------------------------------------------


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return prepare_board_mapping(raw)


# chart type -> (extra chart fields, style.marks path, theme family key)
_MIGRATED: dict[str, tuple[dict[str, Any], tuple[str, ...], str]] = {
    "bar": ({"x": "m", "y": "v"}, ("bar", "color"), "bar"),
    "histogram": ({"x": "v"}, ("bar", "color"), "histogram"),
    "area": ({"x": "m", "y": "v"}, ("area", "color"), "area"),
    "line": ({"x": "m", "y": "v"}, ("line", "stroke", "color"), "line"),
    "scatter": ({"x": "v", "y": "n"}, ("point", "color"), "scatter"),
    "pie": ({"theta": "v"}, ("slice", "color"), "pie"),
    "donut": ({"theta": "v"}, ("slice", "color"), "pie"),
    "heatmap": ({"x": "m", "y": "seg"}, ("rect", "color"), "heatmap"),
    "point_map": (
        {"latitude": "v", "longitude": "n"},
        ("point", "color"),
        "point_map",
    ),
    "bubble_map": (
        {"latitude": "v", "longitude": "n"},
        ("point", "color"),
        "point_map",
    ),
    "geoshape": ({"geo": "world"}, ("geoshape", "fill"), "geoshape"),
    "map": ({"geo": "world"}, ("geoshape", "fill"), "geoshape"),
}


def _board(
    chart: dict[str, Any], style: dict[str, Any] | None = None
) -> dict[str, Any]:
    board: dict[str, Any] = {
        "title": "Test",
        "queries": {"q": {"type": "values", "rows": _ROWS}},
        "charts": {"c": {"query": "q", **chart}},
        "rows": ["c"],
    }
    if style is not None:
        board["style"] = style
    return board


@pytest.mark.parametrize("chart_type", list(_MIGRATED))
def test_chart_tier_static_moves_to_the_primary_mark(chart_type: str) -> None:
    fields, path, _ = _MIGRATED[chart_type]
    raw = _board({"type": chart_type, **fields, "style": {"color": {"static": PAINT}}})

    migrated = _migrate(raw)

    style = migrated["charts"]["c"]["style"]
    assert style["marks"] == _nested(path, PAINT)
    assert "static" not in style.get("color", {})
    AuthoredBoard.model_validate(migrated)


@pytest.mark.parametrize("chart_type", list(_MIGRATED))
def test_theme_tier_static_moves_to_the_primary_mark(chart_type: str) -> None:
    fields, path, family = _MIGRATED[chart_type]
    raw = _board(
        {"type": chart_type, **fields},
        {"charts": {family: {"color": {"static": PAINT}}}},
    )

    migrated = _migrate(raw)

    family_style = migrated["style"]["charts"][family]
    assert family_style["marks"] == _nested(path, PAINT)
    assert "static" not in family_style.get("color", {})
    AuthoredBoard.model_validate(migrated)


def test_a_table_static_color_moves_to_its_default_cell_font() -> None:
    raw = _board({"type": "table", "style": {"color": {"static": PAINT}}})

    migrated = _migrate(raw)

    style = migrated["charts"]["c"]["style"]
    assert style["column_defaults"]["font"]["color"] == PAINT
    assert "color" not in style
    AuthoredBoard.model_validate(migrated)


def test_a_table_theme_static_color_moves_to_its_default_cell_font() -> None:
    raw = _board({"type": "table"}, {"charts": {"table": {"color": {"static": PAINT}}}})

    migrated = _migrate(raw)

    table_style = migrated["style"]["charts"]["table"]
    assert table_style["column_defaults"]["font"]["color"] == PAINT
    assert "color" not in table_style


def test_an_occupied_destination_refuses_the_move() -> None:
    raw = _board(
        {
            "type": "bar",
            "x": "m",
            "y": "v",
            "style": {
                "color": {"static": PAINT},
                "marks": {"bar": {"color": "#000000"}},
            },
        }
    )

    result = compile_board_yaml(raw)

    assert not result.success
    assert any("color.static" in error.message for error in result.errors)


def compile_board_yaml(raw: dict[str, Any]) -> Any:
    import yaml

    return compile_board(yaml.safe_dump(raw))


def test_an_old_board_compiles_and_paints_the_mark() -> None:
    raw = _board(
        {
            "type": "bar",
            "x": "m",
            "y": "v",
            "style": {"color": {"static": PAINT}},
        }
    )

    result = compile_board_yaml(raw)

    assert result.success, result.errors
