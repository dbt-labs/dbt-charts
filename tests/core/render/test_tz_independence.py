"""TZ-independence property test for emitted Vega-Lite specs.

The D-003a contract: specs we hand to ``vl_convert`` must render identically
regardless of the runtime ``TZ``.  This module renders representative
specimens in two subprocesses — ``TZ=UTC`` vs ``TZ=America/Los_Angeles`` —
and asserts the visible axis-tick text is identical.

Subprocess isolation is load-bearing: ``vl_convert.get_local_tz()`` caches
the TZ on first observation per process, so the two TZs must run in fresh
interpreters or the second render silently reuses the first run's cache and
the property test cannot detect leaks.

Specimens cover the two spec-level emit shapes that previously leaked the
runtime TZ:

* ``encoding.x.timeUnit`` on the temporal escape hatch — must be ``utc<grain>``
  so VL bucketing is UTC-aligned (Site A).
* ``axis.format`` d3-time-format strings on non-temporal axes — must route
  through ``utcFormat(toDate(datum.value), <fmt>)`` rather than
  ``formatType: "time"`` (d3-time-format under runtime-local TZ) (Site B).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    AxisYStylePatch,
    BarChartStylePatch,
    LineChartStylePatch,
    ScatterChartStylePatch,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_RS = resolve_style(get_theme_style())
_BOARD_CTX = resolve_chart_style_context(get_theme_style())

_SVG_TEXT_RE = re.compile(r"<text[^>]*>([^<]*)</text>", re.DOTALL)


def _render_in_subprocess(spec: dict[str, Any], tz: str) -> str:
    """Run ``vl_convert.vegalite_to_svg`` in a fresh interpreter with TZ set.

    Returns the SVG string. A fresh subprocess is required because
    ``vl_convert.get_local_tz()`` caches the resolved TZ for the process
    lifetime; running both TZs in the same interpreter would silently reuse
    the first observation's cache and mask the property under test.
    """
    script = textwrap.dedent(
        """
        import json, sys
        try:
            import vl_convert as vlc
        except ImportError:
            sys.exit(99)
        spec = json.loads(sys.stdin.read())
        sys.stdout.write(vlc.vegalite_to_svg(spec))
        """
    )
    env = {k: v for k, v in os.environ.items() if k != "TZ"}
    env["TZ"] = tz
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(spec),
        capture_output=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    if result.returncode == 99:
        pytest.skip("vl_convert not installed in test interpreter")
    assert result.returncode == 0, (
        f"subprocess render failed under TZ={tz}: stderr={result.stderr!r}"
    )
    return result.stdout


def _svg_texts(svg: str) -> tuple[str, ...]:
    """Return the visible ``<text>`` content from an SVG, in document order.

    Axis tick labels, titles, and legend entries land in ``<text>`` nodes.
    Any TZ-dependent leak shows up here as a different bucket / month / day
    string between the two runs.
    """
    return tuple(m.group(1) for m in _SVG_TEXT_RE.finditer(svg))


def _build_spec_temporal_escape_hatch_yearmonth() -> dict[str, Any]:
    """Temporal escape-hatch with ``time_unit: yearmonth`` — exercises Site A
    (``vl_time_unit`` returning ``utcyearmonth``). UTC-midnight rows that fall
    on month boundaries are the failure mode: ``2025-05-01T00:00:00Z`` rolls
    back to April on PDT before bucketing if ``timeUnit`` is ``yearmonth``.
    """
    chart = LineChart(
        id="tz_yearmonth",
        type="line",
        x="date",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {"type": "temporal", "time_unit": "yearmonth"}
                )
            }
        ),
    )
    data = [
        {"date": "2025-01-01T00:00:00Z", "value": 10},
        {"date": "2025-02-01T00:00:00Z", "value": 20},
        {"date": "2025-03-01T00:00:00Z", "value": 30},
        {"date": "2025-05-01T00:00:00Z", "value": 50},
    ]
    return generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )


def _build_spec_temporal_escape_hatch_yearquarter() -> dict[str, Any]:
    """Temporal escape-hatch with ``time_unit: yearquarter`` — same Site A
    hazard as the yearmonth specimen, but quarter-boundary arithmetic differs
    from month-boundary arithmetic, so this is not redundant coverage.
    ``2025-04-01T00:00:00Z`` (Q2 start) rolls back to Q1 on PDT before
    bucketing if the transform is not UTC-anchored.
    """
    chart = LineChart(
        id="tz_yearquarter",
        type="line",
        x="date",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {"type": "temporal", "time_unit": "yearquarter"}
                )
            }
        ),
    )
    data = [
        {"date": "2025-01-01T00:00:00Z", "value": 10},
        {"date": "2025-04-01T00:00:00Z", "value": 20},
        {"date": "2025-07-01T00:00:00Z", "value": 30},
        {"date": "2025-10-01T00:00:00Z", "value": 40},
    ]
    return generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )


def _build_spec_axis_x_time_format_on_ordinal() -> dict[str, Any]:
    """Ordinal-bucketed x axis with author-supplied d3-time-format —
    exercises Site B.x (``_utc_time_label_expr`` replacing
    ``formatType: "time"`` on the non-temporal-x leak path).
    """
    chart = BarChart(
        id="tz_x_fmt_ordinal",
        type="bar",
        x="month",
        y="revenue",
        style=BarChartStylePatch.model_validate(
            {"axis_x": AxisXStylePatch.model_validate({"labels": {"format": "%b %Y"}})}
        ),
    )
    data = [
        {"month": "2024-01", "revenue": 10},
        {"month": "2024-02", "revenue": 20},
        {"month": "2024-03", "revenue": 30},
    ]
    return generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )


def _build_spec_axis_y_time_format_on_scatter() -> dict[str, Any]:
    """Scatter chart with author-supplied d3-time-format on y — exercises
    Site B.y (``_utc_time_label_expr`` replacing ``formatType: "time"`` on
    the non-temporal-y leak path).

    y values are ISO month bands ("2024-01") so the type inference does
    not pre-route this through the temporal path; the leak block fires.
    """
    chart = ScatterChart(
        id="tz_y_fmt_scatter",
        type="scatter",
        x="value",
        y="month_str",
        style=ScatterChartStylePatch.model_validate(
            {"axis_y": AxisYStylePatch.model_validate({"labels": {"format": "%b %Y"}})}
        ),
    )
    data = [
        {"value": 1, "month_str": "2024-01"},
        {"value": 2, "month_str": "2024-02"},
        {"value": 3, "month_str": "2024-03"},
    ]
    return generate_vega_lite_spec(
        chart, data, board_style=_BOARD_RS, chart_style_context=_BOARD_CTX
    )


_SPEC_BUILDERS = (
    ("temporal_escape_hatch_yearmonth", _build_spec_temporal_escape_hatch_yearmonth),
    (
        "temporal_escape_hatch_yearquarter",
        _build_spec_temporal_escape_hatch_yearquarter,
    ),
    ("axis_x_time_format_on_ordinal", _build_spec_axis_x_time_format_on_ordinal),
    ("axis_y_time_format_on_scatter", _build_spec_axis_y_time_format_on_scatter),
)


@pytest.mark.parametrize(
    ("name", "build_spec"), _SPEC_BUILDERS, ids=[n for n, _ in _SPEC_BUILDERS]
)
def test_spec_is_tz_independent(name: str, build_spec) -> None:
    """The visible axis-tick text must match under TZ=UTC and TZ=PDT.

    This is the load-bearing D-003a contract: any future emit-time leak (a
    forgotten ``formatType: "time"``, a tooltip defaulting to
    ``timeFormat``, a layered encoding without a UTC labelExpr override)
    surfaces as a diff between the two SVGs.
    """
    spec = build_spec()
    svg_utc = _render_in_subprocess(spec, tz="UTC")
    svg_pdt = _render_in_subprocess(spec, tz="America/Los_Angeles")
    texts_utc = _svg_texts(svg_utc)
    texts_pdt = _svg_texts(svg_pdt)
    assert texts_utc == texts_pdt, (
        f"{name}: SVG text differs between TZ=UTC and TZ=America/Los_Angeles. "
        f"UTC={texts_utc!r} vs PDT={texts_pdt!r}. Look for a TZ-sensitive "
        f"emit in the spec — formatType:'time', timeFormat() without "
        f"toDate(), or a non-utc timeUnit."
    )


def test_property_test_covers_both_emit_shapes() -> None:
    """Smoke check that the specimen set covers both spec-level leak shapes.

    Site A: ``encoding.x.timeUnit`` carrying ``utc<grain>`` (temporal escape).
    Site B: ``axis.labelExpr`` (no ``formatType: "time"``) on x and y when an
    author-supplied d3-time-format string lands on a non-temporal axis.
    """
    site_a_spec = _build_spec_temporal_escape_hatch_yearmonth()
    site_a_x = site_a_spec.get("encoding", {}).get("x", {})
    assert site_a_x.get("timeUnit", "").startswith("utc"), site_a_x

    site_b_x_spec = _build_spec_axis_x_time_format_on_ordinal()
    site_b_x_axis = site_b_x_spec.get("encoding", {}).get("x", {}).get("axis", {})
    assert "labelExpr" in site_b_x_axis and "format" not in site_b_x_axis, site_b_x_axis
    assert site_b_x_axis.get("formatType") != "time", site_b_x_axis

    site_b_y_spec = _build_spec_axis_y_time_format_on_scatter()
    site_b_y_axis = site_b_y_spec.get("encoding", {}).get("y", {}).get("axis", {})
    assert "labelExpr" in site_b_y_axis and "format" not in site_b_y_axis, site_b_y_axis
    assert site_b_y_axis.get("formatType") != "time", site_b_y_axis
