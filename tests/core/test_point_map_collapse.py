"""Tests for point_map's `collapse` opt-in (co-located marks sized by count).

`collapse` is an explicit authored opt-in (`collapse: true`), never a
silent default, and must refuse when it would repurpose an already-authored
`size:` measure channel (the bubble_map case).

Also covers the area-proportional size-scale fix: the previous [50, 1000]
range floor made bubble_map/collapse size an affine, not proportional,
function of the domain value.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.chart.authored import (
    PointMapChart as AuthoredPointMapChart,
)
from dbt_charts.core.render.chart.emitters._tooltip import COUNT_TITLE
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

# ---------------------------------------------------------------------------
# 1. Hard constraint: collapse cannot repurpose an authored size: channel
# ---------------------------------------------------------------------------


def test_collapse_with_size_rejected() -> None:
    """`collapse: true` + `size: <col>` both bind the size channel — refuse."""
    with pytest.raises(ValidationError, match="collapse"):
        AuthoredPointMapChart(type="bubble_map", size="magnitude", collapse=True)


def test_collapse_with_size_error_names_bubble_map_not_point_map() -> None:
    """`size:` is the bubble_map-only field — it's the likeliest way to hit
    this validator, so the error must name the chart type the author
    actually typed, not a hardcoded "point_map:" that never applies here."""
    with pytest.raises(ValidationError, match="bubble_map:") as excinfo:
        AuthoredPointMapChart(type="bubble_map", size="magnitude", collapse=True)
    assert "point_map:" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 1b. Hard constraint: collapse cannot repurpose the color channel either
# (2026-08-12 Decision — "collapse and the color channel").
# ---------------------------------------------------------------------------


def test_collapse_with_field_color_rejected() -> None:
    """`collapse: true` + `color: <col>` makes VL group the count aggregate by
    an extra field — re-creating the exact pile `collapse` exists to remove.
    """
    with pytest.raises(ValidationError, match="collapse"):
        AuthoredPointMapChart(type="point_map", color="store_type", collapse=True)


def test_collapse_error_names_both_resolutions() -> None:
    """The error must name both fixes: drop one field, or aggregate in SQL
    and use bubble_map with size:/color: on the pre-aggregated counts."""
    with pytest.raises(ValidationError, match="bubble_map"):
        AuthoredPointMapChart(type="point_map", color="store_type", collapse=True)


def test_collapse_alone_is_valid() -> None:
    chart = AuthoredPointMapChart(type="point_map", collapse=True)
    assert chart.collapse is True


def test_size_alone_is_valid() -> None:
    chart = AuthoredPointMapChart(type="bubble_map", size="magnitude")
    assert chart.size == "magnitude"
    assert chart.collapse is False


def test_collapse_defaults_false() -> None:
    chart = AuthoredPointMapChart(type="point_map")
    assert chart.collapse is False


# ---------------------------------------------------------------------------
# 2. Emitter: collapse aggregates size by count via native VL, not row grouping
# ---------------------------------------------------------------------------

_PILE_ROWS: list[dict[str, Any]] = [
    {"lat": 34.64, "lng": -120.60},
    {"lat": 34.64, "lng": -120.60},
    {"lat": 34.64, "lng": -120.60},
]


def test_collapse_emits_count_aggregate_on_size_channel(make_chart) -> None:
    chart = make_chart("point_map", latitude="lat", longitude="lng", collapse=True)
    spec = generate_vega_lite_spec(chart, _PILE_ROWS)

    size_enc = spec["encoding"]["size"]
    assert size_enc["aggregate"] == "count"
    assert "field" not in size_enc
    assert size_enc["title"] == COUNT_TITLE


def test_collapse_size_channel_is_area_proportional(make_chart) -> None:
    chart = make_chart("point_map", latitude="lat", longitude="lng", collapse=True)
    spec = generate_vega_lite_spec(chart, _PILE_ROWS)

    scale = spec["encoding"]["size"]["scale"]
    assert scale["range"][0] == 0, (
        f"size scale range must start at 0 for area-proportional (not affine) "
        f"scaling; got {scale}"
    )


def test_collapse_suppresses_mark_level_size(make_chart) -> None:
    """When collapsing, the fixed mark_props size must not also apply."""
    chart = make_chart("point_map", latitude="lat", longitude="lng", collapse=True)
    spec = generate_vega_lite_spec(chart, _PILE_ROWS)

    assert "size" not in spec["mark"], (
        f"mark-level fixed size must be absent when collapse's size-by-count "
        f"encoding is active; mark = {spec['mark']}"
    )


def test_collapse_tooltip_shows_count_not_raw_columns(make_chart) -> None:
    """A raw per-row tooltip field would become an unintended extra groupby key."""
    rows = [
        {**r, "facility": name}
        for r, name in zip(_PILE_ROWS, ["a", "b", "c"], strict=True)
    ]
    chart = make_chart("point_map", latitude="lat", longitude="lng", collapse=True)
    spec = generate_vega_lite_spec(chart, rows)

    tooltip_fields = spec["encoding"]["tooltip"]
    tooltip_field_names = {f.get("field") for f in tooltip_fields}
    assert "facility" not in tooltip_field_names
    (count,) = [f for f in tooltip_fields if f.get("aggregate") == "count"]
    assert count["title"] == COUNT_TITLE


def test_no_collapse_keeps_mark_level_size(make_chart) -> None:
    """Baseline: without collapse, plain point_map still uses a fixed mark size."""
    chart = make_chart("point_map", latitude="lat", longitude="lng")
    spec = generate_vega_lite_spec(chart, _PILE_ROWS)

    assert "size" in spec["mark"]
    assert "size" not in spec.get("encoding", {})


def _non_aggregated_encoded_fields(spec: dict[str, Any]) -> set[str]:
    """Every field VL's `size: {aggregate: count}` implicitly groups by —
    every encoded field with no `aggregate` of its own, tooltip list entries
    included. If a future channel silently joins the groupby, this set grows
    past {latitude, longitude} without any code here having to know its name.
    """
    fields: set[str] = set()
    for enc in spec["encoding"].values():
        entries = enc if isinstance(enc, list) else [enc]
        for entry in entries:
            if (
                isinstance(entry, dict)
                and "field" in entry
                and "aggregate" not in entry
            ):
                fields.add(entry["field"])
    return fields


def test_collapse_groupby_is_exactly_lat_lon(make_chart) -> None:
    """The implicit VL groupby under `collapse` must be exactly {lat, lon} —
    the compile-time validator refuses every authored channel known to widen
    it (size, color, conditional color), so a collapse-only chart is the one
    case left standing; this pins that its groupby never silently grows.
    """
    chart = make_chart("point_map", latitude="lat", longitude="lng", collapse=True)
    spec = generate_vega_lite_spec(chart, _PILE_ROWS)

    assert _non_aggregated_encoded_fields(spec) == {"lat", "lng"}


# ---------------------------------------------------------------------------
# 4. Integer count legend — a fractional count is not a data value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pile_count", [1, 2, 3, 4, 5, 6, 7, 10, 11, 13, 20, 25, 26, 100, 101]
)
def test_collapse_legend_ticks_are_never_fractional(make_chart, pile_count) -> None:
    """`Count 0.5 / 1.0 / 1.5 ...` is meaningless — verified against the
    COMPILED legend (vl-convert render), not the presence of a spec key.

    A rounded-but-not-reduced tick set is the same failure in a different
    costume: `tickMinStep` + `format: "d"` alone left the underlying
    half-integer ticks in place and just relabeled two of them "1" — two
    DIFFERENT swatch sizes both claiming to mean 1. A fractional-free legend
    must also have no duplicate label.

    Neither of the first two assertions alone catches `sequence()`
    overshooting its stop — a max that is not an exact multiple of the
    stride produced 15 for a max of 11 (5, 10, 15) and 8 for a max of 7
    (2, 4, 6, 8), both non-fractional, non-duplicate, and both wrong: the
    legend advertised a pile size larger than any pile on the map. 7/11/
    13/26 are exactly the non-dividing cases from that table.
    """
    import re

    import vl_convert as vlc

    if pile_count == 1:
        # Degenerate case: no real pile anywhere (every group count is 1).
        rows = [{"lat": 34.64, "lng": -120.60}, {"lat": 40.0, "lng": -100.0}]
    else:
        rows = [{"lat": 34.64, "lng": -120.60}] * pile_count
    chart = make_chart("point_map", latitude="lat", longitude="lng", collapse=True)
    spec = generate_vega_lite_spec(chart, rows)

    svg = vlc.vegalite_to_svg(spec)
    tick_texts = re.findall(r"<text[^>]*>([^<]+)</text>", svg)
    numeric_ticks = [t for t in tick_texts if t.replace(".", "", 1).isdigit()]
    assert numeric_ticks, f"no numeric legend ticks found in rendered SVG: {tick_texts}"
    assert not any("." in t for t in numeric_ticks), (
        f"fractional legend tick at pile_count={pile_count}: {numeric_ticks}"
    )
    assert len(numeric_ticks) == len(set(numeric_ticks)), (
        f"duplicate legend label at pile_count={pile_count}: {numeric_ticks} — "
        f"a reader matching a circle to this legend gets two candidate answers"
    )
    domain_max = pile_count
    assert max(int(t) for t in numeric_ticks) <= domain_max, (
        f"legend tick exceeds the domain max at pile_count={pile_count}: "
        f"{numeric_ticks} — advertises a pile bigger than any on the map"
    )


def test_no_collapse_size_legend_keeps_default_format(make_chart) -> None:
    """The integer-tick fix is scoped to collapse's count aggregate — an
    authored bubble_map `size:` measure (e.g. revenue) is not a count and
    must not be forced into integer-only ticks.
    """
    chart = make_chart(
        "bubble_map", latitude="lat", longitude="lng", size="magnitude", x=None, y=None
    )
    rows = [{"lat": 34.0, "lng": -120.0, "magnitude": 1}]
    spec = generate_vega_lite_spec(chart, rows)

    assert "legend" not in spec["encoding"]["size"]


# ---------------------------------------------------------------------------
# 5. Draw order — collapsed marks draw largest-behind-smallest
# ---------------------------------------------------------------------------


def test_collapse_orders_marks_largest_behind_smallest(make_chart) -> None:
    """A small pile must never be wholly swallowed by an adjacent large one —
    verified against the actual paint order in a real render, not the spec
    key alone: with no `order` channel, VL draws in input-row order, which
    for these rows (big pile first) would put the SMALL pile on the bottom.
    """
    import re

    import vl_convert as vlc

    rows = [{"lat": 34.6382, "lng": -120.5892}] * 20 + [
        {"lat": 34.6321, "lng": -120.6106}
    ] * 2
    chart = make_chart(
        "point_map",
        latitude="lat",
        longitude="lng",
        collapse=True,
        style={"marks": {"point": {"opacity": 1.0}}},
    )
    spec = generate_vega_lite_spec(chart, rows)

    assert spec["encoding"]["order"]["sort"] == "descending"

    svg = vlc.vegalite_to_svg(spec)
    circle_radii = [
        float(m)
        for m in re.findall(r'aria-roledescription="circle"[^>]*d="M([\d.]+),', svg)
    ]
    assert len(circle_radii) == 2, f"expected 2 collapsed marks, got {circle_radii}"
    # Last-drawn (top of the DOM, painted on top) must be the SMALLER pile.
    assert circle_radii[-1] < circle_radii[0], (
        f"large pile must be drawn first (behind), small pile last (in "
        f"front); radii in draw order = {circle_radii}"
    )


def test_no_collapse_emits_no_order_channel(make_chart) -> None:
    """Draw-order pinning is scoped to collapse — a plain point_map (no
    piles) must not carry an order channel it never needed."""
    chart = make_chart("point_map", latitude="lat", longitude="lng")
    spec = generate_vega_lite_spec(chart, _PILE_ROWS)

    assert "order" not in spec.get("encoding", {})


# ---------------------------------------------------------------------------
# 6. bubble_map's authored size: channel keeps the same area-proportional fix
# ---------------------------------------------------------------------------


def test_bubble_map_size_scale_is_area_proportional(make_chart) -> None:
    chart = make_chart(
        "bubble_map",
        latitude="lat",
        longitude="lng",
        size="magnitude",
        x=None,
        y=None,
    )
    rows = [
        {"lat": 34.0, "lng": -120.0, "magnitude": 1},
        {"lat": 35.0, "lng": -119.0, "magnitude": 20},
    ]
    spec = generate_vega_lite_spec(chart, rows)

    scale = spec["encoding"]["size"]["scale"]
    assert scale["range"][0] == 0
