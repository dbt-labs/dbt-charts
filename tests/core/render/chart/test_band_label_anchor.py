"""TDD tests: value labels anchor to the band edge on a band-width mark.

``curve: step`` on a categorical x draws a full-band-width plateau
(``step_band.py``). Its value label draws from the layer's own one row per
category, so ``left``/``right`` used to anchor at the band CENTER and paint the
caption on top of the plateau. Both now resolve against the band EDGE, keeping
the same house 4px clearance the datum-relative positions use.

The anchor is an ``xOffset`` value expression (``bandwidth('x')``), not a
pixel constant, so it tracks the real band width at every board width and
category count — the render tests below pin that at two different band widths,
since a fixed-offset bug passes at whatever width it was written for.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
import vl_convert as vlc

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.features.value_labels import _LINE_POS_MAP
from dbt_charts.core.render.chart.session import BoardRenderSession
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.step_band import STEP_BAND_EDGE_FIELD

_QUERY_REGISTRY: dict[str, Any] = {"q": SqlQuery(sql="SELECT 1", source="test")}

MONTHS = ["Jan", "Feb", "Mar", "Apr"]
ROWS = [
    {"month": m, "actual": 30 + 5 * i, "target": 45, "cap": "Pace"}
    for i, m in enumerate(MONTHS)
]


@pytest.fixture(autouse=True)
def reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _spec(
    chart_def: dict[str, Any],
    rows: list[dict[str, Any]] = ROWS,
    width: float = 600.0,
) -> dict[str, Any]:
    board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
    compiled = normalize_chart("c", chart_def, _QUERY_REGISTRY, sources={})
    resolved = resolve(compiled, rows, chart_style_context=board_ctx)
    session = BoardRenderSession.create(board_rs)
    return session.finalize_vl(
        session.emit_chart(
            resolved, RenderBox(width=width, height=300.0), {resolved.query_name: rows}
        )
    )


def _overlay(position: str | None, **label_overrides: Any) -> dict[str, Any]:
    """A bar base with a band-step line overlay carrying its own value labels."""
    labels: dict[str, Any] = {"visible": True, "field": "cap", **label_overrides}
    if position is not None:
        labels["position"] = position
    return {
        "query": "q",
        "type": "bar",
        "x": "month",
        "y": "actual",
        "layers": [
            {
                "type": "line",
                "x": "month",
                "y": "target",
                "label": "Target",
                "style": {
                    "marks": {
                        "line": {
                            "curve": "step",
                            "connect": False,
                            "labels": labels,
                        }
                    }
                },
            }
        ],
    }


def _base_line(position: str, curve: str = "step") -> dict[str, Any]:
    return {
        "query": "q",
        "type": "line",
        "x": "month",
        "y": "target",
        "style": {
            "marks": {
                "line": {
                    "curve": curve,
                    "connect": False,
                    "labels": {
                        "visible": True,
                        "field": "cap",
                        "position": position,
                    },
                }
            }
        },
    }


def _text_layers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Every value-label text layer in the spec, in paint order."""
    out: list[dict[str, Any]] = []
    for layer in spec.get("layer", []):
        mark = layer.get("mark")
        if isinstance(mark, dict) and mark.get("type") == "text":
            out.append(layer)
    return out


def _only_text_layer(spec: dict[str, Any]) -> dict[str, Any]:
    layers = _text_layers(spec)
    assert len(layers) == 1, f"expected one text layer, got {len(layers)}"
    return layers[0]


# ── Rendered geometry: the anchor must clear the band at any band width ──────


def _band_geometry(svg: str) -> tuple[list[float], float]:
    """(x-axis band centers, band step) read off the rendered category ticks.

    The step equals ``bandwidth('x')`` only because the theme these fixtures
    resolve against sets the band scale's padding to 0. Raise that default and
    the step exceeds the true band width, so the assertions below overshoot the
    band edge and fail for a reason unrelated to anchoring.
    """
    axis = re.search(
        r'<g class="mark-text role-axis-label[^"]*"[^>]*>(.*?)</g>', svg, re.S
    )
    assert axis is not None
    centers = [
        float(x) for x in re.findall(r'transform="translate\(([-\d.]+),', axis.group(1))
    ]
    assert len(centers) >= 2
    return centers, centers[1] - centers[0]


