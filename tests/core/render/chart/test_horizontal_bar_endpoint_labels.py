"""TDD tests for the endpoint-labels primitive on horizontal stacked bars.

Vertical stacked bars get per-segment side labels (right-edge hconcat pane).
Horizontal stacked bars get a single label *rail* above the top categorical
row: one label per series, anchored to the segment x-midpoint within the
top row. The rail has no per-label collision resolver — when the real,
measured label text for two adjacent series would overlap, resolve steers
the whole chart back to a legend instead (see
``_horizontal_rail_labels_would_collide`` in compile/resolve/chart/bar.py).

Layout shape: ``vconcat`` of [labels-pane, chart-pane] with ``resolve.scale.x
= shared``. The labels pane carries one row per series; ``__x`` is the
chart-x at which the label centers.

Tests use synthetic data so the expected midpoints are computable by
hand without pinning theme literals.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE = resolve_style(get_theme_style())
_BOARD_CTX = resolve_chart_style_context(get_theme_style())


@pytest.fixture
def seed_with_bar_endpoint_labels(model_copy_at):
    """Resolved style with bar.endpoint_labels override applied (seeded from stark)."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    def _build(enabled: bool, label_offset: float = 5.0):
        seed = model_copy_at(
            get_theme_style("stark"),
            "charts.bar.endpoint_labels",
            EndpointLabelsConfig(
                visible=enabled, label_offset=label_offset, height=20.0
            ),
        )
        return resolve_chart_style_context(seed)

    return _build


@pytest.fixture
def resolve_horizontal_bar_chart(make_chart, seed_with_bar_endpoint_labels):
    """Resolved horizontal bar chart with endpoint_labels enabled and optional stack."""
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    def _build(
        data: list[dict[str, Any]],
        enabled: bool,
        color: str | None = "series",
        stack: Any = None,
        stack_order: str | None = None,
        author_asked: bool = False,
        width: float | None = None,
        axis_y: dict[str, Any] | None = None,
        sort: dict[str, Any] | None = None,
    ):
        kwargs: dict[str, Any] = {}
        if color is not None:
            kwargs["color"] = color
        if stack is not None:
            kwargs["stack"] = stack
        if sort is not None:
            kwargs["sort"] = sort
        style: dict[str, Any] = {"orientation": "horizontal"}
        if stack_order is not None:
            style["stack_order"] = stack_order
        if axis_y is not None:
            style["axis_y"] = axis_y
        if author_asked:
            # The default steers a ragged anchor row back to a legend (the rail
            # has no collision resolver); only the chart's own patch outranks it.
            style["endpoint_labels"] = {"visible": True}
        chart = make_chart(
            "bar",
            x="row",
            y="value",
            style=BarChartStylePatch.model_validate(style),
            **kwargs,
        )
        board_style = seed_with_bar_endpoint_labels(enabled)
        rc = resolve(chart, data, chart_style_context=board_style, width=width)
        return rc

    return _build


def _two_series_two_row_data() -> list[dict[str, Any]]:
    """Two categorical rows × two series. Top-row segments yield obvious midpoints.

    First-occurrence row = "alpha" → top of the chart. In the top row:
        A = 40, B = 60.
    Value ordering (_apply_stacked_bar_z_order default: largest at baseline):
        B at left  0..60   → midpoint 30
        A on right 60..100 → midpoint 80
    Normalize: B mid = 0.30, A mid = 0.80.
    """
    return [
        {"row": "alpha", "value": 40, "series": "A"},
        {"row": "alpha", "value": 60, "series": "B"},
        {"row": "beta", "value": 10, "series": "A"},
        {"row": "beta", "value": 90, "series": "B"},
    ]


def _render(rc, data: list[dict[str, Any]], width: float = 400.0) -> dict[str, Any]:
    artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=width)
    assert artifact.kind == "vega_spec"
    return artifact.payload


# ---------------------------------------------------------------------------
# Wrap gate
# ---------------------------------------------------------------------------


def test_horizontal_stacked_emits_vconcat(resolve_horizontal_bar_chart):
    """Stacked horizontal + endpoint_labels.visible → vconcat with labels on top."""
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    assert "vconcat" in spec, f"Expected vconcat top-level key, got keys {list(spec)}"
    assert len(spec["vconcat"]) == 2, (
        "Expected 2 panes: labels pane (top) + chart pane (bottom)"
    )
    assert spec.get("resolve", {}).get("scale", {}).get("x") == "shared"
    assert spec.get("resolve", {}).get("scale", {}).get("color") == "independent"


def test_horizontal_stacked_disabled_no_wrap(resolve_horizontal_bar_chart):
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=False, stack="zero")
    spec = _render(rc, data)
    assert "vconcat" not in spec
    assert "hconcat" not in spec


def test_horizontal_stacked_explicit_zero_fires(resolve_horizontal_bar_chart):
    """Explicit stack=zero on a multi-series horizontal bar → rail fires."""
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    assert "vconcat" in spec


def test_horizontal_grouped_by_default_does_not_fire(resolve_horizontal_bar_chart):
    """stack=None + color (grouped-by-default) → no rail (bars don't stack)."""
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack=None)
    spec = _render(rc, data)
    assert "vconcat" not in spec
    assert "hconcat" not in spec


def test_horizontal_grouped_does_not_fire(resolve_horizontal_bar_chart):
    """Grouped horizontal (stack: none) does not fire — the rail is for stacks only.

    The whole point of the top-row series rail is to direct-label color segments
    *within a single row*. Grouped bars share the row instead of stacking inside
    it; the right-side measure tip is a different placement primitive.
    """
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="none")
    spec = _render(rc, data)
    assert "vconcat" not in spec
    assert "hconcat" not in spec


def test_horizontal_single_series_does_not_fire(resolve_horizontal_bar_chart):
    """Single-series horizontal bar (no color encoding) → no wrap.

    Same multi-series gate as the vertical variant: the rail names color
    segments, and a single-series chart has no segments to name.
    """
    data = [
        {"row": "alpha", "value": 40},
        {"row": "beta", "value": 10},
    ]
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, color=None, stack="zero")
    spec = _render(rc, data)
    assert "vconcat" not in spec
    assert "hconcat" not in spec


# ---------------------------------------------------------------------------
# Anchor: top-row only, segment midpoints
# ---------------------------------------------------------------------------


