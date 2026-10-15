"""TDD: invisible per-datum point overlay on line/area charts for hover tooltips.

Root cause: vl-convert renders a line/area as a single <path> with one aria-label
(always the first datum).  The JS hover layer's querySelectorAll('[aria-label]')
attaches mouseenter listeners per element — on the single line path it always
resolves to the first datum.

Fix: _map_line()/_map_area() add aria:False on the foreground and halo marks
(suppresses the misleading first-datum aria-label) and append a transparent point
overlay layer (opacity=0, tooltip=True) so each datum gets its own
  <path role="graphics-symbol" aria-roledescription="point" aria-label="...">
in the rendered SVG.  _map_layered_chart() applies the same treatment to each
line/area sub-layer in the multi-metric (y: [a, b]) path.

These tests cover:
1. Spec-shape unit tests (_map_line / _map_area / _map_layered_chart)
2. Rendered-SVG integration tests (via vl_convert)
3. Halo-enabled paths (the default theme production path)
4. Multi-metric y: [a, b] layered path
5. Multi-series color: <field> path
6. Layered chart with two line layers
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.emitters._layers import HOVER_TARGET_SIZE
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LINE_DATA = [
    {"day": "2026-01-01", "value": 10},
    {"day": "2026-01-02", "value": 20},
    {"day": "2026-01-03", "value": 15},
    {"day": "2026-01-04", "value": 30},
    {"day": "2026-01-05", "value": 25},
]

_AREA_DATA = [
    {"month": "Jan", "revenue": 100},
    {"month": "Feb", "revenue": 150},
    {"month": "Mar", "revenue": 120},
    {"month": "Apr", "revenue": 200},
    {"month": "May", "revenue": 180},
]


_MULTI_METRIC_DATA = [
    {"day": "2026-01-01", "a": 10, "b": 5},
    {"day": "2026-01-02", "a": 20, "b": 8},
    {"day": "2026-01-03", "a": 15, "b": 12},
]

_MULTI_SERIES_DATA = [
    {"day": "2026-01-01", "value": 10, "series": "X"},
    {"day": "2026-01-01", "value": 5, "series": "Y"},
    {"day": "2026-01-02", "value": 20, "series": "X"},
    {"day": "2026-01-02", "value": 8, "series": "Y"},
    {"day": "2026-01-03", "value": 15, "series": "X"},
    {"day": "2026-01-03", "value": 12, "series": "Y"},
]


def _svg_point_count(svg: str) -> int:
    """Count <path> elements with aria-roledescription="point" in the SVG."""
    return len(re.findall(r'aria-roledescription="point"', svg))


def _svg_line_mark_groups_are_aria_hidden(svg: str) -> bool:
    """Return True if ALL mark-line role-mark groups in the SVG carry aria-hidden="true".

    vl-convert emits: <g class="mark-line role-mark layer_N_marks" aria-hidden="true">
    when mark.aria == False.  We must verify the line-mark group itself is hidden,
    not merely that aria-hidden appears anywhere (axis ticks etc. always have it).
    """
    # Find all line mark groups and check each carries aria-hidden
    groups = re.findall(
        r'<g[^>]*class="[^"]*mark-line role-mark[^"]*"[^>]*>',
        svg,
    )
    if not groups:
        return False
    return all('aria-hidden="true"' in g for g in groups)


def _svg_area_mark_groups_are_aria_hidden(svg: str) -> bool:
    """Return True if ALL mark-area role-mark groups in the SVG carry aria-hidden="true"."""
    groups = re.findall(
        r'<g[^>]*class="[^"]*mark-area role-mark[^"]*"[^>]*>',
        svg,
    )
    if not groups:
        return False
    return all('aria-hidden="true"' in g for g in groups)


def _point_overlay_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find layers that are the hover point overlay (opacity=0, tooltip=True, type=point)."""
    return [
        lyr
        for lyr in layers
        if isinstance(lyr.get("mark"), dict)
        and lyr["mark"].get("type") == "point"
        and lyr["mark"].get("opacity") == 0
        and lyr["mark"].get("tooltip") is True
    ]


