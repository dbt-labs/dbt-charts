"""Outlier bars: the break value resolve bakes, and the shapes the cap refuses."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored import AuthoredChart
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedBarChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._torn_outlier import pick_torn_break
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics import ERR_TORN_OUTLIER_UNSUPPORTED


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _resolved(
    values: list[float] | list[dict[str, Any]],
    *,
    outliers: dict[str, Any] | None = None,
    chart: dict[str, Any] | None = None,
) -> ResolvedBarChart:
    rows = [
        r if isinstance(r, dict) else {"cat": f"c{i}", "v": r}
        for i, r in enumerate(values)
    ]
    style: dict[str, Any] = {"outliers": outliers or {}}
    style.update((chart or {}).pop("style", {}))
    raw = {
        "id": "t",
        "type": "bar",
        "query_name": "q",
        "x": "cat",
        "y": "v",
        **(chart or {}),
        "style": style,
    }
    _, ctx = resolve_style_and_context(get_theme_style("clarity"))
    out = resolve(
        BarChart.model_validate(raw),
        rows,
        chart_style_context=ctx,
        rendered_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert isinstance(out, ResolvedBarChart)
    return out


CAP = {"variant": "torn_with_cap"}
ONE_OUTLIER = [145, 10, 9, 8, 6]  # median 9: the 145 is 16x
VERTICAL = {"style": {"orientation": "vertical"}}


def test_a_bar_at_the_multiplier_tears_and_one_just_under_does_not() -> None:
    assert pick_torn_break([108, 10, 9, 8, 6], None, 12, None) is not None
    assert pick_torn_break([107, 10, 9, 8, 6], None, 12, None) is None


def test_the_break_is_the_headroom_max_of_the_untorn_bars() -> None:
    assert pick_torn_break([100, 10, 9, 8], 0.1, 4, None) == pytest.approx(11.0)


def test_every_bar_at_the_multiplier_tears() -> None:
    assert pick_torn_break([900, 700, 10, 9, 8, 7, 6], None, 12, None) == 10


def test_two_bars_have_no_outlier() -> None:
    assert pick_torn_break([1000, 1], None, 12, None) is None


def test_none_turns_detection_off() -> None:
    assert pick_torn_break(ONE_OUTLIER, None, None, None) is None


def test_a_break_headroom_lifts_past_the_outlier_tears_nothing() -> None:
    assert pick_torn_break([30, 10, 9, 8], 2.5, 3, None) is None


def test_the_default_tears_off_the_chart_and_pins_the_axis_at_the_break() -> None:
    resolved = _resolved(ONE_OUTLIER, chart=VERTICAL)
    assert resolved.torn_break is not None and 8 < resolved.torn_break < 20
    assert resolved.style.outliers.variant == "torn"
    assert resolved.style.axis_y.tick_values[-1] <= resolved.torn_break


def test_the_multiplier_is_authorable() -> None:
    assert _resolved(ONE_OUTLIER, outliers={"median_multiplier": 20}).torn_break is None
    assert _resolved(ONE_OUTLIER, outliers={"median_multiplier": 3}).torn_break


def test_none_leaves_the_chart_plain() -> None:
    resolved = _resolved(ONE_OUTLIER, outliers={"median_multiplier": "none"})
    assert resolved.torn_break is None


def test_a_pinned_domain_max_tears_whatever_the_multiplier_says() -> None:
    pinned = {"style": {"axis_y": {"scale": {"continuous": {"domain": [0, 40]}}}}}
    resolved = _resolved(
        [30, 20, 45, 10], outliers={"median_multiplier": "none"}, chart=pinned
    )
    assert resolved.torn_break == 40


def test_a_pinned_domain_no_bar_passes_beats_the_multiplier() -> None:
    pinned = {"style": {"axis_y": {"scale": {"continuous": {"domain": [0, 200]}}}}}
    assert _resolved(ONE_OUTLIER, chart=pinned).torn_break is None


@pytest.mark.parametrize(
    "chart",
    [
        {"style": {"orientation": "horizontal"}},
        {"y_start": "lo"},
        {"multiples": {"rows": "s"}},
        {"layers": [{"type": "bar", "y": "t"}]},
        {"style": {"axis_y": {"mirror": True}}},
        {"style": {"axis_y": {"scale": {"continuous": {"zero": False}}}}},
        {"x": "n"},
    ],
)
def test_a_shape_off_the_chart_cannot_draw_keeps_its_axis_but_names_the_break(
    chart: dict[str, Any],
) -> None:
    rows = [
        {"cat": f"c{i}", "n": i, "s": "x", "lo": 0, "t": 5, "v": v}
        for i, v in enumerate(ONE_OUTLIER)
    ]
    chart = {**chart, "style": {"orientation": "vertical", **chart.get("style", {})}}
    plain = _resolved(rows, outliers={"median_multiplier": "none"}, chart=dict(chart))
    resolved = _resolved(rows, chart=dict(chart))
    assert resolved.torn_break is not None
    assert resolved.style.axis_y == plain.style.axis_y


def test_a_line_layer_rides_on_the_off_chart_axis() -> None:
    rows = [{"cat": f"c{i}", "t": 5, "v": v} for i, v in enumerate(ONE_OUTLIER)]
    chart = {
        "layers": [{"type": "line", "y": "t"}],
        "style": {"orientation": "vertical"},
    }
    plain = _resolved(rows, outliers={"median_multiplier": "none"}, chart=dict(chart))
    resolved = _resolved(rows, chart=dict(chart))
    assert resolved.torn_break is not None
    assert resolved.style.axis_y.tick_values[-1] <= resolved.torn_break
    assert resolved.style.axis_y != plain.style.axis_y


@pytest.mark.parametrize(
    "extra",
    [
        {"layers": [{"type": "line", "y": "t"}]},
        {"annotations": [{"type": "rule", "y": -30}]},
    ],
    ids=["layer", "annotation"],
)
def test_a_value_below_zero_widens_the_off_chart_floor(extra: dict[str, Any]) -> None:
    rows = [{"cat": f"c{i}", "t": -30, "v": v} for i, v in enumerate(ONE_OUTLIER)]
    resolved = _resolved(rows, chart={**extra, "style": {"orientation": "vertical"}})
    assert resolved.torn_break is not None
    assert resolved.style.axis_y.tick_values[0] <= -30


def test_a_stacked_chart_keeps_its_axis_and_names_the_break() -> None:
    rows = [
        {"cat": f"c{i}", "s": s, "v": v}
        for i, v in enumerate(ONE_OUTLIER)
        for s in "xy"
    ]
    stacked = {"color": "s", "style": {"stack": "zero", "orientation": "vertical"}}
    plain = _resolved(rows, outliers={"median_multiplier": "none"}, chart=dict(stacked))
    resolved = _resolved(rows, chart=dict(stacked))
    assert resolved.torn_break is not None
    assert resolved.stacked_domain_max == plain.stacked_domain_max


def test_the_cap_variant_breaks_at_the_headroom_max_of_the_rest() -> None:
    resolved = _resolved(ONE_OUTLIER, outliers=CAP)
    assert resolved.torn_break is not None and 8 < resolved.torn_break < 20
    assert resolved.style.axis_y.domain_max == resolved.torn_break
    assert resolved.style.outliers.variant == "torn_with_cap"


def test_the_cap_variant_with_none_and_no_pinned_domain_is_plain() -> None:
    cap_off = {**CAP, "median_multiplier": "none"}
    assert _resolved(ONE_OUTLIER, outliers=cap_off).torn_break is None


def test_a_cap_chart_carries_the_cap_shape_and_reveal() -> None:
    style = _resolved(ONE_OUTLIER, outliers={**CAP, "reveal": "bar"}).style.outliers
    assert style.reveal == "bar"
    assert set(style.model_dump()) == {"variant", "reveal", "cap"}


def test_two_outliers_are_both_torn() -> None:
    resolved = _resolved([255, 145, 10, 9, 8, 7, 6, 5, 5, 5], outliers=CAP)
    assert resolved.torn_break is not None and resolved.torn_break < 20


def test_an_annotation_under_the_break_draws() -> None:
    resolved = _resolved(
        [145, 10, 9, 8, 6],
        outliers=CAP,
        chart={"annotations": [{"type": "rule", "y": 5}]},
    )
    assert resolved.torn_break is not None


def test_an_authored_tick_ladder_stops_at_the_break() -> None:
    resolved = _resolved(
        [145, 10, 9, 8, 6],
        outliers=CAP,
        chart={"style": {"axis_y": {"scale": {"values": [0, 5, 10, 50, 100]}}}},
    )
    assert resolved.torn_break is not None
    assert resolved.style.axis_y.tick_values == (0, 5, 10)


def _capped(
    values: list[float] | list[dict[str, Any]], *, chart: dict[str, Any] | None = None
) -> ResolvedBarChart:
    return _resolved(values, outliers=CAP, chart=chart)


def test_stacked_extents_are_category_totals() -> None:
    rows = [
        {"cat": cat, "s": s, "v": v}
        for cat, parts in {
            "a": (100, 72),
            "b": (3, 2),
            "c": (3, 2),
            "d": (3, 2),
            "e": (3, 2),
        }.items()
        for s, v in zip("xy", parts, strict=True)
    ]
    resolved = _capped(rows, chart={"color": "s", "style": {"stack": "zero"}})
    assert resolved.torn_break is not None
    assert resolved.torn_break < 20
    assert resolved.stacked_domain_max == resolved.torn_break


@pytest.mark.parametrize(
    ("chart", "needle"),
    [
        ({"style": {"stack": "normalize"}, "color": "s"}, "normalize"),
        ({"y_start": "lo"}, "y_start"),
        (
            {"layers": [{"type": "bar", "y": "v"}]},
            "bar layer on the shared axis_y scale",
        ),
        (
            {"style": {"axis_y": {"scale": {"continuous": {"domain": [10, 200]}}}}},
            "domain above zero",
        ),
        (
            {
                "layers": [{"type": "line", "y": "t"}],
                "style": {"axis_y": {"scale": {"continuous": {"domain": [0, 12]}}}},
            },
            "layer value at 50, past the break at 12",
        ),
        ({"multiples": {"rows": "s", "scale": "independent"}}, "multiples"),
        ({"multiples": {"rows": "s"}}, "multiples"),
        (
            {"style": {"axis_y": {"scale": {"continuous": {"zero": False}}}}},
            "zero",
        ),
        ({"annotations": [{"type": "rule", "y": 200}]}, "annotation at 200"),
    ],
)
def test_unsupported_shapes_raise(chart: dict[str, Any], needle: str) -> None:
    rows = [
        {"cat": f"c{i}", "s": "x", "lo": 0, "t": 50, "v": v}
        for i, v in enumerate([145, 10, 9, 8])
    ]
    with pytest.raises(CompilationError, match=needle) as err:
        _capped(rows, chart=dict(chart))
    assert err.value.code is ERR_TORN_OUTLIER_UNSUPPORTED


def _with_target(values: list[float], target: float) -> list[dict[str, Any]]:
    return [{"cat": f"c{i}", "v": v, "t": target} for i, v in enumerate(values)]


def test_a_layer_under_the_break_leaves_it_alone() -> None:
    plain = _capped([145, 10, 9, 8, 6]).torn_break
    layered = _capped(
        _with_target([145, 10, 9, 8, 6], 5),
        chart={"layers": [{"type": "line", "y": "t"}]},
    )
    assert layered.torn_break == plain


def test_a_layer_value_lifts_the_auto_break() -> None:
    plain = _capped([145, 10, 9, 8, 6]).torn_break
    layered = _capped(
        _with_target([145, 10, 9, 8, 6], 40),
        chart={"layers": [{"type": "line", "y": "t"}]},
    )
    assert plain is not None and layered.torn_break is not None
    assert layered.torn_break >= 40 > plain


def test_a_layer_value_past_every_cut_bar_leaves_nothing_to_tear() -> None:
    layered = _capped(
        _with_target([145, 10, 9, 8, 6], 200),
        chart={"layers": [{"type": "line", "y": "t"}]},
    )
    assert layered.torn_break is None


def test_an_own_scale_layer_is_ignored_by_the_break() -> None:
    plain = _capped([145, 10, 9, 8, 6]).torn_break
    layered = _capped(
        _with_target([145, 10, 9, 8, 6], 40),
        chart={"layers": [{"type": "line", "y": "t", "axis_y": {"position": "right"}}]},
    )
    assert layered.torn_break == plain


def test_an_authored_domain_max_is_the_break() -> None:
    resolved = _capped(
        [145, 10, 9, 8, 6],
        chart={"style": {"axis_y": {"scale": {"continuous": {"domain": [-5, 40]}}}}},
    )
    assert resolved.torn_break == 40
    assert resolved.style.axis_y.domain_min == -5
    assert (
        resolved.style.axis_y.tick_values[0] >= -5
        and resolved.style.axis_y.tick_values[-1] <= 40
    )


def test_an_authored_domain_no_bar_passes_is_a_plain_chart() -> None:
    resolved = _capped(
        [30, 10, 9, 8, 6],
        chart={"style": {"axis_y": {"scale": {"continuous": {"domain": [0, 40]}}}}},
    )
    assert resolved.torn_break is None


def test_negative_bars_do_not_raise_and_the_break_ignores_them() -> None:
    plain = _capped([145, 10, 9, 8, 6]).torn_break
    resolved = _capped([145, 10, -9, 8, 6])
    assert resolved.torn_break == plain


def test_negative_bars_bake_the_domain_min_and_ticks_start_there() -> None:
    resolved = _capped([145, 10, -40, 8, 6])
    assert resolved.torn_break is not None
    low = resolved.style.axis_y.domain_min
    assert low is not None and low <= -40
    assert resolved.style.axis_y.tick_values[0] >= low
    assert min(resolved.style.axis_y.tick_values) < 0


def test_stacked_negatives_split_from_the_positive_pile_and_bake_the_floor() -> None:
    # Netted, c's pile would be 1 and the break would sit at the next bar down.
    piles = {"a": (200, 0), "b": (10, 0), "c": (15, -14), "d": (9, 0), "e": (8, 0)}
    rows = [
        {"cat": cat, "s": s, "v": v}
        for cat, parts in {**piles, "f": (7, 0)}.items()
        for s, v in zip("xy", parts, strict=True)
    ]
    resolved = _capped(rows, chart={"color": "s", "style": {"stack": "zero"}})
    assert resolved.torn_break is not None and resolved.torn_break >= 15
    assert resolved.style.axis_y.domain_min is not None
    assert resolved.style.axis_y.domain_min <= -14
    # A zero-anchored stack floors at the ladder's first rung, which must not sit above the pile.
    assert resolved.style.axis_y.tick_values[0] <= -14


def test_a_layer_value_below_zero_bakes_the_domain_min() -> None:
    resolved = _capped(
        _with_target([145, 10, 9, 8, 6], -30),
        chart={"layers": [{"type": "line", "y": "t"}]},
    )
    assert resolved.torn_break is not None
    assert resolved.style.axis_y.domain_min is not None
    assert resolved.style.axis_y.domain_min <= -30


def test_stacked_negatives_do_not_count_toward_the_pile() -> None:
    rows = [
        {"cat": cat, "s": s, "v": v}
        for cat, parts in {
            "a": (100, 72),
            "b": (3, 2),
            "c": (3, -2),
            "d": (3, 2),
            "e": (3, 2),
        }.items()
        for s, v in zip("xy", parts, strict=True)
    ]
    resolved = _capped(rows, chart={"color": "s", "style": {"stack": "zero"}})
    assert resolved.torn_break is not None and resolved.torn_break < 20


def test_histogram_refuses_the_key() -> None:
    with pytest.raises(ValidationError, match="outliers"):
        TypeAdapter(AuthoredChart).validate_python(
            {
                "type": "histogram",
                "x": "v",
                "query": {"sql": "select 1"},
                "style": {"outliers": {"variant": "torn"}},
            }
        )