def _label_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    pane = spec["vconcat"][0]
    return pane["data"]["values"]


def test_label_rail_emits_one_entry_per_series(resolve_horizontal_bar_chart):
    """One label per color value — not per (row, color) data point.

    Pins the top-row-only contract: the rail names every series exactly once,
    not once per categorical row.
    """
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    rows = _label_rows(spec)
    series_in_rail = {r["series"] for r in rows}
    assert series_in_rail == {"A", "B"}
    assert len(rows) == 2


def test_label_rail_anchors_at_top_row_segment_midpoints(resolve_horizontal_bar_chart):
    """``__x`` for each series = cumulative segment midpoint within TOP ROW only.

    Top row is the first y-domain value in query order ("alpha"). In that row
    A=40, B=60; value ordering puts B (larger) at baseline 0..60 → mid 30,
    A on top 60..100 → mid 80. Bottom row ("beta") values must NOT influence
    the rail.
    """
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    rows = _label_rows(spec)
    by_series = {r["series"]: r["__x"] for r in rows}
    assert by_series == {"B": 30.0, "A": 80.0}, (
        f"Expected top-row midpoints B=30, A=80 (value ordering puts B at baseline), "
        f"got {by_series!r}. If a different row's data leaked in, the top-row filter "
        f"is broken."
    )


def test_top_rail_anchors_follow_query_order_not_category_alphabetization(
    resolve_horizontal_bar_chart,
) -> None:
    """The rail anchors on the top category of the first-occurrence domain."""
    data = [
        {"row": "Before", "value": 40, "series": "A"},
        {"row": "Before", "value": 60, "series": "B"},
        {"row": "After", "value": 10, "series": "A"},
        {"row": "After", "value": 90, "series": "B"},
    ]
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)

    assert spec["vconcat"][1]["encoding"]["y"]["sort"] is None
    assert {row["series"]: row["__x"] for row in _label_rows(spec)} == {
        "B": 30.0,
        "A": 80.0,
    }


def _series_missing_from_top_row_data() -> list[dict[str, Any]]:
    """Three series where C has no segment in the top row ("alpha").

    Global sums: B=150, C=60, A=50 → value ordering stacks B, C, A from the
    baseline out, putting the absent series mid-stack.

    Top row: B=60 → 0..60 midpoint 30; C zero-width → seam at 60;
    A=40 → 60..100 midpoint 80.
    """
    return [
        {"row": "alpha", "value": 40, "series": "A"},
        {"row": "alpha", "value": 60, "series": "B"},
        {"row": "beta", "value": 10, "series": "A"},
        {"row": "beta", "value": 90, "series": "B"},
        {"row": "beta", "value": 60, "series": "C"},
    ]


def test_rail_names_series_absent_from_top_row(resolve_horizontal_bar_chart) -> None:
    """Under an explicit opt-in, a series absent from the top row still gets an entry.

    The seam math is the same as the vertical path's and stays correct; it is
    only the *default* that steers away, because this rail cannot yet dodge
    colliding labels. An author who asks for it must not lose a name.
    """
    data = _series_missing_from_top_row_data()
    rc = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="zero", author_asked=True
    )
    spec = _render(rc, data)
    rows = _label_rows(spec)
    assert {r["series"] for r in rows} == {"A", "B", "C"}, (
        f"Rail must name every series in the chart exactly once, got "
        f"{[r['series'] for r in rows]!r}"
    )


def _ragged_sorted_data() -> list[dict[str, Any]]:
    """A key column constant per row, and one row carrying a single series.

    Under the aggregate a bar pins (``bar_sort_op`` → ``min`` here, since the
    sort column is not the measure), the keys rank 1/2/3, so "gamma" leads
    descending and "alpha" leads ascending. Folding with ``sum`` instead would
    rank 2/4/3 and lead with the complete "beta" either way.
    """
    return [
        {"row": "alpha", "key": 1, "value": 40, "series": "A"},
        {"row": "alpha", "key": 1, "value": 60, "series": "B"},
        {"row": "beta", "key": 2, "value": 10, "series": "A"},
        {"row": "beta", "key": 2, "value": 90, "series": "B"},
        {"row": "gamma", "key": 3, "value": 50, "series": "A"},
    ]


def test_sorted_rail_steers_to_a_legend_when_its_anchor_row_is_ragged(
    resolve_horizontal_bar_chart,
) -> None:
    """The anchor-row check reads the axis's own first row, sort aggregate included.

    Descending by ``key`` draws "gamma" first, and "gamma" has no B segment —
    a series that would anchor on a zero-width seam at a neighbor's edge, which
    the default refuses (``_every_series_reaches_the_anchor_row``). Reproducing
    the axis with the wrong aggregate would anchor on "beta" instead and wave
    the rail through.
    """
    data = _ragged_sorted_data()
    rc = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="zero", sort={"by": "key", "order": "desc"}
    )
    assert rc.style.endpoint_labels.visible is False
    assert "vconcat" not in _render(rc, data)


def test_sorted_rail_survives_when_its_anchor_row_carries_every_series(
    resolve_horizontal_bar_chart,
) -> None:
    """Control: the same data ascending anchors on "alpha", which is complete."""
    data = _ragged_sorted_data()
    rc = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="zero", sort={"by": "key", "order": "asc"}
    )
    assert rc.style.endpoint_labels.visible is True
    assert "vconcat" in _render(rc, data)


def test_absent_series_anchors_at_its_zero_width_stack_seam(
    resolve_horizontal_bar_chart,
) -> None:
    """The absent series anchors where its segment would start, not at a neighbor."""
    data = _series_missing_from_top_row_data()
    rc = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="zero", author_asked=True
    )
    spec = _render(rc, data)
    rows = _label_rows(spec)
    by_series = {r["series"]: r["__x"] for r in rows}
    assert by_series == {"B": 30.0, "C": 60.0, "A": 80.0}


def test_normalize_anchors_on_unit_scale(resolve_horizontal_bar_chart):
    """Stack normalize: midpoints are shares of the top row's total.

    A=40, B=60 in top row; value ordering: B (larger) at 0..0.60 → mid 0.30,
    A at 0.60..1.00 → mid 0.80.
    """
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="normalize")
    spec = _render(rc, data)
    rows = _label_rows(spec)
    by_series = {r["series"]: r["__x"] for r in rows}
    assert by_series == {"B": 0.30, "A": 0.80}


