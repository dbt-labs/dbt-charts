"""TDD tests for line endpoint rich label pane (D-053).

Tests are written BEFORE implementation. They should FAIL until the feature lands.

Feature: when `style.line.endpoint_labels.visible: true` is authored on a
multi-series line chart, `render_standard_vega_spec` emits an hconcat spec
with the main chart as pane[0] and a label pane as pane[1].

Label format: `{value_formatted} {series_name}`.
Dark companion palette for label text.
`resolve.scale.color: independent` on the outer hconcat.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.emitters._cartesian import (
    last_nonnull_value_per_series,
)
from dbt_charts.core.render.chart.features.endpoint_labels import _apply_label_cascade
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart


def _resolve_endpoint_label_positions(
    data, x_field, y_field, series_field, min_data_gap, y_domain_min, y_domain_max
):
    """Test helper mirroring the deleted production wrapper of the same name.

    Production now defers this cascade until the real plot geometry is known
    (``features/endpoint_labels.recascade_endpoint_labels``) — see
    ``test_endpoint_label_gap.py``. These pre-existing tests exercise
    ``_apply_label_cascade``'s downward/upward-flip behavior over real
    last-per-series extraction, which is still exact production logic; only
    the pixel<->data conversion moved.
    """
    last_per_series = last_nonnull_value_per_series(
        data, x_field, y_field, series_field
    )
    positions, _dropped = _apply_label_cascade(
        last_per_series,
        min_data_gap=min_data_gap,
        y_domain_min=y_domain_min,
        y_domain_max=y_domain_max,
    )
    return positions


_BOARD_STYLE = resolve_style(get_theme_style())
_BOARD_CONTEXT = resolve_chart_style_context(get_theme_style())


def _extract_y_domain(spec: dict, data: list[dict], field: str) -> tuple[float, float]:
    """Read y domain bounds from a VL spec dict; fall back to data range."""
    scale = spec.get("encoding", {}).get("y", {}).get("scale", {})
    if scale.get("domainMin") is not None or scale.get("domainMax") is not None:
        return float(scale["domainMin"]), float(scale["domainMax"])
    if "domain" in scale:
        dom = scale["domain"]
        return float(min(dom)), float(max(dom))
    vals = [float(r[field]) for r in data if field in r]
    return float(min(vals)), float(max(vals))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _line_chart(make_chart, **kwargs):
    return make_chart("line", x="date", y="value", **kwargs)


def _multi_series_data():
    return [
        {"date": "2024-01-01", "value": 100, "series": "Core"},
        {"date": "2024-02-01", "value": 120, "series": "Core"},
        {"date": "2024-03-01", "value": 140, "series": "Core"},
        {"date": "2024-01-01", "value": 200, "series": "Growth"},
        {"date": "2024-02-01", "value": 220, "series": "Growth"},
        {"date": "2024-03-01", "value": 180, "series": "Growth"},
    ]


def _single_series_data():
    return [
        {"date": "2024-01-01", "value": 100},
        {"date": "2024-02-01", "value": 120},
        {"date": "2024-03-01", "value": 140},
    ]


# ---------------------------------------------------------------------------
# 1. hconcat emitted for multi-series line with endpoint_labels.visible=true
# ---------------------------------------------------------------------------


class TestEndpointLabelsHconcatEmitted:
    def test_hconcat_spec_structure(self, make_chart, model_copy_at):
        """enabled + multi-series → hconcat with 2 panes, resolve.scale.color=independent."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        chart = _line_chart(make_chart, color="series")
        data = _multi_series_data()

        seed = model_copy_at(
            get_theme_style(),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)

        resolved_chart = resolve(chart, data, chart_style_context=board_style)
        artifact = render_resolved_chart(resolved_chart, data, _BOARD_STYLE)
        assert artifact.kind == "vega_spec"
        spec = artifact.payload

        assert "hconcat" in spec, "Expected hconcat top-level key"
        assert len(spec["hconcat"]) == 2, "Expected 2 panes"
        assert spec.get("resolve", {}).get("scale", {}).get("color") == "independent"

    def test_spacing_set(self, make_chart, model_copy_at):
        """hconcat spacing between panes is set."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        chart = _line_chart(make_chart, color="series")
        data = _multi_series_data()

        seed = model_copy_at(
            get_theme_style(),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)
        resolved_chart = resolve(chart, data, chart_style_context=board_style)
        artifact = render_resolved_chart(resolved_chart, data, _BOARD_STYLE)
        assert artifact.kind == "vega_spec"
        spec = artifact.payload

        assert "spacing" in spec

    def test_top_level_keys_hoisted_to_hconcat_root(self, make_chart, model_copy_at):
        """$schema/config/background/data must live on the hconcat root.

        vl-convert treats $schema/config/background/data as top-level-only.
        When they sit inside a concat child, theme config is silently dropped
        (default vega palette + tick chrome) and pane[1] cannot resolve the
        dataset (renders as an empty <g>). This test locks in the canonical
        hconcat shape.

        title intentionally stays on pane[0] so vega-lite anchors it over the
        chart body's visual bounds. Hoisting title to the wrapper centers it
        over the full hconcat width (chart + label pane), shifting the title
        ~20px rightward for wide label panes.
        """
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        chart = _line_chart(make_chart, color="series")
        data = _multi_series_data()

        seed = model_copy_at(
            get_theme_style(),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)
        resolved_chart = resolve(chart, data, chart_style_context=board_style)
        artifact = render_resolved_chart(resolved_chart, data, _BOARD_STYLE)
        assert artifact.kind == "vega_spec"
        spec = artifact.payload

        pane0 = spec["hconcat"][0]

        # Required: data must reach the wrapper so pane[1] can resolve it.
        assert "data" in spec, "Expected dataset on hconcat root"
        values = spec["data"].get("values")
        assert isinstance(values, list) and len(values) == len(data)

        # vl-convert silently drops $schema/config/background/data when they
        # sit inside a concat child: theme config falls back to vega defaults
        # and pane[1] renders empty. These must live on the root.
        for key in ("$schema", "config", "data", "background"):
            assert key not in pane0, (
                f"{key!r} must not stay on pane[0]; vl-convert ignores it "
                "there and the wrapper loses theme/data"
            )
        assert "config" in spec, "Expected theme config on hconcat root"

        # title, autosize, and padding intentionally stay on pane[0]:
        # vega-lite centers the title over the view's own visual bounds —
        # hoisting it to the wrapper would center it over the full hconcat
        # width (chart + label pane), shifting it rightward for wide label
        # panes.
        assert "padding" in pane0, "Expected pane[0] to keep its padding"
        # title must NOT be on the wrapper — it would mis-center over the
        # label pane. It must stay on pane[0] to align with the chart body.
        assert "title" not in spec, (
            "title must not be hoisted to hconcat root; it mis-centers over "
            "the label pane. Keep it on pane[0]."
        )

    def test_label_pane_renders_visible_text_marks(self, make_chart, model_copy_at):
        """End-to-end: vl-convert must produce a visible text mark per series.

        Locks in the regression where the label pane was syntactically present
        but rendered an empty <g> because data was not propagated to pane[1].
        """
        import re

        import vl_convert as vlc

        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        chart = _line_chart(make_chart, color="series")
        data = _multi_series_data()
        # Seed from `stark` so the vivid-10 palette pin below matches —
        # the shipped editorial `default` uses editorial-10 and would emit
        # different stops, defeating the "theme config reaches the lines"
        # regression check.

        seed = model_copy_at(
            get_theme_style("stark"),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)
        resolved_chart = resolve(chart, data, chart_style_context=board_style)
        artifact = render_resolved_chart(resolved_chart, data, _BOARD_STYLE)
        assert artifact.kind == "vega_spec"
        spec = artifact.payload

        svg = vlc.vegalite_to_svg(spec)
        match = re.search(r"concat_1_marks[^>]*>(.*?)(?:</g>|/>)", svg, re.DOTALL)
        assert match is not None, "Expected concat_1 (label pane) marks group"
        label_block = match.group(1)
        # Each series name must appear as visible text in the label pane.
        for series in {row["series"] for row in data}:
            assert series in label_block, (
                f"Expected series name {series!r} as visible text in label "
                f"pane; got: {label_block[:200]!r}"
            )
        # And: no numeric data values should leak into the label text. Text
        # encodes the series name only — values live on the y-axis.
        unique_values = {str(int(row["value"])) for row in data}
        for v in unique_values:
            text_marks = re.findall(r">([^<]+)</text>", label_block)
            assert all(v not in t for t in text_marks), (
                f"Numeric value {v!r} appeared in label pane text marks "
                f"{text_marks!r}; expected series name only"
            )

        # The cream-default category palette must reach the line strokes.
        # Catches the regression where config sat inside pane[0], vl-convert
        # silently ignored it, and the line fell back to vega's default
        # `#4c78a8` / tick chrome appeared in the wrong color.
        for themed_color in ("#0073c2", "#00c8ee"):
            assert themed_color.lower() in svg.lower(), (
                f"Expected theme palette color {themed_color!r} in rendered "
                "SVG; if the default vega palette appears instead the "
                "wrapper is dropping `config`."
            )
        assert "#4c78a8" not in svg, (
            "Default vega palette color leaked into rendered SVG — "
            "theme config is not being applied to the hconcat wrapper"
        )

    def test_pane0_shrunk_to_fit_label_pane_within_original_width(
        self, make_chart, model_copy_at
    ):
        """$df_target_width is set; render_vega_spec shrinks pane[0] via two-pass render.

        autosize:fit is a no-op on hconcat in vl-convert, so the total SVG width
        overflows the allocated cell. The fix: _maybe_wrap_endpoint_label_pane
        stamps ``$df_target_width`` on the wrapper and leaves pane[0].width
        untouched; render_vega_spec does a first render to measure actual SVG
        width, computes the overshoot, and subtracts it from pane[0].width before
        the real render. This test verifies the spec-build contract only.
        """
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        chart = _line_chart(make_chart, color="series")
        data = _multi_series_data()

        seed = model_copy_at(
            get_theme_style(),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)
        resolved_chart = resolve(chart, data, chart_style_context=board_style)
        for requested in (480.0, 600.0, 720.0):
            artifact = render_resolved_chart(
                resolved_chart, data, _BOARD_STYLE, width=requested
            )
            assert artifact.kind == "vega_spec"
            spec = artifact.payload

            assert spec.get("$df_target_width") == requested, (
                f"Expected $df_target_width={requested} on the hconcat wrapper "
                "(spec-build stamps target; render_vega_spec applies correction)"
            )
            # pane[0].width is the original requested width — not yet shrunk.
            # render_vega_spec applies the shrinkage after measuring actual SVG width.
            pane0_width = spec["hconcat"][0].get("width")
            assert pane0_width == requested, (
                f"pane[0].width should be the original {requested}; "
                "two-pass correction happens in render_vega_spec, not spec-build"
            )

    def test_title_aligned_at_card_padding_after_render(
        self, make_chart, model_copy_at
    ):
        """Rendered SVG title x must equal the spec's padding_left, not drift left.

        hconcat charts have a left y-axis that pushes the plot area right, and
        the title anchors to the leftmost y-axis label rather than to the card
        padding. ``padding`` is root-only in Vega-Lite — on a concat it is
        dropped from a child pane — so it belongs on the wrapper, and
        render_vega_spec reads it from the one node that carries it.
        """
        import re

        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
        from dbt_charts.core.render.converters.chart import render_vega_spec

        # Title required so the role-title group appears in the rendered SVG.
        chart = _line_chart(make_chart, color="series", title="Revenue")
        data = _multi_series_data()

        seed = model_copy_at(
            get_theme_style(),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)
        resolved_chart = resolve(chart, data, chart_style_context=board_style)
        card_padding_val = 16.0
        artifact = render_resolved_chart(
            resolved_chart,
            data,
            _BOARD_STYLE,
            width=576.0,
            padding={
                "left": card_padding_val,
                "right": card_padding_val,
                "top": 16,
                "bottom": 16,
            },
        )
        assert artifact.kind == "vega_spec"
        spec = artifact.payload
        root_pad = spec.get("padding") or {}
        card_padding = (
            float(root_pad.get("left", 0))
            if isinstance(root_pad, dict)
            else float(root_pad)
        )
        assert card_padding == card_padding_val, (
            "padding must sit on the hconcat root — Vega-Lite reads it nowhere else, "
            "and a child pane's copy is dropped"
        )
        # render_vega_spec does two-pass correction AND calls fix_title_alignment.
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        svg = render_vega_spec(
            spec,
            "svg",
            resolve_style(get_theme_style()),
            width=576.0,
            height=None,
            is_placeholder=False,
            chart_id="chart",
        )

        outer_m = re.search(
            r'stroke-miterlimit="10" transform="translate\((-?[0-9.]+),', svg
        )
        title_m = re.search(
            r'class="mark-group role-title"><g transform="translate\((-?[0-9.]+),',
            svg,
        )
        assert outer_m and title_m, (
            "Could not find outer or title group in rendered SVG"
        )
        outer_tx = float(outer_m.group(1))
        title_local = float(title_m.group(1))
        title_svg_x = outer_tx + title_local
        assert abs(title_svg_x - card_padding) < 1.0, (
            f"Title at x={title_svg_x:.1f} but card_padding={card_padding}; "
            "render_vega_spec must call fix_title_alignment with pane[0].padding"
        )

    def test_hconcat_title_limit_shrinks_after_pane_correction(
        self, make_chart, model_copy_at
    ):
        """Title wrap limit must reflect pane[0]'s post-correction width.

        vl-convert ignores autosize:fit on hconcat children, so at spec-build
        time pane[0].width is still the full column width (the
        label pane hasn't been reserved from it yet) — wrapping the title
        against that width overstates how much room the title actually has,
        which is why a wide subtitle can floor the plot in the original bug
        (ai_notes/charts/chart-chrome-vs-plot-dimensions-2026-07-20.md, case B#1).
        render_vega_spec must defer the wrap until after _correct_concat_overshoot
        has shrunk pane[0] to its real, chrome-aware width.
        """
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )
        from dbt_charts.core.render.converters.chart import render_vega_spec

        chart = _line_chart(make_chart, color="series", title="Revenue by region")
        data = _multi_series_data()

        seed = model_copy_at(
            get_theme_style(),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)
        resolved_chart = resolve(chart, data, chart_style_context=board_style)
        requested_width = 576.0
        artifact = render_resolved_chart(
            resolved_chart,
            data,
            _BOARD_STYLE,
            width=requested_width,
            padding={"left": 16.0, "right": 16.0, "top": 16, "bottom": 16},
        )
        assert artifact.kind == "vega_spec"
        spec = artifact.payload
        title_block = spec["hconcat"][0]["title"]
        assert "limit" not in title_block, (
            "spec-build must not wrap the hconcat title yet — pane[0].width is "
            "still the pre-correction full column width at this stage"
        )

        render_vega_spec(
            spec,
            "svg",
            resolve_style(get_theme_style()),
            width=requested_width,
            height=None,
            is_placeholder=False,
            chart_id="chart",
        )

        full_width_limit = int(requested_width - 16.0 - 16.0)
        corrected_limit = spec["hconcat"][0]["title"]["limit"]
        assert corrected_limit is not None
        assert corrected_limit < full_width_limit, (
            f"title.limit={corrected_limit} must be narrower than the "
            f"full-column limit {full_width_limit} once the label pane is "
            "reserved from pane[0]"
        )


# ---------------------------------------------------------------------------
# 2. Label pane structure
# ---------------------------------------------------------------------------


@pytest.fixture
def label_pane(make_chart, model_copy_at):
    """The right-hand label pane of a multi-series line spec with endpoint_labels on."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    chart = _line_chart(make_chart, color="series")
    data = _multi_series_data()

    seed = model_copy_at(
        get_theme_style(),
        "charts.line.endpoint_labels",
        EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
    )
    board_style = resolve_chart_style_context(seed)
    resolved_chart = resolve(chart, data, chart_style_context=board_style)
    artifact = render_resolved_chart(resolved_chart, data, _BOARD_STYLE)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload

    return spec["hconcat"][1]


class TestEndpointLabelsPaneStructure:
    def test_label_pane_has_inline_data_one_row_per_series(self, label_pane):
        """Label pane carries its own pre-computed dataset.

        The pane previously filtered the shared dataset down to last-x rows
        via joinaggregate + filter transforms. We now compute label
        positions in Python (so we can apply collision-avoidance nudging)
        and emit one row per series with the resolved `__y`.
        """
        pane = label_pane
        values = pane.get("data", {}).get("values")
        assert isinstance(values, list), "Expected inline data on label pane"
        # _multi_series_data has 2 series → 2 rows
        assert len(values) == 2
        for row in values:
            assert "series" in row
            assert "__y" in row, f"Expected pre-computed __y on each row, got {row!r}"

    def test_label_pane_has_no_query_transforms(self, label_pane):
        """Pane has no joinaggregate/filter — positions are precomputed."""
        pane = label_pane
        for t in pane.get("transform") or []:
            assert "joinaggregate" not in t and "filter" not in t, (
                "Label pane should not run joinaggregate/filter; the "
                "Python-side resolver already produced one row per series"
            )

    def test_label_pane_text_mark(self, label_pane):
        """Label pane mark is type=text with align=left."""
        pane = label_pane
        mark = pane.get("mark", {})
        assert mark.get("type") == "text"
        assert mark.get("align") == "left"

    def test_label_pane_text_is_series_name(self, label_pane):
        """Label text encodes the series field directly — series name only.

        No value formatting, no concatenation. The dark companion stroke
        carries series identity; the text spells it out.
        """
        pane = label_pane
        text_enc = pane.get("encoding", {}).get("text", {})
        assert text_enc.get("field") == "series", (
            f"Expected text.field='series', got {text_enc!r}"
        )

    def test_label_pane_y_anchored_via_label_y_field(self, label_pane):
        """Label y encodes the precomputed `__y` field.

        pane[1] uses `y.field='__y'` quantitative; combined with the
        wrapper's `resolve.scale.y: shared`, vega-lite maps the label_y
        (a value in the chart's y data domain after collision-avoidance
        nudging) to the same pixel as the corresponding line endpoint.
        """
        pane = label_pane
        y_enc = pane.get("encoding", {}).get("y", {})
        assert y_enc.get("field") == "__y", (
            f"Expected y.field='__y' (resolver output), got {y_enc!r}"
        )
        assert y_enc.get("type") == "quantitative"
        # No row-number window transform — labels land where the line ends.
        for t in pane.get("transform", []):
            assert "window" not in t, (
                "row_number window transform should be gone; labels are "
                "y-anchored to the data, not stacked by rank"
            )

    def test_label_pane_color_encoding_dark_palette(self, label_pane):
        """Label pane color encoding uses dark companion palette stops."""
        pane = label_pane
        color_enc = pane.get("encoding", {}).get("color", {})
        assert color_enc, "Expected color encoding in label pane"
        scale_range = color_enc.get("scale", {}).get("range", [])
        assert scale_range, "Expected color scale range in label pane"
        # Dark stops should be different from (darker than) the main palette
        # We can't pin exact hex values but we can verify they're non-empty strings
        assert all(isinstance(s, str) and len(s) > 0 for s in scale_range)

    def test_label_pane_width(self, label_pane):
        """Label pane width is a positive float derived from measured series names."""
        pane = label_pane
        width = pane.get("width")
        assert isinstance(width, float) and width > 0, (
            f"label pane width must be a positive float, got {width!r}"
        )

    def test_label_pane_x_at_zero(self, label_pane):
        """Label pane x encoding positions all labels at x=0 (left edge of pane)."""
        pane = label_pane
        x_enc = pane.get("encoding", {}).get("x", {})
        assert x_enc.get("value") == 0

    def test_label_resolver_cascades_downward_when_anchors_cluster_at_top(self):
        """Anchors clustered in the upper half of the domain → cascade
        downward, pinning the highest endpoint and pushing successive
        labels down by exactly min_data_gap.
        """

        last = {
            "International": 183000,
            "Partner": 177500,
            "Growth": 174500,
            "Enterprise": 167000,
            "Self-Serve": 164500,
            "Core": 155500,
        }
        data = []
        for s, y in last.items():
            data.append({"date": "2025-01-01", "value": 50000, "series": s})
            data.append({"date": "2025-06-29", "value": y, "series": s})

        min_gap = 7000.0  # data units; > all natural gaps so cascade kicks in
        positions = _resolve_endpoint_label_positions(
            data,
            "date",
            "value",
            "series",
            min_data_gap=min_gap,
            y_domain_min=50000.0,
            y_domain_max=200000.0,  # midpoint 125000; anchor mean ~ 170333
        )
        assert len(positions) == 6
        ys = [y for _, y in positions]
        assert ys == sorted(ys, reverse=True), (
            f"Output must be sorted top-to-bottom, got {positions!r}"
        )
        for prev, cur in zip(ys, ys[1:], strict=False):
            assert prev - cur >= min_gap - 1e-6, (
                f"Adjacent labels too close: {prev} → {cur}"
            )
        # Cluster sits in upper half → top is pinned at its anchor.
        assert positions[0] == ("International", 183000)

    def test_label_resolver_cascades_upward_when_anchors_cluster_at_bottom(self):
        """Anchors clustered in the lower half of the domain → cascade
        upward, pinning the lowest endpoint and pushing successive labels
        up. Without this flip, top-down nudging would shove the bottom
        labels off the chart.
        """

        # All anchors crammed near 60-70k on a [50k, 200k] domain (midpoint
        # 125k). Cluster mean ≈ 65k, well below midpoint → upward cascade.
        last = {"A": 70000, "B": 67000, "C": 64000, "D": 61000, "E": 58000}
        data = []
        for s, y in last.items():
            data.append({"date": "2025-01-01", "value": 50000, "series": s})
            data.append({"date": "2025-06-29", "value": y, "series": s})

        min_gap = 8000.0  # > all natural gaps → cascade triggers
        positions = _resolve_endpoint_label_positions(
            data,
            "date",
            "value",
            "series",
            min_data_gap=min_gap,
            y_domain_min=50000.0,
            y_domain_max=200000.0,
        )
        ys = [y for _, y in positions]
        assert ys == sorted(ys, reverse=True), (
            f"Output must be sorted top-to-bottom, got {positions!r}"
        )
        for prev, cur in zip(ys, ys[1:], strict=False):
            assert prev - cur >= min_gap - 1e-6, (
                f"Adjacent labels too close: {prev} → {cur}"
            )
        # Bottommost series ('E', 58000) is pinned — upward cascade pushes
        # everyone above *up*, never below E.
        assert positions[-1] == ("E", 58000)
        # And the lowest label is still at the lowest anchor — labels
        # never get shoved below the chart.
        assert ys[-1] == 58000

    def test_label_color_scale_uses_alphabetical_domain(
        self, make_chart, model_copy_at
    ):
        """Color scale domain is alphabetically sorted to mirror vega-lite's
        default nominal-scale sorting on the line pane.

        The line pane's color encoding maps `palette[i]` to the i-th series
        in alphabetical order (vega-lite default). If the label pane's color
        domain were in encounter order or y-sorted order, each label would
        render in the *wrong* dark stop — e.g. International's label would
        appear in Core's blue rather than International's gold. Locks the
        ordering to match what vega-lite does on the line side.
        """
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        # Encounter order intentionally NOT alphabetical: Zebra, Apple, Mango
        chart = _line_chart(make_chart, color="series")
        data = [
            {"date": "2024-01-01", "value": 50, "series": "Zebra"},
            {"date": "2024-01-01", "value": 200, "series": "Apple"},
            {"date": "2024-01-01", "value": 100, "series": "Mango"},
            {"date": "2024-02-01", "value": 60, "series": "Zebra"},
            {"date": "2024-02-01", "value": 220, "series": "Apple"},
            {"date": "2024-02-01", "value": 110, "series": "Mango"},
        ]

        seed = model_copy_at(
            get_theme_style(),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)
        rc = resolve(chart, data, chart_style_context=board_style)
        artifact = render_resolved_chart(rc, data, _BOARD_STYLE)
        assert artifact.kind == "vega_spec"
        spec = artifact.payload

        pane = spec["hconcat"][1]
        domain = pane["encoding"]["color"]["scale"]["domain"]
        assert domain == ["Apple", "Mango", "Zebra"], (
            f"Color domain must be alphabetical ['Apple', 'Mango', 'Zebra'] "
            f"to mirror vega-lite's default nominal-scale sort on the line "
            f"side; got {domain!r}"
        )

    def test_label_resolver_keeps_endpoints_when_well_separated(self):
        """Two well-separated series sit exactly at their endpoints.

        Locks in the 2-series case the user already validated: when the
        gap between endpoints exceeds min_gap, no nudging happens — the
        labels render right next to the actual line ends.
        """

        data = [
            {"date": "2024-01-01", "value": 75000, "series": "Core"},
            {"date": "2024-06-29", "value": 155500, "series": "Core"},
            {"date": "2024-01-01", "value": 75000, "series": "Growth"},
            {"date": "2024-06-29", "value": 174500, "series": "Growth"},
        ]
        # 19 000 endpoint gap > min_gap, so no nudging.
        positions = _resolve_endpoint_label_positions(
            data,
            "date",
            "value",
            "series",
            min_data_gap=8000.0,
            y_domain_min=75000.0,
            y_domain_max=200000.0,
        )
        assert positions == [("Growth", 174500.0), ("Core", 155500.0)]


# ---------------------------------------------------------------------------
# 3. Disabled by default — no hconcat
# ---------------------------------------------------------------------------


class TestEndpointLabelsDisabled:
    def test_no_hconcat_when_disabled(self, make_chart, model_copy_at):
        """When endpoint labels disabled → no hconcat emitted.

        Seeds ``endpoint_labels.visible=False`` explicitly rather than leaning on
        a theme's inherited default — theme defaults are tunable (dbt-charts/
        AGENTS.md), and the stark family now defaults this on.
        """
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        chart = _line_chart(make_chart, color="series")
        data = _multi_series_data()
        seed = model_copy_at(
            get_theme_style(),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=False, label_offset=5.0, height=20.0),
        )
        board_style, board_context = resolve_style_and_context(seed)

        resolved_chart = resolve(chart, data, chart_style_context=board_context)
        artifact = render_resolved_chart(resolved_chart, data, board_style)
        assert artifact.kind == "vega_spec"
        spec = artifact.payload

        assert "hconcat" not in spec


# ---------------------------------------------------------------------------
# 4. Single-series line — no hconcat even if enabled
# ---------------------------------------------------------------------------


class TestEndpointLabelsSingleSeries:
    def test_no_hconcat_for_single_series(self, make_chart, model_copy_at):
        """Single-series line (no color encoding) → no hconcat even if enabled."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        # No color= → single series
        chart = _line_chart(make_chart)
        data = _single_series_data()

        seed = model_copy_at(
            get_theme_style(),
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        board_style = resolve_chart_style_context(seed)
        resolved_chart = resolve(chart, data, chart_style_context=board_style)
        artifact = render_resolved_chart(resolved_chart, data, _BOARD_STYLE)
        assert artifact.kind == "vega_spec"
        spec = artifact.payload

        assert "hconcat" not in spec


# ---------------------------------------------------------------------------
# 5. Main chart pane keeps its original layers — no endpoint-dot injection
# ---------------------------------------------------------------------------


class TestEndpointLabelsNoEndpointDot:
    def test_main_pane_layers_unchanged(self, make_chart, model_copy_at):
        """Main pane's layer count must match the unwrapped chart.

        The earlier design injected a visible endpoint dot at the last data
        point per series; we drop that — the line itself plus the right-side
        labels carry the endpoint affordance. Both baselines seed the
        ``endpoint_labels.visible`` flag explicitly (off vs on) off the same
        theme, so the change is isolated to that flag and independent of the
        theme's (tunable) inherited default.
        """
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        chart = _line_chart(make_chart, color="series")
        data = _multi_series_data()
        base = get_theme_style()

        # Baseline: endpoint labels explicitly off → no hconcat
        off_seed = model_copy_at(
            base,
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=False, label_offset=5.0, height=20.0),
        )
        off_board, off_context = resolve_style_and_context(off_seed)
        resolved_off = resolve(chart, data, chart_style_context=off_context)
        artifact_off = render_resolved_chart(resolved_off, data, off_board)
        assert artifact_off.kind == "vega_spec"
        spec_off = artifact_off.payload

        # Enable: same base but with endpoint labels on
        seed = model_copy_at(
            base,
            "charts.line.endpoint_labels",
            EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
        )
        board_style, board_context = resolve_style_and_context(seed)
        resolved_on = resolve(chart, data, chart_style_context=board_context)
        artifact_on = render_resolved_chart(resolved_on, data, board_style)
        assert artifact_on.kind == "vega_spec"
        spec_on = artifact_on.payload
        main_on = spec_on["hconcat"][0]

        layers_off = spec_off.get("layer", [])
        layers_on = main_on.get("layer", [])
        assert len(layers_on) == len(layers_off), (
            f"Wrapped main pane added/removed layers (was {len(layers_off)}, "
            f"got {len(layers_on)}) — endpoint-dot layer should not be injected"
        )


def test_label_pane_min_pixel_gap_at_least_font_size():
    """Min pixel gap between adjacent labels must be >= font size.

    Regression: ``_LABEL_LINE_HEIGHT_MULTIPLIER`` was once 0.7, which let
    14pt labels nudge to 9.8px apart — narrower than the labels themselves,
    so the collision-avoidance algorithm produced overlapping bounding
    rects. The contract is that the multiplier is >= 1.0 so labels'
    bounding rects can never overlap on the y-axis. We assert the *rule*,
    not the literal value, so tuning the multiplier upward stays free.

    A unit test of the pixel<->data conversion formula in isolation cannot
    see whether the *delivered* pixel gap actually meets this floor — see
    ``test_endpoint_label_gap.py``, which measures it from rendered SVG.
    This test only pins the typographic rule the engine constant must satisfy.
    """
    from dbt_charts.core.constants.marks import ENDPOINT_LABEL_LINE_HEIGHT_MULTIPLIER

    multiplier = ENDPOINT_LABEL_LINE_HEIGHT_MULTIPLIER
    assert multiplier >= 1.0, (
        f"line_height_multiplier ({multiplier}) must be >= 1.0 "
        f"or adjacent label bounding rects can overlap"
    )


def test_label_pane_height_pinned_to_pane0(make_chart, model_copy_at):
    """Label pane must carry an explicit height equal to pane[0]'s spec height.

    Regression: the label pane had no explicit height, so VL defaulted to
    view.continuousHeight for pane[1]. When spec_height < continuousHeight (the
    sizing pass height is narrower than VL's default), the hconcat rendered
    taller than a chart without endpoint labels, causing cols-layout height
    misalignment.

    Fix: _build_endpoint_label_pane stamps "height": spec_height on the pane
    when spec_height is available, so both panes are equal height and the
    hconcat total == the single-pane height.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    chart = _line_chart(make_chart, color="series")
    data = _multi_series_data()

    seed = model_copy_at(
        get_theme_style(),
        "charts.line.endpoint_labels",
        EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
    )
    board_style = resolve_chart_style_context(seed)
    resolved = resolve(chart, data, chart_style_context=board_style)

    explicit_height = 250.0
    artifact = render_resolved_chart(
        resolved, data, _BOARD_STYLE, width=600, height=explicit_height
    )
    assert artifact.kind == "vega_spec"
    spec = artifact.payload

    assert "hconcat" in spec, "Expected hconcat from endpoint labels"
    pane0, pane1 = spec["hconcat"]
    pane0_height = pane0.get("height")
    pane1_height = pane1.get("height")

    assert pane0_height == explicit_height, (
        f"pane[0] height must be {explicit_height}, got {pane0_height}"
    )
    assert pane1_height == explicit_height, (
        f"Label pane height must match pane[0] ({explicit_height}), "
        f"got {pane1_height} — mismatched heights let VL expand the hconcat "
        f"beyond the sizing-pass estimate"
    )


# ---------------------------------------------------------------------------
# 6b. Wrapper stamps $df_target_height for two-pass height correction
# ---------------------------------------------------------------------------


def test_hconcat_wrapper_stamps_df_target_height(make_chart, model_copy_at):
    """_maybe_wrap_endpoint_label_pane must stamp $df_target_height on the wrapper.

    vl-convert ignores autosize:fit on hconcat children (same limitation as
    width). Without a probe-and-correct pass the hconcat SVG height exceeds the
    spec height by the chart chrome overhead (~80px), making a chart with
    endpoint labels taller than the identical chart without labels.

    Fix: stamp "$df_target_height": spec_height on the wrapper alongside the
    existing "$df_target_width"; render_vega_spec probes the SVG height and
    shrinks both pane heights by the overshoot before the real render.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    chart = _line_chart(make_chart, color="series")
    data = _multi_series_data()

    seed = model_copy_at(
        get_theme_style(),
        "charts.line.endpoint_labels",
        EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
    )
    board_style = resolve_chart_style_context(seed)
    resolved = resolve(chart, data, chart_style_context=board_style)

    explicit_height = 250.0
    artifact = render_resolved_chart(
        resolved, data, _BOARD_STYLE, width=600, height=explicit_height
    )
    assert artifact.kind == "vega_spec"
    spec = artifact.payload

    assert "hconcat" in spec, "Expected hconcat wrapper from endpoint labels"
    assert "$df_target_height" in spec, (
        "Wrapper must carry $df_target_height so render_vega_spec can probe and "
        "correct the SVG height overshoot caused by vl-convert ignoring autosize:fit "
        "on hconcat children"
    )
    assert spec["$df_target_height"] == explicit_height, (
        f"$df_target_height must equal the requested spec height ({explicit_height}), "
        f"got {spec['$df_target_height']}"
    )


# ---------------------------------------------------------------------------
# 7. Theme corpus smoke test
# ---------------------------------------------------------------------------


def test_all_themes_compile_with_endpoint_labels_field(compiled_themes):
    """Every production theme must compile and resolve with the endpoint_labels field."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    for _, style in compiled_themes.items():
        resolve_style(style)  # must not raise


def test_line_endpoint_labels_disable_pane0_legend(make_chart, model_copy_at):
    """When wrapping fires on line, pane[0]'s color legend is auto-disabled.

    Mirror of the bar/area tests — the suppression lives at the shared
    wrap boundary in _maybe_wrap_endpoint_label_pane, so every family
    that wraps must observe the contract. Resolved in
    design/chart-briefs/endpoint-labels.md.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    chart = _line_chart(make_chart, color="series")
    data = _multi_series_data()

    seed = model_copy_at(
        get_theme_style(),
        "charts.line.endpoint_labels",
        EndpointLabelsConfig(visible=True, label_offset=5.0, height=20.0),
    )
    board_style = resolve_chart_style_context(seed)
    resolved = resolve(chart, data, chart_style_context=board_style)
    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload

    pane0 = spec["hconcat"][0]
    assert pane0["encoding"]["color"]["legend"] is None, (
        "pane[0].encoding.color.legend must be None when line endpoint labels wrap; "
        "the right-edge label pane already names every series."
    )


def test_zero_rule_does_not_expand_domain_for_positive_data(make_chart):
    """When all data is positive, the baseline zero rule must not fire.

    Regression: the zero rule's datum:0 layer pulls 0 into the shared hconcat
    y-scale, expanding it from [80k,185k] to [0,185k] and shrinking endpoint-label
    pixel gaps from ~16px to ~9px — all six labels overlapped. V2's
    BaselineFeature (render/chart/features/baseline.py) only fires the line
    zero rule when the data straddles 0 (given scale.zero is not True), so the
    all-positive spec must carry no datum:0 rule layer.
    """
    # Six-series revenue data: all values well above 0 (80k–185k).
    series = ["A", "B", "C", "D", "E", "F"]
    data = [
        {"date": f"2024-0{i + 1}-01", "value": 80000 + i * 1000 + j * 1000, "series": s}
        for j, s in enumerate(series)
        for i in range(5)
    ]
    assert all(r["value"] > 0 for r in data), "test data must be all-positive"

    chart = _line_chart(make_chart, color="series")
    resolved = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)

    # Full render: the hconcat pane[0] must NOT contain a datum:0 rule layer.
    artifact = render_resolved_chart(resolved, data, _BOARD_STYLE, width=600)
    assert artifact.kind == "vega_spec"
    spec = artifact.payload

    assert "hconcat" in spec
    pane0 = spec["hconcat"][0]
    datum_zero_layers = [
        layer
        for layer in pane0.get("layer", [])
        if layer.get("encoding", {}).get("y", {}).get("datum") == 0
    ]
    assert not datum_zero_layers, (
        "pane[0] must not contain a datum:0 rule layer when all data is positive; "
        f"found {datum_zero_layers}"
    )


# ---------------------------------------------------------------------------
# 8. Label cascade domain invariant — kept labels must never exceed y_domain bounds
# ---------------------------------------------------------------------------


def test_label_cascade_stays_in_domain_downward():
    """Downward cascade must not push a KEPT label below y_domain_min.

    Regression: _apply_label_cascade used to cascade without any bound at
    all, so 3 series clustered in the upper half of a narrow domain (range
    < 2 * min_data_gap) could produce label y positions below y_domain_min.
    The hconcat shared y-scale then expanded the rendered SVG height beyond
    the sizing pass estimate, causing ~81px overshoot on multi-series line
    charts.

    This domain genuinely cannot hold all 3 labels at this gap — (3-1)*15 =
    30 exceeds the 10-unit span — so today's fix drops what does not fit
    (need-based, see TestRailOverflowDrop in test_endpoint_label_gap.py)
    rather than clamping every survivor onto one pixel or letting them
    escape the domain. The bound invariant this test pins is on whatever
    stays: nothing kept ever sits below y_domain_min.
    """

    y_domain_min, y_domain_max = 50.0, 60.0
    # min_data_gap > (y_max - y_min) / 2 guarantees overflow
    min_data_gap = 15.0

    # Centroid = 58.7 > domain_mid=55 → downward cascade
    anchors = {"A": 59.0, "B": 58.0, "C": 57.0}
    result, dropped = _apply_label_cascade(
        anchors,
        min_data_gap=min_data_gap,
        y_domain_min=y_domain_min,
        y_domain_max=y_domain_max,
    )

    # A sits at its own true anchor already, needs no push, and the domain
    # cannot hold two more 15 apart from it — B and C are the ones that
    # genuinely have nowhere to go.
    assert dropped == ["B", "C"], f"expected B and C to be dropped, got {dropped}"
    ys = [y for _, y in result]
    assert all(y >= y_domain_min for y in ys), (
        f"Downward cascade pushed a kept label below y_domain_min={y_domain_min}: {ys}"
    )
    assert all(y <= y_domain_max for y in ys), (
        f"Kept labels exceed y_domain_max={y_domain_max}: {ys}"
    )


def test_label_cascade_stays_in_domain_upward():
    """Upward cascade must not push a KEPT label above y_domain_max.

    Mirror of test_label_cascade_stays_in_domain_downward for the lower-half cluster
    case (centroid < domain_mid → cascade upward).
    """

    y_domain_min, y_domain_max = 50.0, 60.0
    min_data_gap = 15.0

    # Centroid = 51.3 < domain_mid=55 → upward cascade
    anchors = {"A": 51.0, "B": 52.0, "C": 53.0}
    result, dropped = _apply_label_cascade(
        anchors,
        min_data_gap=min_data_gap,
        y_domain_min=y_domain_min,
        y_domain_max=y_domain_max,
    )

    assert dropped == ["B", "C"], f"expected B and C to be dropped, got {dropped}"
    ys = [y for _, y in result]
    assert all(y >= y_domain_min for y in ys), (
        f"Kept labels fell below y_domain_min={y_domain_min}: {ys}"
    )
    assert all(y <= y_domain_max for y in ys), (
        f"Upward cascade pushed a kept label above y_domain_max={y_domain_max}: {ys}"
    )


# ---------------------------------------------------------------------------
# 9. _extract_y_domain reads domainMin/domainMax (individual keys)
# ---------------------------------------------------------------------------


def test_extract_y_domain_reads_domain_min_max():
    """_extract_y_domain must read domainMin/domainMax when scale.domain is absent.

    When tick values are pinned via nice_tick_values, the cartesian emitter
    writes scale.domainMin and scale.domainMax (not scale.domain as a
    list). If _extract_y_domain ignores them it falls back to raw data
    range, making _label_pane_min_data_gap underestimate y_range and
    space labels too tightly.
    """

    spec = {
        "encoding": {
            "y": {
                "field": "revenue",
                "scale": {"zero": False, "domainMin": 50000.0, "domainMax": 200000.0},
            }
        }
    }
    data = [{"revenue": 97000}, {"revenue": 168000}]
    lo, hi = _extract_y_domain(spec, data, "revenue")
    assert lo == 50000.0, f"expected domainMin=50000.0, got {lo}"
    assert hi == 200000.0, f"expected domainMax=200000.0, got {hi}"


def test_extract_y_domain_falls_back_to_data_when_no_scale():
    """_extract_y_domain falls back to data range when scale carries no bounds."""

    spec = {"encoding": {"y": {"field": "revenue"}}}
    data = [{"revenue": 97000}, {"revenue": 168000}]
    lo, hi = _extract_y_domain(spec, data, "revenue")
    assert lo == 97000.0
    assert hi == 168000.0