def _text_mark_groups(svg: str) -> list[list[float]]:
    """Rendered x of every value-label text, one list per text layer."""
    return [
        [
            float(x)
            for x in re.findall(r'transform="translate\(([-\d.]+),', group.group(1))
        ]
        for group in re.finditer(
            r'<g class="mark-text role-mark[^"]*"[^>]*>(.*?)</g>', svg, re.S
        )
    ]


def _label_positions(svg: str) -> dict[str, float]:
    """Rendered x of each value-label text, keyed by its category."""
    positions: dict[str, float] = {}
    for group in re.finditer(
        r'<g class="mark-text role-mark[^"]*"[^>]*>(.*?)</g>', svg, re.S
    ):
        for text in re.finditer(r"<text ([^>]*)>", group.group(1)):
            attrs = text.group(1)
            label = re.search(r'aria-label="Month: ([^;"]+)', attrs)
            x = re.search(r'transform="translate\(([-\d.]+),', attrs)
            if label is not None and x is not None:
                positions[label.group(1)] = float(x.group(1))
    return positions


def _render(chart_def: dict[str, Any], rows: list[dict[str, Any]], width: float) -> str:
    return vlc.vegalite_to_svg(json.dumps(_spec(chart_def, rows, width)))


@pytest.mark.parametrize(
    ("categories", "width"),
    [(4, 600.0), (9, 1100.0)],
)
def test_right_label_clears_the_band_trailing_edge_at_any_band_width(
    categories: int, width: float
) -> None:
    """``right`` sits outside the band, not 4px right of the band center.

    Two band widths: a fixed-pixel offset tuned for one of them fails the other.
    """
    months = [f"M{i}" for i in range(categories)]
    rows = [
        {"month": m, "actual": 30 + i, "target": 45, "cap": "Pace"}
        for i, m in enumerate(months)
    ]
    svg = _render(_overlay("right"), rows, width)
    centers, band = _band_geometry(svg)
    positions = _label_positions(svg)
    # The last band falls back to `top` (nothing to its right) — check the rest.
    for month, center in zip(months[:-1], centers[:-1], strict=True):
        assert positions[month] >= center + band / 2, (
            f"{month}: label at {positions[month]} is inside the band "
            f"(trailing edge {center + band / 2}, band width {band})"
        )


@pytest.mark.parametrize(
    ("categories", "width"),
    [(4, 600.0), (9, 1100.0)],
)
def test_left_label_clears_the_band_leading_edge_at_any_band_width(
    categories: int, width: float
) -> None:
    months = [f"M{i}" for i in range(categories)]
    rows = [
        {"month": m, "actual": 30 + i, "target": 45, "cap": "Pace"}
        for i, m in enumerate(months)
    ]
    svg = _render(_overlay("left"), rows, width)
    centers, band = _band_geometry(svg)
    positions = _label_positions(svg)
    # The first band falls back to `top` (nothing to its left) — check the rest.
    for month, center in zip(months[1:], centers[1:], strict=True):
        assert positions[month] <= center - band / 2, (
            f"{month}: label at {positions[month]} is inside the band "
            f"(leading edge {center - band / 2}, band width {band})"
        )


# ── Spec shape ───────────────────────────────────────────────────────────────


def test_right_anchors_to_the_band_trailing_edge_and_keeps_the_house_offset() -> None:
    layers = _text_layers(_spec(_overlay("right")))
    anchored = next(lyr for lyr in layers if "xOffset" in lyr["encoding"])
    assert anchored["encoding"]["xOffset"] == {"value": {"expr": "bandwidth('x')"}}
    assert anchored["mark"]["align"] == "left"
    assert anchored["mark"]["dx"] == 4


