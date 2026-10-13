"""X-axis tick values must cover the full scale domain, not the base's rows.

When an overlay layer carries more x buckets than the base series (a forward
goal ramp running past actuals, say), Vega-Lite unions the layer domains and
the band scale grows to hold every bucket. ``axis.values`` was derived from
``ordinal_axis_values(data, x_field)`` over the BASE series' rows alone, so the
bands past the base's last row got no label.

Nothing here covers the separate width-dependent symptom (Vega dropping the
leading label at isolated plot widths, present again half a pixel to either
side). Measured against this same chart, that fires identically whether
``values`` lists 8 bands or all 13, with ``labelOverlap`` false or true, and
with a one-word label as readily as a two-part one — so it is a Vega layout
bug, not a consequence of the truncated list, and no assertion here can hold
it. See the task brief for the upstream repro.

``values`` is not decorative: it carries the month -> quarter -> year label
cadence ladder. The fix derives it from the full domain and then thins, so the
thinning assertions here are as load-bearing as the coverage ones.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from typing import Any

import vl_convert as vlc

from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.font_measure import RESERVATION_GUARD
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.emitters.bar import BarEmitter
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.translate import translate_to_vl


# Bars: Jan-Aug 2026 (8 months). Goal line: Jan 2026 - Jan 2027 (13 months).
# The band scale is the 13-month union; the bars fill the left 8/13.
def _months(count: int) -> list[str]:
    out, year, month = [], 2026, 1
    for _ in range(count):
        out.append(f"{year}-{month:02d}-01")
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return out


_BASE_MONTHS = [f"2026-{m:02d}-01" for m in range(1, 9)]
_GOAL_MONTHS = [f"2026-{m:02d}-01" for m in range(1, 13)] + ["2027-01-01"]

_BASE_DATA: list[dict] = [
    {"month": m, "revenue": 100.0 + i * 10} for i, m in enumerate(_BASE_MONTHS)
]
_GOAL_DATA: list[dict] = [
    {"month": m, "goal": 150.0 + i * 10} for i, m in enumerate(_GOAL_MONTHS)
]


def _default_board_style():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _layered_vl(width: float = 600.0) -> VLDict:
    """Emit the repro chart: month bars with a longer goal-line overlay."""
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        variable_dependencies=set(),
        layers=[LineLayer(type="line", y="goal", query="goals")],
    )
    resolved = resolve(chart, _BASE_DATA, _default_board_style())
    return translate_to_vl(
        BarEmitter().emit(
            resolved,
            RenderBox(width=width, height=300.0),
            regroup((), _BASE_DATA),
            datasets={"goals": _GOAL_DATA},
        )
    )


def _axis_values_at_every_injection_site(vl: VLDict) -> list[list]:
    """Every ``axis.values`` list the spec carries on an x encoding.

    The outer spec and ``layer[0]`` hold the SAME encoding dict by reference
    (``render_cartesian_overlay`` hoists it rather than copying), so today
    these are two views of one list. Asserting at both paths keeps that true:
    if the hoist ever becomes a deep copy, a single-path assertion would go
    green against a half-fixed spec.
    """
    found: list[list] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            x_enc = node.get("encoding", {}).get("x")
            if isinstance(x_enc, dict):
                values = x_enc.get("axis", {}).get("values")
                if values is not None:
                    found.append(values)
            for child in node.get("layer", []):
                walk(child)

    walk(vl)
    return found


def _rendered_x_labels(vl: VLDict) -> list[str]:
    """The chart's x-axis category labels, ordered by rendered pixel position.

    Reads Vega's compiled scenegraph, not the VL spec — whether a label
    actually paints is the whole question here, and the spec cannot answer it.
    Numeric y-axis tick labels interleave in scenegraph order; a right-edge
    y-axis pads its labels with the digit-field device, so strip that padding
    before the digit check or a padded "0" reads as non-numeric and leaks in.
    """
    vega = vlc.vegalite_to_vega(json.dumps(vl))
    scenegraph = vlc.vega_to_scenegraph(vega)

    def walk(node: Any) -> Iterator[tuple[float, str]]:
        if isinstance(node, dict):
            if node.get("marktype") == "text" and node.get("role") == "axis-label":
                yield from ((item["x"], item["text"]) for item in node["items"])
            for child in node.get("items", []):
                yield from walk(child)
        elif isinstance(node, list):
            for child in node:
                yield from walk(child)

    positioned = sorted(walk(scenegraph["scenegraph"]), key=lambda pair: pair[0])
    pad = RESERVATION_GUARD
    # A wrapped label arrives as a list of lines; join it back into one string.
    joined = [
        (" ".join(text) if isinstance(text, list) else text) for _, text in positioned
    ]
    return [
        text
        for text in joined
        if not text.strip(pad).replace(",", "").replace(".", "").lstrip("-").isdigit()
    ]


def test_axis_values_cover_the_full_union_domain() -> None:
    """Every band the overlay adds to the scale must earn a tick value.

    The base carries 8 months, the goal layer 13. Before the fix the emitted
    ``values`` listed only the base's 8, so the right 5 bands rendered blank.
    """
    vl = _layered_vl()
    injections = _axis_values_at_every_injection_site(vl)
    assert injections, "expected axis.values on the shared x encoding"
    domain = vl["encoding"]["x"]["scale"]["domain"]
    assert len(domain) == len(_GOAL_MONTHS)
    for values in injections:
        assert len(values) == len(domain), (
            f"axis.values covers {len(values)} of {len(domain)} bands: {values}"
        )


def test_every_band_in_the_domain_renders_a_label() -> None:
    """The user-visible assertion: 13 bands, 13 painted labels."""
    labels = _rendered_x_labels(_layered_vl())
    assert len(labels) == len(_GOAL_MONTHS), f"{len(labels)} labels painted: {labels}"


def test_cadence_thins_to_openers_across_the_whole_union_domain() -> None:
    """``values`` carries the label cadence ladder, so the fix is "derive from
    the full domain, THEN thin" — not "emit every band".

    A label time unit coarser than the encoding grain filters the tick values
    to that period's calendar openers (``label_opener_values``). With months
    encoded and years labeled, that is one tick per January. Those openers
    must be picked out of the union domain: the base stops in 2024, so a
    base-derived list yields two openers and the overlay's 2025/2026 stretch
    goes unlabeled.

    (Width-driven visibility thinning is a different mechanism — it blanks
    labels through ``labelExpr`` and deliberately leaves the tick values at
    full grain, so it is not what this exercises.)
    """
    base = [
        {"month": f"{y}-{m:02d}-01", "revenue": 1.0}
        for y in (2023, 2024)
        for m in range(1, 13)
    ]
    goal = [
        {"month": f"{y}-{m:02d}-01", "goal": 2.0}
        for y in (2023, 2024, 2025, 2026)
        for m in range(1, 13)
    ]
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        variable_dependencies=set(),
        layers=[LineLayer(type="line", y="goal", query="goals")],
        style={"axis_x": {"labels": {"time_unit": "year"}}},
    )
    resolved = resolve(chart, base, _default_board_style())
    vl = translate_to_vl(
        BarEmitter().emit(
            resolved,
            RenderBox(width=600.0, height=300.0),
            regroup((), base),
            datasets={"goals": goal},
        )
    )
    assert vl["encoding"]["x"]["axis"]["values"] == [
        "2023-01-01",
        "2024-01-01",
        "2025-01-01",
        "2026-01-01",
    ]


def _emit_with_layer_rows(base_rows: list[dict], layer_rows: list[dict]) -> VLDict:
    """A layer whose own query returns the base's x field name, unauthored."""
    chart = NBarChart(
        id="bar1",
        type="bar",
        x=next(k for k in base_rows[0] if k != "revenue"),
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        variable_dependencies=set(),
        layers=[LineLayer(type="line", y="goal", query="goals")],
    )
    resolved = resolve(chart, base_rows, _default_board_style())
    return translate_to_vl(
        BarEmitter().emit(
            resolved,
            RenderBox(width=600.0, height=300.0),
            regroup((), base_rows),
            datasets={"goals": layer_rows},
        )
    )