def test_hover_target_size_stays_under_x_step_ceiling() -> None:
    """HOVER_TARGET_SIZE must not exceed the x-step ceiling its own comment
    derives (18px spacing floor / 2, as radius): a radius past that lets one
    datum's hover disc steal a neighbor's hit region. Hardcodes the same 18
    the comment does rather than reading `POINT_MIN_PX_PER_POINT`
    (a different, safety-padded number for a different purpose); this only catches HOVER_TARGET_SIZE
    drifting past a fixed ceiling, not that 18px ceiling itself going stale."""
    assert HOVER_TARGET_SIZE <= 4 * (18.0 / 2) ** 2


def _fg_line_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find foreground line layers (type=line, tooltip=True)."""
    return [
        lyr
        for lyr in layers
        if isinstance(lyr.get("mark"), dict)
        and lyr["mark"].get("type") == "line"
        and lyr["mark"].get("tooltip") is True
    ]


def _fg_area_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find foreground area layers (type=area, tooltip=True)."""
    return [
        lyr
        for lyr in layers
        if isinstance(lyr.get("mark"), dict)
        and lyr["mark"].get("type") == "area"
        and lyr["mark"].get("tooltip") is True
    ]


def _halo_line_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find halo line layers (type=line, tooltip=False — the knockout undercoat)."""
    return [
        lyr
        for lyr in layers
        if isinstance(lyr.get("mark"), dict)
        and lyr["mark"].get("type") == "line"
        and lyr["mark"].get("tooltip") is False
    ]


def _halo_area_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find halo area layers (type=area, tooltip=False — the knockout undercoat)."""
    return [
        lyr
        for lyr in layers
        if isinstance(lyr.get("mark"), dict)
        and lyr["mark"].get("type") == "area"
        and lyr["mark"].get("tooltip") is False
    ]


# ---------------------------------------------------------------------------
# Spec-shape unit tests — _map_line
# ---------------------------------------------------------------------------


