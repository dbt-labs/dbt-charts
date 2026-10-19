"""Regression coverage for warehouse-shaped date buckets on cartesian charts.

For a genuinely continuous (non-bucketed) date shape, bar and line charts share
identical Vega-Lite x-encoding — swapping mark type never silently changes
sub-daily timestamp semantics. For a BUCKETED_CALENDAR_UNITS grain (yearmonth,
yearquarter, yearweek, year), bar and line intentionally diverge: bar/column
default to ordinal bands (D-002), line/area default to continuous temporal (see
value-driven-axis-type-inference task) — this file pins both halves of that
split, plus the value normalization that both mark types share.

Date-shape semantics covered here:
- ISO datetimes whose clock is not 00:00:00 disable ``time_unit: auto``
  detection (`detect_time_unit` treats any non-midnight as sub-daily) and fall
  back to a continuous **temporal** axis (both mark types, unaffected by the
  bar/line split above — there's no bucketed grain to split on).
- ``infer_vega_type_from_data`` recognizes both ``T`` and space separators in
  timestamp strings (the space form is what the dbt adapter / DuckDB cache emit
  for Snowflake), so midnight ``datetime`` month buckets resolve to a
  BUCKETED_CALENDAR_UNITS grain (ordinal on bar, temporal on line).

Cross-links: D-002 (bar ordinal default), D-003a (UTC path).
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import TypeAdapter as _TA

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    Chart,
)
from dbt_charts.core.compile.models.primitives import BoardLinks

_CHART_ADAPTER: _TA[Chart] = _TA(Chart)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.time_unit_detect import (
    detect_time_unit,
    normalize_labeled_temporal,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


def _x_encoding(chart_type: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    chart = _CHART_ADAPTER.validate_python(
        {"id": f"test_{chart_type}", "type": chart_type, "x": "m", "y": "v"}
    )
    resolved = resolve(chart, rows, chart_style_context=_BOARD_CTX)
    spec = render_resolved_chart(resolved, rows, _BOARD_STYLE).payload
    return spec.get("encoding", {}).get("x", {})


def _x_core(enc: dict[str, Any]) -> dict[str, Any]:
    """Stable subset for equality — axis paint differs; encoding channel must not."""
    keys = ("field", "type", "timeUnit", "sort")
    return {k: enc[k] for k in keys if k in enc}


def _assert_bar_line_x_match(rows: list[dict[str, Any]], label: str) -> None:
    xb = _x_core(_x_encoding("bar", rows))
    xl = _x_core(_x_encoding("line", rows))
    assert xb == xl, f"{label}: bar x {xb!r} != line x {xl!r}"


def _assert_bar_ordinal_line_temporal(
    rows: list[dict[str, Any]], label: str, expected_time_unit: str
) -> None:
    """Pin the bucketed-grain bar/line split: bar stays ordinal band, line
    always routes to continuous temporal (value-driven-axis-type-inference)."""
    xb = _x_core(_x_encoding("bar", rows))
    xl = _x_core(_x_encoding("line", rows))
    assert xb.get("type") == "ordinal", f"{label}: bar x {xb!r} must stay ordinal"
    assert "timeUnit" not in xb, f"{label}: bar x {xb!r} must not emit timeUnit"
    assert xl.get("type") == "temporal", f"{label}: line x {xl!r} must be temporal"
    assert xl.get("timeUnit") == f"utc{expected_time_unit}", (
        f"{label}: line x {xl!r} must emit timeUnit utc{expected_time_unit}"
    )


# --- bar / line encoding parity ---


def test_bar_line_match_iso_date_first_of_month() -> None:
    rows = [{"m": f"2024-{m:02d}-01", "v": m} for m in range(1, 5)]
    _assert_bar_ordinal_line_temporal(
        rows, label="iso YYYY-MM-DD", expected_time_unit="yearmonth"
    )


def test_bar_line_match_python_date_first_of_month() -> None:
    rows = [{"m": dt.date(2024, m, 1), "v": m} for m in range(1, 5)]
    _assert_bar_ordinal_line_temporal(
        rows, label="datetime.date", expected_time_unit="yearmonth"
    )


def test_bar_line_match_datetime_midnight_buckets() -> None:
    """``render_standard_vega_spec`` runs ``normalize_data_types`` first.

    Python stringifies naive ``datetime`` as ``"YYYY-MM-DD HH:MM:SS"`` (space
    separator). ``infer_vega_type_from_data`` recognizes that form as temporal,
    so midnight month buckets resolve to the same BUCKETED_CALENDAR_UNITS grain
    as ISO ``YYYY-MM-DD`` and ``datetime.date`` inputs: bar stays ordinal (D-002),
    line goes continuous temporal.
    """

    rows = [{"m": dt.datetime(2024, m, 1, 0, 0, 0), "v": m} for m in range(1, 5)]
    _assert_bar_ordinal_line_temporal(
        rows, label="datetime midnight", expected_time_unit="yearmonth"
    )


def test_bar_line_match_year_month_strings() -> None:
    rows = [{"m": f"2024-{m:02d}", "v": m} for m in range(1, 5)]
    _assert_bar_ordinal_line_temporal(
        rows, label="YYYY-MM strings", expected_time_unit="yearmonth"
    )


def test_bar_line_match_after_quarter_label_normalization() -> None:
    raw = [{"m": "2024-Q1", "v": 1}, {"m": "2024-Q2", "v": 2}, {"m": "2024-Q3", "v": 3}]
    rows = normalize_labeled_temporal(raw, "m")
    assert rows[0]["m"] == "2024-01-01"
    _assert_bar_ordinal_line_temporal(
        rows, label="quarter labels → ISO dates", expected_time_unit="yearquarter"
    )


def test_bar_line_match_after_week_label_normalization() -> None:
    raw = [{"m": "2024-W01", "v": 1}, {"m": "2024-W02", "v": 2}]
    rows = normalize_labeled_temporal(raw, "m")
    _assert_bar_ordinal_line_temporal(
        rows, label="week labels → ISO dates", expected_time_unit="yearweek"
    )


def test_detect_time_unit_iso_dates() -> None:
    vals = [f"2024-{m:02d}-01" for m in range(1, 5)]
    assert detect_time_unit(vals) == "yearmonth"


def test_detect_time_unit_python_dates() -> None:
    vals = [dt.date(2024, m, 1) for m in range(1, 5)]
    assert detect_time_unit(vals) == "yearmonth"


def test_detect_time_unit_accepts_plain_year_month_strings() -> None:
    """YYYY-MM is now a recognized yearmonth bucket format."""
    vals = [f"2024-{m:02d}" for m in range(1, 5)]
    assert detect_time_unit(vals) == "yearmonth"


@pytest.mark.parametrize(
    ("label", "rows", "no_time_unit", "x_type"),
    [
        (
            "noon-utc iso month stamps",
            [
                {"m": "2024-01-01T12:00:00Z", "v": 1},
                {"m": "2024-02-01T12:00:00Z", "v": 2},
            ],
            True,
            "temporal",
        ),
        (
            "non-midnight wall-time datetimes on month boundaries",
            [
                {"m": dt.datetime(2024, 1, 1, 9, 0, 0), "v": 1},
                {"m": dt.datetime(2024, 2, 1, 9, 0, 0), "v": 2},
            ],
            True,
            "temporal",
        ),
    ],
)
def test_documented_timezone_and_detection_edges(
    label: str,
    rows: list[dict[str, Any]],
    *,
    no_time_unit: bool,
    x_type: str,
) -> None:
    """Non-midnight timestamps (tz-suffixed or wall-time) are temporal-continuous:
    recognized as dates but with no bucketable grain, so no timeUnit is emitted."""

    _assert_bar_line_x_match(rows, label=label)
    enc = _x_core(_x_encoding("bar", rows))
    assert enc["type"] == x_type, label
    if no_time_unit:
        assert enc.get("timeUnit") is None, label


def test_json_dump_encoding_x_stable() -> None:
    """Guards shallow dict key-order noise in assertions.

    Uses non-midnight timestamps (no bucketable grain) so bar and line agree —
    the bucketed-grain split (bar ordinal / line temporal) is pinned separately
    by _assert_bar_ordinal_line_temporal above."""
    rows = [
        {"m": "2024-01-01T09:00:00", "v": 1},
        {"m": "2024-02-01T09:00:00", "v": 2},
    ]
    bar_json = json.dumps(_x_core(_x_encoding("bar", rows)), sort_keys=True)
    line_json = json.dumps(_x_core(_x_encoding("line", rows)), sort_keys=True)
    assert bar_json == line_json


def test_mon_dd_yyyy_lex_ordered_rows_become_chronological() -> None:
    """Bars draw in chronological order even when rows arrive in lex-sorted Mon DD YYYY order.

    'Mon DD, YYYY' strings lex-sort Feb < Jan < Mar, so a warehouse ORDER BY 1
    produces a wrong row order for a time axis. After normalize_labeled_temporal
    rewrites values to ISO, the bar/line emitters must sort rows by the ISO
    x-field so the Vega-Lite scale domain follows chronological order, not
    input row order.
    """
    # Build rows in lex order: Feb first (F < J < M), then Jan, then Mar
    rows: list[dict[str, Any]] = []
    for month in (2, 1, 3):
        _, days_in_month = calendar.monthrange(2024, month)
        for day in range(1, days_in_month + 1):
            label = dt.date(2024, month, day).strftime("%b %d, %Y")
            rows.append({"m": label, "v": 1})

    assert rows[0]["m"].startswith("Feb"), (
        "test data must open with Feb rows (lex first)"
    )

    for chart_type in ("bar", "line"):
        chart = _CHART_ADAPTER.validate_python(
            {"id": f"test_{chart_type}", "type": chart_type, "x": "m", "y": "v"}
        )
        resolved = resolve(chart, rows, chart_style_context=_BOARD_CTX)
        spec = render_resolved_chart(resolved, rows, _BOARD_STYLE).payload
        x_vals = [r["m"] for r in spec.get("data", {}).get("values", []) if "m" in r]
        assert x_vals, f"{chart_type}: no x values in spec data"
        # ISO strings lex-sort == chronological; list must be non-decreasing
        assert x_vals == sorted(x_vals), (
            f"{chart_type}: spec data not chronological; "
            f"first={x_vals[0]!r} last={x_vals[-1]!r}"
        )

    _assert_bar_ordinal_line_temporal(
        rows, label="Mon DD, YYYY lex-sorted rows", expected_time_unit="yearmonthdate"
    )


def test_categorical_x_row_order_unchanged() -> None:
    """Arbitrary categorical x-values must not be reordered by the renderer.

    normalize_labeled_temporal returns the input list unchanged (same object)
    when no labeled bucket strings are found. The sort gate in the bar/line
    emitters uses identity comparison to detect rewrites; this test pins that
    it stays off for plain categorical data so arbitrary ordering isn't
    silently broken.
    """
    categories = ["cherry", "banana", "apple"]
    rows = [{"m": v, "v": i} for i, v in enumerate(categories)]
    chart = BarChart(id="test_bar", type="bar", x="m", y="v")
    resolved = resolve(chart, rows, chart_style_context=_BOARD_CTX)
    spec = render_resolved_chart(resolved, rows, _BOARD_STYLE).payload
    x_vals = [r["m"] for r in spec.get("data", {}).get("values", []) if "m" in r]
    assert x_vals == categories, (
        f"categorical rows must not be reordered; got {x_vals!r}"
    )


# --- ordinal gap-fill lookup-key regression (bar renders real marks, not synthesized nulls) ---


@pytest.mark.parametrize(
    ("label", "rows"),
    [
        (
            "T separator, yearmonth grain",
            [{"m": f"2024-{m:02d}-01T00:00:00", "v": m} for m in range(1, 4)],
        ),
        (
            "space separator, yearmonth grain",
            [{"m": f"2024-{m:02d}-01 00:00:00", "v": m} for m in range(1, 4)],
        ),
        (
            "Z-suffixed timezone, yearmonth grain",
            [{"m": f"2024-{m:02d}-01T00:00:00Z", "v": m} for m in range(1, 4)],
        ),
        (
            "T separator, year grain",
            [
                {"m": f"{y}-01-01T00:00:00", "v": i}
                for i, y in enumerate((2022, 2023, 2024), start=1)
            ],
        ),
        (
            "T separator, yearquarter grain",
            [
                {"m": m_val, "v": i}
                for i, m_val in enumerate(
                    (
                        "2024-01-01T00:00:00",
                        "2024-04-01T00:00:00",
                        "2024-07-01T00:00:00",
                    ),
                    start=1,
                )
            ],
        ),
        (
            "T separator, yearmonthdate (day) grain",
            [
                {"m": m_val, "v": i}
                for i, m_val in enumerate(
                    (
                        "2024-01-01T00:00:00",
                        "2024-01-02T00:00:00",
                        "2024-01-03T00:00:00",
                    ),
                    start=1,
                )
            ],
        ),
        (
            "mixed ISO datetime strings and datetime objects in one column",
            [
                {"m": "2024-01-01T00:00:00", "v": 1},
                {"m": dt.datetime(2024, 2, 1, 0, 0, 0), "v": 2},
                {"m": "2024-03-01T00:00:00", "v": 3},
            ],
        ),
        (
            "control: datetime.date objects",
            [{"m": dt.date(2024, m, 1), "v": m} for m in range(1, 4)],
        ),
    ],
)
def test_ordinal_bucket_key_matches_scaffold_and_renders_real_marks(
    make_chart: Any, label: str, rows: list[dict[str, Any]]
) -> None:
    """`complete_ordinal_time_series` cross-joins a bucket scaffold against the
    query rows by `_ordinal_bucket_key`. If a row's key doesn't match the
    scaffold's ISO-date-string key, the row is treated as missing and every
    bucket is synthesized null — a chart-shaped hole with zero real marks.
    Asserts through the real render path (`render_chart_item`), not a
    hand-built VL spec, so a regression here fails the way the bug actually
    manifests: ERR-CHART-PAINTED-NO-MARKS on data that should have painted.
    """
    from unittest.mock import MagicMock

    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.chart.mark_extents import mark_extents
    from dbt_charts.core.render.chart.rendering import render_chart_item

    chart = make_chart("bar", x="m", y="v")
    executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    executor.execute_query.return_value = rows
    resolved_chart = resolve(chart, rows, chart_style_context=_BOARD_CTX)

    svg, _height = render_chart_item(
        resolved_chart,
        executor,
        variables={},
        available_width=400,
        available_height=200,
        resolved_style=_BOARD_STYLE,
        render_cache={},
    )

    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg, (
        f"{label}: guard fired — 0 real marks"
    )
    wrapped_svg = f'<svg xmlns="http://www.w3.org/2000/svg">{svg}</svg>'
    extents = mark_extents(wrapped_svg)
    measured = extents[0].count if extents else 0
    # +1: the always-on zero-baseline rule mark (features/baseline.py) counts
    # alongside the real bars.
    expected = len(rows) + 1
    assert measured == expected, (
        f"{label}: expected {expected} marks, measured {measured}"
    )


# --- authored ``time_unit: auto`` sentinel must resolve to a real grain ---


@pytest.mark.parametrize(
    ("label", "rows"),
    [
        (
            "ISO date string",
            [{"m": f"2024-{m:02d}-01", "v": m} for m in range(1, 4)],
        ),
        (
            "ISO datetime string",
            [{"m": f"2024-{m:02d}-01T00:00:00", "v": m} for m in range(1, 4)],
        ),
        (
            "MM/DD/YYYY string",
            [{"m": f"{m:02d}/01/2024", "v": m} for m in range(1, 4)],
        ),
        (
            "YYYY-MM string",
            [{"m": f"2024-{m:02d}", "v": m} for m in range(1, 4)],
        ),
        (
            "typed DATE",
            [{"m": dt.date(2024, m, 1), "v": m} for m in range(1, 4)],
        ),
    ],
)
def test_time_unit_auto_renders_marks_for_every_date_shape(
    make_chart: Any, label: str, rows: list[dict[str, Any]]
) -> None:
    """Authoring ``style.axis_x.time_unit: auto`` must resolve to a real
    detected grain, not survive as the literal string "auto" into the VL
    ``timeUnit`` slot — an invalid VL timeUnit drops the whole x-encoding and
    paints zero marks, for every date shape (axis_x.time_unit: auto renders
    an empty chart for every date shape).
    """
    from unittest.mock import MagicMock

    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.chart.mark_extents import mark_extents
    from dbt_charts.core.render.chart.rendering import render_chart_item

    chart = make_chart("bar", x="m", y="v", style={"axis_x": {"time_unit": "auto"}})
    resolved_chart = resolve(chart, rows, chart_style_context=_BOARD_CTX)

    spec = render_resolved_chart(resolved_chart, rows, _BOARD_STYLE).payload
    x_enc = spec.get("encoding", {}).get("x", {})
    assert x_enc.get("timeUnit") != "auto", (
        f"{label}: literal sentinel 'auto' leaked into VL timeUnit: {x_enc!r}"
    )

    executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    executor.execute_query.return_value = rows
    svg, _height = render_chart_item(
        resolved_chart,
        executor,
        variables={},
        available_width=400,
        available_height=200,
        resolved_style=_BOARD_STYLE,
        render_cache={},
    )

    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg, (
        f"{label}: guard fired — 0 real marks under time_unit: auto"
    )
    wrapped_svg = f'<svg xmlns="http://www.w3.org/2000/svg">{svg}</svg>'
    extents = mark_extents(wrapped_svg)
    measured = extents[0].count if extents else 0
    # +1: the always-on zero-baseline rule mark (features/baseline.py) counts
    # alongside the real bars.
    expected = len(rows) + 1
    assert measured == expected, (
        f"{label}: expected {expected} marks under time_unit: auto, measured {measured}"
    )


# --- ordinal scaffold budget gates line/area/scatter grain too ---


def _off_lattice_dates(start: dt.date, n: int, gap_days: int) -> list[dt.date]:
    """n dates gap_days apart, on no calendar lattice. 45 days is no whole
    number of months, quarters or years and shifts weekday every step, so
    nothing coarser fits and ``detect_time_unit`` returns yearmonthdate — a
    day grain whose [min, max] span is ~45x the row count."""
    return [start + dt.timedelta(days=gap_days * i) for i in range(n)]


def test_line_area_scatter_drop_grain_past_scaffold_budget() -> None:
    """Ten dates 45 days apart detect as yearmonthdate (day) grain, a 406-day
    span for 10 real points. ``ordinal_scaffold_within_budget``
    already rejects this for bar; line/area/scatter must consult the
    same gate and drop the grain too, or Vega-Lite bands one gridline per day
    into a solid gray stripe behind the plot."""
    rows = [
        {"m": d.isoformat(), "v": i}
        for i, d in enumerate(_off_lattice_dates(dt.date(2023, 1, 4), 10, 45))
    ]
    for chart_type in ("line", "area", "scatter"):
        enc = _x_encoding(chart_type, rows)
        assert enc.get("type") == "temporal", f"{chart_type}: {enc!r}"
        assert "timeUnit" not in enc, (
            f"{chart_type}: over-budget grain must be dropped, got {enc!r}"
        )


def test_line_area_scatter_dense_daily_keeps_grain() -> None:
    """120 contiguous daily points is within budget (span == row count) — the
    control proving the scaffold gate discriminates rather than dropping
    grain everywhere. Must not move alongside the sparse case above."""
    first = dt.date(2024, 1, 1)
    rows = [
        {"m": (first + dt.timedelta(days=i)).isoformat(), "v": i} for i in range(120)
    ]
    for chart_type in ("line", "area", "scatter"):
        enc = _x_encoding(chart_type, rows)
        assert enc.get("type") == "temporal", f"{chart_type}: {enc!r}"
        assert enc.get("timeUnit") == "utcyearmonthdate", (
            f"{chart_type}: dense-daily grain must be preserved, got {enc!r}"
        )


def _faceted_x_encoding(chart_type: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Like ``_x_encoding``, but with ``multiples: {rows: "panel"}`` authored,
    reading the x encoding out of the faceted spec's inner ``spec`` pane."""
    chart = _CHART_ADAPTER.validate_python(
        {
            "id": f"test_{chart_type}",
            "type": chart_type,
            "x": "m",
            "y": "v",
            "multiples": {"rows": "panel"},
        }
    )
    resolved = resolve(chart, rows, chart_style_context=_BOARD_CTX)
    spec = render_resolved_chart(resolved, rows, _BOARD_STYLE).payload
    return spec["spec"]["encoding"]["x"]


def test_faceted_line_area_scatter_dense_panels_keep_grain() -> None:
    """Two panels of contiguous dailies twenty years apart: each panel is
    individually dense (zero synthesized buckets, budget == row count), so
    the per-panel scaffold measurement bar.py already uses must apply here
    too. Pooling the two panels' spans instead reads an ~7,300-day range for
    80 real points and wrongly drops the grain — the exact failure
    ``ordinal_scaffold_within_budget``'s docstring names as the case this
    gate exists to prevent."""
    rows = [
        {"m": (start + dt.timedelta(days=i)).isoformat(), "v": i, "panel": panel}
        for panel, start in (("a", dt.date(2000, 1, 1)), ("b", dt.date(2020, 1, 1)))
        for i in range(40)
    ]
    for chart_type in ("line", "area", "scatter"):
        enc = _faceted_x_encoding(chart_type, rows)
        assert enc.get("type") == "temporal", f"{chart_type}: {enc!r}"
        assert enc.get("timeUnit") == "utcyearmonthdate", (
            f"{chart_type}: dense per-panel grain must be preserved, got {enc!r}"
        )