def test_left_anchors_to_the_band_leading_edge_and_keeps_the_house_offset() -> None:
    layers = _text_layers(_spec(_overlay("left")))
    anchored = next(lyr for lyr in layers if "xOffset" in lyr["encoding"])
    assert anchored["encoding"]["xOffset"] == {"value": {"expr": "0"}}
    assert anchored["mark"]["align"] == "right"
    assert anchored["mark"]["dx"] == -4


def test_authored_dx_still_nudges_the_band_anchor() -> None:
    layers = _text_layers(_spec(_overlay("right", dx=12)))
    anchored = next(lyr for lyr in layers if "xOffset" in lyr["encoding"])
    assert anchored["encoding"]["xOffset"] == {"value": {"expr": "bandwidth('x')"}}
    assert anchored["mark"]["dx"] == 12


@pytest.mark.parametrize("position", ["top", "bottom", "middle"])
def test_vertical_positions_on_a_band_mark_are_untouched(position: str) -> None:
    """Band width is horizontal — these three never move, and gain no xOffset.

    Compared against the same chart with a non-band curve, which is what
    "byte-identical to a datum-anchored label" actually means.
    """
    plain = _overlay(position)
    plain["layers"][0]["style"]["marks"]["line"]["curve"] = "linear"
    layer = _only_text_layer(_spec(_overlay(position)))
    assert "xOffset" not in layer["encoding"]
    assert layer == _only_text_layer(_spec(plain))


@pytest.mark.parametrize("position", ["left", "right", "top", "middle"])
def test_non_band_line_labels_are_untouched(position: str) -> None:
    """A plain (non-step) curve is not a band mark — no anchoring, no split."""
    chart = _overlay(position)
    chart["layers"][0]["style"]["marks"]["line"]["curve"] = "linear"
    layer = _only_text_layer(_spec(chart))
    assert "xOffset" not in layer["encoding"]
    assert "transform" not in layer


# ── Domain-edge fallback ─────────────────────────────────────────────────────


def _filters(layer: dict[str, Any]) -> list[str]:
    return [t["filter"] for t in layer.get("transform", []) if "filter" in t]


def test_right_falls_back_to_top_on_the_last_band() -> None:
    """The trailing band has no room outside it — the caption goes above.

    Never to the opposite edge, which would park it over the previous
    category's band and read as labeling the wrong period.
    """
    layers = _text_layers(_spec(_overlay("right")))
    assert len(layers) == 2
    anchored, fallback = layers
    assert _filters(anchored) == ['datum["month"] !== "Apr"']
    assert _filters(fallback) == ['datum["month"] === "Apr"']
    assert "xOffset" not in fallback["encoding"]
    assert fallback["mark"]["baseline"] == "bottom"
    assert fallback["mark"]["dy"] == -4
    assert "align" not in fallback["mark"]


def test_left_falls_back_to_top_on_the_first_band() -> None:
    layers = _text_layers(_spec(_overlay("left")))
    assert len(layers) == 2
    anchored, fallback = layers
    assert _filters(anchored) == ['datum["month"] !== "Jan"']
    assert _filters(fallback) == ['datum["month"] === "Jan"']
    assert fallback["mark"]["baseline"] == "bottom"


def test_fallback_label_renders_centered_on_its_own_band() -> None:
    svg = _render(_overlay("right"), ROWS, 600.0)
    centers, band = _band_geometry(svg)
    positions = _label_positions(svg)
    assert abs(positions["Apr"] - centers[-1]) <= 1.0


def test_no_fallback_layer_when_no_labeled_row_sits_at_the_domain_edge() -> None:
    """The motivating case: one pace tick, mid-domain. One layer, no filters."""
    rows = [dict(r) for r in ROWS]
    for row in rows:
        if row["month"] != "Feb":
            row["cap"] = None
            row["target"] = None
    layer = _only_text_layer(_spec(_overlay("right"), rows))
    assert "xOffset" in layer["encoding"]
    assert _filters(layer) == []


# ── Base chart (the band step is the chart's own curve) ──────────────────────