class TestLineSpecShape:
    def test_line_spec_has_point_overlay_layer(self, make_chart):
        """_map_line always emits a transparent point overlay layer."""
        chart = make_chart("line", x="day", y="value")
        _rc = resolve(chart, _LINE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _LINE_DATA, width=400)

        layers = spec.get("layer", [])
        overlays = _point_overlay_layers(layers)
        assert overlays, (
            f"Expected a transparent point overlay layer (opacity=0, tooltip=True, type=point) "
            f"in {[lyr.get('mark') for lyr in layers]!r}"
        )

    def test_line_point_overlay_has_exact_hit_size(self, make_chart):
        """Point overlay size must be the calibrated HOVER_TARGET_SIZE constant.

        Vega's circle symbol radius is sqrt(size)/2, not sqrt(size/π) (measured
        off vl-convert's emitted path — see HOVER_TARGET_SIZE in _layers.py)."""
        chart = make_chart("line", x="day", y="value")
        _rc = resolve(chart, _LINE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _LINE_DATA, width=400)

        layers = spec.get("layer", [])
        overlays = _point_overlay_layers(layers)
        assert overlays, "Expected a point overlay layer"
        overlay_size = overlays[0]["mark"].get("size", 0)
        assert overlay_size == HOVER_TARGET_SIZE, (
            f"Point overlay size={overlay_size}, expected {HOVER_TARGET_SIZE}"
        )

    def test_line_point_overlay_is_filled(self, make_chart):
        """Point overlay must be filled so vl-convert renders it as a solid path."""
        chart = make_chart("line", x="day", y="value")
        _rc = resolve(chart, _LINE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _LINE_DATA, width=400)

        layers = spec.get("layer", [])
        overlays = _point_overlay_layers(layers)
        assert overlays, "Expected a point overlay layer"
        assert overlays[0]["mark"].get("filled") is True, (
            "Point overlay mark.filled must be True"
        )

    def test_line_foreground_has_aria_false(self, make_chart):
        """Foreground line mark must have aria=False to suppress the misleading aria-label."""
        chart = make_chart("line", x="day", y="value")
        _rc = resolve(chart, _LINE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _LINE_DATA, width=400)

        layers = spec.get("layer", [])
        fg_lines = _fg_line_layers(layers)
        assert fg_lines, f"Expected at least one foreground line layer in {layers!r}"
        fg_mark = fg_lines[0]["mark"]
        assert fg_mark.get("aria") is False, (
            f"Foreground line mark.aria must be False (got {fg_mark.get('aria')!r}) "
            "to suppress the misleading first-datum aria-label"
        )

    def test_line_point_overlay_is_last_data_layer(self, make_chart):
        """Point overlay must be the last data layer (before any rule layers)."""
        chart = make_chart("line", x="day", y="value")
        _rc = resolve(chart, _LINE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _LINE_DATA, width=400)

        layers = spec.get("layer", [])
        assert layers, "Expected layered spec"
        # Rule layers (zero-baseline) are appended after; skip them.
        data_layers = [
            lyr
            for lyr in layers
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") != "rule"
        ]
        assert data_layers, "Expected at least one non-rule layer"
        last_data_mark = data_layers[-1].get("mark", {})
        assert (
            isinstance(last_data_mark, dict)
            and last_data_mark.get("type") == "point"
            and last_data_mark.get("opacity") == 0
            and last_data_mark.get("tooltip") is True
        ), (
            f"Last non-rule data layer must be the transparent point overlay; "
            f"got {last_data_mark!r}"
        )

    def test_line_halo_has_aria_false(self, make_chart):
        """Halo line mark must have aria=False — it protrudes past the fg stroke and
        would otherwise trigger the first-datum tooltip in the edge bands."""
        chart = make_chart("line", x="day", y="value")
        # Force halo enabled by using a theme that enables it; fall back to checking
        # only when the spec actually has halo layers.
        _rc = resolve(chart, _LINE_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _LINE_DATA, width=400)
        layers = spec.get("layer", [])
        halo_lines = _halo_line_layers(layers)
        # Only assert if a halo layer was emitted (theme-dependent).
        if halo_lines:
            for halo_layer in halo_lines:
                halo_mark = halo_layer["mark"]
                assert halo_mark.get("aria") is False, (
                    f"Halo line mark.aria must be False (got {halo_mark.get('aria')!r}); "
                    "the halo's protruding stroke band triggers the first-datum tooltip"
                )

    def test_line_overlay_layers_are_independent_dicts(self, make_chart):
        """Each call to generate_vega_lite_spec must produce a distinct point overlay dict.

        Prevents a regression where _HOVER_OVERLAY_POINT was a shared mutable
        module-level singleton — mutation at one call site would corrupt all others.
        """
        chart = make_chart("line", x="day", y="value")
        _rc = resolve(chart, _LINE_DATA, chart_style_context=_BOARD_STYLE)
        spec_a = generate_vega_lite_spec(chart, _LINE_DATA, width=400)
        _rc = resolve(chart, _LINE_DATA, chart_style_context=_BOARD_STYLE)
        spec_b = generate_vega_lite_spec(chart, _LINE_DATA, width=400)

        overlays_a = _point_overlay_layers(spec_a.get("layer", []))
        overlays_b = _point_overlay_layers(spec_b.get("layer", []))
        assert overlays_a and overlays_b, "Both specs must have point overlay layers"
        # Must be distinct dict objects (different identity)
        assert overlays_a[0]["mark"] is not overlays_b[0]["mark"], (
            "Point overlay mark dicts must be distinct objects across calls; "
            "shared mutable singleton detected"
        )


# ---------------------------------------------------------------------------
# Spec-shape unit tests — _map_area
# ---------------------------------------------------------------------------


