"""Heatmap and scatter adopt bar/line's temporal x-axis label vocabulary.

Heatmap's x stays a nominal grid dimension (the domain value is never
reformatted, only the painted label), and scatter joins line/area's
always-continuous-temporal rule. Both now share
``default_label_expr_for``/``resolve_temporal_label_visibility`` with bar
instead of heatmap falling through to ``_generic_layout`` (tilt-only, no
vocabulary) and scatter falling through to Vega-Lite's own default temporal
formatter.

Assertions here read the real vl_convert-compiled scenegraph/SVG, not the
emitted VL JSON — a labelExpr string proves the mechanism was wired, not that
the calendar-ordered domain and the ticks-follow-labels count actually
painted that way.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest
import vl_convert as vlc

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())

_MONTHS = [f"2024-{m:02d}-01" for m in range(1, 13)]


def _render(
    rc: Any,
    data: list[dict[str, Any]],
    datasets: dict[str | None, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    artifact = render_resolved_chart(
        rc, data, _BOARD_STYLE, width=600, datasets=datasets
    )
    assert artifact.kind == "vega_spec"
    return artifact.payload


def _axis_label_texts(spec: dict[str, Any]) -> list[Any]:
    """Real painted x-axis label text, in domain order, from vl_convert's
    compiled scenegraph — the accessibility-role text marks Vega itself
    decided to draw, not the emitted ``labelExpr`` source string."""
    scenegraph = vlc.vegalite_to_scenegraph(dict(spec))
    texts: list[Any] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("marktype") == "text" and node.get("role") == "axis-label":
                texts.append([item.get("text") for item in node.get("items", [])])
                return
            for _key, value in node.items():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(scenegraph)
    # The x-axis is emitted before the y-axis in every family here.
    assert texts, "no axis-label text mark found in the rendered scenegraph"
    return texts[0]


def _axis_tick_count(spec: dict[str, Any]) -> int:
    scenegraph = vlc.vegalite_to_scenegraph(dict(spec))
    counts: list[int] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("marktype") == "rule" and node.get("role") == "axis-tick":
                counts.append(len(node.get("items", [])))
                return
            for _key, value in node.items():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(scenegraph)
    assert counts, "no axis-tick rule mark found in the rendered scenegraph"
    return counts[0]


def _rendered_heatmap_column_order(spec: dict[str, Any]) -> list[str]:
    """Each heatmap cell's tooltip description (carries the real date), in
    real rendered left-to-right x order — proves the band domain stayed
    chronological even though the painted label was reformatted. Mirrors
    test_bar_null_bucket_axis.py's ``_rendered_x_order`` for heatmap's rect
    mark instead of bar's."""
    scenegraph = vlc.vegalite_to_scenegraph(dict(spec))
    cells: list[tuple[float, str]] = []

    def walk(node: Any, x_offset: float, y_offset: float) -> None:
        if isinstance(node, dict):
            nx = x_offset + node.get("x", 0)
            ny = y_offset + node.get("y", 0)
            if node.get("marktype") == "rect":
                for item in node.get("items", []):
                    if "description" in item:
                        cells.append((nx + item.get("x", 0), item["description"]))
                return
            for key, value in node.items():
                if key == "items":
                    walk(value, nx, ny)
                elif isinstance(value, (dict, list)):
                    walk(value, x_offset, y_offset)
        elif isinstance(node, list):
            for item in node:
                walk(item, x_offset, y_offset)

    walk(scenegraph, 0.0, 0.0)
    return [description for _, description in sorted(cells, key=lambda c: c[0])]


def _rendered_scatter_point_positions(spec: dict[str, Any]) -> list[float]:
    """Real painted scatter point x positions from vl_convert's compiled
    scenegraph. An unparseable domain value resolves to NaN, and Vega drops a
    NaN-valued property from the serialized item entirely rather than
    emitting a null — so a point whose position never resolved is simply
    missing its "x" key, not present with a null/NaN value."""
    scenegraph = vlc.vegalite_to_scenegraph(dict(spec))
    positions: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("marktype") == "symbol":
                for item in node.get("items", []):
                    if "x" in item:
                        positions.append(item["x"])
                return
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(scenegraph)
    return positions