def test_label_rail_color_domain_is_alphabetical(resolve_horizontal_bar_chart):
    """Pin the color encoding so dark-companion stops match the rendered segments."""
    data = [
        {"row": "alpha", "value": 30, "series": "Zebra"},
        {"row": "alpha", "value": 50, "series": "Apple"},
        {"row": "beta", "value": 10, "series": "Zebra"},
        {"row": "beta", "value": 20, "series": "Apple"},
    ]
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    pane = spec["vconcat"][0]
    assert pane["encoding"]["color"]["scale"]["domain"] == ["Apple", "Zebra"]


def test_label_rail_color_range_is_dark_companions(resolve_horizontal_bar_chart):
    """Pin the color *range* on the rail to each segment's label ink.

    The whole point of the ``_dark_companion_stops`` helper is that each
    label inks a notch darker than its segment for legibility — without
    this assertion a future refactor could silently swap the dark range
    for the bright one and the chart would still render, just with low-
    contrast labels.
    """
    from dbt_charts.core.compile.resolve.style.palette import (
        label_ink,
        palette as resolve_palette,
    )

    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    pane = spec["vconcat"][0]
    rail_range = pane["encoding"]["color"]["scale"]["range"]
    # Main stack order is B, A while the independent rail scale keeps its
    # alphabetical domain A, B. Each label must retain its segment's slot.
    bright = resolve_palette("vivid-10")
    ink = [label_ink(c, _BOARD_CTX.background) for c in bright]
    assert rail_range == [ink[1], ink[0]]


def test_wrap_reduces_chart_pane_height_to_absorb_rail(resolve_horizontal_bar_chart):
    """The vconcat wrap helper must subtract the rail pane's footprint from the
    chart pane's height so the outer SVG fits the configured cell height.

    Mirrors the hconcat width-reduction strategy. Without this the rail
    would push the chart pane down and overflow the cell height.
    """

    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400, height=200)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload
    chart_pane = spec["vconcat"][1]
    rail_pane = spec["vconcat"][0]
    rail_height = float(rail_pane.get("height", 0) or 0)
    spacing = float(spec.get("spacing", 0) or 0)
    chart_height = float(chart_pane.get("height", 0) or 0)
    # Chart pane height was reduced to absorb the rail, so the total
    # stack (rail + spacing + chart) ≤ the configured 200px cell.
    assert chart_height + rail_height + spacing <= 200.0 + 0.5, (
        f"vconcat wrap should keep total height ≤ configured cell; got "
        f"chart={chart_height}, rail={rail_height}, spacing={spacing} "
        f"(total={chart_height + rail_height + spacing})"
    )


# ---------------------------------------------------------------------------
# Collision detection: a crowded rail falls back to a legend
# ---------------------------------------------------------------------------


def _crowded_long_name_data() -> list[dict[str, Any]]:
    """Seven long series names sharing one narrow top-row segment.

    Same shape as a real crowded chart (quirky-length series like
    "extensions_contrib" and "replication_report"): each name is wide enough,
    relative to its own share of the row, that the rail's labels cannot all
    fit without touching a neighbor on a narrow-enough chart.
    """
    names = [
        "extensions_contrib",
        "replication_report",
        "build",
        "platform",
        "warehouse_sync",
        "connector_health",
        "usage_metering",
    ]
    return [{"row": "alpha", "value": 10, "series": name} for name in names]


def test_horizontal_rail_falls_back_to_legend_when_labels_would_collide(
    resolve_horizontal_bar_chart,
):
    """Real pixel-width measurement, not a heuristic, disqualifies a crowded rail.

    Seven long series names sharing one narrow top row cannot all fit their
    own label without overlapping a neighbor — the default steers back to a
    legend exactly like every other rail disqualifier in this module.

    Width is close to the real collision threshold for this data (940px:
    disqualified; 990px in the companion test below: fires), not a wide
    margin either side of it — a half/double error in the pixel math would
    flip one of the two tests, where a generous 480px/4000px bracket
    wouldn't catch it.
    """
    data = _crowded_long_name_data()
    rc = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="zero", width=940.0
    )
    assert rc.style.endpoint_labels.visible is False
    spec = _render(rc, data, width=940.0)
    assert "vconcat" not in spec, "A colliding rail must fall back to a legend"


def test_horizontal_rail_survives_when_labels_have_room(resolve_horizontal_bar_chart):
    """Regression: the same seven series on a wide-enough chart keep the rail.

    The collision check only disqualifies a rail whose labels actually
    overlap — widening the chart alone must bring the rail back. 990px pins
    the collision threshold tightly against the companion test above's
    940px (disqualified) — see that test's docstring for why.
    """
    data = _crowded_long_name_data()
    rc = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="zero", width=990.0
    )
    assert rc.style.endpoint_labels.visible is True
    spec = _render(rc, data, width=990.0)
    assert "vconcat" in spec


def test_crowded_rail_legend_matches_another_disqualified_shape(
    resolve_horizontal_bar_chart,
):
    """A crowded rail's legend fallback is the same shared placement decision.

    A center stack is disqualified for an unrelated reason (the rail cannot
    anchor on a diverging domain) but is still a genuine stack — same
    ``is_stacked`` legend-placement branch inside
    ``cartesian_series_naming()`` as the collision disqualifier reaches —
    proving the crowded case doesn't invent a bespoke legend path of its own.
    (``stack: none`` is not used for this comparison: grouped bars take
    cartesian_series_naming's separate unconditional-top-legend branch, so
    its placement legitimately differs for a reason unrelated to this check.)
    """
    data = _crowded_long_name_data()
    crowded = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="zero", width=480.0
    )
    centered = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="center", width=480.0
    )
    assert crowded.style.endpoint_labels.visible is False
    assert centered.style.endpoint_labels.visible is False
    assert crowded.legend.position == centered.legend.position
    assert crowded.legend.direction == centered.legend.direction


