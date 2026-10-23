"""TDD tests for unified emit (all surfaces: axis, legend, mark).

After unification:
- axis → encoding.x.axis.* / encoding.y.axis.* (ALL variants incl. quantitative/band/global)
- legend → encoding.color.legend.* / encoding.size.legend.*
- mark → spec.mark.{...} extended object

Legitimately stays at config.*:
- config.view (no per-encoding equivalent)
- config.title, config.range.category, config.font
"""

from __future__ import annotations

import dataclasses

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

# ── Axis ──────────────────────────────────────────────────────────────────────


def test_axis_to_vl_mapper_emits_offset():
    """axis_to_vl() canonical mapper emits offset from resolved axis.ticks.offset."""
    reset_config()
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

    base = resolve_chart_style_context(get_theme_style())
    axis_y = resolved_axis_style(
        base, "axis_y", "quantitative", chart_type="", labeled=False
    )
    new_ticks = dataclasses.replace(axis_y.ticks, offset=99.0)
    new_axis = dataclasses.replace(axis_y, ticks=new_ticks)
    result = axis_to_vl(new_axis)
    assert result.get("offset") == 99.0


# ── Precedence-pin tests ───────────────────────────────────────────────────────


# ── Legend ────────────────────────────────────────────────────────────────────


# ── Mark ──────────────────────────────────────────────────────────────────────


# ── Legitimately stays at config.* ───────────────────────────────────────────
