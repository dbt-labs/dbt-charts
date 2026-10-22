"""Tests for the theme-level `dashes` palette → encoding-level `strokeDash` scale.

Note: Vega-Lite's documented `config.range.dashPattern` key is not honored by
vl_convert (silently falls back to defaults). The dash range therefore lives
on the per-chart `encoding.strokeDash.scale.range` instead of config.range.
The model fields (`ThemeChartsConfig.dashes`, `ChartsStyle.dashes`,
`ResolvedChartsStyle.dashes`) still carry the canonical theme value; the
render layer reads it and stamps it onto the encoding directly.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.vega_lite import (
    generate_vega_lite_spec,
    render_resolved_chart,
)

# Fixture used to exercise list-of-lists round-trip through the cascade — not
# the production palette. The shipped 5+1 palette is documented in the task
# worksheet; tests intentionally avoid pinning its exact values per the repo's
# "don't pin theme defaults in tests" convention (see dbt-charts/AGENTS.md).
SAMPLE_DASHES = [
    [4, 4],
    [8, 8],
    [4, 2, 1, 3],
    [2, 2],
    [8, 2, 1, 3],
    [],
]


class TestDashesCascade:
    """Dashes flow from authored StylePatch through compiled ChartsStyle
    into ResolvedChartsStyle. The render layer reads `merged_charts.dashes` and
    stamps it onto the `strokeDash` encoding."""

    def test_dashes_propagate_through_resolve_style(self) -> None:
        patch = StylePatch.model_validate({"charts": {"dashes": SAMPLE_DASHES}})
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        ctx = resolve_chart_style_context(get_theme_style(), patch)

        assert ctx.dashes == SAMPLE_DASHES

    def test_default_theme_has_no_dashes(self) -> None:
        """An empty palette is the resolved no-dash state."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )

        ctx = resolve_chart_style_context(get_theme_style())

        assert ctx.dashes == []


SERIES_DATA = [
    {"month": "Jan", "revenue": 100, "region": "north"},
    {"month": "Jan", "revenue": 80, "region": "south"},
    {"month": "Feb", "revenue": 120, "region": "north"},
    {"month": "Feb", "revenue": 90, "region": "south"},
]


def _board_with_dashes(dashes: list[list[int]]) -> tuple:
    """Build (ResolvedStyle, ChartStyleContext) with the given dashes palette.

    Resolves against `stark` with line/area endpoint labels seeded OFF and the
    legend seeded ON: these tests observe the merged color+dash legend, which
    endpoint labels would replace (they suppress the color legend) and which
    the `_base` endpoint-label default would hide. Seeding both flags
    explicitly keeps the test independent of either tunable.
    """
    from dbt_charts.core.compile.config import get_theme_style

    compiled = get_theme_style("stark")
    line = compiled.charts.line.model_copy(
        update={
            "endpoint_labels": compiled.charts.line.endpoint_labels.model_copy(
                update={"visible": False}
            )
        }
    )
    area = compiled.charts.area.model_copy(
        update={
            "endpoint_labels": compiled.charts.area.endpoint_labels.model_copy(
                update={"visible": False}
            )
        }
    )
    legend = compiled.charts.legend.model_copy(update={"visible": True})
    charts = compiled.charts.model_copy(
        update={"dashes": dashes, "line": line, "area": area, "legend": legend}
    )
    return resolve_style_and_context(compiled.model_copy(update={"charts": charts}))


