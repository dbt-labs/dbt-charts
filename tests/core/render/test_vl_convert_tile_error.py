"""vl-convert JS TypeError must surface as a per-tile error, not a process abort.

Regression for dashboards 1291 and 955: unsupported layered Vega-Lite specs caused
vl_convert.vegalite_to_svg to raise, which propagated through the render pipeline as
an unhandled exception — aborting the whole process (exit 133) instead of recording a
per-tile failure and continuing.

Fix 1 (PR #1692): wrap vlc.vegalite_to_svg in render_vega_spec → ChartDataError.
Fix 2 (this PR): add ChartDataError to the sizing-pass exception handler in
layout_sizing.py so a vl-convert failure during the sizing pass falls back to
aspect-ratio estimate and the render pass gets to run render_chart_item, which
already catches ChartDataError and renders an error tile.

Without Fix 2: ChartDataError from the sizing pass propagates past layout_sizing,
gets wrapped by renderer.py as RenderError("Failed to render board: ..."),
and the whole dashboard PNG is never produced.
"""

from __future__ import annotations

import re
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.chart.rendering import render_chart_item

from .._svg_render import PINNED_RENDER_TIME


def _noop_register_fonts(vlc_module: object) -> None:
    pass


@pytest.fixture(autouse=True)
def reset_config_autouse():
    reset_config()
    yield
    reset_config()


_BOARD_YAML = """
queries:
  q:
    columns: [month, revenue, target]
    values:
      - [Jan, 100, 110]
      - [Feb, 120, 115]
charts:
  combo_chart:
    type: bar
    query: q
    x: month
    y: revenue
    layers:
      - type: line
        query: q
        x: month
        y: target
rows:
  - combo_chart
"""


def _make_fake_vlc(raise_on_svg: Exception) -> types.SimpleNamespace:
    """Return a fake vl_convert module whose vegalite_to_svg raises."""
    return types.SimpleNamespace(
        vegalite_to_svg=MagicMock(side_effect=raise_on_svg),
        register_font_directory=MagicMock(),
    )


def test_vl_convert_exception_surfaces_as_chart_data_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """render_vega_spec must re-raise vl_convert errors as ChartDataError."""
    from dbt_charts.core.render.converters import chart as chart_converter

    js_error = RuntimeError(
        "TypeError: Cannot read properties of undefined (reading 'marktype')"
    )
    fake_vlc = _make_fake_vlc(js_error)
    monkeypatch.setitem(sys.modules, "vl_convert", fake_vlc)

    monkeypatch.setattr(
        chart_converter, "register_vl_convert_fonts", _noop_register_fonts
    )

    with pytest.raises(ChartDataError, match="marktype"):
        chart_converter.render_vega_spec(
            {"layer": []},
            "svg",
            resolve_style(get_theme_style()),
            600,
            300,
            False,
            chart_id="chart",
        )


def test_vl_convert_exception_renders_error_tile_not_process_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vl_convert error on one tile must render an error card, not abort the process."""
    from dbt_charts.core.render.converters import chart as chart_converter

    result = compile(_BOARD_YAML)
    assert result.success and result.board is not None
    board = result.board

    js_error = RuntimeError(
        "TypeError: Cannot read properties of undefined (reading 'marktype')"
    )
    fake_vlc = _make_fake_vlc(js_error)
    monkeypatch.setitem(sys.modules, "vl_convert", fake_vlc)

    monkeypatch.setattr(
        chart_converter, "register_vl_convert_fonts", _noop_register_fonts
    )

    executor = MagicMock(spec=Executor)
    executor.execute_chart.return_value = [
        {"month": "Jan", "revenue": 100, "target": 110},
        {"month": "Feb", "revenue": 120, "target": 115},
    ]
    executor.execute_query.return_value = []

    rs = resolve_style(get_theme_style())
    ctx = resolve_chart_style_context(get_theme_style())
    svg, height = render_chart_item(
        resolve(board.charts["combo_chart"], [], chart_style_context=ctx),
        executor,
        variables={},
        available_width=600,
        available_height=300,
        resolved_style=rs,
        render_cache={},
    )

    # Must produce an error tile, not raise or abort.
    assert "dbt-chart-callout" in svg
    assert height > 0
    # The JS error message must appear in the rendered error card.
    svg_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", svg)).strip()
    assert "marktype" in svg_text


# ─────────────────────────────────────────────────────────────────────────────
# Full-dashboard render path regression
# ─────────────────────────────────────────────────────────────────────────────

# A two-chart board: good_chart (bar) and bad_chart (bar with a sentinel title).
# The sentinel title flows into the Vega-Lite spec so the mock can detect it.
_TWO_CHART_BOARD_YAML = """
queries:
  q:
    columns: [cat, val]
    values:
      - [A, 10]
      - [B, 20]
