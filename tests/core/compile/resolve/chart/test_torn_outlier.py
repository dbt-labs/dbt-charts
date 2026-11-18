"""Torn outlier bars: the break value resolve bakes, and the shapes it refuses."""

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
from dbt_charts.core.constants.bar import TORN_OUTLIER_MIN_RATIO
from dbt_charts.core.diagnostics import (
    ERR_TORN_OUTLIER_UNSUPPORTED,
    WARN_TORN_OUTLIER_NO_OUTLIER,
)
from dbt_charts.core.render.warnings import torn_outlier_no_outlier
from dbt_charts.core.render.warnings.base import WarningContext


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _resolved(
    values: list[float] | list[dict[str, Any]],
    *,
    torn: dict[str, Any] | None = None,
    chart: dict[str, Any] | None = None,
) -> ResolvedBarChart:
    rows = [
        r if isinstance(r, dict) else {"cat": f"c{i}", "v": r}
        for i, r in enumerate(values)
    ]
    style: dict[str, Any] = {"torn_outlier": {"enabled": True, **(torn or {})}}
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


def test_single_outlier_breaks_at_the_headroom_max_of_the_rest() -> None:
    resolved = _resolved([145, 10, 9, 8, 6])
    assert resolved.torn_break is not None
    assert resolved.torn_break > 8.0
    assert resolved.torn_break < 20  # never reaches the outlier


def test_domain_ends_exactly_at_the_break() -> None:
    resolved = _resolved([145, 10, 9, 8, 6])
    assert resolved.style.axis_y.domain_max == resolved.torn_break


def test_two_outliers_are_both_torn() -> None:
    resolved = _resolved([255, 145, 10, 9, 8, 7, 6, 5])
    single = _resolved([255, 10, 9, 8, 7, 6, 5, 4]).torn_break
    assert resolved.torn_break is not None and single is not None
    assert resolved.torn_break < 20


def test_ratio_below_threshold_does_not_tear() -> None:
    top = TORN_OUTLIER_MIN_RATIO * 0.9 * 10
    assert pick_torn_break([top, 10, 9, 8], None, None, None) is None
    assert (
        pick_torn_break([TORN_OUTLIER_MIN_RATIO * 10, 10, 9, 8], None, None, None) == 10
    )


def test_fewer_than_four_bars_never_tear() -> None:
    assert pick_torn_break([100, 1, 1], None, None, None) is None


def test_largest_ratio_among_the_first_third_wins() -> None:
    values = [900, 100, 10, 9, 8, 7, 6, 5]  # n=8 -> k in 1..2; r1=9, r2=10
    assert pick_torn_break(values, None, None, None) == 10


def test_two_of_six_bars_tear() -> None:
    assert pick_torn_break([400, 300, 20, 18, 16, 14], None, None, None) == 20


def test_a_break_headroom_lifts_past_the_outlier_tears_nothing() -> None:
    assert pick_torn_break([30, 10, 9, 8], 2.5, None, None) is None


def test_an_annotation_under_the_break_draws() -> None:
    resolved = _resolved(
        [145, 10, 9, 8, 6], chart={"annotations": [{"type": "rule", "y": 5}]}
    )
    assert resolved.torn_break is not None


def test_an_authored_tick_ladder_stops_at_the_break() -> None:
    resolved = _resolved(
        [145, 10, 9, 8, 6],
        chart={"style": {"axis_y": {"scale": {"values": [0, 5, 10, 50, 100]}}}},
    )
    assert resolved.torn_break is not None
    assert resolved.style.axis_y.tick_values == (0, 5, 10)


def test_headroom_expands_the_break() -> None:
    assert pick_torn_break([100, 10, 9, 8], 0.1, None, None) == pytest.approx(11.0)


def test_authored_at_replaces_the_auto_rule() -> None:
    resolved = _resolved([30, 20, 10, 5], torn={"at": 15})
    assert resolved.torn_break == 15
    assert resolved.style.axis_y.domain_max == 15


def test_authored_at_above_every_bar_tears_nothing() -> None:
    assert _resolved([30, 20, 10, 5], torn={"at": 40}).torn_break is None


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
    resolved = _resolved(rows, chart={"color": "s", "style": {"stack": "zero"}})
    assert resolved.torn_break is not None
    assert resolved.torn_break < 20
    assert resolved.stacked_domain_max == resolved.torn_break


def test_style_carries_the_theme_shape_without_at() -> None:
    resolved = _resolved([145, 10, 9, 8, 6], torn={"at": 12})
    assert set(resolved.style.torn_outlier.model_dump()) == {
        "enabled",
        "band",
        "cap",
    }
    assert resolved.style.torn_outlier.enabled is True


