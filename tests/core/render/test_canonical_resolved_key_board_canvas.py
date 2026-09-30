"""canonical_resolved_key must distinguish scopes whose chart canvas matches
but whose board canvas differs.

_require_resolved's canonical-reuse path serves the WHOLE cached
ResolvedChart, including .canvas, to any later placement whose
canonical_resolved_key collides. Two board scopes can share the same
chart_defaults.ink_canvas (an opaque charts.background occludes whatever
is beneath it) while their board_canvas genuinely differs -- and a
chart's own translucent style.background composites over board_canvas
(chart_context.py), so reusing the first placement's canvas for the
second paints the wrong pixels.
"""

from __future__ import annotations

import copy
import dataclasses
from unittest.mock import MagicMock

from dbt_charts.core.compile.models.chart.normalized.bar import BarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.execute.executor import Executor

_TEST_QUERY = SqlQuery(sql="SELECT 1", source="test")


def _translucent_bar_chart() -> BarChart:
    return BarChart(
        id="revenue",
        query=_TEST_QUERY,
        query_name="q",
        type="bar",
        x="x",
        y="y",
        style=BarChartStylePatch.model_validate(
            {"background": "rgba(255,255,255,0.5)"}
        ),
    )


def test_two_scopes_with_matching_chart_canvas_but_different_board_canvas_resolve_independently() -> (
    None
):
    from dbt_charts.core.colors import parse_css_color
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.compile.resolve.style.palette import ink_canvas
    from dbt_charts.core.render.layout_sizing import SizingRenderCtx, _require_resolved

    # Two ResolvedStyle objects with identical content but distinct identity
    # -- resolve_style_and_context's own no-patch fast path caches by
    # id(base), so a plain second call returns the SAME object; copy.copy is
    # the codebase's own idiom for "same content, different identity" here
    # (normalize/dispatch.py's compile_board_resolved_style cache-hit path
    # does exactly this for the same reason: two boards that merge to equal
    # styles must still hold two objects). Equal repr is what makes
    # canonical_resolved_key's value key collide, exactly like it would for
    # two equal-but-distinct unstyled nested board scopes.
    resolved_style_a = resolve_style(get_theme_style("stark"))
    resolved_style_b = copy.copy(resolved_style_a)
    assert resolved_style_a is not resolved_style_b
    assert repr(resolved_style_a) == repr(resolved_style_b)

    ctx_a = resolve_chart_style_context(get_theme_style("stark"))
    # A different board_canvas standing in for a scope nested under a
    # differently-tinted ancestor -- everything else about the scope
    # (theme, patches) is identical, which is exactly how two real unstyled
    # nested boards under different-colored ancestors would resolve.
    ctx_b = dataclasses.replace(ctx_a, board_canvas="#000000")
    assert ctx_a.board_canvas != ctx_b.board_canvas

    chart = _translucent_bar_chart()
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = []
    render_ctx = SizingRenderCtx(
        executor=executor,
        resolved_style=resolved_style_a,
        chart_style_context=ctx_a,
    )

    resolved_a = _require_resolved(
        render_ctx, chart, executor, {}, 400.0, resolved_style_a, ctx_a
    )
    resolved_b = _require_resolved(
        render_ctx, chart, executor, {}, 400.0, resolved_style_b, ctx_b
    )
    assert resolved_a is not None
    assert resolved_b is not None

    # Expected canvas: rgba(255,255,255,0.5) composited over each scope's
    # own board canvas, computed independently of _require_resolved's output.
    assert chart.style is not None
    local_background = chart.style.background
    assert local_background is not None
    expected_a = ink_canvas(local_background, ctx_a.board_canvas)
    expected_b = ink_canvas(local_background, ctx_b.board_canvas)
    assert expected_a != expected_b, (
        "test setup must give the two scopes genuinely different composited "
        "canvases, or this isn't exercising the collision at all"
    )
    _, _, _, alpha_a = parse_css_color(expected_a)
    _, _, _, alpha_b = parse_css_color(expected_b)
    assert alpha_a == alpha_b == 1.0

    assert resolved_a.canvas == expected_a
    assert resolved_b.canvas == expected_b, (
        f"scope B's chart must composite its translucent local background over "
        f"ITS OWN board_canvas ({ctx_b.board_canvas!r} -> {expected_b!r}), not "
        f"reuse scope A's cached canvas ({resolved_a.canvas!r}) via a colliding "
        f"canonical_resolved_key. Got {resolved_b.canvas!r}."
    )