def test_authored_measure_domain_feeds_the_collision_check(
    resolve_horizontal_bar_chart,
):
    """Regression: the check must score against the domain that actually
    renders, not the stacked-total derivation the emitter discards.

    A tiny natural stacked total (10 + 10 = 20) spaces two short labels
    generously — no collision. But an authored ``axis_y.scale.continuous
    .domain`` far wider than that total (the emitter puts an authored
    domain straight on the scale, unconditionally, ahead of the baked
    stacked total — see ``emitters/bar.py``) compresses both labels' real
    pixel positions toward the origin, closing the gap between them, even
    though nothing about the data itself changed.
    """
    data = [
        {"row": "alpha", "value": 10, "series": "A"},
        {"row": "alpha", "value": 10, "series": "B"},
    ]
    natural = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="zero", width=480.0
    )
    assert natural.style.endpoint_labels.visible is True, (
        "sanity check: two short labels over a small natural total must not "
        "collide on their own"
    )
    widened = resolve_horizontal_bar_chart(
        data=data,
        enabled=True,
        stack="zero",
        width=480.0,
        axis_y={"scale": {"continuous": {"domain": [0, 800]}}},
    )
    assert widened.style.endpoint_labels.visible is False, (
        "an authored domain far wider than the natural stacked total must "
        "compress the rail's real pixel positions and be read by the "
        "collision check, not ignored in favor of the stacked total"
    )


@pytest.mark.parametrize("domain", [[0, 0], [50, 50]])
def test_zero_width_authored_domain_does_not_crash_resolve(
    resolve_horizontal_bar_chart, domain
):
    """Regression: a zero-width authored domain must not raise ZeroDivisionError.

    _stacked_measure_domain_span's authored branch used to return
    ``max(authored)`` unguarded — a valid-looking ``domain: [0, 0]`` (any
    ``lo == hi``) reached the collision check's ``(mid - lo) / (hi - lo)``
    division with a zero denominator. Bad input from an author must not
    crash resolve. A zero-width span has nothing real to anchor a rail on,
    so it disqualifies rather than guessing.
    """
    data = [
        {"row": "alpha", "value": 10, "series": "A"},
        {"row": "alpha", "value": 10, "series": "B"},
    ]
    rc = resolve_horizontal_bar_chart(
        data=data,
        enabled=True,
        stack="zero",
        width=480.0,
        axis_y={"scale": {"continuous": {"domain": domain}}},
    )
    assert rc.style.endpoint_labels.visible is False
    assert rc.legend.visible is not False


def test_negative_low_edge_authored_domain_does_not_crash_resolve(
    resolve_horizontal_bar_chart,
):
    """Regression: the old ``max(authored)``-only formula divided by the high
    edge alone — ``domain: [-100, 0]`` has ``max() == 0``, a zero divisor,
    even though the span itself (100 units) is perfectly well-defined. Fixed
    by reading the full ``(lo, hi)`` pair and dividing by their difference.

    Two short labels over a well-defined 100-unit span have plenty of real
    room at this width, so the rail fires — this also confirms the fix
    doesn't just avoid crashing, it produces a sane, correctly-scaled result.
    """
    data = [
        {"row": "alpha", "value": 10, "series": "A"},
        {"row": "alpha", "value": 10, "series": "B"},
    ]
    rc = resolve_horizontal_bar_chart(
        data=data,
        enabled=True,
        stack="zero",
        width=480.0,
        axis_y={"scale": {"continuous": {"domain": [-100, 0]}}},
    )
    assert rc.style.endpoint_labels.visible is True


def test_non_zero_anchored_authored_domain_keeps_its_low_edge(
    resolve_horizontal_bar_chart, monkeypatch
):
    """Regression: ``mid / hi`` is wrong once an authored domain's low edge
    isn't 0 — the real rendered position is ``(mid - lo) / (hi - lo)``, so
    the low edge must survive past ``_stacked_measure_domain_span`` rather
    than being discarded in favor of the high edge alone.

    Spies on the real call ``_horizontal_rail_labels_would_collide`` makes
    during a real resolve, rather than hand-constructing an ``AxisYStyle``
    (a fully cascade-baked theme model with many required fields) — the
    spied call is the actual one the collision check runs against.
    """
    from dbt_charts.core.compile.resolve.chart import bar as bar_module

    captured: dict[str, object] = {}
    original = bar_module._stacked_measure_domain_span

    def _spy(ay, rows, x_field, y_fields):  # noqa: ANN001 — matches original's signature
        result = original(ay, rows, x_field, y_fields)
        captured["result"] = result
        return result

    monkeypatch.setattr(bar_module, "_stacked_measure_domain_span", _spy)
    data = [
        {"row": "alpha", "value": 45, "series": "A"},
        {"row": "alpha", "value": 55, "series": "B"},
    ]
    resolve_horizontal_bar_chart(
        data=data,
        enabled=True,
        stack="zero",
        width=480.0,
        axis_y={"scale": {"continuous": {"domain": [90, 110]}}},
    )
    assert captured["result"] == (90.0, 110.0), (
        "the collision check must read the authored domain's real (lo, hi) "
        "span, not just its high edge"
    )


def test_all_null_top_row_measure_falls_back_to_a_legend(
    resolve_horizontal_bar_chart,
):
    """A degenerate (zero/null) top-row measure has no real span to anchor on.

    Every label's cumulative midpoint collapses onto the same zero-width
    point — the collision this check exists to catch, not a shape it should
    wave through because the domain-max derivation came back empty.
    """
    data = [
        {"row": "alpha", "value": 0, "series": "extensions_contrib"},
        {"row": "alpha", "value": 0, "series": "replication_report"},
        {"row": "alpha", "value": 0, "series": "connector_health"},
    ]
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    assert rc.style.endpoint_labels.visible is False
    assert rc.legend.visible is not False


def _crowded_wide_measure_data() -> list[dict[str, Any]]:
    """The wide-measure (``y: [...]``, no ``color:``) shape of the crowded chart.

    resolve_wide_measure_channels injects the same synthetic series-color
    channel an authored ``color:`` gets, and the rail fires for it too — this
    mirrors _crowded_long_name_data's column-per-series values but as one
    row with several measure columns instead of several rows sharing one
    color column.
    """
    return [
        {
            "row": "alpha",
            "extensions_contrib": 10,
            "replication_report": 10,
            "build": 10,
            "platform": 10,
            "warehouse_sync": 10,
            "connector_health": 10,
            "usage_metering": 10,
        }
    ]


