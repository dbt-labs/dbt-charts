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
                visible="auto" if enabled else False,
                label_offset=label_offset,
                height=20.0,
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


# ---------------------------------------------------------------------------
# Anchor: top-row only, segment midpoints
# ---------------------------------------------------------------------------


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
        temporal_band_axes=frozenset(),
    )
    hidden_labels = plan.axes.x.style.labels.model_copy(update={"visible": False})
    hidden = plan.axes.x.style.model_copy(update={"labels": hidden_labels})
    assert _categorical_axis_gutter_px(hidden, data, "row") == 0.0


# ---------------------------------------------------------------------------
# Wrap-time legend suppression and normalize x-domain pin
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Label-pane structure
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# axis_y orient: horizontal endpoint labels do NOT flip axis_y
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Regression: color→segment mapping was inverted (follow-up to #2390)
# ---------------------------------------------------------------------------


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
    verdicts' gap (381.5px raw-collides, humanized-fits)."""
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

    rc_humanized = resolve(chart, data, chart_style_context=_BOARD_CTX, width=381.5)

    monkeypatch.setattr(
        bar_module,
        "wide_measure_labels_for",
        lambda measures, field_labels: {m: m for m in measures},
    )
    rc_raw = resolve(chart, data, chart_style_context=_BOARD_CTX, width=381.5)

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

    # 463px sits between the two: the humanized composites fit the rail and
    # the raw ones do not. The two spellings differ only in glyph width
    # (spaces and parens against underscores), so the window is narrow.
    width = 463.0
    rc_humanized = resolve(chart, data, chart_style_context=_BOARD_CTX, width=width)

    monkeypatch.setattr(
        bar_module,
        "wide_measure_labels_for",
        lambda measures, field_labels: {m: m for m in measures},
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
