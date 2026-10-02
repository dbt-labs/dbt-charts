"""V2 render path: ``style.axis_y.mirror`` draws the y-scale on both edges.

The feature is a ``MirrorAxisFeature`` in the V2 FeaturePipeline that appends a
transparent ghost overlay layer carrying the opposite-edge y-axis and sets
``resolve.axis.y = "independent"`` off the single shared scale.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    LineChart,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)
_SVG_NS = "http://www.w3.org/2000/svg"


_MIRROR: dict[str, Any] = {"axis_y": {"mirror": True}}


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _board() -> Any:
    return resolve_style_and_context(get_theme_style("clarity"))


def _v2_vl(
    norm: Chart, data: list[dict[str, Any]], board: Any | None = None
) -> dict[str, Any]:
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.session import BoardRenderSession

    board_rs, board_ctx = board if board is not None else _board()
    resolved = resolve(norm, data, chart_style_context=board_ctx)
    session = BoardRenderSession.create(board_rs)
    return session.finalize_vl(
        session.emit_chart(resolved, _DEFAULT_BOX, {resolved.query_name: data})
    )


def _wide_data() -> list[dict[str, Any]]:
    return [{"month": i, "value": 100 + i * 5} for i in range(6)]


def _y_axis_orients(spec: dict[str, Any]) -> set[str]:
    """All y-axis orient values across the spec's layers + top level."""
    found: set[str] = set()

    def walk(s: dict[str, Any]) -> None:
        enc = s.get("encoding")
        if isinstance(enc, dict) and isinstance(enc.get("y"), dict):
            axis = enc["y"].get("axis")
            if isinstance(axis, dict) and axis.get("orient"):
                found.add(axis["orient"])
        for lyr in s.get("layer", []):
            if isinstance(lyr, dict):
                walk(lyr)

    walk(spec)
    return found


