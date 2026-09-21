"""Tests for the LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER render-warning detector.

Detection rule:
  - Chart has a y-encoding field whose name matches currency or percent signals
    AND the chart's baked y-axis format does not suit that kind (percent format
    carries '%', currency carries '$'; the SI default '~s' suits neither).

Currency name signals: ends in _usd, _dollars, _revenue, _amount, _price,
  _cost, _spend, _value, _gmv, _arr, _mrr; contains 'revenue', 'dollars', 'usd';
  or is bare 'usd'/'dollars'/'revenue'/'price'/'gmv'/'arr'/'mrr'.

Percent name signals: ends in _pct, _percent, _percentage, _rate, _share;
  contains 'percent'; or is bare 'pct'/'percent'/'percentage'/'share'.

Bare 'value'/'amount'/'rate'/'cost'/'spend' do NOT match (no unit meaning on
their own) — only their `_`-prefixed suffix form does.

Skip: charts whose type implies no y-axis (kpi, table, callout, text, markdown, pivot).
Once any layer pins axis_y.position the y axes resolve independently: a layer is
then checked against its own axis_y.labels.format when it sets one, else the
chart's. With no side pinned every layer is checked against the chart's format.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.diagnostics import (
    WARN_LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER,
    Diagnostic,
)
from dbt_charts.core.render.warnings import (
    WarningContext,
    likely_currency_or_percent_missing_formatter as detector,
)

from ...core._board_utils import (
    make_test_resolved_board,
    make_test_resolved_chart,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CHART_DEFAULTS: dict[str, object] = {
    "id": "c1",
    "type": "bar",
    "query_name": "q",
    "title": "",
}


def _make_chart(**kwargs: Any) -> Chart:
    return TypeAdapter(Chart).validate_python(dict(**{**_CHART_DEFAULTS, **kwargs}))


def _make_ctx(
    chart: Chart,
    rows: list[dict[str, Any]] | None = None,
    vega_spec: dict[str, Any] | None = None,
) -> WarningContext:
    resolved = make_test_resolved_chart(chart)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows or []},
        vega_specs={resolved.id: vega_spec or {"mark": "bar"}},
    )


# ---------------------------------------------------------------------------
# Test 1: Bar chart with y: revenue_usd, no format → fires, fix mentions currency
# ---------------------------------------------------------------------------


def test_bar_currency_column_no_format_fires() -> None:
    """Bar chart with y=revenue_usd and no format must fire with currency fix."""
    chart = _make_chart(y="revenue_usd")
    ctx = _make_ctx(chart)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    w = warnings[0]
    assert isinstance(w, Diagnostic)
    assert w.code == WARN_LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER.code
    assert w.chart == "c1"
    assert w.field == "revenue_usd"
    assert "currency" in (w.fix or "").lower()


# ---------------------------------------------------------------------------
# Test 2: Bar chart with y: revenue_usd, format: currency → no warning
# ---------------------------------------------------------------------------


def test_bar_currency_column_with_format_no_warning() -> None:
    """Bar chart with y=revenue_usd and format=currency must not fire."""
    chart = _make_chart(y="revenue_usd", format="currency")
    ctx = _make_ctx(chart)
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Test 3: Line chart with y: conversion_rate, no format → fires, fix mentions percent
# ---------------------------------------------------------------------------


def test_line_percent_column_no_format_fires() -> None:
    """Line chart with y=conversion_rate and no format must fire with percent fix."""
    chart = _make_chart(type="line", y="conversion_rate")
    ctx = _make_ctx(chart)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER.code
    assert w.field == "conversion_rate"
    assert "percent" in (w.fix or "").lower()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Test 4: Chart with y: order_count → no warning (not currency or percent)
# ---------------------------------------------------------------------------


def test_non_currency_non_percent_column_no_warning() -> None:
    """Chart with y=order_count must not fire — count is not currency/percent."""
    chart = _make_chart(y="order_count")
    ctx = _make_ctx(chart)
    assert detector.detect(ctx) == []


def test_wide_scatter_currency_column_no_format_fires() -> None:
    """Wide (``y: [...]``) scatter -- scatter joined the wide-measures shape
    already covered for bar/area/line. Must check the real authored measure
    names, not the synthetic WIDE_VALUE_FIELD that replaces chart.y at
    resolve time."""
    chart = _make_chart(type="scatter", x="month", y=["revenue_usd", "order_count"])
    ctx = _make_ctx(chart)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == WARN_LIKELY_CURRENCY_OR_PERCENT_MISSING_FORMATTER.code
    assert w.field == "revenue_usd"
    assert "currency" in (w.fix or "").lower()


def test_overlay_layer_currency_fires_when_base_format_absent() -> None:
    """A currency-looking y on an overlay layer warns when chart format is absent.

    The base y (order_count) is not currency; only the line layer's cost_usd
    should fire. Regression coverage for per-layer detection on the base+layers
    surface (replaces the old type: layered per-layer test).
    """
    chart = _make_chart(
        type="bar",
        y="order_count",
        layers=[{"type": "line", "y": "cost_usd"}],
    )
    ctx = _make_ctx(chart)
    warnings = detector.detect(ctx)
    fields = {w.field for w in warnings}
    assert "cost_usd" in fields
    assert "order_count" not in fields


# ---------------------------------------------------------------------------
# Test 6: Line chart with a percent y field AND a top-level style.axis_y.format
#          → no warning. On resolved charts the format lives at style.axis_y.format
#          (the per-family style.<family> patch is None), so the detector must read
#          it there. Regression: it previously read style.<family>.axis_y.format and
#          false-positived on every author who set a correct axis_y format.
# ---------------------------------------------------------------------------


def _resolve_authored(chart_yaml: str, chart_id: str = "c1") -> Any:
    """Compile authored chart YAML → ResolvedChart, mirroring the render path."""
    import yaml

    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    compiled = normalize_chart(chart_id, yaml.safe_load(chart_yaml), {}, sources={})
    return resolve(compiled, [], resolve_chart_style_context(get_theme_style()))


def test_line_percent_column_with_axis_y_format_no_warning() -> None:
    """Percent y with a top-level style.axis_y.format must not fire (resolved path)."""
    resolved = _resolve_authored(
        "type: line\nx: month\ny: win_rate\n"
        'style:\n  axis_y:\n    labels:\n      format: ".0%"\n'
    )
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: []},
        vega_specs={resolved.id: {"mark": "line"}},
    )
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Test 7: Currency field with a *present but unfit* format (a percent format) still
#          fires. This is the presence→appropriateness distinction: a plain
#          presence check would suppress on any format, but appropriateness fires
#          because '.0%' carries no '$'. Locks the behavior the fix is built on —
#          a regression to `return bool(fmt)` would break this test.
# ---------------------------------------------------------------------------


def test_currency_field_with_unfit_percent_format_still_fires() -> None:
    """A currency y whose baked format is a percent format must still fire."""
    resolved = _resolve_authored(
        "type: bar\nx: month\ny: revenue_usd\n"
        'style:\n  axis_y:\n    labels:\n      format: ".0%"\n'
    )
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: []},
        vega_specs={resolved.id: {"mark": "bar"}},
    )
    warnings = detector.detect(ctx)
    assert len(warnings) == 1
    assert warnings[0].field == "revenue_usd"
    assert "currency" in (warnings[0].fix or "").lower()


# ---------------------------------------------------------------------------
# Test 8: A currency format supplied via whole-chart style.number_format suppresses
#          the warning. number_format bakes into the resolved style.axis_y.format,
#          so the detector sees the '$' — guards the docstring's baking claim.
# ---------------------------------------------------------------------------


def test_currency_field_with_number_format_no_warning() -> None:
    """A currency y formatted via style.number_format must not fire."""
    resolved = _resolve_authored(
        'type: bar\nx: month\ny: revenue_usd\nstyle:\n  number_format: "$.2f"\n'
    )
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: []},
        vega_specs={resolved.id: {"mark": "bar"}},
    )
    assert detector.detect(ctx) == []


# ---------------------------------------------------------------------------
# Test 9: Bare field names (no prefix/underscore) must classify too — "share"
# is a realistic column name that previously slipped past the suffix-only
# match (`"share".endswith("_share")` is False).
# ---------------------------------------------------------------------------


def test_bare_percent_field_name_no_format_fires() -> None:
    """Bar chart with y=share (bare, no underscore) and no format must fire."""
    chart = _make_chart(y="share")
    ctx = _make_ctx(chart)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    assert warnings[0].field == "share"
    assert "percent" in (warnings[0].fix or "").lower()


def test_bare_currency_field_name_no_format_fires() -> None:
    """Bar chart with y=mrr (bare) and no format must fire."""
    # "mrr" (not "revenue") to exercise the bare-name path, not the substring one.
    chart = _make_chart(y="mrr")
    ctx = _make_ctx(chart)
    warnings = detector.detect(ctx)

    assert len(warnings) == 1
    assert warnings[0].field == "mrr"
    assert "currency" in (warnings[0].fix or "").lower()


def test_bare_generic_metric_names_no_warning() -> None:
    """Bare value/amount/rate/cost/spend must NOT fire; only their
    `_`-prefixed suffix form (`_value`, `_amount`, ...) still matches."""
    for name in ("value", "amount", "rate", "cost", "spend"):
        chart = _make_chart(y=name)
        ctx = _make_ctx(chart)
        assert detector.detect(ctx) == [], f"{name!r} must not fire bare"


def _combo(layer_axis_y: dict[str, Any], **base: Any) -> WarningContext:
    layer = {"type": "line", "y": "open_rate", "axis_y": layer_axis_y}
    return _make_ctx(_make_chart(type="bar", y="sends", layers=[layer], **base))


def test_dual_axis_layer_with_own_format_no_warning() -> None:
    ctx = _combo({"position": "right", "labels": {"format": ".0%"}})
    assert detector.detect(ctx) == []


def test_dual_axis_layer_without_format_fires_at_the_layer() -> None:
    (warning,) = detector.detect(_combo({"position": "right"}))
    assert warning.field == "open_rate"
    assert warning.path == "charts.c1.layers.0.axis_y.labels.format"
    assert "layers[0].axis_y.labels.format" in (warning.fix or "")


def test_dual_axis_layer_with_unfit_own_format_fires() -> None:
    (warning,) = detector.detect(
        _combo({"position": "right", "labels": {"format": "$,.0f"}})
    )
    assert warning.field == "open_rate"


def test_layer_format_without_a_pinned_side_is_not_rendered_so_still_fires() -> None:
    (warning,) = detector.detect(_combo({"labels": {"format": ".0%"}}))
    assert warning.field == "open_rate"
    assert warning.path == "charts.c1.style.axis_y.labels.format"
    assert "`style.axis_y.labels.format`" in (warning.fix or "")


def test_dual_axis_layer_without_format_inherits_a_fit_chart_format() -> None:
    layer = {"type": "line", "y": "refund_amount", "axis_y": {"position": "right"}}
    chart = _make_chart(
        type="bar",
        y="sends",
        layers=[layer],
        style={"axis_y": {"labels": {"format": "$,.0f"}}},
    )
    assert detector.detect(_make_ctx(chart)) == []


def test_dual_axis_base_warns_while_formatted_layer_does_not() -> None:
    layer = {
        "type": "line",
        "y": "open_rate",
        "axis_y": {"position": "right", "labels": {"format": ".0%"}},
    }
    chart = _make_chart(type="bar", y="revenue_usd", layers=[layer])
    assert [w.field for w in detector.detect(_make_ctx(chart))] == ["revenue_usd"]