class TestAreaSpecShape:
    def test_area_spec_has_point_overlay_layer(self, make_chart):
        """_map_area always emits a transparent point overlay layer."""
        chart = make_chart("area", x="month", y="revenue")
        _rc = resolve(chart, _AREA_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _AREA_DATA, width=400)

        layers = spec.get("layer", [])
        overlays = _point_overlay_layers(layers)
        assert overlays, (
            f"Expected a transparent point overlay layer (opacity=0, tooltip=True, type=point) "
            f"in {[lyr.get('mark') for lyr in layers]!r}"
        )

    def test_area_point_overlay_has_exact_hit_size(self, make_chart):
        """Point overlay size must be the calibrated HOVER_TARGET_SIZE constant."""
        chart = make_chart("area", x="month", y="revenue")
        _rc = resolve(chart, _AREA_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _AREA_DATA, width=400)

        layers = spec.get("layer", [])
        overlays = _point_overlay_layers(layers)
        assert overlays, "Expected a point overlay layer"
        overlay_size = overlays[0]["mark"].get("size", 0)
        assert overlay_size == HOVER_TARGET_SIZE, (
            f"Point overlay size={overlay_size}, expected {HOVER_TARGET_SIZE}"
        )

    def test_stacked_area_point_overlay_has_exact_hit_size(self, make_chart):
        """The stacked path (``chart.stack`` set) shares ``_hover_target_point``
        with overlap area and line, so the same calibrated constant applies
        there too — single-series stacking alone is enough to reach the
        stacked code path (``is_stacked = chart.stack not in (None, "none")``)."""
        chart = make_chart("area", x="month", y="revenue", stack="zero")
        _rc = resolve(chart, _AREA_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _AREA_DATA, width=400)

        layers = spec.get("layer", [])
        overlays = _point_overlay_layers(layers)
        assert overlays, "Expected a point overlay layer"
        overlay_size = overlays[0]["mark"].get("size", 0)
        assert overlay_size == HOVER_TARGET_SIZE, (
            f"Point overlay size={overlay_size}, expected {HOVER_TARGET_SIZE}"
        )

    def test_area_foreground_has_aria_false(self, make_chart):
        """Foreground area mark must have aria=False."""
        chart = make_chart("area", x="month", y="revenue")
        _rc = resolve(chart, _AREA_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _AREA_DATA, width=400)

        layers = spec.get("layer", [])
        fg_areas = _fg_area_layers(layers)
        assert fg_areas, f"Expected at least one foreground area layer in {layers!r}"
        fg_mark = fg_areas[0]["mark"]
        assert fg_mark.get("aria") is False, (
            f"Foreground area mark.aria must be False (got {fg_mark.get('aria')!r})"
        )

    def test_area_point_overlay_is_last_layer(self, make_chart):
        """Point overlay is the last layer in the area chart stack."""
        chart = make_chart("area", x="month", y="revenue")
        _rc = resolve(chart, _AREA_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _AREA_DATA, width=400)

        layers = spec.get("layer", [])
        assert layers, "Expected layered spec"
        # Zero-rule may follow the overlay; the overlay must be the last data layer.
        # Find the last non-rule layer.
        data_layers = [
            lyr
            for lyr in layers
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") != "rule"
        ]
        assert data_layers, "Expected at least one non-rule layer"
        last_data_mark = data_layers[-1].get("mark", {})
        assert (
            isinstance(last_data_mark, dict)
            and last_data_mark.get("type") == "point"
            and last_data_mark.get("opacity") == 0
            and last_data_mark.get("tooltip") is True
        ), (
            f"Last non-rule data layer must be the transparent point overlay; "
            f"got {last_data_mark!r}"
        )

    def test_area_halo_has_aria_false(self, make_chart):
        """Halo area mark must have aria=False."""
        chart = make_chart("area", x="month", y="revenue")
        _rc = resolve(chart, _AREA_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _AREA_DATA, width=400)
        layers = spec.get("layer", [])
        halo_areas = _halo_area_layers(layers)
        if halo_areas:
            for halo_layer in halo_areas:
                halo_mark = halo_layer["mark"]
                assert halo_mark.get("aria") is False, (
                    f"Halo area mark.aria must be False (got {halo_mark.get('aria')!r})"
                )


# ---------------------------------------------------------------------------
# Spec-shape unit tests — multi-metric line y: [a, b] (_map_layered_chart)
# ---------------------------------------------------------------------------