def _y_axis_defs(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Every encoding.y.axis def in the spec (root + every layer), in document order.

    Unlike ``_y_axis_orients`` (a set — collapses duplicates), this preserves
    count so a triple-drawn axis is distinguishable from a single one.
    """
    found: list[dict[str, Any]] = []

    def walk(s: dict[str, Any]) -> None:
        enc = s.get("encoding")
        if isinstance(enc, dict) and isinstance(enc.get("y"), dict):
            axis = enc["y"].get("axis")
            if isinstance(axis, dict):
                found.append(axis)
        for lyr in s.get("layer", []):
            if isinstance(lyr, dict):
                walk(lyr)

    walk(spec)
    return found


def _y_axis_key_by_orient(spec: dict[str, Any], key: str) -> dict[str, Any]:
    """Map each y-axis orient ('left'/'right') to its VL axis value for `key`."""
    found: dict[str, Any] = {}

    def walk(s: dict[str, Any]) -> None:
        enc = s.get("encoding")
        if isinstance(enc, dict) and isinstance(enc.get("y"), dict):
            axis = enc["y"].get("axis")
            if isinstance(axis, dict) and axis.get("orient"):
                found[axis["orient"]] = axis.get(key)
        for lyr in s.get("layer", []):
            if isinstance(lyr, dict):
                walk(lyr)

    walk(spec)
    return found


class TestV2MirrorAxis:
    def test_mirror_draws_both_edges(self):
        chart = AreaChart.model_validate(
            {"id": "t", "type": "area", "x": "month", "y": "value", "style": _MIRROR}
        )
        spec = _v2_vl(chart, _wide_data())
        orients = _y_axis_orients(spec)
        assert {
            "left",
            "right",
        } <= orients, f"expected y-axis on both edges, got {orients}"

    def test_mirror_without_y_is_inert(self):
        """`axis_y.mirror: true` on a chart that authors no `y:` at all emits
        no y encoding and no y axis — there is nothing to mirror, so the
        feature is inert. Previously it raised the code-less ChartDataError
        ("could not find the chart's y encoding to mirror"), surfacing as
        ERR-INTERNAL for a board the design panel can produce by clearing y."""
        chart = LineChart.model_validate(
            {"id": "t", "type": "line", "x": "month", "style": _MIRROR}
        )
        spec = _v2_vl(chart, _wide_data())
        assert _y_axis_orients(spec) == set()
        assert spec.get("resolve", {}).get("axis", {}).get("y") != "independent"

    def test_histogram_mirror_draws_both_edges(self):
        """A histogram resolves y=None by design (the count measure is
        synthesized at emit time), yet its emitted spec carries a real y
        encoding — mirror binds to THAT, not to the resolved model's y.
        Regression: gating applies_to on `chart.y` silently dropped the
        mirrored edge from a working histogram."""
        chart = BarChart.model_validate(
            {"id": "t", "type": "histogram", "x": "price", "style": _MIRROR}
        )
        data = [{"price": float(i % 7) + i / 10} for i in range(30)]
        spec = _v2_vl(chart, data)
        assert {"left", "right"} <= _y_axis_orients(spec)
        assert spec.get("resolve", {}).get("axis", {}).get("y") == "independent"

    @pytest.mark.parametrize("top_y", [None, "revenue"])
    def test_layered_mirror_raises_typed_error(self, top_y):
        """`layers:` move every y encoding onto the layers (the base's own
        included), so the shared encoding has no y for the ghost to bind —
        but a real y axis still paints, so mirror must refuse with the typed
        ERR-MIRROR-LAYERS rather than silently skip (or die in the code-less
        ChartDataError that surfaces as ERR-INTERNAL, the pre-fix behavior).
        Both authored shapes, since the y-less one still binds layer ys."""
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import ERR_MIRROR_LAYERS

        chart_def: dict[str, Any] = {
            "id": "t",
            "type": "line",
            "x": "month",
            "layers": [
                {"type": "line", "y": "target"},
            ],
            "style": _MIRROR,
        }
        if top_y is not None:
            chart_def["y"] = top_y
        chart = LineChart.model_validate(chart_def)
        data = [{"month": i, "revenue": 100 + i, "target": 90 + i} for i in range(6)]
        with pytest.raises(ChartDataError, match="layers") as exc_info:
            _v2_vl(chart, data)
        assert exc_info.value.code is ERR_MIRROR_LAYERS

    def test_resolve_axis_y_independent(self):
        chart = AreaChart.model_validate(
            {"id": "t", "type": "area", "x": "month", "y": "value", "style": _MIRROR}
        )
        spec = _v2_vl(chart, _wide_data())
        assert spec.get("resolve", {}).get("axis", {}).get("y") == "independent"

    def test_mirror_draws_both_edges_bar(self):
        """Single-series bar goes through the non-layered translate path; mirror must
        still emit resolve + both edges there (regression: resolve was layered-only)."""
        chart = BarChart.model_validate(
            {"id": "t", "type": "bar", "x": "month", "y": "value", "style": _MIRROR}
        )
        spec = _v2_vl(chart, _wide_data())
        assert {"left", "right"} <= _y_axis_orients(spec)
        assert spec.get("resolve", {}).get("axis", {}).get("y") == "independent"

    def test_mirror_draws_both_edges_bar_mixed_sign(self):
        """Mixed-sign bar data triggers emit_bar_layer's pos/neg corner-radius
        split, which pops the y channel off the outer spec's encoding — mirror
        must still find a y encoding to reflect (regression: ChartDataError)."""
        chart = BarChart.model_validate(
            {"id": "t", "type": "bar", "x": "month", "y": "value", "style": _MIRROR}
        )
        data = [
            {"month": 0, "value": 100},
            {"month": 1, "value": -40},
            {"month": 2, "value": 260},
        ]
        spec = _v2_vl(chart, data)
        assert {"left", "right"} <= _y_axis_orients(spec)
        assert spec.get("resolve", {}).get("axis", {}).get("y") == "independent"

    def test_vertical_bar_mixed_sign_no_mirror_draws_axis_once(self):
        """Regression: a vertical bar chart with mixed-sign data (positive AND
        negative y values, no mirroring) triggered emit_bar_layer's pos/neg
        corner-radius split. The split copied the same axis-bearing y encoding
        onto the outer spec AND both sub-layers, so the finalized VL spec
        declared (and vl_convert painted) the same y-axis three times instead
        of once."""
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "x": "month",
                "y": "value",
                "style": {"orientation": "vertical"},
            }
        )
        data = [
            {"month": "Jan", "value": 400000},
            {"month": "Feb", "value": -1500000},
            {"month": "Mar", "value": 250000},
        ]
        spec = _v2_vl(chart, data)
        assert len(_y_axis_defs(spec)) == 1

    def test_vertical_bar_mixed_sign_mirror_draws_axis_once_per_edge(self):
        """Same corner-radius split, with mirror on: the outer axis and the
        pos-layer's own axis are the SAME primary edge, so mirror's edge count
        (a set) still reads {left, right} even though 'right' is drawn twice.
        Assert defs-per-orient instead of just membership."""
        chart = BarChart.model_validate(
            {
                "id": "t",
                "type": "bar",
                "x": "month",
                "y": "value",
                "style": {"orientation": "vertical", "axis_y": {"mirror": True}},
            }
        )
        data = [
            {"month": "Jan", "value": 400000},
            {"month": "Feb", "value": -1500000},
            {"month": "Mar", "value": 250000},
        ]
        spec = _v2_vl(chart, data)
        defs = _y_axis_defs(spec)
        orients = [d.get("orient") for d in defs]
        assert sorted(orients) == [
            "left",
            "right",
        ], f"expected exactly one axis def per edge, got {orients}"

    def test_mirror_multi_series_errors(self):
        """mirror on a multi-series (list y) chart errors — not a silent no-op.

        Fires the typed ERR-MIRROR-MULTI-SERIES code, never the ERR-INTERNAL
        fallback. This check stays at render because mirror is cascade-resolved:
        a theme layer can turn it on for a chart that never authored it, so the
        authored chart alone can't decide the combination.
        """
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import ERR_MIRROR_MULTI_SERIES

        chart = LineChart.model_validate(
            {"id": "t", "type": "line", "x": "month", "y": ["a", "b"], "style": _MIRROR}
        )
        rows = [{"month": i, "a": i, "b": i * 2} for i in range(4)]
        with pytest.raises(ChartDataError, match="multi-series") as exc_info:
            _v2_vl(chart, rows)
        assert exc_info.value.code is ERR_MIRROR_MULTI_SERIES
        # from_code joins an all-str sequence before formatting, so the template
        # must not also repr it — "(y = 'a, b')" reads as one comma-named field.
        assert "(y = a, b)" in exc_info.value.message

    def test_scatter_mirror_multi_series_errors(self):
        """Same as ``test_mirror_multi_series_errors``, for scatter's own
        wide (``y: [a, b]``) fold -- scatter joined the wide-measures shape
        this check already covers for line/area/bar."""
        from dbt_charts.core.compile.models.chart.normalized import ScatterChart
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import ERR_MIRROR_MULTI_SERIES

        chart = ScatterChart.model_validate(
            {
                "id": "t",
                "type": "scatter",
                "x": "month",
                "y": ["a", "b"],
                "style": _MIRROR,
            }
        )
        rows = [{"month": i, "a": i, "b": i * 2} for i in range(4)]
        with pytest.raises(ChartDataError, match="multi-series") as exc_info:
            _v2_vl(chart, rows)
        assert exc_info.value.code is ERR_MIRROR_MULTI_SERIES

    def test_heatmap_multi_measure_mirror_errors_as_multi_series(self):
        """A multi-measure heatmap folds its list y into per-measure sublayers
        (no shared y encoding, no authored `layers:`), so mirror must refuse
        with ERR-MIRROR-MULTI-SERIES — naming the fields the author wrote —
        never ERR-MIRROR-LAYERS, whose remedy ("drop the `layers:`") is
        un-actionable on a board that has none."""
        from dbt_charts.core.compile.models.chart.normalized import HeatmapChart
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import ERR_MIRROR_MULTI_SERIES

        chart = HeatmapChart.model_validate(
            {
                "id": "t",
                "type": "heatmap",
                "x": "month",
                "y": ["revenue", "target"],
                "style": _MIRROR,
            }
        )
        rows = [{"month": i, "revenue": i, "target": i * 2} for i in range(4)]
        with pytest.raises(ChartDataError, match="multi-series") as exc_info:
            _v2_vl(chart, rows)
        assert exc_info.value.code is ERR_MIRROR_MULTI_SERIES

    def test_no_mirror_no_wrap(self):
        chart = AreaChart(id="t", type="area", x="month", y="value")
        spec = _v2_vl(chart, _wide_data())
        assert spec.get("resolve", {}).get("axis", {}).get("y") != "independent"

    def test_mirror_with_explicit_label_align_computes_measured_padding(self):
        """mirror + authored label.align: the align value safe on the primary
        edge is copied onto the opposite-orient ghost axis, where it becomes
        the own-side invading case — the same measured-labelPadding fix as
        the non-mirrored guard (vl_field_maps.py), applied to the ghost.
        """
        chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                # position right + align left is safe on the primary edge —
                # the ghost (orient left) is where this invades.
                "style": {
                    "axis_y": {
                        "mirror": True,
                        "position": "right",
                        "labels": {"align": "left"},
                    }
                },
            }
        )
        spec = _v2_vl(chart, _wide_data())
        padding_by_orient = _y_axis_key_by_orient(spec, "labelPadding")
        assert padding_by_orient["left"] > 0

    def test_mirror_with_affixed_format_measures_the_ghost_gutter(self):
        """An affixed format paints via labelExpr, not ``format``; the ghost still
        measures the affixed labels instead of finding nothing to measure."""
        base = {
            "id": "t",
            "type": "line",
            "x": "month",
            "y": "value",
        }

        def left_padding(fmt: object) -> float:
            chart = LineChart.model_validate(
                {
                    **base,
                    "style": {
                        "axis_y": {
                            "mirror": True,
                            "position": "right",
                            "labels": {"align": "left", "format": fmt},
                        }
                    },
                }
            )
            spec = _v2_vl(chart, _wide_data())
            return _y_axis_key_by_orient(spec, "labelPadding")["left"]

        plain = left_padding(",.0f")
        affixed = left_padding({"spec": ",.0f", "prefix": "EUR "})
        assert affixed > plain > 0

    def test_mirror_measures_tick_label_not_label_format(self):
        """Same measurement-must-match-paint contrast as vl_field_maps.py's
        own-side-align guard (see test_vega_lite_axes.py's
        ``test_own_side_align_measures_tick_label_not_label_format``),
        applied to the mirror ghost: a non-compacting ladder bakes
        ``tick_label.format`` (plain, comma-grouped digits) while
        ``labels.format`` stays the theme's untouched SI default -- the
        ghost's gutter must be measured from what actually paints.
        Contrasted against explicitly authoring ``labels.format: .3~s``,
        which suppresses the bake entirely and keeps the ghost's own-side
        padding measured from the (narrower) SI string. If
        ``MirrorAxisFeature`` ever stops preferring ``axis_y.tick_label``
        over the ghost's inherited ``format``, both paddings collapse to the
        SI-measured value and this assertion catches it.
        """

        def _padding(labels: dict[str, Any]) -> float:
            chart = LineChart.model_validate(
                {
                    "id": "t",
                    "type": "line",
                    "x": "month",
                    "y": "value",
                    "style": {
                        "axis_y": {
                            "mirror": True,
                            "position": "right",
                            "labels": labels,
                        }
                    },
                }
            )
            data = [{"month": i, "value": 10_000 + i * 10_000} for i in range(6)]
            spec = _v2_vl(chart, data)
            padding_by_orient = _y_axis_key_by_orient(spec, "labelPadding")
            return float(padding_by_orient["left"])

        unauthored_padding = _padding({"align": "left"})
        authored_si_padding = _padding({"align": "left", "format": ".3~s"})
        assert unauthored_padding > authored_si_padding

    def test_mirror_format_ghost_measures_its_own_tick_label(self):
        """A ladder-less mirror.format preset paints sub-1 values as plain digits
        (``mirror.tick_label``); the ghost gutter must measure those, not the
        SI milli strings the bare spec would give."""

        def _padding(mirror_format: str) -> float:
            chart = LineChart.model_validate(
                {
                    "id": "t",
                    "type": "line",
                    "x": "month",
                    "y": "value",
                    "style": {
                        "axis_y": {
                            "mirror": {"format": mirror_format},
                            "position": "left",
                            "labels": {"align": "right"},
                            "ticks": {"count": None},
                        }
                    },
                }
            )
            data = [{"month": i, "value": 0.1 + i * 0.1} for i in range(6)]
            spec = _v2_vl(chart, data)
            return float(_y_axis_key_by_orient(spec, "labelPadding")["right"])

        assert _padding("number") < _padding(".3~s")

    def test_mirror_with_explicit_label_align_computes_padding_on_stark_theme(self):
        """Same as above, but on a theme (``stark``) that never bakes
        ``tick_values`` — must estimate from the actual chart data instead of
        rejecting, same as the non-mirrored guard.
        """
        chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "mirror": True,
                        "position": "right",
                        "labels": {"align": "left"},
                    }
                },
            }
        )
        stark_board = resolve_style_and_context(get_theme_style("stark"))
        spec = _v2_vl(chart, _wide_data(), board=stark_board)
        padding_by_orient = _y_axis_key_by_orient(spec, "labelPadding")
        assert padding_by_orient["left"] > 0

    def test_mirror_with_compacting_ladder_computes_padding_not_raises(self):
        """Regression: mirror + own-side align + a ladder that compacts (the
        ruler composition applies) must still measure and render, not raise.

        ``ghost_axis = dict(primary_axis)`` copies the primary's own
        engine-injected labelExpr onto the ghost. The own-side-align guard
        must not mistake that inherited, engine-composed expression for an
        authored one dbt charts can't introspect (the bug: it was gated on
        bare ``"labelExpr" not in ghost_axis"``, so any compacting ladder
        made every mirrored, own-side-aligned axis look unmeasurable and
        raise) — a board that rendered before this task must still render.
        """
        chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "mirror": True,
                        "position": "right",
                        "labels": {"align": "left"},
                    }
                },
            }
        )
        data = [{"month": i, "value": 1_000_000 + i * 500_000} for i in range(6)]
        spec = _v2_vl(chart, data)
        padding_by_orient = _y_axis_key_by_orient(spec, "labelPadding")
        assert padding_by_orient["left"] > 0

    def test_mirror_with_uppercase_labels_rejects_own_side_align(self):
        """Regression: ``inject_axis_label_case`` runs on the PRIMARY axis
        (after ``measure_axis_to_vl``), and the ghost is ``dict(primary_axis)``
        — so it inherits an ``upper(...)``-wrapped ``labelExpr`` too, not just
        an authored ``label.expr``. The ghost's own-side-align guard must
        treat that as just as unmeasurable as an authored expression:
        ``quantitative_tick_labels`` has no case-folding of its own, so
        measuring un-cased strings while Vega paints ``upper(...)`` would
        under-measure the gutter by the case width delta. Small, non-
        compacting data rules out the ruler as the cause — this isolates the
        ``font.case`` guard specifically.

        Uses an explicit RAW (non-predefined) format spec, not the theme
        default: a house-alias-derived format (including the theme's own
        unauthored baseline) is now force-right-aligned regardless of the
        authored ``align: left`` here, and the forced literal ``"right"``
        happens to be the safe/away-side default on the ghost's opposite
        (left) edge too — so the own-side-invasion scenario this test
        protects against is only still reachable via a raw spec, which
        ``build_resolved_axis`` never overrides.
        """
        chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "mirror": True,
                        "position": "right",
                        "labels": {
                            "align": "left",
                            "format": ".2f",
                            "font": {"case": "upper"},
                        },
                    }
                },
            }
        )
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        data = [{"month": i, "value": 10 + i} for i in range(6)]
        with pytest.raises(ChartDataError, match="labels.align"):
            _v2_vl(chart, data)

    def test_mirror_estimate_covers_an_authored_domain_wider_than_the_data(self):
        """Same authored-``scale.domain``-vs-data-only-estimate gap as the
        non-mirrored guard, on the ghost axis: an authored domain wider than
        the data must still widen the estimated padding, not just the data
        values themselves.
        """
        narrow_chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "mirror": True,
                        "position": "right",
                        "labels": {"align": "left"},
                    }
                },
            }
        )
        wide_domain_chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "mirror": True,
                        "position": "right",
                        "labels": {"align": "left"},
                        "scale": {"continuous": {"domain": [0, 2_000_000]}},
                    }
                },
            }
        )
        stark_board = resolve_style_and_context(get_theme_style("stark"))
        # Same (narrow) data for both — only the authored domain differs.
        data = [{"month": 0, "value": 5}, {"month": 1, "value": 500}]
        narrow_padding = _y_axis_key_by_orient(
            _v2_vl(narrow_chart, data, board=stark_board), "labelPadding"
        )["left"]
        wide_domain_padding = _y_axis_key_by_orient(
            _v2_vl(wide_domain_chart, data, board=stark_board), "labelPadding"
        )["left"]
        assert wide_domain_padding > narrow_padding

    def test_mirror_estimate_on_a_faceted_chart_reads_the_union_of_every_panel(self):
        """Regression: the ghost gutter must be sized from every panel's
        values pooled together, not just the panel with the largest single
        max value.

        `multiples: {columns: region}` auto-mirrors (shared scale — mirror
        with `scale: independent` is refused entirely, so a faceted chart
        that reaches this code always shares one scale the primary axis
        itself measures from the pooled union). `ticks: {count: null}`
        suppresses the baked tick ladder so the ghost falls to the
        data-estimate branch this bug lives in (a baked ladder takes a
        different branch and isn't affected). Panel West holds `[5, 10]`;
        panel East holds `[-1250000, -3]` — West has the larger *max*, but
        East needs far more label width. Picking only West's values (the
        bug) under-reserves the gutter by the width of East's real widest
        label. Proven by comparing against an unfaceted chart fed West's
        data alone: pooling both panels must reserve strictly more than
        West's data would on its own.
        """
        _ticks_off = {"ticks": {"count": None}}
        multiples_chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                "multiples": {"columns": "region"},
                "style": {
                    "axis_y": {
                        "position": "right",
                        "labels": {"align": "left"},
                        **_ticks_off,
                    }
                },
            }
        )
        west_only_chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "mirror": True,
                        "position": "right",
                        "labels": {"align": "left"},
                        **_ticks_off,
                    }
                },
            }
        )
        faceted_data = [
            {"month": 0, "region": "West", "value": 5},
            {"month": 1, "region": "West", "value": 10},
            {"month": 0, "region": "East", "value": -1_250_000},
            {"month": 1, "region": "East", "value": -3},
        ]
        west_only_data = [
            {"month": 0, "value": 5},
            {"month": 1, "value": 10},
        ]
        pooled_padding = _y_axis_key_by_orient(
            _v2_vl(multiples_chart, faceted_data)["spec"], "labelPadding"
        )["left"]
        west_only_padding = _y_axis_key_by_orient(
            _v2_vl(west_only_chart, west_only_data), "labelPadding"
        )["left"]
        assert pooled_padding > west_only_padding

    def test_mirror_with_explicit_label_align_and_expr_override_errors(self):
        """mirror.expr replaces the ghost's rendered label with an arbitrary
        Vega expression — dbt charts can't measure that string, so the
        own-side invasion still rejects even though tick_values/format are
        baked (unlike the plain-format case above).

        Uses an explicit RAW (non-predefined) format spec -- see
        ``test_mirror_with_uppercase_labels_rejects_own_side_align``'s
        docstring for why the theme-default/alias case no longer reaches
        this invasion at all (force-right's literal "right" is always safe
        on the ghost's opposite edge).
        """
        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "mirror": {"expr": "datum.value + ' units'"},
                        "position": "right",
                        "labels": {"align": "left", "format": ".2f"},
                    }
                },
            }
        )
        with pytest.raises(ChartDataError, match="labels.align"):
            _v2_vl(chart, _wide_data())

    def test_mirror_is_boolean_only(self):
        """style.axis_y.mirror is a boolean flag — non-bool values are rejected."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AreaChart.model_validate(
                {
                    "id": "t",
                    "type": "area",
                    "x": "month",
                    "y": "value",
                    "style": {"axis_y": {"mirror": "left"}},
                }
            )

    def test_mirror_true_copies_primary_format_verbatim(self):
        """Regression: plain `mirror: true` still mirrors the primary axis's
        format verbatim onto the ghost edge — no per-edge override requested."""
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "value",
                "style": {"axis_y": {"labels": {"format": "$.2s"}, "mirror": True}},
            }
        )
        spec = _v2_vl(chart, _wide_data())
        formats = _y_axis_key_by_orient(spec, "format")
        # Inline d3 spec passes through without trim (three-way contract).
        assert formats == {"left": "$.2s", "right": "$.2s"}

    def test_mirror_format_override_relabels_only_ghost_axis(self):
        """`mirror: {format: ...}` relabels only the mirrored edge; the primary
        axis's own format is untouched."""
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "labels": {"format": "$.2s"},
                        "mirror": {"format": ".0%"},
                    }
                },
            }
        )
        spec = _v2_vl(chart, _wide_data())
        formats = _y_axis_key_by_orient(spec, "format")
        # Primary y-axis position defaults to "right" absent endpoint labels;
        # the mirrored ghost lands on the opposite ("left") edge.
        # Inline d3 spec passes through without trim (three-way contract).
        assert formats == {"right": "$.2s", "left": ".0%"}

    def test_mirror_expr_override_relabels_only_ghost_axis(self):
        """`mirror: {expr: ...}` emits VL `labelExpr` on the mirrored edge only;
        the primary axis carries no labelExpr and keeps its own format. This is
        the share-of-total relabel — a Vega expression can divide by a total
        before formatting, which a d3-format string cannot."""
        expr = "format(datum.value / 315000000, '.0%')"
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {"labels": {"format": "$.2s"}, "mirror": {"expr": expr}}
                },
            }
        )
        spec = _v2_vl(chart, _wide_data())
        assert _y_axis_key_by_orient(spec, "labelExpr") == {"right": None, "left": expr}
        # The ghost still copies the primary's format (labelExpr wins in VL, and
        # None-override means "reuse"), and the primary format is untouched.
        # Inline d3 spec passes through without trim (three-way contract).
        assert _y_axis_key_by_orient(spec, "format") == {
            "right": "$.2s",
            "left": "$.2s",
        }

    def test_mirror_format_override_passes_through_without_trim(self):
        """`mirror: {format: ...}` is an authored inline d3 spec stored on
        AxisMirrorStyle and handed to Vega verbatim by mirror_axis.py.
        Under the three-way contract, inline specs pass through without trim
        injection — both the primary and the ghost axis carry the raw authored
        spec. Authors who want trim on inline specs write ``~`` themselves."""
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "labels": {"format": "$.2s"},
                        "mirror": {"format": ".2s"},
                    }
                },
            }
        )
        spec = _v2_vl(chart, _wide_data())
        formats = _y_axis_key_by_orient(spec, "format")
        assert formats == {"right": "$.2s", "left": ".2s"}

    def test_mirror_format_override_with_affix_measures_the_composed_labelExpr(
        self,
    ) -> None:
        """Regression: `mirror: {format: {spec, prefix}}` composes a `labelExpr` on the
        ghost (`compose_axis_format`), not VL's native `format`.
        """

        def _padding(mirror_format: Any) -> float:
            chart = LineChart.model_validate(
                {
                    "id": "t",
                    "type": "line",
                    "x": "month",
                    "y": "value",
                    "style": {
                        "axis_y": {
                            "position": "right",
                            "labels": {"align": "left", "format": ",.0f"},
                            "mirror": {"format": mirror_format},
                        }
                    },
                }
            )
            data = [{"month": i, "value": 10_000 + i * 10_000} for i in range(6)]
            spec = _v2_vl(chart, data)
            padding_by_orient = _y_axis_key_by_orient(spec, "labelPadding")
            return float(padding_by_orient["left"])

        bare_padding = _padding(",.0f")
        affixed_padding = _padding({"spec": ",.0f", "prefix": "EUR "})
        assert affixed_padding > bare_padding

    def test_mirror_format_override_drops_inherited_label_expr(self):
        """When the primary axis carries a labelExpr (style.axis_y.labels.expr)
        and the mirror override is `format:`, the ghost must drop the inherited
        labelExpr — VL prefers labelExpr over format, so leaving the copy in
        place would silently turn the explicit format override into a no-op."""
        expr = "'$' + format(datum.value / 1e6, '.0f') + 'M'"
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {"labels": {"expr": expr}, "mirror": {"format": ".0%"}}
                },
            }
        )
        spec = _v2_vl(chart, _wide_data())
        assert _y_axis_key_by_orient(spec, "labelExpr") == {"right": expr, "left": None}
        assert _y_axis_key_by_orient(spec, "format")["left"] == ".0%"

    def test_mirror_with_endpoint_labels_errors(self):
        """mirror on a chart composed into an endpoint-label pane errors, not silent.

        Fires the typed ERR-MIRROR-ENDPOINT-LABELS code, never the
        ERR-INTERNAL fallback — this collision only reaches MirrorAxisFeature
        when the author genuinely authored axis_y.mirror (the multi-column
        auto-mirror default backs off whenever endpoint labels will render;
        see test_render_small_multiples.py's TestFacetRefusals for that path),
        so naming the field in the message is always accurate here.
        """
        from dbt_charts.core.compile.resolve import resolve
        from dbt_charts.core.diagnostics.chart_data import ChartDataError
        from dbt_charts.core.diagnostics.codes_render import ERR_MIRROR_ENDPOINT_LABELS
        from dbt_charts.core.render.chart.features.mirror_axis import (
            MirrorAxisFeature,
        )
        from dbt_charts.core.render.chart.spec import ChartSpec

        chart = AreaChart.model_validate(
            {"id": "t", "type": "area", "x": "month", "y": "value", "style": _MIRROR}
        )
        resolved = resolve(chart, _wide_data(), chart_style_context=_board()[1])
        # A real endpoint-label pane always carries the y encoding its rail
        # was positioned from — mirror checks for one before anything else
        # (no y encoding -> inert, never a collision error).
        composed = ChartSpec(
            mark="area",
            encoding={"y": {"field": "value", "type": "quantitative"}},
            endpoint_label_layout="right_pane",
        )
        with pytest.raises(ChartDataError, match="endpoint labels") as exc_info:
            MirrorAxisFeature().apply(
                composed, resolved, _DEFAULT_BOX, {resolved.query_name: _wide_data()}
            )
        assert exc_info.value.code is ERR_MIRROR_ENDPOINT_LABELS