def test_wide_measure_rail_also_falls_back_to_a_legend_when_crowded(make_chart):
    """Regression: the wide-measure horizontal rail must be collision-checked too.

    _bar_endpoint_labels_for_stack's default reaches both series-color
    shapes that actually draw a rail — an authored ``color:`` and wide
    measures (``y: [a, b, ...]``) alike. Before this fix the collision check
    bailed out unconditionally whenever ``color`` wasn't a string, silently
    skipping the wide-measure shape even though it renders the identical
    top-rail layout and can overlap the identical way.
    """
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    data = _crowded_wide_measure_data()
    measures = [k for k in data[0] if k != "row"]
    style = BarChartStylePatch.model_validate(
        {"orientation": "horizontal", "stack": "zero"}
    )
    chart = make_chart("bar", x="row", y=measures, style=style)
    rc = resolve(chart, data, chart_style_context=_BOARD_CTX, width=480.0)
    assert rc.style.endpoint_labels.visible is False
    assert rc.legend.visible is not False


def _seven_series_at_one_category(category: str) -> list[dict[str, Any]]:
    names = [
        "extensions_contrib",
        "replication_report",
        "build",
        "platform",
        "warehouse_sync",
        "connector_health",
        "usage_metering",
    ]
    return [{"row": category, "value": 10, "series": name} for name in names]


def test_long_category_name_shrinks_the_rails_own_usable_width(
    resolve_horizontal_bar_chart,
):
    """Regression: the categorical-label gutter must actually be subtracted.

    A short category name ("US") and a long one at the same card width
    reserve very different real gutter widths on the horizontal bar's own
    left axis (the rail and chart panes share that x scale) — the long name
    leaves the rail measurably less room. Deleting the subtraction
    (``_categorical_axis_gutter_px``) would score both identically and this
    test would fail: at width=1000 the short-name chart has room for its
    rail and the long-name one does not.
    """
    width = 1000.0
    short = resolve_horizontal_bar_chart(
        data=_seven_series_at_one_category("US"),
        enabled=True,
        stack="zero",
        width=width,
    )
    long = resolve_horizontal_bar_chart(
        data=_seven_series_at_one_category("X" * 60),
        enabled=True,
        stack="zero",
        width=width,
    )
    assert short.style.endpoint_labels.visible is True, (
        "sanity check: a short category name must leave the rail enough room"
    )
    assert long.style.endpoint_labels.visible is False, (
        "a long category name must shrink the rail's usable width enough to "
        "disqualify it here — if it doesn't, the gutter subtraction was removed"
    )


def test_categorical_gutter_caps_at_vega_lites_label_limit(
    resolve_horizontal_bar_chart,
):
    """Regression: the gutter estimate must cap at Vega-Lite's own labelLimit
    clamp, not keep growing with the category name's raw text length.

    Vega-Lite truncates any axis label wider than ``labelLimit`` before it
    ever occupies gutter space — the real reserved width saturates. An
    extremely long category name (200 characters) at width=1200 leaves the
    rail comfortably enough room once the gutter estimate is capped the same
    way; an uncapped estimate would keep growing with the name's raw width,
    collapse ``usable_width`` toward (or past) zero, and wrongly disqualify
    a rail that genuinely has room to render.
    """
    rc = resolve_horizontal_bar_chart(
        data=_seven_series_at_one_category("X" * 200),
        enabled=True,
        stack="zero",
        width=1200.0,
    )
    assert rc.style.endpoint_labels.visible is True, (
        "an uncapped gutter estimate would collapse usable_width toward zero "
        "for this 200-character category name and wrongly disqualify the rail"
    )


def test_categorical_axis_gutter_is_zero_when_labels_are_hidden() -> None:
    """Regression: no gutter is reserved when the axis hides its own labels.

    Vega-Lite draws nothing in that space when ``ax.labels.visible`` is
    False — subtracting a measured width anyway would shrink the rail's
    usable width for a gutter that never actually renders.
    """
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.chart._plan import plan_cartesian
    from dbt_charts.core.compile.resolve.chart.bar import _categorical_axis_gutter_px

    data = [{"row": "a very long category name indeed and then some", "value": 10}]
    chart = BarChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="row",
        y="value",
    )
    plan = plan_cartesian(
        chart,
        data,
        _BOARD_CTX,
        "bar",
        "nominal",
        "quantitative",
        None,
        chart.y,
        has_quantitative_axis=True,
    )
    hidden_labels = plan.ax_merged.labels.model_copy(update={"visible": False})
    hidden = plan.ax_merged.model_copy(update={"labels": hidden_labels})
    assert _categorical_axis_gutter_px(hidden, data, "row") == 0.0


# ---------------------------------------------------------------------------
# Wrap-time legend suppression and normalize x-domain pin
# ---------------------------------------------------------------------------


def test_wrap_disables_pane1_legend(resolve_horizontal_bar_chart):
    """Auto-disable the categorical legend on the chart pane when wrapping.

    Mirrors the vertical/hconcat behavior: direct labels and a side legend
    encode the same series→color mapping, and the rail is the direct label
    here, so the legend is suppressed.
    """
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    chart_pane = spec["vconcat"][1]
    assert chart_pane["encoding"]["color"]["legend"] is None


def test_normalize_pins_chart_pane_x_scale_to_unit(resolve_horizontal_bar_chart):
    """``stack: normalize`` must pin the chart pane's x-scale to [0, 1].

    Without this pin VL's auto-scale on the raw value field would yield
    ``[0, max(value)]`` and ``resolve.scale.x = shared`` would propagate
    that raw-domain scale to the labels pane — so the labels (which work
    on a [0, 1] domain after normalize) would stack near zero.
    """
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="normalize")
    spec = _render(rc, data)
    chart_pane = spec["vconcat"][1]
    x_scale = chart_pane["encoding"]["x"].get("scale", {})
    assert x_scale.get("domain") == [0, 1], (
        f"Expected chart-pane x-scale domain [0, 1] for stack=normalize, "
        f"got {x_scale!r}"
    )


def test_zero_stack_does_not_pin_x_scale_domain(resolve_horizontal_bar_chart):
    """Default (zero) stack must NOT inject an x-scale domain on the chart pane."""
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    chart_pane = spec["vconcat"][1]
    x_enc = chart_pane["encoding"]["x"]
    domain = (
        x_enc.get("scale", {}).get("domain")
        if isinstance(x_enc.get("scale"), dict)
        else None
    )
    assert domain is None, (
        f"chart-pane x-scale domain must NOT be pinned for stack=zero; got {domain!r}"
    )