def test_base_band_step_draws_one_label_per_band() -> None:
    """The base chart's label layer inherits the band-DOUBLED rows.

    Without a dedupe it paints two captions per band, one at each plateau end.
    """
    layer = _only_text_layer(_spec(_base_line("middle")))
    assert _filters(layer) == [f'datum["{STEP_BAND_EDGE_FIELD}"] === 0']
    assert layer["encoding"]["xOffset"] == {"value": {"expr": "bandwidth('x') / 2"}}


def test_base_band_step_right_label_clears_the_band() -> None:
    """One caption per band, each outside its own band's trailing edge.

    The base chart's text marks carry no aria-label, so read the two layers
    positionally: the anchored one draws every band but the last, in row order.
    """
    svg = vlc.vegalite_to_svg(json.dumps(_spec(_base_line("right"))))
    centers, band = _band_geometry(svg)
    anchored, fallback = _text_mark_groups(svg)
    assert len(anchored) == len(MONTHS) - 1
    for x, center in zip(anchored, centers[:-1], strict=True):
        assert x >= center + band / 2
    assert abs(fallback[0] - centers[-1]) <= 1.0


def test_base_non_band_curve_keeps_its_plain_label_layer() -> None:
    layer = _only_text_layer(_spec(_base_line("right", curve="linear")))
    assert "xOffset" not in layer["encoding"]
    assert _filters(layer) == []


# ── The edge fallback must name the band that actually renders at the edge ───


def test_sorted_x_moves_the_fallback_to_the_rendered_trailing_band() -> None:
    """An authored sort reorders the bands, so it reorders which one is last.

    Reading query order instead fires the fallback on a mid-domain band and
    leaves the real trailing caption anchored a full band past the plot edge.
    """
    chart = _overlay("right")
    chart["sort"] = {"by": "actual", "order": "desc"}
    anchored, fallback = _text_layers(_spec(chart))
    # actual ascends Jan..Apr, so descending order renders Apr..Jan.
    assert _filters(anchored) == ['datum["month"] !== "Jan"']
    assert _filters(fallback) == ['datum["month"] === "Jan"']


def test_sorted_x_renders_every_anchored_caption_inside_the_plot() -> None:
    chart = _overlay("right")
    chart["sort"] = {"by": "actual", "order": "desc"}
    svg = _render(chart, ROWS, 600.0)
    centers, band = _band_geometry(svg)
    positions = _label_positions(svg)
    plot_right = centers[-1] + band / 2
    for month, x in positions.items():
        assert x <= plot_right, (
            f"{month}: caption at {x} is past the plot ({plot_right})"
        )
    # The visually trailing band is the one that fell back, and it sits on its
    # own band center rather than outside it.
    assert abs(positions["Jan"] - centers[-1]) <= 1.0


def test_labeled_temporal_x_filters_against_the_canonicalized_value() -> None:
    """The emitter rewrites a labeled-temporal x before the label layer sees it.

    A filter literal built from the raw query rows matches nothing, so the
    anchored layer keeps the trailing band and the fallback renders empty.
    """
    rows = [{"month": f"Q{i + 1} 2024", "target": 45, "cap": "Pace"} for i in range(4)]
    layers = _text_layers(_spec(_base_line("right"), rows))
    assert len(layers) == 2
    anchored, fallback = layers
    trailing = _filters(fallback)[0]
    assert trailing != 'datum["month"] === "Q4 2024"'
    # Whatever the emitter canonicalized it to, the two layers must partition
    # the rows — the anchored one excludes exactly what the fallback selects.
    assert _filters(anchored)[0] == trailing.replace("===", "!==")
    assert (
        len(
            _text_mark_groups(
                vlc.vegalite_to_svg(json.dumps(_spec(_base_line("right"), rows)))
            )
        )
        == 2
    )


def test_labeled_temporal_x_renders_one_caption_per_band() -> None:
    rows = [{"month": f"Q{i + 1} 2024", "target": 45, "cap": "Pace"} for i in range(4)]
    svg = vlc.vegalite_to_svg(json.dumps(_spec(_base_line("right"), rows)))
    centers, band = _band_geometry(svg)
    anchored, fallback = _text_mark_groups(svg)
    assert len(anchored) == len(rows) - 1
    assert len(fallback) == 1
    for x, center in zip(anchored, centers[:-1], strict=True):
        assert center + band / 2 <= x <= centers[-1] + band / 2
    assert abs(fallback[0] - centers[-1]) <= 1.0


