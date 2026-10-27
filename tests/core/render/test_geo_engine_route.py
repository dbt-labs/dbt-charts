"""The geo families render in the engine and never reach Vega-Lite."""

from __future__ import annotations

import pytest
import vl_convert

from dbt_charts.core.diagnostics.codes_render import (
    ERR_CHART_PAINTED_NO_MARKS,
    ERR_GEO_SOURCE_UNAVAILABLE,
    ERR_MAP_LOOKUP_KEY_MISMATCH,
    WARN_POINT_MAP_NEGATIVE_SIZE_VALUES,
    WARN_POINT_MAP_OUT_OF_PROJECTION,
)
from dbt_charts.core.render import layout_sizing
from dbt_charts.core.render.geo.loader import geometry_fetcher_scope
from dbt_charts.core.render.render_result import RenderResult

from .._svg_render import render_board_svg_result

_POPULATION = """
  q:
    type: values
    rows:
      - {state_code: "9", pop: 742}
      - {state_code: "23", pop: 44}
      - {state_code: "25", pop: 901}
"""

_CITIES = """
  q:
    type: values
    rows:
      - {lat: 34.05, lng: -118.25, profit: 100}
      - {lat: 40.71, lng: -74.01, profit: 0}
      - {lat: 47.61, lng: -122.33, profit: -25}
      - {lat: 9.0, lng: 168.0, profit: 50}
"""

_MAPS = {
    "map": (
        _POPULATION,
        "type: map\n    geo:\n      source: us-states\n"
        "    lookup: state_code\n    value: pop",
    ),
    "geoshape": (
        _POPULATION,
        "type: geoshape\n    geo:\n      source: us-states\n"
        "    lookup: state_code\n    value: pop",
    ),
    "point_map": (
        _CITIES,
        "type: point_map\n    latitude: lat\n    longitude: lng\n"
        "    projection: mercator",
    ),
    "bubble_map": (
        _CITIES,
        "type: bubble_map\n    latitude: lat\n    longitude: lng\n"
        "    size: profit\n    projection: mercator",
    ),
}


def _board(queries: str, chart: str) -> str:
    return (
        f"title: T\nqueries:{queries}"
        f"charts:\n  c:\n    query: q\n    {chart}\nrows:\n  - c\n"
    )


def _render(queries: str, chart: str) -> RenderResult:
    return render_board_svg_result(_board(queries, chart))


@pytest.mark.parametrize("chart_type", sorted(_MAPS))
def test_a_geo_chart_renders_in_the_engine_without_vega_lite(
    chart_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the Vega-Lite path ran")

    monkeypatch.setattr(vl_convert, "vegalite_to_svg", refuse)
    monkeypatch.setattr(vl_convert, "vegalite_to_scenegraph", refuse)

    result = _render(*_MAPS[chart_type])

    assert not result.chart_errors
    mark_class = "mark-shape" if chart_type in {"map", "geoshape"} else "mark-symbol"
    assert f'class="{mark_class} role-mark' in result.output
    assert result.output.count('role="graphics-symbol"') >= 3


@pytest.mark.parametrize("chart_type", sorted(_MAPS))
def test_a_geo_cell_is_sized_without_rendering_first(
    chart_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a geo cell was rendered to measure it")

    monkeypatch.setattr(layout_sizing, "_render_chart_to_svg", refuse)

    assert not _render(*_MAPS[chart_type]).chart_errors


def test_out_of_projection_points_warn_with_the_dropped_count() -> None:
    result = _render(
        _CITIES,
        "type: point_map\n    latitude: lat\n    longitude: lng\n"
        "    projection: albersUsa",
    )

    (warning,) = [
        w for w in result.warnings if w.code == "WARN-POINT-MAP-OUT-OF-PROJECTION"
    ]
    assert warning.chart == "c"
    assert warning.path == "charts.c.projection"
    assert warning.message == WARN_POINT_MAP_OUT_OF_PROJECTION.message_template.format(
        chart_id="c", dropped_count=1, total_count=4, projection="albersUsa"
    )
    assert warning.fix == WARN_POINT_MAP_OUT_OF_PROJECTION.fix_template


def test_negative_sizes_warn_with_the_dropped_count() -> None:
    result = _render(
        _CITIES,
        "type: bubble_map\n    latitude: lat\n    longitude: lng\n"
        "    size: profit\n    projection: mercator",
    )

    (warning,) = [
        w for w in result.warnings if w.code == "WARN-POINT-MAP-NEGATIVE-SIZE-VALUES"
    ]
    assert warning.chart == "c"
    assert warning.path == "charts.c.size"
    assert warning.field == "profit"
    assert (
        warning.message
        == WARN_POINT_MAP_NEGATIVE_SIZE_VALUES.message_template.format(
            chart_id="c", dropped_count=1, total_count=4, size_field="profit"
        )
    )
    assert warning.fix == WARN_POINT_MAP_NEGATIVE_SIZE_VALUES.fix_template.format(
        size_field="profit"
    )


def test_a_map_whose_points_all_fit_warns_of_nothing() -> None:
    result = _render(
        _CITIES,
        "type: point_map\n    latitude: lat\n    longitude: lng\n"
        "    projection: mercator",
    )

    assert [w.code for w in result.warnings if "POINT-MAP" in w.code] == []


def test_lookup_keys_that_cannot_match_the_source_fail_the_chart() -> None:
    result = _render(
        """
  q:
    type: values
    rows:
      - {code: USA, launches: 100}
""",
        "type: map\n    geo:\n      source: world-countries\n"
        "    lookup: code\n    value: launches",
    )

    (error,) = result.chart_errors
    assert error.code == ERR_MAP_LOOKUP_KEY_MISMATCH.code
    assert "world-countries" in error.message
    assert "code" in error.message
    assert "USA" in error.message


def test_an_unreachable_geo_source_fails_the_chart() -> None:
    def unreachable(url: str) -> bytes:
        raise OSError("connection refused")

    with geometry_fetcher_scope(unreachable):
        result = _render(
            _POPULATION,
            "type: map\n    geo:\n      source: https://example.test/none.json\n"
            "    projection: mercator\n    lookup: state_code\n    value: pop",
        )

    (error,) = result.chart_errors
    assert error.code == ERR_GEO_SOURCE_UNAVAILABLE.code
    assert "https://example.test/none.json" in error.message
    assert "connection refused" in error.message


def test_a_point_map_with_one_coordinate_field_paints_no_marks() -> None:
    result = _render(
        """
  q:
    type: values
    rows:
      - {la: 40.7, pop: 10}
      - {la: 34.0, pop: 20}
""",
        "type: point_map\n    latitude: la\n    projection: mercator",
    )

    (error,) = result.chart_errors
    assert error.code == ERR_CHART_PAINTED_NO_MARKS.code


@pytest.mark.parametrize("chart_type", ["map", "bubble_map"])
def test_a_titled_map_beside_a_subtitled_chart_shares_its_row_title_band(
    chart_type: str,
) -> None:
    queries, chart = _MAPS[chart_type]
    board = (
        f"title: T\nqueries:{queries}"
        f"charts:\n  m:\n    query: q\n    title: Map\n    {chart}\n"
        "  b:\n    query: q\n    type: table\n    title: Side\n    subtitle: Sub\n"
        "cols:\n  - m\n  - b\n"
    )

    result = render_board_svg_result(board)

    assert not result.chart_errors