def test_off_by_default() -> None:
    _, ctx = resolve_style_and_context(get_theme_style("clarity"))
    chart = BarChart.model_validate(
        {"id": "t", "type": "bar", "query_name": "q", "x": "cat", "y": "v"}
    )
    out = resolve(
        chart,
        [{"cat": f"c{i}", "v": v} for i, v in enumerate([145, 10, 9, 8, 6])],
        chart_style_context=ctx,
    )
    assert isinstance(out, ResolvedBarChart)
    assert out.torn_break is None and out.style.torn_outlier.enabled is False


def test_no_outlier_is_a_plain_chart_with_a_warning() -> None:
    resolved = _resolved([10, 9, 8, 7, 6])
    assert resolved.torn_break is None
    ctx = WarningContext.model_construct(layout_charts={"t": resolved})
    codes = [d.code for d in torn_outlier_no_outlier.detect(ctx)]
    assert codes == [WARN_TORN_OUTLIER_NO_OUTLIER.code]


def test_a_torn_chart_raises_no_warning() -> None:
    resolved = _resolved([145, 10, 9, 8, 6])
    ctx = WarningContext.model_construct(layout_charts={"t": resolved})
    assert torn_outlier_no_outlier.detect(ctx) == []


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
                "style": {
                    "torn_outlier": {"enabled": True, "at": 12},
                    "axis_y": {"scale": {"continuous": {"domain": [0, 200]}}},
                }
            },
            "both `torn_outlier.at` and an authored axis_y domain",
        ),
        (
            {
                "layers": [{"type": "line", "y": "t"}],
                "style": {"torn_outlier": {"enabled": True, "at": 12}},
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
        _resolved(rows, chart=dict(chart))
    assert err.value.code is ERR_TORN_OUTLIER_UNSUPPORTED


def _with_target(values: list[float], target: float) -> list[dict[str, Any]]:
    return [{"cat": f"c{i}", "v": v, "t": target} for i, v in enumerate(values)]


def test_a_layer_under_the_break_leaves_it_alone() -> None:
    plain = _resolved([145, 10, 9, 8, 6]).torn_break
    layered = _resolved(
        _with_target([145, 10, 9, 8, 6], 5),
        chart={"layers": [{"type": "line", "y": "t"}]},
    )
    assert layered.torn_break == plain


def test_a_layer_value_lifts_the_auto_break() -> None:
    plain = _resolved([145, 10, 9, 8, 6]).torn_break
    layered = _resolved(
        _with_target([145, 10, 9, 8, 6], 40),
        chart={"layers": [{"type": "line", "y": "t"}]},
    )
    assert plain is not None and layered.torn_break is not None
    assert layered.torn_break >= 40 > plain


def test_a_layer_value_past_every_cut_bar_leaves_nothing_to_tear() -> None:
    layered = _resolved(
        _with_target([145, 10, 9, 8, 6], 200),
        chart={"layers": [{"type": "line", "y": "t"}]},
    )
    assert layered.torn_break is None


def test_an_own_scale_layer_is_ignored_by_the_break() -> None:
    plain = _resolved([145, 10, 9, 8, 6]).torn_break
    layered = _resolved(
        _with_target([145, 10, 9, 8, 6], 40),
        chart={"layers": [{"type": "line", "y": "t", "axis_y": {"position": "right"}}]},
    )
    assert layered.torn_break == plain


def test_an_authored_domain_max_is_the_break() -> None:
    resolved = _resolved(
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
    resolved = _resolved(
        [30, 10, 9, 8, 6],
        chart={"style": {"axis_y": {"scale": {"continuous": {"domain": [0, 40]}}}}},
    )
    assert resolved.torn_break is None


def test_negative_bars_do_not_raise_and_the_break_ignores_them() -> None:
    plain = _resolved([145, 10, 9, 8, 6]).torn_break
    resolved = _resolved([145, 10, -9, 8, 6])
    assert resolved.torn_break == plain


def test_negative_bars_bake_the_domain_min_and_ticks_start_there() -> None:
    resolved = _resolved([145, 10, -40, 8, 6])
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
    resolved = _resolved(rows, chart={"color": "s", "style": {"stack": "zero"}})
    assert resolved.torn_break is not None and resolved.torn_break >= 15
    assert resolved.style.axis_y.domain_min is not None
    assert resolved.style.axis_y.domain_min <= -14
    # A zero-anchored stack floors at the ladder's first rung, which must not sit above the pile.
    assert resolved.style.axis_y.tick_values[0] <= -14


def test_a_layer_value_below_zero_bakes_the_domain_min() -> None:
    resolved = _resolved(
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
    resolved = _resolved(rows, chart={"color": "s", "style": {"stack": "zero"}})
    assert resolved.torn_break is not None and resolved.torn_break < 20


def test_histogram_refuses_the_key() -> None:
    with pytest.raises(ValidationError, match="torn_outlier"):
        TypeAdapter(AuthoredChart).validate_python(
            {
                "type": "histogram",
                "x": "v",
                "query": {"sql": "select 1"},
                "style": {"torn_outlier": {"enabled": True}},
            }
        )