def test_heatmap_monthly_labels_match_bar_vocabulary(make_chart) -> None:
    """Same 12-month data, same family-agnostic Jan/2024...Dec vocabulary."""
    data = [{"month": m, "row": "Row A", "value": i} for i, m in enumerate(_MONTHS)]
    bar_data = [{"month": m, "value": i} for i, m in enumerate(_MONTHS)]

    heatmap = make_chart("heatmap", x="month", y="row", color="value")
    bar = make_chart("bar", x="month", y="value")

    heatmap_spec = _render(resolve(heatmap, data, chart_style_context=_BOARD_CTX), data)
    bar_spec = _render(resolve(bar, bar_data, chart_style_context=_BOARD_CTX), bar_data)

    assert heatmap_spec["encoding"]["x"]["type"] == "nominal", (
        "heatmap x must stay a nominal grid dimension — promoting it collapses cells"
    )
    assert _axis_label_texts(heatmap_spec) == _axis_label_texts(bar_spec)


def test_scatter_monthly_labels_match_line_vocabulary(make_chart) -> None:
    data = [{"month": m, "value": i} for i, m in enumerate(_MONTHS)]

    scatter = make_chart("scatter", x="month", y="value")
    line = make_chart("line", x="month", y="value")

    scatter_spec = _render(resolve(scatter, data, chart_style_context=_BOARD_CTX), data)
    line_spec = _render(resolve(line, data, chart_style_context=_BOARD_CTX), data)

    assert scatter_spec["encoding"]["x"]["type"] == "temporal"
    assert _axis_label_texts(scatter_spec) == _axis_label_texts(line_spec)


def test_heatmap_weekly_domain_stays_chronological_not_alphabetized(
    make_chart,
) -> None:
    """The trap the Decision names: reformatting the label must never touch
    the ISO domain value doing double duty as sort key. Assert the real
    rendered column order, not the emitted `sort`/`values` key — a pinned
    domain can carry the wrong order even with the right key present."""
    dates = [
        (dt.date(2023, 1, 2) + dt.timedelta(weeks=i)).isoformat() for i in range(52)
    ]
    data = [{"week": d, "row": "Row A", "value": i} for i, d in enumerate(dates)]
    chart = make_chart("heatmap", x="week", y="row", color="value")
    spec = _render(resolve(chart, data, chart_style_context=_BOARD_CTX), data)

    order = _rendered_heatmap_column_order(spec)
    assert len(order) == 52
    date_re = re.compile(r"\d+ \w+ \d{4}")
    rendered_dates = [
        dt.datetime.strptime(date_re.findall(d)[-1], "%d %b %Y").date() for d in order
    ]
    assert rendered_dates == sorted(rendered_dates), (
        "heatmap columns must read chronologically left-to-right, not "
        "alphabetized by the reformatted label"
    )


def test_heatmap_weekly_ticks_equal_labels(make_chart) -> None:
    """52 weekly bands thinned to 12 month-opening labels: tick count and
    label count must match — thinning label TEXT while leaving a tick under
    every band (52 ticks under 12 labels) is the rejected mechanical option."""
    dates = [
        (dt.date(2023, 1, 2) + dt.timedelta(weeks=i)).isoformat() for i in range(52)
    ]
    data = [{"week": d, "row": "Row A", "value": i} for i, d in enumerate(dates)]
    chart = make_chart("heatmap", x="week", y="row", color="value")
    spec = _render(resolve(chart, data, chart_style_context=_BOARD_CTX), data)

    labels = _axis_label_texts(spec)
    assert len(labels) == 12
    assert _axis_tick_count(spec) == 12
    assert labels[0] == ["Jan", "2023"]
    assert labels[1:] == [
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]


def _off_anchor_months() -> list[str]:
    months = []
    d = dt.date(2024, 4, 1)
    for _ in range(12):
        months.append(d.isoformat())
        d = (
            dt.date(d.year + 1, 1, 1)
            if d.month == 12
            else dt.date(d.year, d.month + 1, 1)
        )
    return months


def test_heatmap_off_anchor_two_tier_context(make_chart) -> None:
    """Apr 2024 -> Mar 2025: year context prints at both the leftmost entry
    (Apr/2024) and the anchor where the calendar year rolls (Jan/2025) —
    porting bar's vocabulary must carry this rule, not re-derive it."""
    months = _off_anchor_months()
    data = [{"month": m, "row": "Row A", "value": i} for i, m in enumerate(months)]
    chart = make_chart("heatmap", x="month", y="row", color="value")
    spec = _render(resolve(chart, data, chart_style_context=_BOARD_CTX), data)

    labels = _axis_label_texts(spec)
    assert labels[0] == ["Apr", "2024"]
    assert labels[9] == ["Jan", "2025"]


