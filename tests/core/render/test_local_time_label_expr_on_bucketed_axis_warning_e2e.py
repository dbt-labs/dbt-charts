"""End-to-end: LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS surfaces from render()
for a chart whose authored ``axis_x.labels.expr`` calls ``timeFormat()`` on a
bucketed (UTC-midnight) temporal x-axis.

Proves the full seam -- compile() -> render() -> RenderResult.warnings --
not a hand-built WarningContext. This is also the regression pin for the
underlying mechanism: Vega-Lite's bucketed ``timeUnit`` transform produces
UTC-midnight ``Date`` values, but ``timeFormat`` (unlike ``utcFormat``) is a
local-time accessor -- it reads them in whatever zone the rendering process
is in. dbt charts does not rewrite the authored expression -- rewriting
arbitrary authored code is exactly the hidden-mutation the render layer's
no-magic policy forbids -- so the warning is the whole fix for the authoring
mistake. It is not a fix for host-dependent drift: that half is now handled
one layer down, by every composition root pinning the render process to
TZ=UTC before it renders (``dbt_charts._render_tz.pin_vl_convert_tz_utc``),
so this test (which renders inside the pinned pytest session) can no longer
observe the old "reads a tick one bucket early on a negative-offset machine"
symptom directly -- see
``test_authored_label_expr_local_time_pins_current_behavior.py`` for that,
proved against raw, unpinned vl-convert instead.
"""

from __future__ import annotations

from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render

_CODE = "WARN-LOCAL-TIME-LABEL-EXPR-ON-BUCKETED-AXIS"

_ROWS = [
    {"month": "2024-04-15 00:00:00", "val": 10},
    {"month": "2024-05-15 00:00:00", "val": 20},
    {"month": "2024-06-15 00:00:00", "val": 30},
]


_QUARTERLY_ROWS = [
    {"month": "2024-02-15 00:00:00", "val": 10},
    {"month": "2024-05-15 00:00:00", "val": 20},
    {"month": "2024-08-15 00:00:00", "val": 30},
]


_COLOR_ROWS = [
    {"month": "2024-04-15 00:00:00", "seg": "a", "val": 10},
    {"month": "2024-05-15 00:00:00", "seg": "a", "val": 20},
    {"month": "2024-06-15 00:00:00", "seg": "a", "val": 30},
    {"month": "2024-04-15 00:00:00", "seg": "b", "val": 15},
    {"month": "2024-05-15 00:00:00", "seg": "b", "val": 25},
    {"month": "2024-06-15 00:00:00", "seg": "b", "val": 35},
]


def _make_executor(
    board: object, query_registry: object, rows: list[dict[str, object]] | None = None
) -> Executor:
    ok = Mock()
    ok.is_success = True
    ok.data = rows if rows is not None else _ROWS
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    return Executor(
        board, adapter_registry=mock_registry, query_registry=query_registry
    )


def _board_yaml(*, time_unit: str | None, expr: str) -> str:
    axis_x = f"time_unit: {time_unit}\n        " if time_unit else ""
    return f"""
title: Probe
charts:
  area1:
    query: q
    type: area
    x: month
    y: val
    style:
      axis_x:
        {axis_x}labels:
          expr: "{expr}"
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - area1
"""


def _render(
    yaml_source: str, rows: list[dict[str, object]] | None = None
) -> list[object]:
    result = compile(yaml_source)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, rows)
    render_result = render(result.board, executor, format="svg")
    return list(render_result.warnings)


def test_fires_for_timeformat_on_yearmonth_bucketed_axis() -> None:
    yaml_source = _board_yaml(
        time_unit="yearmonth", expr="timeFormat(datum.value, '%b %Y')"
    )
    warnings = _render(yaml_source)
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes
    w = next(w for w in warnings if w.code == _CODE)
    assert w.chart == "area1"
    assert w.fix
    assert w.path == "charts.area1.style.axis_x.labels.expr"


def test_fires_for_timeformat_on_yearquarter_bucketed_axis() -> None:
    """A second grain -- the boundary arithmetic differs from yearmonth.

    Quarter-grained rows: ``_ROWS`` is monthly, and three months inside one
    quarter is a bucket collision, not one point per bucket.
    """
    yaml_source = _board_yaml(
        time_unit="yearquarter", expr="timeFormat(datum.value, 'Q%q %Y')"
    )
    warnings = _render(yaml_source, _QUARTERLY_ROWS)
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes


