"""Regressions: which of a line chart's sub-layers carry a datum's structured
description.

Each datum is announced by exactly one mark: the foreground point when it is
drawn, else the invisible hover-target overlay. The halo point never is.

Halo point, first:

Root cause (confirmed from a real render's DOM): ``_line_halo_sublayers``
(``emitters/_layers.py``) sets ``aria: False`` on its stroke-halo sub-layer but
never on its point-halo sub-layer -- an inconsistent pair, not a deliberate
design split (the sibling halo/backdrop sub-layers in ``_area_halo_layers`` both
set it). The point halo therefore inherits the chart's shared ``description``
encoding like every other data mark, and because it sits earlier in DOM order
than the real foreground point, ``chart_interactivity.js``'s
``collectMatchingMarks`` (which dedups per (header, series) by first-DOM-order
match) picks the halo mark as the tooltip row's color source.
``markSeriesColor()`` then reads the halo's own paint -- the theme's halo/knockout
color, white by default -- so the x-unified tooltip draws a white swatch on its
white background: present in the DOM, invisible on screen.

The halo point is purely a decorative knockout mask (drawn under the fg point
and under the invisible oversized hover-target overlay, which is what the
pointer actually needs to hit for hover to work) -- it is never the pointer's
hit target, so suppressing its aria/description is safe. This mirrors the fix
already applied to the halo LINE sub-layer and to both of ``_area_halo_layers``'s
sub-layers.

The overlay is the harder case: it is the pointer's hit target, and the hover
runtime only finds labeled marks. It drops its description only when a
foreground point stands in for it; on a dense line (points suppressed) it is
the chart's only labeled mark and must keep it.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import pytest

from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.render.chart.emitters._tooltip import ROLE_SERIES
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_MULTI_SERIES_DATA = [
    {"day": "2026-01-01", "value": 10, "series": "X"},
    {"day": "2026-01-01", "value": 5, "series": "Y"},
    {"day": "2026-01-02", "value": 20, "series": "X"},
    {"day": "2026-01-02", "value": 8, "series": "Y"},
    {"day": "2026-01-03", "value": 15, "series": "X"},
    {"day": "2026-01-03", "value": 12, "series": "Y"},
]

# Forces the halo point sub-layer to be emitted regardless of the theme's
# density-based auto-point behavior (see compile/resolve/chart/line.py's
# bake_point_companions) -- an explicit authored size is the direct lever.
# endpoint_labels disabled so the spec keeps its plain top-level `layer` shape
# instead of the endpoint-label rail's `hconcat` wrapper -- irrelevant to this
# bug, and every helper here reads `spec["layer"]` directly.
_POINT_STYLE = {
    "marks": {"point": {"size": 120}},
    "endpoint_labels": {"visible": False},
}


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _halo_point_layers(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find halo point layers: type=point, tooltip=False (the knockout mask) --
    distinct from both the fg point (tooltip=True) and the invisible hover
    overlay (tooltip=True, opacity=0)."""
    return [
        lyr
        for lyr in layers
        if isinstance(lyr.get("mark"), dict)
        and lyr["mark"].get("type") == "point"
        and lyr["mark"].get("tooltip") is False
    ]


def test_line_halo_point_has_aria_false(make_chart):
    """Halo point mark.aria must be False, matching its sibling halo line mark."""
    chart = make_chart("line", x="day", y="value", color="series", style=_POINT_STYLE)
    spec = generate_vega_lite_spec(chart, _MULTI_SERIES_DATA, width=400)

    layers = spec.get("layer", [])
    halo_points = _halo_point_layers(layers)
    assert halo_points, f"Expected a halo point sub-layer in {layers!r}"
    for halo in halo_points:
        assert halo["mark"].get("aria") is False, (
            f"Halo point mark.aria must be False (got {halo['mark'].get('aria')!r}); "
            "an un-hidden halo point inherits the shared structured-tooltip "
            "description and, being earlier in DOM order than the foreground "
            "point, wins chart_interactivity.js's dedupe with its own (halo) "
            "paint -- producing a swatch the same color as the tooltip background"
        )


