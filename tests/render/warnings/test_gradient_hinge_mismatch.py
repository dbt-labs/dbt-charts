"""Tests for the GRADIENT_HINGE_MISMATCH render-warning detector.

A continuous color scale pivots at its hinge. The detector flags the three
authored shapes where the pivot is not a neutral value the author chose:

  palette family | hinge   | data                    | verdict
  ---------------|---------|-------------------------|-----------------------
  diverging      | unset   | any                     | fires (neutral = midpoint)
  diverging      | 0       | any                     | silent
  diverging      | auto    | crosses zero            | silent
  diverging      | auto    | all positive            | fires (auto = midpoint)
  diverging      | auto    | all positive, min < 0 < max pinned | silent
  sequential     | unset   | any                     | silent
  sequential     | 0       | any                     | fires (no neutral center)
  sequential     | auto    | crosses zero            | fires (no neutral center)
  inline stops   | auto    | all positive            | fires (auto = midpoint)
  inline stops   | unset   | any                     | silent (family unknown)

Exercised on every family that binds a gradient to a data field: heatmap,
geoshape, a color channel, and a table column scale.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.diagnostics import WARN_GRADIENT_HINGE_MISMATCH

from ...core._svg_render import render_board_svg_result

_DIV = "dbt-div-blue-red"
_SEQ = "dbt-seq-blue"
_SIGNED = [-5.0, 0.0, 5.0, 10.0]
_POSITIVE = [10.0, 20.0, 30.0, 40.0]


def _scale(palette: Any, hinge: Any, **extra: Any) -> str:
    lines = [f"palette: {palette}"]
    if hinge is not None:
        lines.append(f"hinge: {hinge}")
    lines += [f"{k}: {v}" for k, v in extra.items()]
    return "\n".join(lines)


def _indent(text: str, by: int) -> str:
    return "\n".join(" " * by + line for line in text.splitlines())


def _heatmap(scale: str, values: list[float]) -> str:
    rows = "\n".join(f"      - {{x: a{i}, y: b, v: {v}}}" for i, v in enumerate(values))
    return f"""
title: T
queries:
  q:
    type: values
    rows:
{rows}
charts:
  c:
    type: heatmap
    query: q
    x: x
    y: y
    color: v
    style:
      color:
        gradient:
{_indent(scale, 10)}
rows: [c]
"""


def _table(scale: str, values: list[float]) -> str:
    rows = "\n".join(f"      - {{k: a{i}, v: {v}}}" for i, v in enumerate(values))
    return f"""
title: T
queries:
  q:
    type: values
    rows:
{rows}
charts:
  c:
    type: table
    query: q
    style:
      columns:
        v:
          scale:
            background:
{_indent(scale, 14)}
rows: [c]
"""


def _fired(board: str) -> list[str]:
    result = render_board_svg_result(board)
    assert not result.chart_errors
    return [
        w.message
        for w in result.warnings
        if w.code == WARN_GRADIENT_HINGE_MISMATCH.code
    ]


CASES = [
    pytest.param(_DIV, None, _SIGNED, "middle of the data", id="diverging-no-hinge"),
    pytest.param(_DIV, 0, _SIGNED, None, id="diverging-hinge-0"),
    pytest.param(_DIV, "auto", _SIGNED, None, id="diverging-auto-crosses-zero"),
    pytest.param(_DIV, "auto", _POSITIVE, "midpoint", id="diverging-auto-positive"),
    pytest.param(_SEQ, None, _POSITIVE, None, id="sequential-no-hinge"),
    pytest.param(_SEQ, 0, _POSITIVE, "no neutral", id="sequential-hinge-0"),
    pytest.param(_SEQ, "auto", _SIGNED, "no neutral", id="sequential-auto"),
    pytest.param("['#ffffff', '#0088ff']", None, _POSITIVE, None, id="inline-no-hinge"),
    pytest.param(
        "['#cc0000', '#ffffff', '#0000cc']",
        "auto",
        _POSITIVE,
        "midpoint",
        id="inline-auto",
    ),
]


@pytest.mark.parametrize(("palette", "hinge", "values", "expect"), CASES)
@pytest.mark.parametrize("build", [_heatmap, _table], ids=["heatmap", "table"])
def test_accept_reject_table(
    build: Any, palette: Any, hinge: Any, values: list[float], expect: str | None
) -> None:
    messages = _fired(build(_scale(palette, hinge), values))
    if expect is None:
        assert messages == []
    else:
        assert len(messages) == 1
        assert expect in messages[0]
        assert "'v'" in messages[0]


def test_vega_diverging_scheme_without_hinge_fires() -> None:
    assert _fired(_heatmap(_scale("redblue", None), _SIGNED))


def test_vega_sequential_scheme_with_hinge_fires() -> None:
    assert _fired(_heatmap(_scale("viridis", 0), _POSITIVE))


def test_vega_sequential_scheme_without_hinge_is_silent() -> None:
    assert _fired(_heatmap(_scale("viridis", None), _POSITIVE)) == []


def test_pinned_domain_crossing_zero_keeps_auto_silent() -> None:
    board = _heatmap(_scale(_DIV, "auto", min=-10, max=10), _POSITIVE)
    assert _fired(board) == []


def test_geoshape_gradient_is_checked_against_its_value_field() -> None:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
    from dbt_charts.core.compile.resolve.chart.geo import _resolve_geoshape
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.render.warnings import WarningContext, gradient_hinge_mismatch

    from ...core._board_utils import make_test_resolved_board

    chart = _resolve_geoshape(
        GeoshapeChart.model_validate(
            {
                "id": "geo1",
                "type": "geoshape",
                "geo_source": "fake-geo-source",
                "projection": "mercator",
                "lookup": "state",
                "value": "pop",
                "style": {"color": {"gradient": {"palette": "redblue"}}},
            }
        ),
        [],
        resolve_chart_style_context(get_theme_style(get_default_theme_name())),
        800.0,
        None,
    )
    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={"geo1": chart}),
        chart_results={"geo1": [{"state": "CA", "pop": -1.0}]},
    )
    [warning] = gradient_hinge_mismatch.detect(ctx)
    assert warning.field == "pop"
    assert warning.path == "charts.geo1.style.color.gradient"


@pytest.mark.parametrize(
    ("scheme", "hinge", "fires"),
    [
        ("lightmulti", None, False),
        ("darkblue", 0, True),
        ("rainbow", None, False),
        ("category10", 0, False),
    ],
)
def test_every_vega_scheme_classifies_without_raising(
    scheme: str, hinge: object, fires: bool
) -> None:
    """Called directly: `run_all` swallows a detector that raises."""
    from dbt_charts.core.compile.models.chart.normalized import ScatterChart
    from dbt_charts.core.compile.models.chart.resolved._channel import (
        ResolvedStyleChannel,
    )
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.warnings import WarningContext, gradient_hinge_mismatch

    from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

    chart = make_test_resolved_chart(
        ScatterChart(id="c", type="scatter", query_name="q", title="", x="x", y="y"),
        [{"x": 1, "y": 2}],
    )
    scale = ResolvedScaleTargetConfig.model_validate(
        {"palette": scheme, "hinge": hinge}
    )
    object.__setattr__(
        chart,
        "resolved_channels",
        {
            "color": ResolvedStyleChannel(
                channel="color", mode="gradient", data_field="y", scale=scale
            )
        },
    )
    ctx = WarningContext(
        board_spec=make_test_resolved_board(charts={"c": chart}),
        chart_results={"c": [{"x": 1, "y": 2}, {"x": 2, "y": 4}]},
    )
    assert bool(gradient_hinge_mismatch.detect(ctx)) is fires