charts:
  good_chart:
    type: bar
    query: q
    x: cat
    y: val
  bad_chart:
    title: "BAD_CHART_MARKER"
    type: bar
    query: q
    x: cat
    y: val
rows:
  - good_chart
  - bad_chart
"""

_MINIMAL_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300">'
    '<g class="mark-rect role-mark"><rect x="0" y="0" width="40" height="30"/></g>'
    "</svg>"
)
_JS_ERROR = RuntimeError(
    "TypeError: Cannot read properties of undefined (reading 'marktype')"
)


def _selective_vlc(spec: dict[str, Any]) -> str:
    """Raise for bad_chart specs (identified by sentinel title); succeed otherwise.

    Transformation chain: authored YAML title "BAD_CHART_MARKER"
    → compile-time style.title.font.case: title (Gruber title-case algorithm)
    → "Bad_chart_marker" in the Vega-Lite spec title.text.
    If the default theme ever changes case: title to case: upper or case: none,
    the comparison below will silently stop matching and good_chart+bad_chart will
    both render normally — causing the "only 1 dbt-chart-callout" assertion to fail.
    To fix: update the expected string here or change the sentinel to already be
    title-cased (e.g. "Bad Chart Marker") so it is immune to the transformation.
    """
    title = spec.get("title", {})
    title_text = title.get("text") if isinstance(title, dict) else None
    if title_text == "Bad_chart_marker":
        raise _JS_ERROR
    return _MINIMAL_SVG


def test_full_dashboard_render_produces_output_with_error_block_not_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vl-convert failure during sizing must not abort the whole dashboard render.

    Regression for dashboard 1291/955: ChartDataError raised by render_vega_spec
    during the layout_sizing pass was NOT caught by the sizing-pass except clause
    (which only caught ExecutionError, KeyError, ValueError, OSError).  It propagated
    up through calculate_data_aware_layout as an unhandled ChartDataError, then was
    re-raised by renderer.py's broad except-Exception as RenderError("Failed to render
    board: ...") — aborting the full dashboard render and producing no PNG.

    The fix: add ChartDataError to the sizing-pass except tuple so the sizing
    falls back to aspect-ratio estimate, and the render pass can proceed to call
    render_chart_item, which already catches ChartDataError and renders an error tile.
    """
    from dbt_charts.core.render.converters import chart as chart_converter
    from dbt_charts.core.render.renderer import render

    result = compile(_TWO_CHART_BOARD_YAML)
    assert result.success and result.board is not None, result.errors

    fake_vlc = types.SimpleNamespace(
        vegalite_to_svg=_selective_vlc,
        register_font_directory=MagicMock(),
    )
    monkeypatch.setitem(sys.modules, "vl_convert", fake_vlc)

    monkeypatch.setattr(
        chart_converter, "register_vl_convert_fonts", _noop_register_fonts
    )

    executor = MagicMock(spec=Executor)
    executor.render_with_range_filters.return_value = None
    executor._query_errors = {}
    data = [{"cat": "A", "val": 10}, {"cat": "B", "val": 20}]
    executor.execute_chart.return_value = data
    executor.execute_query.return_value = data
    # No cache in play here — the real Executor.cache_hit_ats contract is an
    # empty list until a persistent-cache hit occurs, which never happens
    # against this mock's adapter stand-in.
    executor.cache_hit_ats = []
    executor.rendered_at = PINNED_RENDER_TIME

    # Must NOT raise — full dashboard SVG must be produced.
    svg = render(result.board, executor, format="svg", variables={}).output

    assert isinstance(svg, str), "render() must return a string, not raise"
    assert "<svg" in svg, "result must be SVG"
    # bad_chart must render as an error block.
    assert "dbt-chart-callout" in svg, (
        "bad_chart vl-convert failure must produce a per-tile error block"
    )
    # good_chart must render normally (no error class on its wrapper).
    # The good chart SVG is the minimal SVG we returned from the mock.
    assert svg.count("dbt-chart-callout") == 1, (
        "only bad_chart should have an error block; good_chart must render normally"
    )