def test_silent_when_expr_already_uses_utcformat() -> None:
    """The UTC-safe fix the warning itself recommends must not also warn."""
    yaml_source = _board_yaml(
        time_unit="yearmonth",
        expr="utcFormat(toDate(datum.value), '%b %Y')",
    )
    warnings = _render(yaml_source)
    codes = {w.code for w in warnings}
    assert _CODE not in codes, codes


def test_silent_when_axis_is_not_bucketed() -> None:
    """timeFormat() on a continuous (non-bucketed) temporal axis is not the
    UTC-midnight hazard this detector targets -- no grain boundary exists."""
    yaml_source = _board_yaml(time_unit="none", expr="timeFormat(datum.value, '%b %Y')")
    warnings = _render(yaml_source)
    codes = {w.code for w in warnings}
    assert _CODE not in codes, codes


def test_fires_for_color_series_line_chart_with_endpoint_labels() -> None:
    """The theme default turns on endpoint_labels for a multi-series line
    chart, which wraps the emitted spec in an hconcat -- the x encoding
    lands at hconcat[0], not the spec root. A detector reading the root
    misses this, the ordinary shape for any time-series chart with a
    series color."""
    yaml_source = """
title: Probe
charts:
  line1:
    query: q
    type: line
    x: month
    y: val
    color: seg
    style:
      axis_x:
        time_unit: yearmonth
        labels:
          expr: "timeFormat(datum.value, '%b %Y')"
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - line1
"""
    warnings = _render(yaml_source, rows=_COLOR_ROWS)
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes


def test_silent_when_time_unit_is_monthofyear_time_part_unit() -> None:
    """monthofyear is a documented, non-bucketed time-part grain (VL's
    non-UTC `month` transform) -- not the UTC-midnight calendar bucketing
    this detector targets. Firing here would tell the author to switch to
    utcFormat(), which renders the wrong month under a positive UTC offset."""
    yaml_source = _board_yaml(
        time_unit="monthofyear", expr="timeFormat(datum.value, '%b')"
    )
    warnings = _render(yaml_source)
    codes = {w.code for w in warnings}
    assert _CODE not in codes, codes


def test_fires_for_bar_chart_with_todate_expr() -> None:
    """An ordinal bar chart whose label expr calls toDate() is the hazard.
    complete_ordinal_time_series rewrites every x value to a date-only ISO
    bucket string, so toDate() always receives a UTC-midnight-parseable value
    in the emitted spec -- regardless of whether the raw rows held datetime
    strings ('2024-04-15 00:00:00') or Python date objects. The detector uses
    _ordinal_bucket_key to mirror this normalization and fires correctly."""
    yaml_source = """
title: Probe
charts:
  bar1:
    query: q
    type: bar
    x: month
    y: val
    style:
      axis_x:
        time_unit: yearmonth
        labels:
          expr: "timeFormat(toDate(datum.value), '%b %Y')"
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - bar1
"""
    warnings = _render(
        yaml_source
    )  # _ROWS has datetime-space strings -- emitter rewrites
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes


def test_fires_for_auto_detected_yearmonth_grain_no_authored_time_unit() -> None:
    """When time_unit: is absent from axis_x, the detector auto-detects the
    grain from query data via resolve_cartesian_x_type. The warning's own doc
    says it fires 'whether that grain was authored via axis_x.time_unit or
    auto-detected from the query data' -- this test pins that claim.

    An area chart with yearmonth data resolves to vl_type='temporal' (line/area
    always temporal for bucketed grains) so the warning fires on bare
    timeFormat(datum.value, ...) without toDate()."""
    yaml_source = _board_yaml(time_unit=None, expr="timeFormat(datum.value, '%b %Y')")
    warnings = _render(
        yaml_source
    )  # _ROWS has three monthly rows -- yearmonth detected
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes


def test_silent_when_no_authored_expr() -> None:
    """The built-in smart labelExpr path is already UTC-safe -- must stay silent."""
    yaml_source = """
title: Probe
charts:
  area1:
    query: q
    type: area
    x: month
    y: val
    style:
      axis_x:
        time_unit: yearmonth
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - area1
"""
    warnings = _render(yaml_source)
    codes = {w.code for w in warnings}
    assert _CODE not in codes, codes
