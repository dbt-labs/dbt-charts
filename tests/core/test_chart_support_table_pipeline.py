"""End-to-end pipeline tests for chart.support_table.

Goes compile → resolve → render and asserts the Vega-Lite output shape.
Complements the unit tests in tests/core/compile/test_support_table_attachment.py
by confirming the primitive actually threads through the public renderer.
"""

from __future__ import annotations

import datetime
import importlib
import xml.etree.ElementTree as ET
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.support_table_attachment import (
    DRAWN_INDEX_FIELD,
    StripAnchor,
    _entry_numerals,
    _pin_sorted_category_domain,
    resolved_pad_font_family,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec, render_chart
from dbt_charts.core.render.errors import RenderError

from .conftest import chart_pane

_BOARD_STYLE = resolve_style(get_theme_style())
_BOARD_CONTEXT = resolve_chart_style_context(get_theme_style())


def _render_v2_spec(
    chart: Chart,
    data: list[dict[str, Any]],
    *,
    width: float,
    height: float,
    monkeypatch: Any,
    padding: dict[str, int | float] | None = None,
    datasets: dict[str | None, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Render *chart* through the v2 path and return the captured VL spec dict."""
    board_style = resolve_style(get_theme_style(get_default_theme_name()))
    board_context = resolve_chart_style_context(
        get_theme_style(get_default_theme_name())
    )
    vl_module = importlib.import_module("dbt_charts.core.render.chart.vega_lite")
    captured: dict[str, Any] = {}

    def _capture(_chart_id: str, renderer: str, spec: dict[str, Any]) -> None:
        captured[renderer] = spec

    monkeypatch.setattr(vl_module, "_trace_vl_spec", _capture)
    render_chart(
        chart,
        board_style,
        board_context,
        data,
        format="svg",
        width=width,
        height=height,
        padding=padding,
        datasets=datasets,
    )
    spec = captured.get("v2")
    assert spec is not None, "expected v2 renderer to fire; check chart.v2"
    return spec


def _render_v2_svg_text_nodes(
    chart: Chart,
    data: list[dict[str, Any]],
    *,
    width: float,
    height: float,
) -> list[str]:
    """Render *chart* through the REAL pipeline (compile -> resolve -> render
    -> vl_convert's actual Vega expression evaluator) and return every
    painted ``<text>`` node's content, in document order.

    Ground truth for "what does Vega actually paint", independent of any
    Python re-implementation of a Vega expression's semantics -- vl_convert
    is the real Rust/Vega runtime, not this codebase.
    """
    board_style = resolve_style(get_theme_style(get_default_theme_name()))
    board_context = resolve_chart_style_context(
        get_theme_style(get_default_theme_name())
    )
    svg = render_chart(
        chart,
        board_style,
        board_context,
        data,
        format="svg",
        width=width,
        height=height,
    )
    root = ET.fromstring(svg)
    return [
        "".join(el.itertext()) for el in root.iter("{http://www.w3.org/2000/svg}text")
    ]


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config()
    yield
    reset_config()


def _compiled_bar_with_support_table(entries):
    """Build a vertical-bar Chart with a support_table block, bypassing YAML parsing.

    Orientation is pinned to vertical so the strip renders as rows above/below
    the plot (see _compiled_bar_horizontal_with_support_table for the
    column-block counterpart); "month" is a categorical string x that would
    otherwise auto-resolve to horizontal.
    """
    flat = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": entries},
            "style": {"orientation": "vertical"},
        }
    )
    return flat


def _compiled_bar_horizontal_with_support_table(entries, style=None):
    """Build a horizontal-bar Chart with a support_table block.

    Horizontal bars swap the category axis onto y — support_table then
    renders as value columns beside the plot instead of rows above/below it.
    """
    chart_style = {"orientation": "horizontal"}
    if style:
        chart_style.update(style)
    flat = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": entries},
            "style": chart_style,
        }
    )
    return flat


_HBAR_DATA = [
    {"region": "East", "revenue": 100.0},
    {"region": "West", "revenue": 200.0},
    {"region": "North", "revenue": 150.0},
]


_SAMPLE_DATA = [
    {"month": "Jan", "revenue": 100.0},
    {"month": "Feb", "revenue": 200.0},
    {"month": "Mar", "revenue": 150.0},
]

_TEMPORAL_DATA = [
    {"date": "2024-01-01", "revenue": 100.0},
    {"date": "2024-02-01", "revenue": 200.0},
    {"date": "2024-03-01", "revenue": 150.0},
]


def _compiled_line_temporal_with_support_table(entries):
    flat = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "line",
            "x": "date",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": entries},
        }
    )
    return flat


def _compiled_area_temporal_with_support_table(entries):
    flat = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "area",
            "x": "date",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": entries},
        }
    )
    return flat


def test_render_pipeline_svg_width_overhead_independent_of_requested_width():
    # Pinning the autosize:pad trade-off documented in attach_support_table:
    # the outer SVG is wider than spec.width by axis-y label + padding
    # overhead, but that overhead is independent of the requested width.
    # Compare two requests to assert the contract — a relative test
    # survives vl_convert font-metric updates and theme tweaks that an
    # absolute-bound test would flip on.
    import re

    pytest.importorskip("vl_convert")
    import vl_convert as vlc

    def overhead(width: int) -> float:
        chart = _compiled_bar_with_support_table(
            [{"source": "revenue", "format": "$.2s"}]
        )
        _rc = resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
        spec = generate_vega_lite_spec(chart, _SAMPLE_DATA, width=width, height=200)
        svg = vlc.vegalite_to_svg(spec)
        m = re.search(r'<svg[^>]*?width="([0-9.]+)"', svg)
        assert m is not None
        return float(m.group(1)) - width

    over_400 = overhead(400)
    over_1200 = overhead(1200)
    assert over_400 > 0, "autosize:pad always overshoots by axis overhead"
    assert abs(over_1200 - over_400) < 5, (
        f"overhead must be width-independent; got {over_400} at 400 vs "
        f"{over_1200} at 1200"
    )


def test_render_pipeline_with_support_table_synthesizes_height_when_only_width_given():
    # Optional callers (warnings detector, diagnostic scripts) reach the
    # public API with width=None,height=None — we don't want to drop those
    # charts. When a width is supplied but no height, attach_support_table
    # synthesizes a height from charts_style.aspect_ratio clamped to
    # [min_height, max_height]. That gives pixel-y placement an anchor
    # without forcing every caller to know the resolved chart sizing.
    chart = _compiled_bar_with_support_table([{"source": "revenue"}])
    _rc = resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _SAMPLE_DATA, width=400, height=None)
    assert isinstance(spec["height"], (int, float))
    assert spec["height"] > 0


def test_render_pipeline_with_support_table_falls_back_to_preferred_width_anchor():
    # generate_vega_lite_spec's own width resolution (preferred_chart_width)
    # is the single place an omitted width becomes a number, upstream of the
    # support_table strip — width and height both absent no longer raises; it
    # anchors on the chart family's theme preferred_width instead.
    from dbt_charts.core.compile.resolve import preferred_chart_width

    chart = _compiled_bar_with_support_table([{"source": "revenue"}])
    _rc = resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _SAMPLE_DATA, width=None, height=None)
    size_target = spec["hconcat"][0] if "hconcat" in spec else spec
    assert size_target["width"] == preferred_chart_width(chart, _BOARD_CONTEXT)


def test_render_pipeline_bumps_correct_padding_side_with_support_table():
    # Regression: render_standard_vega_spec previously rebuilt the padding
    # dict and clobbered bump_padding_bottom's support-table strip reservation.
    # Verify that the correct padding side is increased by the strip height.
    # With the default position=top, the strip is above the plot → padding.top bumped.
    chart = _compiled_bar_with_support_table([{"source": "revenue", "format": "$.2s"}])
    _rc = resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _SAMPLE_DATA, width=400, height=200)
    padding = spec["padding"]
    # the spec starts at {0,0,0,0}; default position=top bumps padding.top.
    assert padding["left"] == 0
    assert padding["top"] > 0  # strip height reserved above the plot
    assert padding["right"] == 0
    assert padding["bottom"] == 0  # no bottom bump for position=top


def test_render_bar_with_source_row_adds_layer():
    chart = _compiled_bar_with_support_table([{"source": "revenue", "format": "$.2s"}])
    _rc = resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _SAMPLE_DATA, width=400, height=200)
    assert "layer" in spec
    layers = spec["layer"]
    text_layers = [
        layer
        for layer in layers
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "text"
    ]
    # Exactly one attached-row text layer for the source entry.
    assert len(text_layers) >= 1


def test_render_bar_with_aggregate_row_adds_aggregate_transform():
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"aggregate": "sum", "source": "revenue"}]},
            "style": {"orientation": "vertical"},
        }
    )
    _rc = resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _SAMPLE_DATA, width=400, height=200)
    layers = spec["layer"]
    agg_layers = [
        layer
        for layer in layers
        if any("aggregate" in t for t in layer.get("transform", []))
    ]
    assert len(agg_layers) == 1
    agg = agg_layers[0]["transform"][0]
    assert agg["groupby"] == ["month"]
    assert agg["aggregate"][0]["field"] == "revenue"


def test_render_bar_horizontal_with_support_table_renders_column():
    # Horizontal bars swap VL channels (category on y, measure on x): the
    # category axis is vertical, so support_table attaches as a column block
    # beside the plot rather than a strip of rows above/below it.
    chart = _compiled_bar_horizontal_with_support_table([{"source": "revenue"}])
    resolved_chart = resolve(chart, _HBAR_DATA, chart_style_context=_BOARD_CONTEXT)
    assert resolved_chart.orientation == "horizontal"
    spec = generate_vega_lite_spec(chart, _HBAR_DATA, width=400, height=200)
    assert "layer" in spec
    text_layers = [
        layer
        for layer in spec["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "text"
    ]
    assert len(text_layers) >= 1
    # Cell layers ride the category channel on y (shared with the parent's own
    # y-encoding) and a pixel-literal x — the exact transpose of the row strip.
    cell_layer = text_layers[0]
    assert cell_layer["encoding"]["y"]["field"] == "region"
    assert "value" in cell_layer["encoding"]["x"]


def test_default_numeric_column_decimal_aligns_with_no_authored_format():
    """The board-26 26A case, end to end: a support_table entry with NO
    ``format:`` authored inherits the chart's own measure format, the
    engine's ``number`` default (an SI spec, ``.3~s``). At this magnitude
    (well under the SI compaction threshold) ``shared_scale_for_column``
    refuses -- there is no shared tier, so each cell keeps its own per-cell
    significant-figures spec. The column must still decimal-align, on the
    column's own observed max fractional depth rather than a shared tier
    (``_unscaled_decimal_pad``).

    CRITICAL regression: a WHOLE 3-digit value (``128`` -- the integer part
    alone fills the ``.3~s`` significant-figure budget, so there's no
    fractional part to trim) mixed with fractional ones is the exact case
    that broke -- the Python pad table was built by fractional-DIGIT count
    (``decimal_pad_for``'s index space) but the painted Vega expression
    selected by STRING-LENGTH-DIFF against the untrimmed spec, and those
    two only coincide for a fixed-point spec, not this SI one. ``128``'s
    trimmed and untrimmed forms are both ``"128"`` (no diff), so the OLD
    code painted it with no pad at all while ``30`` (also whole, but the
    Python side got the trimmed/untrimmed diff right by coincidence) got
    one -- two whole-number cells, inconsistently padded.

    Verified against the REAL Vega evaluator (vl_convert), not a Python
    re-implementation: extracts the actual painted ``<text>`` node content
    from the rendered SVG and checks it against ``StripNumerals.bare_text``
    -- the DIFFERENT code path (Python, width-measurement side) that reserved
    the column's width in the first place. If paint and measurement diverge,
    the column doesn't align even though each side is internally consistent.
    """
    data = [
        {"region": "A", "revenue": 30.0},  # 3 sig figs -> "30" (whole)
        {"region": "B", "revenue": 84.3},  # 3 sig figs -> "84.3" (full precision)
        {
            "region": "C",
            "revenue": 128.0,
        },  # 3 sig figs -> "128" (whole, no room to trim)
    ]
    chart = _compiled_bar_horizontal_with_support_table([{"source": "revenue"}])
    resolved_chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    assert resolved_chart.effective_support_table_style is not None
    font_family = resolved_pad_font_family(resolved_chart.effective_support_table_style)
    numerals = _entry_numerals(
        resolved_chart.support_table,
        [[row["revenue"] for row in data]],
        formats=None,
        font_family=font_family,
        anchor=StripAnchor.by_drawn_index_data_order(),
    )[0]
    assert numerals.digit_spec == ".3~s", "digit_spec stays the engine's own default"
    assert numerals.decimal_pad_table, (
        "mixed observed depth (30/84.3/128) must bake a pad"
    )

    measured = {row["revenue"]: numerals.bare_text(row["revenue"]) for row in data}
    pad_30 = measured[30.0].removeprefix("30")
    pad_128 = measured[128.0].removeprefix("128")
    # The measurement side itself must treat both whole numbers identically —
    # otherwise the reserved column width is already wrong before painting.
    assert pad_30 == pad_128, (
        f"both whole-number cells need the SAME pad (same missing depth); "
        f"got {pad_30!r} for 30 vs {pad_128!r} for 128"
    )
    assert pad_30, (
        "a whole number sharing the column with a fractional sibling must pad"
    )
    assert measured[84.3] == "84.3", "already at full observed depth -- no pad"

    painted = _render_v2_svg_text_nodes(chart, data, width=400, height=200)
    for row in data:
        assert measured[row["revenue"]] in painted, (
            f"expected the REAL Vega-painted text to match the measured "
            f"bare_text {measured[row['revenue']]!r} for revenue={row['revenue']}; "
            f"painted text nodes were {painted!r}"
        )


def test_si_suffix_column_with_a_genuine_tier_mix_bakes_no_pad():
    """An SI column whose shared-scale bake refuses because its values span
    a genuine tier mix (``$500`` prints bare; ``$4.5M``/``$5M`` both print a
    "M" suffix, so each cell keeps its own per-cell ``$.3~s``) must not get
    padded at all: aligning a bare "$500" against "$4.5M"'s decimal point
    would line up unrelated place values (a units digit against a
    hundred-thousands-of-millions digit), which is meaningless -- see
    ``column_shares_one_printed_unit``.
    """
    data = [
        {"region": "A", "revenue": 500.0},
        {"region": "B", "revenue": 4.5e6},
        {"region": "C", "revenue": 5e6},
    ]
    chart = _compiled_bar_horizontal_with_support_table(
        [{"source": "revenue", "format": "currency"}]
    )
    resolved_chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    assert resolved_chart.effective_support_table_style is not None
    font_family = resolved_pad_font_family(resolved_chart.effective_support_table_style)
    numerals = _entry_numerals(
        resolved_chart.support_table,
        [[row["revenue"] for row in data]],
        formats=None,
        font_family=font_family,
        anchor=StripAnchor.by_drawn_index_data_order(),
    )[0]
    assert numerals.digit_spec == "$.3~s"
    assert numerals.decimal_pad_table == ()

    four_point_five_m = numerals.bare_text(4.5e6)
    five_hundred = numerals.bare_text(500.0)
    five_m = numerals.bare_text(5e6)
    assert four_point_five_m == "$4.5M"
    assert five_hundred == "$500"
    assert five_m == "$5M"

    painted = _render_v2_svg_text_nodes(chart, data, width=400, height=200)
    for value, expected in (
        (500.0, five_hundred),
        (4.5e6, four_point_five_m),
        (5e6, five_m),
    ):
        assert expected in painted, (
            f"expected the REAL Vega-painted text {expected!r} for "
            f"revenue={value}; painted text nodes were {painted!r}"
        )


def test_shared_scale_column_pad_agrees_when_capped_below_declared_precision():
    """CRITICAL regression: a shared-scale SI column whose OBSERVED max
    fractional depth is narrower than its rebuilt spec's own DECLARED
    precision (``decimal_pad_table_for``'s ``max_precision`` cap,
    ``_shared_scale_decimal_pad``) must still paint the pad the CAPPED
    table reserves, not one sized to the uncapped declared precision.

    1.2M / 3M / 5M (default ``.3~s`` format) rebuilds to a ``,.2~f`` digit
    spec (2 declared decimal places, chosen so 1.2M's own 2 significant
    figures show), but every cell's OBSERVED depth is 0 or 1 -- nothing
    ever shows the full 2 declared digits, so the pad table is capped to
    precision=1 (3 entries). The old length-diff selector computed its
    "full" reference from the spec's own UNCAPPED declared precision (2, a
    4-entry table), disagreeing with the capped table and over-widening the
    pad -- confirmed via ``git stash`` against the pre-fix code: the painted
    pad for the whole-number cells was ``"\u2007\u200b\u2007\u200b\u2008\u200b"``
    (5 chars, the uncapped index) before this fix, ``"\u2007\u200b\u2008\u200b"``
    (3 chars, the capped index) after.
    """
    from dbt_charts.core.compile.format import decimal_pad_table_for
    from dbt_charts.core.text.numeral_scale import decimal_pad_for

    data = [
        {"region": "A", "revenue": 1.2e6},
        {"region": "B", "revenue": 3.0e6},
        {"region": "C", "revenue": 5.0e6},
    ]
    chart = _compiled_bar_horizontal_with_support_table([{"source": "revenue"}])
    resolved_chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    assert resolved_chart.effective_support_table_style is not None
    font_family = resolved_pad_font_family(resolved_chart.effective_support_table_style)
    painted = _render_v2_svg_text_nodes(chart, data, width=400, height=200)
    one_point_two_m = next(t for t in painted if t.startswith("1.2") and "mn" not in t)
    three_m = next(t for t in painted if t.startswith("3") and "mn" not in t)
    five_m = next(t for t in painted if t.startswith("5") and "mn" not in t)

    # The capped table (precision=1, from the OBSERVED max depth, not the
    # ,.2~f spec's own declared 2) is the single independent source of truth
    # here -- built the same way _shared_scale_decimal_pad builds it, but
    # not by calling that function.
    capped_table = decimal_pad_table_for(",.2~f", font_family, max_precision=1)
    assert len(capped_table) - 2 == 1

    assert one_point_two_m.removeprefix("1.2").removesuffix("M") == decimal_pad_for(
        capped_table, "1.2"
    ), one_point_two_m
    three_pad = three_m[len("3M") :]
    five_pad = five_m[len("5M") :]
    assert three_pad == decimal_pad_for(capped_table, "3") == five_pad, (
        three_m,
        five_m,
    )
    assert three_pad, (
        "a whole cell sharing the column with a fractional sibling must pad"
    )


def test_layered_horizontal_bar_support_table_honors_chart_sort(monkeypatch):
    """A layered horizontal bar's `chart.sort` must compose with an attached
    support_table.

    Root cause: Vega-Lite's own native sort-by-field resolves correctly
    across a plain layered chart, but silently reverts the shared
    categorical scale to alphabetical order the moment ANY sibling layer
    sharing that scale carries its own `transform:` pipeline -- exactly
    what every support_table cell/header layer carries (their
    value-producing `calculate` transform). Confirmed empirically against a
    real compiled spec before writing this fix.
    """
    data = [
        {"region": "C", "revenue": 10.0, "target": 50.0},
        {"region": "A", "revenue": 30.0, "target": 10.0},
        {"region": "B", "revenue": 20.0, "target": 30.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "sort": {"by": "target", "order": "desc"},
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
            "layers": [{"type": "bar", "y": "target", "label": "Target"}],
            "style": {"orientation": "horizontal"},
        }
    )
    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)

    domain = spec["encoding"]["y"]["scale"]["domain"]
    assert domain == ["C", "B", "A"], (
        f"expected the domain sorted by target descending (C, B, A), not "
        f"alphabetical; got {domain!r}"
    )


def test_layered_horizontal_bar_support_table_with_no_chart_sort_keeps_engine_order(
    monkeypatch,
):
    """With NO `chart.sort` authored, an attached support_table must not
    reorder the category domain away from the order the engine default
    already renders -- a horizontal bar with no color channel compiles an
    ENGINE DEFAULT sort onto its encoding (`{"field": measure, "order":
    "descending"}`, see `emitters/bar.py`), and `A, B, C` is that order, not
    the query's own `C, A, B`.

    The domain is pinned (the chart is layered, so `_reconcile_x_domain`
    fires); what this pins is its VALUE. `rendered_x_domain` reads the
    compiled encoding's sort, so a change that stopped it doing so would
    fall through to base row order and flip the axis to `C, A, B`.
    """
    data = [
        {"region": "C", "revenue": 10.0, "target": 50.0},
        {"region": "A", "revenue": 30.0, "target": 10.0},
        {"region": "B", "revenue": 20.0, "target": 30.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
            "layers": [{"type": "bar", "y": "target", "label": "Target"}],
            "style": {"orientation": "horizontal"},
        }
    )
    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)

    assert spec["encoding"]["y"]["sort"] == {"field": "revenue", "order": "descending"}
    assert spec["encoding"]["y"]["scale"]["domain"] == ["A", "B", "C"]


def test_layered_horizontal_bar_support_table_keeps_a_layer_only_category(monkeypatch):
    """CRITICAL regression: a support_table attached to a layered chart must
    not narrow the shared categorical domain `_reconcile_x_domain`
    (`emitters/_overlay.py`) already pinned as a UNION of the base rows and
    every layer's own rows. The support_table post-pass only ever sees the
    base rows, so recomputing the domain from them alone would silently drop
    a category that exists ONLY in a layer's own diverging query -- exactly
    the failure `_reconcile_x_domain`'s own docstring warns against.

    The layer here queries a region ("D") the base query never returns.
    Region names are chosen so alphabetical order (`A, B, C`) disagrees with
    the expected revenue-descending order (`B, C, A`), so this also catches
    an order regression, not just a dropped category.
    """
    base_data = [
        {"region": "A", "revenue": 10.0},
        {"region": "B", "revenue": 30.0},
        {"region": "C", "revenue": 20.0},
    ]
    layer_data = [{"region": "D", "target": 5.0}]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
            "layers": [
                {"type": "bar", "query": "q_layer", "y": "target", "label": "Target"}
            ],
            "style": {"orientation": "horizontal"},
        }
    )
    spec = _render_v2_spec(
        chart,
        base_data,
        width=400,
        height=200,
        monkeypatch=monkeypatch,
        datasets={"q": base_data, "q_layer": layer_data},
    )

    domain = spec["encoding"]["y"]["scale"]["domain"]
    assert domain == ["B", "C", "A", "D"], (
        f"expected revenue-descending base order with the layer-only "
        f"category 'D' tailed at the end; got {domain!r}"
    )


def test_support_table_pin_honors_an_emitted_engine_default_sort():
    """With NO `chart.sort` authored, the support_table pin must still honor
    a horizontal bar's ENGINE DEFAULT sort already present on its compiled
    encoding (`{"field": measure, "order": "descending"}`, see
    `emitters/bar.py`'s largest-measure-first default) -- otherwise a
    support_table's own transform-bearing layers revert Vega-Lite's shared
    categorical scale to alphabetical order. This mirrors `rendered_x_domain`
    (`x_domain.py`), which reads the compiled encoding's `sort`
    unconditionally (authored or emitted) for the layered path's own domain
    pin.

    Region names are chosen so alphabetical order (`A, B, C`) disagrees with
    the expected revenue-descending order (`B, C, A`) -- a regression
    collapsing to alphabetical is caught.

    Asserted against the pin itself, not a rendered spec: a layered chart's
    shared categorical domain is pinned by `_reconcile_x_domain`
    (`emitters/_overlay.py`) before this post-pass ever runs, so the
    presence of a `scale.domain` on the finished spec says nothing about
    whether this gate held for a layered chart -- this fixture is
    unlayered, so the pin here is the only thing that could have set it.
    """
    data = [
        {"region": "A", "revenue": 10.0},
        {"region": "B", "revenue": 30.0},
        {"region": "C", "revenue": 20.0},
    ]
    spec = {
        "encoding": {
            "y": {
                "field": "region",
                "type": "nominal",
                "sort": {"field": "revenue", "order": "descending"},
            }
        }
    }

    pinned = _pin_sorted_category_domain(spec, "y", "region", data, None)
    assert pinned["encoding"]["y"]["scale"]["domain"] == ["B", "C", "A"]


def test_support_table_pin_no_ops_with_no_emitted_sort_at_all():
    """With neither an authored `chart.sort` nor an emitted engine-default
    sort on the compiled encoding (e.g. a colored or wide horizontal bar,
    which pins `sort: null` -- see `emitters/bar.py`), the support_table pin
    must leave the category domain alone: there is no order to reproduce.
    """
    data = [
        {"region": "C", "revenue": 10.0},
        {"region": "A", "revenue": 30.0},
        {"region": "B", "revenue": 20.0},
    ]
    spec = {
        "encoding": {
            "y": {
                "field": "region",
                "type": "nominal",
                "sort": None,
            }
        }
    }

    pinned = _pin_sorted_category_domain(spec, "y", "region", data, None)
    assert "domain" not in pinned["encoding"]["y"].get("scale", {})


def test_temporal_x_line_chart_support_table_with_sort_does_not_crash(monkeypatch):
    """CRITICAL regression: a temporal-x chart with an attached support_table
    AND an authored `chart.sort` must not pin a raw `date` list as
    `scale.domain` -- Vega-Lite's own spec is JSON, and a Python `date`
    object isn't JSON-serializable, so the render crashes outright. Mirrors
    `emitters/_overlay.py`'s own `x_enc.get("type") not in
    _CATEGORICAL_X_TYPES` guard: an explicit domain pin is a categorical
    (nominal/ordinal) scale concept, and a temporal x is neither.
    """
    data = [
        {"week": datetime.date(2024, 1, 1), "revenue": 10.0, "target": 50.0},
        {"week": datetime.date(2024, 1, 8), "revenue": 30.0, "target": 10.0},
        {"week": datetime.date(2024, 1, 15), "revenue": 20.0, "target": 30.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "line",
            "x": "week",
            "y": "revenue",
            "sort": {"by": "target", "order": "desc"},
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
        }
    )
    # A control with no sort at all must render too -- confirms the crash is
    # specifically the sort + support_table + temporal-x combination, not
    # support_table on a temporal x in general.
    control = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "line",
            "x": "week",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
        }
    )
    _render_v2_spec(control, data, width=400, height=200, monkeypatch=monkeypatch)

    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)
    x_encoding = spec["encoding"]["x"]
    assert x_encoding.get("type") == "temporal"
    assert "domain" not in x_encoding.get("scale", {}), (
        "a temporal x must never get an explicit categorical domain pin"
    )


def test_bar_date_x_column_support_table_with_sort_does_not_crash(monkeypatch):
    """CRITICAL regression: a BAR chart's ``::date`` x column clamps to a
    band scale and compiles its encoding ``type`` to ordinal (vertical) or
    nominal (horizontal) -- it LOOKS categorical, so the encoding-type gate
    the temporal-x-line fix relies on doesn't catch it. The pinned DOMAIN
    VALUES are still ``datetime.date`` objects regardless, and Vega-Lite's
    JSON serializer rejects those outright. The no-sort control (same date
    column, no `chart.sort`) must render fine either way -- confirming the
    sort + support_table combination is the sole cause, on a chart type the
    prior temporal-x-line test never exercised.
    """
    data = [
        {"week": datetime.date(2024, 1, 1), "revenue": 10.0, "target": 50.0},
        {"week": datetime.date(2024, 1, 8), "revenue": 30.0, "target": 10.0},
        {"week": datetime.date(2024, 1, 15), "revenue": 20.0, "target": 30.0},
    ]
    for orientation, category_channel in (("vertical", "x"), ("horizontal", "y")):
        chart = TypeAdapter(Chart).validate_python(
            {
                "id": "test_chart",
                "type": "bar",
                "x": "week",
                "y": "revenue",
                "sort": {"by": "target", "order": "desc"},
                "query": SqlQuery(sql="SELECT 1", source="test_db"),
                "query_name": "q",
                "support_table": {
                    "entries": [{"source": "revenue", "label": "Revenue"}]
                },
                "style": {"orientation": orientation},
            }
        )
        control = TypeAdapter(Chart).validate_python(
            {
                "id": "test_chart",
                "type": "bar",
                "x": "week",
                "y": "revenue",
                "query": SqlQuery(sql="SELECT 1", source="test_db"),
                "query_name": "q",
                "support_table": {
                    "entries": [{"source": "revenue", "label": "Revenue"}]
                },
                "style": {"orientation": orientation},
            }
        )
        _render_v2_spec(control, data, width=400, height=200, monkeypatch=monkeypatch)

        spec = _render_v2_spec(
            chart, data, width=400, height=200, monkeypatch=monkeypatch
        )
        category_encoding = spec["encoding"][category_channel]
        assert category_encoding.get("type") in ("ordinal", "nominal"), (
            orientation,
            category_encoding,
        )
        assert "domain" not in category_encoding.get("scale", {}), (
            f"a date-object category domain must never get an explicit pin "
            f"({orientation})"
        )


def test_continuous_x_with_sort_does_not_pin_a_categorical_domain(monkeypatch):
    """CRITICAL regression: an explicit ``scale.domain`` array is a
    categorical concept. On a continuous scale (quantitative, or temporal
    from ISO-string values) d3 reads it as scale STOPS -- it pairs only
    ``min(len(domain), len(range))`` entries and extrapolates the rest
    off-canvas, so marks render outside the plot and axis ticks vanish. Such
    an x's values are plain JSON scalars (numbers / ISO strings), so they
    pass the value-type gate; only the encoding-type gate keeps the pin off
    them. Both the quantitative and the string-temporal shape must leave the
    x scale unpinned, while the no-sort control renders either way.
    """
    cases = [
        # (x_field, rows) -> x compiles to a continuous scale
        (
            "wk",
            [
                {"wk": 1, "revenue": 10.0, "target": 10.0},
                {"wk": 2, "revenue": 30.0, "target": 50.0},
                {"wk": 3, "revenue": 20.0, "target": 30.0},
            ],
        ),
        (
            "week",
            [
                {"week": "2024-01-01", "revenue": 10.0, "target": 10.0},
                {"week": "2024-01-08", "revenue": 30.0, "target": 50.0},
                {"week": "2024-01-15", "revenue": 20.0, "target": 30.0},
            ],
        ),
    ]
    for x_field, data in cases:
        chart = TypeAdapter(Chart).validate_python(
            {
                "id": "test_chart",
                "type": "line",
                "x": x_field,
                "y": "revenue",
                "sort": {"by": "target", "order": "desc"},
                "query": SqlQuery(sql="SELECT 1", source="test_db"),
                "query_name": "q",
                "support_table": {
                    "entries": [{"source": "revenue", "label": "Revenue"}]
                },
            }
        )
        control = TypeAdapter(Chart).validate_python(
            {
                "id": "test_chart",
                "type": "line",
                "x": x_field,
                "y": "revenue",
                "query": SqlQuery(sql="SELECT 1", source="test_db"),
                "query_name": "q",
                "support_table": {
                    "entries": [{"source": "revenue", "label": "Revenue"}]
                },
            }
        )
        _render_v2_spec(control, data, width=400, height=200, monkeypatch=monkeypatch)

        spec = _render_v2_spec(
            chart, data, width=400, height=200, monkeypatch=monkeypatch
        )
        x_encoding = spec["encoding"]["x"]
        assert x_encoding.get("type") not in ("nominal", "ordinal"), (
            x_field,
            x_encoding,
        )
        assert "domain" not in x_encoding.get("scale", {}), (
            f"a continuous ({x_encoding.get('type')}) x scale must never get "
            f"a categorical domain pin (x_field={x_field!r})"
        )


def test_null_sort_value_keeps_its_category_in_the_domain(monkeypatch):
    """CRITICAL regression: a row whose sort-field value is NULL must not
    be silently dropped from the pinned domain -- Vega-Lite's own
    `scale.domain` is EXCLUSIVE, so any category the pinned list omits never
    renders at all. Vega-Lite puts a category with no sort-field value LAST
    (rather than dropping it), and the pinned domain must match.
    """
    data = [
        {"region": "C", "revenue": 10.0, "target": 50.0},
        {"region": "A", "revenue": 30.0, "target": None},
        {"region": "B", "revenue": 20.0, "target": 30.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "sort": {"by": "target", "order": "desc"},
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
            "style": {"orientation": "horizontal"},
        }
    )
    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)

    domain = spec["encoding"]["y"]["scale"]["domain"]
    assert set(domain) == {"A", "B", "C"}, (
        f"every category must still render, including the null-sort-value "
        f"one; got domain {domain!r}"
    )
    assert domain[-1] == "A", "the null-sort-value category goes last, where VL puts it"


def test_stacked_bar_support_table_sort_uses_sum_not_first_occurrence():
    """HIGH regression: a stacked bar's own multiple rows per category
    (support_table's `aggregate:`/non-by_measure `per_series:` entries both
    permit this -- board 26's own 26M/26N cells author exactly this shape)
    must sort by the SUM of the sort field per category, matching
    Vega-Lite's own `EncodingSortField.op` default -- not by whichever row
    happens to appear FIRST in the data.

    Category A's two rows sum to 15 but its FIRST row is 10; category B's
    sum to 16 but its FIRST row is only 8. A first-occurrence reimplementation
    ranks A ahead of B (10 > 8); the correct sum-based ranking puts B ahead
    of A (16 > 15) -- the two orderings disagree, so this discriminates them.
    """
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "category",
            "y": "revenue",
            "color": "series",
            "stack": "zero",
            "sort": {"by": "revenue", "order": "desc"},
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"aggregate": "sum", "source": "revenue", "label": "Total"}]
            },
            "style": {"orientation": "horizontal"},
        }
    )
    data = [
        {"category": "A", "series": "s1", "revenue": 10.0},
        {"category": "A", "series": "s2", "revenue": 5.0},
        {"category": "B", "series": "s1", "revenue": 8.0},
        {"category": "B", "series": "s2", "revenue": 8.0},
        {"category": "C", "series": "s1", "revenue": 20.0},
    ]
    resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, data, width=400, height=200)
    pane = chart_pane(spec)

    domain = pane["encoding"]["y"]["scale"]["domain"]
    assert domain == ["C", "B", "A"], (
        f"expected sum-ranked order (C=20, B=16, A=15) descending, not "
        f"first-occurrence order; got {domain!r}"
    )


def test_unlayered_horizontal_bar_support_table_honors_chart_sort(monkeypatch):
    """The non-layered support_table path must also honor `chart.sort` --
    the same underlying Vega-Lite limitation (a transform-bearing sibling
    layer defeats native sort-by-field) applies whether or not the chart
    itself authors `layers:`, since support_table's own cell/header layers
    are the transform-bearing siblings either way.
    """
    data = [
        {"region": "C", "revenue": 10.0, "target": 50.0},
        {"region": "A", "revenue": 30.0, "target": 10.0},
        {"region": "B", "revenue": 20.0, "target": 30.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "sort": {"by": "target", "order": "desc"},
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
            "style": {"orientation": "horizontal"},
        }
    )
    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)

    domain = spec["encoding"]["y"]["scale"]["domain"]
    assert domain == ["C", "B", "A"], (
        f"expected the domain sorted by target descending (C, B, A), not "
        f"alphabetical; got {domain!r}"
    )


def test_unlayered_horizontal_bar_support_table_with_no_chart_sort_keeps_engine_order(
    monkeypatch,
):
    """A single-series horizontal bar's value-descending default must
    survive a `support_table` attachment even with NO `chart.sort`
    authored. The non-layered path's own ENGINE DEFAULT sort (`{"field":
    measure, "order": "descending"}`, `emitters/bar.py`) must be honored the
    same way `test_layered_horizontal_bar_support_table_with_no_chart_sort_keeps_engine_order`
    already requires for the layered path -- otherwise attaching a strip
    reverts the board to alphabetical.

    Stage names are chosen so alphabetical order (`Drew discussion`, `Linked
    to a fix commit`, `Reports filed`) disagrees with both query row order
    and the expected value-descending domain (`Reports filed`, `Drew
    discussion`, `Linked to a fix commit`), so a regression collapsing to
    either is caught.
    """
    data = [
        {"stage": "Linked to a fix commit", "reports": 1113.0},
        {"stage": "Reports filed", "reports": 5501.0},
        {"stage": "Drew discussion", "reports": 4604.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "stage",
            "y": "reports",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "reports", "label": "Reports"}]},
            "style": {"orientation": "horizontal"},
        }
    )
    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)

    assert spec["encoding"]["y"]["sort"] == {"field": "reports", "order": "descending"}
    assert spec["encoding"]["y"]["scale"]["domain"] == [
        "Reports filed",
        "Drew discussion",
        "Linked to a fix commit",
    ]


def test_vertical_bar_support_table_honors_chart_sort(monkeypatch):
    """The row-strip (vertical-orientation) support_table path must also
    honor `chart.sort` -- same root cause, the `x` channel instead of `y`.
    """
    data = [
        {"region": "C", "revenue": 10.0, "target": 50.0},
        {"region": "A", "revenue": 30.0, "target": 10.0},
        {"region": "B", "revenue": 20.0, "target": 30.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "sort": {"by": "target", "order": "desc"},
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
            "style": {"orientation": "vertical"},
        }
    )
    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)

    domain = spec["encoding"]["x"]["scale"]["domain"]
    assert domain == ["C", "B", "A"], (
        f"expected the domain sorted by target descending (C, B, A), not "
        f"alphabetical; got {domain!r}"
    )


def _header_fill(spec: dict[str, Any], header_text: str) -> str:
    """Find the `mark.fill` of the support_table header layer whose inline
    `__header` datum equals *header_text*."""
    for layer in spec["layer"]:
        values = layer.get("data", {}).get("values")
        if values and values[0].get("__header") == header_text:
            return layer["mark"]["fill"]
    raise AssertionError(f"no header layer found for {header_text!r}")


def test_layered_bar_support_table_headers_take_series_companion_ink(monkeypatch):
    """A layered bar's `source:` support_table columns take their linked
    series' companion ink on the header, when the chart has more than one
    mark series and no legend -- the colored header stands in for a legend
    swatch. Auto-linked purely by `source:` column == a series' own `y`
    measure; no new authored field.
    """
    data = [
        {"region": "A", "revenue": 30.0, "target": 10.0},
        {"region": "B", "revenue": 20.0, "target": 30.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [
                    {"source": "revenue", "label": "Revenue"},
                    {"source": "target", "label": "Target"},
                ]
            },
            "layers": [{"type": "bar", "y": "target", "label": "Target"}],
            "style": {"orientation": "horizontal", "legend": {"visible": False}},
        }
    )
    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)

    color_scale = None
    for layer in spec["layer"]:
        scale = layer.get("encoding", {}).get("color", {}).get("scale")
        if isinstance(scale, dict) and isinstance(scale.get("range"), list):
            color_scale = scale
            break
    assert color_scale is not None, "expected a shared categorical color scale"
    base_fill, layer_fill = color_scale["range"]

    from dbt_charts.core.render.chart.emitters._cartesian import (
        companion_color_for_fill,
    )

    resolved_chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    palette = list(resolved_chart.palette)
    dark_companions = resolved_chart.style.series_label.dark_companion_palette

    assert _header_fill(spec, "Revenue") == companion_color_for_fill(
        base_fill, palette, dark_companions
    )
    assert _header_fill(spec, "Target") == companion_color_for_fill(
        layer_fill, palette, dark_companions
    )


def test_layered_bar_support_table_headers_stay_default_ink_when_legend_visible(
    monkeypatch,
):
    """HIGH regression: a layered chart whose legend IS visible (no
    `legend.visible: false` authored) must NOT recolor its support_table
    headers -- doing so would name each series twice, once by the real
    legend swatch and once by header ink.
    """
    data = [
        {"region": "A", "revenue": 30.0, "target": 10.0},
        {"region": "B", "revenue": 20.0, "target": 30.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [
                    {"source": "revenue", "label": "Revenue"},
                    {"source": "target", "label": "Target"},
                ]
            },
            "layers": [{"type": "bar", "y": "target", "label": "Target"}],
            "style": {"orientation": "horizontal"},
        }
    )
    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)

    color_scale = None
    for layer in spec["layer"]:
        scale = layer.get("encoding", {}).get("color", {}).get("scale")
        if isinstance(scale, dict) and isinstance(scale.get("range"), list):
            color_scale = scale
            break
    assert color_scale is not None, "expected a shared categorical color scale"
    for layer in spec["layer"]:
        legend = layer.get("encoding", {}).get("color", {}).get("legend")
        if isinstance(legend, dict):
            break
    else:
        raise AssertionError("expected a live legend on this chart")

    default_ink = get_theme_style().charts.support_table.font.color
    assert _header_fill(spec, "Revenue") == default_ink
    assert _header_fill(spec, "Target") == default_ink


def test_bar_base_with_line_layer_support_table_headers_take_series_ink(monkeypatch):
    """HIGH regression: a bar base with a LINE overlay layer -- the
    canonical "hide the legend, let header color name the series" shape --
    must color both headers, not just the bar's. A line layer wraps its
    real marks one level deeper (`mark: "layered"`, halo + foreground +
    hover-target sublayers) than a bar layer's direct `mark` dict, so a
    positional `spec["layer"][i]["mark"]` read silently found nothing for
    it before this fix.
    """
    data = [
        {"region": "A", "revenue": 30.0, "target": 10.0},
        {"region": "B", "revenue": 20.0, "target": 30.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [
                    {"source": "revenue", "label": "Revenue"},
                    {"source": "target", "label": "Target"},
                ]
            },
            "layers": [{"type": "line", "y": "target", "label": "Target"}],
            "style": {"orientation": "horizontal", "legend": {"visible": False}},
        }
    )
    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)

    line_layer = spec["layer"][1]
    assert "mark" not in line_layer or not isinstance(line_layer.get("mark"), dict), (
        "expected the line layer to wrap its real marks in a nested layer array"
    )
    line_stroke = line_layer["layer"][0]["mark"]["stroke"]
    bar_fill = spec["layer"][0]["mark"]["fill"]

    from dbt_charts.core.colors import ensure_readable_ink
    from dbt_charts.core.render.chart.emitters._cartesian import (
        companion_color_for_fill,
    )

    resolved_chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    palette = list(resolved_chart.palette)
    dark_companions = resolved_chart.style.series_label.dark_companion_palette
    background = _BOARD_CONTEXT.background

    assert _header_fill(spec, "Revenue") == ensure_readable_ink(
        companion_color_for_fill(bar_fill, palette, dark_companions), background
    )
    assert _header_fill(spec, "Target") == ensure_readable_ink(
        companion_color_for_fill(line_stroke, palette, dark_companions), background
    )
    # The two series' headers must not collapse to the same ink.
    assert _header_fill(spec, "Revenue") != _header_fill(spec, "Target")


def test_outline_only_layer_support_table_header_follows_border_color(monkeypatch):
    """An outline-only layer (fill opacity 0 + a visible border) must color
    its support_table header from the BORDER, not the invisible palette
    fill -- the mark's own compiled `fill` stays set at its palette slot
    even when `fillOpacity: 0` hides it, so reading fill unconditionally
    would color the header a value nothing on the mark actually shows.
    """
    data = [
        {"region": "A", "revenue": 30.0, "target": 10.0},
        {"region": "B", "revenue": 20.0, "target": 30.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [
                    {"source": "revenue", "label": "Revenue"},
                    {"source": "target", "label": "Target"},
                ]
            },
            "layers": [
                {
                    "type": "bar",
                    "y": "target",
                    "label": "Target",
                    "style": {
                        "marks": {
                            "bar": {
                                "opacity": 0,
                                "border": {"width": 4, "color": "#d49656"},
                            }
                        }
                    },
                }
            ],
            "style": {"orientation": "horizontal", "legend": {"visible": False}},
        }
    )
    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)

    layer_mark = None
    for layer in spec["layer"]:
        mark = layer.get("mark")
        if (
            isinstance(mark, dict)
            and mark.get("type") == "bar"
            and mark.get("fillOpacity") == 0
        ):
            layer_mark = mark
            break
    assert layer_mark is not None, "expected an outline-only bar layer"
    assert layer_mark["stroke"] == "#d49656"
    # The palette fill is still set (VL keeps it even though fillOpacity
    # hides it) -- the header must NOT match it.
    assert layer_mark["fill"] != "#d49656"

    from dbt_charts.core.colors import ensure_readable_ink
    from dbt_charts.core.render.chart.emitters._cartesian import (
        companion_color_for_fill,
    )

    resolved_chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    expected = ensure_readable_ink(
        companion_color_for_fill(
            "#d49656",
            list(resolved_chart.palette),
            resolved_chart.style.series_label.dark_companion_palette,
        ),
        _BOARD_CONTEXT.background,
    )
    assert _header_fill(spec, "Target") == expected
    assert _header_fill(spec, "Target") != layer_mark["fill"]


def test_light_fill_series_header_gets_readability_floor(monkeypatch):
    """A series whose fill has no registered dark companion (a custom
    categorical palette override, e.g. a light sequential-gray slot) must
    still get a legible header ink, not the raw near-white fill.
    """
    data = [
        {"region": "A", "revenue": 30.0, "target": 10.0},
        {"region": "B", "revenue": 20.0, "target": 30.0},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [
                    {"source": "revenue", "label": "Revenue"},
                    {"source": "target", "label": "Target"},
                ]
            },
            "layers": [{"type": "bar", "y": "target", "label": "Target"}],
            "style": {
                "orientation": "horizontal",
                "legend": {"visible": False},
                "color": {
                    "categorical": {"palette": ["dbt-seq-gray.2", "dbt-seq-gray.10"]}
                },
            },
        }
    )
    spec = _render_v2_spec(chart, data, width=400, height=200, monkeypatch=monkeypatch)

    from dbt_charts.core.colors import wcag_contrast

    revenue_ink = _header_fill(spec, "Revenue")
    assert revenue_ink != "#d7d7d7", "must not use the raw, illegible light fill"
    assert wcag_contrast(revenue_ink, _BOARD_CONTEXT.background) >= 4.5


def test_resolve_bakes_a_concrete_support_table_position():
    # position is resolved once, at compile time, onto
    # effective_support_table_style — render never patches it in afterward
    # (see compile.resolve.chart._kwargs._support_table_geometry).
    vertical = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue"}]},
            "style": {"orientation": "vertical"},
        }
    )
    resolved_vertical = resolve(
        vertical, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT
    )
    assert resolved_vertical.effective_support_table_style is not None
    assert resolved_vertical.effective_support_table_style.position == "top"

    horizontal_default = _compiled_bar_horizontal_with_support_table(
        [{"source": "revenue"}]
    )
    resolved_horizontal_default = resolve(
        horizontal_default, _HBAR_DATA, chart_style_context=_BOARD_CONTEXT
    )
    assert resolved_horizontal_default.effective_support_table_style is not None
    assert resolved_horizontal_default.effective_support_table_style.position == "left"

    horizontal_right = _compiled_bar_horizontal_with_support_table(
        [{"source": "revenue"}], style={"support_table": {"position": "right"}}
    )
    resolved_horizontal_right = resolve(
        horizontal_right, _HBAR_DATA, chart_style_context=_BOARD_CONTEXT
    )
    assert resolved_horizontal_right.effective_support_table_style is not None
    assert resolved_horizontal_right.effective_support_table_style.position == "right"


@pytest.mark.parametrize("position", ["left", "right"])
@pytest.mark.parametrize("layered", [False, True], ids=["single", "layered"])
@pytest.mark.parametrize(
    ("padding", "entry_count"),
    [(None, 1), ({"left": 17, "right": 23, "top": 11, "bottom": 13}, 2)],
    ids=["default-padding-one-entry", "authored-padding-two-entries"],
)
def test_same_side_support_columns_preserve_outer_padding(
    position: str,
    layered: bool,
    padding: dict[str, int | float] | None,
    entry_count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = [{**row, "target": 250.0} for row in _HBAR_DATA]
    chart_fields = {
        "id": "test_chart",
        "type": "bar",
        "x": "region",
        "y": "revenue",
        "query": SqlQuery(sql="SELECT 1", source="test_db"),
        "query_name": "q",
        "layers": [{"type": "bar", "y": "target"}] if layered else [],
        "style": {
            "orientation": "horizontal",
            "axis_y": {"position": position},
            "support_table": {"position": position},
        },
    }
    control = _render_v2_spec(
        TypeAdapter(Chart).validate_python(chart_fields),
        data,
        width=400,
        height=200,
        monkeypatch=monkeypatch,
        padding=padding,
    )
    chart = TypeAdapter(Chart).validate_python(
        {
            **chart_fields,
            "support_table": {
                "entries": [{"source": "revenue"}, {"source": "target"}][:entry_count]
            },
        }
    )
    spec = _render_v2_spec(
        chart,
        data,
        width=400,
        height=200,
        monkeypatch=monkeypatch,
        padding=padding,
    )
    control_axis = control["encoding"]["y"]["axis"]
    support_axis = spec["encoding"]["y"]["axis"]
    assert support_axis["labelPadding"] > control_axis["labelPadding"]
    if layered:
        control_base_axis = control["layer"][0]["encoding"]["y"]["axis"]
        support_base_axis = spec["layer"][0]["encoding"]["y"]["axis"]
        assert support_base_axis["labelPadding"] > control_base_axis["labelPadding"]
    if padding is not None:
        assert control["padding"] == padding
    for side in ("left", "right", "bottom"):
        assert spec["padding"][side] == control["padding"][side]
    assert spec["padding"]["top"] > control["padding"]["top"]


def test_render_bar_horizontal_support_table_reserves_header_height() -> None:
    chart = _compiled_bar_horizontal_with_support_table(
        [{"source": "revenue", "format": "$.2s"}]
    )
    resolve(chart, _HBAR_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _HBAR_DATA, width=400, height=200)
    padding = spec["padding"]
    # Column headers always reserve a band above the plot, regardless
    # of which side the value columns sit on.
    assert padding["top"] > 0
    assert padding["bottom"] == 0


@pytest.mark.parametrize("position", ["left", "right"])
def test_opposite_side_support_columns_reserve_outer_padding(position: str) -> None:
    axis_position = "right" if position == "left" else "left"
    chart = _compiled_bar_horizontal_with_support_table(
        [{"source": "revenue"}],
        style={
            "support_table": {"position": position},
            "axis_y": {"position": axis_position},
        },
    )
    resolve(chart, _HBAR_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _HBAR_DATA, width=400, height=200)
    padding = spec["padding"]
    assert padding[position] > 0
    assert padding[axis_position] == 0
    control_chart = TypeAdapter(Chart).validate_python(
        {**dict(chart), "support_table": None}
    )
    control = generate_vega_lite_spec(control_chart, _HBAR_DATA, width=400, height=200)
    axis = spec["encoding"]["y"]["axis"]
    assert axis["labelPadding"] == control["encoding"]["y"]["axis"]["labelPadding"]


def test_render_bar_horizontal_stacked_with_aggregate_support_table_renders_column():
    # Only a per_series entry turns off the endpoint-label rail
    # (compile/resolve/chart/bar.py) — an aggregate/source entry leaves it on,
    # so a stacked horizontal bar with a series color wraps the chart in
    # vconcat (rail on top, chart pane at index 1). The column attachment
    # must find the chart pane inside that wrapper rather than treating the
    # vconcat root itself as the spec.
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "color": "category",
            "stack": "zero",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"aggregate": "sum", "source": "revenue"}]},
            "style": {"orientation": "horizontal"},
        }
    )
    data = [
        {"region": "East", "category": "Apple", "revenue": 100.0},
        {"region": "East", "category": "Banana", "revenue": 50.0},
        {"region": "West", "category": "Apple", "revenue": 200.0},
        {"region": "West", "category": "Banana", "revenue": 80.0},
    ]
    resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, data, width=400, height=200)
    assert "vconcat" in spec, (
        "fixture precondition: a stacked horizontal bar with a series color "
        "keeps its endpoint-label rail for a non-per_series support_table "
        "entry — otherwise this test exercises nothing"
    )
    pane = chart_pane(spec)
    assert pane["encoding"]["y"]["field"] == "region"
    text_layers = [
        layer
        for layer in pane["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "text"
    ]
    assert len(text_layers) >= 1


def test_render_bar_horizontal_support_table_rejects_top_bottom_position():
    # Schema: top/bottom are invalid on a vertical category axis — never
    # silently remapped. Raises at resolve time (a compile-time error): the
    # value is baked onto effective_support_table_style before any render
    # branch runs, so a bad board fails dct validate, not just a render.
    chart = _compiled_bar_horizontal_with_support_table(
        [{"source": "revenue"}], style={"support_table": {"position": "top"}}
    )
    with pytest.raises(CompilationError, match=r"(?i)position"):
        resolve(chart, _HBAR_DATA, chart_style_context=_BOARD_CONTEXT)


def test_render_bar_vertical_support_table_rejects_left_right_position():
    # Mirrored: left/right are invalid on a horizontal category axis.
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue"}]},
            "style": {
                "orientation": "vertical",
                "support_table": {"position": "left"},
            },
        }
    )
    with pytest.raises(CompilationError, match=r"(?i)position"):
        resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)


def test_render_line_supports_support_table():
    # Chart-type eligibility: spec §1 includes line.
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "line",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue"}]},
        }
    )
    _rc = resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _SAMPLE_DATA, width=400, height=200)
    assert "layer" in spec


def test_render_area_supports_support_table():
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "area",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue"}]},
        }
    )
    _rc = resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _SAMPLE_DATA, width=400, height=200)
    assert "layer" in spec


def test_render_bar_with_support_table_produces_svg_with_row_labels():
    # HIGH 2: pipeline test must go all the way to SVG and assert the
    # strip's text actually renders. Skipped when vl_convert is not
    # installed.
    pytest.importorskip("vl_convert")

    from dbt_charts.core.render.chart.vega_lite import render_chart

    chart = _compiled_bar_with_support_table(
        [{"source": "revenue", "format": "$.0f", "label": "REV"}]
    )
    svg = render_chart(
        chart,
        resolve_style(get_theme_style()),
        resolve_chart_style_context(get_theme_style()),
        _SAMPLE_DATA,
        format="svg",
        width=600,
        height=320,
    )
    # Label text must appear in the SVG output (left-stub by default).
    assert "REV" in svg
    # At least one per-x formatted cell value renders (raw value or format
    # output — both match the format-string contract).
    assert "$100" in svg or ">100<" in svg


def test_render_bar_support_table_missing_cell_and_default_label_in_svg():
    pytest.importorskip("vl_convert")

    from dbt_charts.core.render.chart.vega_lite import render_chart

    chart = _compiled_bar_with_support_table(
        [{"source": "sample_size", "format": ",d"}]
    )
    data = [
        {"month": "Jan", "revenue": 100.0, "sample_size": 10},
        {"month": "Feb", "revenue": 120.0, "sample_size": None},
    ]
    svg = render_chart(
        chart,
        resolve_style(get_theme_style()),
        resolve_chart_style_context(get_theme_style()),
        data,
        format="svg",
        width=600,
        height=320,
    )
    assert "Sample Size" in svg
    assert ">-</text>" in svg
    assert ">NaN<" not in svg
    assert ">null<" not in svg


def test_render_bar_support_table_ambiguous_source_errors_at_render():
    # CRITICAL 4: bare source: on a query with multiple rows per x must
    # error at render time and point the author at aggregate:.
    from dbt_charts.core.diagnostics.chart_data import ChartDataError  # noqa: F401

    chart = _compiled_bar_with_support_table([{"source": "revenue"}])
    pivoted_data = [
        {"month": "Jan", "revenue": 60.0, "segment": "A"},
        {"month": "Jan", "revenue": 40.0, "segment": "B"},
        {"month": "Feb", "revenue": 120.0, "segment": "A"},
        {"month": "Feb", "revenue": 80.0, "segment": "B"},
    ]
    # validate_preaggregated_data would fire first for a bar chart with
    # multi-row-per-x data, so this test routes through the attachment
    # validator directly to isolate the ambiguous-aggregation guard.
    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )

    assert chart.support_table is not None
    with pytest.raises(RenderError, match=r"(?i)ambiguous|multiple rows") as excinfo:
        validate_support_table_against_data(chart.support_table, "month", pivoted_data)
    assert "aggregate" in str(excinfo.value).lower()


def test_render_bar_support_table_missing_source_column_errors_at_render():
    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )

    chart = _compiled_bar_with_support_table([{"source": "sample_size"}])
    data_without_sample_size = [{"month": "Jan", "revenue": 100.0}]
    assert chart.support_table is not None
    with pytest.raises(RenderError, match="sample_size"):
        validate_support_table_against_data(
            chart.support_table, "month", data_without_sample_size
        )


def test_render_bar_support_table_rejects_over_40_x_ticks():
    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )

    chart = _compiled_bar_with_support_table([{"source": "revenue"}])
    big_data = [{"month": f"M{i}", "revenue": float(i)} for i in range(45)]
    assert chart.support_table is not None
    with pytest.raises(RenderError, match="40"):
        validate_support_table_against_data(chart.support_table, "month", big_data)


def test_render_chart_without_support_table_is_unchanged_shape():
    # A bar chart with no support_table block must render with a bar mark and
    # no support_table strip / extra padding.  The bar lives in layer[0]
    # alongside the zero-baseline rule layer added by the standard renderer.
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
        }
    )
    _rc = resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _SAMPLE_DATA, width=400, height=200)
    # No support_table → no strip layer; the only non-mark layer is the zero rule.
    layers = spec.get("layer") or [{"mark": spec.get("mark", {})}]
    assert layers[0].get("mark", {}).get("type") == "bar"
    # No support_table padding inflation
    assert "padding" not in spec or all(
        v <= 50 for v in spec["padding"].values() if isinstance(v, (int, float))
    )


# =============================================================================
# REGRESSION: line/area + temporal x — strip must inherit parent x type
# =============================================================================


def test_line_temporal_x_strip_layers_inherit_x_type():
    # Regression: _shared_x_encoding hardcoded ordinal; line+ordinal x caused
    # a vl-convert null-deref because the strip layers had mismatched scale type.
    # Line always routes a bucketed-calendar grain to continuous temporal (see
    # value-driven-axis-type-inference); strip layers must match.
    chart = _compiled_line_temporal_with_support_table([{"source": "revenue"}])
    _rc = resolve(chart, _TEMPORAL_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _TEMPORAL_DATA, width=400, height=200)
    assert "layer" in spec
    # x encoding is at the top level in a halo-layered spec
    parent_x_type = spec["encoding"]["x"]["type"]
    assert parent_x_type == "temporal"
    # Every data-bound text layer (strip rows) must use the same x type.
    text_layers = [
        layer
        for layer in spec["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and "field" in layer.get("encoding", {}).get("x", {})
    ]
    assert text_layers, "expected at least one data-bound strip text layer"
    for layer in text_layers:
        assert layer["encoding"]["x"]["type"] == "temporal", (
            f"strip layer x type must match parent temporal, got {layer['encoding']['x']['type']!r}"
        )


def test_area_temporal_x_strip_layers_inherit_x_type():
    chart = _compiled_area_temporal_with_support_table([{"source": "revenue"}])
    _rc = resolve(chart, _TEMPORAL_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _TEMPORAL_DATA, width=400, height=200)
    assert "layer" in spec
    text_layers = [
        layer
        for layer in spec["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and "field" in layer.get("encoding", {}).get("x", {})
    ]
    assert text_layers
    for layer in text_layers:
        assert layer["encoding"]["x"]["type"] == "temporal"


def test_line_temporal_x_support_table_no_crash_through_vl_convert():
    pytest.importorskip("vl_convert")
    from dbt_charts.core.render.chart.vega_lite import render_chart

    chart = _compiled_line_temporal_with_support_table(
        [{"source": "revenue", "format": "$.0f"}]
    )
    # Must not raise — previously crashed with "Cannot read properties of null (reading 'type')"
    svg = render_chart(
        chart,
        resolve_style(get_theme_style()),
        resolve_chart_style_context(get_theme_style()),
        _TEMPORAL_DATA,
        format="svg",
        width=600,
        height=320,
    )
    assert svg  # non-empty SVG means vl-convert succeeded


def test_area_temporal_x_support_table_no_crash_through_vl_convert():
    pytest.importorskip("vl_convert")
    from dbt_charts.core.render.chart.vega_lite import render_chart

    chart = _compiled_area_temporal_with_support_table(
        [{"source": "revenue", "format": "$.0f"}]
    )
    svg = render_chart(
        chart,
        resolve_style(get_theme_style()),
        resolve_chart_style_context(get_theme_style()),
        _TEMPORAL_DATA,
        format="svg",
        width=600,
        height=320,
    )
    assert svg


def test_bar_with_support_table_promotes_tooltip_to_spec_level():
    """Pipeline: bar with support_table has tooltip on the bar mark layer.

    V2 bar emits mark-level tooltip:True on the bar mark rather than
    spec.encoding.tooltip array. Both let Vega-Lite show row data on hover;
    mark-level is V2's canonical shape.
    """
    chart = _compiled_bar_with_support_table([{"source": "revenue", "format": "$.0f"}])
    _rc = resolve(chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _SAMPLE_DATA, width=400, height=200)

    bar_layers = [
        layer
        for layer in spec.get("layer", [])
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "bar"
    ]
    assert bar_layers, "expected at least one bar mark layer"
    has_tooltip = any(layer["mark"].get("tooltip") is True for layer in bar_layers)
    # Accept either mark-level tooltip:True (V2 shape) or spec-level
    # encoding.tooltip array (V1 shape / future promotion).
    spec_has_tooltip = "tooltip" in (spec.get("encoding") or {})
    assert has_tooltip or spec_has_tooltip, (
        "bar with support_table must have tooltip on the bar mark (mark.tooltip=True) "
        "or a spec-level encoding.tooltip so hovering shows row values."
    )


# =============================================================================
# TEMPORAL/DATE-LIKE ORDINAL SAMPLING — over-40-row bypass
# =============================================================================


def _compiled_line_yearmonth_with_support_table(entries):
    """Line chart with year-month string x (ordinal-inferred, but date-like)."""
    return TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "line",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": entries},
        }
    )


def _yearmonth_data(n: int) -> list[dict]:
    """n rows of year-month string x like Looker's FORMAT_TIMESTAMP('%Y-%m', ...)."""
    import datetime

    start = datetime.date(2016, 1, 1)
    rows = []
    for i in range(n):
        d = datetime.date(start.year + i // 12, (start.month + i - 1) % 12 + 1, 1)
        rows.append({"month": f"{d.year:04d}-{d.month:02d}", "revenue": float(i)})
    return rows


def test_line_yearmonth_ordinal_over_40_rows_does_not_raise():
    # Regression for dashboard 1291's transformations_model_runs tile:
    # accounts_timeline_date_month is "YYYY-MM" string — inferred as ordinal
    # by dbt charts but semantically temporal. The strip must thin safely at
    # dense widths without raising, whether that comes from the >40-row
    # sampling path or the visible-tick parity filter.
    data = _yearmonth_data(97)
    chart = _compiled_line_yearmonth_with_support_table(
        [{"aggregate": "sum", "source": "revenue"}]
    )
    _rc = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, data, width=400, height=200)
    assert "layer" in spec
    # Sampling transforms should appear on data-bound strip cell layers.
    text_layers = _strip_text_layers(spec)
    assert text_layers
    for tl in text_layers:
        assert _has_strip_thinning(tl), (
            "dense ordinal yearmonth data must thin the strip rather than render "
            f"every cell. Got transforms: {tl.get('transform')}"
        )


def test_line_yearmonth_ordinal_30_rows_strip_thins_when_cells_wider_than_band():
    # 30 yearmonth labels at a 600px render width: band = 600/30 = 20px. The
    # axis overlap walk tilts labels to -90° where the footprint (~16px) fits the
    # 20px band, so all 30 AXIS labels are visible. But strip thinning is decided
    # by the strip's OWN cell width, not the axis's tilted-label fit: the strip's
    # horizontal number cells are far wider than 20px, so the strip must thin.
    # (A tilted axis label needs only its glyph height horizontally; a horizontal
    # currency cell needs its full width — decoupling these is the whole fix.)
    data = _yearmonth_data(30)
    chart = _compiled_line_yearmonth_with_support_table(
        [{"aggregate": "sum", "source": "revenue"}]
    )
    _rc = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, data, width=600, height=200)
    assert "layer" in spec
    text_layers = _strip_text_layers(spec)
    assert text_layers
    # Horizontal cells overflow the 20px band even though tilted axis labels fit,
    # so every strip row must thin.
    assert all(_has_strip_thinning(tl) for tl in text_layers), (
        "30 yearmonth labels at 600px (20px/band): tilted axis labels fit, but the "
        "strip's horizontal cells are wider than the band — the strip must thin."
    )


def test_mon_yyyy_label_ordinal_over_40_thins_without_raising():
    # "Jan 2024" labels are normalized to ISO dates by normalize_labeled_temporal
    # before the support-table validator runs. After normalization the values ARE
    # lex-sortable-chronologically ("2024-01-01" < "2024-02-01"), so the
    # sampling window fires correctly — no cardinality error.
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "line",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"aggregate": "sum", "source": "revenue"}]},
        }
    )
    import calendar

    months = list(calendar.month_abbr)[1:]  # Jan..Dec
    data = [
        {"month": f"{m} {2016 + i // 12}", "revenue": float(i)}
        for i, m in enumerate(months * 5)  # 60 distinct month-year combos
    ]
    _rc = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, data, width=400, height=200)
    assert "layer" in spec
    text_layers = _strip_text_layers(spec)
    assert text_layers
    for tl in text_layers:
        assert _has_strip_thinning(tl), (
            "dense ordinal Mon YYYY labels must thin the strip rather than "
            f"render every cell. Got transforms: {tl.get('transform')}"
        )


# ── per_series row order regression tests ────────────────────────────────
#
# These tests drive apply_chart_support_table_post_pass's stack-vs-non-stack branch
# end-to-end (compile → render). The unit tests in test_support_table_attachment
# exercise attach_support_table with hand-supplied series_order and so don't
# regress if the branch in apply_chart_support_table_post_pass is broken or removed.


def _compiled_chart_with_color_and_per_series(
    chart_type: str,
    stack: str = "zero",
) -> Chart:
    payload: dict[str, Any] = {
        "id": "test_chart",
        "type": chart_type,
        "x": "month",
        "y": "revenue",
        "color": "category",
        "query": SqlQuery(sql="SELECT 1", source="test_db"),
        "query_name": "q",
        "support_table": {"entries": [{"per_series": "revenue", "format": "$,.0f"}]},
    }
    if chart_type == "bar" or chart_type == "area":
        payload["stack"] = stack
    if chart_type == "bar":
        # "month" is a categorical string x, which auto-resolves to horizontal;
        # support_table is unsupported there, so pin vertical explicitly.
        payload["style"] = {"orientation": "vertical"}
    return TypeAdapter(Chart).validate_python(payload)


_THREE_SERIES_DATA = [
    {"month": "Jan", "category": c, "revenue": v}
    for c, v in [("Apple", 100.0), ("Banana", 200.0), ("Cherry", 150.0)]
] + [
    {"month": "Feb", "category": c, "revenue": v}
    for c, v in [("Apple", 110.0), ("Banana", 210.0), ("Cherry", 160.0)]
]


def _per_series_layer_order(spec: dict[str, Any]) -> list[str]:
    """Return per-series strip rows in layer-index order (series_order[0] first).

    series_order[0] = strip row 0 = VISUAL BOTTOM of the strip for
    position:top (the default).  The visual reading order (top of strip first)
    is the REVERSE of what this function returns.  Callers that care about
    visual order should use reversed().

    When endpoint labels fire (editorial default on line/area), the spec is
    wrapped in hconcat; the layers live at hconcat[0].
    """
    import re

    main = spec["hconcat"][0] if "hconcat" in spec else spec
    series_order: list[str] = []
    for layer in main.get("layer", []):
        for transform in layer.get("transform", []) or []:
            f = transform.get("filter")
            if isinstance(f, str):
                m = re.search(r"datum\['category'\] === '([^']+)'", f)
                if m:
                    series_order.append(m.group(1))
                    break
    return series_order


def _per_series_label_fills(spec: dict[str, Any]) -> dict[str, str]:
    main = spec["hconcat"][0] if "hconcat" in spec else spec
    fills: dict[str, str] = {}
    for layer in main.get("layer", []):
        values = layer.get("data", {}).get("values", [])
        if values and "__label" in values[0] and "fill" in layer["mark"]:
            fills[values[0]["__label"]] = layer["mark"]["fill"]
    return fills


def test_per_series_stacked_bar_strip_value_order_largest_sum_first():
    """Stacked bars with default stack_order (value): VL puts the largest-sum series
    at the baseline (bottom).  series_order[0] = largest sum (baseline) so that the
    visual top of the strip (row N for position:top) shows the smallest-sum series,
    matching chart top-to-bottom reading order.

    _THREE_SERIES_DATA sums: Apple=210, Cherry=310, Banana=410.
    Descending by sum: Banana → Cherry → Apple (series_order index order).
    """

    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = _compiled_chart_with_color_and_per_series(chart_type="bar", stack="zero")
    _rc = resolve(chart, _THREE_SERIES_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _THREE_SERIES_DATA, width=400, height=200)
    order = _per_series_layer_order(spec)
    assert order == ["Banana", "Cherry", "Apple"], (
        f"stacked bar (value order): series_order must be descending by global sum "
        f"(largest = series_order[0]); got {order}"
    )


def test_per_series_stacked_bar_strip_honors_chart_local_stack_order() -> None:
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = _compiled_chart_with_color_and_per_series(chart_type="bar", stack="zero")
    chart.style.stack_order = "alphabetical"
    spec = generate_vega_lite_spec(chart, _THREE_SERIES_DATA, width=400, height=200)

    assert _per_series_layer_order(spec) == ["Apple", "Banana", "Cherry"]


def test_per_series_stacked_bar_strip_ink_follows_stack_palette_slots() -> None:
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = _compiled_chart_with_color_and_per_series(chart_type="bar", stack="zero")
    resolved = resolve(chart, _THREE_SERIES_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _THREE_SERIES_DATA, width=400, height=200)
    dark = resolved.style.series_label.dark_companion_palette

    assert _per_series_label_fills(spec) == {
        "Banana": dark[0],
        "Cherry": dark[1],
        "Apple": dark[2],
    }


def test_per_series_degenerate_stacked_bar_strip_matches_chart_order() -> None:
    """https://github.com/dbt-labs/dbt-charts/issues/28 regression, chart<->strip
    agreement: a genuinely degenerate stacked bar (color 1:1 with a
    CATEGORICAL x, no authored sort/stack_order) takes the x-domain-order
    override (query row order), NOT the ordinary sum-ranked total. The
    per-series strip must read that SAME order -- threaded via
    ``ChartSpec.stacked_series_order`` / ``$df_stacked_series_order`` --
    rather than re-deriving its own sum-ranked verdict from raw rows, which
    (before this fix, or if the stamp is ever dropped) disagrees with the
    chart's own query-order legend.
    """
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    payload: dict[str, Any] = {
        "id": "test_chart",
        "type": "bar",
        "x": "quarter",
        "y": "revenue",
        # "category" (not "fruit") because _per_series_layer_order's filter
        # regex hardcodes that field name (see its own callers above).
        "color": "category",
        "query": SqlQuery(sql="SELECT 1", source="test_db"),
        "query_name": "q",
        "stack": "zero",
        "style": {"orientation": "vertical"},
        "support_table": {"entries": [{"per_series": "revenue", "format": "$,.0f"}]},
    }
    chart = TypeAdapter(Chart).validate_python(payload)
    # Deliberately NOT sum-rank-order-equivalent: query order is mango,
    # apple, cherry, but sum-ranked descending (mango=90, cherry=50,
    # apple=10) is mango, cherry, apple -- a dropped stamp falling back to
    # the strip's OWN sum-ranked re-derivation must read differently from
    # the correct, threaded, query-order answer.
    data = [
        {"quarter": "Q1", "category": "mango", "revenue": 90},
        {"quarter": "Q2", "category": "apple", "revenue": 10},
        {"quarter": "Q3", "category": "cherry", "revenue": 50},
    ]
    spec = generate_vega_lite_spec(chart, data, width=400, height=200)

    # Query-order baseline (mango, apple, cherry) -- both the strip and the
    # chart's own paint-scale domain read it unreversed (a degenerate stack
    # has no visual top/bottom to reverse against). Sum-ranked (mango,
    # cherry, apple on either side) would mean the stamp never reached the
    # strip.
    chart_display_order = spec["encoding"]["color"]["scale"]["domain"]
    assert chart_display_order == ["mango", "apple", "cherry"]
    assert _per_series_layer_order(spec) == ["mango", "apple", "cherry"]


def test_per_series_degenerate_stacked_bar_strip_matches_chart_order_when_layered() -> (
    None
):
    """Same regression as the unlayered test above, with an overlay
    ``chart.layers`` line -- ``render_cartesian_overlay`` wraps the base
    spec into a NEW ``ChartSpec(mark="layered", ...)``, which must carry
    the base's own ``stacked_series_order`` across (the way it already
    carries ``base_series_label`` and ``x_label_block_height``) or a
    layered stacked bar's strip silently reverts to re-deriving its own,
    possibly-disagreeing (sum-ranked) verdict."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    payload: dict[str, Any] = {
        "id": "test_chart",
        "type": "bar",
        "x": "quarter",
        "y": "revenue",
        "color": "category",
        "query": SqlQuery(sql="SELECT 1", source="test_db"),
        "query_name": "q",
        "stack": "zero",
        "style": {"orientation": "vertical"},
        "support_table": {"entries": [{"per_series": "revenue", "format": "$,.0f"}]},
    }
    chart = TypeAdapter(Chart).validate_python(payload)
    chart.layers = [LineLayer(type="line", y="target", label="Target")]
    # Same non-sum-rank-order-equivalent values as the unlayered test above.
    data = [
        {"quarter": "Q1", "category": "mango", "revenue": 90, "target": 50},
        {"quarter": "Q2", "category": "apple", "revenue": 10, "target": 50},
        {"quarter": "Q3", "category": "cherry", "revenue": 50, "target": 50},
    ]
    spec = generate_vega_lite_spec(chart, data, width=400, height=200)

    # Query-order baseline (mango, apple, cherry), unreversed on the paint
    # domain (same degenerate-stack rule as the unlayered test above), with
    # the overlay line's own "Target" label appended.
    chart_display_order = spec["layer"][0]["encoding"]["color"]["scale"]["domain"]
    assert chart_display_order == ["mango", "apple", "cherry", "Target"]
    assert _per_series_layer_order(spec) == ["mango", "apple", "cherry"]


def test_layered_stacked_bar_strip_ink_reads_base_layer_color_scale() -> None:
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = _compiled_chart_with_color_and_per_series(chart_type="bar", stack="zero")
    chart.layers = [LineLayer(type="line", y="target", label="Target")]
    data = [{**row, "target": 500.0} for row in _THREE_SERIES_DATA]
    resolved = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)

    spec = generate_vega_lite_spec(chart, data, width=400, height=200)

    assert _per_series_label_fills(spec) == {
        "Banana": resolved.style.series_label.dark_companion_palette[0],
        "Cherry": resolved.style.series_label.dark_companion_palette[1],
        "Apple": resolved.style.series_label.dark_companion_palette[2],
    }


def test_per_series_stacked_bar_with_gradient_color_scale_does_not_crash() -> None:
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "gradient_bar",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "color": "score",
            "stack": "zero",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"per_series": "revenue"}]},
            "style": {
                "orientation": "vertical",
                "color": {"gradient": {"palette": ["#ffffff", "#000000"]}},
            },
        }
    )
    data = [
        {"month": "Jan", "score": 1.0, "revenue": 10.0},
        {"month": "Jan", "score": 2.0, "revenue": 20.0},
    ]
    spec = generate_vega_lite_spec(chart, data, width=400, height=200)

    dark = _BOARD_CONTEXT.dark_companion_palette
    assert _per_series_label_fills(spec) == {"1.0": dark[0], "2.0": dark[1]}


def test_per_series_grouped_bar_strip_is_alphabetical():
    """Grouped (non-stacked) bars don't have a top-to-bottom stack — alphabetical."""
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = _compiled_chart_with_color_and_per_series(chart_type="bar", stack="none")
    _rc = resolve(chart, _THREE_SERIES_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _THREE_SERIES_DATA, width=400, height=200)
    order = _per_series_layer_order(spec)
    assert order == [
        "Apple",
        "Banana",
        "Cherry",
    ], f"grouped bar strip rows must be alphabetical (legend order); got {order}"


def test_per_series_stacked_area_strip_uses_global_total_order():
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = _compiled_chart_with_color_and_per_series(chart_type="area", stack="zero")
    _rc = resolve(chart, _THREE_SERIES_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _THREE_SERIES_DATA, width=400, height=200)
    order = _per_series_layer_order(spec)
    assert order == ["Banana", "Cherry", "Apple"]


def test_layered_stacked_area_strip_ink_matches_base_stack_palette() -> None:
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = _compiled_chart_with_color_and_per_series(chart_type="area", stack="zero")
    chart.layers = [LineLayer(type="line", y="target", label="Target")]
    data = [{**row, "target": 500.0} for row in _THREE_SERIES_DATA]
    resolved = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)

    spec = generate_vega_lite_spec(chart, data, width=400, height=200)

    assert _per_series_label_fills(spec) == {
        "Banana": resolved.style.series_label.dark_companion_palette[0],
        "Cherry": resolved.style.series_label.dark_companion_palette[1],
        "Apple": resolved.style.series_label.dark_companion_palette[2],
    }


def test_per_series_unstacked_area_strip_uses_last_value_order():
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = _compiled_chart_with_color_and_per_series(chart_type="area", stack="none")
    spec = generate_vega_lite_spec(chart, _THREE_SERIES_DATA, width=400, height=200)

    assert _per_series_layer_order(spec) == ["Apple", "Cherry", "Banana"]


def test_per_series_line_strip_by_last_x_y_ascending():
    """Line chart strip: series_order[0] = lowest last-x y (bottom of chart).
    Highest last-x y (chart top) = series_order[N] = visual top of strip.

    _THREE_SERIES_DATA last x (Feb): Apple=110, Cherry=160, Banana=210.
    Ascending by last-x y: Apple(110) → Cherry(160) → Banana(210).
    """
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "line",
            "x": "month",
            "y": "revenue",
            "color": "category",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"per_series": "revenue", "format": "$,.0f"}]
            },
        }
    )
    _rc = resolve(chart, _THREE_SERIES_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _THREE_SERIES_DATA, width=400, height=200)
    order = _per_series_layer_order(spec)
    assert order == [
        "Apple",
        "Cherry",
        "Banana",
    ], (
        f"line strip: series_order must be ascending by last-x y (lowest = series_order[0]); got {order}"
    )


def test_per_series_stacked_bar_position_top_adjacency_invariant():
    """position=top stacked bar: series_order[0] = largest sum (Banana=410 = baseline).
    For position:top, row 0 is at the VISUAL BOTTOM of the strip (closest to chart top).
    The chart top shows the SMALLEST sum (Apple=210).  series_order[0]=Banana (largest)
    means row 0 = Banana, and the visual top of the strip (row 2) = Apple.

    _THREE_SERIES_DATA sums: Apple=210, Cherry=310, Banana=410.
    Descending by sum: Banana(410) → Cherry(310) → Apple(210).
    """
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "color": "category",
            "stack": "zero",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"per_series": "revenue", "format": "$,.0f"}]
            },
            "style": {"orientation": "vertical", "support_table": {"position": "top"}},
        }
    )
    _rc = resolve(chart, _THREE_SERIES_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _THREE_SERIES_DATA, width=400, height=200)
    order = _per_series_layer_order(spec)
    # series_order[0] = Banana (largest sum = baseline); visual top of strip = Apple.
    assert order == ["Banana", "Cherry", "Apple"], (
        f"position=top stacked bar: series_order[0] must be Banana (largest sum = baseline); "
        f"got {order}"
    )


# ── per_series COLUMN order regression tests ──
#
# Column order reads left to right in the chart's own stack order, on both
# placements. _column_edges lists every column in authored/series_order
# order, left to right on screen, regardless of whether the block sits at
# position "left" or "right" -- a reader maps visual column N to the chart's
# own segment N by screen position, the same way the source/aggregate columns
# already read left to right in authored order. Series order is never
# reversed to chase which screen edge is "closest to the plot".


def _column_layer_order(spec: dict[str, Any], color_field: str) -> list[str]:
    """Series values of a per_series column block's value cells, in screen
    left-to-right x order -- read from each cell's own pixel-literal x and
    stack-filter transform, not from a sorted list.
    """
    import re

    cells: list[tuple[float, str]] = []
    for layer in spec.get("layer", []):
        mark = layer.get("mark")
        if not (isinstance(mark, dict) and mark.get("type") == "text"):
            continue
        encoding = layer.get("encoding", {})
        if encoding.get("text", {}).get("field") == "__header":
            continue
        x_val = encoding.get("x", {}).get("value")
        if x_val is None:
            continue
        for transform in layer.get("transform", []) or []:
            f = transform.get("filter")
            if isinstance(f, str):
                m = re.search(rf"datum\['{color_field}'\] === '([^']+)'", f)
                if m:
                    cells.append((x_val, m.group(1)))
                    break
    cells.sort(key=lambda pair: pair[0])
    return [series for _, series in cells]


@pytest.mark.parametrize("position", ["left", "right"])
def test_per_series_column_order_matches_painted_stack_order_both_placements(
    position,
):
    """Regression: cell 26N of the demo board rendered per_series column
    headers scrambled (S1 S2 S6 S3 S5 S4 Total) -- neither the series' own
    order nor the chart's own painted stack order. The column path must read
    left to right in the chart's own stack order, on both placements (see
    the module comment above).

    _THREE_SERIES_DATA sums: Apple=210, Cherry=310, Banana=410. The chart's
    own stack order (series_order, "value" order, largest at the baseline):
    Banana → Cherry → Apple -- painted left to right, unreversed, on either
    placement.
    """
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "color": "category",
            "stack": "zero",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"per_series": "revenue", "format": "$,.0f"}]
            },
            "style": {
                "orientation": "horizontal",
                "support_table": {"position": position},
            },
        }
    )
    resolved_chart = resolve(
        chart, _THREE_SERIES_DATA, chart_style_context=_BOARD_CONTEXT
    )
    assert resolved_chart.orientation == "horizontal"
    spec = generate_vega_lite_spec(chart, _THREE_SERIES_DATA, width=400, height=200)

    order = _column_layer_order(spec, "category")
    assert order == ["Banana", "Cherry", "Apple"], (
        f"position={position!r}: painted column order (left to right on "
        f"screen) must match the chart's own stack order; got {order}"
    )


# =============================================================================
# CADENCE DOWNSAMPLING — band cadence finer than label cadence
# =============================================================================

# 24 monthly ISO date rows (2 years) — same shape as interval-label-centering-lab.
_MONTHLY_2Y_DATA = [
    {
        "date": f"{'2022' if i < 12 else '2023'}-{(i % 12) + 1:02d}-01",
        "revenue": float(1000 + i * 100),
    }
    for i in range(24)
]


def _compiled_bar_monthly_quarterly_labels(entries, axis_type="ordinal"):
    """Bar chart with monthly ISO date x, quarterly labels (lab chart 1/2/4 shape)."""
    style_axis_x: dict[str, Any] = {
        "time_unit": "yearmonth",
        "labels": {"time_unit": "yearquarter"},
    }
    if axis_type == "temporal":
        style_axis_x["type"] = "temporal"
    return TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "date",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": entries},
            "style": {"axis_x": style_axis_x},
        }
    )


def _strip_text_layers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Return text layers from the spec that are data-bound strip cells."""
    return [
        layer
        for layer in spec.get("layer", [])
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and "field" in layer.get("encoding", {}).get("x", {})
    ]


def _period_filter_exprs(layer: dict[str, Any]) -> list[str]:
    """Return all filter expressions from a layer's transforms."""
    return [t["filter"] for t in layer.get("transform", []) if "filter" in t]


def _has_strip_thinning(layer: dict[str, Any]) -> bool:
    transforms = layer.get("transform", [])
    return any("window" in t for t in transforms) or any(
        "indexof" in expr or "utcmonth" in expr or "utcdate" in expr
        for expr in _period_filter_exprs(layer)
    )


def test_ordinal_monthly_bands_quarterly_labels_adds_period_filter():
    # Regression: 24 monthly bands + 8 quarterly labels → support_table must show
    # 8 cells (one per quarter), not 24. Fix: period-opener filter on strip layers.
    chart = _compiled_bar_monthly_quarterly_labels(
        [{"source": "revenue", "format": "$,.0f", "label": "Revenue"}]
    )
    _rc = resolve(chart, _MONTHLY_2Y_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _MONTHLY_2Y_DATA, width=600, height=200)
    assert "layer" in spec
    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected at least one strip text layer"
    for layer in text_layers:
        exprs = _period_filter_exprs(layer)
        # Ordinal and temporal paths share one canonical opens_label_period gate
        # now (utcmonth/utcdate), not a separate indexof-list mechanism.
        assert any("utcmonth" in e or "indexof" in e for e in exprs), (
            "ordinal monthly-band + quarterly-label chart must have a "
            "period-opener filter on every strip text layer; "
            f"transforms: {layer.get('transform')}"
        )


def test_temporal_monthly_bands_quarterly_labels_adds_period_filter():
    # Same fix for the temporal escape-hatch path (axis_x.type: temporal).
    # Charts 1 and 2 in interval-label-centering-lab use this path.
    chart = _compiled_bar_monthly_quarterly_labels(
        [{"source": "revenue", "format": "$,.0f", "label": "Revenue"}],
        axis_type="temporal",
    )
    _rc = resolve(chart, _MONTHLY_2Y_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _MONTHLY_2Y_DATA, width=600, height=200)
    assert "layer" in spec
    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected at least one strip text layer"
    for layer in text_layers:
        exprs = _period_filter_exprs(layer)
        # Temporal path: expects utcmonth % 3 === 0 (quarter opener gate)
        assert any("utcmonth" in e and "% 3 === 0" in e for e in exprs), (
            "temporal monthly-band + quarterly-label chart must have a utcmonth % 3 "
            "period-opener filter on every strip text layer; "
            f"transforms: {layer.get('transform')}"
        )


def test_matching_band_and_label_cadence_no_period_filter():
    # When band cadence == label cadence (quarterly bars + quarterly labels),
    # no period-opener filter should be added.
    quarterly_data = [
        {"date": f"{y}-{m:02d}-01", "revenue": float(1000)}
        for y in (2022, 2023)
        for m in (1, 4, 7, 10)
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "date",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "format": "$,.0f"}]},
            "style": {
                "axis_x": {
                    "time_unit": "yearquarter",
                    "labels": {"time_unit": "yearquarter"},
                }
            },
        }
    )
    _rc = resolve(chart, quarterly_data, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, quarterly_data, width=600, height=200)
    assert "layer" in spec
    text_layers = _strip_text_layers(spec)
    assert text_layers
    for layer in text_layers:
        exprs = _period_filter_exprs(layer)
        assert not any("indexof" in e or "utcmonth" in e for e in exprs), (
            "quarterly bands + quarterly labels must NOT add a period-opener filter; "
            f"got transforms: {layer.get('transform')}"
        )


def test_per_series_monthly_bands_quarterly_labels_adds_period_filter():
    # Regression: per_series layers must also get the period-opener filter inserted
    # after the per-series color filter and before sampling. Verify transform ordering.
    monthly_two_series = [
        {
            "date": f"{'2022' if i < 12 else '2023'}-{(i % 12) + 1:02d}-01",
            "revenue": float(1000 + i * 100),
            "category": "A" if i % 2 == 0 else "B",
        }
        for i in range(24)
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "date",
            "y": "revenue",
            "color": "category",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"per_series": "revenue", "format": "$,.0f"}]
            },
            "style": {
                "axis_x": {
                    "time_unit": "yearmonth",
                    "labels": {"time_unit": "yearquarter"},
                }
            },
        }
    )
    _rc = resolve(chart, monthly_two_series, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, monthly_two_series, width=600, height=200)
    assert "layer" in spec
    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected per_series strip text layers"
    for layer in text_layers:
        transforms = layer.get("transform", [])
        exprs = _period_filter_exprs(layer)
        # Period-opener filter must be present — ordinal and temporal paths
        # share one canonical opens_label_period gate (utcmonth), not a
        # separate indexof-list mechanism.
        assert any("utcmonth" in e for e in exprs), (
            "per_series monthly-band + quarterly-label layer must have period-opener filter; "
            f"transforms: {transforms}"
        )
        # Series filter must appear before period-opener filter
        series_idx = next(
            (i for i, t in enumerate(transforms) if "category" in t.get("filter", "")),
            None,
        )
        period_idx = next(
            (i for i, t in enumerate(transforms) if "utcmonth" in t.get("filter", "")),
            None,
        )
        assert series_idx is not None, f"no series filter in transforms: {transforms}"
        assert period_idx is not None, f"no period filter in transforms: {transforms}"
        assert series_idx < period_idx, (
            f"series filter (idx {series_idx}) must precede period filter (idx {period_idx})"
        )


# ---------------------------------------------------------------------------
# Grouped-by-default bars: strip rows must be alphabetical (not reverse).
# Regression for the is_stacked_bar predicate that was not guarded against
# the new stack=None + color → grouped default.
# ---------------------------------------------------------------------------


_PER_SERIES_DATA = [
    {"month": "Jan", "revenue": 100.0, "segment": "Apple"},
    {"month": "Jan", "revenue": 60.0, "segment": "Banana"},
    {"month": "Jan", "revenue": 80.0, "segment": "Cherry"},
    {"month": "Feb", "revenue": 120.0, "segment": "Apple"},
    {"month": "Feb", "revenue": 70.0, "segment": "Banana"},
    {"month": "Feb", "revenue": 90.0, "segment": "Cherry"},
]


def _series_filter_order(spec: dict[str, Any]) -> list[str]:
    """Return the per-series strip row values in the order they appear in spec layers.

    Endpoint labels may have wrapped the chart in hconcat/vconcat — unwrap to
    the real chart pane first (chart_pane() is a no-op otherwise).
    """
    result: list[str] = []
    for layer in chart_pane(spec).get("layer", []):
        for t in layer.get("transform", []):
            f = t.get("filter", "")
            if "segment" in f and "===" in f:
                # Extract 'Apple' from "datum['segment'] === 'Apple'"
                val = f.split("===")[1].strip().strip("'")
                if val not in result:
                    result.append(val)
    return result


def test_per_series_grouped_bar_strip_rows_are_alphabetical():
    """Grouped-by-default (stack=None + color) bars must emit strips alphabetically.

    Previously is_stacked_bar fired for stack=None, emitting reverse-alphabetical
    order as if the bars formed a stack (alpha-first at bottom = alpha-last at top).
    Grouped bars carry no z-order pin; VL renders them alphabetical left-to-right.
    """
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "color": "segment",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"per_series": "revenue"}]},
            "style": {"orientation": "vertical"},
        }
    )
    _rc = resolve(chart, _PER_SERIES_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _PER_SERIES_DATA, width=400, height=200)
    order = _series_filter_order(spec)
    assert order == ["Apple", "Banana", "Cherry"], (
        f"Grouped-by-default bar strip rows must be alphabetical; got {order}. "
        "Stacked bars use reverse-alphabetical (alpha-last at top) but grouped "
        "bars have no stack — they follow VL's alphabetical nominal default."
    )


# ---------------------------------------------------------------------------
# Period filter must NOT fire when bands are wide enough to show all cells.
# Regression: for weekly ISO-date data over a short range, the ordinal
# bucketed-time path set axis.values to 2 monthly openers and the period
# filter restricted strip cells to those 2 positions — making the data
# table look like scattered numbers instead of a proper cell-per-band table.
# ---------------------------------------------------------------------------


# 8 weekly ISO dates spanning Jan–Feb 2024 (one per calendar week).
_WEEKLY_8W_DATA = [
    {"week": f"2024-{m:02d}-{d:02d}", "revenue": float(1000 + i * 100)}
    for i, (m, d) in enumerate(
        [(1, 7), (1, 14), (1, 21), (1, 28), (2, 4), (2, 11), (2, 18), (2, 25)]
    )
]

_MONTHLY_11M_DATA = [
    {"month": month, "revenue": value}
    for month, value in zip(
        [
            "2025-06-01",
            "2025-07-01",
            "2025-08-01",
            "2025-09-01",
            "2025-10-01",
            "2025-11-01",
            "2025-12-01",
            "2026-01-01",
            "2026-02-01",
            "2026-03-01",
            "2026-04-01",
        ],
        [8.43, 27.85, 24.02, 13.98, 31.82, 16.15, 5.47, 74.95, 0.15, 30.48, 16.87],
        strict=True,
    )
]


def _compiled_bar_monthly_full_cadence(entries, chart_format: str | None = None):
    chart: dict[str, Any] = {
        "id": "monthly_bar",
        "type": "bar",
        "x": "month",
        "y": "revenue",
        "query": SqlQuery(sql="SELECT 1", source="test_db"),
        "query_name": "q",
        "support_table": {"entries": entries},
    }
    if chart_format is not None:
        chart["format"] = chart_format
    return TypeAdapter(Chart).validate_python(chart)


def test_sparse_weekly_ordinal_no_period_filter():
    """8 weekly bands at 576 px (72 px/band) must NOT have a period filter.

    The ordinal bucketed-time path sets axis.values to monthly openers (2 values
    for a 2-month range) and previously filtered strip cells to those 2 positions.
    When band width >= 45 px all cells fit — the period filter must be suppressed
    so the strip shows one value per weekly bar, not one per month.

    Pre-fix: filter expression `indexof([...], datum['week']) >= 0` is present.
    Post-fix: no period filter — all 8 cells render.
    """
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "weekly_bar",
            "type": "bar",
            "x": "week",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"source": "revenue", "format": "$,.0f", "label": "Rev"}]
            },
        }
    )
    # 576 px / 8 bands = 72 px/band — well above the 45 px min
    _rc = resolve(chart, _WEEKLY_8W_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _WEEKLY_8W_DATA, width=576, height=200)
    assert "layer" in spec
    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected at least one strip text layer"
    for layer in text_layers:
        exprs = _period_filter_exprs(layer)
        assert not any("indexof" in e for e in exprs), (
            "8 weekly bands at 576 px must NOT have a period filter — bands are "
            "wide enough (72 px each) to show all cells without overlap. "
            f"Got transforms: {layer.get('transform')}"
        )


def test_dense_monthly_ordinal_period_filter_fires_at_narrow_width():
    """24 monthly bands at 300 px (12.5 px/band) MUST have a period filter.

    When bands are too narrow to show all cells (< 45 px each), the period
    filter must thin strip cells to label-period openers. This test ensures
    the band-density guard only suppresses the filter for sparse data.

    Pre-fix: filter already present (passes incidentally).
    Post-fix: filter still present (the new guard doesn't interfere).
    """
    chart = _compiled_bar_monthly_quarterly_labels(
        [{"source": "revenue", "format": "$,.0f"}]
    )
    # 300 px / 24 bands = 12.5 px/band — below the 45 px threshold
    _rc = resolve(chart, _MONTHLY_2Y_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _MONTHLY_2Y_DATA, width=300, height=200)
    assert "layer" in spec
    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected at least one strip text layer"
    any_period_filter = any(
        any("utcmonth" in e for e in _period_filter_exprs(layer))
        for layer in text_layers
    )
    assert any_period_filter, (
        "24 monthly bands at 300 px (12.5 px/band) must have a period filter — "
        "bands are too narrow to show all cells without overlap. "
        "The band-density guard must not suppress filtering for dense data."
    )


def test_fitting_monthly_ordinal_cadence_does_not_calendar_thin_strip():
    """11 ordinal monthly bars at 420px keep every non-overlapping label.

    The month names fit at roughly 38 px per band. A previous edge/gap penalty
    stepped this case to quarters even though the rendered labels did not
    overlap. The support-table strip must not inherit a calendar-period filter;
    its value cells may still apply their own generic density thinning.

    V2 bar places shared encoding at the spec top level (not in layer[0]), so
    we read spec["encoding"]["x"] directly.
    """
    chart = _compiled_bar_monthly_full_cadence(
        [{"source": "revenue", "format": "$,.2f"}]
    )
    _rc = resolve(chart, _MONTHLY_11M_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _MONTHLY_11M_DATA, width=420, height=200)
    base_x = spec["encoding"]["x"]
    axis = base_x.get("axis", {})
    assert base_x.get("type") == "ordinal"
    # Calendar cadence resolves natively, never through parity.
    assert axis.get("labelOverlap") is False
    assert "utcmonth(toDate(datum.value)) % 3 === 0" not in axis.get("labelExpr", "")
    # Strip does not receive a calendar-period gate from the unthinned axis.
    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected at least one strip text layer"
    filter_exprs = [e for layer in text_layers for e in _period_filter_exprs(layer)]
    assert not any("utcmonth" in expr or "utcdate" in expr for expr in filter_exprs)


def test_support_table_strip_renders_inherited_chart_format():
    """A source row whose format was resolved from chart.format at normalize time
    must produce a formatted strip cell.

    normalize_chart() now resolves entry.format = chart.format for entries
    reading chart.y before the chart reaches the render layer. This test
    constructs the already-normalized Chart (bypassing normalize_chart) with
    the format already set on the entry, reflecting the post-normalize state.
    """
    chart = _compiled_bar_monthly_full_cadence(
        [{"source": "revenue", "label": "Revenue", "format": "~s"}],
        chart_format="~s",
    )
    _rc = resolve(chart, _MONTHLY_11M_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _MONTHLY_11M_DATA, width=420, height=200)
    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected at least one strip text layer"
    calc_exprs = [
        t["calculate"]
        for layer in text_layers
        for t in layer.get("transform", [])
        if "calculate" in t
    ]
    assert any("format(datum[\"revenue\"], '~s')" in expr for expr in calc_exprs), (
        "A support_table entry with format set must produce a formatted strip cell. "
        f"Got calculate transforms: {calc_exprs}"
    )


def test_per_series_stacked_bar_strip_rows_are_reverse_alphabetical():
    """Explicit stack=zero bars: series_order[0] = largest sum (at baseline).
    Visual top of strip (series_order[N]) = smallest sum = chart top.

    _PER_SERIES_DATA sums: Apple=220, Cherry=170, Banana=130.
    Descending by sum: Apple → Cherry → Banana.
    """
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "t",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "color": "segment",
            "stack": "zero",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"per_series": "revenue"}]},
            "style": {"orientation": "vertical"},
        }
    )
    _rc = resolve(chart, _PER_SERIES_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _PER_SERIES_DATA, width=400, height=200)
    order = _series_filter_order(spec)
    assert order == ["Apple", "Cherry", "Banana"], (
        f"Stacked bar (value order): series_order must be descending by global sum "
        f"(largest = series_order[0]); got {order}."
    )


# =============================================================================
# by_measure multi-y support_table pipeline tests
# =============================================================================

# ISO date x so the chart renders as a temporal vertical bar (has explicit x enc).
_MULTI_Y_DATA = [
    {"date": "2024-01-01", "revenue": 100.0, "cost": 60.0},
    {"date": "2024-02-01", "revenue": 200.0, "cost": 120.0},
    {"date": "2024-03-01", "revenue": 150.0, "cost": 90.0},
]


def test_by_measure_multi_y_bar_compiles_and_has_two_strip_rows():
    """Multi-y bar with by_measure entries produces a spec with two strip rows.

    Each by_measure entry expands to one visual row — two entries → two rows.
    The spec must be a valid layered spec with at least two x-bound text layers.
    """
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "multi_y_bar",
            "type": "bar",
            "x": "date",
            "y": ["revenue", "cost"],
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [
                    {"per_series": "revenue", "by_measure": True},
                    {"per_series": "cost", "by_measure": True},
                ]
            },
        }
    )
    _rc = resolve(chart, _MULTI_Y_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _MULTI_Y_DATA, width=400, height=200)
    assert "layer" in spec, "multi-y by_measure chart must produce a layered spec"
    text_layers = _strip_text_layers(spec)
    assert len(text_layers) >= 2, (
        f"Expected at least 2 strip text layers (one per by_measure entry); "
        f"got {len(text_layers)}. Layers: {[lyr.get('encoding') for lyr in text_layers]}"
    )


def test_by_measure_strip_rows_reference_measure_fields_directly():
    """by_measure strip rows must read the named measure field without a series filter.

    Unlike normal per_series rows (which have a 'datum[color_field] ===' filter),
    by_measure rows read the measure field directly — no color groupby transform.
    """
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "multi_y_bar",
            "type": "bar",
            "x": "date",
            "y": ["revenue", "cost"],
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [
                    {"per_series": "revenue", "by_measure": True},
                    {"per_series": "cost", "by_measure": True},
                ]
            },
        }
    )
    _rc = resolve(chart, _MULTI_Y_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _MULTI_Y_DATA, width=400, height=200)
    text_layers = _strip_text_layers(spec)
    # Collect all field names referenced by strip text layers.
    text_fields = {
        layer["encoding"]["text"]["field"]
        for layer in text_layers
        if "text" in layer.get("encoding", {})
    }
    # Must reference the measure fields (possibly after a format calculate as
    # __support_table_N, but the per_series column must appear in transforms).
    # Simpler check: no layer has a series-equality filter transform.
    for layer in text_layers:
        for t in layer.get("transform", []):
            f = t.get("filter", "")
            assert "===" not in f, (
                f"by_measure strip rows must NOT have a series equality filter; "
                f"got filter: {f!r}"
            )
    # Revenue or cost must be reachable from the text encoding or transforms.
    all_transform_fields = set()
    for layer in text_layers:
        for t in layer.get("transform", []):
            if "calculate" in t:
                all_transform_fields.add(t["calculate"])
    reachable = text_fields | all_transform_fields
    assert any("revenue" in str(r) for r in reachable) or any(
        "cost" in str(r) for r in reachable
    ), (
        f"by_measure strip layers must reference 'revenue' or 'cost'; "
        f"text_fields={text_fields}, transforms={all_transform_fields}"
    )


# =============================================================================
# Continuous-axis tick density: period_filter supersedes sampling
# =============================================================================
#
# When a chart has a finer-grained x-axis than its label cadence (e.g. 365
# daily rows displayed with monthly labels), the engine applies a
# label_period_filter_expr to keep one cell per label period. A sampling
# transform must NOT also be injected: sampling is computed from raw data row
# count (365 > 40 → step=10), but with the period filter already restricting
# output to 12 cells, sampling would further thin to 2 cells — an incorrect
# result. When period_filter is in effect, sampling must be suppressed.


_DAILY_365_DATA = [
    {
        "date": (datetime.date(2024, 1, 1) + datetime.timedelta(days=i)).isoformat(),
        "revenue": float(100 + i),
    }
    for i in range(365)
]


def test_daily_data_with_monthly_labels_no_sampling_transform():
    """365 daily rows with monthly labels must NOT have sampling transforms.

    Regression: when n_x_values > CHART_SUPPORT_TABLE_MAX_X_TICKS (365 > 40),
    the validator computed sampling_step = ceil(365/40) = 10. Combined with
    the label_period_filter_expr (which restricts to ~12 monthly openers),
    sampling further thinned to ~2 cells. The strip showed 2 cells instead
    of 12 — far fewer than the visible axis tick count.

    Fix: when label_period_filter_expr is in effect, set sampling_step = 1
    (no sampling). The period filter is already the correct thinning mechanism.
    """
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "daily_revenue",
            "type": "bar",
            "x": "date",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"aggregate": "sum", "source": "revenue", "format": "~s"}]
            },
            "style": {
                "axis_x": {
                    "time_unit": "yearmonthdate",
                    "labels": {"time_unit": "yearmonth"},
                }
            },
        }
    )
    _rc = resolve(chart, _DAILY_365_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _DAILY_365_DATA, width=600, height=200)
    assert "layer" in spec
    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected at least one strip text layer"

    # The period filter must be present (monthly labels on daily bands)
    for layer in text_layers:
        exprs = _period_filter_exprs(layer)
        assert any("utcmonth" in e or "utcdate" in e for e in exprs), (
            "daily data with monthly labels must have a period-opener filter "
            "(utcmonth/utcdate gate); "
            f"transforms: {layer.get('transform')}"
        )

    # Sampling must NOT be present — the period filter handles thinning
    for layer in text_layers:
        for t in layer.get("transform", []):
            if "window" in t:
                ops = [w.get("op") for w in (t.get("window") or [])]
                assert "row_number" not in ops, (
                    "strip layers must NOT have sampling (row_number window) when "
                    "label_period_filter_expr is active — the period filter already "
                    "restricts to label-period openers; adding sampling over-thins "
                    f"the strip. Transforms: {layer.get('transform')}"
                )


def test_period_filtered_strip_with_predefined_format_shows_all_openers():
    """A period-filtered strip using a predefined format must not over-thin.

    Regression: the band-budget expansion for anchor affixes (band_budget_w >
    max_cell_w) bypassed the unconditional `sampling_step = 1` reset, so a
    365-row chart with `currency` emitted a `% 46 === 0` sampling
    transform on top of the monthly period filter — collapsing 12 openers to 1.
    """
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "daily_revenue",
            "type": "bar",
            "x": "date",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [
                    {"aggregate": "sum", "source": "revenue", "format": "currency"}
                ]
            },
            "style": {
                "axis_x": {
                    "time_unit": "yearmonthdate",
                    "labels": {"time_unit": "yearmonth"},
                }
            },
        }
    )
    resolve(chart, _DAILY_365_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _DAILY_365_DATA, width=600, height=200)
    assert "layer" in spec
    text_layers = _strip_text_layers(spec)
    assert text_layers

    for layer in text_layers:
        for t in layer.get("transform", []):
            if "window" in t:
                ops = [w.get("op") for w in (t.get("window") or [])]
                assert "row_number" not in ops, (
                    "strip layers must NOT have a sampling row_number window when "
                    "label_period_filter_expr is active, even with a predefined format "
                    f"that widens the band budget. Transforms: {layer.get('transform')}"
                )


# ---------------------------------------------------------------------------
# V2 renderer parity: support_table strip thinning
# ---------------------------------------------------------------------------


def _bar_yearmonth_with_support_table() -> Chart:
    return TypeAdapter(Chart).validate_python(
        {
            "id": "ym_bar",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
        }
    )


def test_v2_bar_yearmonth_dense_support_table_thins_via_period_filter(
    monkeypatch: Any,
) -> None:
    """48 ordinal yearmonth bars at 600px: bar's x path now normalizes
    "YYYY-MM" bucket strings to ISO (same normalize_labeled_temporal call
    line already made — see value-driven-axis-type-inference), and the
    label cadence ladder auto-steps month labels to quarter at this density
    (cadence-ladder task — replaces the old shrink/tilt behavior this test
    name used to describe). The strip must thin to match via the
    opens_label_period gate (not window-modulo).
    """
    data = _yearmonth_data(48)
    chart = _bar_yearmonth_with_support_table()
    spec = _render_v2_spec(chart, data, width=600, height=300, monkeypatch=monkeypatch)

    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected at least one support_table strip text layer"

    for tl in text_layers:
        exprs = _period_filter_exprs(tl)
        assert any("utcmonth" in e for e in exprs), (
            "48 yearmonth labels at 600px must thin via the quarter-opener "
            f"period filter. Transforms: {tl.get('transform')}"
        )
        for t in tl.get("transform", []):
            assert "window" not in t, (
                "period-filter thinning must not also apply window-modulo. "
                f"Transforms: {tl.get('transform')}"
            )


def _weekly_date_data(n: int) -> list[dict]:
    """n weekly true-DATE rows, one week apart, starting 2024-01-01."""
    start = datetime.date(2024, 1, 1)
    return [
        {"week": start + datetime.timedelta(weeks=i), "revenue": float(i) * 100}
        for i in range(n)
    ]


def _daily_date_data(n: int) -> list[dict]:
    start = datetime.date(2024, 1, 1)
    return [
        {"day": start + datetime.timedelta(days=i), "revenue": float(i) * 100}
        for i in range(n)
    ]


def _bar_daily_with_support_table() -> Chart:
    return TypeAdapter(Chart).validate_python(
        {
            "id": "daily_bar",
            "type": "bar",
            "x": "day",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
        }
    )


def test_v2_bar_daily_monday_promotion_matches_support_table_filter(
    monkeypatch: Any,
) -> None:
    # Render-local (non-authored) promotion: bar's axis keeps a tick under
    # every daily band (`values` stays the full 60 days, ticks restored) —
    # only the label TEXT thins to Mondays, via the same "utcday" gate the
    # support_table attachment filters its own cells with.
    data = _daily_date_data(60)
    chart = _bar_daily_with_support_table()
    spec = _render_v2_spec(chart, data, width=300, height=300, monkeypatch=monkeypatch)

    all_day_values = [row["day"].isoformat() for row in data]
    assert spec["encoding"]["x"]["axis"]["values"] == all_day_values
    assert spec["encoding"]["x"]["axis"]["ticks"] is True
    assert "utcday" in spec["encoding"]["x"]["axis"]["labelExpr"]
    text_layers = _strip_text_layers(spec)
    assert text_layers
    for layer in text_layers:
        assert any("utcday" in expression for expression in _period_filter_exprs(layer))


def _line_weekly_with_support_table() -> Chart:
    return TypeAdapter(Chart).validate_python(
        {
            "id": "weekly_line",
            "type": "line",
            "x": "week",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
        }
    )


def test_v2_line_sparse_weekly_temporal_support_table_shows_every_cell(
    monkeypatch: Any,
) -> None:
    """Sparse weekly-grain temporal line (5 true-DATE points) must show one
    support_table cell per data point, not collapse to the month-opener gate.

    Regression: line/area now default to continuous temporal for any
    BUCKETED_CALENDAR_UNITS grain (value-driven-axis-type-inference). The
    temporal branch of _label_period_filter_expr computed label_tu (weekly ->
    yearmonth) and applied the month-opener gate unconditionally whenever
    label_tu != base_time_unit — with no density guard, unlike the ordinal
    branch's `spec_width / len(x_distinct) >= 45.0` check. 5 weekly points at
    a normal chart width must render all 5 cells.
    """
    data = _weekly_date_data(5)
    chart = _line_weekly_with_support_table()
    spec = _render_v2_spec(chart, data, width=600, height=300, monkeypatch=monkeypatch)

    assert spec["encoding"]["x"]["type"] == "temporal"
    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected at least one support_table strip text layer"
    for tl in text_layers:
        assert not _period_filter_exprs(tl), (
            "5 sparse weekly points must show every cell — no period filter "
            f"expected. Transforms: {tl.get('transform')}"
        )
        assert not _has_strip_thinning(tl), (
            f"5 sparse weekly points must not thin. Transforms: {tl.get('transform')}"
        )


def test_v2_line_dense_weekly_temporal_support_table_still_thins(
    monkeypatch: Any,
) -> None:
    """Dense weekly-grain temporal line still thins via the month-opener gate."""
    data = _weekly_date_data(60)
    chart = _line_weekly_with_support_table()
    spec = _render_v2_spec(chart, data, width=600, height=300, monkeypatch=monkeypatch)

    assert spec["encoding"]["x"]["type"] == "temporal"
    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected at least one support_table strip text layer"
    for tl in text_layers:
        # Temporal thinning is a raw opens_label_period boolean filter, not the
        # ordinal path's indexof(...) list — _has_strip_thinning only checks
        # for "window" or "indexof", so assert on the filter directly.
        assert _period_filter_exprs(tl), (
            "60 dense weekly points must thin the strip via a period filter. "
            f"Transforms: {tl.get('transform')}"
        )


def test_v2_line_dense_monthly_support_table_matches_flushed_axis_thinning(
    monkeypatch: Any,
) -> None:
    """The strip uses the same quarter-opener cadence as flushed axis labels."""
    data = [
        {
            "date": f"{2024 + month // 12:04d}-{month % 12 + 1:02d}-01",
            "revenue": float(month),
        }
        for month in range(24)
    ]
    chart = _compiled_line_temporal_with_support_table([{"source": "revenue"}])
    spec = _render_v2_spec(chart, data, width=700, height=300, monkeypatch=monkeypatch)

    assert (
        "utcmonth(toDate(datum.value)) % 3 === 0"
        in spec["encoding"]["x"]["axis"]["labelExpr"]
    )
    text_layers = _strip_text_layers(spec)
    assert text_layers
    for layer in text_layers:
        assert any("utcmonth" in expr for expr in _period_filter_exprs(layer)), (
            "support_table cells must use the axis's quarter-opener filter. "
            f"Transforms: {layer.get('transform')}"
        )


def test_v2_line_support_table_respects_chart_local_flush_override(
    monkeypatch: Any,
) -> None:
    """A chart-local flush override keeps the strip and axis on monthly labels."""
    data = [
        {
            "date": f"{2024 + month // 12:04d}-{month % 12 + 1:02d}-01",
            "revenue": float(month),
        }
        for month in range(24)
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "monthly_line",
            "type": "line",
            "x": "date",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue"}]},
            "style": {"axis_x": {"labels": {"flush": False}}},
        }
    )
    spec = _render_v2_spec(chart, data, width=700, height=300, monkeypatch=monkeypatch)

    assert "% 3 === 0" not in spec["encoding"]["x"]["axis"]["labelExpr"]
    for layer in _strip_text_layers(spec):
        assert not _period_filter_exprs(layer), (
            "the strip must retain every monthly cell when label flushing is off. "
            f"Transforms: {layer.get('transform')}"
        )


def test_v2_bar_yearmonth_x_emits_axis_values(monkeypatch: Any) -> None:
    """V2 bar chart with ordinal yearmonth x emits axis.values on the x encoding.

    axis.values is the tick-visibility set that _label_period_filter_expr reads to
    build the indexof period filter on support_table strips.  Without it the strip
    falls back to window-modulo sampling and cells can appear between tick marks.
    """
    data = _yearmonth_data(12)
    chart = _bar_yearmonth_with_support_table()

    # V2
    spec_v2 = _render_v2_spec(
        chart, data, width=600, height=300, monkeypatch=monkeypatch
    )
    enc_x_v2 = spec_v2.get("encoding", {}).get("x", {})
    assert enc_x_v2.get("axis", {}).get("values"), (
        "V2 bar chart with yearmonth ordinal x must emit axis.values; "
        "build_cartesian_x_encoding must call detect_time_unit for 'ordinal' data"
    )


def test_v2_bar_half_year_ordinal_renders_without_raising(monkeypatch: Any) -> None:
    """V2 bar chart with half-year ordinal x (YYYY-Hn) renders without raising.

    Half-year strings match DATE_LIKE_PATTERNS so infer_vega_type_from_data
    returns "ordinal", but detect_time_unit cannot parse them and raises.
    build_cartesian_x_encoding must degrade to time_unit=None on ValueError
    so these charts fall back to window sampling, matching V1's plain-ordinal
    behavior, instead of crashing the renderer.
    """
    flat = TypeAdapter(Chart).validate_python(
        {
            "id": "hy_bar",
            "type": "bar",
            "x": "period",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "label": "Revenue"}]},
            "style": {"orientation": "vertical"},
        }
    )
    chart = flat
    data = [
        {"period": f"20{20 + i // 2:02d}-H{(i % 2) + 1}", "revenue": float(i)}
        for i in range(12)
    ]
    # Must not raise ValueError
    spec = _render_v2_spec(chart, data, width=600, height=300, monkeypatch=monkeypatch)
    assert spec is not None


# =============================================================================
# BOARD RENDER PATH — render_resolved_chart is called directly by
# rendering.py's board pipeline (no chart.support_table available, only
# resolved.support_table). generate_vega_lite_spec / render_chart apply the
# post-pass themselves and don't exercise this path.
# =============================================================================


def _strip_text_layers_from_vl(spec: dict[str, Any]) -> list[dict[str, Any]]:
    main = spec["hconcat"][0] if "hconcat" in spec else spec
    return [
        layer
        for layer in main.get("layer", [])
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "text"
    ]


def test_board_path_render_resolved_chart_emits_support_table_strip_for_cartesian():
    # Regression: render_resolved_chart (the function rendering.py's board
    # pipeline calls directly) previously never applied the support_table
    # post-pass, so attached support tables silently disappeared from boards
    # while generate_vega_lite_spec/render_chart still rendered them fine.
    from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

    chart = _compiled_bar_with_support_table([{"source": "revenue", "format": "$.2s"}])
    compiled = chart
    resolved = resolve(compiled, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT)
    assert resolved.support_table is not None

    artifact = render_resolved_chart(
        resolved, _SAMPLE_DATA, _BOARD_STYLE, width=400, height=200
    )
    assert artifact.kind == "vega_spec"
    assert isinstance(artifact.payload, dict)
    text_layers = _strip_text_layers_from_vl(artifact.payload)
    assert text_layers, (
        "board render path (render_resolved_chart) must attach the "
        "support_table strip's text layers"
    )


def test_support_table_bar_with_date_object_x_over_cap_samples_not_raises():
    # Regression: a bar chart whose x is a `::date` column (datetime.date
    # objects) is clamped to a categorical scale by the bar mark, but is really
    # temporal. With >40 ticks the strip must SAMPLE (like V1's data-inferred
    # x_type), not raise the ordinal/nominal cardinality cap. Before the fix the
    # validator only recognized lex-sortable date STRINGS, so date OBJECTS fell
    # through to the raise branch — crashing company-overview.yml's board render.
    from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

    weeks = [
        {
            "month": datetime.date(2018, 1, 1) + datetime.timedelta(weeks=i),
            "revenue": float(i),
        }
        for i in range(72)
    ]
    chart = _compiled_bar_with_support_table([{"source": "revenue", "format": ",d"}])
    resolved = resolve(chart, weeks, chart_style_context=_BOARD_CONTEXT)
    assert resolved.support_table is not None
    # Must not raise the 40-tick cap for a chronological (date-object) x axis.
    artifact = render_resolved_chart(
        resolved, weeks, _BOARD_STYLE, width=800, height=300
    )
    assert artifact.kind == "vega_spec"
    assert isinstance(artifact.payload, dict)


def test_shared_x_encoding_anchor_rules() -> None:
    """Strip cell anchor must match where the base mark renders.

    - ordinal/nominal → band center (0.5); marks render at the band center for
      both bar and line, so mark_is_bar doesn't change it.
    - temporal+timeUnit → band center (0.5) ONLY for a bar (spans the band);
      line/area ride the per-point position (no bandPosition) so cells land on
      the data point / grid tick and the band _end never widens the x-domain.
    - plain temporal / quantitative → continuous, no band, no anchor.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _shared_x_encoding

    tu = {"field": "week", "type": "temporal", "timeUnit": "utcyearweek"}
    bar = _shared_x_encoding(tu, mark_is_bar=True)
    assert bar["bandPosition"] == 0.5 and bar["timeUnit"] == "utcyearweek"
    line = _shared_x_encoding(tu, mark_is_bar=False)
    assert "bandPosition" not in line and line["timeUnit"] == "utcyearweek"

    for mib in (True, False):
        ordinal = _shared_x_encoding(
            {"field": "cat", "type": "ordinal"}, mark_is_bar=mib
        )
        assert ordinal["bandPosition"] == 0.5

    plain_temporal = _shared_x_encoding(
        {"field": "ts", "type": "temporal"}, mark_is_bar=False
    )
    assert "bandPosition" not in plain_temporal
    quantitative = _shared_x_encoding(
        {"field": "n", "type": "quantitative"}, mark_is_bar=True
    )
    assert "bandPosition" not in quantitative


def test_temporal_line_support_table_does_not_widen_x_domain():
    """A temporal line's support_table strip must not push the band _end into the
    x-scale domain, which would extend the axis past the data (the "chart starts
    in Dec / ends in Feb" regression). The strip rides the per-point position,
    so the compiled x-scale domain references only the bucket-start field, never
    a ``*_end`` companion.
    """
    import json

    vlc = pytest.importorskip("vl_convert")
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    chart = _compiled_line_temporal_with_support_table([{"source": "revenue"}])
    spec = generate_vega_lite_spec(chart, _TEMPORAL_DATA)
    vg = vlc.vegalite_to_vega(json.dumps(spec))
    vg = json.loads(vg) if isinstance(vg, str) else vg

    x_scale = next(s for s in vg["scales"] if s["name"] == "x")
    domain = x_scale["domain"]
    fields = (
        {f["field"] for f in domain["fields"]}
        if isinstance(domain, dict) and "fields" in domain
        else set()
    )
    assert not any(f.endswith("_end") for f in fields), (
        f"temporal line support_table widened the x-domain with a band _end field: "
        f"{sorted(fields)} — strip must ride the per-point position"
    )


def _goal_row_calc(spec, index: int = 0) -> str | None:
    """The calculate expression producing strip row `index`'s cell text."""
    cell = f"__support_table_{index}"
    for layer in spec.get("layer", []):
        for transform in layer.get("transform", []):
            if transform.get("as") == cell:
                return transform["calculate"]
    return None


_GOAL_DATA = [
    {"date": "2026-02-01", "revenue": 5.5, "goal": 441_000_000.0},
    {"date": "2026-03-01", "revenue": 5.9, "goal": 448_000_000.0},
    {"date": "2026-04-01", "revenue": 5.8, "goal": 456_000_000.0},
    {"date": "2026-05-01", "revenue": 6.2, "goal": 473_000_000.0},
]


def test_pipeline_strip_declares_its_unit_once(monkeypatch):
    """End to end: a builtin currency row reads "$441mn 448 456 473".

    Only engine-predefined format names get the house rule of declaring the unit
    once. A raw literal like "$,.3s" repeats "$…M" on every cell and still must
    — the author must use the predefined name to opt into the declare-once
    behavior (see test_pipeline_raw_literal_format_is_not_anchored below).
    """
    chart = _compiled_line_temporal_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    spec = _render_v2_spec(
        chart, _GOAL_DATA, width=900, height=300, monkeypatch=monkeypatch
    )

    calc = _goal_row_calc(spec)
    assert calc is not None, "expected a support_table strip row in the spec"
    assert '"mn"' in calc
    assert "datum.__support_table_drawn_index === 1" in calc
    # The magnitude is divided out once rather than left to d3's `s` type.
    assert "1000000.0" in calc
    assert "currency" not in calc


def test_pipeline_percent_strip_declares_its_sign_once(monkeypatch):
    """The TV board's shape: a builtin 'percent' row states "%" on the anchor."""
    data = [
        {"date": "2026-02-01", "revenue": 0.055, "goal": 0.055},
        {"date": "2026-03-01", "revenue": 0.059, "goal": 0.062},
        {"date": "2026-04-01", "revenue": 0.058, "goal": 0.25},
    ]
    chart = _compiled_line_temporal_with_support_table(
        [{"source": "goal", "format": "percent", "label": "Goal"}]
    )
    spec = _render_v2_spec(chart, data, width=900, height=300, monkeypatch=monkeypatch)

    calc = _goal_row_calc(spec)
    assert calc is not None
    # The author's own percent spec formats every cell — nothing is rebuilt —
    # and the cells that don't declare the unit have the "%" removed.
    # "percent" resolves to ".1%", so the emitted spec is the resolved form.
    assert "format(datum[\"goal\"], '.1%')" in calc
    assert "replace(format(datum[\"goal\"], '.1%'), \"%\", '')" in calc
    assert "datum.__support_table_drawn_index === 1" in calc


def test_pipeline_category_axis_anchors_on_the_first_cell_in_data_order(monkeypatch):
    """A category axis carries `sort: null`, so data order IS the painted order.

    That makes the leftmost cell exactly computable rather than assumed, so a
    category strip declares its unit like any other — no reason for two strips
    on one board to behave differently.
    """
    data = [
        {"month": "Widget", "revenue": 100.0, "goal": 441_000_000.0},
        {"month": "Anvil", "revenue": 200.0, "goal": 448_000_000.0},
    ]
    chart = _compiled_bar_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    spec = _render_v2_spec(chart, data, width=900, height=300, monkeypatch=monkeypatch)

    calc = _goal_row_calc(spec)
    assert calc is not None
    # Unsorted drawn_index window — anchor fires at DRAWN_INDEX==1 (Widget, first in data).
    assert "__support_table_drawn_index" in calc
    assert '"mn"' in calc


def test_pipeline_authored_sort_on_a_category_axis_does_not_anchor(monkeypatch):
    """An authored `sort:` replaces data order with one we cannot reproduce.

    Fail closed there rather than guess — anchoring the wrong cell would strand
    the unit somewhere in the middle of the row.
    """
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "sort": {"by": "revenue", "order": "desc"},
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"source": "goal", "format": "currency", "label": "Goal"}]
            },
            "style": {"orientation": "vertical"},
        }
    )
    data = [
        {"month": "Widget", "revenue": 100.0, "goal": 441_000_000.0},
        {"month": "Anvil", "revenue": 200.0, "goal": 448_000_000.0},
    ]
    spec = _render_v2_spec(chart, data, width=900, height=300, monkeypatch=monkeypatch)

    calc = _goal_row_calc(spec)
    assert calc is not None
    # Authored sort replaces data order — anchor cannot identify the leftmost
    # cell, so the strip keeps per-cell formatting (no drawn-index window).
    assert "__support_table_drawn_index" not in calc
    assert "mn" not in calc


def test_pipeline_left_aligned_strip_currency_does_not_anchor(monkeypatch):
    """A currency strip on a left-oriented axis repeats the unit on every cell.

    For left-oriented y-axes, a prefix-carrying format (currency) cannot anchor
    without leaving the unit absent from non-anchor cells or using non-standard
    trailing-prefix typography ('441$mn'). The strip routes to StripAnchor.nowhere()
    so every cell formats with its own spec, preserving the currency symbol.
    Orientation controls which side the affix hangs -- for prefix-free SI formats
    (e.g. '.3s') the magnitude suffix still anchors on a left-axis strip.
    """
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "line",
            "x": "date",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"source": "goal", "format": "currency", "label": "Goal"}]
            },
            "style": {"axis_y": {"position": "left"}},
        }
    )
    spec = _render_v2_spec(
        chart, _GOAL_DATA, width=900, height=300, monkeypatch=monkeypatch
    )

    calc = _goal_row_calc(spec)
    assert calc is not None
    # Currency prefix routes to nowhere: no anchor fires, every cell shows $441mn.
    assert "__support_table_drawn_index" not in calc, (
        "currency format on left-axis must not anchor (StripAnchor.nowhere())"
    )


def test_pipeline_zero_first_row_anchors_on_the_first_nonzero_cell(monkeypatch):
    """End to end: a magnitude row whose first period is 0 declares its unit on
    the first cell that can carry it, and the zero cell shows a bare 0."""
    data = [
        {"date": "2026-01-01", "revenue": 5.0, "goal": 0.0},
        {"date": "2026-02-01", "revenue": 5.9, "goal": 448_000_000.0},
        {"date": "2026-03-01", "revenue": 6.0, "goal": 456_000_000.0},
    ]
    chart = _compiled_line_temporal_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    spec = _render_v2_spec(chart, data, width=900, height=300, monkeypatch=monkeypatch)

    calc = _goal_row_calc(spec)
    assert calc is not None
    # The unit gates on the carrying rank, so a 0 first cell cannot strand it.
    assert "datum.__support_table_carries === 1" in calc
    assert '"mn"' in calc


def test_pipeline_negative_magnitude_signs_before_the_symbol(monkeypatch):
    """A variance/goal row of negatives reads '-$448mn', not '$-448mn'."""
    data = [
        {"date": "2026-01-01", "revenue": 5.0, "goal": -441_000_000.0},
        {"date": "2026-02-01", "revenue": 5.9, "goal": -448_000_000.0},
    ]
    chart = _compiled_line_temporal_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    spec = _render_v2_spec(chart, data, width=900, height=300, monkeypatch=monkeypatch)

    calc = _goal_row_calc(spec)
    assert calc is not None
    assert calc.index("datum[\"goal\"] < 0 ? '−'") < calc.index('? "$"')
    assert 'abs(datum["goal"])' in calc


def test_pipeline_raw_literal_format_is_not_anchored(monkeypatch):
    """A raw d3 literal like '$,.3s' keeps today's per-cell behavior.

    House rules — the narrative register this feature applies — are for
    engine-predefined format names only. A literal spec or user alias opts out
    of the d3 opt-outs, and that contract must not be broken by this feature.
    The author must use 'currency' to get the declare-once behavior.
    """
    chart = _compiled_line_temporal_with_support_table(
        [{"source": "goal", "format": "$,.3s", "label": "Goal"}]
    )
    spec = _render_v2_spec(
        chart, _GOAL_DATA, width=900, height=300, monkeypatch=monkeypatch
    )

    calc = _goal_row_calc(spec)
    assert calc is not None
    # Raw literal is rendered per-cell using the author's own spec unchanged.
    assert "$,.3s" in calc
    assert '"mn"' not in calc
    assert "__support_table_drawn_index" not in calc


def test_pipeline_quantitative_x_does_not_anchor():
    """A quantitative x axis has no leftmost band — anchoring is not valid there.

    The category branch previously gated on field + no sort, with no type check,
    so a quantitative x fell into it and anchored via at_value — landing the unit
    on a random data point rather than the actual leftmost position.

    Tested directly against the anchor-selection logic in the post-pass, injecting
    a spec with quantitative x encoding to bypass the render pipeline's own
    type inference.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        apply_chart_support_table_post_pass,
    )

    # Unique x values so the validator sees 1 row per x (no aggregation needed).
    data = [
        {"month": "Widget", "revenue": 5.0, "goal": 441_000_000.0},
        {"month": "Anvil", "revenue": 5.9, "goal": 448_000_000.0},
    ]
    chart = _compiled_bar_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    resolved = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    assert resolved.support_table is not None

    # Swap the x encoding type to quantitative — the field/sort branch must
    # reject this instead of anchoring on the first data value as a band.
    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "quantitative"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "height": 300,
        "width": 900,
        "padding": {"top": 0, "bottom": 0, "left": 0, "right": 0},
        "layer": [],
    }
    result_spec, _ = apply_chart_support_table_post_pass(
        spec, resolved, _BOARD_STYLE.chart_defaults, data, None, "bar", None
    )

    calc = _goal_row_calc(result_spec)
    assert calc is not None
    # Quantitative x has no bands: plain per-cell format, no anchor mechanism.
    assert "__support_table_drawn_index" not in calc
    assert 'datum["month"] ===' not in calc


def test_pipeline_null_first_x_category_still_anchors_via_drawn_index(monkeypatch):
    """A null first x on a category axis anchors at the first drawable band.

    `by_drawn_index_data_order` ranks cells by data order using a window on the
    value field — not the x field — so a null x band still receives DRAWN_INDEX=1
    if its value is non-null and non-zero. No x-value equality test is needed.
    """
    data = [
        {"month": None, "revenue": 100.0, "goal": 441_000_000.0},
        {"month": "Widget", "revenue": 200.0, "goal": 448_000_000.0},
    ]
    chart = _compiled_bar_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    spec = _render_v2_spec(chart, data, width=900, height=300, monkeypatch=monkeypatch)

    calc = _goal_row_calc(spec)
    assert calc is not None
    # Drawn-index window fires — anchors at DRAWN_INDEX==1 (null band, first in data).
    # No x-value equality test needed (the old at_value path required one).
    assert "__support_table_drawn_index" in calc
    assert 'datum["month"] === "Widget"' not in calc


def test_pipeline_absent_sort_key_in_x_encoding_does_not_anchor():
    """A nominal x encoding with no `sort` key at all must not anchor.

    `sort: null` is Vega-Lite's opt-in to data order (the anchor prerequisite).
    An absent sort key means VL picks alphabetical order — not predictable from
    data row order, so anchoring on `first_x` would land the unit on the wrong
    cell. The post-pass must distinguish absent from null.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        apply_chart_support_table_post_pass,
    )

    data = [
        {"month": "Widget", "revenue": 5.0, "goal": 441_000_000.0},
        {"month": "Anvil", "revenue": 5.9, "goal": 448_000_000.0},
    ]
    chart = _compiled_bar_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    resolved = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    assert resolved.support_table is not None

    # Nominal x without a sort key — VL defaults to alphabetical, not data order.
    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "nominal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "height": 300,
        "width": 900,
        "padding": {"top": 0, "bottom": 0, "left": 0, "right": 0},
        "layer": [],
    }
    result_spec, _ = apply_chart_support_table_post_pass(
        spec, resolved, _BOARD_STYLE.chart_defaults, data, None, "bar", None
    )

    calc = _goal_row_calc(result_spec)
    assert calc is not None
    # Absent sort key → no anchor: plain per-cell format.
    assert "__support_table_drawn_index" not in calc
    assert 'datum["month"] ===' not in calc


def test_pipeline_null_value_at_first_x_anchors_on_next_nonnull(monkeypatch):
    """A null VALUE at the first x cell is skipped; anchor fires at next non-null.

    CARRIES marks a cell only when its value is valid and finite, so a null
    first cell receives CARRIES=0 and is skipped. The anchor fires at Anvil
    (the first non-null cell), not stranded nowhere.
    """
    data = [
        {"month": "Widget", "revenue": 100.0, "goal": None},
        {"month": "Anvil", "revenue": 200.0, "goal": 448_000_000.0},
        {"month": "Acme", "revenue": 150.0, "goal": 456_000_000.0},
    ]
    chart = _compiled_bar_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    spec = _render_v2_spec(chart, data, width=900, height=300, monkeypatch=monkeypatch)

    calc = _goal_row_calc(spec)
    assert calc is not None
    # Drawn-index window fires — anchors at DRAWN_INDEX==1 (Anvil, first non-null).
    assert "__support_table_drawn_index" in calc
    assert 'datum["month"] === "Widget"' not in calc


def test_pipeline_format_config_object_anchors_when_spec_is_predefined(monkeypatch):
    """format: {spec: currency} must anchor like format: currency.

    The provenance gate unwraps FormatConfig.spec before checking the predefined
    set — a frozen model object is always outside the frozenset without unwrapping.
    """
    data = [
        {"month": "Widget", "revenue": 100.0, "goal": 441_000_000.0},
        {"month": "Anvil", "revenue": 200.0, "goal": 448_000_000.0},
    ]
    chart = _compiled_bar_with_support_table(
        [{"source": "goal", "format": {"spec": "currency"}, "label": "Goal"}]
    )
    spec = _render_v2_spec(chart, data, width=900, height=300, monkeypatch=monkeypatch)

    calc = _goal_row_calc(spec)
    assert calc is not None
    # FormatConfig with predefined spec → anchors.
    assert '"mn"' in calc


def test_pipeline_currency_precision_derives_decimal_depth(monkeypatch):
    """currency derives its decimal depth from its own `.3` precision.

    At 3 significant digits 4.1M needs 2 decimals (3-1-floor(log10(4.1))=2).
    The depth follows the spec's precision, so the name's compactness reaches
    the support table and not just the cell text. The precisionless branch of
    ``column_digit_format`` (d3's 6-sig-fig default) is covered directly in
    ``test_shared_scale_table_columns.py``; no predefined name reaches it.

    The rebuilt spec uses the $.3~s spec's grouping (none — it has no comma),
    so digit_spec is '.2~f', not ',.2~f'.
    """
    data = [
        {"date": "2026-02-01", "revenue": 5.5, "goal": 4_100_000.0},
        {"date": "2026-03-01", "revenue": 5.9, "goal": 5_500_000.0},
        {"date": "2026-04-01", "revenue": 6.0, "goal": 5_200_000.0},
        {"date": "2026-05-01", "revenue": 6.2, "goal": 12_000_000.0},
    ]
    chart = _compiled_line_temporal_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    spec = _render_v2_spec(chart, data, width=900, height=300, monkeypatch=monkeypatch)

    calc = _goal_row_calc(spec)
    assert calc is not None
    # depth = 2 (from 4.1M at 3 sig figs); no comma on $.3~s → '.2~f'
    assert ".2~f" in calc
    assert "1000000.0" in calc


def test_pipeline_aggregate_min_zero_at_first_x_anchors_on_next_nonzero():
    """aggregate:min with zero at the first cell anchors on the next non-zero cell.

    The CARRIES window marks a cell only when its value is non-zero and finite,
    so a zero first cell is skipped and the anchor fires on the first cell that
    can actually carry the unit affix. No downgrade to plain.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        apply_chart_support_table_post_pass,
    )

    # Widget: min=0 (zero skipped by CARRIES), Anvil: min=448M (anchor fires here)
    data = [
        {"month": "Widget", "revenue": 100.0, "goal": 0.0},
        {"month": "Widget", "revenue": 200.0, "goal": 5_000_000.0},
        {"month": "Widget", "revenue": 300.0, "goal": 441_000_000.0},
        {"month": "Anvil", "revenue": 150.0, "goal": 448_000_000.0},
    ]
    chart = _compiled_bar_with_support_table(
        [
            {
                "aggregate": "min",
                "source": "goal",
                "format": "currency",
                "label": "Goal",
            }
        ]
    )
    # data[-2:] = last Widget row (goal=0) + Anvil row — unique x for the resolver.
    resolved = resolve(chart, data[-2:], chart_style_context=_BOARD_CONTEXT)
    assert resolved.support_table is not None

    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "nominal", "sort": None},
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "height": 300,
        "width": 900,
        "padding": {"top": 0, "bottom": 0, "left": 0, "right": 0},
        "layer": [],
    }
    result_spec, _ = apply_chart_support_table_post_pass(
        spec, resolved, _BOARD_STYLE.chart_defaults, data, None, "bar", None
    )

    calc = _goal_row_calc(result_spec)
    assert calc is not None
    # Anchor uses drawn_index (skips zero Widget, lands on Anvil)
    assert "__support_table_drawn_index" in calc
    assert '"mn"' in calc


def test_pipeline_aggregate_min_nonzero_at_first_x_anchors():
    """aggregate:min with a non-zero first cell anchors at that first cell."""
    from dbt_charts.core.render.chart.support_table_attachment import (
        apply_chart_support_table_post_pass,
    )

    data = [
        {"month": "Widget", "revenue": 100.0, "goal": 441_000_000.0},
        {"month": "Widget", "revenue": 200.0, "goal": 500_000_000.0},
        {"month": "Anvil", "revenue": 150.0, "goal": 448_000_000.0},
    ]
    chart = _compiled_bar_with_support_table(
        [
            {
                "aggregate": "min",
                "source": "goal",
                "format": "currency",
                "label": "Goal",
            }
        ]
    )
    resolved = resolve(chart, data[-2:], chart_style_context=_BOARD_CONTEXT)
    assert resolved.support_table is not None

    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "nominal", "sort": None},
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "height": 300,
        "width": 900,
        "padding": {"top": 0, "bottom": 0, "left": 0, "right": 0},
        "layer": [],
    }
    result_spec, _ = apply_chart_support_table_post_pass(
        spec, resolved, _BOARD_STYLE.chart_defaults, data, None, "bar", None
    )

    calc = _goal_row_calc(result_spec)
    assert calc is not None
    # Anchor uses drawn_index; Widget is first in data order → DRAWN_INDEX=1 fires there.
    assert "__support_table_drawn_index" in calc
    assert '"mn"' in calc


def test_pipeline_per_series_absent_at_first_x_anchors_at_own_first_cell():
    """Each per_series series independently anchors at its own first non-zero cell.

    When series B has no row at the chart's leftmost x (Widget), the drawn_index
    window for series B starts at Anvil (its first row after the series filter).
    Series B anchors at Anvil and series A anchors at Widget — neither falls to plain.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        apply_chart_support_table_post_pass,
    )

    # At month='Widget': only series A. At month='Anvil': both A and B.
    data = [
        {"month": "Widget", "revenue": 441_000_000.0, "series": "A"},
        {"month": "Anvil", "revenue": 448_000_000.0, "series": "A"},
        {"month": "Anvil", "revenue": 456_000_000.0, "series": "B"},
    ]
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "color": "series",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"per_series": "revenue", "format": "currency"}]
            },
            "style": {"orientation": "vertical"},
        }
    )
    resolved = resolve(chart, data[:2], chart_style_context=_BOARD_CONTEXT)
    assert resolved.support_table is not None

    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "nominal", "sort": None},
            "y": {"field": "revenue", "type": "quantitative"},
            "color": {"field": "series", "type": "nominal"},
        },
        "height": 300,
        "width": 900,
        "padding": {"top": 0, "bottom": 0, "left": 0, "right": 0},
        "layer": [],
    }
    result_spec, _ = apply_chart_support_table_post_pass(
        spec, resolved, _BOARD_STYLE.chart_defaults, data, None, "bar", None
    )

    # Series A (row 0): anchors at Widget (its first non-zero row in data order)
    calc_a = _goal_row_calc(result_spec, index=0)
    assert calc_a is not None
    assert "__support_table_drawn_index" in calc_a
    # Series B (row 1): anchors at Anvil (its only row — first in its filtered window)
    calc_b = _goal_row_calc(result_spec, index=1)
    assert calc_b is not None
    assert "__support_table_drawn_index" in calc_b


def test_pipeline_date_like_ordinal_with_data_order_anchors_via_drawn_index():
    """A date-like ordinal x with sort:null uses an unsorted drawn_index window.

    `by_drawn_index_data_order` uses a window without `sort:`, so it follows
    data-insertion order. This is immune to query sort direction, datetime
    normalization differences, and period-filter dropping the first row.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        apply_chart_support_table_post_pass,
    )

    # Descending query order: Mar first in data, Jan last.
    data = [
        {"month": "2026-03", "revenue": 6.2, "goal": 456_000_000.0},
        {"month": "2026-02", "revenue": 5.9, "goal": 448_000_000.0},
        {"month": "2026-01", "revenue": 5.5, "goal": 441_000_000.0},
    ]
    chart = _compiled_bar_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    resolved = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    assert resolved.support_table is not None

    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "nominal", "sort": None},
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "height": 300,
        "width": 900,
        "padding": {"top": 0, "bottom": 0, "left": 0, "right": 0},
        "layer": [],
    }
    result_spec, _ = apply_chart_support_table_post_pass(
        spec, resolved, _BOARD_STYLE.chart_defaults, data, None, "bar", None
    )

    calc = _goal_row_calc(result_spec)
    assert calc is not None
    # Unsorted window — no equality test on x value; anchor fires at DRAWN_INDEX==1.
    assert "__support_table_drawn_index" in calc
    # No x-value equality expression (old at_value path) anywhere in the calc.
    assert 'datum["month"] ===' not in calc


def test_pipeline_date_like_ordinal_with_authored_sort_falls_to_plain():
    """A date-like ordinal x with authored non-null sort must not anchor.

    An authored sort's paint order cannot be reproduced from data rows alone;
    fail-closed rather than anchor on the wrong cell.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        apply_chart_support_table_post_pass,
    )

    data = [
        {"month": "2026-01", "revenue": 5.5, "goal": 441_000_000.0},
        {"month": "2026-02", "revenue": 5.9, "goal": 448_000_000.0},
        {"month": "2026-03", "revenue": 6.2, "goal": 456_000_000.0},
    ]
    chart = _compiled_bar_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    resolved = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    assert resolved.support_table is not None

    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {
                "field": "month",
                "type": "nominal",
                "sort": [{"field": "revenue", "order": "descending"}],
            },
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "height": 300,
        "width": 900,
        "padding": {"top": 0, "bottom": 0, "left": 0, "right": 0},
        "layer": [],
    }
    result_spec, _ = apply_chart_support_table_post_pass(
        spec, resolved, _BOARD_STYLE.chart_defaults, data, None, "bar", None
    )

    calc = _goal_row_calc(result_spec)
    assert calc is not None
    # Authored sort → plain per-cell, no anchor.
    assert "__support_table_drawn_index" not in calc
    assert 'datum["month"] ===' not in calc


def test_band_budget_expands_for_anchor_affix_at_narrow_width():
    """Width thinning accounts for the anchor cell's affix width.

    The anchor cell for currency reads "$441mn" (prefix + digits + suffix)
    while sibling cells read "441" (bare digits). The band budget must be expanded
    to the anchor's measured width so the width-thinning step fires when the anchor
    would overflow the band, even if bare cells would fit.

    10 yearmonth rows at width=500: band=50px ≥ 45px so NO period filter fires
    (period filter threshold is 45px/band; below that the period filter handles
    thinning and sampling is suppressed). Bare-text "441" + 12px padding ~33.4px
    — fits; no thinning without the budget expansion. Anchor "$441mn" + 12px
    padding ~56.6px > 50px → width-thinning must fire (sampling_step > 1).
    """
    data = _yearmonth_data(10)
    # Assign revenues in the millions so currency lands on the mn tier.
    for i, row in enumerate(data):
        row["revenue"] = (441 + i) * 1_000_000.0
    chart = _compiled_line_yearmonth_with_support_table(
        [{"aggregate": "sum", "source": "revenue", "format": "currency"}]
    )
    _rc = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    # width=500 → 50px/band: avoids the period-filter threshold (45px) so
    # sampling is the only active thinning mechanism.
    spec = generate_vega_lite_spec(chart, data, width=500, height=200)
    assert "layer" in spec
    text_layers = _strip_text_layers(spec)
    assert text_layers, "expected at least one strip text layer"
    # Sampling (row_number window) must fire: the anchor's affix width expanded the
    # band budget beyond the 50px band, so the width-thinning step kicked in.
    assert any(
        any(
            "row_number" in str(t.get("window", []))
            for t in layer.get("transform", [])
            if "window" in t
        )
        for layer in text_layers
    ), (
        "strip must thin (row_number window) when the anchor cell's affix width "
        "overflows the band: '$441mn' is ~45px; band at width=500 with 10 x-values "
        "is 50px — anchor overflows even though bare '441' fits at ~33px"
    )


def test_strip_numerals_decimal_span_bail_out():
    """strip_numerals_for_values bails to plain when values span more than one tier.

    A row with values [441e6, 448e6, 441.0] has a shared SI magnitude of 10^8,
    but 441.0 / 1e8 = 4.41e-6 << 0.1. Dividing by the magnitude would produce
    "0.000441" for that cell — the guard exits to the author's own spec instead,
    leaving each cell to format itself at full precision.
    """
    from dbt_charts.core.compile.format import resolve_format
    from dbt_charts.core.render.chart.support_table_attachment import (
        StripAnchor,
        strip_numerals_for_values,
    )

    resolved = resolve_format("currency", None)
    anchor = StripAnchor.by_drawn_index()
    result = strip_numerals_for_values(
        [441e6, 448e6, 441.0], resolved, anchor, font_family="Inter"
    )

    # Bail-out: divisor=1.0 (no division), original digit_spec unchanged.
    assert result.divisor == 1.0, (
        f"expected divisor=1.0 (bail-out to plain), got {result.divisor}"
    )
    assert result.digit_spec == resolved, (
        f"expected original spec {resolved!r} preserved, got {result.digit_spec!r}"
    )
    # Anchor is preserved so the row still uses the drawn-index pattern;
    # only the magnitude arithmetic is suppressed.
    assert result.anchor is anchor


def _goal_col_transforms(spec, visual_idx: int = 0) -> list[dict[str, Any]] | None:
    """The full transform chain producing column `visual_idx`'s cell text."""
    cell = f"__support_table_col_{visual_idx}"
    for layer in spec.get("layer", []):
        transforms = layer.get("transform", [])
        if any(t.get("as") == cell for t in transforms):
            return transforms
    return None


def _goal_col_calc(spec, visual_idx: int = 0) -> str | None:
    """The calculate expression producing column `visual_idx`'s cell text."""
    cell = f"__support_table_col_{visual_idx}"
    for layer in spec.get("layer", []):
        for transform in layer.get("transform", []):
            if transform.get("as") == cell:
                return transform["calculate"]
    return None


def test_pipeline_column_declares_its_unit_once(monkeypatch):
    """Column-block counterpart of test_pipeline_strip_declares_its_unit_once.

    Regression: the column path never inserted the row strip's drawn-index
    window, so the declare-once anchor test could never fire — every cell
    fell through to the bare (divided, unlabeled) spelling. A $120,588,000
    total painted "120" with no "mn" anywhere on the column.

    A `color:` channel is required to reach `sort: null` on the category
    axis (a colorless horizontal bar instead defaults to largest-measure-
    first, which the anchor mechanism refuses to anchor against — see
    test_pipeline_authored_sort_on_a_category_axis_does_not_anchor).
    """
    data = [
        {"region": "East", "seg": "A", "goal": 441_000_000.0},
        {"region": "West", "seg": "A", "goal": 448_000_000.0},
    ]
    chart = _compiled_bar_horizontal_with_support_table(
        [{"source": "goal", "format": "currency", "label": "Goal"}]
    )
    chart = chart.model_copy(update={"color": "seg"})
    spec = _render_v2_spec(chart, data, width=900, height=300, monkeypatch=monkeypatch)

    transforms = _goal_col_transforms(spec)
    assert transforms is not None, "expected a support_table column in the spec"
    # The window transform that actually computes __support_table_drawn_index —
    # the calc expression references that field either way, so asserting on the
    # calc string alone would pass even with no transform producing it.
    assert any(
        DRAWN_INDEX_FIELD in [w.get("as") for w in t.get("window", [])]
        for t in transforms
    ), (
        f"expected a window transform producing {DRAWN_INDEX_FIELD!r}; got {transforms!r}"
    )
    calc = _goal_col_calc(spec)
    assert calc is not None
    assert "datum.__support_table_drawn_index === 1" in calc
    assert '"mn"' in calc
    # The magnitude is divided out once rather than left to d3's `s` type.
    assert "1000000.0" in calc


def test_pipeline_column_per_series_uses_dark_companion_ink_by_default():
    """Unauthored style.support_table.font.color: per_series columns keep
    today's dark-companion-per-series ink."""
    chart = _compiled_bar_horizontal_with_support_table([{"per_series": "revenue"}])
    chart = chart.model_copy(update={"color": "product"})
    data = [
        {"region": "East", "product": "A", "revenue": 100.0},
        {"region": "East", "product": "B", "revenue": 50.0},
        {"region": "West", "product": "A", "revenue": 200.0},
        {"region": "West", "product": "B", "revenue": 75.0},
    ]
    resolved_chart = resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, data, width=400, height=200)
    cells = [
        layer
        for layer in spec["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] != "__header"
    ]
    fills = {c["mark"].get("fill") for c in cells}
    ambient = resolved_chart.effective_support_table_style.font.color
    # At least one series must carry a fill distinct from the ambient/board
    # default ink -- the dark-companion palette, not one flat color.
    assert len(fills) > 1 or ambient not in fills


def test_pipeline_column_per_series_uniform_ink_when_font_color_authored():
    """Authoring style.support_table.font.color on the chart gives every
    per_series column cell that one uniform color instead of dark-companion ink.
    """
    chart = _compiled_bar_horizontal_with_support_table(
        [{"per_series": "revenue"}],
        style={"support_table": {"font": {"color": "#123456"}}},
    )
    chart = chart.model_copy(update={"color": "product"})
    data = [
        {"region": "East", "product": "A", "revenue": 100.0},
        {"region": "East", "product": "B", "revenue": 50.0},
        {"region": "West", "product": "A", "revenue": 200.0},
        {"region": "West", "product": "B", "revenue": 75.0},
    ]
    resolve(chart, data, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, data, width=400, height=200)
    cells = [
        layer
        for layer in spec["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] != "__header"
    ]
    assert len(cells) == 2
    for cell in cells:
        assert cell["mark"]["fill"] == "#123456"


# =============================================================================
# GROUPED VS STACKED: per_series LAYS OUT THE WAY THE CHART LAYS OUT ITS SERIES
# =============================================================================


def _column_headers(spec: dict[str, Any]) -> list[str]:
    return [
        layer["data"]["values"][0]["__header"]
        for layer in spec["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] == "__header"
    ]


def _color_legend(spec: dict[str, Any]) -> object:
    """Read the color legend the same two spots _suppress_series_legend checks."""
    base_enc = spec["layer"][0].get("encoding", {})
    color_enc = base_enc.get("color")
    if not isinstance(color_enc, dict) or color_enc.get("field") is None:
        color_enc = spec.get("encoding", {}).get("color", {})
    return color_enc.get("legend", "MISSING")


_GROUPED_PRODUCT_DATA = [
    {"region": "East", "product": "A", "revenue": 100.0},
    {"region": "East", "product": "B", "revenue": 50.0},
    {"region": "West", "product": "A", "revenue": 200.0},
    {"region": "West", "product": "B", "revenue": 75.0},
]


def test_render_bar_horizontal_grouped_per_series_uses_a_single_column():
    """A grouped bar (bar's own default -- no stack authored) lays each
    series out on its own sub-band, so a per_series entry stays one column
    with every series' cell on it, not one column per series.
    """
    chart = _compiled_bar_horizontal_with_support_table([{"per_series": "revenue"}])
    chart = chart.model_copy(update={"color": "product"})
    resolved_chart = resolve(
        chart, _GROUPED_PRODUCT_DATA, chart_style_context=_BOARD_CONTEXT
    )
    assert resolved_chart.stack in (None, "none")
    spec = generate_vega_lite_spec(chart, _GROUPED_PRODUCT_DATA, width=400, height=200)
    assert len(_column_headers(spec)) == 1


def test_render_bar_horizontal_stacked_per_series_still_expands_multiple_columns():
    chart = _compiled_bar_horizontal_with_support_table(
        [{"per_series": "revenue"}], style={"stack": "zero"}
    )
    chart = chart.model_copy(update={"color": "product"})
    resolved_chart = resolve(
        chart, _GROUPED_PRODUCT_DATA, chart_style_context=_BOARD_CONTEXT
    )
    assert resolved_chart.stack not in (None, "none")
    spec = generate_vega_lite_spec(chart, _GROUPED_PRODUCT_DATA, width=400, height=200)
    assert len(_column_headers(spec)) == 2


def test_render_bar_horizontal_grouped_per_series_does_not_suppress_legend():
    """A grouped bar's single column has no per-series header, so its
    series stay named and colored by the chart's own legend -- never
    suppressed, unlike the stacked (multi-column) case.
    """
    chart = _compiled_bar_horizontal_with_support_table([{"per_series": "revenue"}])
    chart = chart.model_copy(update={"color": "product"})
    resolve(chart, _GROUPED_PRODUCT_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _GROUPED_PRODUCT_DATA, width=400, height=200)
    assert _color_legend(spec) is not None


def test_render_bar_horizontal_stacked_per_series_suppresses_legend():
    chart = _compiled_bar_horizontal_with_support_table(
        [{"per_series": "revenue"}], style={"stack": "zero"}
    )
    chart = chart.model_copy(update={"color": "product"})
    resolve(chart, _GROUPED_PRODUCT_DATA, chart_style_context=_BOARD_CONTEXT)
    spec = generate_vega_lite_spec(chart, _GROUPED_PRODUCT_DATA, width=400, height=200)
    assert _color_legend(spec) is None


# ---------------------------------------------------------------------------
# A bottom strip clears whatever tilt the x labels were drawn at
# ---------------------------------------------------------------------------

_TILT_WIDTH = 520.0
_TILT_HEIGHT = 320.0
_LONG_CATEGORY_DATA = [
    {"month": f"Department {i:02d}", "revenue": 100.0 + i} for i in range(20)
]


def _bottom_strip_chart(x_axis_style: dict[str, Any] | None = None) -> Chart:
    """Vertical bar with a ``position: bottom`` strip and one source row."""
    style: dict[str, Any] = {
        "orientation": "vertical",
        "support_table": {"position": "bottom"},
    }
    if x_axis_style is not None:
        style["axis_x"] = x_axis_style
    return TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue"}]},
            "style": style,
        }
    )


def _first_strip_row_y(spec: dict[str, Any], position: str = "bottom") -> float:
    """Pixel y of strip row 0 — the row nearest the plot.

    Rows run away from the plot as their index grows, downward from
    ``spec.height`` at ``position: bottom`` and upward (negative) at ``top``,
    so row 0 is the smallest y below the plot and the largest above it.
    """
    main = spec["hconcat"][0] if "hconcat" in spec else spec
    ys = [
        layer["encoding"]["y"]["value"]
        for layer in main.get("layer", [])
        if isinstance(layer.get("encoding", {}).get("y", {}).get("value"), (int, float))
    ]
    assert ys, "no pixel-positioned strip layer found"
    return min(ys) if position == "bottom" else max(ys)


def _reserved_axis_gap(spec: dict[str, Any], dt_style: Any) -> float:
    """The axis gap a ``position: bottom`` strip actually left, read back out of
    row 0's pixel y.

    The offset enters ``_row_y_pixel`` linearly, so row 0's distance from where
    a zero offset would put it IS the offset — nothing about the strip's own
    geometry is re-derived here. Reading row 0 as the smallest y assumes no
    divider rule, which sits lower still.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _row_y_pixel

    assert dt_style.position == "bottom"
    assert dt_style.divider.width == 0
    return _first_strip_row_y(spec) - _row_y_pixel(0, dt_style, 0.0, _TILT_HEIGHT)


def _widest_label_px(data: list[dict[str, Any]], axis_x: Any) -> float:
    from dbt_charts.core.font_measure import get_font_measurer

    font = axis_x.labels.font
    measurer = get_font_measurer(font.family)
    return max(measurer.measure(str(row["month"]), font.size) for row in data)


def test_bottom_strip_clears_vertically_tilted_x_labels() -> None:
    """20 long categories tilt the labels to -90, where each is as tall as it is
    long. The strip's gap is baked at compile time from two HORIZONTAL label
    lines, so without the render-time measurement row 0 draws through them.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _row_y_pixel

    chart = _bottom_strip_chart()
    resolved = resolve(
        chart,
        _LONG_CATEGORY_DATA,
        chart_style_context=_BOARD_CONTEXT,
        width=_TILT_WIDTH,
    )
    spec = generate_vega_lite_spec(
        chart,
        _LONG_CATEGORY_DATA,
        width=_TILT_WIDTH,
        height=_TILT_HEIGHT,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CONTEXT,
    )
    main = spec["hconcat"][0] if "hconcat" in spec else spec
    assert main["encoding"]["x"]["axis"]["labelAngle"] == -90.0, (
        "precondition: this chart must tilt its labels to vertical"
    )
    dt_style = resolved.effective_support_table_style
    assert dt_style is not None
    # At -90 a label stands as tall as it is wide, so the strip must start at
    # least that far below the plot — compare against the row y that offset
    # would produce rather than re-deriving the strip's own pixel arithmetic.
    cleared = _row_y_pixel(
        0,
        dt_style,
        _widest_label_px(_LONG_CATEGORY_DATA, resolved.style.axis_x),
        _TILT_HEIGHT,
    )
    assert _first_strip_row_y(spec) >= cleared


def test_bottom_strip_gap_is_unchanged_when_labels_sit_flat() -> None:
    """Three short categories need no tilt: the compile-baked gap stands, to the
    pixel."""
    from dbt_charts.core.render.chart.support_table_attachment import _row_y_pixel

    chart = _bottom_strip_chart()
    resolved = resolve(
        chart, _SAMPLE_DATA, chart_style_context=_BOARD_CONTEXT, width=_TILT_WIDTH
    )
    spec = generate_vega_lite_spec(
        chart,
        _SAMPLE_DATA,
        width=_TILT_WIDTH,
        height=_TILT_HEIGHT,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CONTEXT,
    )
    main = spec["hconcat"][0] if "hconcat" in spec else spec
    assert main["encoding"]["x"]["axis"]["labelAngle"] == 0.0
    dt_style = resolved.effective_support_table_style
    assert dt_style is not None
    assert _first_strip_row_y(spec) == pytest.approx(
        _row_y_pixel(0, dt_style, resolved.support_table_axis_offset, _TILT_HEIGHT)
    )


def test_bottom_strip_clears_an_authored_label_angle() -> None:
    """An authored angle short-circuits the tilt ladder, which would have picked
    -90 for these labels — the pinned -45 still has to be measured and cleared.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _row_y_pixel

    chart = _bottom_strip_chart(x_axis_style={"labels": {"angle": -45}})
    resolved = resolve(
        chart,
        _LONG_CATEGORY_DATA,
        chart_style_context=_BOARD_CONTEXT,
        width=_TILT_WIDTH,
    )
    spec = generate_vega_lite_spec(
        chart,
        _LONG_CATEGORY_DATA,
        width=_TILT_WIDTH,
        height=_TILT_HEIGHT,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CONTEXT,
    )
    main = spec["hconcat"][0] if "hconcat" in spec else spec
    assert main["encoding"]["x"]["axis"]["labelAngle"] == -45.0
    dt_style = resolved.effective_support_table_style
    assert dt_style is not None
    baked = _row_y_pixel(0, dt_style, resolved.support_table_axis_offset, _TILT_HEIGHT)
    vertical = _row_y_pixel(
        0,
        dt_style,
        _widest_label_px(_LONG_CATEGORY_DATA, resolved.style.axis_x),
        _TILT_HEIGHT,
    )
    # Half-tilted: more room than the baked two flat lines, less than the
    # upright block a -90 would have cost.
    assert baked < _first_strip_row_y(spec) < vertical


def test_top_strip_ignores_the_x_label_tilt() -> None:
    """At ``position: top`` the x-axis is on the far side of the plot — a tilt
    down there is none of the strip's business."""
    from dbt_charts.core.render.chart.support_table_attachment import _row_y_pixel

    chart = _compiled_bar_with_support_table([{"source": "revenue"}])
    resolved = resolve(
        chart,
        _LONG_CATEGORY_DATA,
        chart_style_context=_BOARD_CONTEXT,
        width=_TILT_WIDTH,
    )
    dt_style = resolved.effective_support_table_style
    assert dt_style is not None
    assert dt_style.position == "top"
    spec = generate_vega_lite_spec(
        chart,
        _LONG_CATEGORY_DATA,
        width=_TILT_WIDTH,
        height=_TILT_HEIGHT,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CONTEXT,
    )
    assert _first_strip_row_y(spec, position="top") == pytest.approx(
        _row_y_pixel(0, dt_style, None, _TILT_HEIGHT)
    )


def test_tilted_bottom_strip_gap_no_longer_depends_on_label_max_lines() -> None:
    """The coupling this fix removes: `label_max_lines` was the author's only
    lever over a tilted axis's gap. Once the tilt itself is measured, raising it
    reserves the same room — the two knobs no longer have to be tuned together.
    """
    gaps = []
    for label_max_lines in (2, 3):
        chart = _bottom_strip_chart()
        chart.style.support_table.label_max_lines = label_max_lines
        spec = generate_vega_lite_spec(
            chart,
            _LONG_CATEGORY_DATA,
            width=_TILT_WIDTH,
            height=_TILT_HEIGHT,
            board_style=_BOARD_STYLE,
            chart_style_context=_BOARD_CONTEXT,
        )
        gaps.append(_first_strip_row_y(spec))
    assert gaps[0] == pytest.approx(gaps[1])


def test_hidden_x_labels_reserve_no_tilt_room() -> None:
    """The tilt resolver picks an angle whether or not the labels are drawn, and
    the compile-side bake reserves nothing for hidden ones. The render-time
    growth has to agree, or the strip floats below an empty gap.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _row_y_pixel

    chart = _bottom_strip_chart(x_axis_style={"labels": {"visible": False}})
    resolved = resolve(
        chart,
        _LONG_CATEGORY_DATA,
        chart_style_context=_BOARD_CONTEXT,
        width=_TILT_WIDTH,
    )
    spec = generate_vega_lite_spec(
        chart,
        _LONG_CATEGORY_DATA,
        width=_TILT_WIDTH,
        height=_TILT_HEIGHT,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CONTEXT,
    )
    dt_style = resolved.effective_support_table_style
    assert dt_style is not None
    assert _first_strip_row_y(spec) == pytest.approx(
        _row_y_pixel(0, dt_style, resolved.support_table_axis_offset, _TILT_HEIGHT)
    )


def test_bottom_strip_clears_a_pinned_tilt_on_a_sub_daily_axis() -> None:
    """A timestamp column has no bucketed calendar grain, so the axis goes
    continuous and Vega draws its own clock labels. Their text is not knowable
    here, so the gap is reserved against the datum's own (never narrower)
    text — pinned exactly, since an over-reservation is a visible empty band.
    """

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "line",
            "x": "ts",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue"}]},
            "style": {
                "support_table": {"position": "bottom"},
                "axis_x": {"labels": {"angle": -90}},
            },
        }
    )
    data = [
        {"ts": f"2024-01-01T{hour:02d}:30:00", "revenue": 100.0 + hour}
        for hour in range(12)
    ]
    resolved = resolve(
        chart, data, chart_style_context=_BOARD_CONTEXT, width=_TILT_WIDTH
    )
    spec = generate_vega_lite_spec(
        chart,
        data,
        width=_TILT_WIDTH,
        height=_TILT_HEIGHT,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CONTEXT,
    )
    dt_style = resolved.effective_support_table_style
    assert dt_style is not None
    from dbt_charts.core.font_measure import get_font_measurer

    font = resolved.style.axis_x.labels.font
    measurer = get_font_measurer(font.family)
    baked = resolved.support_table_axis_offset
    assert baked is not None
    gap = _reserved_axis_gap(spec, dt_style)
    # Wide enough for the clock label Vega draws at -90 ("12:30am" upright),
    # and no wider than the datum's own text, which is what gets measured in
    # its place. Bounding both ends keeps the deliberate over-reservation
    # deliberate — a looser "it grew" would pass on any wrong magnitude.
    assert gap > baked + measurer.measure("12:30am", font.size)
    assert gap <= baked + measurer.measure(data[0]["ts"], font.size)


def test_quantitative_pinned_angle_reserves_no_tilt_room() -> None:
    """The gap this pins is a known one. A quantitative axis draws a handful of
    ticks Vega both places and formats, so a pinned tilt there is measured
    against nothing and the baked gap stands — rather than against the rows'
    own digits, which are not what the axis paints.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _row_y_pixel

    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": "line",
            "x": "spend",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue"}]},
            "style": {
                "support_table": {"position": "bottom"},
                "axis_x": {"labels": {"angle": -90}},
            },
        }
    )
    data = [
        {"spend": 1000000.0 + 250000.0 * i, "revenue": 100.0 + i} for i in range(20)
    ]
    resolved = resolve(
        chart, data, chart_style_context=_BOARD_CONTEXT, width=_TILT_WIDTH
    )
    spec = generate_vega_lite_spec(
        chart,
        data,
        width=_TILT_WIDTH,
        height=_TILT_HEIGHT,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CONTEXT,
    )
    dt_style = resolved.effective_support_table_style
    assert dt_style is not None
    assert _first_strip_row_y(spec) == pytest.approx(
        _row_y_pixel(0, dt_style, resolved.support_table_axis_offset, _TILT_HEIGHT)
    )


@pytest.mark.parametrize(
    ("chart_type", "extra_style"),
    [
        pytest.param("line", {}, id="line"),
        pytest.param("bar", {"orientation": "vertical"}, id="bar"),
    ],
)
def test_bottom_strip_clears_a_pinned_tilt_past_the_ordinal_scaffold_budget(
    chart_type: str, extra_style: dict[str, Any]
) -> None:
    """Ten dates 45 days apart detect as daily grain (see
    ordinal_scaffold_within_budget's own docstring example), but banding that
    grain would synthesize ~400 empty buckets against a budget of 60. Daily
    ("yearmonthdate") is a FINE_BUCKET_UNITS grain, so resolve_cartesian_x_type
    resolves both mark types to vl_type="temporal" here: line always does
    (mark_type in ("line", "area", "scatter") forces it); bar reaches the
    same verdict only because a FINE_BUCKET_UNITS grain past the scaffold
    budget routes there too. Both then hit the shared `if vl_type ==
    "temporal" and not scaffold_ok: time_unit = None` gate, so
    resolve_cartesian_x_type drops the grain on both, the emitted axis
    carries no timeUnit/format/labelExpr/values/tickCount of its own, and
    Vega-Lite falls through to its own default temporal multi-format ticks
    ('September', '2024', ...) instead of the bucketed '1 Jan' vocabulary the
    daily grain alone would suggest. Render-time Python code cannot predict
    that default's exact text, so the pinned-angle reservation has to agree
    with the dropped verdict and fall back to measuring the raw datum, a
    deliberate over-reservation, rather than re-detect a grain the axis no
    longer has.
    """
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_chart",
            "type": chart_type,
            "x": "ts",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue"}]},
            "style": {
                **extra_style,
                "support_table": {"position": "bottom"},
                "axis_x": {"labels": {"angle": -90}},
            },
        }
    )
    data = [
        {
            "ts": (
                datetime.date(2024, 1, 1) + datetime.timedelta(days=45 * i)
            ).isoformat(),
            "revenue": 100.0 + i,
        }
        for i in range(10)
    ]
    resolved = resolve(
        chart, data, chart_style_context=_BOARD_CONTEXT, width=_TILT_WIDTH
    )
    spec = generate_vega_lite_spec(
        chart,
        data,
        width=_TILT_WIDTH,
        height=_TILT_HEIGHT,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CONTEXT,
    )
    dt_style = resolved.effective_support_table_style
    assert dt_style is not None
    from dbt_charts.core.font_measure import get_font_measurer

    font = resolved.style.axis_x.labels.font
    measurer = get_font_measurer(font.family)
    baked = resolved.support_table_axis_offset
    assert baked is not None
    gap = _reserved_axis_gap(spec, dt_style)
    widest_raw = max(
        (row["ts"] for row in data), key=lambda v: measurer.measure(v, font.size)
    )
    # At angle=-90 the block height IS the widest label's width
    # (_label_block_height: max_width*sin(90) + line_height*cos(90)).
    # _tilted_label_axis_offset only grows the baked gap by whatever the
    # block exceeds the label_max_lines reservation already baked in.
    reserved = font.size * dt_style.label_max_lines
    # The raw ISO datum, measured as a deliberate over-reservation for
    # whatever continuous date label Vega ends up painting once the
    # scaffold-budget gate drops the grain. A bucketed "1 Jan"-style label
    # reads well under half this width.
    assert gap == pytest.approx(
        baked + max(0.0, measurer.measure(widest_raw, font.size) - reserved)
    )


def test_bottom_strip_clears_a_pinned_tilt_measured_in_the_axis_case() -> None:
    """``axis_x.labels.font.case: upper`` repaints every label upper-case at
    render time (inject_axis_label_case) — the pinned-angle reservation has
    to measure that same casing, not the source rows' own casing, which
    paints narrower for a typeface with distinct upper/lower glyph widths.
    """
    chart = _bottom_strip_chart(
        x_axis_style={"labels": {"angle": -90, "font": {"case": "upper"}}}
    )
    data = [{"month": f"department {i:02d}", "revenue": 100.0 + i} for i in range(20)]
    resolved = resolve(
        chart, data, chart_style_context=_BOARD_CONTEXT, width=_TILT_WIDTH
    )
    spec = generate_vega_lite_spec(
        chart,
        data,
        width=_TILT_WIDTH,
        height=_TILT_HEIGHT,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CONTEXT,
    )
    dt_style = resolved.effective_support_table_style
    assert dt_style is not None
    from dbt_charts.core.font_measure import get_font_measurer

    font = resolved.style.axis_x.labels.font
    assert font.case == "upper"
    measurer = get_font_measurer(font.family)
    baked = resolved.support_table_axis_offset
    assert baked is not None
    gap = _reserved_axis_gap(spec, dt_style)
    widest_upper = max(
        (row["month"].upper() for row in data),
        key=lambda v: measurer.measure(v, font.size),
    )
    reserved = font.size * dt_style.label_max_lines
    assert gap == pytest.approx(
        baked + max(0.0, measurer.measure(widest_upper, font.size) - reserved)
    )
    # The un-cased source text measures narrower — confirms the case
    # transform is what widens the reservation, not an incidental rounding
    # difference.
    widest_raw = max(
        (row["month"] for row in data), key=lambda v: measurer.measure(v, font.size)
    )
    assert measurer.measure(widest_raw, font.size) < measurer.measure(
        widest_upper, font.size
    )


def test_layered_bar_bottom_strip_clears_tilted_x_labels() -> None:
    """A `layers:` chart is wrapped in a fresh outer spec; the base owns the
    x-axis, so its label measurement has to survive that wrap.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _row_y_pixel

    chart = _bottom_strip_chart()
    chart = chart.model_copy(
        update={"layers": [LineLayer(type="line", y="revenue", label="Trend")]}
    )
    resolved = resolve(
        chart,
        _LONG_CATEGORY_DATA,
        chart_style_context=_BOARD_CONTEXT,
        width=_TILT_WIDTH,
    )
    spec = generate_vega_lite_spec(
        chart,
        _LONG_CATEGORY_DATA,
        width=_TILT_WIDTH,
        height=_TILT_HEIGHT,
        board_style=_BOARD_STYLE,
        chart_style_context=_BOARD_CONTEXT,
    )
    main = spec["hconcat"][0] if "hconcat" in spec else spec
    assert main["encoding"]["x"]["axis"]["labelAngle"] == -90.0
    dt_style = resolved.effective_support_table_style
    assert dt_style is not None
    cleared = _row_y_pixel(
        0,
        dt_style,
        _widest_label_px(_LONG_CATEGORY_DATA, resolved.style.axis_x),
        _TILT_HEIGHT,
    )
    assert _first_strip_row_y(spec) >= cleared