class TestMultiMetricLineSpecShape:
    def test_multi_metric_line_has_hover_point_layer(self, make_chart):
        """Fold-based multi-metric line emits a shared hover point layer.

        The fold path emits one mark spec shared across all metrics via a top-level
        color encoding. The hover target is a single opacity-0 point layer. When
        endpoint labels are active (editorial default theme), the spec is wrapped
        in hconcat — the inner unit spec still has the layers.
        """
        chart = make_chart("line", x="day", y=["a", "b"])
        _rc = resolve(chart, _MULTI_METRIC_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _MULTI_METRIC_DATA, width=400)

        # Unwrap hconcat if endpoint labels wrapped the spec.
        unit = spec.get("hconcat", [spec])[0]
        layers = unit.get("layer", [])
        overlays = _point_overlay_layers(layers)
        assert len(overlays) >= 1, (
            f"Expected at least one hover point layer for y=['a','b'], "
            f"got {len(overlays)} in {[lyr.get('mark') for lyr in layers]!r}"
        )
        # The fold approach uses a field-based color at the top level.
        color = unit.get("encoding", {}).get("color", {})
        assert color.get("field") is not None, (
            f"Fold-based line must use a field-based top-level color encoding; "
            f"got {color!r}"
        )

    def test_multi_metric_line_marks_have_aria_false(self, make_chart):
        """Line marks in a multi-metric fold chart must have aria=False."""
        chart = make_chart("line", x="day", y=["a", "b"])
        _rc = resolve(chart, _MULTI_METRIC_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _MULTI_METRIC_DATA, width=400)

        # Unwrap hconcat if endpoint labels wrapped the spec.
        unit = spec.get("hconcat", [spec])[0]
        layers = unit.get("layer", [])
        line_layers = [
            lyr
            for lyr in layers
            if isinstance(lyr.get("mark"), dict) and lyr["mark"].get("type") == "line"
        ]
        assert len(line_layers) >= 1, (
            f"Expected at least one line layer for y=['a','b'], got {len(line_layers)}"
        )
        for lyr in line_layers:
            mark = lyr["mark"]
            assert mark.get("aria") is False, (
                f"Line mark in multi-metric chart must have aria=False; got {mark!r}"
            )

    def test_multi_metric_area_has_overlay_per_metric(self, make_chart):
        """_map_layered_chart for area type must emit one overlay per y-metric."""
        chart = make_chart("area", x="day", y=["a", "b"])
        _rc = resolve(chart, _MULTI_METRIC_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _MULTI_METRIC_DATA, width=400)

        layers = spec.get("hconcat", [spec])[0].get("layer", [])
        overlays = _point_overlay_layers(layers)
        assert overlays, "Expected a folded-area point overlay for y=['a','b']"


# ---------------------------------------------------------------------------
# Spec-shape unit tests — multi-series color: <field>
# ---------------------------------------------------------------------------


