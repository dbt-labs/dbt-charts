"""Multi-field `y:` combinations that are decidable from structure alone.

Each case here used to reach a render emitter and raise a bare-message
``ChartDataError``, which the diagnostics layer stamps ``ERR-INTERNAL`` — so a
board passed ``dct validate`` and then died at render with the engine's
"this is a bug" code. They are data-free, so they belong on the authored model
alongside ``_validate_support_table``.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored import AuthoredChart
from dbt_charts.core.compile.resolve.chart.positional import bind_channels

_chart_patch_adapter = TypeAdapter(AuthoredChart)


def _validate(**fields: object) -> object:
    return _chart_patch_adapter.validate_python({"query": "q", **fields})


# =============================================================================
# spark_bar — single-series family
# =============================================================================


def test_spark_bar_rejects_multi_field_y():
    with pytest.raises(ValidationError) as exc:
        _validate(type="spark_bar", x="month", y=["revenue", "cost"])
    assert "List should have at most 1 item after validation, not 2" in str(exc.value)


def test_spark_bar_accepts_single_element_y_list():
    chart = _validate(type="spark_bar", x="month", y=["revenue"])
    assert chart.y == ["revenue"]


# =============================================================================
# bar / area — layers: is single-series-only with a y list; color: composes
# =============================================================================


@pytest.mark.parametrize("chart_type", ["bar", "area"])
def test_rejects_layers_with_multi_field_y(chart_type: str):
    with pytest.raises(
        ValidationError, match="layers are not supported with multi-metric"
    ):
        _validate(
            type=chart_type,
            x="month",
            y=["revenue", "cost"],
            layers=[{"type": "line", "y": "target"}],
        )


@pytest.mark.parametrize("fields", [{}, {"color": "region"}, {"y": []}, {"y": ""}])
def test_bar_with_x_and_no_measure_parses_and_fails_to_bind(
    fields: dict[str, object], make_chart
):
    """A bar with an x and no measure has nothing to draw. Parse accepts it, so
    a board can be edited one channel at a time; the binder refuses it before
    the engine ever sees a null field, an empty list or name included."""
    _validate(type="bar", x="month", **fields)
    with pytest.raises(CompilationError, match="but not `y`"):
        bind_channels(
            make_chart("bar", **{"x": "month", "y": None, **fields}),
            ["month", "revenue"],
        )


def test_bar_without_x_or_y_still_parses():
    chart = _validate(type="bar")
    assert chart.x is None and chart.y is None


def test_histogram_with_x_and_no_y_still_parses():
    chart = _validate(type="histogram", x="amount")
    assert chart.type == "histogram"


def test_bar_with_a_multi_field_y_and_no_x_parses_and_fails_to_bind(make_chart):
    _validate(type="bar", y=["revenue", "cost"])
    with pytest.raises(CompilationError, match="names `y` but not `x`"):
        bind_channels(
            make_chart("bar", x=None, y=["revenue", "cost"]), ["month", "revenue"]
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"color": "segment"},
        {},
    ],
)
def test_histogram_is_not_caught_by_the_bar_rules(fields: dict[str, object]):
    # A histogram bins x and counts, never reading y — the emitter returns into
    # the histogram path before the multi-metric branch, so these combinations
    # render today and must not be newly rejected.
    chart = _validate(type="histogram", x="amount", y=["a", "b"], **fields)
    assert chart.type == "histogram"


# =============================================================================
# axis_y.mirror — one shared y-scale, no meaning across series
#
# Stays a render-time check: mirror is cascade-resolved, so a theme layer can
# turn it on for a chart that never authored it. It only has to stop being
# ERR-INTERNAL.
# =============================================================================


@pytest.mark.parametrize("mirror", [True, {"format": "$,.2f"}])
def test_authored_model_accepts_mirror_with_multi_field_y(mirror: object):
    chart = _validate(
        type="line",
        x="month",
        y=["revenue", "cost"],
        style={"axis_y": {"mirror": mirror}},
    )
    assert chart.y == ["revenue", "cost"]


# =============================================================================
# Combinations the authored model still accepts — the validators above must not
# widen past bar/area. This pins parse-time acceptance only; it makes no claim
# about what each family emits (scatter's multi-y y-channel and heatmap's
# dropped color: are separate defects, not something these validators decide).
# =============================================================================


@pytest.mark.parametrize("chart_type", ["bar", "line", "area", "scatter", "heatmap"])
def test_accepts_plain_multi_field_y(chart_type: str):
    chart = _validate(type=chart_type, x="month", y=["revenue", "cost"])
    assert chart.y == ["revenue", "cost"]


@pytest.mark.parametrize("chart_type", ["bar", "area", "line", "scatter", "heatmap"])
def test_accepts_color_with_multi_field_y_on_supporting_families(chart_type: str):
    """A color: column is the dimension a wide fold crosses its measures with
    (resolve/chart/_wide_fields.py), so no family rejects the pair at parse."""
    chart = _validate(type=chart_type, x="month", y=["revenue", "cost"], color="region")
    assert chart.color == "region"


@pytest.mark.parametrize("chart_type", ["bar", "area"])
def test_accepts_color_with_single_y(chart_type: str):
    chart = _validate(type=chart_type, x="month", y="revenue", color="region")
    assert chart.color == "region"
