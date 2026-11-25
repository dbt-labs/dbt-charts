"""The ink theme's resting-state values for scatter: off in `_base`, and carried to the
resolved style when a theme or board sets them."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_SCATTER_ROWS = [{"x": 1, "y": 2, "g": "a"}, {"x": 2, "y": 3, "g": "b"}]
_INK_SCATTER = {
    "frame": "range",
    "series_symbols": ["hollow_circle", "plus", "open_triangle", "cross"],
    "marks": {"point": {"halo": 1, "size": 42.25}},
}


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _resolved(
    chart: dict[str, Any], rows: list[dict[str, Any]], board_style: dict[str, Any]
) -> Any:  # noqa: ANN401 — a ResolvedChart union narrowed per test
    normalized = TypeAdapter(Chart).validate_python({"id": "t", **chart})
    _, context = resolve_style_and_context(
        get_theme_style(), StylePatch.model_validate(board_style)
    )
    return resolve(normalized, rows, chart_style_context=context)


_HEAT_ROWS = [
    {"hour": "12a", "day": "Mon", "n": 4},
    {"hour": "2a", "day": "Mon", "n": 9},
    {"hour": "12a", "day": "Tue", "n": 30},
]


def test_base_leaves_the_heatmap_values_off() -> None:
    heatmap = get_theme_style().charts.heatmap
    assert (heatmap.tints, heatmap.label_divides) == (None, None)


def test_heatmap_tints_shade_a_numeric_color_through_its_pattern_ramp() -> None:
    resolved = _resolved(
        {"type": "heatmap", "x": "hour", "y": "day", "color": "n"},
        _HEAT_ROWS,
        {"charts": {"heatmap": {"tints": True, "label_divides": True}}},
    )
    assert (resolved.style.tints, resolved.style.label_divides) == (True, True)
    assert resolved.pattern is not None
    assert (resolved.pattern.field, resolved.pattern.scale) == ("n", "sequential")


def test_an_untinted_heatmap_resolves_no_pattern() -> None:
    resolved = _resolved(
        {"type": "heatmap", "x": "hour", "y": "day", "color": "n"}, _HEAT_ROWS, {}
    )
    assert resolved.pattern is None


_INK_TINTS = {"color": "#2A2A2A", "ground": "background", "inverse": False, "steps": 5}
_STATE_ROWS = [{"state": "06", "v": 9.8}, {"state": "48", "v": 7.1}]
_CITY_ROWS = [
    {"lat": 40.7, "lon": -74.0, "n": 10},
    {"lat": 34.05, "lon": -118.2, "n": 8},
]


def test_base_leaves_the_map_values_off() -> None:
    charts = get_theme_style().charts
    assert (charts.geoshape.tints, charts.geoshape.no_data_dash) == (None, None)
    point_map = charts.point_map
    assert (point_map.tints, point_map.named, point_map.size_key) == (None, None, None)
    assert point_map.marks.point.halo is None


def test_point_halo_reaches_the_point_map_and_refuses_a_negative_width() -> None:
    resolved = _resolved(
        {
            "type": "bubble_map",
            "latitude": "lat",
            "longitude": "lon",
            "geo_source": "us-states",
            "size": "n",
        },
        _CITY_ROWS,
        {"charts": {"point_map": {"marks": {"point": {"halo": 1}}}}},
    )
    assert resolved.style.point_mark.halo == 1
    with pytest.raises(ValidationError):
        StylePatch.model_validate(
            {"charts": {"point_map": {"marks": {"point": {"halo": -1}}}}}
        )


def test_choropleth_tints_resolve_a_ramp_over_the_value() -> None:
    resolved = _resolved(
        {
            "type": "map",
            "geo": {"source": "us-states"},
            "lookup": "state",
            "value": "v",
        },
        _STATE_ROWS,
        {"charts": {"geoshape": {"tints": _INK_TINTS, "no_data_dash": [2, 2]}}},
    )
    assert resolved.pattern is not None
    assert (resolved.pattern.field, resolved.pattern.color) == ("v", "#2A2A2A")
    assert resolved.style.geoshape.no_data_dash == [2, 2]


def test_dot_map_tints_span_the_size_field_and_need_one() -> None:
    style = {"charts": {"point_map": {"tints": _INK_TINTS, "named": 3}}}
    chart = {
        "type": "bubble_map",
        "latitude": "lat",
        "longitude": "lon",
        "geo_source": "us-states",
        "size": "n",
    }
    resolved = _resolved(chart, _CITY_ROWS, style)
    assert resolved.pattern is not None
    assert resolved.pattern.field == "n"
    assert resolved.style.point_map.named == 3
    unsized = {k: v for k, v in chart.items() if k != "size"} | {"type": "point_map"}
    with pytest.raises(ValueError, match="set `size:`"):
        _resolved(unsized, _CITY_ROWS, style)


def test_tints_need_an_ink() -> None:
    no_ink = {k: v for k, v in _INK_TINTS.items() if k != "color"}
    with pytest.raises(ValueError, match="names the ink"):
        _resolved(
            {
                "type": "map",
                "geo": {"source": "us-states"},
                "lookup": "state",
                "value": "v",
            },
            _STATE_ROWS,
            {"charts": {"geoshape": {"tints": no_ink}}},
        )


def test_a_strip_key_reaches_the_heatmap_and_map_with_a_value_label_format() -> None:
    heat = _resolved(
        {"type": "heatmap", "x": "hour", "y": "day", "color": "n"},
        _HEAT_ROWS,
        {"charts": {"heatmap": {"tints": True, "strip_key": True}}},
    )
    assert heat.style.strip_key is True
    assert heat.style.strip_key_format is not None
    choro = _resolved(
        {
            "type": "map",
            "geo": {"source": "us-states"},
            "lookup": "state",
            "value": "v",
        },
        _STATE_ROWS,
        {"charts": {"geoshape": {"tints": _INK_TINTS, "strip_key": True}}},
    )
    assert choro.style.geoshape.strip_key is True
    assert choro.style.strip_key_format is not None


def test_the_strip_key_prints_in_the_value_label_format_not_the_tooltips() -> None:
    heat = _resolved(
        {"type": "heatmap", "x": "hour", "y": "day", "color": "n"},
        _HEAT_ROWS,
        {"charts": {"heatmap": {"tints": True, "strip_key": True}}},
    )
    assert heat.style.strip_key_format.spec == ",.0f"
    assert heat.style.tooltip_format.spec != heat.style.strip_key_format.spec


def test_an_untinted_heatmap_resolves_its_switches_as_plain_false() -> None:
    heat = _resolved(
        {"type": "heatmap", "x": "hour", "y": "day", "color": "n"}, _HEAT_ROWS, {}
    )
    assert (heat.style.tints, heat.style.strip_key, heat.style.label_divides) == (
        False,
        False,
        False,
    )


def test_heatmap_tints_leave_a_categorical_color_to_its_palette() -> None:
    rows = [
        {"hour": "12a", "day": "Mon", "g": "a"},
        {"hour": "2a", "day": "Tue", "g": "b"},
    ]
    heat = _resolved(
        {"type": "heatmap", "x": "hour", "y": "day", "color": "g"},
        rows,
        {"charts": {"heatmap": {"tints": True}}},
    )
    assert heat.pattern is None


def test_choropleth_tints_without_a_value_name_value_not_size() -> None:
    with pytest.raises(ValueError, match="set `value:`"):
        _resolved(
            {"type": "map", "geo": {"source": "us-states"}, "lookup": "state"},
            _STATE_ROWS,
            {"charts": {"geoshape": {"tints": _INK_TINTS}}},
        )


def test_named_points_print_in_the_value_label_format_when_named_is_set() -> None:
    chart = {
        "type": "bubble_map",
        "latitude": "lat",
        "longitude": "lon",
        "geo_source": "us-states",
        "size": "n",
    }
    named = _resolved(chart, _CITY_ROWS, {"charts": {"point_map": {"named": 2}}})
    assert named.style.named_format.spec == ",.0f"
    assert named.style.tooltip_format.spec != named.style.named_format.spec
    plain = _resolved(chart, _CITY_ROWS, {})
    assert plain.style.named_format is None


def test_base_leaves_the_strip_key_off_and_refuses_a_form_name() -> None:
    charts = get_theme_style().charts
    assert (charts.heatmap.strip_key, charts.geoshape.strip_key) == (None, None)
    with pytest.raises(ValidationError):
        StylePatch.model_validate({"charts": {"heatmap": {"strip_key": "ends"}}})


def test_a_partial_dot_map_tints_write_names_the_missing_keys() -> None:
    chart = {
        "type": "bubble_map",
        "latitude": "lat",
        "longitude": "lon",
        "geo_source": "us-states",
        "size": "n",
    }
    style = {"charts": {"point_map": {"tints": {"steps": 5}}}}
    with pytest.raises(ValidationError, match=r"tints\.ground\n\s+Field required"):
        _resolved(chart, _CITY_ROWS, style)


def test_base_leaves_the_scatter_values_off() -> None:
    scatter = get_theme_style().charts.scatter
    assert (scatter.frame, scatter.series_symbols, scatter.marks.point.halo) == (
        "full",
        None,
        None,
    )


def test_frame_full_resolves_like_the_unframed_default() -> None:
    chart = {"type": "scatter", "x": "x", "y": "y", "color": "g"}
    unframed = _resolved(chart, _SCATTER_ROWS, {})
    full = _resolved(chart, _SCATTER_ROWS, {"charts": {"scatter": {"frame": "full"}}})
    assert unframed.style.frame == "full"
    assert full == unframed


def test_a_theme_scatter_frame_reaches_the_resolved_style() -> None:
    resolved = _resolved(
        {"type": "scatter", "x": "x", "y": "y", "color": "g"},
        _SCATTER_ROWS,
        {"charts": {"scatter": _INK_SCATTER}},
    )
    assert resolved.style.frame == "range"
    assert resolved.style.series_symbols == tuple(_INK_SCATTER["series_symbols"])
    assert resolved.style.point_mark.halo == 1
    assert resolved.style.point_mark.size == 42.25


@pytest.mark.parametrize(
    "patch",
    [
        {"frame": "offset"},
        {"series_symbols": ["star"]},
        {"series_symbols": []},
        {"series_symbols": ["plus", "cross", "plus"]},
        {"marks": {"point": {"halo": -1}}},
    ],
)
def test_scatter_values_outside_their_vocabulary_are_refused(
    patch: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        StylePatch.model_validate({"charts": {"scatter": patch}})


@pytest.mark.parametrize("family", ["marks", "line.marks", "area.marks"])
def test_the_halo_exists_only_on_the_families_that_draw_it(family: str) -> None:
    patch: dict[str, Any] = {"point": {"halo": 1}}
    for key in reversed(family.split(".")):
        patch = {key: patch}
    with pytest.raises(ValidationError, match=r"point\.halo\n\s+Extra inputs"):
        StylePatch.model_validate({"charts": patch})


_LAYERED_LINE = {
    "type": "line",
    "x": "x",
    "y": "y",
    "layers": [{"type": "scatter", "y": "y"}],
}


def test_a_scatter_layer_has_no_halo_to_set() -> None:
    layer = {"type": "scatter", "y": "y", "style": {"marks": {"point": {"halo": 1}}}}
    with pytest.raises(ValidationError, match=r"point\.halo\n\s+Extra inputs"):
        TypeAdapter(Chart).validate_python(
            {"id": "t", **_LAYERED_LINE, "layers": [layer]}
        )


def test_a_scatter_halo_in_the_theme_does_not_reach_a_scatter_layer() -> None:
    with_halo = _resolved(
        _LAYERED_LINE,
        _SCATTER_ROWS,
        {"charts": {"scatter": {"marks": {"point": {"halo": 1}}}}},
    )
    without_halo = _resolved(_LAYERED_LINE, _SCATTER_ROWS, {})
    assert with_halo.layers[0].point_mark == without_halo.layers[0].point_mark


def _end_label_formats(
    rows: list[dict[str, Any]], scatter: dict[str, Any]
) -> list[str | None]:
    style = _resolved(
        {"type": "scatter", "x": "x", "y": "y"},
        rows,
        {"charts": {"scatter": scatter}},
    ).style
    return [
        fmt.spec if (fmt := axis.labels.format) is not None else None
        for axis in (style.axis_x, style.axis_y)
    ]


def test_a_framed_axis_with_no_format_leaves_its_ends_to_the_range_end_rule() -> None:
    whole = [{"x": 12, "y": 2}, {"x": 59, "y": 3}]
    assert _end_label_formats(whole, _INK_SCATTER) == [None, None]
    fractional = [{"x": 12.5, "y": 2.5}, {"x": 59.5, "y": 3.5}]
    assert _end_label_formats(fractional, _INK_SCATTER) == [None, None]
    assert _end_label_formats(whole, {}) == [".3~s", ".3~s"]


def test_a_framed_axis_keeps_an_authored_label_format() -> None:
    rows = [{"x": 12, "y": 2}, {"x": 59, "y": 3}]
    axis = {"labels": {"format": ",.1f"}}
    scatter = {**_INK_SCATTER, "axis_x": axis}
    assert _end_label_formats(rows, scatter) == [",.1f", None]
    named = {**_INK_SCATTER, "axis_x": {"labels": {"format": "number"}}}
    assert _end_label_formats(rows, named) == [".3~s", None]
