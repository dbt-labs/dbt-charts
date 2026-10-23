"""Cross-cutting compile-time checks for the log scale build:

- bar + axis_y.scale.type: log raises — a bar's length encodes magnitude
  from zero, meaningless on a log scale (no authored-floor escape hatch).
- axis_y.scale.type: log + axis_y.ticks.count raises — a target tick count
  on a log axis is nonsense; Vega-Lite computes log-decade ticks natively.

Both checks need information no single style model owns in isolation (chart
family for the bar check; the sibling `ticks.count` field for the tick
check), so they're raised procedurally from the resolve functions in
`compile/resolve/chart/bar.py` (bar check) and `compile/resolve/chart/_domain.py` (tick
check), mirroring the existing
`ERR-LAYERS-AMBIGUOUS-Y-DOMAIN` raise-site pattern — not a Pydantic
model_validator (see test_scale_style_type_domain.py for the validators that
*can* live on ScaleContinuousStyle itself).
"""

from __future__ import annotations

import pytest


def _default_board_style():  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _sql(sql: str = "SELECT 1"):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    return SqlQuery(sql=sql, source="t")


_DATA: list[dict] = [
    {"month": "Jan", "revenue": 100.0},
    {"month": "Feb", "revenue": 200.0},
    {"month": "Mar", "revenue": 150.0},
]


def _bar_normalized(**kwargs):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart

    defaults = {
        "id": "bar1",
        "type": "bar",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
        "variable_dependencies": set(),
    }
    defaults.update(kwargs)
    return NBarChart(**defaults)


def _line_normalized(**kwargs):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.models.chart.normalized import LineChart as NLineChart

    defaults = {
        "id": "line1",
        "type": "line",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
        "variable_dependencies": set(),
    }
    defaults.update(kwargs)
    return NLineChart(**defaults)


def _area_normalized(**kwargs):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.models.chart.normalized import AreaChart as NAreaChart

    defaults = {
        "id": "area1",
        "type": "area",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
        "variable_dependencies": set(),
    }
    defaults.update(kwargs)
    return NAreaChart(**defaults)


def _scatter_normalized(**kwargs):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.models.chart.normalized import (
        ScatterChart as NScatterChart,
    )

    defaults = {
        "id": "scatter1",
        "type": "scatter",
        "x": "month_index",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
        "variable_dependencies": set(),
    }
    defaults.update(kwargs)
    return NScatterChart(**defaults)


# ── bar + type: log ───────────────────────────────────────────────────────


def test_bar_with_log_scale_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _bar_normalized(
        style=BarChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, _DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-BAR-LOG-SCALE-NOT-SUPPORTED"


def test_bar_with_linear_scale_does_not_raise() -> None:
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _bar_normalized(
        style=BarChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "linear"}}}}
        )
    )
    resolve(chart, _DATA, _default_board_style())  # must not raise


def test_bar_with_no_scale_type_does_not_raise() -> None:
    from dbt_charts.core.compile.resolve import resolve

    chart = _bar_normalized()
    resolve(chart, _DATA, _default_board_style())  # must not raise


# ── bar + pow/sqrt/symlog passthrough (only log is banned on bar) ────────


# ── type: log + ticks.count ───────────────────────────────────────────────