class TestDashesRenderEmission:
    """Render layer emits a `strokeDash` encoding (Vega-Lite's encoding channel
    name) bound to the categorical color field when (a) chart is line-family,
    (b) theme has `dashes` set, and (c) chart has a categorical color/stroke
    field. Note: encoding channel is `strokeDash`; the range-config key for
    the dash palette is `dashPattern` (Vega-Lite's spelling)."""

    def test_line_chart_with_color_and_dashes_emits_strokeDash_encoding(
        self, make_chart
    ) -> None:
        board_rs, board_ctx = _board_with_dashes(SAMPLE_DASHES)
        chart = make_chart("line", x="month", y="revenue", color="region")

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        encoding = spec.get("encoding", {})
        assert "strokeDash" in encoding
        assert encoding["strokeDash"]["field"] == "region"
        assert encoding["strokeDash"]["type"] == "nominal"
        # Range lives on the encoding scale, not config.range, because
        # vl_convert ignores `config.range.dashPattern`.
        assert encoding["strokeDash"]["scale"]["range"] == SAMPLE_DASHES

    def test_color_legend_configured_for_line_samples(self, make_chart) -> None:
        """Color legend flips to line-sample symbols so the merged color+dash
        legend shows actual dash patterns, not dashed circle outlines."""
        board_rs, board_ctx = _board_with_dashes(SAMPLE_DASHES)
        chart = make_chart("line", x="month", y="revenue", color="region")

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        encoding = spec.get("encoding", {})
        color_legend = encoding["color"]["legend"]
        assert color_legend["symbolType"] == "stroke"

    def test_strokeDash_inherits_color_title_for_merged_legend(
        self, make_chart
    ) -> None:
        """strokeDash gets the same title as color so Vega-Lite merges legends."""
        board_rs, board_ctx = _board_with_dashes(SAMPLE_DASHES)
        chart = make_chart("line", x="month", y="revenue", color="region")

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        encoding = spec.get("encoding", {})
        color_title = encoding["color"].get("title")
        # Guard against the silent-None case — both encodings missing a title
        # would satisfy equality but fail to merge legends in Vega-Lite.
        assert color_title is not None
        assert encoding["strokeDash"].get("title") == color_title

    def test_line_chart_redundant_color_plus_dash_encoding(self, make_chart) -> None:
        """Color and strokeDash bind to the same field — redundant encoding default."""
        board_rs, board_ctx = _board_with_dashes(SAMPLE_DASHES)
        chart = make_chart("line", x="month", y="revenue", color="region")

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        encoding = spec.get("encoding", {})
        assert encoding["color"]["field"] == "region"
        assert encoding["strokeDash"]["field"] == "region"

    def test_line_chart_without_dashes_omits_strokeDash_encoding(
        self, make_chart
    ) -> None:
        board_rs, board_ctx = _board_with_dashes([])
        chart = make_chart("line", x="month", y="revenue", color="region")

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        assert "strokeDash" not in spec.get("encoding", {})

    def test_bar_chart_with_color_and_dashes_omits_strokeDash_encoding(
        self, make_chart
    ) -> None:
        """Line-family gate: bar charts get no strokeDash even with dashes set."""
        board_rs, board_ctx = _board_with_dashes(SAMPLE_DASHES)
        chart = make_chart("bar", x="month", y="revenue", color="region")

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        assert "strokeDash" not in spec.get("encoding", {})

    def test_dash_encoding_propagates_legend_none_instead_of_crashing(
        self, make_chart
    ) -> None:
        """When the color legend is theme-disabled, strokeDash emission must not crash.

        Regression: the shipped `editorial` theme sets `legend.disable: true`,
        which produces `encoding.color.legend = None`. The dash-symbol merge
        previously did ``{**color_enc.get("legend", {}), ...}`` — which crashes
        with ``TypeError: 'NoneType' object is not a mapping`` when the key exists
        with a None value. The fix propagates the suppression so the dash channel
        inherits the same legend-None state as color.

        Verified via the editorial theme (legend.disable = true) + a dashes board.
        """
        from dbt_charts.core.compile.config import get_theme_style

        # Build a board with dashes on the editorial theme (legend.disable=true).
        editorial = get_theme_style("clarity")
        charts = editorial.charts.model_copy(update={"dashes": SAMPLE_DASHES})
        board_rs, board_ctx = resolve_style_and_context(
            editorial.model_copy(update={"charts": charts})
        )
        chart = make_chart("line", x="month", y="revenue", color="region")

        # Must not raise.
        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        # Editorial theme wraps in hconcat when legend is disabled; navigate to the
        # chart pane that carries the encoding channels.
        encoding = spec.get("encoding") or (
            spec["hconcat"][0].get("encoding", {}) if "hconcat" in spec else {}
        )
        assert "strokeDash" in encoding, (
            "strokeDash must still be emitted even with legend disabled"
        )
        # When the color legend is suppressed, strokeDash.legend must also be None
        # so Vega-Lite does not resurrect a legend the user opted out of.
        assert encoding["strokeDash"].get("legend") is None, (
            "strokeDash.legend must inherit the color-legend suppression (None)"
        )