def _compacting_data() -> list[dict[str, Any]]:
    """A 0-450k-shaped ladder — big enough to compact (six written-out
    integer digits at the extreme) and small enough to land in ANCHOR mode,
    matching the worked example the ruler tests already pin.
    """
    return [{"month": i, "value": i * 100_000} for i in range(6)]


class TestV2MirrorAxisRulerMechanism:
    """Regression: a single ``ResolvedRulerAxis`` bakes the mechanism for
    the PRIMARY axis's own resolved anchoring, and a start-anchored axis
    (VL's own per-orient default for whichever edge doesn't get an
    authored/away-side align) now bakes no ruler at all -- neither the
    primary nor the ghost paints a special labelExpr in that state.
    Regression for the old ghost-inherits-the-primary's-mechanism bug: a
    ``position: right`` primary (start-anchored) mirrored a ghost that
    rendered left/end-anchored but still painted the leading-pad JS built
    for the primary, silently losing its suffix-tick alignment -- the fix
    is that BOTH edges compute their own mechanism independently, and here
    that independent computation is "none" on both, not a wrongly-inherited
    one.
    """

    def test_right_primary_default_bakes_no_labelexpr_on_either_edge(self):
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "position": "right",
                        "labels": {"format": "$.3~s"},
                        "mirror": True,
                    },
                },
            }
        )
        spec = _v2_vl(chart, _compacting_data())
        exprs = _y_axis_key_by_orient(spec, "labelExpr")
        # The primary (right, start-anchored by VL's own default) bakes no
        # ruler, so no labelExpr. The mirror block that would build the
        # ghost's own ruler only fires when the primary has one, so the
        # ghost (left) gets none either -- not the primary's mechanism
        # inherited verbatim.
        assert exprs["right"] is None
        assert exprs["left"] is None

    def test_left_primary_default_bakes_no_labelexpr_on_either_edge(self):
        """The mirror image: a default (left) primary is end-anchored and
        keeps its reservation-free plain rendering; its right-edge ghost is
        start-anchored by VL's own default, which now bakes no ruler.
        """
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {"labels": {"format": "$.3~s"}, "mirror": True},
                },
            }
        )
        spec = _v2_vl(chart, _compacting_data())
        exprs = _y_axis_key_by_orient(spec, "labelExpr")
        assert exprs["left"] is None
        assert exprs["right"] is None

    def test_right_primary_mirror_on_non_tabular_font_does_not_raise(self):
        """A start-anchored primary now bakes no ruler at all, so it never
        reaches the tabular guard -- and the mirror block that would build
        the ghost's own ruler (and its own guard) never runs either, since
        it's gated on the primary having one. A non-tabular font is a
        non-event on this axis shape.
        """
        from dbt_charts.core.fonts import SOURCE_SERIF_4_FONT_FAMILY

        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "position": "right",
                        "labels": {
                            "format": "$.3~s",
                            "font": {"family": SOURCE_SERIF_4_FONT_FAMILY},
                        },
                        "mirror": True,
                    },
                },
            }
        )
        spec = _v2_vl(chart, _compacting_data())
        exprs = _y_axis_key_by_orient(spec, "labelExpr")
        assert exprs["right"] is None
        assert exprs["left"] is None

    def test_right_primary_case_wraps_ghost_the_same_as_primary(self):
        """Regression: the ghost's rebuilt labelExpr (mirror.ruler differs
        from the inherited copy's mechanism, same as the two tests above)
        dropped the ``inject_axis_label_case`` wrapper entirely -- it called
        ``inject_axis_numeral_expr`` alone, never the case wrap line.py's
        own build applies to the primary right after. With
        ``labels.font.case: upper`` authored, the primary rendered
        ``upper(...)`` and the ghost rendered the bare, lowercase-digit
        producer underneath -- the two edges of one shared scale disagreeing
        about the actual label TEXT, not just its alignment mechanism (the
        two tests above). Line chart: the only emitter that currently wires
        ``inject_axis_label_case`` onto the y-axis at all, so it is the one
        chart type where the primary's own build actually case-wraps and a
        silent ghost omission is observable.
        """
        chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "position": "right",
                        "labels": {
                            "format": "$.3~s",
                            "font": {"case": "upper"},
                        },
                        "mirror": True,
                    },
                },
            }
        )
        spec = _v2_vl(chart, _compacting_data())
        exprs = _y_axis_key_by_orient(spec, "labelExpr")
        assert exprs["right"] is not None and exprs["left"] is not None
        assert exprs["right"].startswith("upper(")
        assert exprs["left"].startswith("upper(")