def test_scatter_off_anchor_two_tier_context(make_chart) -> None:
    months = _off_anchor_months()
    data = [{"month": m, "value": i} for i, m in enumerate(months)]
    chart = make_chart("scatter", x="month", y="value")
    spec = _render(resolve(chart, data, chart_style_context=_BOARD_CTX), data)

    labels = _axis_label_texts(spec)
    assert labels[0] == ["Apr", "2024"]
    assert labels[9] == ["Jan", "2025"]


# ---------------------------------------------------------------------------
# Regression: heatmap/scatter must canonicalize x before the shared temporal
# encoding path — the family that emits axis.values/labelExpr/timeUnit owns
# making its domain values JS-Date-parseable, date-only ISO first (mirrors
# bar/line/area's normalize_labeled_temporal + canonicalize_and_sort_ordinal_x
# preconditions). Every corpus board casts ::DATE and the tests above use ISO
# YYYY-MM-DD strings, so this shape was structurally unreachable by the rest
# of the suite — these assert painted output (labels exist / points have a
# real position), not the presence of a spec key, since a stale spec key can
# survive even when Vega drops the actual mark.
# ---------------------------------------------------------------------------


def _datetime_x_rows() -> list[dict[str, Any]]:
    return [
        {"month": dt.datetime(2024, m, 1, 0, 0, 0), "value": i}
        for i, m in enumerate([1, 4, 7, 10], start=1)
    ]


def _date_x_rows() -> list[dict[str, Any]]:
    return [
        {"month": dt.date(2024, m, 1), "value": i}
        for i, m in enumerate([1, 4, 7, 10], start=1)
    ]


def _quarter_label_rows() -> list[dict[str, Any]]:
    return [{"month": f"Q{q} 2024", "value": q} for q in range(1, 5)]


def _fiscal_year_label_rows() -> list[dict[str, Any]]:
    return [
        {"month": f"FY{y}", "value": i}
        for i, y in enumerate([2021, 2022, 2023, 2024], start=1)
    ]


def _iso_week_label_rows() -> list[dict[str, Any]]:
    # Consecutive weeks (not a sparse sample) — detect_time_unit needs a
    # regular cadence to resolve a "yearweek" grain at all; a sparse sample
    # falls back to a plain ordinal read that never touches the temporal
    # path this test targets.
    return [{"month": f"2024-W{w:02d}", "value": w} for w in range(1, 9)]


def _mm_yyyy_label_rows() -> list[dict[str, Any]]:
    return [
        {"month": f"{m:02d}/2024", "value": i}
        for i, m in enumerate([1, 4, 7, 10], start=1)
    ]


_DATE_LIKE_X_CASES = {
    "datetime_datetime": _datetime_x_rows,
    "datetime_date": _date_x_rows,
    "quarter_label": _quarter_label_rows,
    "fiscal_year_label": _fiscal_year_label_rows,
    "iso_week_label": _iso_week_label_rows,
    "mm_yyyy_label": _mm_yyyy_label_rows,
}


@pytest.mark.parametrize("case_name", sorted(_DATE_LIKE_X_CASES))
def test_heatmap_date_like_x_paints_labels(make_chart, case_name: str) -> None:
    """A datetime.datetime x (TIMESTAMP columns, e.g. date_trunc() output) or
    a labeled bucket string x must still paint axis labels — before the fix,
    a datetime.datetime x painted zero labels (axis.values didn't match the
    band domain) and a labeled string x painted NaN garbage (toDate() on an
    unparseable label)."""
    rows = [{**row, "row": "Row A"} for row in _DATE_LIKE_X_CASES[case_name]()]
    chart = make_chart("heatmap", x="month", y="row", color="value")
    spec = _render(resolve(chart, rows, chart_style_context=_BOARD_CTX), rows)
    labels = _axis_label_texts(spec)
    assert labels, f"{case_name}: heatmap painted zero x-axis labels"
    flat = [
        text
        for label in labels
        for text in (label if isinstance(label, list) else [label])
    ]
    assert not any("NaN" in str(text) for text in flat), (
        f"{case_name}: heatmap painted a NaN label: {labels!r}"
    )