def test_line_halo_point_svg_group_is_aria_hidden(make_chart):
    """Rendered SVG: the halo point's mark-symbol group must carry aria-hidden.

    Distinguishes the halo group from the foreground point / hover-target
    groups by DOM order -- for this chart shape only the halo point sub-layer
    precedes the foreground point sub-layer among the point/symbol groups
    (see ``emit_line_layer``'s assembly order: halo point, then fg point, then
    the invisible hover target).
    """
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    chart = make_chart("line", x="day", y="value", color="series", style=_POINT_STYLE)
    spec = generate_vega_lite_spec(chart, _MULTI_SERIES_DATA, width=400)
    svg = vlc.vegalite_to_svg(spec)

    symbol_groups = re.findall(
        r'<g[^>]*class="[^"]*mark-symbol role-mark[^"]*"[^>]*>',
        svg,
    )
    assert len(symbol_groups) >= 2, (
        f"Expected at least 2 mark-symbol role-mark groups (halo point + fg "
        f"point), got {len(symbol_groups)}"
    )
    first_group = symbol_groups[0]
    assert 'aria-hidden="true"' in first_group, (
        "Expected the first (halo) mark-symbol group to carry aria-hidden='true'; "
        f"got {first_group!r}"
    )


# A size of 0 is the same resolved value density suppression bakes for a dense
# line (bake_point_companions), so it drives the no-foreground-point path.
_POINT_SIZES = pytest.mark.parametrize(
    "point_size", [120, 0], ids=["points-drawn", "points-suppressed"]
)


def _line_spec(make_chart, point_size: int) -> dict[str, Any]:
    style = {**_POINT_STYLE, "marks": {"point": {"size": point_size}}}
    chart = make_chart("line", x="day", y="value", color="series", style=style)
    return generate_vega_lite_spec(chart, _MULTI_SERIES_DATA, width=400)


def _render_line_svg(make_chart, point_size: int) -> str:
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    return vlc.vegalite_to_svg(_line_spec(make_chart, point_size))


def _series_colors(node: Any) -> dict[str, str] | None:
    """series -> color, from the spec's own ``color: series`` scale."""
    if isinstance(node, dict):
        color = node.get("color")
        if isinstance(color, dict) and color.get("field") == "series":
            scale = color["scale"]
            return dict(zip(scale["domain"], scale["range"], strict=True))
        node = list(node.values())
    if isinstance(node, list):
        for child in node:
            if (found := _series_colors(child)) is not None:
                return found
    return None


def _datum_marks(svg: str) -> list[tuple[str, dict[str, str]]]:
    """(aria-label, attributes) of every element announcing a structured datum."""
    marks = []
    for tag in re.findall(r"<path\b[^>]*>", svg):
        attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', tag))
        if ROLE_SERIES in attrs.get("aria-label", ""):
            marks.append((attrs["aria-label"], attrs))
    return marks


@_POINT_SIZES
def test_line_announces_each_datum_exactly_once(make_chart, point_size):
    """One announced mark per datum: the foreground point when it is drawn, the
    hover-target overlay when it is not. The suppressed case also guards hover:
    the overlay is then the chart's only labeled mark, and
    ``chart_interactivity.js`` only hovers labeled marks."""
    marks = _datum_marks(_render_line_svg(make_chart, point_size))
    counts = Counter(label for label, _ in marks)

    assert len(counts) == len(_MULTI_SERIES_DATA), counts
    repeated = {label: n for label, n in counts.items() if n > 1}
    assert not repeated, f"Datum labels announced more than once: {repeated!r}"


@_POINT_SIZES
def test_line_announced_mark_paints_its_series_color(make_chart, point_size):
    """The tooltip swatch (``markSeriesColor``: fill, else stroke) read off each
    announced mark is its series' line color, never a knockout color."""
    series_colors = _series_colors(_line_spec(make_chart, point_size))
    assert series_colors is not None, "no color: series scale in the spec"

    swatches: dict[str, set[str]] = {}
    svg = _render_line_svg(make_chart, point_size)
    for label, attrs in _datum_marks(svg):
        series = next(
            p.strip()[1:] for p in label.split(";") if p.strip().startswith(ROLE_SERIES)
        )
        fill = attrs.get("fill")
        swatches.setdefault(series, set()).add(
            fill if fill and fill != "none" else attrs["stroke"]
        )

    assert swatches == {s: {c} for s, c in series_colors.items()}


def _area_svg(make_chart, point_size: int) -> str:
    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    style = {"marks": {"point": {"size": point_size}}}
    chart = make_chart("area", x="day", y="value", color="series", style=style)
    return vlc.vegalite_to_svg(
        generate_vega_lite_spec(chart, _MULTI_SERIES_DATA, width=400)
    )


@_POINT_SIZES
def test_area_announces_each_datum_exactly_once(make_chart, point_size):
    """Same rule as the line family: the drawn point announces the datum, the
    hover overlay only when no point is drawn."""
    marks = _datum_marks(_area_svg(make_chart, point_size))
    counts = Counter(label for label, _ in marks)

    assert len(counts) == len(_MULTI_SERIES_DATA), counts
    repeated = {label: n for label, n in counts.items() if n > 1}
    assert not repeated, f"Datum labels announced more than once: {repeated!r}"