class TestRenderAccurateAnchoring:
    """Regression: ``_build_ruler`` bakes its mechanism from a resolve-time
    anchoring that render can still change afterward, in two separate ways.
    Both are reachable from authored YAML with no mirror involved, and the
    third is the mirror-specific instance of the same root cause -- the
    anchoring the digit field is baked against must be the anchoring that
    actually renders.
    """

    def test_own_side_align_with_upper_case_bakes_the_mechanism_render_actually_uses(
        self,
    ):
        """``measure_axis_to_vl``'s ``_fallback_reversed_label_align`` drops
        an own-side ``labelAlign`` when ``label.font.case`` is upper/lower
        (VL prefers the injected, un-measurable ``labelExpr`` over
        ``format`` then) -- flipping this left-orient, ``align: left``
        (start-anchored, own-side) axis to VL's away-side default for a
        left-orient axis, which is end-anchored, at render. ``_build_ruler`` used to bake
        the digit field from the PRE-fallback ``align: left`` alone: each
        tick would then carry a different count of leading U+2007s while
        the (now ``labelAlign``-less, VL-default end-anchored) text shared
        its right edge on its own -- padding on the far side of an
        end-anchored label moves nothing, so the digits would scatter
        instead of aligning. The fix must bake the reservation instead,
        matching what render actually anchors.
        """
        chart = LineChart.model_validate(
            {
                "id": "t",
                "type": "line",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "position": "left",
                        "labels": {
                            "format": "$.3~s",
                            "align": "left",
                            "font": {"case": "upper"},
                        },
                    },
                },
            }
        )
        spec = _v2_vl(chart, _compacting_data())
        axis = _y_axis_defs(spec)[0]
        # The fallback fired: render actually ships no explicit labelAlign
        # (VL's own away-side default takes over), confirming this case
        # really does hit the render-side deletion this test is about.
        assert "labelAlign" not in axis
        # ...so the baked mechanism must be the reservation (no pad()-based
        # digit field in the expression), not the digit field that
        # the pre-fallback "align: left" alone would have picked.
        assert "pad(" not in axis["labelExpr"]

    def test_outward_align_mirror_bakes_no_ruler_on_either_edge(self):
        """Regression: ``mirror.ruler`` used to derive the ghost's
        anchoring by re-resolving the AUTHORED ``inward``/``outward``
        directive against the ghost's OWN (opposite) edge
        (``_resolve_own_side_align(axis.labels.align, opposite_edge)``).
        Render does no such thing -- ``MirrorAxisFeature`` copies the
        PRIMARY's already-resolved ``labelAlign`` onto the ghost VERBATIM
        (``dict(primary_axis)``), never re-deriving it. This theme's
        default (unset ``position``) primary edge is "right"; ``outward``
        on a right-edge primary resolves to ``"left"`` (away from the
        plot, own-side for the OPPOSITE edge) -- start-anchored, which now
        bakes no ruler at all. Since the ghost inherits that literal
        ``"left"`` string unchanged regardless of its own left edge, it
        renders start-anchored too, and also bakes no ruler -- both edges
        agree (neither wrongly inherits a mechanism the other doesn't
        have), which is the invariant this test protects.
        """
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "labels": {"format": "$.3~s", "align": "outward"},
                        "mirror": True,
                    },
                },
            }
        )
        spec = _v2_vl(chart, _compacting_data())
        exprs = _y_axis_key_by_orient(spec, "labelExpr")
        # Primary (default right edge, align "outward" -> away from the
        # plot -> "left" -> start-anchored) bakes no ruler. The ghost
        # inherits the SAME literal "left" verbatim -> ALSO start-anchored
        # -> also no ruler, not a mismatched mechanism copied from the
        # primary.
        assert exprs["right"] is None
        assert exprs["left"] is None

    def test_area_primary_and_mirror_ghost_share_case_wrapped_text(self):
        """Regression: ``compose_axis_label_expr`` -- the one-path case/
        values-filter wrapper chain added to fix the round-3 CRITICAL --
        only reached ``line.py``. ``area.py``/``bar.py``/``scatter.py``
        still called ``inject_axis_numeral_expr`` alone for their own
        y-axis build, so the mirror ghost (which always rebuilds through
        the full chain once ``mirror.ruler`` bakes) case-wrapped its text
        while the primary did not -- the exact bug fixed last round,
        reintroduced in the opposite direction on every non-line chart.
        """
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "value",
                "style": {
                    "axis_y": {
                        "position": "right",
                        "labels": {
                            "format": "$.3~s",
                            "font": {"case": "upper"},
                        },
                        "mirror": True,
                    },
                },
            }
        )
        spec = _v2_vl(chart, _compacting_data())
        exprs = _y_axis_key_by_orient(spec, "labelExpr")
        assert exprs["right"] is not None and exprs["left"] is not None
        assert exprs["right"].startswith("upper(")
        assert exprs["left"].startswith("upper(")