def test_layer_column_of_a_different_type_does_not_crash_the_render() -> None:
    """A layer's own query can return the base's x field NAME holding another
    type. Sorting those together raises ``'<' not supported between 'str' and
    'int'`` — a bare TypeError out of the renderer. The union must skip a
    column that doesn't share the base's vocabulary, leaving the chart
    rendering exactly as it did before the union existed."""
    base = [{"sku": "A", "revenue": 1.0}, {"sku": "B", "revenue": 2.0}]
    layer = [{"sku": 1, "goal": 3.0}, {"sku": 2, "goal": 4.0}]
    vl = _emit_with_layer_rows(base, layer)
    assert vl["encoding"]["x"]["field"] == "sku"


def test_layer_of_plain_labels_against_a_calendar_base_does_not_crash() -> None:
    """Same-name, same-type (both str), but not the same vocabulary: plain
    labels are not buckets of the base's monthly grain. Merging them made
    calendar-grain detection raise with a remedy pointing at the axis style
    when the cause was the layer's query."""
    base = [{"month": m, "revenue": 1.0} for m in _BASE_MONTHS]
    layer = [{"month": "Sales", "goal": 1.0}, {"month": "Marketing", "goal": 2.0}]
    vl = _emit_with_layer_rows(base, layer)
    assert vl["encoding"]["x"]["axis"]["values"] == _BASE_MONTHS