class TestFixLegendSymbolLinecap:
    """The SVG post-processor stamps stroke-linecap=round onto legend-symbol
    paths. Tests pin the regex against representative vl_convert outputs so a
    future change in vl_convert's class string doesn't silently break the
    merged-legend rendering of dotted entries.
    """

    def test_stamps_linecap_on_path_with_no_existing_linecap(self) -> None:
        from dbt_charts.core.render.converters.chart import _fix_legend_symbol_linecap

        svg = (
            '<g class="mark-symbol role-legend-symbol series_legend_symbols">'
            '<path d="M-22,0L22,0" stroke="#1f77b4" stroke-width="2" '
            'stroke-dasharray="12,16"/>'
        )

        result = _fix_legend_symbol_linecap(svg)

        assert 'stroke-linecap="round"' in result

    def test_skips_paths_that_already_have_a_linecap(self) -> None:
        from dbt_charts.core.render.converters.chart import _fix_legend_symbol_linecap

        svg = (
            '<g class="mark-symbol role-legend-symbol series_legend_symbols">'
            '<path d="M-22,0L22,0" stroke="#1f77b4" stroke-linecap="butt"/>'
        )

        result = _fix_legend_symbol_linecap(svg)

        # Skipped — the original linecap is preserved untouched, no duplicate
        # stroke-linecap attribute injected.
        assert result.count("stroke-linecap") == 1
        assert 'stroke-linecap="butt"' in result

    def test_does_not_touch_non_legend_paths(self) -> None:
        from dbt_charts.core.render.converters.chart import _fix_legend_symbol_linecap

        svg = '<g class="mark-line"><path d="M0,0L100,100" stroke="#1f77b4"/>'

        result = _fix_legend_symbol_linecap(svg)

        assert "stroke-linecap" not in result