def _v2_svg(norm: Chart, data: list[dict[str, Any]], board: Any | None = None) -> str:
    """Render a chart through the full V2 pipeline to SVG via vl-convert."""
    from dbt_charts.core.render.chart.vega_lite import render_chart

    board_rs, board_ctx = board if board is not None else _board()
    return render_chart(
        norm, board_rs, board_ctx, data, format="svg", width=600, height=300
    )


def _svg_text_count(svg: str, label: str) -> int:
    """Count SVG <text> elements whose text content equals label exactly."""
    root = ET.fromstring(svg)
    return sum(1 for t in root.iter(f"{{{_SVG_NS}}}text") if t.text == label)


class TestMirrorAxisTitleSuppression:
    """Regression: mirrored y-axis must not leak the measure's display name
    as a stray axis-title text node in the rendered SVG.

    Root cause: ``MirrorAxisFeature.apply`` built the ghost axis with
    ``ghost_axis.pop("title", None)``, which removes the key entirely rather
    than nulling it. With no ``title`` key, Vega-Lite falls back to
    ``encoding.y.title`` on the ghost layer and paints it as a visible rotated
    label. Setting ``ghost_axis["title"] = None`` keeps an explicit null.

    Both entry points reach the same code path:
    - Authored ``style.axis_y.mirror: true`` via ``MirrorAxisFeature``
    - ``multiples: {columns: ...}`` via ``_apply_multiples_mirror`` at resolve time
    """

    @pytest.fixture(autouse=True)
    def reset(self):
        reset_config()
        yield
        reset_config()

    def test_authored_mirror_renders_zero_axis_title_text_nodes(self):
        """style.axis_y.mirror: true — no stray measure-axis title text in the SVG."""
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "signups",
                "style": {"axis_y": {"mirror": True}},
            }
        )
        data = [{"month": i, "signups": 100 + i * 5} for i in range(6)]
        svg = _v2_svg(chart, data)
        assert _svg_text_count(svg, "Signups") == 0

    def test_multiples_columns_auto_mirror_renders_zero_axis_title_text_nodes(self):
        """multiples: {columns: ...} auto-mirror — same zero-title assertion on SVG."""
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "signups",
                "multiples": {"columns": "region"},
            }
        )
        data = [
            {"month": i, "region": r, "signups": 100 + i * 5 + j * 50}
            for j, r in enumerate(["East", "West"])
            for i in range(4)
        ]
        svg = _v2_svg(chart, data)
        assert _svg_text_count(svg, "Signups") == 0

    def test_authored_mirror_aria_labels_still_carry_field_name(self):
        """After fixing the axis title leak, data marks still carry 'Signups'
        in their aria-labels — the tooltip/accessibility field label is intact.

        The fix sets encoding.y.title: null on the BASE (shared) encoding only.
        The LAYER encodings retain title: 'Signups', so VL's aria-label on each
        mark still reads 'Signups: <value>'.
        """
        chart = AreaChart.model_validate(
            {
                "id": "t",
                "type": "area",
                "x": "month",
                "y": "signups",
                "style": {"axis_y": {"mirror": True}},
            }
        )
        data = [{"month": i, "signups": 100 + i * 5} for i in range(6)]
        svg = _v2_svg(chart, data)
        # Aria-label attributes (not text content) still carry "Signups: <value>"
        assert "signups: 100" in svg
