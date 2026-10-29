"""Line, area and scatter annotations resolve against the chart's rows, series and clock."""

from __future__ import annotations

import json
import math
import statistics
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
    user_facing_theme_names,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.chart.resolved._annotation import (
    ResolvedAnnotation,
    ResolvedCategoryAnchor,
    ResolvedRangeAnchor,
    ResolvedValueAnchor,
)
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.chart.resolved.scatter import ResolvedScatterChart
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.theme.annotations import (
    NoteTextMarkStyle,
    TrendLabelStyle,
)
from dbt_charts.core.compile.parse.yaml_error_formatter import (
    format_validation_errors_structured,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.font_measure import get_weighted_font_measurer

NOW = datetime(2024, 6, 14, 9, 30, tzinfo=UTC)

MONTHS = [date(2024, m, 1) for m in range(1, 9)]
REVENUE = [10.0, 12.0, 11.0, 15.0, 14.0, 18.0, 17.0, 21.0]
ROWS: list[dict[str, Any]] = [
    {"month": m, "revenue": r} for m, r in zip(MONTHS, REVENUE, strict=True)
]
SERIES_ROWS: list[dict[str, Any]] = [
    {"month": m, "region": region, "revenue": r * scale}
    for m, r in zip(MONTHS, REVENUE, strict=True)
    for region, scale in (("EMEA", 1.0), ("APAC", 2.0))
]


def _ctx(theme: str | None = None) -> ChartStyleContext:
    return resolve_chart_style_context(
        get_theme_style(theme or get_default_theme_name())
    )


def _line(
    annotations: list[dict[str, Any]],
    rows: list[dict[str, Any]] = ROWS,
    rendered_at: datetime | None = NOW,
    board_style: dict[str, Any] | None = None,
    **chart: Any,
) -> ResolvedLineChart:
    normalized = LineChart.model_validate(
        {
            "id": "rev",
            "type": "line",
            "x": "month",
            "y": "revenue",
            "query_name": "q",
            "annotations": annotations,
            **chart,
        }
    )
    ctx = (
        _ctx()
        if board_style is None
        else resolve_style_and_context(
            get_theme_style(get_default_theme_name()),
            StylePatch.model_validate(board_style),
        )[1]
    )
    resolved = resolve(normalized, rows, ctx, rendered_at=rendered_at)
    assert isinstance(resolved, ResolvedLineChart)
    return resolved


def _one(annotation: dict[str, Any], **kwargs: Any) -> ResolvedAnnotation:
    [item] = _line([annotation], **kwargs).annotations
    return item


def _value(anchor: object) -> object:
    assert isinstance(anchor, ResolvedValueAnchor)
    return anchor.value


def test_literal_anchors_read_in_the_axis_type() -> None:
    rule = _one({"type": "rule", "y": 16, "text": "Goal {{ value | format }}"})
    assert _value(rule.y) == 16.0
    assert rule.x is None
    assert rule.text is not None and rule.text.startswith("Goal ") and "16" in rule.text
    note = _one({"type": "note", "x": date(2024, 4, 1), "y": 15, "text": "Peak"})
    assert _value(note.x) == "2024-04-01"
    band = _one({"type": "band", "x": {"from": "2024-03-01", "to": "2024-05-01"}})
    assert band.x == ResolvedRangeAnchor(
        kind="range", **{"from": "2024-03-01", "to": "2024-05-01"}
    )
    assert (rule.plane, band.plane) == ("foreground", "background")
    assert rule.panel is None and rule.series is None and rule.y_axis == "base"


@pytest.mark.parametrize(
    ("authored", "resolved"),
    [({}, "none"), ({"connector": "line"}, "line"), ({"connector": "arrow"}, "arrow")],
)
def test_a_note_connector_resolves_to_its_type(
    authored: dict[str, Any], resolved: str
) -> None:
    note = _one(
        {"type": "note", "x": date(2024, 4, 1), "y": 15, "text": "Peak", **authored}
    )
    assert note.connector == resolved


def test_payload_dumps_to_the_contract_shape() -> None:
    rule = _one({"type": "rule", "y": 16})
    dumped = rule.model_dump(mode="json")
    assert dumped["y"] == {"kind": "value", "value": 16.0}
    assert ResolvedAnnotation.model_validate(dumped) == rule


def test_aggregates_run_over_the_plotted_rows() -> None:
    assert _value(_one({"type": "rule", "y": "avg()"}).y) == statistics.fmean(REVENUE)
    band = _one({"type": "band", "y": {"from": "percentile(25)", "to": "max()"}})
    assert isinstance(band.y, ResolvedRangeAnchor) and band.y.to == max(REVENUE)
    assert _value(_one({"type": "rule", "x": "max()"}).x) == "2024-08-01"
    with pytest.raises(CompilationError, match="only min\\(\\) and max\\(\\)"):
        _one({"type": "rule", "x": "avg()"})


def test_a_nan_value_is_not_plotted_so_aggregates_skip_it() -> None:
    rows = [*ROWS, {"month": date(2024, 9, 1), "revenue": float("nan")}]
    rule = _one({"type": "rule", "y": "avg()"}, rows=rows)
    assert _value(rule.y) == statistics.fmean(REVENUE)


def test_a_value_beyond_the_data_joins_the_y_domain() -> None:
    plain = _line([]).style.axis_y.tick_values
    widened = _line([{"type": "rule", "y": 60}]).style.axis_y.tick_values
    assert max(plain) < 60 <= max(widened)


def _area(
    annotations: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    theme: str | None = None,
    **chart: Any,
) -> ResolvedAreaChart:
    normalized = AreaChart.model_validate(
        {
            "id": "rev",
            "type": "area",
            "x": "month",
            "query_name": "q",
            "annotations": annotations,
            **chart,
        }
    )
    resolved = resolve(normalized, rows, _ctx(theme), rendered_at=NOW)
    assert isinstance(resolved, ResolvedAreaChart)
    return resolved


# dbt-grays rung 7 on the light themes that set no rule ink of their own; paper
# and stark keep theirs (a cream, and stark's near-black).
RULE_INK = {
    "clarity": "#8A8C8F",
    "vivid": "#8A8C8F",
    "paper": "#6F685D",
    "stark": "#313233",
}


def _area_rule(theme: str, **rule: Any) -> ResolvedAnnotation:
    [item] = _area(
        [{"type": "rule", "y": 15, "text": "Goal", **rule}],
        ROWS,
        theme=theme,
        y="revenue",
    ).annotations
    return item


@pytest.mark.parametrize(("theme", "ink"), RULE_INK.items())
def test_a_rule_and_its_label_wear_the_themes_rule_ink(theme: str, ink: str) -> None:
    rule = _area_rule(theme)
    assert rule.style.rule.stroke.color == ink
    assert rule.style.text.font.color == ink
    assert rule.style.rule.ladder == () and rule.style.rule.avoid == ()


def test_neon_leaves_the_rule_gray_to_the_engine_with_the_ladder_to_pick_from() -> None:
    rule = _area_rule("neon")
    assert rule.style.rule.stroke.color == "auto"
    assert rule.style.text.font.color == "auto"
    # Rungs 3 to 10 of dbt-grays: the pale and near-black ends are never eligible.
    assert rule.style.rule.ladder == (
        "#EDEFF2",
        "#DFE1E5",
        "#D0D3D9",
        "#B0B2B8",
        "#8A8C8F",
        "#626366",
        "#494A4C",
        "#313233",
    )
    assert rule.style.rule.avoid == ("#494A4C", "#626366")


@pytest.mark.parametrize("theme", ["neon", "clarity"])
def test_a_rule_that_sets_its_own_color_is_not_picked_and_its_label_follows(
    theme: str,
) -> None:
    rule = _area_rule(
        theme, style={"marks": {"rule": {"stroke": {"color": "#123456"}}}}
    )
    assert rule.style.rule.stroke.color == "#123456"
    assert rule.style.text.font.color == "#123456"
    assert rule.style.rule.ladder == ()


def test_a_series_rule_keeps_its_series_color_on_neon() -> None:
    rules = _area(
        [{"type": "rule", "y": "avg()", "series": "each", "text": "{{ series }}"}],
        SERIES_ROWS,
        theme="neon",
        y="revenue",
        color="region",
        stack="none",
    ).annotations
    assert len(rules) == 2
    for rule in rules:
        assert rule.style.rule.stroke.color != "auto"
        assert rule.style.text.font.color == rule.style.rule.stroke.color
        assert rule.style.rule.ladder == ()


def test_a_multi_metric_area_widens_for_an_annotation() -> None:
    rows = [{**row, "cost": row["revenue"] / 2} for row in ROWS]
    widened = _area(
        [{"type": "rule", "y": 60}], rows, y=["revenue", "cost"]
    ).style.axis_y.tick_values
    assert max(widened) >= 60


def test_a_log_area_widens_for_an_annotation() -> None:
    log = {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
    resolved = _area([{"type": "rule", "y": 60}], ROWS, y="revenue", style=log)
    scale = resolved.style.axis_y.scale
    assert scale is not None and scale.continuous is not None
    domain = scale.continuous.domain
    assert domain is not None and domain[1] >= 60


def test_a_rule_sits_at_a_category_x() -> None:
    rows = [{"region": r, "revenue": v} for r, v in (("East", 3.0), ("West", 5.0))]
    east = ResolvedCategoryAnchor(kind="category", value="East")
    assert _one({"type": "band", "x": "East"}, rows=rows, x="region").x == east
    assert _one({"type": "rule", "x": "East"}, rows=rows, x="region").x == east
    with pytest.raises(CompilationError, match="category x"):
        _one(
            {"type": "rule", "y": "avg()", "x": {"from": "min()"}},
            rows=rows,
            x="region",
        )


def test_series_each_expands_in_series_color_and_an_author_color_wins() -> None:
    rules = _line(
        [{"type": "rule", "y": "avg()", "series": "each", "text": "{{ series }}"}],
        rows=SERIES_ROWS,
        color="region",
    ).annotations
    assert [r.series for r in rules] == ["APAC", "EMEA"]
    assert [r.text for r in rules] == ["APAC", "EMEA"]
    assert rules[0].y == ResolvedValueAnchor(
        kind="value", value=2 * statistics.fmean(REVENUE)
    )
    assert rules[0].style.rule.stroke.color != rules[1].style.rule.stroke.color
    assert rules[0].style.text.box.fill == rules[1].style.text.box.fill
    [pinned] = _line(
        [
            {
                "type": "rule",
                "y": "avg()",
                "series": ["EMEA"],
                "style": {"marks": {"rule": {"stroke": {"color": "#123456"}}}},
            }
        ],
        rows=SERIES_ROWS,
        color="region",
    ).annotations
    assert pinned.style.rule.stroke.color == "#123456"


def test_a_note_s_box_stays_chrome_under_a_series_color() -> None:
    notes = _line(
        [
            {
                "type": "note",
                "x": "max()",
                "y": "max()",
                "series": "each",
                "enclose": "rect",
                "text": "{{ series }}",
            }
        ],
        rows=SERIES_ROWS,
        color="region",
    ).annotations
    assert notes[0].style.text.font.color != notes[1].style.text.font.color
    assert notes[0].style.rect.fill == notes[1].style.rect.fill


def _boxed_note(**extra: object) -> list[dict[str, object]]:
    return [
        {
            "type": "note",
            "x": "max()",
            "y": "max()",
            "enclose": "rect",
            "text": "peak",
            "style": {"marks": {"text": {"position": {"edge": "top"}}}},
            **extra,
        }
    ]


def test_a_centered_box_rejects_a_text_position() -> None:
    with pytest.raises(ValidationError, match="marks.text.position"):
        _line(_boxed_note())


def test_a_connected_box_takes_a_text_position() -> None:
    notes = _line(_boxed_note(connector="line")).annotations
    assert notes[0].style.text.position.edge == "top"


def test_a_note_style_without_text_passes_the_text_checks() -> None:
    style = {"marks": {"line": {"opacity": 0.5}}}
    assert _line(_boxed_note(style=style)).annotations
    point = {"type": "note", "x": "2024-03-01", "y": 11, "text": "Dip", "style": style}
    assert _one(point)


_ALL_MARKS = ("rule", "rect", "line", "area", "circle", "text")


def test_a_resolved_style_carries_only_the_marks_its_type_draws() -> None:
    drawn = {
        "rule": {"rule", "text"},
        "band": {"rect", "text"},
        "note": {"text", "line", "circle", "rect"},
        "trend": {"line", "area", "text"},
    }
    annotations = _line(
        [
            {"type": "rule", "y": 60},
            {"type": "band", "y": {"from": 40, "to": 80}},
            {"type": "note", "x": "max()", "y": "max()", "text": "peak"},
            {"type": "trend", "fit": "linear()", "interval": "confidence(95)"},
        ]
    ).annotations
    for annotation in annotations:
        style = annotation.style
        present = {name for name in _ALL_MARKS if getattr(style, name) is not None}
        assert present == drawn[annotation.type]
    assert annotations[1].style.rect.padding is None
    assert annotations[2].style.rect.padding is not None


def test_series_total_pools_every_row_when_nothing_stacks() -> None:
    peak, mean, trend = _line(
        [
            {"type": "rule", "y": "max()", "series": "total"},
            {"type": "rule", "y": "avg()", "series": "total"},
            {"type": "trend", "fit": "linear()", "series": "total"},
        ],
        rows=SERIES_ROWS,
        color="region",
    ).annotations
    assert peak.series is None
    assert _value(peak.y) == 2 * max(REVENUE)
    assert _value(mean.y) == pytest.approx(1.5 * statistics.fmean(REVENUE))
    # Two series scaled 1x and 2x pool to the same OLS line as their 1.5x mean.
    [single] = _line([{"type": "trend", "fit": "linear()"}], rows=ROWS).annotations
    assert trend.trend is not None and single.trend is not None
    assert [y for _, y in trend.trend.line] == pytest.approx(
        [1.5 * y for _, y in single.trend.line]
    )


def test_series_total_on_a_stacked_area_is_the_per_x_sum() -> None:
    chart = AreaChart.model_validate(
        {
            "id": "rev",
            "type": "area",
            "x": "month",
            "y": "revenue",
            "color": "region",
            "stack": "zero",
            "query_name": "q",
            "annotations": [{"type": "rule", "y": "max()", "series": "total"}],
        }
    )
    resolved = resolve(chart, SERIES_ROWS, _ctx(), rendered_at=NOW)
    assert isinstance(resolved, ResolvedAreaChart)
    [total] = resolved.annotations
    assert _value(total.y) == 3 * max(REVENUE)


@pytest.mark.parametrize(
    ("annotation", "match"),
    [
        ({"type": "rule", "y": "avg()"}, "set series"),
        ({"type": "rule", "y": "avg()", "series": ["LATAM"]}, "'LATAM' not in"),
        ({"type": "rule", "y": 5, "series": "each"}, "literal"),
    ],
)
def test_series_mistakes_are_errors(annotation: dict[str, Any], match: str) -> None:
    with pytest.raises(CompilationError, match=match):
        _line([annotation], rows=SERIES_ROWS, color="region")


def test_a_stacked_area_takes_only_the_total() -> None:
    normalized = AreaChart.model_validate(
        {
            "id": "rev",
            "type": "area",
            "x": "month",
            "y": "revenue",
            "color": "region",
            "stack": "zero",
            "query_name": "q",
            "annotations": [{"type": "rule", "y": "avg()", "series": "each"}],
        }
    )
    with pytest.raises(CompilationError, match="only series: total"):
        resolve(normalized, SERIES_ROWS, _ctx(), rendered_at=NOW)
    total = normalized.model_copy(
        update={
            "annotations": AreaChart.model_validate(
                {
                    "id": "rev",
                    "type": "area",
                    "query_name": "q",
                    "annotations": [{"type": "rule", "y": "avg()", "series": "total"}],
                }
            ).annotations
        }
    )
    resolved = resolve(total, SERIES_ROWS, _ctx(), rendered_at=NOW)
    assert isinstance(resolved, ResolvedAreaChart)
    assert len(resolved.annotations) == 1


def test_clock_anchors_read_the_render_time() -> None:
    assert _value(_one({"type": "rule", "x": "now()"}).x) == "2024-06-14T09:30:00"
    month = _one({"type": "band", "x": "current(month)"})
    assert month.x == ResolvedRangeAnchor(
        kind="range", **{"from": "2024-06-01", "to": "2024-07-01"}
    )
    forecast = _one({"type": "band", "x": {"from": "now()"}})
    assert isinstance(forecast.x, ResolvedRangeAnchor) and forecast.x.to is None
    with pytest.raises(CompilationError, match="is a period"):
        _one({"type": "rule", "x": "current(week)"})


def test_a_clock_period_outside_the_data_still_resolves() -> None:
    resolved = _line(
        [{"type": "rule", "y": 5}, {"type": "band", "x": "current(year)"}],
        rendered_at=datetime(2031, 2, 1, tzinfo=UTC),
    )
    assert [a.index for a in resolved.annotations] == [0, 1]


def test_a_trend_samples_its_fit_and_interval() -> None:
    trend = _one({"type": "trend", "fit": "linear()", "interval": "confidence(95)"})
    assert trend.trend is not None
    assert [x for x, _ in trend.trend.line] == [m.isoformat() for m in MONTHS]
    assert trend.trend.interval is not None
    for (_, y), (_, low, high) in zip(
        trend.trend.line, trend.trend.interval, strict=True
    ):
        assert low < y < high
    extended = _one({"type": "trend", "fit": "linear()", "x": {"to": "2024-12-01"}})
    assert extended.trend is not None
    assert len(extended.trend.line) == 64
    assert extended.trend.line[-1][0] == "2024-12-01"
    with pytest.raises(CompilationError, match="above zero"):
        _one(
            {"type": "trend", "fit": "exponential()"},
            rows=[*ROWS, {"month": date(2024, 9, 1), "revenue": -1}],
        )


def test_multiples_compute_per_panel_and_literals_draw_on_every_panel() -> None:
    annotations = _line(
        [{"type": "rule", "y": "max()"}, {"type": "rule", "y": 3}],
        rows=SERIES_ROWS,
        multiples={"columns": "region"},
    ).annotations
    computed = [a for a in annotations if a.index == 0]
    assert {a.panel: _value(a.y) for a in computed} == {
        json.dumps(["APAC"]): 2 * max(REVENUE),
        json.dumps(["EMEA"]): max(REVENUE),
    }
    [literal] = [a for a in annotations if a.index == 1]
    assert literal.panel is None


def test_series_each_skips_a_series_a_panel_has_no_rows_for() -> None:
    rows = [
        {**row, "market": "retail"} for row in SERIES_ROWS if row["region"] == "EMEA"
    ] + [{**row, "market": "online"} for row in SERIES_ROWS]
    rules = _line(
        [{"type": "rule", "y": "max()", "series": "each"}],
        rows=rows,
        color="region",
        multiples={"columns": "market"},
    ).annotations
    assert sorted((r.panel, r.series) for r in rules) == [
        (json.dumps(["online"]), "APAC"),
        (json.dumps(["online"]), "EMEA"),
        (json.dumps(["retail"]), "EMEA"),
    ]


def test_a_frame_note_sits_where_the_theme_pins_frame_notes() -> None:
    frame = _one({"type": "note", "text": "Dashed is forecast"})
    position = _ctx().cascaded.annotations.note.frame.text.position
    assert (frame.style.text.position.edge, frame.style.text.position.align) == (
        position.edge,
        position.align,
    )
    assert frame.x is None and frame.y is None


def _auto_measure(item: ResolvedAnnotation) -> float:
    """28 glyph widths of "n" in the annotation's own font: what `auto` bakes to."""
    font = item.style.text.font
    measurer = get_weighted_font_measurer(", ".join(font.family), font.weight)
    return measurer.measure("n" * 28, font.size)


def test_only_a_frame_note_carries_the_wrap_measure() -> None:
    frame = _one({"type": "note", "text": "Dashed = forecast"})
    assert frame.style.text.max_width == _auto_measure(frame)
    assert frame.style.text.font.line_height == 1.25
    point = _one({"type": "note", "x": "2024-03-01", "y": 11, "text": "Dip"})
    rule = _one({"type": "rule", "y": 16, "text": "Goal"})
    derived = _one({"type": "note", "y": "max()", "text": "Peak"})
    assert point.style.text.max_width is None and rule.style.text.max_width is None
    assert derived.style.text.max_width is None
    assert derived.style.text.position != frame.style.text.position


def test_a_trend_wraps_to_the_theme_measure_and_share() -> None:
    trend = _one({"type": "trend", "fit": "linear()", "text": "Long trend label"})
    assert trend.style.text.max_width == _auto_measure(trend)
    assert trend.style.text.max_share == 0.3


def _wrapped(annotation: dict[str, Any], width: float | str) -> ResolvedAnnotation:
    return _one({**annotation, "style": {"marks": {"text": {"max_width": width}}}})


def test_the_trend_share_is_a_fraction_of_the_plot() -> None:
    for bad in (0, -0.1, 1.5):
        with pytest.raises(ValidationError):
            TrendLabelStyle(max_share=bad)


def test_the_wrap_measure_is_auto_or_a_positive_width() -> None:
    for good in ("auto", 240.0):
        assert NoteTextMarkStyle(max_width=good).max_width == good
    for bad in ("wide", 0, -5):
        with pytest.raises(ValidationError):
            NoteTextMarkStyle(max_width=bad)


def test_an_authored_width_wins_for_a_frame_note() -> None:
    frame = {"type": "note", "text": "Dashed = forecast"}
    assert _wrapped(frame, 120).style.text.max_width == 120
    auto = _wrapped(frame, "auto")
    assert auto.style.text.max_width == _auto_measure(auto)


def test_an_authored_width_wins_for_a_trend() -> None:
    trend = _wrapped({"type": "trend", "fit": "linear()", "text": "Long"}, 90)
    assert trend.style.text.max_width == 90
    assert trend.style.text.max_share == 0.3
    auto = _wrapped({"type": "trend", "fit": "linear()", "text": "Long"}, "auto")
    assert auto.style.text.max_width == _auto_measure(auto)


def test_a_point_note_rejects_a_wrap_width() -> None:
    with pytest.raises(ValidationError, match="max_width"):
        _wrapped({"type": "note", "x": "2024-03-01", "y": 11, "text": "Dip"}, 120)


def test_shape_errors() -> None:
    with pytest.raises(CompilationError, match="is past to"):
        _one({"type": "band", "x": {"from": "2024-05-01", "to": "2024-02-01"}})
    with pytest.raises(CompilationError, match="takes a range"):
        _one({"type": "band", "y": 4})
    with pytest.raises(CompilationError, match="not a date"):
        _one({"type": "rule", "x": "July"})


def test_a_resolve_with_no_clock_draws_no_annotations() -> None:
    assert _line([{"type": "rule", "y": 5}], rendered_at=None).annotations == ()


SPEND = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
SIGNUPS = [12.0, 18.0, 33.0, 38.0, 52.0, 57.0]
SCATTER_ROWS: list[dict[str, Any]] = [
    {"spend": s, "signups": v} for s, v in zip(SPEND, SIGNUPS, strict=True)
]


def _scatter(
    annotations: list[dict[str, Any]],
    rows: list[dict[str, Any]] = SCATTER_ROWS,
    rendered_at: datetime | None = NOW,
    **chart: Any,
) -> ResolvedScatterChart:
    normalized = ScatterChart.model_validate(
        {
            "id": "cac",
            "type": "scatter",
            "x": "spend",
            "y": "signups",
            "query_name": "q",
            "annotations": annotations,
            **chart,
        }
    )
    resolved = resolve(normalized, rows, _ctx(), rendered_at=rendered_at)
    assert isinstance(resolved, ResolvedScatterChart)
    return resolved


def test_scatter_anchors_read_in_the_numeric_x() -> None:
    [rule, band, trend] = _scatter(
        [
            {"type": "rule", "x": 35},
            {"type": "band", "x": {"from": 20, "to": "max()"}},
            {"type": "trend", "fit": "linear()", "interval": "confidence(95)"},
        ]
    ).annotations
    assert _value(rule.x) == 35.0
    assert band.x == ResolvedRangeAnchor(kind="range", **{"from": 20.0, "to": 60.0})
    assert trend.trend is not None
    assert [x for x, _ in trend.trend.line] == SPEND
    assert trend.trend.interval is not None


def test_scatter_aggregates_run_over_the_plotted_rows() -> None:
    chart = _scatter([{"type": "rule", "y": "avg()"}])
    assert _value(chart.annotations[0].y) == statistics.fmean(SIGNUPS)


def test_a_scatter_value_beyond_the_data_joins_the_y_domain() -> None:
    plain = _scatter([]).style.axis_y.tick_values
    widened = _scatter([{"type": "rule", "y": 200}]).style.axis_y.tick_values
    assert max(plain) < 200 <= max(widened)


def test_scatter_multiples_compute_per_panel() -> None:
    rows = [{**r, "region": g} for g in ("EMEA", "APAC") for r in SCATTER_ROWS]
    annotations = _scatter(
        [{"type": "rule", "y": "max()"}], rows=rows, multiples={"columns": "region"}
    ).annotations
    assert {a.panel for a in annotations} == {
        json.dumps(["APAC"]),
        json.dumps(["EMEA"]),
    }


REGION_ROWS: list[dict[str, Any]] = [
    {**r, "region": g} for r, g in zip(SCATTER_ROWS, "AABBCC", strict=True)
]


def test_a_dot_plot_band_names_one_category_on_either_axis() -> None:
    [on_x] = _scatter(
        [{"type": "band", "x": "B"}], rows=REGION_ROWS, x="region"
    ).annotations
    [on_y] = _scatter(
        [{"type": "band", "y": "B"}], rows=REGION_ROWS, y="region"
    ).annotations
    assert on_x.x == ResolvedCategoryAnchor(kind="category", value="B")
    assert on_y.y == ResolvedCategoryAnchor(kind="category", value="B")


def test_a_dot_plot_computes_over_its_value_axis() -> None:
    by_x = _scatter(
        [{"type": "rule", "y": "avg()"}], rows=REGION_ROWS, x="region"
    ).annotations[0]
    by_y = _scatter(
        [{"type": "rule", "x": "avg()"}, {"type": "band", "x": {"from": 15, "to": 45}}],
        rows=REGION_ROWS,
        y="region",
    )
    assert _value(by_x.y) == statistics.fmean(SIGNUPS)
    assert _value(by_y.annotations[0].x) == statistics.fmean(SPEND)
    assert by_y.annotations[1].x == ResolvedRangeAnchor(
        kind="range", **{"from": 15.0, "to": 45.0}
    )


def test_a_dot_plot_rule_or_note_sits_at_a_category() -> None:
    b = ResolvedCategoryAnchor(kind="category", value="B")
    rule, note = _scatter(
        [
            {"type": "rule", "y": "B"},
            {"type": "note", "x": 20, "y": "B", "text": "n"},
        ],
        rows=REGION_ROWS,
        y="region",
    ).annotations
    assert rule.y == b and note.y == b and _value(note.x) == 20.0


@pytest.mark.parametrize(
    ("chart", "annotation", "reason"),
    [
        (
            {"y": "region"},
            {"type": "band", "y": {"from": "A", "to": "B"}},
            "range on a category y",
        ),
        ({"y": "region"}, {"type": "rule", "y": "avg()"}, "category y"),
        ({"y": "region"}, {"type": "rule", "y": "Z"}, "'Z' is not a category"),
        ({"x": "region"}, {"type": "trend", "fit": "linear()"}, "category x"),
        ({"y": "region"}, {"type": "trend", "fit": "linear()"}, "category y"),
    ],
)
def test_a_dot_plot_refuses_values_off_its_categories(
    chart: dict[str, Any], annotation: dict[str, Any], reason: str
) -> None:
    with pytest.raises(CompilationError, match=reason):
        _scatter([annotation], rows=REGION_ROWS, **chart)


def test_a_dot_plot_on_a_date_x_computes_over_its_dates() -> None:
    rows = [{**r, "day": m} for r, m in zip(REGION_ROWS, MONTHS, strict=False)]
    [rule] = _scatter(
        [{"type": "rule", "x": "max()"}], rows=rows, x="day", y="region"
    ).annotations
    assert _value(rule.x) == MONTHS[5].isoformat()


def test_a_colored_dot_plot_computes_over_every_dot() -> None:
    rows = [{**r, "year": y} for r in REGION_ROWS for y in ("2023", "2024")]
    [rule] = _scatter(
        [{"type": "rule", "x": "avg()", "series": "total"}],
        rows=rows,
        y="region",
        color="year",
    ).annotations
    assert _value(rule.x) == statistics.fmean(SPEND)


def test_a_dot_plot_y_annotation_adds_nothing_to_a_category_domain() -> None:
    plain = _scatter([], rows=REGION_ROWS, y="region").style.axis_x
    banded = _scatter([{"type": "band", "y": "B"}], rows=REGION_ROWS, y="region")
    assert banded.style.axis_x == plain


@pytest.mark.parametrize("axis", ["axis_x", "axis_y"])
@pytest.mark.parametrize("scale", ["sqrt", "pow", "symlog"])
def test_a_scatter_on_a_bent_axis_resolves_any_sign(axis: str, scale: str) -> None:
    style = {axis: {"scale": {"continuous": {"type": scale}}}}
    chart = _scatter(
        [{"type": "rule", "y": -5}, {"type": "rule", "x": -5}], style=style
    )
    assert [_value(chart.annotations[0].y), _value(chart.annotations[1].x)] == [
        -5.0,
        -5.0,
    ]
    assert min(chart.style.axis_y.tick_values) <= -5


def test_a_scatter_with_a_log_y_refuses_a_non_positive_annotation_value() -> None:
    style = {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
    with pytest.raises(CompilationError, match="positive"):
        _scatter([{"type": "rule", "y": 0}], style=style)


def test_a_scatter_resolve_with_no_clock_draws_no_annotations() -> None:
    assert _scatter([{"type": "rule", "y": 5}], rendered_at=None).annotations == ()


def test_value_format_reads_the_axis_labels_format() -> None:
    rows = [{"month": r["month"], "revenue": r["revenue"] * 1.37} for r in ROWS]
    rule = {"type": "rule", "y": "avg()", "text": "{{ value | format }}"}
    [auto] = _line([rule], rows=rows).annotations
    assert auto.text == "20"
    style = {"style": {"axis_y": {"labels": {"format": "$,.1f"}}}}
    [explicit] = _line([rule], rows=rows, **style).annotations
    assert explicit.text == "$20.2"


@pytest.mark.parametrize(
    "annotation",
    [
        {"type": "rule", "y": 25, "text": "{{ series }}"},
        {"type": "rule", "y": "avg()", "series": "each"},
        {"type": "rule", "y": "avg()", "series": "total"},
        {"type": "rule", "y": "avg()", "series": ["A"]},
    ],
)
def test_series_needs_a_color_channel(annotation: dict[str, Any]) -> None:
    with pytest.raises(CompilationError, match="chart with color"):
        _line([annotation])


def _validate_messages(annotation: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        AuthoredBoard.model_validate(
            {
                "title": "t",
                "charts": {
                    "c": {
                        "type": "line",
                        "query": "q",
                        "x": "month",
                        "y": "revenue",
                        "annotations": [annotation],
                    }
                },
                "rows": ["c"],
            }
        )
    diagnostics = format_validation_errors_structured(caught.value)
    return "\n".join(f"{d.message} {d.hint or ''}" for d in diagnostics)


def test_a_trend_y_hint_says_a_trend_takes_no_y() -> None:
    text = _validate_messages({"type": "trend", "fit": "linear()", "y": 5})
    assert "A trend takes no `y`" in text
    assert "belongs at" not in text


def test_an_empty_note_text_is_an_error() -> None:
    text = _validate_messages({"type": "note", "x": "2024-06-01", "y": 20, "text": ""})
    assert "text" in text and "at least 1 character" in text


def test_a_whitespace_only_note_text_is_an_error() -> None:
    text = _validate_messages(
        {"type": "note", "x": "2024-06-01", "y": 20, "text": "  "}
    )
    assert "text" in text and "should match pattern" in text


@pytest.mark.parametrize("field", ["opacity", "length", "radius", "padding"])
def test_negative_or_oversized_style_values_are_errors(field: str) -> None:
    mark = {"opacity": "rect", "length": "line", "radius": "circle", "padding": "rect"}
    value = 2 if field == "opacity" else -1
    text = _validate_messages(
        {
            "type": "note",
            "x": "2024-06-01",
            "y": 20,
            "style": {"marks": {mark[field]: {field: value}}},
        }
    )
    assert "should be" in text


def test_series_text_error_has_one_prefix() -> None:
    with pytest.raises(CompilationError) as caught:
        _line([{"type": "rule", "y": 25, "text": "{{ series }}"}])
    assert str(caught.value).count("text '{{ series }}'") == 1


def test_a_bad_timestamp_anchor_names_the_timestamp_format() -> None:
    with pytest.raises(CompilationError, match=r"YYYY-MM-DDTHH:MM"):
        _line([{"type": "rule", "x": "2024-06-01T25:00:00"}])


def test_x_value_format_reads_the_x_axis_format() -> None:
    [rule] = _scatter(
        [{"type": "rule", "x": "avg()", "text": "x {{ value | format }}"}],
        rows=[{"spend": s, "signups": 1.0} for s in (1.0, 2.0, 4.5)],
    ).annotations
    assert rule.text == "x 2.5"
    [explicit] = _scatter(
        [{"type": "rule", "x": "avg()", "text": "x {{ value | format }}"}],
        rows=[{"spend": s, "signups": 1.0} for s in (1.0, 2.0, 4.5)],
        style={"axis_x": {"labels": {"format": "$,.1f"}}},
    ).annotations
    assert explicit.text == "x $2.5"


def test_a_bad_anchor_function_reports_only_itself() -> None:
    text = _validate_messages({"type": "rule", "y": "mean()"})
    assert "'mean()' is not an anchor function" in text
    assert "expects a mapping" not in text


def test_a_note_range_reports_one_error() -> None:
    text = _validate_messages({"type": "note", "x": {"from": "2024-01-01"}, "y": 20})
    assert "a note takes a single value" in text
    assert "valid number" not in text and "valid datetime" not in text


def test_theme_defaults_bake_radius_and_dash_and_leave_a_rule_solid() -> None:
    chart = _line(
        [
            {"type": "rule", "y": 60, "text": "goal"},
            {
                "type": "note",
                "x": "max()",
                "y": "max()",
                "text": "peak",
                "enclose": "circle",
            },
            {"type": "trend", "fit": "linear()"},
        ]
    )
    rule, note, trend = chart.annotations
    assert rule.style.rule is not None and rule.style.rule.stroke.dasharray is None
    size = chart.style.point_mark.size
    assert size is not None and size > 0
    assert note.style.circle is not None
    assert note.style.circle.radius == pytest.approx(math.sqrt(size) / 2 + 4)
    assert trend.style.line is not None
    width = trend.style.line.stroke.width
    dash, gap = (max(1, math.floor(k * width + 0.5)) for k in (3, 2.5))
    assert trend.style.line.stroke.dasharray == f"{dash} {gap}"
    for annotation in (rule, note, trend):
        assert annotation.style.text.halo_opacity == 0.75


@pytest.mark.parametrize(("width", "dash"), [(1.5, "5 4"), (2.0, "6 5"), (0.1, "1 1")])
def test_an_auto_dash_is_three_widths_then_two_and_a_half(
    width: float, dash: str
) -> None:
    rule = _one(
        {
            "type": "rule",
            "y": 60,
            "style": {
                "marks": {"rule": {"stroke": {"width": width, "dasharray": "auto"}}}
            },
        }
    )
    assert rule.style.rule is not None and rule.style.rule.stroke.dasharray == dash


def _circle_radius(**chart: Any) -> float:
    note = {"type": "note", "x": "max()", "y": "max()", "enclose": "circle"}
    circle = _one(note, **chart).style.circle
    assert circle is not None and isinstance(circle.radius, float)
    return circle.radius


def test_an_auto_circle_clears_its_visible_point_by_the_text_gap() -> None:
    assert _circle_radius(style={"marks": {"point": {"size": 0}}}) == 6.0
    assert _circle_radius(style={"marks": {"point": {"size": 100}}}) == 9.0
    assert _circle_radius(style={"marks": {"point": {"size": 4}}}) == 6.0


def test_authored_radius_dash_and_halo_opacity_win_over_auto() -> None:
    rule, note, trend = _line(
        [
            {
                "type": "rule",
                "y": 60,
                "style": {"marks": {"rule": {"stroke": {"dasharray": "4 3"}}}},
            },
            {
                "type": "note",
                "x": "max()",
                "y": "max()",
                "enclose": "circle",
                "style": {
                    "marks": {"circle": {"radius": 11}, "text": {"halo_opacity": 0.4}}
                },
            },
            {
                "type": "trend",
                "fit": "linear()",
                "style": {"marks": {"line": {"stroke": {"dasharray": "2 2"}}}},
            },
        ]
    ).annotations
    assert rule.style.rule is not None and rule.style.rule.stroke.dasharray == "4 3"
    assert note.style.circle is not None and note.style.circle.radius == 11
    assert note.style.text.halo_opacity == 0.4
    assert trend.style.line is not None and trend.style.line.stroke.dasharray == "2 2"


@pytest.mark.parametrize("opacity", [-0.1, 1.5])
def test_halo_opacity_outside_zero_to_one_is_an_error(opacity: float) -> None:
    text = _validate_messages(
        {
            "type": "rule",
            "y": 5,
            "style": {"marks": {"text": {"halo_opacity": opacity}}},
        }
    )
    assert "should be" in text


def test_an_unknown_radius_keyword_is_an_error() -> None:
    text = _validate_messages(
        {
            "type": "note",
            "x": "2024-06-01",
            "y": 20,
            "style": {"marks": {"circle": {"radius": "big"}}},
        }
    )
    assert "radius" in text


def test_a_scatter_circle_clears_the_ring_of_an_unfilled_point() -> None:
    point = {"size": 100, "filled": False, "stroke_width": 2}
    [note] = _scatter(
        [{"type": "note", "x": "max()", "y": "max()", "enclose": "circle"}],
        style={"marks": {"point": point}},
    ).annotations
    assert note.style.circle is not None and note.style.circle.radius == 11.0


def _trend_colors(item: ResolvedAnnotation) -> tuple[str, str, str]:
    assert item.style.line is not None and item.style.area is not None
    return (
        item.style.line.stroke.color,
        item.style.area.fill,
        item.style.text.font.color,
    )


def test_a_trend_wears_its_series_color_line_interval_and_label() -> None:
    palette = _ctx().palette
    trends = _line(
        [
            {
                "type": "trend",
                "fit": "linear()",
                "series": "each",
                "interval": "confidence(95)",
            }
        ],
        rows=SERIES_ROWS,
        color="region",
    ).annotations
    assert [t.series for t in trends] == ["APAC", "EMEA"]
    assert [_trend_colors(t) for t in trends] == [(c,) * 3 for c in palette[:2]]
    [listed] = _line(
        [{"type": "trend", "fit": "linear()", "series": ["EMEA"]}],
        rows=SERIES_ROWS,
        color="region",
    ).annotations
    assert _trend_colors(listed) == (palette[1],) * 3


def test_a_trend_over_combined_series_is_the_muted_gray() -> None:
    muted = "#626366"  # clarity's secondary ink, its `muted` role
    [total] = _line(
        [{"type": "trend", "fit": "linear()", "series": "total"}],
        rows=SERIES_ROWS,
        color="region",
    ).annotations
    assert _trend_colors(total) == (muted,) * 3


def test_a_trend_on_an_uncolored_chart_wears_the_chart_s_single_series_ink() -> None:
    chart = _line([{"type": "trend", "fit": "linear()"}])
    [trend] = chart.annotations
    assert _trend_colors(trend) == (chart.style.single_series_fill,) * 3
    scatter = _scatter([{"type": "trend", "fit": "linear()"}])
    assert (
        _trend_colors(scatter.annotations[0]) == (scatter.style.single_series_fill,) * 3
    )


def test_an_authored_trend_color_beats_the_series_color() -> None:
    [pinned] = _line(
        [
            {
                "type": "trend",
                "fit": "linear()",
                "series": ["EMEA"],
                "style": {"marks": {"line": {"stroke": {"color": "#123456"}}}},
            }
        ],
        rows=SERIES_ROWS,
        color="region",
    ).annotations
    assert pinned.style.line is not None
    assert pinned.style.line.stroke.color == "#123456"


def test_a_trend_label_follows_an_authored_line_color_unless_its_own_is_set() -> None:
    def label_and_line(marks: dict[str, Any]) -> tuple[str, str]:
        [trend] = _line(
            [{"type": "trend", "fit": "linear()", "style": {"marks": marks}}]
        ).annotations
        assert trend.style.line is not None
        return trend.style.text.font.color, trend.style.line.stroke.color

    line = {"stroke": {"color": "#123456"}}
    assert label_and_line({"line": line}) == ("#123456", "#123456")
    own = {"font": {"color": "#654321"}}
    assert label_and_line({"line": line, "text": own}) == ("#654321", "#123456")


def _every_type() -> list[dict[str, Any]]:
    return [
        {"type": "rule", "y": 16, "text": "Goal"},
        {"type": "band", "x": {"from": "2024-03-01", "to": "2024-05-01"}},
        {"type": "note", "x": date(2024, 4, 1), "y": 15, "text": "Peak"},
        {"type": "trend", "fit": "linear()", "text": "Trend"},
    ]


def test_rules_and_trends_default_over_the_data_and_bands_under_it() -> None:
    annotations = _every_type()
    line = {a.type: a.plane for a in _line(annotations).annotations}
    # An area takes no trend; a trend over a scatter layer still sits above the fill.
    area_annotations = [a for a in annotations if a["type"] != "trend"]
    area = {
        a.type: a.plane for a in _area(area_annotations, ROWS, y="revenue").annotations
    }
    assert line == {
        "rule": "foreground",
        "band": "background",
        "note": "foreground",
        "trend": "foreground",
    }
    assert area == {
        "rule": "foreground",
        "band": "background",
        "note": "foreground",
    }


def test_an_authored_plane_wins_over_the_default() -> None:
    over = _one({"type": "rule", "y": 16, "plane": "foreground"})
    assert over.plane == "foreground"
    [under] = _area(
        [{"type": "rule", "y": 16, "plane": "background"}], ROWS, y="revenue"
    ).annotations
    assert under.plane == "background"


@pytest.mark.parametrize("theme", user_facing_theme_names())
def test_annotation_text_halo_is_three_pixels_over_marks_only_by_default(
    theme: str,
) -> None:
    ctx = resolve_chart_style_context(get_theme_style(theme))
    normalized = LineChart.model_validate(
        {
            "id": "rev",
            "type": "line",
            "x": "month",
            "y": "revenue",
            "query_name": "q",
            "annotations": _every_type(),
        }
    )
    resolved = resolve(normalized, ROWS, ctx, rendered_at=NOW)
    assert isinstance(resolved, ResolvedLineChart)
    halos = [
        (a.style.text.halo, a.style.text.halo_over_marks) for a in resolved.annotations
    ]
    assert halos == [(3.0, True)] * 4


def test_an_authored_halo_width_is_kept_and_drawn_everywhere() -> None:
    rule = _one(
        {
            "type": "rule",
            "y": 16,
            "text": "Goal",
            "style": {"marks": {"text": {"halo": 3}}},
        }
    )
    assert rule.style.text.halo == 3
    assert not rule.style.text.halo_over_marks


def _label_at(band: ResolvedAnnotation) -> tuple[str, str, bool]:
    position = band.style.text.position
    return position.edge, position.align, position.overlay


def test_an_x_band_label_sits_top_center() -> None:
    band = _one({"type": "band", "x": {"from": "2024-03-01", "to": "2024-05-01"}})
    assert _label_at(band) == ("top", "center", True)


def test_a_y_band_label_sits_right_center() -> None:
    band = _one({"type": "band", "y": {"from": 11, "to": 14}})
    assert _label_at(band) == ("right", "center", True)


def test_a_rectangle_band_label_sits_top_center() -> None:
    band = _one(
        {
            "type": "band",
            "x": {"from": "2024-03-01", "to": "2024-05-01"},
            "y": {"from": 11, "to": 14},
        }
    )
    assert _label_at(band) == ("top", "center", True)


def test_an_authored_band_label_position_wins() -> None:
    position = {"edge": "bottom", "align": "start", "overlay": False}
    band = _one(
        {
            "type": "band",
            "y": {"from": 11, "to": 14},
            "style": {"marks": {"text": {"position": position}}},
        }
    )
    assert _label_at(band) == ("bottom", "start", False)


def _free(a: ResolvedAnnotation) -> tuple[str, str, bool, bool]:
    p = a.style.text.position
    return p.edge, p.align, p.auto_edge, p.auto_align


def test_auto_keys_take_their_preferred_spot_and_stay_free() -> None:
    note = _one({"type": "note", "x": "2024-03-01", "y": 12, "text": "Peak"})
    band = _one({"type": "band", "y": {"from": 11, "to": 14}})
    rule = _one({"type": "rule", "y": 16, "text": "Goal"})
    assert [_free(a) for a in (note, band, rule)] == [
        ("top", "center", True, True),
        ("right", "center", True, True),
        ("top", "end", True, True),
    ]


def test_an_authored_key_stays_fixed_and_the_other_stays_free() -> None:
    rule = _one(
        {
            "type": "rule",
            "y": 16,
            "text": "Goal",
            "style": {"marks": {"text": {"position": {"edge": "bottom"}}}},
        }
    )
    assert _free(rule) == ("bottom", "end", False, True)


def _at(note: ResolvedAnnotation) -> tuple[object, object]:
    return _value(note.x), _value(note.y)


def test_a_note_at_max_y_derives_x_from_the_max_row() -> None:
    note = _one({"type": "note", "y": "max()", "text": "Peak {{ value | format }}"})
    assert _at(note) == ("2024-08-01", 21.0)
    assert note.text == "Peak 21"
    [scatter] = _scatter([{"type": "note", "y": "max()"}]).annotations
    assert _at(scatter) == (60.0, 57.0)


def test_a_note_at_min_x_derives_y_from_the_min_row() -> None:
    [note] = _scatter(
        [{"type": "note", "x": "min()", "text": "{{ value | format }}"}]
    ).annotations
    assert _at(note) == (10.0, 12.0)
    assert note.text == "10"
    dated = _one({"type": "note", "x": "min()"})
    assert _at(dated) == ("2024-01-01", 10.0)


def test_tied_extremes_draw_one_note_each() -> None:
    rows = [
        {"month": m, "revenue": v} for m, v in zip(MONTHS, [5, 9, 3, 9], strict=False)
    ]
    notes = _line([{"type": "note", "y": "max()", "text": "hi"}], rows=rows).annotations
    assert [_at(n) for n in notes] == [("2024-02-01", 9.0), ("2024-04-01", 9.0)]
    assert {n.text for n in notes} == {"hi"}


def test_series_each_derives_one_note_per_series() -> None:
    notes = _line(
        [{"type": "note", "y": "max()", "series": "each"}],
        rows=SERIES_ROWS,
        color="region",
    ).annotations
    assert [(n.series, _at(n)) for n in notes] == [
        ("APAC", ("2024-08-01", 42.0)),
        ("EMEA", ("2024-08-01", 21.0)),
    ]


def test_series_total_derives_from_the_pooled_rows_when_nothing_stacks() -> None:
    rows = [
        {"month": m, "region": g, "revenue": v}
        for m, a, b in zip(
            MONTHS, [5, 30, 2, 4, 1, 1, 1, 1], [9, 1, 3, 4, 8, 1, 1, 1], strict=True
        )
        for g, v in (("EMEA", a), ("APAC", b))
    ]
    [note] = _line(
        [{"type": "note", "y": "max()", "series": "total"}], rows=rows, color="region"
    ).annotations
    assert note.series is None
    assert _at(note) == ("2024-02-01", 30.0)


def test_series_total_derives_from_the_per_x_sum_when_stacked() -> None:
    rows = [
        {"month": m, "region": g, "revenue": v}
        for m, a, b in zip(
            MONTHS, [5, 30, 2, 4, 1, 1, 1, 1], [9, 1, 3, 4, 8, 1, 1, 1], strict=True
        )
        for g, v in (("EMEA", a), ("APAC", b))
    ]
    chart = AreaChart.model_validate(
        {
            "id": "rev",
            "type": "area",
            "x": "month",
            "y": "revenue",
            "color": "region",
            "stack": "zero",
            "query_name": "q",
            "annotations": [{"type": "note", "y": "max()", "series": "total"}],
        }
    )
    resolved = resolve(chart, rows, _ctx(), rendered_at=NOW)
    assert isinstance(resolved, ResolvedAreaChart)
    [note] = resolved.annotations
    # Unstacked pooling would pick Feb's 30; the per-x sums peak at Feb's 31.
    assert _at(note) == ("2024-02-01", 31.0)


def test_a_derived_note_keeps_its_connector_and_enclose() -> None:
    [note] = _line(
        [{"type": "note", "y": "max()", "connector": "arrow", "enclose": "circle"}]
    ).annotations
    assert note.connector == "arrow" and note.enclose == "circle"


@pytest.mark.parametrize("fn", ["avg()", "median()", "percentile(90)"])
def test_other_aggregates_cannot_derive_a_coordinate(fn: str) -> None:
    with pytest.raises(ValidationError, match="only min\\(\\) and max\\(\\)"):
        _line([{"type": "note", "y": fn}])
    with pytest.raises(ValidationError, match="only min\\(\\) and max\\(\\)"):
        _line([{"type": "note", "x": fn}])


def test_a_literal_with_a_missing_coordinate_stays_an_error() -> None:
    with pytest.raises(ValidationError, match="both x and y, or neither"):
        _line([{"type": "note", "y": 15}])


def test_a_note_with_neither_coordinate_is_still_a_frame_note() -> None:
    note = _one({"type": "note", "text": "Hello"})
    assert note.x is None and note.y is None
    with pytest.raises(ValidationError, match="no anchor takes no connector"):
        _line([{"type": "note", "connector": "line"}])


def test_a_derived_y_with_several_y_columns_is_ambiguous() -> None:
    rows = [{**r, "cost": 1.0} for r in ROWS]
    with pytest.raises(CompilationError, match="several y columns.*set y_column"):
        _line([{"type": "note", "x": "min()"}], rows=rows, y=["revenue", "cost"])


_RULE = {"type": "rule", "y": 16}


def _rule_style(color: str) -> dict[str, Any]:
    return {"rule": {"marks": {"rule": {"stroke": {"color": color}}}}}


def test_a_board_annotations_default_applies() -> None:
    [rule] = _line(
        [_RULE], board_style={"annotations": _rule_style("#111111")}
    ).annotations
    assert rule.style.rule is not None and rule.style.rule.stroke.color == "#111111"


def test_a_chart_annotations_default_beats_the_board_for_that_chart_only() -> None:
    board = {"annotations": _rule_style("#111111")}
    [own] = _line(
        [_RULE], board_style=board, style={"annotations": _rule_style("#222222")}
    ).annotations
    [other] = _line([_RULE], board_style=board).annotations
    assert own.style.rule is not None and own.style.rule.stroke.color == "#222222"
    assert other.style.rule is not None and other.style.rule.stroke.color == "#111111"


def test_an_annotation_own_style_beats_board_and_chart_defaults() -> None:
    pinned = {
        **_RULE,
        "style": {"marks": {"rule": {"stroke": {"color": "#333333"}}}},
    }
    [rule] = _line(
        [pinned],
        board_style={"annotations": _rule_style("#111111")},
        style={"annotations": _rule_style("#222222")},
    ).annotations
    assert rule.style.rule is not None and rule.style.rule.stroke.color == "#333333"


def test_the_charts_level_annotations_path_is_rejected() -> None:
    with pytest.raises(ValidationError, match="annotations"):
        StylePatch.model_validate({"charts": {"annotations": _rule_style("#111111")}})


@pytest.mark.parametrize("year", [str, Decimal])
def test_a_string_or_decimal_year_x_resolves_like_a_date(year: type) -> None:
    rows = [
        {"year": year(2018 + i), "revenue": float(r)}
        for i, r in enumerate([1, 3, 2, 5, 4, 6])
    ]
    trend, note = _line(
        [
            {"type": "trend", "fit": "linear()"},
            {"type": "note", "x": "max()", "y": "max()", "text": "peak"},
        ],
        rows=rows,
        x="year",
    ).annotations
    assert trend.trend is not None
    assert _value(note.x) == "2023-01-01"


def test_a_non_finite_x_is_not_plotted_so_aggregates_and_fits_skip_it() -> None:
    rows = [
        {"spend": x, "signups": y}
        for x, y in [(1.0, 2.0), (3.0, 4.0), (float("nan"), 5.0), (float("inf"), 6.0)]
        + [(5.0, 9.0)]
    ]
    rule, trend = _scatter(
        [{"type": "rule", "x": "max()"}, {"type": "trend", "fit": "linear()"}],
        rows=rows,
    ).annotations
    assert _value(rule.x) == 5.0
    assert trend.trend is not None


def test_a_zero_row_query_draws_nothing_and_says_why() -> None:
    chart = _line([{"type": "rule", "y": "max()"}], rows=[])
    assert chart.annotations == ()
    [gap] = chart.annotation_gaps
    assert gap.index == 0 and "no plotted rows" in gap.reason


def test_a_listed_series_a_panel_lacks_draws_nothing_while_the_others_draw() -> None:
    rows = [
        {**row, "market": "retail"} for row in SERIES_ROWS if row["region"] == "EMEA"
    ] + [{**row, "market": "online"} for row in SERIES_ROWS]
    chart = _line(
        [{"type": "rule", "y": "max()", "series": ["EMEA", "APAC"]}],
        rows=rows,
        color="region",
        multiples={"columns": "market"},
    )
    assert len(chart.annotations) == 3
    [gap] = chart.annotation_gaps
    assert "APAC" in gap.reason and "retail" in gap.reason


def test_a_single_point_series_cannot_fit_a_trend_so_it_draws_nothing() -> None:
    rows = [{"month": MONTHS[0], "region": "A", "revenue": 1.0}] + [
        {"month": m, "region": "B", "revenue": r}
        for m, r in zip(MONTHS, REVENUE, strict=True)
    ]
    chart = _line(
        [{"type": "trend", "fit": "linear()", "series": "each"}],
        rows=rows,
        color="region",
    )
    assert [t.series for t in chart.annotations] == ["B"]
    [gap] = chart.annotation_gaps
    assert "A" in gap.reason


def test_an_interval_on_a_two_point_fit_is_a_gap_naming_its_cause() -> None:
    chart = _line(
        [{"type": "trend", "fit": "linear()", "interval": "confidence(95)"}],
        rows=ROWS[:2],
    )
    assert chart.annotations == ()
    [gap] = chart.annotation_gaps
    assert "interval needs more rows" in gap.reason


def test_a_multi_y_trend_wears_the_ink_of_its_y_column() -> None:
    rows = [
        {"month": m, "a": r, "b": r * 2} for m, r in zip(MONTHS, REVENUE, strict=True)
    ]
    chart = _line(
        [{"type": "trend", "fit": "linear()", "y_column": "b"}], rows=rows, y=["a", "b"]
    )
    assert _trend_colors(chart.annotations[0])[0] == _ctx().palette[1]


def test_a_scatter_log_y_refusal_names_the_offending_annotation() -> None:
    style = {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
    with pytest.raises(CompilationError, match="annotation 1:"):
        _scatter([{"type": "rule", "y": 5}, {"type": "rule", "y": 0}], style=style)


def test_a_size_channel_circle_clears_the_largest_mark() -> None:
    rows = [{**row, "deals": n} for n, row in enumerate(SCATTER_ROWS, start=1)]
    note = {"type": "note", "x": "max()", "y": "max()", "enclose": "circle"}
    [annotation] = _scatter([note], rows=rows, size="deals").annotations
    # The largest cell takes the top of the engine's 4..361 px^2 size range.
    assert annotation.style.circle is not None
    assert annotation.style.circle.point_radius == math.sqrt(361.0) / 2
    assert annotation.style.circle.radius == math.sqrt(361.0) / 2 + 4.0
