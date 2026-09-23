"""TDD tests for area chart endpoint labels and halo render order.

These tests must FAIL until:
- AreaStyle gains endpoint_labels: EndpointLabelsConfig
- theme YAML gains area.endpoint_labels section
- _maybe_wrap_endpoint_label_pane handles chart_type == "area"
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.emitters._cartesian import companion_color_for_fill
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


@pytest.fixture
def make_area_resolved_chart(make_chart, model_copy_at):
    """Build a resolved multi-series area chart with endpoint_labels configured."""
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig

    def _build(enabled: bool, chart_width: int = 400):
        chart = make_chart("area", x="date", y="value", color="series")
        data = [
            {"date": "2024-01-01", "value": 100, "series": "Revenue"},
            {"date": "2024-02-01", "value": 120, "series": "Revenue"},
            {"date": "2024-01-01", "value": 80, "series": "Costs"},
            {"date": "2024-02-01", "value": 90, "series": "Costs"},
        ]

        seed = model_copy_at(
            get_theme_style(),
            "charts.area.endpoint_labels",
            EndpointLabelsConfig(visible=enabled, label_offset=6.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)

        resolved = resolve(chart, data, chart_style_context=board_style)
        return resolved, data

    return _build


def test_area_endpoint_labels_emits_hconcat(make_area_resolved_chart):
    """Multi-series area chart with endpoint_labels.visible=True → hconcat with 2 panes."""

    resolved, data = make_area_resolved_chart(enabled=True)
    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE, width=400)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload

    assert "hconcat" in spec, (
        "Expected hconcat top-level key for area with endpoint labels"
    )
    assert len(spec["hconcat"]) == 2, "Expected 2 panes: chart + label pane"
    assert spec.get("resolve", {}).get("scale", {}).get("color") == "independent"


def test_area_endpoint_labels_disabled_no_wrap(make_area_resolved_chart):
    """Multi-series area chart with endpoint_labels.visible=False → no hconcat."""

    resolved, data = make_area_resolved_chart(enabled=False)
    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE, width=400)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload

    assert "hconcat" not in spec, "Expected no hconcat when endpoint labels disabled"


def test_area_halo_render_order_all_halos_before_foreground(make_chart):
    """Multi-series area spec: halo layer (background fill) comes before foreground.

    Layer 0 must have fill=background and tooltip=False (halo_fill).
    Layer 1 must be a line with tooltip=False (halo_line).
    The last area-type layer (with tooltip=True) must have fillOpacity < 1.
    This verifies the 'all halos first, then fg' invariant is maintained.
    """

    chart = make_chart("area", x="date", y="value", color="series")
    data = [
        {"date": "2024-01-01", "value": 100, "series": "Revenue"},
        {"date": "2024-02-01", "value": 120, "series": "Revenue"},
        {"date": "2024-01-01", "value": 80, "series": "Costs"},
        {"date": "2024-02-01", "value": 90, "series": "Costs"},
    ]
    resolved = resolve(chart, data, chart_style_context=_BOARD_CTX)
    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE, width=400)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload

    # Multi-series area is always a layered spec (halo + foreground).
    # When endpoint labels are enabled (editorial default), the spec is hconcat
    # with the main chart pane as hconcat[0]; otherwise the layers are at the top.
    main_pane = spec["hconcat"][0] if "hconcat" in spec else spec
    layers = main_pane.get("layer", [])
    assert len(layers) >= 2, (
        f"Expected layered spec for multi-series area; got {list(main_pane.keys())}"
    )

    # Halo area fill is always layer[0]: opacity=1, fill=background, no tooltip
    halo_mark = layers[0].get("mark", {})
    assert halo_mark.get("opacity") == 1, "Halo layer must have opacity=1"
    assert halo_mark.get("tooltip") is False, "Halo layer must not show tooltip"

    # Foreground area fill: last area-type layer with fillOpacity < 1
    fg_area_layers = [
        layer
        for layer in layers
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "area"
        and layer["mark"].get("tooltip") is True
    ]
    assert fg_area_layers, "No foreground area layer (tooltip=True) found"
    fg_fill_opacity = fg_area_layers[-1]["mark"].get("fillOpacity")
    assert fg_fill_opacity is not None and fg_fill_opacity < 1, (
        f"Foreground area layer must have fillOpacity < 1 (soft fill); got {fg_fill_opacity}"
    )


def test_area_endpoint_labels_disable_pane0_legend(make_area_resolved_chart):
    """When wrapping fires on area, pane[0]'s color legend is auto-disabled.

    Mirror of the bar test — the suppression lives at the shared wrap
    boundary in _maybe_wrap_endpoint_label_pane, so every family that
    wraps must observe the contract. Resolved in
    design/chart-briefs/endpoint-labels.md.
    """

    resolved, data = make_area_resolved_chart(enabled=True)
    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE, width=400)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload
    pane0 = spec["hconcat"][0]
    assert pane0["encoding"]["color"]["legend"] is None, (
        "pane[0].encoding.color.legend must be None when area endpoint labels wrap; "
        "the right-edge label pane already names every series."
    )


def test_label_dark_companion_follows_chart_local_palette_override(
    make_chart, model_copy_at
):
    """When ``style.palette`` overrides the chart palette, label
    dark-companion colors must follow the OVERRIDE, not the alphabetical-
    domain position in the theme palette.

    Regression: an area chart authored with ``palette:
    [palette[0], palette[2]]`` (skip slot 1's clashing cyan) was rendering
    Core/Growth as blue/green areas but the LABEL pane was inking
    Core=dark-blue and Growth=dark-CYAN — the dark companion was indexed
    by alphabetical-domain position (slot 0, slot 1) instead of by the
    actual emitted color. Fix looks each emitted color up in the bright
    vivid-10 palette and pairs with the dark palette by that index.
    """
    from dbt_charts.core.compile.models.style.authored import (
        AreaChartStylePatch,
        EndpointLabelsConfig,
    )

    # Authoring picks the BLUE+GREEN pair (theme palette slots 0 and 2),
    # skipping the cyan slot-1.
    chart = make_chart(
        "area",
        x="date",
        y="value",
        color="series",
        style=AreaChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": ["#0073c2", "#00ad75"]}}}
        ),
    )
    data = [
        {"date": "2024-01-01", "value": 100, "series": "Core"},
        {"date": "2024-02-01", "value": 120, "series": "Core"},
        {"date": "2024-01-01", "value": 80, "series": "Growth"},
        {"date": "2024-02-01", "value": 90, "series": "Growth"},
    ]
    seed = model_copy_at(
        get_theme_style(),
        "charts.area.endpoint_labels",
        EndpointLabelsConfig(visible=True, label_offset=6.0, height=20.0),
    )
    board_style = resolve_chart_style_context(seed)
    resolved = resolve(chart, data, chart_style_context=board_style)
    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE, width=400)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload
    label_pane = spec["hconcat"][1]
    label_range = label_pane["encoding"]["color"]["scale"]["range"]

    # The two emitted area colors are the chart-local override, blue and
    # green. The label ink must be EACH color's own canvas-aware ink — NOT
    # a hand-tuned companion looked up by theme-palette slot (which used to
    # give dark-blue + dark-CYAN when indexed by alphabetical-domain
    # position instead of the actual emitted color).
    from dbt_charts.core.compile.resolve.style.palette import label_ink

    expected = [
        label_ink("#0073c2", board_style.background),  # Core
        label_ink("#00ad75", board_style.background),  # Growth
    ]
    assert label_range == expected, (
        f"Label pane dark companion colors must track the chart-local "
        f"palette override; expected {expected!r} got {label_range!r}"
    )


# ---------------------------------------------------------------------------
# Stack-aware anchors (mirrors test_bar_endpoint_labels.py) — regression for
# stacked-area-endpoint-labels-place-at-raw-y-not-band-midpoints +
# normalize-stacked-top-rule-shipped-but-is-not-visible-root-cause.
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_with_area_endpoint_labels(model_copy_at):
    """Resolved style with area.endpoint_labels override applied, seeded from stark."""

    def _build(enabled: bool, label_offset: float = 5.0):
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig

        seed = model_copy_at(
            get_theme_style("stark"),
            "charts.area.endpoint_labels",
            EndpointLabelsConfig(
                visible=enabled, label_offset=label_offset, height=20.0
            ),
        )
        return resolve_chart_style_context(seed)

    return _build


@pytest.fixture
def resolve_area_chart(make_chart, seed_with_area_endpoint_labels):
    """Resolved area chart with endpoint_labels enabled and optional stack."""

    def _build(
        data: list[dict[str, Any]],
        enabled: bool,
        color: str | None = "series",
        stack: Any = None,
    ):
        kwargs: dict[str, Any] = {}
        if color is not None:
            kwargs["color"] = color
        if stack is not None:
            kwargs["stack"] = stack
        chart = make_chart("area", x="date", y="value", **kwargs)
        board_style = seed_with_area_endpoint_labels(enabled)
        return resolve(chart, data, chart_style_context=board_style)

    return _build


def _three_series_stacked_data() -> list[dict[str, Any]]:
    """Three-series data whose global-total order differs from alphabetical.

    Trailing x = 2024-02-01: Alpha=10, Beta=50, Gamma=30 -> total 90.
    Global totals: Beta=70, Gamma=45, Alpha=15.
      Beta at baseline: 0..50   -> midpoint 25
      Gamma:            50..80  -> midpoint 65
      Alpha at top:      80..90  -> midpoint 85
    """
    return [
        {"date": "2024-01-01", "value": 5, "series": "Alpha"},
        {"date": "2024-01-01", "value": 20, "series": "Beta"},
        {"date": "2024-01-01", "value": 15, "series": "Gamma"},
        {"date": "2024-02-01", "value": 10, "series": "Alpha"},
        {"date": "2024-02-01", "value": 50, "series": "Beta"},
        {"date": "2024-02-01", "value": 30, "series": "Gamma"},
    ]


def _render_area(rc, data: list[dict[str, Any]]) -> dict[str, Any]:
    artifact = render_resolved_chart(rc, data, _BOARD_STYLE, width=400)
    assert artifact.kind == "vega_spec"
    return artifact.payload


def _area_label_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    pane = spec["hconcat"][1]
    return pane["data"]["values"]


def test_stacked_area_anchors_are_cumulative_midpoints_not_raw_values(
    resolve_area_chart,
):
    """Stacked area labels follow the emitted global-total stack order.

    Regression for stacked-area-endpoint-labels-place-at-raw-y-not-band-midpoints:
    before the fix, labels anchored at the raw values {Alpha: 10, Beta: 50,
    Gamma: 30} regardless of stacking.
    """
    data = _three_series_stacked_data()
    rc = resolve_area_chart(data=data, enabled=True, stack="zero")
    spec = _render_area(rc, data)
    rows = _area_label_rows(spec)
    by_series = {r["series"]: r["__y"] for r in rows}
    assert by_series == {"Beta": 25.0, "Gamma": 65.0, "Alpha": 85.0}

    main_scale = spec["hconcat"][0]["encoding"]["color"]["scale"]
    label_scale = spec["hconcat"][1]["encoding"]["color"]["scale"]
    fill_by_series = dict(zip(main_scale["domain"], main_scale["range"], strict=True))
    label_fill_by_series = dict(
        zip(label_scale["domain"], label_scale["range"], strict=True)
    )
    assert label_fill_by_series == {
        series: companion_color_for_fill(
            fill,
            list(rc.palette),
            rc.style.series_label.dark_companion_palette,
        )
        for series, fill in fill_by_series.items()
    }


def _series_missing_from_final_column_data() -> list[dict[str, Any]]:
    """Three series where Beta has no row at the trailing x, but has a real
    row at Jan — it anchors on its own last real segment there, not a
    zero-height seam at the trailing column.

    Area stacks by descending chart-global total:
    series_order = [Gamma, Beta, Alpha].

    Beta anchors at Jan (its own last real value), where the stack is
    {Alpha: 50, Beta: 200, Gamma: 150} in series_order [Gamma, Beta, Alpha]:
      Gamma at baseline: 0..150    → midpoint 75 (Gamma's own anchor is
                                      still Feb, below — this is only Jan's
                                      column shape for computing Beta's cum)
      Beta:               150..350 → midpoint 250
    Gamma and Alpha still anchor at the trailing x (Feb, where both have
    real values): Gamma stays at 150, Alpha at 350.
    """
    return [
        {"date": "2024-01-01", "value": 50, "series": "Alpha"},
        {"date": "2024-01-01", "value": 200, "series": "Beta"},
        {"date": "2024-01-01", "value": 150, "series": "Gamma"},
        {"date": "2024-02-01", "value": 100, "series": "Alpha"},
        {"date": "2024-02-01", "value": 300, "series": "Gamma"},
    ]


def test_stacked_area_labels_name_series_absent_from_final_column(
    resolve_area_chart,
) -> None:
    """Bar and area share the stacked-anchor helper, so both keep the naming contract."""
    data = _series_missing_from_final_column_data()
    rc = resolve_area_chart(data=data, enabled=True, stack="zero")
    spec = _render_area(rc, data)
    rows = _area_label_rows(spec)
    by_series = {r["series"]: r["__y"] for r in rows}
    assert by_series == {"Gamma": 150.0, "Beta": 250.0, "Alpha": 350.0}


def test_normalize_stacked_area_anchors_on_unit_scale(resolve_area_chart):
    """Normalize-stacked area: midpoints are shares of the column total."""
    data = _three_series_stacked_data()
    rc = resolve_area_chart(data=data, enabled=True, stack="normalize")
    spec = _render_area(rc, data)
    rows = _area_label_rows(spec)
    by_series = {r["series"]: r["__y"] for r in rows}
    assert by_series["Beta"] == pytest.approx(25.0 / 90)
    assert by_series["Gamma"] == pytest.approx(65.0 / 90)
    assert by_series["Alpha"] == pytest.approx(85.0 / 90)


def test_unstacked_area_still_anchors_at_raw_values(resolve_area_chart):
    """Unstacked (stack=None) area keeps the pre-fix raw-value anchor — the
    stack-aware fix must not perturb overlapping/ungrouped area behavior."""
    data = _three_series_stacked_data()
    rc = resolve_area_chart(data=data, enabled=True, stack=None)
    spec = _render_area(rc, data)
    rows = _area_label_rows(spec)
    by_series = {r["series"]: r["__y"] for r in rows}
    assert by_series == {"Alpha": 10.0, "Beta": 50.0, "Gamma": 30.0}


def test_normalize_stack_pins_pane0_y_scale_domain_to_unit_for_area(
    resolve_area_chart,
):
    """Stack normalize must pin pane[0].encoding.y.scale.domain = [0, 1] for area,
    mirroring the bar family. This is the #30 root cause: without the pin, VL's
    auto-scale on the raw value field yields [0, max(value)], and resolve.scale.y
    = shared then squashes the normalize stack (and its baseline rules) near zero.
    """
    data = _three_series_stacked_data()
    rc = resolve_area_chart(data=data, enabled=True, stack="normalize")
    spec = _render_area(rc, data)
    pane0 = spec["hconcat"][0]
    y_scale = pane0["encoding"]["y"].get("scale", {})
    assert y_scale.get("domain") == [0, 1], (
        f"Expected pane[0].encoding.y.scale.domain == [0, 1] for stack=normalize, "
        f"got {y_scale!r}"
    )


def test_zero_stack_does_not_pin_y_scale_domain_for_area(resolve_area_chart):
    """Default (zero) stack must NOT inject a y-scale domain for area."""
    data = _three_series_stacked_data()
    rc = resolve_area_chart(data=data, enabled=True, stack="zero")
    spec = _render_area(rc, data)
    pane0 = spec["hconcat"][0]
    y_enc = pane0["encoding"]["y"]
    domain = (
        y_enc.get("scale", {}).get("domain")
        if isinstance(y_enc.get("scale"), dict)
        else None
    )
    assert domain is None, (
        f"pane[0].encoding.y.scale.domain must NOT be pinned when stack != normalize; "
        f"got {domain!r}"
    )


def test_area_center_stack_anchors_at_offset_midpoints(resolve_area_chart):
    """stack: 'center' (streamgraph): labels sit at cumulative segment
    midpoints offset by Vega-Lite's own center-offset formula
    ``(max_column_total - this_column_total) / 2`` — verified against
    Vega-Lite's compiled scenegraph output, NOT a per-column ``-total/2``
    silhouette (that formula produces pixel-mismatched labels; see
    test_streamgraph_endpoint_labels.py for a fixture that catches it).

    Final x (2024-02-01) is ALSO the max-column-total x in this fixture
    (both 90), so offset = (90-90)/2 = 0 here — center-stack anchors land
    exactly on the plain cumulative midpoints, same as zero-stack.
      Beta:  mid 25, offset: 25 + 0 = 25
      Gamma: mid 65, offset: 65 + 0 = 65
      Alpha: mid 85, offset: 85 + 0 = 85
    """
    data = _three_series_stacked_data()
    rc = resolve_area_chart(data=data, enabled=True, stack="center")
    spec = _render_area(rc, data)
    assert "hconcat" in spec, "Center-stack area must wrap in an endpoint-label pane"
    rows = _area_label_rows(spec)
    by_series = {r["series"]: r["__y"] for r in rows}
    assert by_series == {"Beta": 25.0, "Gamma": 65.0, "Alpha": 85.0}


def test_center_stack_pins_pane0_y_scale_domain_to_stacked_extent(
    resolve_area_chart,
):
    """Center stack pins pane[0].encoding.y.scale.domain to
    [0, max_column_total] — the SAME domain a plain zero-stack renders on,
    not a domain symmetric around zero. Vega-Lite's center-offset floats
    each column up toward that shared ceiling rather than self-centering
    each column independently. Max column total across both x is 90
    (2024-02-01), so domain is [0, 90].
    """
    data = _three_series_stacked_data()
    rc = resolve_area_chart(data=data, enabled=True, stack="center")
    spec = _render_area(rc, data)
    pane0 = spec["hconcat"][0]
    y_scale = pane0["encoding"]["y"].get("scale", {})
    assert y_scale.get("domain") == [0.0, 90.0]


def test_area_negative_values_raise_for_stacked(resolve_area_chart):
    """Negative values in a stacked area would put the cumulative domain across
    both signs and break the midpoint computation — must raise."""
    data = [
        {"date": "2024-01-01", "value": 10, "series": "A"},
        {"date": "2024-01-01", "value": -20, "series": "B"},
    ]
    rc = resolve_area_chart(data=data, enabled=True, stack="zero")
    with pytest.raises(ChartDataError, match="negative"):
        _render_area(rc, data)


def test_normalize_stacked_area_top_rule_stays_within_pinned_domain(
    resolve_area_chart,
):
    """#30 regression: normalize-stacked area WITH endpoint labels visible must
    keep the shared y-scale pinned to [0, 1] so BaselineFeature's 0/1 rule pair
    renders at the domain extremes — not squashed by the (pre-fix) raw-value
    label pane blowing out the shared hconcat y-scale, which is why the
    100%-baseline top rule "disappeared" in practice.
    """
    data = _three_series_stacked_data()
    rc = resolve_area_chart(data=data, enabled=True, stack="normalize")
    spec = _render_area(rc, data)
    pane0 = spec["hconcat"][0]
    y_scale = pane0["encoding"]["y"].get("scale", {})
    assert y_scale.get("domain") == [0, 1]

    rule_layers = [
        layer
        for layer in pane0.get("layer", [])
        if layer.get("mark") == "rule"
        or (isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule")
    ]
    datums = sorted(layer["encoding"]["y"]["datum"] for layer in rule_layers)
    assert datums == [0, 1], (
        f"Expected baseline rule pair at datum 0 and 1 with the shared y-scale "
        f"pinned to [0, 1]; got rule datums {datums!r}"
    )


def test_wide_area_truncation_names_the_y_key_not_the_fold_column(make_chart):
    """A wide-form area has no `color:` — its rail labels are the `y:` measures.

    Regression: the truncation record carried the fold-generated column name,
    so the warning read "from column '__df_wide_measure_label'" and anchored on
    a `color` key the author never wrote.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
    from dbt_charts.core.render.chart.series_label_truncation import (
        collect_series_label_truncations,
    )
    from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

    board_style, board_ctx = resolve_style_and_context(get_theme_style())
    long_measure = "enterprise_cloud_data_integration_platform_north_america_west"
    rows = [
        {"date": "2024-01-01", long_measure: 100, "costs": 80},
        {"date": "2024-02-01", long_measure: 120, "costs": 90},
    ]
    chart = make_chart("area", x="date", y=[long_measure, "costs"])
    resolved = resolve(chart, rows, chart_style_context=board_ctx)

    with collect_series_label_truncations() as truncations:
        render_resolved_chart(resolved, rows, board_style, width=400)

    records = truncations[resolved.id]
    assert [r.authored_field for r in records] == ["y"]
    # Humanized (default_axis_title), matching the rail's own rendered text
    # and the wide chart's y-axis title for the same measure -- not the raw
    # column name.
    assert (
        records[0].series_name
        == "enterprise cloud data integration platform north america west"
    )