def test_authored_dx_does_not_follow_the_caption_onto_the_top_fallback() -> None:
    """dx was a nudge along the horizontal anchor; the fallback is vertical."""
    anchored, fallback = _text_layers(_spec(_overlay("right", dx=12)))
    assert anchored["mark"]["dx"] == 12
    assert "dx" not in fallback["mark"]


def test_authored_dy_does_not_overwrite_the_fallback_clearance() -> None:
    """`top` carries its own -4 clearance in dy, so an authored dy erases it.

    The anchored captions keep the nudge; the one that fell back must stay
    4px ABOVE its datum, not `dy` px below it — inside the plateau.
    """
    anchored, fallback = _text_layers(_spec(_overlay("right", dy=12)))
    assert anchored["mark"]["dy"] == 12
    assert fallback["mark"]["dy"] == _LINE_POS_MAP["top"]["dy"]


# ── The area family is wired to the same anchor ──────────────────────────────


def _area_overlay(position: str) -> dict[str, Any]:
    chart = _overlay(position)
    chart["layers"][0]["type"] = "area"
    marks = chart["layers"][0]["style"]["marks"]
    marks["area"] = {"curve": "step"}
    marks["line"] = {"labels": marks["line"]["labels"]}
    return chart


def test_area_overlay_layer_anchors_to_the_band_edge() -> None:
    anchored, fallback = _text_layers(_spec(_area_overlay("right")))
    assert anchored["encoding"]["xOffset"] == {"value": {"expr": "bandwidth('x')"}}
    assert _filters(fallback) == ['datum["month"] === "Apr"']


def test_area_base_chart_anchors_to_the_band_edge() -> None:
    chart = _base_line("right")
    chart["type"] = "area"
    chart["style"]["marks"] = {
        "area": {"curve": "step"},
        "line": {"labels": chart["style"]["marks"]["line"]["labels"]},
    }
    anchored, fallback = _text_layers(_spec(chart))
    assert anchored["encoding"]["xOffset"] == {"value": {"expr": "bandwidth('x')"}}
    assert f'datum["{STEP_BAND_EDGE_FIELD}"] === 0' in _filters(anchored)
    assert _filters(fallback)[0] == 'datum["month"] === "Apr"'


# ── The one shape that reaches the undoubled + pinned-domain branches ────────


def _base_step_with_layer() -> dict[str, Any]:
    """A band-step base chart that also carries an authored overlay layer.

    The only shape where the band transform stays on the base's OWN sub-layer:
    the outer spec keeps undoubled rows and no ``xOffset``, and the overlay's
    domain reconciliation pins ``x.scale.domain`` before the label feature
    runs. Every other fixture here is either a layer-less base (doubled rows,
    no pinned domain) or a bar base whose step line is the overlay.

    The layer authors its own query and x so it contributes a category the
    base lacks — that extra band is what the pinned domain adds, and it is the
    one the caption at the trailing edge has to name.
    """
    chart = _base_line("right")
    chart["layers"] = [
        {
            "type": "bar",
            "query": "extra",
            "x": "month",
            "y": "actual",
            "label": "Actual",
        }
    ]
    return chart


_EXTRA_ROWS = [{"month": m, "actual": 20 + i} for i, m in enumerate([*MONTHS, "May"])]


def _spec_with_extra_layer_query(chart: dict[str, Any]) -> dict[str, Any]:
    board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
    registry: dict[str, Any] = {
        "q": SqlQuery(sql="SELECT 1", source="test"),
        "extra": SqlQuery(sql="SELECT 2", source="test"),
    }
    compiled = normalize_chart("c", chart, registry, sources={})
    resolved = resolve(compiled, ROWS, chart_style_context=board_ctx)
    session = BoardRenderSession.create(board_rs)
    return session.finalize_vl(
        session.emit_chart(
            resolved,
            RenderBox(width=600.0, height=300.0),
            {"q": ROWS, "extra": _EXTRA_ROWS},
        )
    )