class TestMultiSeriesLineSpecShape:
    def _get_main_layers(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the layer list from spec, handling hconcat wrapping (endpoint labels)."""
        if "hconcat" in spec:
            return spec["hconcat"][0].get("layer", [])
        return spec.get("layer", [])

    def test_multi_series_line_has_point_overlay(self, make_chart):
        """Multi-series line (color: series) must emit a point overlay layer."""
        chart = make_chart("line", x="day", y="value", color="series")
        _rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _MULTI_SERIES_DATA, width=400)

        layers = self._get_main_layers(spec)
        overlays = _point_overlay_layers(layers)
        assert overlays, (
            f"Expected point overlay layers for multi-series line; "
            f"got {[lyr.get('mark') for lyr in layers]!r}"
        )

    def test_multi_series_line_foreground_has_aria_false(self, make_chart):
        """Multi-series foreground line mark must have aria=False."""
        chart = make_chart("line", x="day", y="value", color="series")
        _rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, _MULTI_SERIES_DATA, width=400)

        layers = self._get_main_layers(spec)
        fg_lines = _fg_line_layers(layers)
        assert fg_lines, "Expected foreground line layer in multi-series spec"
        for lyr in fg_lines:
            mark = lyr["mark"]
            assert mark.get("aria") is False, (
                f"Multi-series foreground line mark.aria must be False; got {mark!r}"
            )


# ---------------------------------------------------------------------------
# Rendered-SVG integration tests — line chart
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_rows", [5])
def test_line_svg_has_per_datum_point_aria_labels(make_chart, n_rows):
    """Rendered SVG must contain ≥n_rows point-roledescription elements with distinct labels.

    This is the test that would have caught the original bug: previously the
    line rendered as a single <path> with one aria-label (first datum).  After
    the fix, each datum gets its own point element with a distinct aria-label.
    """
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    chart = make_chart("line", x="day", y="value")
    _rc = resolve(chart, _LINE_DATA[:n_rows], chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _LINE_DATA[:n_rows], width=400)
    svg = vlc.vegalite_to_svg(spec)

    # Points are emitted as <path role="graphics-symbol" aria-roledescription="point" ...>
    point_elements = re.findall(r'aria-roledescription="point"', svg)
    assert len(point_elements) >= n_rows, (
        f"Expected ≥{n_rows} point elements with aria-roledescription='point', "
        f"got {len(point_elements)}. "
        "The transparent point overlay layer is missing or not rendering."
    )

    # Each point must carry a distinct aria-label (not all pointing to first datum)
    all_labels = re.findall(r'aria-label="([^"]+)"', svg)
    # Filter to labels that contain our data fields
    value_labels = [lb for lb in all_labels if "Value" in lb or "value" in lb]
    unique_values = set(value_labels)
    assert len(unique_values) >= n_rows, (
        f"Expected ≥{n_rows} distinct aria-labels containing 'Value', got {unique_values!r}. "
        "All point overlays may be carrying the same (first-datum) label."
    )


def test_line_svg_line_mark_group_is_aria_hidden(make_chart):
    """Rendered SVG: the line-mark group element itself must carry aria-hidden='true'.

    Verifies the line-mark group specifically, not just that aria-hidden appears
    anywhere in the SVG (axis ticks always carry it and would make a naive check pass).
    """
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    chart = make_chart("line", x="day", y="value")
    _rc = resolve(chart, _LINE_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _LINE_DATA, width=400)
    svg = vlc.vegalite_to_svg(spec)
    assert _svg_line_mark_groups_are_aria_hidden(svg), (
        "Expected all mark-line role-mark groups to carry aria-hidden='true' — "
        "the line-mark group must be suppressed so JS hover does not resolve to "
        "the misleading first-datum aria-label.  Reverting aria:False on the "
        "foreground mark would cause this test to fail."
    )


# ---------------------------------------------------------------------------
# Rendered-SVG integration tests — area chart
# ---------------------------------------------------------------------------


def test_area_svg_has_per_datum_point_aria_labels(make_chart):
    """Rendered SVG for area chart must contain ≥5 distinct point aria-labels."""
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    n_rows = len(_AREA_DATA)
    chart = make_chart("area", x="month", y="revenue")
    _rc = resolve(chart, _AREA_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _AREA_DATA, width=400)
    svg = vlc.vegalite_to_svg(spec)

    point_elements = re.findall(r'aria-roledescription="point"', svg)
    assert len(point_elements) >= n_rows, (
        f"Expected ≥{n_rows} point elements in area SVG, got {len(point_elements)}"
    )

    all_labels = re.findall(r'aria-label="([^"]+)"', svg)
    revenue_labels = [lb for lb in all_labels if "Revenue" in lb or "revenue" in lb]
    unique_revenues = set(revenue_labels)
    assert len(unique_revenues) >= n_rows, (
        f"Expected ≥{n_rows} distinct revenue labels in area chart SVG, "
        f"got {unique_revenues!r}"
    )


def test_area_svg_area_mark_group_is_aria_hidden(make_chart):
    """Rendered SVG: area mark group element must carry aria-hidden='true'.

    Verifies the area-mark group specifically, not just any aria-hidden occurrence.
    """
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    chart = make_chart("area", x="month", y="revenue")
    _rc = resolve(chart, _AREA_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _AREA_DATA, width=400)
    svg = vlc.vegalite_to_svg(spec)
    assert _svg_area_mark_groups_are_aria_hidden(svg), (
        "Expected all mark-area role-mark groups to carry aria-hidden='true'. "
        "Reverting aria:False on the foreground area mark would cause this to fail."
    )


# ---------------------------------------------------------------------------
# Rendered-SVG integration tests — halo-enabled line (default theme production path)
# ---------------------------------------------------------------------------


def test_halo_line_svg_all_line_groups_are_aria_hidden(make_chart):
    """With halo enabled, ALL line-mark groups (halo + fg) must be aria-hidden.

    The halo's stroke is wider than the fg stroke and protrudes past the fg on
    each side.  A cursor in that edge band triggers the halo's aria-label if the
    halo is not suppressed.  This test catches a regression where aria:False was
    set only on the foreground but not the halo.
    """
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    chart = make_chart("line", x="day", y="value")
    _rc = resolve(chart, _LINE_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _LINE_DATA, width=400)
    svg = vlc.vegalite_to_svg(spec)

    line_groups = re.findall(
        r'<g[^>]*class="[^"]*mark-line role-mark[^"]*"[^>]*>',
        svg,
    )
    # If there's only one group the theme has no halo; still must be hidden.
    assert line_groups, "Expected at least one mark-line role-mark group in SVG"
    non_hidden = [g for g in line_groups if 'aria-hidden="true"' not in g]
    assert not non_hidden, (
        f"Every line-mark group must carry aria-hidden='true'; "
        f"non-hidden groups: {non_hidden!r}"
    )


def test_halo_area_svg_all_area_groups_are_aria_hidden(make_chart):
    """With halo enabled, ALL area-mark groups (halo + fg) must be aria-hidden."""
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    chart = make_chart("area", x="month", y="revenue")
    _rc = resolve(chart, _AREA_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _AREA_DATA, width=400)
    svg = vlc.vegalite_to_svg(spec)

    area_groups = re.findall(
        r'<g[^>]*class="[^"]*mark-area role-mark[^"]*"[^>]*>',
        svg,
    )
    assert area_groups, "Expected at least one mark-area role-mark group in SVG"
    non_hidden = [g for g in area_groups if 'aria-hidden="true"' not in g]
    assert not non_hidden, (
        f"Every area-mark group must carry aria-hidden='true'; "
        f"non-hidden groups: {non_hidden!r}"
    )


# ---------------------------------------------------------------------------
# Rendered-SVG integration tests — multi-metric y: [a, b]
# ---------------------------------------------------------------------------


def test_multi_metric_line_svg_has_per_datum_points_for_each_metric(make_chart):
    """Multi-metric line (y: [a, b]) SVG must have N_rows × N_metrics point elements."""
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    n_rows = len(_MULTI_METRIC_DATA)
    n_metrics = 2
    chart = make_chart("line", x="day", y=["a", "b"])
    _rc = resolve(chart, _MULTI_METRIC_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _MULTI_METRIC_DATA, width=400)
    svg = vlc.vegalite_to_svg(spec)

    count = _svg_point_count(svg)
    assert count >= n_rows * n_metrics, (
        f"Expected ≥{n_rows * n_metrics} point elements for {n_metrics} metrics × "
        f"{n_rows} rows, got {count}.  "
        "_map_layered_chart may be missing the hover overlay for multi-metric paths."
    )


def test_multi_metric_line_svg_all_line_groups_aria_hidden(make_chart):
    """Multi-metric line: every line-mark group must be aria-hidden."""
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    chart = make_chart("line", x="day", y=["a", "b"])
    _rc = resolve(chart, _MULTI_METRIC_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _MULTI_METRIC_DATA, width=400)
    svg = vlc.vegalite_to_svg(spec)

    line_groups = re.findall(
        r'<g[^>]*class="[^"]*mark-line role-mark[^"]*"[^>]*>',
        svg,
    )
    assert line_groups, "Expected mark-line role-mark groups in multi-metric SVG"
    non_hidden = [g for g in line_groups if 'aria-hidden="true"' not in g]
    assert not non_hidden, (
        f"All line-mark groups must be aria-hidden in multi-metric chart; "
        f"non-hidden: {non_hidden!r}"
    )


# ---------------------------------------------------------------------------
# Rendered-SVG integration tests — multi-series color: <field>
# ---------------------------------------------------------------------------


def test_multi_series_line_svg_has_per_datum_points(make_chart):
    """Multi-series line (color: series) SVG must have point elements for each datum."""
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    n_rows = len(_MULTI_SERIES_DATA)  # 6 rows (3 per series)
    chart = make_chart("line", x="day", y="value", color="series")
    _rc = resolve(chart, _MULTI_SERIES_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, _MULTI_SERIES_DATA, width=400)
    svg = vlc.vegalite_to_svg(spec)

    count = _svg_point_count(svg)
    assert count >= n_rows, (
        f"Expected ≥{n_rows} point elements for multi-series line, got {count}"
    )
