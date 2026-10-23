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

import sys
import types
from unittest.mock import MagicMock

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError


def _noop_register_fonts(vlc_module: object) -> None:
    pass


@pytest.fixture(autouse=True)
def reset_config_autouse():
    reset_config()
    yield
    reset_config()


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


# ─────────────────────────────────────────────────────────────────────────────
# Full-dashboard render path regression
# ─────────────────────────────────────────────────────────────────────────────
