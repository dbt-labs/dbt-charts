"""Regression tests for three v2 cartesian emitter parity gaps.

Each test is a TDD-first regression: written to fail against the unfixed v2
path, then passes after the fix.

1. Bar gap-fill: bar.py must call gap_fill_ordinal_time like line/area.
2. Legend titles: _emit_horizontal and scatter must pass title= to
   channel_to_encoding.
3. Format alias: axis_y.format aliases (e.g. currency_whole) must be resolved
   to d3-format strings in _bake_cartesian_axes, not emitted raw.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)

# ---------------------------------------------------------------------------
# Shared helpers (same contract as test_render_v2_parity.py)
# ---------------------------------------------------------------------------


def _board_style() -> Any:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    raw = get_theme_style(get_default_theme_name())
    return resolve_style_and_context(raw)


def _make_registry(queries_raw: dict[str, Any]) -> dict[str, Any]:
    from dbt_charts.core.compile.models.query.normalized import ValuesQuery

    return {
        qname: ValuesQuery(
            columns=qdef["columns"],
            values=qdef["values"],
            rows=[],
        )
        for qname, qdef in queries_raw.items()
    }


def _oracle_vl(
    chart_id: str,
    chart_def: dict[str, Any],
    queries_raw: dict[str, Any],
    data: list[dict[str, Any]],
) -> dict[str, Any]:
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    rs, ctx = _board_style()
    registry = _make_registry(queries_raw)
    flat = normalize_chart(chart_id, chart_def, registry, sources={})
    return generate_vega_lite_spec(flat, data, board_style=rs, chart_style_context=ctx)


def _v2_vl(
    chart_id: str,
    chart_def: dict[str, Any],
    queries_raw: dict[str, Any],
    data: list[dict[str, Any]],
) -> dict[str, Any]:
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.session import BoardRenderSession

    rs, ctx = _board_style()
    registry = _make_registry(queries_raw)
    compiled = normalize_chart(chart_id, chart_def, registry, sources={})
    resolved = resolve(compiled, data, chart_style_context=ctx)
    session = BoardRenderSession.create(rs)
    spec = session.emit_chart(resolved, _DEFAULT_BOX, {resolved.query_name: data})
    vl = session.finalize_vl(spec)
    # Real production always routes through _render_vl_artifact, which injects
    # the resolved width into the spec unconditionally (width is never None in
    # prod). This helper bypasses that layer, so mirror the same injection here
    # to keep the oracle comparison honest rather than accidentally matching by
    # both sides omitting the key.
    size_target = vl["hconcat"][0] if "hconcat" in vl else vl
    size_target["width"] = _DEFAULT_BOX.width
    if "hconcat" in vl:
        vl["$df_target_width"] = _DEFAULT_BOX.width
    # Same mirroring rule for the axis-label identity sentinel that
    # _render_vl_artifact plants unconditionally on the production path.
    from dbt_charts.core.render.chart.vega_lite import _stamp_axis_label_kind_sentinel

    _stamp_axis_label_kind_sentinel(resolved, vl)
    return vl


# ---------------------------------------------------------------------------
# Fix 1: bar gap-fill
#
# Gapped monthly data: 2024-01 and 2024-03 present, 2024-02 missing.
# gap_fill_ordinal_time adds a null row for 2024-02 — v1 does this, v2 must.
# ---------------------------------------------------------------------------

_BAR_GAP_FILL_CHART = {
    "type": "bar",
    "query": "q",
    "x": "month",
    "y": "revenue",
}

_BAR_GAP_FILL_QUERY = {
    "q": {
        "columns": ["month", "revenue"],
        "values": [
            ["2024-01-01", 100],
            ["2024-03-01", 150],
        ],
    }
}

_BAR_GAP_FILL_DATA: list[dict[str, Any]] = [
    {"month": "2024-01-01", "revenue": 100},
    {"month": "2024-03-01", "revenue": 150},
]


def test_bar_gap_fill_matches_oracle() -> None:
    """v2 bar must fill the missing 2024-02 row, matching v1 oracle output."""
    oracle = _oracle_vl(
        "c", _BAR_GAP_FILL_CHART, _BAR_GAP_FILL_QUERY, _BAR_GAP_FILL_DATA
    )
    v2 = _v2_vl("c", _BAR_GAP_FILL_CHART, _BAR_GAP_FILL_QUERY, _BAR_GAP_FILL_DATA)

    oracle_rows: list[Any] = oracle.get("data", {}).get("values", [])
    v2_rows: list[Any] = v2.get("data", {}).get("values", [])

    # Oracle fills the gap: 3 rows (Jan, Feb=null, Mar).
    assert len(oracle_rows) == 3, f"Oracle expected 3 rows, got {len(oracle_rows)}"
    # v2 must match.
    assert len(v2_rows) == len(oracle_rows), (
        f"v2 bar gap-fill regression: expected {len(oracle_rows)} rows, "
        f"got {len(v2_rows)}. bar.py must call gap_fill_ordinal_time."
    )
    assert v2 == oracle, "v2 bar gap-fill spec must be byte-identical to oracle"


# ---------------------------------------------------------------------------
# Fix 1b: bar axis ordering for non-presorted bucketed queries (#7)
#
# encoding.x.sort: null disables VL's sort → raw row order.
# v1 compensates via complete_ordinal_time_series returning rows sorted
# (bucket asc) even when no buckets are missing.  v2 bar was skipping all
# of that → scrambled month order on non-presorted data.
#
# Data: 3 complete monthly rows arriving in reverse order (Mar, Jan, Feb).
# No missing buckets → gap_fill fires only for sorting/ISO rewrite.
# Oracle output must have x values Jan, Feb, Mar (chronological + ISO).
# ---------------------------------------------------------------------------

_BAR_ORDERING_CHART = {
    "type": "bar",
    "query": "q",
    "x": "month",
    "y": "revenue",
}

_BAR_ORDERING_QUERY = {
    "q": {
        "columns": ["month", "revenue"],
        "values": [
            ["2024-03-01", 150],
            ["2024-01-01", 100],
            ["2024-02-01", 120],
        ],
    }
}

# Rows arrive in non-chronological order (Mar, Jan, Feb) — simulates a
# GROUP BY without ORDER BY.
_BAR_ORDERING_DATA: list[dict[str, Any]] = [
    {"month": "2024-03-01", "revenue": 150},
    {"month": "2024-01-01", "revenue": 100},
    {"month": "2024-02-01", "revenue": 120},
]


def test_bar_ordering_non_presorted_matches_oracle() -> None:
    """v2 bar must sort non-presorted bucketed rows chronologically, matching v1.

    This exercises the axis-ordering fix (#7): encoding.x.sort: null disables
    VL's default sort; the renderer must produce sorted + ISO-rewritten x values
    via gap_fill_ordinal_time even when there are zero gaps to fill.
    """
    oracle = _oracle_vl(
        "c", _BAR_ORDERING_CHART, _BAR_ORDERING_QUERY, _BAR_ORDERING_DATA
    )
    v2 = _v2_vl("c", _BAR_ORDERING_CHART, _BAR_ORDERING_QUERY, _BAR_ORDERING_DATA)

    oracle_rows: list[Any] = oracle.get("data", {}).get("values", [])
    v2_rows: list[Any] = v2.get("data", {}).get("values", [])

    assert len(oracle_rows) == 3, f"Oracle must have 3 rows, got {len(oracle_rows)}"
    assert len(v2_rows) == 3, (
        f"v2 bar ordering regression: expected 3 rows, got {len(v2_rows)}"
    )

    oracle_x = [r.get("month") for r in oracle_rows]
    v2_x = [r.get("month") for r in v2_rows]

    # Both must be sorted chronologically and ISO-formatted.
    assert oracle_x == [
        "2024-01-01",
        "2024-02-01",
        "2024-03-01",
    ], f"Oracle x must be chronological ISO, got {oracle_x}"
    assert v2_x == ["2024-01-01", "2024-02-01", "2024-03-01"], (
        f"v2 bar ordering regression: x must be chronological ISO, got {v2_x}. "
        "bar.py must call gap_fill_ordinal_time to sort even when no gaps exist."
    )
    assert v2 == oracle, "v2 bar ordering spec must be byte-identical to oracle"


# ---------------------------------------------------------------------------
# Degenerate-stack legend order must not fire on gap-filled data
# (https://github.com/dbt-labs/dbt-charts/issues/28 regression).
#
# Pre-fill: one color per month (mango/Jan, apple/Mar, cherry/Apr) -- looks
# degenerate (color 1:1 with x). But Feb is missing, so gap_fill_ordinal_time
# cross-joins every bucket x every color seen anywhere, synthesizing null
# rows for every OTHER color at every bucket -- post-fill, the stack is a
# genuine multi-segment one (most segments null), not degenerate at all.
# The legend must fall back to the ordinary sum-ranked (and reversed) order,
# never the degenerate x-domain order the pre-fill rows alone would suggest.
# ---------------------------------------------------------------------------

_BAR_GAP_FILL_DEGENERATE_CHART = {
    "type": "bar",
    "query": "q",
    "x": "month",
    "y": "revenue",
    "color": "fruit",
    "style": {"stack": "zero"},
}

_BAR_GAP_FILL_DEGENERATE_QUERY = {
    "q": {
        "columns": ["month", "fruit", "revenue"],
        "values": [
            ["2024-01-01", "mango", 10],
            ["2024-03-01", "apple", 90],
            ["2024-04-01", "cherry", 50],
        ],
    }
}

_BAR_GAP_FILL_DEGENERATE_DATA: list[dict[str, Any]] = [
    {"month": "2024-01-01", "fruit": "mango", "revenue": 10},
    {"month": "2024-03-01", "fruit": "apple", "revenue": 90},
    {"month": "2024-04-01", "fruit": "cherry", "revenue": 50},
]


def test_bar_degenerate_stack_order_skipped_on_gap_filled_data() -> None:
    """A gap-filled stacked bar must use sum-ranked order, not the
    degenerate x-domain order -- see the module docstring above."""
    v2 = _v2_vl(
        "c",
        _BAR_GAP_FILL_DEGENERATE_CHART,
        _BAR_GAP_FILL_DEGENERATE_QUERY,
        _BAR_GAP_FILL_DEGENERATE_DATA,
    )

    v2_rows: list[Any] = v2.get("data", {}).get("values", [])
    # 4 months (Jan-Apr, Feb filled) x 3 colors = 12 rows -- confirms
    # gap-fill's cross-join actually ran, or the rest of this assertion
    # would be exercising the wrong code path.
    assert len(v2_rows) == 12, (
        f"expected the 4-bucket x 3-color cross-join (12 rows), got "
        f"{len(v2_rows)} -- gap-fill did not fire as this test assumes"
    )

    legend = v2["encoding"]["color"]["legend"]
    # Sum-ranked descending (apple=90, cherry=50, mango=10), reversed for
    # the legend: mango, cherry, apple. The degenerate (buggy) order would
    # instead read mango, apple, cherry -- first-occurrence query order,
    # unreversed.
    assert legend["values"] == ["mango", "cherry", "apple"]


# ---------------------------------------------------------------------------
# Degenerate-stack legend order on a FACETED gap-filled chart -- a distinct
# failure mode from the single-panel test above. gap_fill_ordinal_time_per_
# panel cross-joins each panel over only that panel's own [min, max] bucket
# range and its own dim values, so the pooled per-panel-synthesized rows can
# cover every series exactly once (one panel per series here) and pass
# degenerate_or_stacked_series_order's coverage check on an order that is an
# artifact of which rows gap-fill invented for that panel, not a genuine
# degenerate-stack order. `not gap_fired` is the only thing that stops the
# single-panel test's coverage-check argument from silently mis-firing here.
# ---------------------------------------------------------------------------

_BAR_FACETED_GAP_FILL_DEGENERATE_CHART = {
    "type": "bar",
    "query": "q",
    "x": "month",
    "y": "revenue",
    "color": "fruit",
    "style": {"stack": "zero"},
    "multiples": {"rows": "region"},
}

_BAR_FACETED_GAP_FILL_DEGENERATE_QUERY = {
    "q": {
        "columns": ["region", "month", "fruit", "revenue"],
        "values": [
            ["East", "2024-01-01", "apple", 60],
            ["East", "2024-03-01", "apple", 40],
            ["West", "2024-04-01", "banana", 20],
            ["West", "2024-06-01", "banana", 10],
        ],
    }
}

_BAR_FACETED_GAP_FILL_DEGENERATE_DATA: list[dict[str, Any]] = [
    {"region": "East", "month": "2024-01-01", "fruit": "apple", "revenue": 60},
    {"region": "East", "month": "2024-03-01", "fruit": "apple", "revenue": 40},
    {"region": "West", "month": "2024-04-01", "fruit": "banana", "revenue": 20},
    {"region": "West", "month": "2024-06-01", "fruit": "banana", "revenue": 10},
]


def test_bar_degenerate_stack_order_skipped_on_faceted_gap_filled_data() -> None:
    """See the module docstring above."""
    v2 = _v2_vl(
        "c",
        _BAR_FACETED_GAP_FILL_DEGENERATE_CHART,
        _BAR_FACETED_GAP_FILL_DEGENERATE_QUERY,
        _BAR_FACETED_GAP_FILL_DEGENERATE_DATA,
    )

    assert "facet" in v2, "expected a faceted spec -- multiples did not fire"
    # 2 panels x 3 buckets each (Jan-Mar, Apr-Jun) = 6 rows -- confirms
    # gap-fill fired *and* left x ordinal (not temporal), which is what
    # keeps `not gap_fired` the only discriminating term below.
    assert len(v2["data"]["values"]) == 6
    legend = v2["spec"]["encoding"]["color"]["legend"]
    # Sum-ranked descending (apple=100, banana=30), reversed for the legend:
    # banana, apple. The faceted-degenerate artifact would instead read
    # apple, banana -- each panel's own gap-fill first row, unreversed.
    assert legend["values"] == ["banana", "apple"]


# ---------------------------------------------------------------------------
# Fix 2a: legend title on horizontal bar with color channel
#
# _emit_horizontal calls channel_to_encoding without title= → legend has no
# title; v1 oracle includes it.
# ---------------------------------------------------------------------------

_HORIZ_BAR_COLOR_CHART = {
    "type": "bar",
    "query": "q",
    "x": "category",
    "y": "revenue",
    "color": "region",
    "style": {
        "orientation": "horizontal",
    },
}

_HORIZ_BAR_COLOR_QUERY = {
    "q": {
        "columns": ["category", "revenue", "region"],
        "values": [
            ["A", 100, "North"],
            ["A", 80, "South"],
            ["B", 120, "North"],
            ["B", 90, "South"],
        ],
    }
}

_HORIZ_BAR_COLOR_DATA: list[dict[str, Any]] = [
    {"category": "A", "revenue": 100, "region": "North"},
    {"category": "A", "revenue": 80, "region": "South"},
    {"category": "B", "revenue": 120, "region": "North"},
    {"category": "B", "revenue": 90, "region": "South"},
]


def test_horizontal_bar_color_legend_title_matches_oracle() -> None:
    """v2 horizontal bar color encoding must carry the legend title, matching v1."""
    oracle = _oracle_vl(
        "c",
        _HORIZ_BAR_COLOR_CHART,
        _HORIZ_BAR_COLOR_QUERY,
        _HORIZ_BAR_COLOR_DATA,
    )
    v2 = _v2_vl(
        "c",
        _HORIZ_BAR_COLOR_CHART,
        _HORIZ_BAR_COLOR_QUERY,
        _HORIZ_BAR_COLOR_DATA,
    )
    oracle_color_title = oracle.get("encoding", {}).get("color", {}).get("title")
    v2_color_title = v2.get("encoding", {}).get("color", {}).get("title")

    assert oracle_color_title is not None, "Oracle must include color title"
    assert v2_color_title == oracle_color_title, (
        f"v2 horizontal bar legend title regression: expected {oracle_color_title!r}, "
        f"got {v2_color_title!r}. _emit_horizontal must pass title= to channel_to_encoding."
    )


# ---------------------------------------------------------------------------
# Fix 2b: legend title on scatter with color channel
#
# scatter.py calls channel_to_encoding without title= → legend has no title.
# ---------------------------------------------------------------------------

_SCATTER_COLOR_CHART = {
    "type": "scatter",
    "query": "q",
    "x": "cost",
    "y": "revenue",
    "color": "region",
}

_SCATTER_COLOR_QUERY = {
    "q": {
        "columns": ["cost", "revenue", "region"],
        "values": [
            [100, 200, "North"],
            [150, 350, "South"],
            [80, 180, "North"],
        ],
    }
}

_SCATTER_COLOR_DATA: list[dict[str, Any]] = [
    {"cost": 100, "revenue": 200, "region": "North"},
    {"cost": 150, "revenue": 350, "region": "South"},
    {"cost": 80, "revenue": 180, "region": "North"},
]


def test_scatter_color_legend_title_matches_oracle() -> None:
    """v2 scatter color encoding must carry the legend title, matching v1."""
    oracle = _oracle_vl(
        "c",
        _SCATTER_COLOR_CHART,
        _SCATTER_COLOR_QUERY,
        _SCATTER_COLOR_DATA,
    )
    v2 = _v2_vl(
        "c",
        _SCATTER_COLOR_CHART,
        _SCATTER_COLOR_QUERY,
        _SCATTER_COLOR_DATA,
    )
    oracle_color_title = oracle.get("encoding", {}).get("color", {}).get("title")
    v2_color_title = v2.get("encoding", {}).get("color", {}).get("title")

    assert oracle_color_title is not None, "Oracle must include color title"
    assert v2_color_title == oracle_color_title, (
        f"v2 scatter legend title regression: expected {oracle_color_title!r}, "
        f"got {v2_color_title!r}. scatter.py must pass title= to channel_to_encoding."
    )
    assert v2 == oracle, "v2 scatter spec must be byte-identical to oracle"


# ---------------------------------------------------------------------------
# Fix 3: format alias not leaked into VL
#
# scatter with style.axis_y.format: currency_whole must emit "$,.0f" (the d3
# string) not the raw alias "currency_whole".  v2 must resolve it in
# _bake_cartesian_axes.
# ---------------------------------------------------------------------------

_SCATTER_FORMAT_CHART = {
    "type": "scatter",
    "query": "q",
    "x": "cost",
    "y": "revenue",
    "style": {
        "axis_y": {
            "labels": {"format": "currency_whole"},
        }
    },
}

_SCATTER_FORMAT_QUERY = {
    "q": {
        "columns": ["cost", "revenue"],
        "values": [
            [100, 2000],
            [150, 3500],
        ],
    }
}

_SCATTER_FORMAT_DATA: list[dict[str, Any]] = [
    {"cost": 100, "revenue": 2000},
    {"cost": 150, "revenue": 3500},
]


def test_scatter_axis_format_alias_resolved_to_d3() -> None:
    """v2 scatter must resolve axis_y.labels.format alias to its d3 string before emit."""
    oracle = _oracle_vl(
        "c",
        _SCATTER_FORMAT_CHART,
        _SCATTER_FORMAT_QUERY,
        _SCATTER_FORMAT_DATA,
    )
    v2 = _v2_vl(
        "c",
        _SCATTER_FORMAT_CHART,
        _SCATTER_FORMAT_QUERY,
        _SCATTER_FORMAT_DATA,
    )

    oracle_fmt = oracle.get("encoding", {}).get("y", {}).get("axis", {}).get("format")
    v2_fmt = v2.get("encoding", {}).get("y", {}).get("axis", {}).get("format")

    assert oracle_fmt == "$,.0f", (
        f"Oracle must resolve currency_whole → $,.0f, got {oracle_fmt!r}"
    )
    assert v2_fmt == "$,.0f", (
        f"v2 format alias leak: axis_y.format 'currency_whole' must resolve to "
        f"'$,.0f', got {v2_fmt!r}. _bake_cartesian_axes must call resolve_format."
    )
    # Also verify the encoding-level format (tooltip) matches the resolved axis format.
    oracle_enc_fmt = oracle.get("encoding", {}).get("y", {}).get("format")
    v2_enc_fmt = v2.get("encoding", {}).get("y", {}).get("format")
    assert oracle_enc_fmt == "$,.0f", (
        f"Oracle y.format must be $,.0f, got {oracle_enc_fmt!r}"
    )
    assert v2_enc_fmt == "$,.0f", (
        f"v2 encoding y.format regression: expected '$,.0f', got {v2_enc_fmt!r}. "
        "_resolve_scatter must promote authored axis_y.format to tooltip_format."
    )