@pytest.mark.parametrize("case_name", sorted(_DATE_LIKE_X_CASES))
def test_scatter_date_like_x_positions_points(make_chart, case_name: str) -> None:
    """A datetime.datetime x or a labeled bucket string x must position every
    point — before the fix, both shapes positioned points at [None, ...] on
    an empty plot (Vega parsed the raw domain value as an Invalid Date)."""
    rows = _DATE_LIKE_X_CASES[case_name]()
    chart = make_chart("scatter", x="month", y="value")
    spec = _render(resolve(chart, rows, chart_style_context=_BOARD_CTX), rows)
    positions = _rendered_scatter_point_positions(spec)
    assert len(positions) == len(rows), (
        f"{case_name}: scatter positioned {len(positions)} of {len(rows)} points"
    )


def test_scatter_datetime_x_emits_date_only_iso_domain_values(make_chart) -> None:
    """A datetime.datetime x must reach the emitted spec as date-only ISO
    ("2024-01-01"), never a datetime string with a time component
    ("2024-01-01 00:00:00"). The latter parses as LOCAL time in JS (no
    offset), so a runtime east of UTC buckets it into the wrong month —
    silent and only reproducible under a non-UTC TZ, so this pins the
    domain-value shape directly rather than relying on the rendered
    position, which happens to look right in a UTC test environment."""
    rows = _datetime_x_rows()
    chart = make_chart("scatter", x="month", y="value")
    spec = _render(resolve(chart, rows, chart_style_context=_BOARD_CTX), rows)
    values = [row["month"] for row in spec["data"]["values"]]
    assert values == ["2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01"]


# ---------------------------------------------------------------------------
# Regression: canonicalize_cartesian_x_data's bucketed-grain gate must agree
# with build_cartesian_x_encoding's — the latter resolves time_unit as
# authored-or-detected (type_inference.py's resolve_cartesian_x_type), so an
# authored style.axis_x.time_unit on a non-midnight datetime x must also
# canonicalize even though detect_time_unit alone returns None for it (its
# own sub-daily fallthrough). Before the fix, heatmap's gate checked only the
# detected grain: axis.values (stringified via .isoformat(), "T" separator)
# diverged from the row values (stringified via str(), " " separator), so a
# nominal band scale matched nothing and painted zero x-axis labels.
# ---------------------------------------------------------------------------


def test_heatmap_authored_bucketed_grain_on_non_midnight_datetime_paints_labels(
    make_chart,
) -> None:
    rows = [
        {"month": dt.datetime(2024, m, 1, 13, 30, 0), "row": "Row A", "value": i}
        for i, m in enumerate([1, 4, 7, 10], start=1)
    ]
    chart = make_chart(
        "heatmap",
        x="month",
        y="row",
        color="value",
        style={"axis_x": {"time_unit": "yearmonth"}},
    )
    spec = _render(resolve(chart, rows, chart_style_context=_BOARD_CTX), rows)
    labels = _axis_label_texts(spec)
    assert labels, "heatmap with an authored bucketed grain painted zero x-axis labels"


# ---------------------------------------------------------------------------
# Regression: canonicalize_cartesian_x_data must gate row-rewriting on the
# same detect_time_unit-in-BUCKETED_CALENDAR_UNITS grain check bar/line/area
# use via _channels.py, not merely "does this column look temporal at all" —
# else it rewrites rows those families were never meant to touch. Every case
# above uses midnight-only timestamps (the safe date_trunc() cadence), which
# structurally cannot exercise the sub-daily fallthrough.
# ---------------------------------------------------------------------------


def _sub_daily_x_rows() -> list[dict[str, Any]]:
    return [
        {"event_time": dt.datetime(2024, 1, 15, 9, 0, 0), "latency": 10.0},
        {"event_time": dt.datetime(2024, 1, 15, 14, 0, 0), "latency": 20.0},
        {"event_time": dt.datetime(2024, 1, 16, 9, 0, 0), "latency": 15.0},
        {"event_time": dt.datetime(2024, 1, 16, 14, 0, 0), "latency": 25.0},
    ]