def test_label_crowding_is_measured_against_the_rendered_band_count() -> None:
    """The domain feeds ``resolve_axis_x_overlap`` too, not just the tick values.

    Crowding has to be judged against the bands that actually render. With 3
    base months under a 400-month (33-year) goal layer at 300px, measuring the
    base's own 3 labels says they fit flat at native month cadence; measuring
    the 400 that render says nothing fits, even at the cadence ladder's
    terminal `year` rung, so it tilts. Every other assertion in this file
    passes with the overlap half stripped — the emitted spec is byte-identical
    — so without this one that half of the fix is unpinned.

    400 months (not a smaller count): the cadence ladder now loops all the
    way to `year` when that fits — a narrower layer whose year cadence WOULD
    fit flat no longer proves this test's point, since a wrongly-measured
    3-label base also renders flat. 400 months keeps even `year` too dense
    for 300px, so the tilt itself is still the signal.
    """
    base = [{"month": m, "revenue": 10.0 + i} for i, m in enumerate(_months(3))]
    goal = [{"month": m, "goal": 20.0 + i} for i, m in enumerate(_months(400))]
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        variable_dependencies=set(),
        layers=[LineLayer(type="line", y="goal", query="goals")],
    )
    resolved = resolve(chart, base, _default_board_style())
    vl = translate_to_vl(
        BarEmitter().emit(
            resolved,
            RenderBox(width=300.0, height=300.0),
            regroup((), base),
            datasets={"goals": goal},
        )
    )
    # The steepest rung of the theme's tilt ladder, not the literal -90.0 —
    # theme-populated values are not pinned in tests.
    steepest = resolved.style.axis_x.labels.tilt_increments[-1]
    assert vl["encoding"]["x"]["axis"]["labelAngle"] == steepest


def test_heterogeneous_base_column_does_not_crash_when_a_layer_is_added() -> None:
    """The base's own values are sorted too, and query results are not
    guaranteed homogeneous. Because the union runs for every layered chart —
    including ones whose x never resolves to a bucketed calendar grain, where
    nothing read these values before — a mixed base column turned adding a
    layer into a bare ``TypeError`` on a chart that rendered fine without one.
    """
    base = [{"bucket": 1, "revenue": 1.0}, {"bucket": "other", "revenue": 2.0}]
    layer = [{"bucket": 1, "goal": 3.0}, {"bucket": "other", "goal": 4.0}]
    vl = _emit_with_layer_rows(base, layer)
    assert vl["encoding"]["x"]["field"] == "bucket"


def test_vocabulary_gate_recognizes_timezone_aware_calendar_columns() -> None:
    """The gate decides whether a layer's column may widen the tick domain.

    A timezone-aware x column normalizes to an offset-suffixed ISO string, a
    form no date pattern matches, so a pattern-based check reads it as plain
    labels. That is wrong in both directions: it refuses a legitimate merge of
    two date columns, and it admits a plain-label layer by matching "neither
    is a date". Deciding membership with the same parser the grain detector
    uses gets all three right.
    """
    from dbt_charts.core.render.chart.emitters._overlay import _same_x_vocabulary

    tz_aware = [f"2026-{m:02d}-01T00:00:00+00:00" for m in range(1, 9)]
    plain_dates = [f"2026-{m:02d}-01" for m in range(1, 13)]

    assert _same_x_vocabulary(tz_aware, plain_dates)
    assert _same_x_vocabulary(plain_dates, tz_aware)
    assert not _same_x_vocabulary(tz_aware, ["Sales", "Marketing"])