def test_base_step_with_layers_does_not_dedupe_rows_it_never_doubled() -> None:
    """The outer rows are undoubled here, so a dedupe filter matches nothing.

    ``__step_band_pos__`` is absent from every row, and ``undefined === 0`` is
    false — a filter added here silently erases every caption on the chart.
    """
    layers = _text_layers(_spec_with_extra_layer_query(_base_step_with_layer()))
    for layer in layers:
        assert not any(STEP_BAND_EDGE_FIELD in f for f in _filters(layer))


def test_base_step_with_layers_reads_the_pinned_domain_for_its_edge() -> None:
    """The overlay widens the domain, so the base's own last row is not the edge.

    The trailing band is the layer-only ``May``, which the base draws no
    caption on — so nothing splits, and ``Apr`` stays anchored to its band
    edge. Predicting the domain from the base's own rows instead names ``Apr``
    as the edge and demotes that caption to ``top`` one band early.
    """
    spec = _spec_with_extra_layer_query(_base_step_with_layer())
    assert spec["encoding"]["x"]["scale"]["domain"] == [*MONTHS, "May"]
    layer = _only_text_layer(spec)
    assert _filters(layer) == []
    assert layer["encoding"]["xOffset"] == {"value": {"expr": "bandwidth('x')"}}


def test_base_step_with_layers_renders_every_caption() -> None:
    svg = vlc.vegalite_to_svg(
        json.dumps(_spec_with_extra_layer_query(_base_step_with_layer()))
    )
    centers, band = _band_geometry(svg)
    (captions,) = _text_mark_groups(svg)
    assert len(captions) == len(MONTHS)
    for x, center in zip(sorted(captions), centers[: len(MONTHS)], strict=True):
        assert x >= center + band / 2


def test_layer_authoring_its_own_x_filters_on_the_positioning_column() -> None:
    """The label sublayer inherits the base's x, so that is what it filters on.

    Its own ``x:`` column widens the shared domain, but keying the edge filter
    on it tests a column the caption is not positioned by.
    """
    chart = _overlay("right")
    layer = chart["layers"][0]
    layer["x"] = "phase"
    layer["query"] = "extra"
    board_rs, board_ctx = resolve_style_and_context(get_theme_style("clarity"))
    registry: dict[str, Any] = {
        "q": SqlQuery(sql="SELECT 1", source="test"),
        "extra": SqlQuery(sql="SELECT 2", source="test"),
    }
    layer_rows = [{"phase": m, "target": 45, "cap": "Pace", "month": m} for m in MONTHS]
    compiled = normalize_chart("c", chart, registry, sources={})
    resolved = resolve(compiled, ROWS, chart_style_context=board_ctx)
    session = BoardRenderSession.create(board_rs)
    spec = session.finalize_vl(
        session.emit_chart(
            resolved,
            RenderBox(width=600.0, height=300.0),
            {"q": ROWS, "extra": layer_rows},
        )
    )
    for layer_spec in _text_layers(spec):
        assert all("phase" not in f for f in _filters(layer_spec))


def test_band_split_composes_with_the_house_register_calculate() -> None:
    """No `labels.field`, so the caption is the measure through house format.

    That routes through a ``calculate`` transform, which the band filters have
    to sit ahead of — the ordering ``_prepend_filter`` exists for.
    """
    chart = _overlay("right", format="number")
    del chart["layers"][0]["style"]["marks"]["line"]["labels"]["field"]
    anchored, fallback = _text_layers(_spec(chart))
    for layer in (anchored, fallback):
        kinds = [next(iter(t)) for t in layer["transform"]]
        assert kinds[0] == "filter", kinds
        assert "calculate" in kinds, kinds
    svg = vlc.vegalite_to_svg(json.dumps(_spec(chart)))
    assert sum(len(g) for g in _text_mark_groups(svg)) == len(MONTHS)


def test_band_chart_with_no_rows_does_not_raise() -> None:
    """`if not domain` is load-bearing — indexing an empty domain is IndexError."""
    assert _text_layers(_spec(_overlay("right"), [])) != []