def _rendered_heatmap_cell_x_positions(spec: dict[str, Any]) -> list[float]:
    """Real painted heatmap rect x offsets from vl_convert's compiled
    scenegraph, one per data row — proves distinct x-domain buckets stayed
    distinct rather than two rows collapsing onto the same column."""
    scenegraph = vlc.vegalite_to_scenegraph(dict(spec))
    positions: list[float] = []

    def walk(node: Any, x_offset: float) -> None:
        if isinstance(node, dict):
            nx = x_offset + node.get("x", 0)
            if node.get("marktype") == "rect" and node.get("role") == "mark":
                for item in node.get("items", []):
                    if "x" in item:
                        positions.append(nx + item["x"])
                return
            for key, value in node.items():
                if key == "items":
                    walk(value, nx)
                elif isinstance(value, (dict, list)):
                    walk(value, x_offset)
        elif isinstance(node, list):
            for item in node:
                walk(item, x_offset)

    walk(scenegraph, 0.0)
    return positions


def test_scatter_sub_daily_x_stays_distinct_not_collapsed_to_date(make_chart) -> None:
    """Intraday timestamps (nonzero h/m/s) must position each point at its own
    x. detect_time_unit returns None for this cadence (its own explicit
    sub-daily fallthrough) — exactly the signal line/area use via
    _channels.py:131 to skip ordinal-bucket canonicalization entirely.
    Before the fix, canonicalize_cartesian_x_data gated only on "looks
    temporal at all" and truncated every timestamp to its date, piling all
    four points onto two x positions."""
    rows = _sub_daily_x_rows()
    chart = make_chart("scatter", x="event_time", y="latency")
    spec = _render(resolve(chart, rows, chart_style_context=_BOARD_CTX), rows)
    positions = _rendered_scatter_point_positions(spec)
    assert len(positions) == len(rows)
    assert len(set(positions)) == len(rows), (
        f"sub-daily x collapsed to {len(set(positions))} distinct positions "
        f"for {len(rows)} points: {positions!r}"
    )


def test_heatmap_sub_daily_x_columns_stay_distinct(make_chart) -> None:
    """Same collapse, heatmap side: an intraday timestamp x must keep every
    row its own column — before the fix, two rows sharing a calendar day
    landed in the same painted column and overpainted each other."""
    rows = [{**row, "row": "Row A"} for row in _sub_daily_x_rows()]
    chart = make_chart("heatmap", x="event_time", y="row", color="latency")
    spec = _render(resolve(chart, rows, chart_style_context=_BOARD_CTX), rows)
    positions = _rendered_heatmap_cell_x_positions(spec)
    assert len(positions) == len(rows)
    assert len(set(positions)) == len(rows), (
        f"sub-daily x collapsed heatmap to {len(set(positions))} distinct "
        f"columns for {len(rows)} rows: {positions!r}"
    )


# ---------------------------------------------------------------------------
# Regression: a genuinely quantitative scatter x whose integer values happen
# to fall in the year-shaped 1700-2100 band must stay quantitative. Scatter's
# quantitative gate must run before canonicalization ever touches the raw
# rows, since normalize_labeled_temporal's is_year_shaped heuristic is meant
# for bar/line/area's dimension x, not scatter's continuous measure x.
# ---------------------------------------------------------------------------


def test_scatter_year_shaped_quantitative_x_stays_quantitative(make_chart) -> None:
    rows = [
        {"cost": 1950, "latency": 1.0},
        {"cost": 1975, "latency": 2.0},
        {"cost": 2001, "latency": 3.0},
        {"cost": 2049, "latency": 4.0},
    ]
    chart = make_chart("scatter", x="cost", y="latency")
    spec = _render(resolve(chart, rows, chart_style_context=_BOARD_CTX), rows)
    assert spec["encoding"]["x"]["type"] == "quantitative", (
        "a year-shaped integer measure must not be reinterpreted as a date axis"
    )
    values = [row["cost"] for row in spec["data"]["values"]]
    assert values == [1950, 1975, 2001, 2049], (
        "quantitative x rows must reach the spec unmodified"
    )


# ---------------------------------------------------------------------------
# Regression: a layered scatter's overlay layer must see the same
# canonicalized x rows as the base layer.
# ---------------------------------------------------------------------------