# ---------------------------------------------------------------------------
# Label-pane structure
# ---------------------------------------------------------------------------


def test_label_pane_text_mark_is_centered(resolve_horizontal_bar_chart):
    """Each label centers horizontally on its segment midpoint."""
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    pane = spec["vconcat"][0]
    assert pane["mark"]["type"] == "text"
    # Horizontal centering on the segment midpoint is the whole point of
    # the rail; baseline puts the label above its anchor (closer to the
    # chart frame top, away from the bar).
    assert pane["mark"]["align"] == "center"
    assert pane["encoding"]["text"]["field"] == "series"


def test_rail_mark_paints_at_the_resolved_series_label_font(
    resolve_horizontal_bar_chart,
):
    """Regression: the rail's text mark must carry the same font props the
    resolve-time collision check measures with.

    _wrap_vconcat_label_rail (render/chart/translate.py) previously built the
    rail's mark with no font props at all — unlike _wrap_hconcat_label_pane
    (the vertical rail's own pane), which stamps them — so the rail actually
    painted at Vega's global text-mark default, not
    style.series_label.font.*. That silently made every resolve-time width
    measurement (including the collision check) wrong for any theme whose
    series-label size differs from the global default.
    """
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    pane = spec["vconcat"][0]
    assert pane["mark"]["fontSize"] == rc.style.series_label.font_size
    assert pane["mark"]["font"] == rc.style.series_label.font_family


def test_center_stack_never_reaches_the_rail_s_refusal(resolve_horizontal_bar_chart):
    """The rail cannot anchor a center stack, so resolve keeps charts away from it.

    The render-layer raise is still the contract for a ``ResolvedChart`` built
    directly with the combination; it is simply unreachable through resolve now
    that the labeling default disqualifies it.
    """
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="center")

    assert rc.style.endpoint_labels.visible is False
    assert "vconcat" not in _render(rc, data)


def test_center_stack_still_raises_when_the_author_opts_in(
    resolve_horizontal_bar_chart,
):
    """The refusal above is the other half of that contract.

    Resolve steers the default away from a center stack, but an explicit
    ``endpoint_labels.visible: true`` reaches render — and must meet the named
    conflict rather than a rail anchored on an axis the chart does not have.
    """
    import pytest

    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    rc = rc.model_copy(update={"stack": "center"})
    with pytest.raises(ChartDataError, match="center"):
        _render(rc, data)


def test_negative_values_auto_disable_endpoint_labels_horizontal(
    resolve_horizontal_bar_chart,
):
    """Negative values in the top row break the cumulative-midpoint computation.

    The render layer still raises if it ever sees this combination (defense
    in depth for a direct, non-default construction), but the resolve-time
    default now steers away from it first — same auto-disable treatment as
    stack: center above, and the chart still renders."""
    data = [
        {"row": "alpha", "value": 10, "series": "A"},
        {"row": "alpha", "value": -20, "series": "B"},
        {"row": "beta", "value": 5, "series": "A"},
        {"row": "beta", "value": 5, "series": "B"},
    ]
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    assert "vconcat" not in spec, (
        "Negative-value stacked horizontal bar must not emit the top-row "
        "endpoint-label rail"
    )


def test_negative_values_still_raise_when_the_author_opts_in(
    resolve_horizontal_bar_chart,
):
    """The other half of the auto-disable contract above: an explicit opt-in
    reaches render and meets the refusal by name.
    """
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    data = [
        {"row": "alpha", "value": 10, "series": "A"},
        {"row": "alpha", "value": -20, "series": "B"},
        {"row": "beta", "value": 5, "series": "A"},
        {"row": "beta", "value": 5, "series": "B"},
    ]
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    rc = rc.model_copy(
        update={
            "style": rc.style.model_copy(
                update={
                    "endpoint_labels": rc.style.endpoint_labels.model_copy(
                        update={"visible": True}
                    )
                }
            )
        }
    )
    with pytest.raises(ChartDataError, match="negative"):
        _render(rc, data)


def test_top_row_follows_query_order(resolve_horizontal_bar_chart):
    """The rail follows the top of the categorical axis's query-owned domain."""
    forward = _two_series_two_row_data()
    reversed_rows = list(reversed(forward))

    rc_fwd = resolve_horizontal_bar_chart(data=forward, enabled=True, stack="zero")
    rc_rev = resolve_horizontal_bar_chart(
        data=reversed_rows, enabled=True, stack="zero"
    )
    rows_fwd = _label_rows(_render(rc_fwd, forward))
    rows_rev = _label_rows(_render(rc_rev, reversed_rows))
    by_fwd = {r["series"]: r["__x"] for r in rows_fwd}
    by_rev = {r["series"]: r["__x"] for r in rows_rev}
    assert by_fwd == {"B": 30.0, "A": 80.0}
    assert by_rev == {"B": 45.0, "A": 95.0}