class TestSpecHasEncoding:
    """``_spec_has_encoding`` gates SVG post-processors so charts that don't
    use a channel keep producing byte-identical SVG.  Walks top-level encoding,
    layer panes, hconcat panes, and vconcat panes.
    """

    def test_detects_channel_on_top_level_encoding(self) -> None:
        from dbt_charts.core.render.converters.chart import _spec_has_encoding

        spec = {"encoding": {"strokeDash": {"field": "s", "type": "nominal"}}}

        assert _spec_has_encoding(spec, ["strokeDash"]) is True

    def test_detects_channel_inside_layer_encoding(self) -> None:
        from dbt_charts.core.render.converters.chart import _spec_has_encoding

        spec = {
            "layer": [
                {"mark": "line"},
                {
                    "mark": "line",
                    "encoding": {"strokeDash": {"field": "s", "type": "nominal"}},
                },
            ]
        }

        assert _spec_has_encoding(spec, ["strokeDash"]) is True

    def test_returns_false_when_spec_has_none_of_the_channels(self) -> None:
        from dbt_charts.core.render.converters.chart import _spec_has_encoding

        spec = {
            "encoding": {"x": {}, "y": {}, "color": {}},
            "layer": [{"encoding": {"opacity": {}}}],
        }

        assert _spec_has_encoding(spec, ["strokeDash"]) is False

    def test_detects_channel_inside_hconcat_pane_encoding(self) -> None:
        # hconcat endpoint-label wrapper: dashed line chart in pane[0],
        # label pane in pane[1]. The strokeDash lives on pane[0]'s encoding.
        from dbt_charts.core.render.converters.chart import _spec_has_encoding

        spec = {
            "hconcat": [
                {
                    "encoding": {
                        "strokeDash": {"field": "s", "type": "nominal"},
                        "x": {},
                    }
                },
                {"encoding": {"text": {}}},
            ]
        }

        assert _spec_has_encoding(spec, ["strokeDash"]) is True

    def test_detects_channel_inside_vconcat_pane_encoding(self) -> None:
        from dbt_charts.core.render.converters.chart import _spec_has_encoding

        spec = {
            "vconcat": [
                {"encoding": {"strokeDash": {"field": "s", "type": "nominal"}}},
                {"encoding": {"text": {}}},
            ]
        }

        assert _spec_has_encoding(spec, ["strokeDash"]) is True

    def test_matches_any_of_multiple_channels(self) -> None:
        from dbt_charts.core.render.converters.chart import _spec_has_encoding

        spec = {"encoding": {"patternFill": {"field": "s", "type": "nominal"}}}

        assert _spec_has_encoding(spec, ["strokeDash", "patternFill"]) is True

    def test_returns_false_when_none_of_multiple_channels_present(self) -> None:
        from dbt_charts.core.render.converters.chart import _spec_has_encoding

        spec = {"encoding": {"color": {"field": "s", "type": "nominal"}}}

        assert _spec_has_encoding(spec, ["strokeDash", "patternFill"]) is False


class TestDashesSubLayerIsolation:
    """Dashes active on a line chart must appear in the top-level encoding but
    must NOT leak into hover-overlay sub-layers (which use point marks that
    don't render strokeDash).  Tests pin the observable spec shape.
    """

    def test_dashes_active_line_emits_strokeDash_in_top_encoding(
        self, make_chart
    ) -> None:
        board_rs, board_ctx = _board_with_dashes(SAMPLE_DASHES)
        chart = make_chart("line", x="month", y="revenue", color="region")

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        assert "strokeDash" in spec.get("encoding", {}), (
            "strokeDash must appear in top-level encoding when dashes are active"
        )

    def test_bar_chart_with_dashes_emits_no_strokeDash(self, make_chart) -> None:
        board_rs, board_ctx = _board_with_dashes(SAMPLE_DASHES)
        chart = make_chart("bar", x="month", y="revenue", color="region")

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        assert "strokeDash" not in spec.get("encoding", {}), (
            "bar charts must never emit strokeDash even when dashes are set on the board"
        )

    def test_line_chart_without_dashes_emits_no_strokeDash(self, make_chart) -> None:
        board_rs, board_ctx = _board_with_dashes([])
        chart = make_chart("line", x="month", y="revenue", color="region")

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        assert "strokeDash" not in spec.get("encoding", {}), (
            "no strokeDash in spec when board has no dashes palette"
        )

    def test_hover_point_sub_layers_do_not_carry_strokeDash(self, make_chart) -> None:
        """Hover point overlay sub-layers inside a line spec must not have
        strokeDash in their per-layer encoding — point marks don't render
        strokeDash, and propagating it would produce spurious Vega-Lite warnings.
        """
        board_rs, board_ctx = _board_with_dashes(SAMPLE_DASHES)
        chart = make_chart("line", x="month", y="revenue", color="region")

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        # Sub-layers must not carry strokeDash in their own encoding.
        for sub in spec.get("layer", []):
            sub_enc = sub.get("encoding", {})
            assert "strokeDash" not in sub_enc, (
                f"sub-layer must not carry strokeDash; layer mark={sub.get('mark')!r}"
            )


