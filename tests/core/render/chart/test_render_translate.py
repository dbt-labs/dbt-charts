"""Tests for the render-v2 translate_to_vl() function and per-family dispatch."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.resolve.style.typography import concrete_title_style
from dbt_charts.core.render.chart.spec import ChartSpec, EndpointLabelData
from dbt_charts.core.render.chart.translate import translate_to_vl

# Vega-Lite schema URL prefix (the exact version may vary; test for prefix)
_VL_SCHEMA_PREFIX = "https://vega.github.io/schema/vega-lite/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(mark: str, encoding: dict[str, Any] | None = None) -> ChartSpec:
    return ChartSpec(mark=mark, encoding=encoding or {}, layers=[], config={})


def _mark_type(result: dict[str, Any]) -> str:
    """Extract the VL mark type string regardless of shorthand vs object form."""
    mark = result.get("mark")
    if isinstance(mark, dict):
        return mark["type"]
    return str(mark)


def test_endpoint_label_data_requires_label_offset_and_height() -> None:
    """EndpointLabelData must not carry in-code defaults for label_offset/height.

    Both values are theme config (EndpointLabelsConfig.label_offset/height) —
    a hardcoded dataclass default would silently discard whatever the theme
    cascade resolved. Omitting either kwarg must raise TypeError, not fall
    through to a literal.
    """
    kwargs: dict[str, Any] = {
        "series_field": "category",
        "value_alias": "__y",
        "positions": [("A", 10.0)],
        "color_domain": ["A"],
        "color_range": ["#1f77b4"],
    }
    with pytest.raises(TypeError):
        EndpointLabelData(height=20.0, **kwargs)  # missing label_offset
    with pytest.raises(TypeError):
        EndpointLabelData(label_offset=6.0, **kwargs)  # missing height


# ---------------------------------------------------------------------------
# VL families — returns a valid VL dict with $schema
# ---------------------------------------------------------------------------


def test_translate_bar_returns_vl_dict() -> None:
    result = translate_to_vl(
        _spec("bar", {"x": {"field": "month"}, "y": {"field": "revenue"}})
    )
    assert isinstance(result, dict)
    assert "$schema" in result
    assert result["$schema"].startswith(_VL_SCHEMA_PREFIX)


def test_translate_line_returns_vl_dict() -> None:
    result = translate_to_vl(
        _spec("line", {"x": {"field": "date"}, "y": {"field": "value"}})
    )
    assert isinstance(result, dict)
    assert "$schema" in result


def test_translate_geoshape_returns_vl_dict() -> None:
    """Geoshape translate is implemented; must return a VL dict."""
    result = translate_to_vl(_spec("geoshape"))
    assert isinstance(result, dict)
    assert "$schema" in result


def test_line_and_area_layers_always_carry_an_encoding_key() -> None:
    """Vega-Lite 6.x crashes compiling a line or area unit that has no
    `encoding` key at all — its point/line-overlay normalization reads
    `encoding.shape` unconditionally (`TypeError: Cannot read properties of
    undefined`). An empty object means the same thing and is accepted, and a
    parent-level encoding does not substitute. The reachable authored shape is
    a line/area chart with neither `x:` nor `y:`: every sub-layer encoding is
    empty, and dropping the key handed VL the crashing form (ERR-INTERNAL)."""
    layered = ChartSpec(
        mark="layered",
        encoding={},
        layers=[
            _spec("line"),
            _spec("area"),
            _spec("point"),
        ],
        config={},
    )
    result = translate_to_vl(layered)
    line_layer, area_layer, _point_layer = result["layer"]
    assert line_layer["encoding"] == {}
    assert area_layer["encoding"] == {}


# ---------------------------------------------------------------------------
# Mark name — family-level VL mark mapping
# ---------------------------------------------------------------------------


def test_translate_bar_has_bar_mark() -> None:
    result = translate_to_vl(_spec("bar"))
    assert _mark_type(result) == "bar"


def test_translate_point_mark() -> None:
    """Emitters write VL "point" directly; translate_to_vl passes it through."""
    result = translate_to_vl(_spec("point"))
    assert _mark_type(result) == "point"


def test_translate_arc_mark() -> None:
    """Emitters write VL "arc" directly; translate_to_vl passes it through."""
    result = translate_to_vl(_spec("arc", {"theta": {"field": "sales"}}))
    assert _mark_type(result) == "arc"


def test_translate_rect_mark() -> None:
    """Emitters write VL "rect" directly; translate_to_vl passes it through."""
    result = translate_to_vl(_spec("rect"))
    assert _mark_type(result) == "rect"


def test_translate_geoshape_mark_is_geoshape() -> None:
    result = translate_to_vl(_spec("geoshape"))
    assert _mark_type(result) == "geoshape"


# ---------------------------------------------------------------------------
# Unknown marks — raise ValueError
# ---------------------------------------------------------------------------


def test_translate_kpi_mark_raises_unknown() -> None:
    """A spec with mark="kpi" reaches translate_to_vl only in tests or bugs —
    kpi charts are non-VL and routed before emit.  translate_to_vl raises
    because "kpi" is not in _VL_MARKS."""
    with pytest.raises(ValueError, match="kpi"):
        translate_to_vl(_spec("kpi"))


def test_translate_table_mark_raises_unknown() -> None:
    """Same as kpi: "table" is not in _VL_MARKS."""
    with pytest.raises(ValueError, match="table"):
        translate_to_vl(_spec("table"))


def test_translate_unknown_mark_raises() -> None:
    with pytest.raises(ValueError, match="__unknown__"):
        translate_to_vl(_spec("__unknown__"))


def test_translate_layered_raises_for_unmapped_layer_mark() -> None:
    """Layered spec with an unmapped mark in a layer raises, not silently drops."""
    layered_spec = ChartSpec(
        mark="layered",
        encoding={},
        layers=[_spec("kpi")],  # "kpi" is not a VL-translatable mark
        config={},
    )
    with pytest.raises(ValueError, match="kpi"):
        translate_to_vl(layered_spec)


def test_translate_layered_with_underlays_and_no_layers_does_not_raise() -> None:
    """A layered spec whose feature-added underlays (e.g. a threshold rule
    drawn beneath the data) has no ``spec.layers`` of its own must not raise
    ``IndexError``.

    ``_translate_layered`` guarded the main-layer lookup on
    ``if vl["layer"]:`` -- the COMBINED underlays+layers list -- while
    indexing past just the underlays (``vl["layer"][len(spec.underlays):][0]``).
    With underlays present and layers empty, the guard passed but the slice
    was empty, raising IndexError on ``[0]``.
    """
    layered_spec = ChartSpec(
        mark="layered",
        encoding={},
        layers=[],
        underlays=[_spec("rule")],
        config={},
    )
    result = translate_to_vl(layered_spec)
    assert len(result["layer"]) == 1


def test_translate_overlay_layer_unknown_mark_raises() -> None:
    """Overlay layer with an unknown (non-VL) mark must raise, not pass through silently."""
    spec = ChartSpec(
        mark="bar",
        encoding={},
        layers=[_spec("scatter")],  # "scatter" is not a VL mark; was renamed to "point"
        config={},
    )
    with pytest.raises(ValueError, match="scatter"):
        translate_to_vl(spec)


# ---------------------------------------------------------------------------
# Translate boundary: typed ChartSpec fields reach the VL output
# ---------------------------------------------------------------------------


def test_mark_props_reach_vl_mark_dict() -> None:
    """mark_props on ChartSpec → VL mark is a dict with type + extra props."""
    spec = ChartSpec(
        mark="arc",
        encoding={"theta": {"field": "value", "type": "quantitative"}},
        mark_props={"innerRadius": 90},
    )
    result = translate_to_vl(spec)
    assert isinstance(result["mark"], dict)
    assert result["mark"]["type"] == "arc"
    assert result["mark"]["innerRadius"] == 90


def test_projection_field_reaches_vl_spec() -> None:
    """projection on ChartSpec → VL top-level projection dict (via circle mark)."""
    spec = ChartSpec(mark="circle", encoding={}, projection="albersUsa")
    result = translate_to_vl(spec)
    assert result.get("projection") == {"type": "albersUsa"}


# ---------------------------------------------------------------------------
# Endpoint-label wrapping — translate_to_vl must build both hconcat and vconcat
# wrappers from the spec.endpoint_label_layout field.
# ---------------------------------------------------------------------------


def _make_label_data(layout: str) -> EndpointLabelData:
    alias = "__y" if layout == "right_pane" else "__x"
    return EndpointLabelData(
        series_field="category",
        value_alias=alias,
        positions=[("A", 10.0), ("B", 20.0)],
        color_domain=["A", "B"],
        color_range=["#1f77b4", "#ff7f0e"],
        label_offset=6.0,
        height=20.0,
    )


def test_right_pane_layout_produces_hconcat() -> None:
    """endpoint_label_layout='right_pane' → top-level dict has hconcat key."""
    label_data = _make_label_data("right_pane")
    spec = ChartSpec(
        mark="line",
        encoding={"x": {"field": "date", "type": "ordinal"}},
        endpoint_label_layout="right_pane",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    assert "hconcat" in result, f"Expected hconcat wrapper; got keys: {list(result)}"
    assert len(result["hconcat"]) == 2
    # Main chart is first; label pane is second
    main, pane = result["hconcat"]
    assert "mark" in main
    assert pane["mark"]["type"] == "text"


def test_right_pane_requalifies_x_scale_probes_in_mark_exprs() -> None:
    """Wrapping into hconcat renames the chart pane's independent x scale to
    Vega's child-qualified name (concat_0_x), so mark expressions
    that probe a scale by name — continuous_bar_size_prop's min-gap width,
    bandwidth() sizing — must be requalified or they evaluate NaN and paint
    zero-width marks. y stays unqualified: the wrapper shares it."""
    label_data = _make_label_data("right_pane")
    width_expr = "clamp(abs(scale('x', 200.0) - scale('x', 100.0)) - 3.0, 4.0, 20.0)"
    spec = ChartSpec(
        mark="bar",
        encoding={"x": {"field": "d", "type": "temporal"}},
        mark_props={
            "width": {"expr": width_expr},
            "size": {"expr": "1.25 * bandwidth('yOffset')"},
            "opacity": {"expr": "scale('y', 1) > 0 ? 1 : 0"},
        },
        endpoint_label_layout="right_pane",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    main, _pane = result["hconcat"]
    mark = main["mark"]
    assert (
        mark["width"]["expr"]
        == "clamp(abs(scale('concat_0_x', 200.0) - scale('concat_0_x', 100.0)) - 3.0, 4.0, 20.0)"
    )
    # Offset channels are NOT concat-qualified: the label pane has no offset
    # channel, so Vega leaves xOffset/yOffset top-level under their own names.
    assert mark["size"]["expr"] == "1.25 * bandwidth('yOffset')"
    # Shared y is compiled top-level under its own name — untouched.
    assert mark["opacity"]["expr"] == "scale('y', 1) > 0 ? 1 : 0"


def test_right_pane_requalifies_step_band_offset_scale_range() -> None:
    """The walk covers every ``expr``, not only mark props — step-band's
    ``xOffset.scale.range`` probes ``bandwidth('x')`` too.

    ``step_band.apply_step_band`` sizes each plateau off the x band, so a
    step-curve bar wrapped for endpoint labels needs that probe qualified
    exactly like a mark expression: unqualified, it addresses a scale the
    concat no longer has and every plateau collapses to zero width. Narrowing
    the regex to ``scale`` only — the same narrowing this line has already
    taken once — leaves every other bandwidth assertion in the suite green,
    because they all sit on charts without endpoint labels.
    """
    label_data = _make_label_data("right_pane")
    spec = ChartSpec(
        mark="bar",
        encoding={
            "x": {"field": "d", "type": "ordinal"},
            "xOffset": {
                "field": "__step_band_edge",
                "type": "ordinal",
                "scale": {"range": [0, {"expr": "bandwidth('x')"}]},
            },
            "y": {"field": "v", "type": "quantitative"},
        },
        endpoint_label_layout="right_pane",
        endpoint_label_data=label_data,
    )
    main, _pane = translate_to_vl(spec)["hconcat"]
    assert main["encoding"]["xOffset"]["scale"]["range"] == [
        0,
        {"expr": "bandwidth('concat_0_x')"},
    ]


def test_right_pane_scale_names_match_vl_convert() -> None:
    """Pin the child-qualification contract against a real vl_convert compile.

    ``_requalify_concat_scale_probes`` rewrites scale-name probes on the
    assumption that Vega renames the chart pane's scales. That assumption is
    only true for the channels the wrapper leaves independent, so assert it
    against what vl_convert actually emits rather than against the rewrite's
    own output: ``x`` is qualified, the offset channels are not.
    """
    import json

    import vl_convert as vlc

    label_data = _make_label_data("right_pane")
    for offset_channel, dim in (("xOffset", "x"), ("yOffset", "y")):
        spec = ChartSpec(
            mark="bar",
            data=[{"c": "A", "g": "u", "v": 1.0}, {"c": "B", "g": "w", "v": 2.0}],
            encoding={
                dim: {"field": "c", "type": "nominal"},
                offset_channel: {"field": "g", "type": "nominal"},
                "y" if dim == "x" else "x": {"field": "v", "type": "quantitative"},
                "color": {"field": "g", "type": "nominal"},
            },
            endpoint_label_layout="right_pane",
            endpoint_label_data=label_data,
        )
        vega = vlc.vegalite_to_vega(translate_to_vl(spec))
        if isinstance(vega, str):
            vega = json.loads(vega)

        def _scale_names(node: dict[str, Any], out: list[str]) -> list[str]:
            for scale in node.get("scales") or []:
                out.append(scale["name"])
            for mark in node.get("marks") or []:
                _scale_names(mark, out)
            return out

        names = _scale_names(vega, [])
        assert offset_channel in names, (
            f"{offset_channel} should stay top-level, got {names}"
        )
        assert f"concat_0_{offset_channel}" not in names
        assert "concat_0_x" in names, f"x should be concat-qualified, got {names}"


def test_right_pane_hoists_schema_and_config() -> None:
    """$schema and config must be hoisted out of main_vl into the hconcat root."""
    label_data = _make_label_data("right_pane")
    spec = ChartSpec(
        mark="line",
        encoding={},
        config={"axis": {"labelFontSize": 11}},
        endpoint_label_layout="right_pane",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    assert "$schema" in result, "$schema must be hoisted to hconcat root"
    assert "config" in result, "config must be hoisted to hconcat root"
    # Main chart inside hconcat must NOT carry a redundant $schema
    main = result["hconcat"][0]
    assert "$schema" not in main


def test_right_pane_color_scale_encoding_present() -> None:
    """Label pane must carry an independent color scale for series matching."""
    label_data = _make_label_data("right_pane")
    spec = ChartSpec(
        mark="line",
        encoding={},
        endpoint_label_layout="right_pane",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    pane = result["hconcat"][1]
    color_enc = pane["encoding"]["color"]
    assert color_enc["scale"]["domain"] == ["A", "B"]
    assert color_enc["scale"]["range"] == ["#1f77b4", "#ff7f0e"]


def test_top_rail_layout_produces_vconcat() -> None:
    """endpoint_label_layout='top_rail' → top-level dict has vconcat key."""
    label_data = _make_label_data("top_rail")
    spec = ChartSpec(
        mark="bar",
        encoding={"x": {"field": "revenue", "type": "quantitative"}},
        endpoint_label_layout="top_rail",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    assert "vconcat" in result, f"Expected vconcat wrapper; got keys: {list(result)}"
    assert len(result["vconcat"]) == 2
    # Rail is first (top), main chart is second
    rail, main = result["vconcat"]
    assert rail["mark"]["type"] == "text"
    assert "mark" in main


def test_top_rail_requalifies_the_chart_panes_band_probe() -> None:
    """The rail makes the chart pane vega's `concat_1`, whose independent y scale
    is `concat_1_y`: a band-size expression probing `y` would evaluate to zero."""
    spec = ChartSpec(
        mark="bar",
        mark_props={"height": {"expr": "min(0.8 * bandwidth('y'), 24.0)"}},
        encoding={"x": {"field": "revenue", "type": "quantitative"}},
        endpoint_label_layout="top_rail",
        endpoint_label_data=_make_label_data("top_rail"),
    )
    _rail, main = translate_to_vl(spec)["vconcat"]
    assert main["mark"]["height"] == {
        "expr": "min(0.8 * bandwidth('concat_1_y'), 24.0)"
    }


def test_top_rail_uses_configured_rail_height() -> None:
    """Rail height must come from EndpointLabelData.height, not a hardcoded literal."""
    label_data = EndpointLabelData(
        series_field="category",
        value_alias="__x",
        positions=[("A", 10.0), ("B", 20.0)],
        color_domain=["A", "B"],
        color_range=["#1f77b4", "#ff7f0e"],
        label_offset=6.0,
        height=37.0,
    )
    spec = ChartSpec(
        mark="bar",
        encoding={"x": {"field": "revenue", "type": "quantitative"}},
        endpoint_label_layout="top_rail",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    rail, _main = result["vconcat"]
    assert rail["height"] == 37.0, (
        f"rail height is {rail['height']}, expected 37.0 (from EndpointLabelData.height)"
    )


def test_top_rail_hoists_schema() -> None:
    """$schema must be hoisted to vconcat root."""
    label_data = _make_label_data("top_rail")
    spec = ChartSpec(
        mark="bar",
        encoding={},
        endpoint_label_layout="top_rail",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    assert "$schema" in result, "$schema must be hoisted to vconcat root"
    _rail, main = result["vconcat"]
    assert "$schema" not in main


# ---------------------------------------------------------------------------
# spec.tooltip_description must reach the actual chart pane, not the
# hconcat/vconcat wrapper — the wrapper carries no data marks of its own.
# ---------------------------------------------------------------------------


def test_structured_tooltip_reaches_hconcat_main_panel() -> None:
    """right_pane (vertical stacked) layout: description lands in hconcat[0]."""
    label_data = _make_label_data("right_pane")
    spec = ChartSpec(
        mark="bar",
        encoding={"x": {"field": "month", "type": "ordinal"}},
        endpoint_label_layout="right_pane",
        endpoint_label_data=label_data,
        tooltip_description="datum.month",
    )
    result = translate_to_vl(spec)
    main, pane = result["hconcat"]
    assert main["encoding"]["description"] == {"value": {"expr": "datum.month"}}
    # The label pane carries no data marks — it must not gain a description.
    assert "description" not in pane.get("encoding", {})


def test_structured_tooltip_reaches_vconcat_main_panel() -> None:
    """top_rail (horizontal stacked bar) layout: description lands in vconcat[1].

    Regression: _apply_structured_tooltip only recursed into hconcat[0], so a
    horizontal stacked bar's rail wrap (vconcat) stamped the description onto
    the vconcat wrapper itself — a key VL never reads — and the real chart
    pane at vconcat[1] rendered with VL's default per-channel aria-label
    instead of the structured, role-tagged one.
    """
    label_data = _make_label_data("top_rail")
    spec = ChartSpec(
        mark="bar",
        encoding={"x": {"field": "revenue", "type": "quantitative"}},
        endpoint_label_layout="top_rail",
        endpoint_label_data=label_data,
        tooltip_description="datum.revenue",
    )
    result = translate_to_vl(spec)
    rail, main = result["vconcat"]
    assert main["encoding"]["description"] == {"value": {"expr": "datum.revenue"}}
    # The rail pane carries no data marks — it must not gain a description.
    assert "description" not in rail.get("encoding", {})


def test_structured_tooltip_disabled_on_a_dual_axis_zero_rule_nested_one_level_deep() -> (
    None
):
    """`_disable_private_data_channel` is a shared recursive helper used by
    both `_apply_href_link` and `_apply_structured_tooltip` -- this pins the
    generalization directly, at the `_translate_to_vl` level, rather than
    only through the specific pipeline shape (StructuredTooltipFeature
    writes `tooltip_description` onto the inner base entry, not the outer
    wrapper) that would otherwise be the only path exercising this arm."""
    base_entry = ChartSpec(mark="bar", encoding={"x": {"field": "month"}})
    rule = ChartSpec(
        mark="rule",
        encoding={"y": {"datum": 0}},
        data=[{"y": 0}],
    )
    wrapped = ChartSpec(mark="layered", layers=[base_entry, rule])
    spec = ChartSpec(
        mark="layered",
        layers=[wrapped],
        tooltip_description="datum.month",
    )
    result = translate_to_vl(spec)
    nested_rule = result["layer"][0]["layer"][1]
    assert nested_rule["data"] == {"values": [{"y": 0}]}
    assert nested_rule["encoding"]["description"] is None


# ---------------------------------------------------------------------------
# FIX-3: href_link wiring
# ---------------------------------------------------------------------------


def test_href_link_produces_calculate_transform() -> None:
    """spec.href_link → VL calculate transform + href encoding."""
    spec = ChartSpec(
        mark="bar",
        encoding={"x": {"field": "month", "type": "ordinal"}},
        href_link="'https://example.com/' + datum['month']",
    )
    result = translate_to_vl(spec)
    transforms = result.get("transform", [])
    assert any(
        isinstance(t, dict) and t.get("as") == "__df_href__" for t in transforms
    ), f"transform with as='__df_href__' must be present; got {transforms!r}"
    href_enc = result.get("encoding", {}).get("href")
    assert href_enc is not None, "href encoding must be present"
    assert href_enc.get("field") == "__df_href__"


def test_href_link_not_set_produces_no_transform() -> None:
    """When spec.href_link is None, no href transform is injected."""
    result = translate_to_vl(_spec("bar", {"x": {"field": "month"}}))
    assert "transform" not in result or not any(
        isinstance(t, dict) and t.get("as") == "__df_href__"
        for t in result.get("transform", [])
    )


def test_href_link_disabled_on_a_dual_axis_zero_rule_nested_one_level_deep() -> None:
    """A rule sub-layer with its own private data must get `href: None` even
    when it sits nested one level inside a `nest_zero_rule` wrapper (a
    dual-axis base/layer's zero-baseline rule), not only at the top level of
    `spec.layers`.

    Without the recursive guard, the wrapper itself has no `data` key (so
    the single-level scan skips it) and the rule's own private single-row
    data is never reached -- the rule inherits `href` bound to
    `__df_href__`, which is undefined on its row, and Vega-Lite base-URL
    joins the undefined value into a broken link.
    """
    base_entry = ChartSpec(mark="bar", encoding={"x": {"field": "month"}})
    rule = ChartSpec(
        mark="rule",
        encoding={"y": {"datum": 0}},
        data=[{"y": 0}],
    )
    wrapped = ChartSpec(mark="layered", layers=[base_entry, rule])
    spec = ChartSpec(
        mark="layered",
        layers=[wrapped],
        href_link="'https://example.com/' + datum['month']",
    )
    result = translate_to_vl(spec)
    nested_rule = result["layer"][0]["layer"][1]
    assert nested_rule["data"] == {"values": [{"y": 0}]}
    assert nested_rule["encoding"]["href"] is None


# Endpoint labels in _translate_layered
# ---------------------------------------------------------------------------


def test_translate_layered_with_endpoint_labels_produces_hconcat() -> None:
    """_translate_layered must wrap with hconcat when spec.endpoint_label_layout is set."""
    label_data = EndpointLabelData(
        series_field="region",
        value_alias="__y__",
        positions=[("A", 10.0), ("B", 20.0)],
        color_domain=["A", "B"],
        color_range=["#1f77b4", "#ff7f0e"],
        label_offset=4,
        height=20.0,
    )
    spec = ChartSpec(
        mark="layered",
        encoding={"x": {"field": "month", "type": "ordinal"}},
        layers=[
            ChartSpec(
                mark="line",
                mark_props={"stroke": "#1f77b4"},
                encoding={"y": {"field": "revenue", "type": "quantitative"}},
            )
        ],
        endpoint_label_layout="right_pane",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    assert "hconcat" in result, f"expected hconcat wrapper; got keys: {list(result)}"
    assert len(result["hconcat"]) == 2


def test_translate_layered_without_endpoint_labels_has_no_hconcat() -> None:
    """Without endpoint_label_layout, _translate_layered emits plain layer[]."""
    spec = ChartSpec(
        mark="layered",
        encoding={"x": {"field": "month", "type": "ordinal"}},
        layers=[ChartSpec(mark="line", encoding={"y": {"field": "revenue"}})],
    )
    result = translate_to_vl(spec)
    assert "hconcat" not in result
    assert "layer" in result


# ---------------------------------------------------------------------------
# dark_companion_range: when set, label pane uses it instead of color_range
# ---------------------------------------------------------------------------


def _make_label_data_with_dark(layout: str) -> EndpointLabelData:
    alias = "__y" if layout == "right_pane" else "__x"
    return EndpointLabelData(
        series_field="category",
        value_alias=alias,
        positions=[("A", 10.0), ("B", 20.0)],
        color_domain=["A", "B"],
        color_range=["#3164a3", "#779bc9"],
        label_offset=6.0,
        height=20.0,
        dark_companion_range=["#0e4786", "#557daf"],
    )


def test_right_pane_uses_dark_companion_range_when_set() -> None:
    """When dark_companion_range is set, label pane color scale uses it (not color_range)."""
    label_data = _make_label_data_with_dark("right_pane")
    spec = ChartSpec(
        mark="line",
        encoding={},
        endpoint_label_layout="right_pane",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    pane = result["hconcat"][1]
    color_range = pane["encoding"]["color"]["scale"]["range"]
    assert color_range == [
        "#0e4786",
        "#557daf",
    ], f"expected dark companion colors; got {color_range!r}"


def test_right_pane_falls_back_to_color_range_when_no_dark_companion() -> None:
    """When dark_companion_range is empty, label pane color scale uses color_range."""
    label_data = EndpointLabelData(
        series_field="category",
        value_alias="__y",
        positions=[("A", 10.0), ("B", 20.0)],
        color_domain=["A", "B"],
        color_range=["#3164a3", "#779bc9"],
        label_offset=6.0,
        height=20.0,
        # dark_companion_range not set → empty list default
    )
    spec = ChartSpec(
        mark="line",
        encoding={},
        endpoint_label_layout="right_pane",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    pane = result["hconcat"][1]
    color_range = pane["encoding"]["color"]["scale"]["range"]
    assert color_range == ["#3164a3", "#779bc9"]


# ---------------------------------------------------------------------------
# Label pane width / font / height — prevents main-pane horizontal compression
# ---------------------------------------------------------------------------


def _make_label_data_with_width(
    width: float, font_props: dict[str, Any]
) -> EndpointLabelData:
    return EndpointLabelData(
        series_field="category",
        value_alias="__y",
        positions=[("Alpha", 10.0), ("Beta", 20.0)],
        color_domain=["Alpha", "Beta"],
        color_range=["#1f77b4", "#ff7f0e"],
        label_offset=6.0,
        height=20.0,
        label_pane_width=width,
        label_mark_font_props=font_props,
    )


def test_right_pane_emits_explicit_width_and_limit() -> None:
    """label_pane_width > 0 → pane gets width and mark gets limit, preventing overshoot."""
    label_data = _make_label_data_with_width(
        52.4, {"fontSize": 14.0, "font": "Inter Variable", "fontWeight": "400"}
    )
    spec = ChartSpec(
        mark="line",
        encoding={},
        endpoint_label_layout="right_pane",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    pane = result["hconcat"][1]
    assert pane.get("width") == pytest.approx(52.4), "pane must have explicit width"
    mark = pane["mark"]
    assert mark.get("limit") == pytest.approx(52.4), "mark must have limit = pane width"
    assert mark.get("fontSize") == 14.0
    assert mark.get("font") == "Inter Variable"
    assert mark.get("fontWeight") == "400"


def test_right_pane_no_width_when_label_pane_width_zero() -> None:
    """When label_pane_width=0 (default), no explicit width/limit on the pane."""
    label_data = EndpointLabelData(
        series_field="cat",
        value_alias="__y",
        positions=[("X", 1.0)],
        color_domain=["X"],
        color_range=["#aaa"],
        label_offset=6.0,
        height=20.0,
        # label_pane_width=0.0 (default)
    )
    spec = ChartSpec(
        mark="line",
        encoding={},
        endpoint_label_layout="right_pane",
        endpoint_label_data=label_data,
    )
    result = translate_to_vl(spec)
    pane = result["hconcat"][1]
    assert "width" not in pane, "no explicit width when label_pane_width is 0"
    assert pane["mark"].get("limit") is None


# ---------------------------------------------------------------------------
# assemble_final_vl contract
# ---------------------------------------------------------------------------


def test_assemble_final_vl_returns_vl_dict_with_schema_and_config() -> None:
    """assemble_final_vl is the single assembly point: translate + presentation merge."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.render.chart.translate import assemble_final_vl

    theme = get_theme_style()
    board_style = resolve_style(theme)
    chart_ctx = resolve_chart_style_context(theme)
    spec = ChartSpec(
        mark="bar",
        encoding={"x": {"field": "date"}},
        data=[{"date": "Jan"}],
        background=board_style.background,
        title_style=concrete_title_style(chart_ctx),
    )
    vl = assemble_final_vl(spec, board_style)
    assert "$schema" in vl
    assert vl["$schema"].startswith(_VL_SCHEMA_PREFIX)
    assert "config" in vl, "assemble_final_vl must merge board config into result"


def test_assemble_final_vl_background_and_autosize_top_level_only() -> None:
    """background/autosize must live at spec top level only, never duplicated
    under config — VL resolves the top-level value with precedence, so a
    config copy is inert and must not be emitted."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.render.chart.translate import assemble_final_vl

    theme = get_theme_style()
    board_style = resolve_style(theme)
    chart_ctx = resolve_chart_style_context(theme)
    spec = ChartSpec(
        mark="bar",
        encoding={"x": {"field": "date"}},
        data=[{"date": "Jan"}],
        background=board_style.background,
        title_style=concrete_title_style(chart_ctx),
    )
    vl = assemble_final_vl(spec, board_style)
    assert "background" in vl
    assert "autosize" in vl
    # resize:true has no config-level home now that config.autosize is gone —
    # VL/vl-convert does not fill it in from config for the top-level object,
    # so it must be present on the single surviving top-level literal (a
    # regression the CI visual gate caught when this was first dropped).
    assert vl["autosize"]["resize"] is True
    assert "background" not in vl["config"]
    assert "autosize" not in vl["config"]


def test_assemble_final_vl_non_vl_mark_raises() -> None:
    """assemble_final_vl rejects non-VL family marks (kpi, table)."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.translate import assemble_final_vl

    board_style = resolve_style(get_theme_style())
    spec = ChartSpec(mark="kpi", encoding={})
    with pytest.raises(ValueError, match="kpi"):
        assemble_final_vl(spec, board_style)


def test_assemble_final_vl_does_not_leak_title_font_across_charts() -> None:
    """board_style is one instance reused for every chart in a board render
    (BoardRenderSession.board_style). Stamping a chart's title_font onto
    vl["config"]["title"] must never mutate the shared board_style.vega_config
    that the next chart's assemble_final_vl call reads from."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.render.chart.translate import assemble_final_vl

    theme = get_theme_style()
    board_style = resolve_style(theme)
    chart_ctx = resolve_chart_style_context(theme)

    font_a = ResolvedFontStyle(
        family="Courier",
        color="#000000",
        size=12.0,
        weight="normal",
        style="normal",
        decoration="none",
        case="none",
        line_height=1.2,
        tabular_figures=False,
    )
    spec_a = ChartSpec(
        mark="bar",
        encoding={"x": {"field": "date"}},
        data=[{"date": "Jan"}],
        background=board_style.background,
        title_style=concrete_title_style(chart_ctx),
        title="Chart A",
        title_font=font_a,
    )
    vl_a = assemble_final_vl(spec_a, board_style)
    assert vl_a["config"]["title"]["font"] == "Courier", (
        "sanity check: chart A must actually stamp its own title_font"
    )

    spec_b = ChartSpec(
        mark="bar",
        encoding={"x": {"field": "date"}},
        data=[{"date": "Feb"}],
        background=board_style.background,
        title_style=concrete_title_style(chart_ctx),
        title="Chart B",
    )
    vl_b = assemble_final_vl(spec_b, board_style)

    title_font = vl_b.get("config", {}).get("title", {}).get("font")
    assert title_font != "Courier", (
        "chart A's title_font leaked into chart B via a shared board_style.vega_config "
        f"nested dict; got {title_font!r}"
    )


def test_translate_layer_inline_data_is_json_serializable() -> None:
    """Own-query layer rows carrying date/datetime values must be normalized at
    the VL embed boundary — raw temporal objects crash vl-convert with
    'Failed to parse vl_spec dict as JSON: unsupported type date'."""
    import datetime as dt
    import json

    layer = ChartSpec(
        mark="line",
        encoding={"x": {"field": "month"}, "y": {"field": "target"}},
        data=[
            {"month": dt.date(2026, 1, 1), "target": 25},
            {"month": dt.datetime(2026, 2, 1, 0, 0), "target": 25},
        ],
    )
    spec = ChartSpec(mark="layered", encoding={}, layers=[layer], config={})
    vl = translate_to_vl(spec)
    json.dumps(vl)