# ---------------------------------------------------------------------------
# axis_y orient: horizontal endpoint labels do NOT flip axis_y
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Regression: color→segment mapping was inverted (follow-up to #2390)
# ---------------------------------------------------------------------------


def test_ascending_stack_order_new_solved_midpoints(resolve_horizontal_bar_chart):
    """Regression: label x-midpoints must match the actual rendered stack order.

    _apply_stacked_bar_z_order defaults to value ordering (largest series at
    baseline). The label rail must anchor in the same order.

    Row "a_top" is first in query order (the top bar). With new=18, solved=12:
    value ordering puts new (larger) at 0..18 → mid 9;
    solved at 18..30 → mid 24.
    """
    data = [
        {"row": "a_top", "value": 18, "series": "new"},
        {"row": "a_top", "value": 12, "series": "solved"},
        {"row": "b_bottom", "value": 5, "series": "new"},
        {"row": "b_bottom", "value": 8, "series": "solved"},
    ]
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    rows = _label_rows(spec)
    by_series = {r["series"]: r["__x"] for r in rows}
    assert by_series == {"new": 9.0, "solved": 24.0}, (
        f"Expected new=9 (mid of 0..18, largest at baseline) and solved=24 "
        f"(mid of 18..30); got {by_series!r}. Color→segment mapping is inverted."
    )


def test_ascending_stack_order_three_series(resolve_horizontal_bar_chart):
    """Three-series case: all midpoints derive from value-descending stacking.

    Series apple=10, banana=20, cherry=30. Value ordering (largest at baseline):
    cherry at 0..30 → mid 15;
    banana at 30..50 → mid 40;
    apple at 50..60 → mid 55.
    """
    data = [
        {"row": "r1", "value": 10, "series": "apple"},
        {"row": "r1", "value": 20, "series": "banana"},
        {"row": "r1", "value": 30, "series": "cherry"},
        {"row": "r2", "value": 1, "series": "apple"},
        {"row": "r2", "value": 1, "series": "banana"},
        {"row": "r2", "value": 1, "series": "cherry"},
    ]
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    spec = _render(rc, data)
    rows = _label_rows(spec)
    by_series = {r["series"]: r["__x"] for r in rows}
    assert by_series == {"cherry": 15.0, "banana": 40.0, "apple": 55.0}, (
        f"Expected cherry=15 (baseline, largest), banana=40, apple=55 (top, smallest) "
        f"with value-descending stack order; got {by_series!r}."
    )


def test_horizontal_endpoint_midpoints_honor_alphabetical_stack_order(
    resolve_horizontal_bar_chart: Any,
) -> None:
    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(
        data=data,
        enabled=True,
        stack="zero",
        stack_order="alphabetical",
    )
    rows = _label_rows(_render(rc, data))
    by_series = {row["series"]: row["__x"] for row in rows}

    assert by_series == {"A": 20.0, "B": 70.0}


def test_horizontal_stacked_does_not_flip_axis_y_orient(make_chart):
    """The labels pane sits ABOVE the chart, not to its right — axis orient
    resolution must not flip ``axis_y`` to ``"left"`` for horizontal endpoint
    labels (no right-edge collision risk).
    """
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    reset_config()
    patch = BarChartStylePatch.model_validate(
        {"endpoint_labels": {"visible": True}, "orientation": "horizontal"}
    )
    chart = BarChart(
        id="t",
        type="bar",
        x="row",
        y="value",
        color="series",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=patch,
    )
    data = _two_series_two_row_data()
    _rc = resolve(chart, data, chart_style_context=_BOARD_CTX)
    spec = generate_vega_lite_spec(chart, data, width=400)
    # Horizontal stacked endpoint labels emit vconcat (rail above chart);
    # the chart pane is hconcat[1] in the vertical case but vconcat[1] here.
    chart_pane = spec["vconcat"][1] if "vconcat" in spec else spec
    # Post-swap: original axis_y carries the *measure* axis and lands as
    # encoding.x. orient resolution should not silently flip the measure
    # axis to "left" for a horizontal rail-wrapped chart.
    x_orient = chart_pane.get("encoding", {}).get("x", {}).get("axis", {}).get("orient")
    assert x_orient != "left", (
        f"Horizontal rail-wrapped bar should not move the measure axis to "
        f"'left' (no right-edge label pane to collide with); got {x_orient!r}"
    )


def test_rail_follows_vl_stacked_sum_not_per_row_min(resolve_horizontal_bar_chart):
    """Vega-Lite's sort op defaults to `sum` on a stacked plot, not `min`.

    Data chosen so the two disagree: alpha's min (1) beats beta's (30), but
    beta's total (70) beats alpha's (101) ascending. Mirroring `min` would
    anchor the rail on alpha while VL renders beta on top, putting a label
    past the end of the bar it names.
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSort

    data = [
        {"row": "alpha", "value": 1, "series": "A"},
        {"row": "alpha", "value": 100, "series": "B"},
        {"row": "beta", "value": 30, "series": "A"},
        {"row": "beta", "value": 40, "series": "B"},
    ]
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    rc = rc.model_copy(update={"sort": ChartSort(by="value", order="asc")})
    spec = _render(rc, data)

    rows = _label_rows(spec)
    by_series = {r["series"]: r["__x"] for r in rows}
    # beta sorts first by total (70 < 101), so its segments are the anchors:
    # B=40 at the baseline (0..40, mid 20), A=30 on top (40..70, mid 55).
    assert by_series == {"A": 55.0, "B": 20.0}


def test_rail_sorts_decimal_measures_like_vega_lite_does(resolve_horizontal_bar_chart):
    """A SQL ``SUM`` over a numeric column arrives as ``Decimal``.

    Vega-Lite sees floats (the rows are normalized before they reach
    ``data.values``), so a domain order that only counts int/float would fall
    back to alphabetical and anchor the rail on a different row than the one
    rendered on top.
    """
    from decimal import Decimal

    from dbt_charts.core.compile.models.chart.authored import ChartSort

    data = [
        {"row": "alpha", "value": Decimal("1"), "series": "A"},
        {"row": "alpha", "value": Decimal("100"), "series": "B"},
        {"row": "beta", "value": Decimal("30"), "series": "A"},
        {"row": "beta", "value": Decimal("40"), "series": "B"},
    ]
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    rc = rc.model_copy(update={"sort": ChartSort(by="value", order="asc")})
    spec = _render(rc, data)

    rows = _label_rows(spec)
    by_series = {r["series"]: r["__x"] for r in rows}
    # Same totals as the float case: beta (70) sorts ahead of alpha (101), so
    # beta's segments anchor the rail — B=40 (0..40, mid 20), A=30 (40..70, mid 55).
    assert by_series == {"A": 55.0, "B": 20.0}


def test_non_numeric_sort_raises_when_the_author_opts_in(resolve_horizontal_bar_chart):
    """Resolve steers the default away from a sort column with no numbers.

    An explicit opt-in reaches render, where dbt Charts does not confirm which
    category the sorted axis draws first — so it says so rather than anchoring
    every label on a row Vega-Lite may not draw on top.
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSort
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(data=data, enabled=True, stack="zero")
    rc = rc.model_copy(update={"sort": ChartSort(by="row", order="desc")})
    with pytest.raises(ChartDataError, match="carrying numeric values"):
        _render(rc, data)


def test_top_rail_does_not_warn_about_labels_it_never_truncates(
    resolve_horizontal_bar_chart,
):
    """The vconcat rail ignores the pane-width cap, so nothing is cut — no warning.

    Regression: the cap was measured before the layout branch, so a horizontal
    stacked bar with long series names reported labels as truncated while
    _wrap_vconcat_label_rail drew them in full. record_series_label_truncations
    is only ever called from the right_pane (vertical) branch of
    EndpointLabelFeature.apply — top_rail's own measure_label_pane_width call
    computes truncated_labels but never forwards it — so `truncations` is
    structurally empty here at any width; this test's real protection is
    `"limit" not in mark` (the rail must never carry the cap the right_pane
    layout applies), pinning that the two code paths stay split.

    Width is generous only so the two (deliberately long) names don't trip
    the unrelated collision disqualifier — not chosen to hit any
    truncation-specific threshold, since top_rail has none.
    """
    from dbt_charts.core.render.chart.series_label_truncation import (
        collect_series_label_truncations,
    )

    long_a = "Enterprise Cloud Data Integration Platform — North America West"
    data = [
        {"row": "alpha", "value": 40, "series": long_a},
        {"row": "alpha", "value": 60, "series": long_a + " East"},
    ]
    rc = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="zero", width=1600.0
    )

    with collect_series_label_truncations() as truncations:
        spec = _render(rc, data, width=1600.0)

    assert "vconcat" in spec, "fixture must exercise the top_rail layout"
    # The rail mark carries no limit, so Vega draws the full name — warning here
    # would send the author chasing a truncation that never happened.
    assert "limit" not in spec["vconcat"][0]["mark"]
    assert truncations == {}