class TestRenderVegaSpecLinecapOnHconcatWrappedDashedChart:
    """End-to-end regression: a dashed multi-series line chart with
    ``endpoint_labels.visible=true`` produces an hconcat-wrapped spec whose
    ``strokeDash`` lives inside ``hconcat[0]``. The old gate
    (``_spec_has_stroke_dash``) walked only top-level encoding + layer panes
    and missed this case, so ``_fix_legend_symbol_linecap`` silently skipped
    the merged legend. This test pins the new walk firing end-to-end.
    """

    def test_legend_linecap_fix_fires_on_hconcat_wrapped_dashed_chart(
        self, make_chart
    ) -> None:
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.render.converters.chart import (
            _spec_has_encoding,
            render_vega_spec,
        )

        # Build a multi-series line chart with both dashes and endpoint labels on.
        base = get_theme_style()
        seed = base.model_copy(
            update={
                "charts": base.charts.model_copy(
                    update={
                        "dashes": SAMPLE_DASHES,
                        "line": base.charts.line.model_copy(
                            update={
                                "endpoint_labels": EndpointLabelsConfig(
                                    visible=True, label_offset=6.0, height=20.0
                                )
                            }
                        ),
                    }
                )
            }
        )
        board_rs, board_ctx = resolve_style_and_context(seed)
        chart = make_chart("line", x="month", y="revenue", color="region")
        resolved = resolve(chart, SERIES_DATA, chart_style_context=board_ctx)
        spec = render_resolved_chart(resolved, SERIES_DATA, board_rs, width=400).payload

        # The wrapped shape is what makes this test meaningful: if the pipeline
        # ever stops emitting hconcat here, the test would be silently testing
        # the wrong shape and must be re-anchored.
        assert "hconcat" in spec, (
            "Pipeline must wrap a dashed multi-series line chart with "
            "endpoint_labels.visible=true in an hconcat; if this changes, "
            "the gate-walk regression this test pins is testing the wrong shape."
        )
        # Sanity check: strokeDash is nested, not at top level. The old gate
        # would have returned False here; the new gate must return True.
        assert "strokeDash" not in spec.get("encoding", {})
        assert _spec_has_encoding(spec, ["strokeDash"]) is True

        # Mock vl_convert so the test doesn't require the binary and runs fast.
        # The mock returns a representative legend-symbol path; render_vega_spec
        # should stamp stroke-linecap="round" because the gate fires.
        fake_svg = (
            '<svg><g class="mark-symbol role-legend-symbol series_legend_symbols">'
            '<path d="M-22,0L22,0" stroke="#1f77b4" stroke-width="2" '
            'stroke-dasharray="12,16"/></g></svg>'
        )
        # The label pane's endpoint-label rail re-cascades post-probe
        # (recascade_endpoint_labels), so the mocked scenegraph must carry a
        # real-enough label-marks group for it to measure a slope from — an
        # unconfigured MagicMock's default __iter__ yields no leaves, which
        # this test doesn't care about but recascade needs to not raise.
        label_rows = spec["hconcat"][1]["data"]["values"]
        series_field = spec["$df_endpoint_label_cascade"]["series_field"]
        value_alias = spec["$df_endpoint_label_cascade"]["value_alias"]
        fake_scenegraph = {
            "width": 400.0,
            "height": 300.0,
            "origin": [0, 0],
            "scenegraph": {
                "items": [
                    {
                        "items": [
                            None,
                            {
                                "items": [
                                    {
                                        "items": [
                                            {
                                                "items": [
                                                    {
                                                        "text": row[series_field],
                                                        "y": 100.0 - row[value_alias],
                                                    }
                                                    for row in label_rows
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            },
                        ]
                    }
                ]
            },
        }
        fake_vlc = MagicMock()
        fake_vlc.vegalite_to_svg.return_value = fake_svg
        fake_vlc.vegalite_to_scenegraph.return_value = fake_scenegraph
        with patch.dict("sys.modules", {"vl_convert": fake_vlc}):
            result = render_vega_spec(
                spec,
                format="svg",
                resolved_style=board_rs,
                width=400,
                height=300,
                is_placeholder=False,
                chart_id="chart",
            )

        assert 'stroke-linecap="round"' in result, (
            "Linecap fix must fire on hconcat-wrapped dashed charts. Pre-fix "
            "_spec_has_stroke_dash returned False on this shape, skipping the "
            "post-processor and rendering butt-cap dashes in the legend."
        )


class TestDashesLegendValuesResolution:
    """The `style.dashes` branch (emitters/line.py) is a separate legend-
    pinning site from the plain (non-dashed) color path above -- it needs
    its own coverage of `apply_legend_entry_order`, gated (like every
    other family's own dedicated resolution call) on `chart.legend.values
    is not None` AND a nominal/ordinal color type: an unconditional pin
    would str()-cast a bool/int/date color field's `legend.values` while
    `color_scale.domain`/`strokeDash.scale.domain` kept the raw values --
    a type-mismatched legend that vl_convert renders as all-NaN, on a
    board that authored nothing."""

    def test_dashes_writes_no_legend_values_when_nothing_is_authored(
        self, make_chart
    ) -> None:
        board_rs, board_ctx = _board_with_dashes(SAMPLE_DASHES)
        chart = make_chart("line", x="month", y="revenue", color="region")

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        color_legend = spec["encoding"]["color"]["legend"]
        assert "values" not in color_legend

    def test_dashes_resolves_an_authored_entry_against_the_real_domain(
        self, make_chart
    ) -> None:
        board_rs, board_ctx = _board_with_dashes(SAMPLE_DASHES)
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            color="region",
            style={"legend": {"values": ["SOUTH", "NORTH"]}},
        )

        spec = generate_vega_lite_spec(
            chart, data=SERIES_DATA, board_style=board_rs, chart_style_context=board_ctx
        )

        color_legend = spec["encoding"]["color"]["legend"]
        assert color_legend["values"] == ["south", "north"]

    def test_dashes_does_not_pin_legend_values_on_a_boolean_color_field(
        self, make_chart
    ) -> None:
        """Regression: a boolean/int/date `color:` field infers to
        "quantitative"/"temporal", never "nominal"/"ordinal" -- the type
        gate must exclude it the same way pie/heatmap/scatter/geo/bar's
        own dedicated calls do, or `legend.values` gets a str()-cast
        `["True", "False"]` list while `color_scale.domain` and
        `strokeDash.scale.domain` keep the raw booleans, a type mismatch
        vl_convert renders as an all-NaN legend.

        Authors a `legend.values` entry -- the authored-gate alone already
        short-circuits an unauthored board before the type check ever
        runs, so this must author something to actually exercise the type
        clause; deleting it would otherwise leave this test green. A
        quantitative color's `legend.values` is a gradient
        tick ladder, not a categorical entry list (same convention as
        pie.py's own quantitative-color comment) -- resolution must skip
        it, leaving `apply_color_legend`'s verbatim copy untouched, not
        fold-match/drop it against the boolean domain as if it were
        categorical."""
        board_rs, board_ctx = _board_with_dashes(SAMPLE_DASHES)
        chart = make_chart(
            "line",
            x="month",
            y="revenue",
            color="is_target",
            # A lowercase fold-match of "True" plus a bogus entry: if the
            # type gate were removed, resolution would fold-match "true"
            # to "True" and drop "bogus" (WARN), producing a DIFFERENT
            # list than the verbatim authored one -- a genuinely
            # discriminating case, not one that coincidentally resolves
            # to the same output either way.
            style={"legend": {"values": ["true", "bogus"]}},
        )
        data = [
            {"month": "Jan", "revenue": 100, "is_target": True},
            {"month": "Jan", "revenue": 80, "is_target": False},
            {"month": "Feb", "revenue": 120, "is_target": True},
            {"month": "Feb", "revenue": 90, "is_target": False},
        ]

        spec = generate_vega_lite_spec(
            chart, data=data, board_style=board_rs, chart_style_context=board_ctx
        )

        color_legend = spec["encoding"]["color"]["legend"]
        assert color_legend["values"] == ["true", "bogus"]