def test_scatter_layered_overlay_shares_base_canonicalized_data(make_chart) -> None:
    """Before the fix, the base layer's spec.data carried canonicalized
    date-only rows, but render_cartesian_overlay's outer ChartSpec had no
    data of its own — session.py refilled the outer/inherited-by-overlay
    data from raw chart_rows, so the base painted "2024-01-01" while the
    overlay inherited "2024-01-01 00:00:00"."""
    rows = _datetime_x_rows()
    chart = make_chart(
        "scatter",
        x="month",
        y="value",
        layers=[{"type": "scatter", "y": "value"}],
    )
    spec = _render(resolve(chart, rows, chart_style_context=_BOARD_CTX), rows)
    assert "layer" in spec, f"expected an assembled VL layered spec, got {spec!r}"
    top_values = [row["month"] for row in spec["data"]["values"]]
    assert top_values == ["2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01"], (
        f"outer/overlay-inherited data must match the base layer's "
        f"canonicalized rows, got {top_values!r}"
    )


def test_scatter_layered_overlay_layer_shares_base_canonicalized_data(
    make_chart,
) -> None:
    """The overlay LAYER's own effective x domain (not merely the outer
    spec's inherited-by-VL data block) must match the base layer's
    canonicalized rows. A layer authoring no `query:` of its own resolves
    `query_name` to the base's at compile time (resolve/_layers.py), so
    `datasets.get(layer.query_name)` always hit — returning the RAW,
    un-canonicalized base rows — and got stamped directly onto that layer's
    own `data` block, which VL reads in preference to the outer/inherited
    data no matter what the outer block carries. Checking only
    `spec["data"]["values"]` (the previous test) cannot see this: it passes
    whether or not the layer itself carries the wrong-format rows."""
    rows = _datetime_x_rows()
    chart = make_chart(
        "scatter",
        x="month",
        y="value",
        layers=[{"type": "scatter", "y": "value"}],
    )
    spec = _render(resolve(chart, rows, chart_style_context=_BOARD_CTX), rows)
    assert "layer" in spec, f"expected an assembled VL layered spec, got {spec!r}"
    overlay_layer = spec["layer"][1]
    # A layer sharing the base's query has no own `data` block once fixed —
    # it inherits the (canonicalized) outer data VL-natively. A layer that
    # still carries its own `data` here is exactly the un-fixed shape.
    layer_values = (
        [row["month"] for row in overlay_layer["data"]["values"]]
        if "data" in overlay_layer
        else [row["month"] for row in spec["data"]["values"]]
    )
    assert layer_values == ["2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01"], (
        f"overlay layer's effective x domain must match the base layer's "
        f"canonicalized rows, got {layer_values!r}"
    )


def test_scatter_layered_overlay_own_query_layer_canonicalizes_x(make_chart) -> None:
    """A layer authoring its OWN `query:` (distinct from the base's) must
    canonicalize its x rows the same way the base does. Before the fix,
    `own_data` in `_overlay.py` ran only through `normalize_labeled_temporal`
    — never `canonicalize_and_sort_ordinal_x` — so it kept a raw
    ``datetime.datetime`` while the base's rows were truncated to date-only
    ISO by `canonicalize_cartesian_x_data`. Both land on one shared
    utcyearquarter scale: date-only ISO parses as UTC midnight in JS, the
    space-separated naive form parses as local time, so outside UTC the
    overlay buckets a whole quarter away from the base point plotting the
    same row. That divergence is invisible in UTC CI (both forms parse to
    the same instant at UTC), so assert the emitted string form directly
    rather than a rendered position."""
    rows = _datetime_x_rows()
    own_rows = _datetime_x_rows()
    chart = make_chart(
        "scatter",
        x="month",
        y="value",
        layers=[{"type": "scatter", "y": "value", "query": "overlay_q"}],
    )
    spec = _render(
        resolve(chart, rows, chart_style_context=_BOARD_CTX),
        rows,
        datasets={"overlay_q": own_rows},
    )
    assert "layer" in spec, f"expected an assembled VL layered spec, got {spec!r}"
    overlay_layer = spec["layer"][1]
    assert "data" in overlay_layer, (
        "an own-query layer must carry its own data block, distinct from the "
        "base's inherited/outer data"
    )
    layer_values = [row["month"] for row in overlay_layer["data"]["values"]]
    assert layer_values == ["2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01"], (
        f"own-query overlay layer's x rows must be canonicalized to date-only "
        f"ISO, matching the base layer's rows on the shared temporal scale, "
        f"got {layer_values!r}"
    )