def test_half_year_layer_column_does_not_crash_the_render() -> None:
    """Calendar membership has to be decided by the parser, not by shape.

    ``2024-H1`` looks like a date bucket and no normalizer rewrites it, but
    the grain detector cannot parse it. A shape-based gate admitted it into a
    monthly domain and the render raised — on a chart that rendered before the
    union existed.
    """
    base = [{"month": m, "revenue": 1.0} for m in _BASE_MONTHS]
    layer = [{"month": "2024-H1", "goal": 1.0}, {"month": "2025-H1", "goal": 2.0}]
    vl = _emit_with_layer_rows(base, layer)
    assert vl["encoding"]["x"]["axis"]["values"] == _BASE_MONTHS


def test_gate_is_all_or_none_not_all_equals_all() -> None:
    """The two sides must be wholly calendar or wholly not.

    ``all(base) == all(layer)`` looks equivalent and is not: a base carrying a
    few unparseable strays is not "all calendar", so it matched a plain-label
    layer on both-being-false and admitted it into the domain. ``any`` fails
    the other way, admitting a layer that mixes one real date into labels.

    Pinned at the gate rather than through a render: a base with unparseable
    strays raises in the overlap resolver with or without a layer, so an
    emit-path test would be attributing a pre-existing crash to this change.
    """
    from dbt_charts.core.render.chart.emitters._overlay import _same_x_vocabulary

    calendar = _months(8)
    strays = ["n/a", "unknown", "TBD"]
    labels = ["Sales", "Marketing"]

    assert not _same_x_vocabulary(calendar + strays, labels)
    assert not _same_x_vocabulary(calendar, labels + calendar[:1])
    assert _same_x_vocabulary(calendar, _months(13))
    assert _same_x_vocabulary(labels, ["East", "West"])


def test_one_band_spelled_two_ways_counts_once() -> None:
    """A layer returning datetimes isoformats to a long form where the base
    spells the same band date-only. They are one band; counting both measures
    a scale wider than the one rendering, which rotates labels that fit flat.
    """
    from dbt_charts.core.render.chart.emitters._overlay import (
        overlay_x_domain_values,
    )

    months = _months(24)
    base = [{"month": m, "revenue": 1.0} for m in months]
    layer = [
        {
            "month": dt.datetime.fromisoformat(m).replace(tzinfo=dt.UTC),
            "goal": 2.0,
        }
        for m in months
    ]
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="month",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        variable_dependencies=set(),
        style={"axis_x": {"type": "temporal"}},
        layers=[LineLayer(type="line", y="goal", query="goals")],
    )
    resolved = resolve(chart, base, _default_board_style())
    domain = overlay_x_domain_values(
        resolved.layers,
        base,
        "month",
        resolved.style.axis_x,
        True,
        {"goals": layer},
        "q",
    )
    assert domain is not None
    assert len(domain) == len(months)


def test_sub_daily_bands_are_not_collapsed_into_days() -> None:
    """Band identity is the instant, not the calendar day.

    Six-hourly readings put four genuinely distinct bands inside one day.
    Keying the union on the day collapsed eight rendered bands to two, and the
    overlap resolver — which has no bucketed-time gate — then measured
    crowding against two, switched Vega's adaptive overlap removal off and
    painted every sub-day label unthinned. Adding a layer must not do that to
    a chart that rendered correctly without one.
    """
    from dbt_charts.core.render.chart.emitters._overlay import (
        overlay_x_domain_values,
    )

    stamps = [
        f"2024-01-{day:02d}T{hour:02d}:00:00"
        for day in (1, 2)
        for hour in (0, 6, 12, 18)
    ]
    base = [{"t": s, "revenue": 1.0} for s in stamps]
    layer = [{"t": s, "goal": 2.0} for s in stamps]
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="t",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        variable_dependencies=set(),
        layers=[LineLayer(type="line", y="goal", query="goals")],
    )
    resolved = resolve(chart, base, _default_board_style())
    domain = overlay_x_domain_values(
        resolved.layers,
        base,
        "t",
        resolved.style.axis_x,
        False,
        {"goals": layer},
        "q",
    )
    assert domain is not None
    assert len(domain) == len(stamps)