def test_wide_measure_rail_collision_check_measures_humanized_text(
    make_chart, monkeypatch
):
    """The rail-vs-legend collision check must font-measure the same
    ``wide_measure_labels_for`` humanized text the rail itself paints,
    not the raw measure name -- a humanized name can be narrow enough to
    avoid a collision the raw name's width would predict (or vice versa),
    so the check and the render must agree on which text they measure.

    Monkeypatches the check's own humanizer to the identity function
    (measuring the raw name instead) and asserts the collision verdict
    genuinely differs at a width chosen empirically to sit inside the two
    verdicts' gap (380px raw-collides, humanized-fits)."""
    import dbt_charts.core.compile.resolve.chart.bar as bar_module
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    measures = [
        "revenue_usd",
        "net_revenue",
        "gross_margin_pct",
        "yoy_growth_pct",
    ]
    data = [{"row": "alpha", **dict.fromkeys(measures, 10)}]
    style = BarChartStylePatch.model_validate(
        {"orientation": "horizontal", "stack": "zero"}
    )
    chart = make_chart("bar", x="row", y=measures, style=style)

    rc_humanized = resolve(chart, data, chart_style_context=_BOARD_CTX, width=380.0)

    monkeypatch.setattr(
        bar_module,
        "wide_measure_labels_for",
        lambda measures: {m: m for m in measures},
    )
    rc_raw = resolve(chart, data, chart_style_context=_BOARD_CTX, width=380.0)

    assert rc_humanized.style.endpoint_labels.visible is True
    assert rc_raw.style.endpoint_labels.visible is False


def test_wide_measure_rail_collision_check_measures_humanized_composite_text(
    make_chart, monkeypatch
):
    """Same invariant as the sibling test above, on the dimensioned shape:
    the rail-vs-legend collision check must measure the HUMANIZED
    composite (``<dimension value> - <humanized measure>``,
    ``label_of_series`` in ``_horizontal_rail_labels_would_collide``), not
    the raw composite."""
    import dbt_charts.core.compile.resolve.chart.bar as bar_module
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    measures = [
        "revenue_usd",
        "net_revenue",
        "gross_margin_pct",
        "yoy_growth_pct",
    ]
    data = [{"row": "alpha", "d": "A", **dict.fromkeys(measures, 10)}]
    style = BarChartStylePatch.model_validate(
        {"orientation": "horizontal", "stack": "zero"}
    )
    chart = make_chart("bar", x="row", y=measures, color="d", style=style)

    # 460px sits between the two: the humanized composites fit the rail and
    # the raw ones do not. The two spellings differ only in glyph width
    # (spaces and parens against underscores), so the window is narrow.
    width = 460.0
    rc_humanized = resolve(chart, data, chart_style_context=_BOARD_CTX, width=width)

    monkeypatch.setattr(
        bar_module,
        "wide_measure_labels_for",
        lambda measures: {m: m for m in measures},
    )
    rc_raw = resolve(chart, data, chart_style_context=_BOARD_CTX, width=width)

    assert rc_humanized.style.endpoint_labels.visible is True
    assert rc_raw.style.endpoint_labels.visible is False


def test_center_stack_with_an_opted_in_rail_raises_a_registered_code(
    resolve_horizontal_bar_chart,
):
    """An author who forces the rail on over a center stack gets a coded error
    whose fix names the full authored path, not the ERR-INTERNAL fallback."""
    from dbt_charts.core.diagnostics.chart_data import ChartDataError
    from dbt_charts.core.diagnostics.codes_render import (
        ERR_ENDPOINT_LABELS_CENTER_STACK,
    )

    data = _two_series_two_row_data()
    rc = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="center", author_asked=True
    )
    with pytest.raises(ChartDataError) as exc_info:
        _render(rc, data)
    assert exc_info.value.code is ERR_ENDPOINT_LABELS_CENTER_STACK
    assert "style.endpoint_labels.visible" in str(exc_info.value)


def test_negative_values_with_an_opted_in_rail_raise_a_registered_code(
    resolve_horizontal_bar_chart,
):
    from dbt_charts.core.diagnostics.chart_data import ChartDataError
    from dbt_charts.core.diagnostics.codes_render import (
        ERR_ENDPOINT_LABELS_NEGATIVE_STACK,
    )

    data = [
        {"row": "a", "series": "A", "value": 10},
        {"row": "a", "series": "B", "value": -4},
    ]
    rc = resolve_horizontal_bar_chart(
        data=data, enabled=True, stack="zero", author_asked=True
    )
    with pytest.raises(ChartDataError) as exc_info:
        _render(rc, data)
    assert exc_info.value.code is ERR_ENDPOINT_LABELS_NEGATIVE_STACK
    assert "style.endpoint_labels.visible" in str(exc_info.value)
