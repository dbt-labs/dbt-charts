"""Bar annotations resolve against category, date and numeric x, grouped or stacked."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored import BarChart as AuthoredBar
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved._annotation import (
    ResolvedAnnotation,
    ResolvedCategoryAnchor,
    ResolvedRangeAnchor,
    ResolvedValueAnchor,
)
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

NOW = datetime(2024, 6, 14, 9, 30, tzinfo=UTC)

REGIONS: list[dict[str, Any]] = [
    {"region": r, "revenue": v}
    for r, v in (("East", 10.0), ("West", 14.0), ("North", 8.0))
]
MONTHS: list[dict[str, Any]] = [
    {"month": date(2024, m, 1), "revenue": v}
    for m, v in enumerate([10.0, 12.0, 11.0, 15.0, 14.0, 18.0], start=1)
]
NUMERIC: list[dict[str, Any]] = [
    {"size": s, "revenue": v} for s, v in ((10, 5.0), (20, 9.0), (30, 7.0), (40, 12.0))
]
STACKED: list[dict[str, Any]] = [
    {"region": r, "team": t, "revenue": v}
    for r, parts in (("East", (4.0, 6.0)), ("West", (5.0, 9.0)))
    for t, v in zip(("a", "b"), parts, strict=True)
]


def _ctx() -> ChartStyleContext:
    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _bar(
    annotations: list[dict[str, Any]],
    rows: list[dict[str, Any]] = REGIONS,
    rendered_at: datetime | None = NOW,
    **chart: Any,
) -> ResolvedBarChart:
    normalized = BarChart.model_validate(
        {
            "id": "rev",
            "type": "bar",
            "x": "region",
            "y": "revenue",
            "query_name": "q",
            "annotations": annotations,
            **chart,
        }
    )
    resolved = resolve(normalized, rows, _ctx(), rendered_at=rendered_at)
    assert isinstance(resolved, ResolvedBarChart)
    return resolved


def _one(annotation: dict[str, Any], **kwargs: Any) -> ResolvedAnnotation:
    [item] = _bar([annotation], **kwargs).annotations
    return item


def _value(anchor: object) -> object:
    assert isinstance(anchor, ResolvedValueAnchor)
    return anchor.value


def test_a_y_rule_reads_the_measure_and_draws_under_the_bars() -> None:
    rule = _one({"type": "rule", "y": 12, "text": "Quota {{ value | format }}"})
    assert _value(rule.y) == 12.0
    assert rule.x is None and rule.plane == "background" and rule.y_axis == "base"
    assert rule.text is not None and rule.text.startswith("Quota ")


def test_a_value_beyond_the_bars_joins_the_y_domain() -> None:
    plain = _bar([]).style.axis_y.tick_values
    widened = _bar([{"type": "rule", "y": 60}]).style.axis_y.tick_values
    assert max(plain) < 60 <= max(widened)


def test_a_band_rule_or_note_sits_at_a_category() -> None:
    east = ResolvedCategoryAnchor(kind="category", value="East")
    band = _one({"type": "band", "x": "East"})
    assert band.x == east
    assert band.plane == "background"
    assert _one({"type": "rule", "x": "East", "text": "{{ value }}"}).text == "East"
    assert _one({"type": "rule", "x": "East"}).x == east
    note = _one({"type": "note", "x": "East", "y": 17.8, "text": "n"})
    assert note.x == east and _value(note.y) == 17.8
    with pytest.raises(CompilationError, match="avg\\(\\) on a category x"):
        _one({"type": "rule", "x": "avg()"})
    with pytest.raises(CompilationError, match="category x"):
        _one({"type": "trend", "fit": "linear()"})


def test_a_horizontal_bar_resolves_the_same_channels() -> None:
    chart = _bar(
        [{"type": "rule", "y": 12}, {"type": "band", "x": "West"}],
        style={"orientation": "horizontal"},
    )
    rule, band = chart.annotations
    assert _value(rule.y) == 12.0 and rule.x is None
    assert band.x == ResolvedCategoryAnchor(kind="category", value="West")


def test_date_bars_take_ranges_notes_and_trends() -> None:
    kwargs: dict[str, Any] = {"rows": MONTHS, "x": "month"}
    band = _one(
        {"type": "band", "x": {"from": "2024-02-01", "to": "2024-03-01"}}, **kwargs
    )
    assert band.x == ResolvedRangeAnchor(
        kind="range", **{"from": "2024-02-01", "to": "2024-03-01"}
    )
    note = _one(
        {"type": "note", "x": date(2024, 4, 1), "y": 15, "text": "Peak"}, **kwargs
    )
    assert _value(note.x) == "2024-04-01"
    trend = _one(
        {"type": "trend", "fit": "linear()", "interval": "confidence(95)"}, **kwargs
    )
    assert trend.trend is not None and len(trend.trend.line) == len(MONTHS) + 2
    assert trend.trend.interval is not None


def _days(iso: str) -> float:
    return (datetime.fromisoformat(iso) - datetime(1970, 1, 1)).total_seconds() / 86400


def test_a_date_bar_trend_reaches_half_a_band_past_both_ends() -> None:
    trend = _one(
        {"type": "trend", "fit": "linear()", "interval": "confidence(95)"},
        rows=MONTHS,
        x="month",
    ).trend
    assert trend is not None and trend.interval is not None
    xs = [_days(x) for x, _ in trend.line]
    # Jan 1 less half of Jan (31 days); Jun 1 plus half of May (31 days).
    assert xs[0] == _days("2024-01-01") - 15.5
    assert xs[-1] == _days("2024-06-01") + 15.5
    assert xs[1:-1] == [_days(f"2024-0{m}-01") for m in range(1, 7)]
    # The fit is the one over the six points, evaluated at the edges.
    mean_x = sum(xs[1:-1]) / 6
    ys = [r["revenue"] for r in MONTHS]
    mean_y = sum(ys) / 6
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs[1:-1], ys, strict=True))
    slope /= sum((x - mean_x) ** 2 for x in xs[1:-1])
    for x, y in (trend.line[0], trend.line[-1]):
        assert y == pytest.approx(mean_y + slope * (_days(x) - mean_x))
    assert [x for x, _, _ in trend.interval] == [x for x, _ in trend.line]


def test_a_date_bar_trend_range_carries_slack_for_the_engine_to_cut() -> None:
    trend = _one(
        {
            "type": "trend",
            "fit": "linear()",
            "x": {"from": "2024-02-01", "to": "2024-04-01"},
        },
        rows=MONTHS,
        x="month",
    ).trend
    assert trend is not None
    first, last = _days(trend.line[0][0]), _days(trend.line[-1][0])
    assert first < _days("2024-02-01") - 15.5
    assert last > _days("2024-04-01") + 15.5


def test_numeric_x_bar_trend_spans_the_data_extent() -> None:
    trend = _one({"type": "trend", "fit": "linear()"}, rows=NUMERIC, x="size").trend
    assert trend is not None
    assert [x for x, _ in trend.line] == [10.0, 20.0, 30.0, 40.0]


def test_a_clock_period_reads_the_render_time_on_date_bars() -> None:
    kwargs: dict[str, Any] = {"rows": MONTHS, "x": "month"}
    band = _one({"type": "band", "x": "current(month)"}, **kwargs)
    assert band.x == ResolvedRangeAnchor(
        kind="range", **{"from": "2024-06-01", "to": "2024-07-01"}
    )
    chart = _bar([{"type": "band", "x": "current(year)"}], **kwargs)
    assert len(chart.annotations) == 1
    outside = _bar(
        [{"type": "band", "x": "current(month)"}],
        rendered_at=datetime(2025, 1, 5, tzinfo=UTC),
        **kwargs,
    )
    assert [a.index for a in outside.annotations] == [0]


def test_numeric_x_bars_take_numbers_and_trends() -> None:
    kwargs: dict[str, Any] = {"rows": NUMERIC, "x": "size"}
    note = _one({"type": "note", "x": 20, "y": 9, "text": "Mode"}, **kwargs)
    assert _value(note.x) == 20.0
    band = _one({"type": "band", "x": {"from": 15, "to": 35}}, **kwargs)
    assert band.x == ResolvedRangeAnchor(kind="range", **{"from": 15.0, "to": 35.0})
    trend = _one({"type": "trend", "fit": "linear()"}, **kwargs)
    assert trend.trend is not None and len(trend.trend.line) == len(NUMERIC)


def test_a_grouped_bar_expands_per_series_in_series_color() -> None:
    chart = _bar(
        [{"type": "rule", "y": "avg()", "series": "each"}],
        rows=STACKED,
        color="team",
        stack="none",
    )
    assert [a.series for a in chart.annotations] == ["a", "b"]
    assert len({a.style.rule.stroke.color for a in chart.annotations}) == 2
    with pytest.raises(CompilationError, match="set series"):
        _bar([{"type": "rule", "y": "avg()"}], rows=STACKED, color="team", stack="none")


def test_a_grouped_bar_total_pools_every_bar() -> None:
    [rule] = _bar(
        [{"type": "rule", "y": "max()", "series": "total"}],
        rows=STACKED,
        color="team",
        stack="none",
    ).annotations
    assert _value(rule.y) == 9.0


def test_a_stacked_bar_takes_only_the_total() -> None:
    chart = _bar(
        [{"type": "rule", "y": "max()", "series": "total"}],
        rows=STACKED,
        color="team",
        stack="zero",
    )
    [rule] = chart.annotations
    assert _value(rule.y) == 14.0
    with pytest.raises(CompilationError, match="only series: total"):
        _bar(
            [{"type": "rule", "y": "avg()", "series": "each"}],
            rows=STACKED,
            color="team",
            stack="zero",
        )


def test_a_value_beyond_the_stack_joins_the_stacked_domain() -> None:
    kwargs: dict[str, Any] = {"rows": STACKED, "color": "team", "stack": "zero"}
    plain = _bar([], **kwargs)
    widened = _bar([{"type": "rule", "y": 60}], **kwargs)
    assert plain.stacked_domain_max is not None
    assert widened.stacked_domain_max is not None
    assert widened.stacked_domain_max >= 60 > plain.stacked_domain_max
    assert max(widened.style.axis_y.tick_values) >= 60


def test_multiples_compute_per_panel_and_literals_draw_on_every_panel() -> None:
    rows = [
        {"region": r, "revenue": v, "channel": c}
        for c, scale in (("web", 1.0), ("shop", 3.0))
        for r, v in (("East", 10.0), ("West", 14.0))
        for v in (v * scale,)
    ]
    chart = _bar(
        [{"type": "rule", "y": "avg()"}, {"type": "rule", "y": 5}],
        rows=rows,
        multiples={"columns": "channel"},
    )
    averages = [a for a in chart.annotations if a.panel is not None]
    literal = [a for a in chart.annotations if a.panel is None]
    assert len(averages) == 2 and len(literal) == 1
    assert sorted(_value(a.y) for a in averages) == [12.0, 36.0]


def test_a_resolve_with_no_clock_draws_no_annotations() -> None:
    chart = _bar([{"type": "rule", "y": 12}], rendered_at=None)
    assert chart.annotations == ()


def test_a_histogram_takes_no_annotations() -> None:
    with pytest.raises(ValidationError, match="annotations"):
        AuthoredBar.model_validate(
            {
                "type": "histogram",
                "x": "revenue",
                "annotations": [{"type": "rule", "y": 3}],
            }
        )


def test_a_band_must_name_a_category_of_the_x_column() -> None:
    for kind in ("band", "rule"):
        with pytest.raises(
            CompilationError,
            match="'Eest' is not a category of the x column; categories: East, ",
        ):
            _one({"type": kind, "x": "Eest"})


def test_a_date_span_bar_takes_no_annotations() -> None:
    plan = [
        {"task": "a", "start": date(2024, 1, 1), "finish": date(2024, 1, 9)},
        {"task": "b", "start": date(2024, 1, 5), "finish": date(2024, 2, 1)},
    ]
    with pytest.raises(CompilationError, match="date span"):
        _bar(
            [{"type": "rule", "y": 12}],
            rows=plan,
            x="task",
            y="finish",
            y_start="start",
        )


def _label_edge(annotation: dict[str, Any], **chart: Any) -> str:
    return _one(annotation, **chart).style.text.position.edge


def test_a_category_band_label_follows_the_screen_direction() -> None:
    category = {"type": "band", "x": "West"}
    assert _label_edge(category, style={"orientation": "vertical"}) == "top"
    assert _label_edge(category) == "right"


def test_a_measure_band_label_follows_the_screen_direction() -> None:
    measure = {"type": "band", "y": {"from": 8, "to": 12}}
    assert _label_edge(measure, style={"orientation": "vertical"}) == "right"
    assert _label_edge(measure) == "top"


def test_a_note_sits_past_the_value_in_the_screen_direction() -> None:
    note = {"type": "note", "x": "West", "y": 14, "text": "Peak"}
    assert _label_edge(note, style={"orientation": "vertical"}) == "top"
    assert _label_edge(note) == "right"
    assert _one(note).style.text.position.align == "center"


def test_an_authored_note_edge_wins_on_a_horizontal_bar() -> None:
    note = {
        "type": "note",
        "x": "West",
        "y": 14,
        "text": "Peak",
        "style": {"marks": {"text": {"position": {"edge": "top"}}}},
    }
    assert _label_edge(note) == "top"


def test_a_category_bar_note_at_max_y_sits_at_the_max_bar() -> None:
    note = _one({"type": "note", "y": "max()", "text": "{{ value | format }}"})
    assert note.x == ResolvedCategoryAnchor(kind="category", value="West")
    assert _value(note.y) == 14.0
    assert note.text == "14"


def test_a_category_bar_cannot_derive_from_its_category_axis() -> None:
    with pytest.raises(CompilationError, match="max\\(\\) on a category x"):
        _one({"type": "note", "x": "max()"})