def test_line_log_scale_with_ticks_count_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _line_normalized(
        style=LineChartStylePatch.model_validate(
            {
                "axis_y": {
                    "scale": {"continuous": {"type": "log"}},
                    "ticks": {"count": 5},
                }
            }
        )
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, _DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-TICKS-COUNT-REQUIRES-NON-LOG-SCALE"


def test_line_log_scale_with_board_level_ticks_count_raises() -> None:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.style.authored import (
        LineChartStylePatch,
        StylePatch,
    )
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_patch = StylePatch.model_validate(
        {"charts": {"axis_quantitative": {"ticks": {"count": 5}}}}
    )
    chart = _line_normalized(
        style=LineChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(
            chart,
            _DATA,
            resolve_chart_style_context(get_theme_style(), board_patch),
        )
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-TICKS-COUNT-REQUIRES-NON-LOG-SCALE"


def test_line_log_scale_on_clarity_does_not_raise() -> None:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    chart = _line_normalized(
        style=LineChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    resolve(chart, _DATA, resolve_chart_style_context(get_theme_style("clarity")))


def test_line_log_scale_without_ticks_count_does_not_raise() -> None:
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _line_normalized(
        style=LineChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    resolve(chart, _DATA, _default_board_style())  # must not raise


def test_line_linear_scale_with_ticks_count_does_not_raise() -> None:
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _line_normalized(
        style=LineChartStylePatch.model_validate(
            {
                "axis_y": {
                    "scale": {"continuous": {"type": "linear"}},
                    "ticks": {"count": 5},
                }
            }
        )
    )
    resolve(chart, _DATA, _default_board_style())  # must not raise


# ── smart-zero heuristic must never bake zero=True onto a log axis ───────
#
# The Pydantic `type: log` + `zero: true` validator on ScaleContinuousStyle
# only fires on construction/model_validate — _bake_y_zero's smart-zero
# heuristic bakes its decision via model_copy, which does NOT re-run
# validators. Without a guard, a chart whose y data ratio trips the
# heuristic's "extend to zero" threshold (min/max <= 0.25) ends up with an
# internally-inconsistent state (type="log", zero=True) that no validator sees.


def test_area_log_scale_smart_zero_heuristic_does_not_bake_zero_true() -> None:
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    # min/max ratio = 50/200 = 0.25, at the smart-zero "extend to zero" threshold.
    data = [
        {"month": "Jan", "revenue": 200.0},
        {"month": "Feb", "revenue": 50.0},
    ]
    chart = _area_normalized(
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    resolved = resolve(chart, data, _default_board_style())
    _sc = resolved.style.axis_y.scale
    assert _sc is not None
    assert _sc.continuous is None or _sc.continuous.zero is not True


# ── genuine log scale.type reaches the emitted VL spec for line ──────────


# ── type: log requires strictly positive data ─────────────────────────────
#
# A log domain is undefined at and below zero. Without this check, a zero or
# negative data point produces a silently-degenerate render: VL's own
# automatic domain inference degenerates on non-positive data for line/
# scatter (no baked domain), and the area domain-bake would compute a
# `<= 0` lower bound directly from the data — exactly the failure mode the
# rest of this task's validators guard against for the authored-`zero`/
# no-domain cases, but not yet for the data's own content.

_NON_POSITIVE_DATA: list[dict] = [
    {"month": "Jan", "month_index": 1, "revenue": 0.0},
    {"month": "Feb", "month_index": 2, "revenue": 200.0},
]


def test_line_log_scale_with_zero_value_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _line_normalized(
        style=LineChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, _NON_POSITIVE_DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA"


def test_area_log_scale_with_zero_value_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _area_normalized(
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, _NON_POSITIVE_DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA"


def test_line_log_scale_with_zero_in_a_shared_scale_layer_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _line_normalized(
        layers=[LineLayer(type="line", y="target")],
        style=LineChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        ),
    )
    data = [
        {"month": "Jan", "revenue": 100.0, "target": 0.0},
        {"month": "Feb", "revenue": 200.0, "target": 50.0},
    ]
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, data, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA"
    assert "target" in str(exc_info.value)


def test_line_log_scale_with_zero_in_a_right_pinned_layer_does_not_raise() -> None:
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _line_normalized(
        layers=[LineLayer(type="line", y="target", axis_y={"position": "right"})],
        style=LineChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        ),
    )
    data = [
        {"month": "Jan", "revenue": 100.0, "target": 0.0},
        {"month": "Feb", "revenue": 200.0, "target": 50.0},
    ]
    resolve(chart, data, _default_board_style())  # must not raise


def test_area_log_scale_with_zero_in_a_shared_scale_layer_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _area_normalized(
        layers=[LineLayer(type="line", y="target")],
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        ),
    )
    data = [
        {"month": "Jan", "revenue": 100.0, "target": 0.0},
        {"month": "Feb", "revenue": 200.0, "target": 50.0},
    ]
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, data, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA"


def test_scatter_log_scale_with_negative_value_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    data = [
        {"month_index": 1, "revenue": -5.0},
        {"month_index": 2, "revenue": 200.0},
    ]
    chart = _scatter_normalized(
        style=ScatterChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, data, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA"


@pytest.mark.parametrize("x", [0.0, -1.0])
def test_scatter_log_x_scale_with_a_non_positive_value_raises(x: float) -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    data = [
        {"month_index": x, "revenue": 5.0},
        {"month_index": 2.0, "revenue": 200.0},
    ]
    chart = _scatter_normalized(
        style=ScatterChartStylePatch.model_validate(
            {"axis_x": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, data, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LOG-SCALE-REQUIRES-POSITIVE-X-DATA"


def test_line_log_scale_with_all_positive_data_does_not_raise() -> None:
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _line_normalized(
        style=LineChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    resolve(
        chart, _DATA, _default_board_style()
    )  # must not raise (_DATA is all-positive)


def test_line_linear_scale_with_zero_value_does_not_raise() -> None:
    """The positive-data check is log-specific — linear (or unset) scale
    types must never trip it, however small/zero-touching the data is."""
    from dbt_charts.core.compile.resolve import resolve

    chart = _line_normalized()
    resolve(chart, _NON_POSITIVE_DATA, _default_board_style())  # must not raise


def test_line_log_scale_with_non_positive_numeric_string_raises() -> None:
    """Some warehouse adapters return measure columns as numeric strings
    (matching the shape `baseline.py`'s `_numeric_values` already handles) —
    the positive-data guard must catch a non-positive value delivered as a
    string just as it catches a real float/int."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    data = [
        {"month": "Jan", "revenue": "0"},
        {"month": "Feb", "revenue": "200"},
    ]
    chart = _line_normalized(
        style=LineChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, data, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA"


def test_area_multi_metric_log_scale_with_zero_value_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    data = [
        {"month": "Jan", "revenue": 100.0, "target": 0.0},
        {"month": "Feb", "revenue": 200.0, "target": 5000.0},
    ]
    chart = _area_normalized(
        y=["revenue", "target"],
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        ),
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, data, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA"


# ── stacked area + log is rejected (cumulative stack top is undefined) ───
#
# A stacked band's visible top is the per-x cumulative sum, not a raw data
# value — the log-domain bake (below) computes [min(raw), max(raw)] with no
# awareness of stacking, so a stacked area would silently clip the real
# stacked extent. Stacking is also semantically meaningless on a log scale
# for the same reason bar+log is rejected. Reject rather than attempt a
# stack-aware domain bake.


def test_area_stacked_zero_with_log_scale_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _area_normalized(
        stack="zero",
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        ),
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, _DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-STACKED-LOG-SCALE-NOT-SUPPORTED"


def test_area_stacked_normalize_with_log_scale_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _area_normalized(
        stack="normalize",
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        ),
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, _DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-STACKED-LOG-SCALE-NOT-SUPPORTED"


def test_area_stack_none_with_log_scale_does_not_raise_stacked_error() -> None:
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _area_normalized(
        stack="none",
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        ),
    )
    resolve(chart, _DATA, _default_board_style())  # must not raise


# ── area + log + multiples: {scale: independent} ─────────────────────────
#
# The log-domain bake just below computes one [min, max] from every panel's
# data pooled together — necessarily one shared value, since that is what
# avoids Vega-Lite's degenerate log-area rendering. Baking it under
# `scale: independent` would silently apply that one shared domain to every
# panel anyway, contradicting the per-panel-domain promise; suppressing the
# bake would silently reintroduce the degenerate rendering. Refuse instead
# of emitting a spec whose `resolve` and `encoding` contradict each other.


def test_area_log_scale_with_independent_multiples_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.models.chart.authored import MultiplesConfig
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _area_normalized(
        multiples=MultiplesConfig(rows="region", scale="independent"),
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        ),
    )
    data = [{**row, "region": "West"} for row in _DATA] + [
        {**row, "region": "East"} for row in _DATA
    ]
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, data, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-LOG-SCALE-INDEPENDENT-MULTIPLES"


def test_area_log_scale_with_shared_multiples_does_not_raise() -> None:
    from dbt_charts.core.compile.models.chart.authored import MultiplesConfig
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _area_normalized(
        multiples=MultiplesConfig(rows="region"),  # scale defaults to "shared"
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        ),
    )
    data = [{**row, "region": "West"} for row in _DATA] + [
        {**row, "region": "East"} for row in _DATA
    ]
    resolve(chart, data, _default_board_style())  # must not raise


def test_area_log_scale_with_independent_multiples_and_authored_domain_does_not_raise() -> (
    None
):
    """An explicitly authored domain is the author's own choice, not the
    auto-bake this refusal exists to gate — same as any other authored
    domain overriding `scale: independent` elsewhere in the codebase."""
    from dbt_charts.core.compile.models.chart.authored import MultiplesConfig
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _area_normalized(
        multiples=MultiplesConfig(rows="region", scale="independent"),
        style=AreaChartStylePatch.model_validate(
            {
                "axis_y": {
                    "scale": {"continuous": {"type": "log", "domain": [1.0, 1000.0]}}
                }
            }
        ),
    )
    data = [{**row, "region": "West"} for row in _DATA] + [
        {**row, "region": "East"} for row in _DATA
    ]
    resolve(chart, data, _default_board_style())  # must not raise


# ── area + log needs a baked domain (Vega-Lite area-mark quirk) ──────────
#
# Regression for a real bug caught by rendering the task's lab board: an area
# mark on a log scale with NO explicit domain renders as a degenerate flat
# line with zero visible y-axis tick labels — confirmed with a minimal
# vl-convert repro isolating `mark: area` + `scale: {type: log}` (no domain)
# vs. the same spec with an explicit `domain` (renders correctly). A line
# mark on the identical log scale + no domain renders fine — this is
# area-mark-specific, not a general log-scale gap. `_resolve_area` bakes
# `[min(y), max(y)]` onto the axis when type is log and no domain is
# authored, working around the Vega-Lite limitation rather than delegating
# to VL's automatic domain inference (which is what line/scatter still do).


def test_area_log_scale_bakes_domain_from_data() -> None:
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _area_normalized(
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        )
    )
    resolved = resolve(chart, _DATA, _default_board_style())
    scale = resolved.style.axis_y.scale
    assert scale is not None
    assert scale.continuous is not None and scale.continuous.domain == (
        100.0,
        200.0,
    )  # min/max of _DATA's revenue column


def test_area_log_scale_authored_domain_wins() -> None:
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _area_normalized(
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log", "domain": [1, 1000]}}}}
        )
    )
    resolved = resolve(chart, _DATA, _default_board_style())
    scale = resolved.style.axis_y.scale
    assert scale is not None
    assert scale.continuous is not None and scale.continuous.domain == (1, 1000)


def test_area_log_scale_with_right_pinned_layer_does_not_raise() -> None:
    """The baked log-workaround domain must not trip the ambiguous-dual-axis-
    domain check — that check exists for a genuinely AUTHORED chart-level
    domain colliding with a split-scale layer, not dbt charts' own internal
    workaround for a Vega-Lite area-mark rendering limitation.
    """
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    chart = _area_normalized(
        layers=[LineLayer(type="line", y="target", axis_y={"position": "right"})],
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        ),
    )
    data = [
        {"month": "Jan", "revenue": 100.0, "target": 4000.0},
        {"month": "Feb", "revenue": 200.0, "target": 5000.0},
    ]
    resolve(chart, data, _default_board_style())  # must not raise


def test_area_multi_metric_log_scale_bakes_domain_across_all_metrics() -> None:
    """Multi-metric area (`y: [a, b]`) hits the identical Vega-Lite area-mark
    degeneration on a log scale — the domain bake must span every metric's
    data, not just the first, or the omitted metric's range gets clipped."""
    from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    data = [
        {"month": "Jan", "revenue": 100.0, "target": 4000.0},
        {"month": "Feb", "revenue": 200.0, "target": 5000.0},
    ]
    chart = _area_normalized(
        y=["revenue", "target"],
        style=AreaChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"type": "log"}}}}
        ),
    )
    resolved = resolve(chart, data, _default_board_style())
    scale = resolved.style.axis_y.scale
    assert scale is not None
    assert scale.continuous is not None and scale.continuous.domain == (100.0, 5000.0)
