from __future__ import annotations

from dbt_charts.core.compile.models.board.normalized import NO_TITLE_SHIFT
from dbt_charts.core.compile.models.chart.normalized import Chart

"""Regression tests: render cache must key by (chart_id, width, height).

When the same chart is referenced multiple times in a board at different sizes,
each slot must produce a correctly-sized SVG. Before the fix the key was
chart_id alone, so the first render was reused for all slots regardless of size.
"""

from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.board.normalized import LayoutItem
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
    PieChart,
    TableChart,
)
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render import layout_sizing

_TEST_QUERY = SqlQuery(sql="SELECT 1", source="test")


def _bar_chart(chart_id: str) -> Chart:
    return BarChart(
        id=chart_id,
        query=_TEST_QUERY,
        query_name="q",
        type="bar",
        x="x",
        y="y",
        style=None,
    )


def _pie_chart(chart_id: str) -> Chart:
    return PieChart(
        id=chart_id,
        query=_TEST_QUERY,
        query_name="q",
        type="pie",
        theta="value",
        color="series",
        style=None,
    )


def _resolved(chart: Chart, chart_style_context: ChartStyleContext) -> ResolvedChart:
    """Resolve a test chart through the render path with empty data."""
    return resolve(chart, [], chart_style_context=chart_style_context)


def test_repeated_pie_is_resolved_independently_for_each_slot_width() -> None:
    from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
    from dbt_charts.core.render.layout_sizing import SizingRenderCtx, _require_resolved

    chart = _pie_chart("shared_pie")
    rows = [
        {"series": name, "value": value}
        for name, value in [
            ("Enterprise Strategic Accounts", 22),
            ("Mid-Market Commercial", 20),
            ("SMB & Self-Serve", 18),
            ("Public Sector & Education", 16),
            ("Healthcare & Life Sciences", 14),
            ("Partners & Channel", 10),
        ]
    ]
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = rows
    rs, ctx = resolve_style_and_context(get_theme_style())
    render_ctx = SizingRenderCtx(
        executor=executor,
        resolved_style=rs,
        chart_style_context=ctx,
        pre_resolved={},
    )

    wide = _require_resolved(render_ctx, chart, executor, {}, 1128.0, rs, ctx)
    narrow = _require_resolved(render_ctx, chart, executor, {}, 376.0, rs, ctx)

    assert isinstance(wide, ResolvedPieChart)
    assert isinstance(narrow, ResolvedPieChart)
    assert wide.resolution_width == 1128.0
    assert narrow.resolution_width == 376.0
    assert narrow.style.title_font == wide.style.title_font
    assert wide.attached_table is None
    assert narrow.attached_table is not None


def test_repeated_non_pie_reuses_its_canonical_resolved_chart() -> None:
    from dbt_charts.core.render.layout_sizing import SizingRenderCtx, _require_resolved

    chart = _bar_chart("shared_bar")
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = [{"x": "A", "y": 1}]
    rs, ctx = resolve_style_and_context(get_theme_style())
    render_ctx = SizingRenderCtx(
        executor=executor, resolved_style=rs, chart_style_context=ctx
    )

    wide = _require_resolved(render_ctx, chart, executor, {}, 1128.0, rs, ctx)
    narrow = _require_resolved(render_ctx, chart, executor, {}, 376.0, rs, ctx)

    assert narrow is wide


def test_same_chart_different_widths_get_separate_cache_entries():
    """Same chart_id at two different widths → two separate cache entries.

    Pre-fix: second occurrence hits the first-width cache entry and is skipped.
    Post-fix: each (chart_id, width) gets its own entry in natural_heights and
    render_cache.
    """
    from dbt_charts.core.render.layout_sizing import (
        SizingRenderCtx,
        _make_data_aware_height_provider,
    )

    chart = _bar_chart("shared_chart")
    item_narrow = LayoutItem(type="chart", width=300.0, height=0.0, chart=chart)
    item_wide = LayoutItem(type="chart", width=600.0, height=0.0, chart=chart)

    executor = MagicMock(spec=Executor)
    rs, ctx = resolve_style_and_context(get_theme_style())
    render_ctx = SizingRenderCtx(
        executor=executor,
        resolved_style=rs,
        chart_style_context=ctx,
        pre_resolved={"shared_chart": _resolved(chart, ctx)},
    )

    render_calls: list[float] = []

    def fake_render(_resolved_v2, _executor, _variables, width, **kwargs):
        render_calls.append(width)
        return f'<svg viewBox="0 0 {width} 300"></svg>', width, 300.0, None

    with patch.object(layout_sizing, "_render_chart_to_svg", side_effect=fake_render):
        provider = _make_data_aware_height_provider(
            render_ctx,
            executor,
            variables={},
            card_padding=0.0,
            style_contexts={id(rs): ctx},
        )
        provider(item_narrow, 0.0, 0.0, 300.0, None, rs)
        provider(item_wide, 0.0, 0.0, 600.0, None, rs)

    # Both widths must have been rendered independently.
    assert render_calls == [300.0, 600.0], (
        f"Expected renders at both widths [300.0, 600.0], got {render_calls}. "
        "Pre-fix: second render is skipped because chart_id alone keys the cache."
    )
    assert ("shared_chart", 300.0) in render_ctx.natural_heights
    assert ("shared_chart", 600.0) in render_ctx.natural_heights
    assert any(
        k[0] == "shared_chart" and k[1] == 300.0 for k in render_ctx.render_cache
    )
    assert any(
        k[0] == "shared_chart" and k[1] == 600.0 for k in render_ctx.render_cache
    )


def test_same_chart_same_width_is_deduplicated():
    """Same chart_id at the same width → rendered once, second call uses natural_heights."""
    from dbt_charts.core.render.layout_sizing import (
        SizingRenderCtx,
        _make_data_aware_height_provider,
    )

    chart = _bar_chart("shared_chart")
    item1 = LayoutItem(type="chart", width=400.0, height=0.0, chart=chart)
    item2 = LayoutItem(type="chart", width=400.0, height=0.0, chart=chart)

    executor = MagicMock(spec=Executor)
    rs, ctx = resolve_style_and_context(get_theme_style())
    render_ctx = SizingRenderCtx(
        executor=executor,
        resolved_style=rs,
        chart_style_context=ctx,
        pre_resolved={"shared_chart": _resolved(chart, ctx)},
    )

    render_calls: list[float] = []

    def fake_render(_resolved_v2, _executor, _variables, width, **kwargs):
        render_calls.append(width)
        return f'<svg viewBox="0 0 {width} 300"></svg>', width, 300.0, None

    with patch.object(layout_sizing, "_render_chart_to_svg", side_effect=fake_render):
        provider = _make_data_aware_height_provider(
            render_ctx,
            executor,
            variables={},
            card_padding=0.0,
            style_contexts={id(rs): ctx},
        )
        h1 = provider(item1, 0.0, 0.0, 400.0, None, rs)
        h2 = provider(item2, 0.0, 0.0, 400.0, None, rs)

    assert render_calls == [400.0], "Same chart at same width must render only once"
    assert h1 == pytest.approx(h2)


def test_natural_height_tiny_overshoot_clamped_to_static_estimate():
    """Actual heights within 2px above static_estimate must be clamped down to it.

    When endpoint_labels are ON, the two-pass height correction returns
    actual_height = static_estimate + ε (fractional-pixel rounding). Without clamping,
    natural_heights differs by ε between ON and OFF states, causing row_height drift
    and producing slightly different final SVG heights when toggling endpoint_labels.
    """
    from dbt_charts.core.render.layout_sizing import (
        SizingRenderCtx,
        _make_data_aware_height_provider,
    )

    chart = _bar_chart("daily_activity")
    WIDTH = 600.0

    executor = MagicMock(spec=Executor)
    rs, ctx = resolve_style_and_context(get_theme_style())
    render_ctx = SizingRenderCtx(
        executor=executor,
        resolved_style=rs,
        chart_style_context=ctx,
        pre_resolved={"daily_activity": _resolved(chart, ctx)},
    )

    captured_static_estimate: list[float] = []

    def fake_render(_resolved_v2, _executor, _variables, width, height=None, **kwargs):
        # Simulate two-pass correction returning static_estimate + 0.4 (fractional overshoot)
        static_estimate = height or 0.0
        captured_static_estimate.append(static_estimate)
        overshoot = static_estimate + 0.4
        return (
            f'<svg width="{width}" height="{overshoot}"></svg>',
            width,
            overshoot,
            None,
        )

    with patch.object(layout_sizing, "_render_chart_to_svg", side_effect=fake_render):
        provider = _make_data_aware_height_provider(
            render_ctx,
            executor,
            variables={},
            card_padding=0.0,
            style_contexts={id(rs): ctx},
        )
        item = LayoutItem(type="chart", width=WIDTH, height=0.0, chart=chart)
        provider(item, 0.0, 0.0, WIDTH, None, rs)

    assert len(captured_static_estimate) == 1
    static_estimate = captured_static_estimate[0]
    stored = render_ctx.natural_heights.get(("daily_activity", WIDTH))
    assert stored == pytest.approx(static_estimate), (
        f"Tiny overshoot (static_estimate + 0.4 = {static_estimate + 0.4}) must be clamped "
        f"to static_estimate ({static_estimate}); got {stored}. "
        "Pre-fix: natural_heights stores static_estimate + 0.4, causing row_height drift "
        "between endpoint_labels ON and OFF."
    )


def test_natural_height_large_undershoot_is_not_clamped_up():
    """A pie chart rendered much shorter than static_estimate must keep its real height.

    Arc-attached-table "right" placement (wheel beside a "Too small to label" swatch
    table) composes a short/wide SVG whose natural height can be well below the
    generic aspect-ratio static_estimate for a pie chart. The tiny-overshoot clamp
    (`actual_height - static_estimate <= 2.0`) was unconditional on the low side, so
    it also snapped large undershoots up to static_estimate — reserving a layout slot
    far taller than the actual composed content and leaving a dead-zone of whitespace
    below it. Undershoots beyond the rounding-noise band must be trusted as-is.
    """
    from dbt_charts.core.render.layout_sizing import (
        SizingRenderCtx,
        _make_data_aware_height_provider,
    )

    chart = _pie_chart("plan_mix")
    WIDTH = 1180.0

    executor = MagicMock(spec=Executor)
    rs, ctx = resolve_style_and_context(get_theme_style())
    render_ctx = SizingRenderCtx(
        executor=executor,
        resolved_style=rs,
        chart_style_context=ctx,
        pre_resolved={"plan_mix": _resolved(chart, ctx)},
    )

    captured_static_estimate: list[float] = []

    def fake_render(_resolved_v2, _executor, _variables, width, height=None, **kwargs):
        # Simulate the attached-table "right" placement composing a much
        # shorter SVG than the generic aspect-ratio estimate would predict.
        static_estimate = height or 0.0
        captured_static_estimate.append(static_estimate)
        composed_height = max(static_estimate - 150.0, 1.0)
        return (
            f'<svg width="{width}" height="{composed_height}"></svg>',
            width,
            composed_height,
            None,
        )

    with patch.object(layout_sizing, "_render_chart_to_svg", side_effect=fake_render):
        provider = _make_data_aware_height_provider(
            render_ctx,
            executor,
            variables={},
            card_padding=0.0,
            style_contexts={id(rs): ctx},
        )
        item = LayoutItem(type="chart", width=WIDTH, height=0.0, chart=chart)
        provider(item, 0.0, 0.0, WIDTH, None, rs)

    assert len(captured_static_estimate) == 1
    static_estimate = captured_static_estimate[0]
    expected = max(static_estimate - 150.0, 1.0)
    stored = render_ctx.natural_heights.get(("plan_mix", WIDTH))
    assert stored == pytest.approx(expected), (
        f"Composed height {expected} (static_estimate {static_estimate} - 150) must be "
        f"kept as-is, not snapped up to static_estimate; got {stored}. "
        "Pre-fix: the clamp treats any undershoot the same as a tiny rounding "
        "overshoot and inflates the stored natural height, reserving a layout slot "
        "much taller than the actual composed content."
    )


def test_align_cols_heights_same_chart_different_heights_get_separate_entries():
    """Two cols rows with same chart at same width but different target heights
    must not overwrite each other's cache entry.

    Pre-fix: both rows write to render_cache["chart_id"], second overwrites first.
    Post-fix: keys are (chart_id, width, height) — no collision.
    """
    from dbt_charts.core.render.layout_sizing import (
        SizingRenderCtx,
        _align_cols_heights,
    )

    chart = _bar_chart("shared")
    WIDTH = 400.0
    NATURAL_H = 200.0
    ROW1_TARGET = 300.0
    ROW2_TARGET = 500.0

    item1 = LayoutItem(type="chart", width=WIDTH, height=0.0, chart=chart)
    item2 = LayoutItem(type="chart", width=WIDTH, height=0.0, chart=chart)

    executor = MagicMock(spec=Executor)
    rs, ctx = resolve_style_and_context(get_theme_style())

    def fake_render(
        _resolved_v2, _executor, _variables, width, *, height=None, **kwargs
    ):
        h = height or NATURAL_H
        return f'<svg viewBox="0 0 {width} {h}"></svg>', width, h, None

    with patch.object(layout_sizing, "_render_chart_to_svg", side_effect=fake_render):
        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=rs,
            chart_style_context=ctx,
            render_cache={
                ("shared", WIDTH, NATURAL_H): (
                    f'<svg viewBox="0 0 {WIDTH} {NATURAL_H}"></svg>',
                    NATURAL_H,
                ),
            },
            natural_heights={("shared", WIDTH): NATURAL_H},
            pre_resolved={"shared": _resolved(chart, ctx)},
        )
        # Simulate two separate cols rows calling _align_cols_heights.
        _align_cols_heights([item1], ROW1_TARGET, render_ctx)
        _align_cols_heights([item2], ROW2_TARGET, render_ctx)

    key1 = ("shared", WIDTH, ROW1_TARGET, NO_TITLE_SHIFT)
    key2 = ("shared", WIDTH, ROW2_TARGET, NO_TITLE_SHIFT)
    assert key1 in render_ctx.render_cache, "Row 1 entry must exist"
    assert key2 in render_ctx.render_cache, "Row 2 entry must exist"
    _, h1 = render_ctx.render_cache[key1]
    _, h2 = render_ctx.render_cache[key2]
    assert h1 == pytest.approx(ROW1_TARGET)
    assert h2 == pytest.approx(ROW2_TARGET)


class TestGridRowAlignment:
    """Grid layout: Vega charts in the same grid row must be height-aligned.

    Bug: _align_all_cols_in_tree only handled 'cols' layouts. Shorter Vega charts
    in a grid row rendered at their natural (shorter) height instead of the row max.
    """

    def test_shorter_vega_chart_in_grid_row_gets_alignment_cache_entry(self):
        """Shorter Vega chart in same grid row as taller chart is re-rendered at
        the row target height and stored in the render cache."""
        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_all_cols_in_tree,
        )

        chart_tall = _bar_chart("tall")
        chart_short = _bar_chart("short")

        ROW_HEIGHT = 300.0
        NATURAL_SHORT_H = 200.0

        WIDTH = 400.0

        # After sizing, both items in grid row 0 share the same item.height
        item_tall = LayoutItem(
            type="chart",
            width=WIDTH,
            height=ROW_HEIGHT,
            chart=chart_tall,
            row=0,
            col=0,
        )
        item_short = LayoutItem(
            type="chart",
            width=WIDTH,
            height=ROW_HEIGHT,
            chart=chart_short,
            row=0,
            col=1,
        )

        executor = MagicMock(spec=Executor)
        rs, ctx = resolve_style_and_context(get_theme_style())

        def fake_render(
            _resolved_v2, _executor, _variables, width, *, height=None, **kwargs
        ):
            h = height if height is not None else NATURAL_SHORT_H
            return (
                f'<svg viewBox="0 0 {width} {h}"></svg>',
                float(width),
                float(h),
                None,
            )

        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=rs,
            chart_style_context=ctx,
            render_cache={
                ("tall", WIDTH, ROW_HEIGHT): (
                    f'<svg viewBox="0 0 {WIDTH} {ROW_HEIGHT}"></svg>',
                    ROW_HEIGHT,
                ),
                ("short", WIDTH, NATURAL_SHORT_H): (
                    f'<svg viewBox="0 0 {WIDTH} {NATURAL_SHORT_H}"></svg>',
                    NATURAL_SHORT_H,
                ),
            },
            natural_heights={
                ("tall", WIDTH): ROW_HEIGHT,
                ("short", WIDTH): NATURAL_SHORT_H,
            },
            pre_resolved={
                "tall": _resolved(chart_tall, ctx),
                "short": _resolved(chart_short, ctx),
            },
        )

        layout = Layout.model_validate(
            {"type": "grid", "items": [item_tall, item_short]}
        )

        with patch.object(
            layout_sizing, "_render_chart_to_svg", side_effect=fake_render
        ):
            _align_all_cols_in_tree(layout, render_ctx)

        assert (
            "short",
            WIDTH,
            ROW_HEIGHT,
            NO_TITLE_SHIFT,
        ) in render_ctx.render_cache, (
            "Grid alignment cache entry missing for the shorter chart. "
            "_align_all_cols_in_tree must extend alignment to grid rows."
        )

    def test_chart_already_at_row_height_is_not_re_rendered(self):
        """The tallest chart (already at row height) must not trigger a re-render."""
        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_all_cols_in_tree,
        )

        chart_a = _bar_chart("a")
        chart_b = _bar_chart("b")
        ROW_HEIGHT = 300.0
        WIDTH = 400.0

        item_a = LayoutItem(
            type="chart", width=WIDTH, height=ROW_HEIGHT, chart=chart_a, row=0, col=0
        )
        item_b = LayoutItem(
            type="chart", width=WIDTH, height=ROW_HEIGHT, chart=chart_b, row=0, col=1
        )

        executor = MagicMock(spec=Executor)
        rs, ctx = resolve_style_and_context(get_theme_style())

        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=rs,
            chart_style_context=ctx,
            render_cache={
                ("a", WIDTH, ROW_HEIGHT): (
                    f'<svg viewBox="0 0 {WIDTH} {ROW_HEIGHT}"></svg>',
                    ROW_HEIGHT,
                ),
                ("b", WIDTH, ROW_HEIGHT): (
                    f'<svg viewBox="0 0 {WIDTH} {ROW_HEIGHT}"></svg>',
                    ROW_HEIGHT,
                ),
            },
            natural_heights={("a", WIDTH): ROW_HEIGHT, ("b", WIDTH): ROW_HEIGHT},
        )

        layout = Layout.model_validate({"type": "grid", "items": [item_a, item_b]})

        re_renders: list[str] = []

        def fake_render(_resolved_v2, _executor, _variables, width, **kwargs):
            re_renders.append(_resolved_v2.id)
            return (
                f'<svg viewBox="0 0 {WIDTH} {ROW_HEIGHT}"></svg>',
                float(WIDTH),
                ROW_HEIGHT,
                None,
            )

        with patch.object(
            layout_sizing, "_render_chart_to_svg", side_effect=fake_render
        ):
            _align_all_cols_in_tree(layout, render_ctx)

        assert re_renders == [], (
            f"Charts already at row height must not be re-rendered; got {re_renders}"
        )

    def test_items_in_different_grid_rows_are_not_cross_aligned(self):
        """Items in different grid rows must not be aligned against each other."""
        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_all_cols_in_tree,
        )

        chart_r0 = _bar_chart("r0")
        chart_r1 = _bar_chart("r1")
        ROW0_H = 300.0
        ROW1_H = 200.0
        WIDTH = 400.0

        item_r0 = LayoutItem(
            type="chart", width=WIDTH, height=ROW0_H, chart=chart_r0, row=0, col=0
        )
        item_r1 = LayoutItem(
            type="chart", width=WIDTH, height=ROW1_H, chart=chart_r1, row=1, col=0
        )

        executor = MagicMock(spec=Executor)
        rs, ctx = resolve_style_and_context(get_theme_style())

        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=rs,
            chart_style_context=ctx,
            render_cache={
                ("r0", WIDTH, ROW0_H): (
                    f'<svg viewBox="0 0 {WIDTH} {ROW0_H}"></svg>',
                    ROW0_H,
                ),
                ("r1", WIDTH, ROW1_H): (
                    f'<svg viewBox="0 0 {WIDTH} {ROW1_H}"></svg>',
                    ROW1_H,
                ),
            },
            natural_heights={("r0", WIDTH): ROW0_H, ("r1", WIDTH): ROW1_H},
        )

        layout = Layout.model_validate({"type": "grid", "items": [item_r0, item_r1]})

        re_renders: list[str] = []

        def fake_render(_resolved_v2, _executor, _variables, width, **kwargs):
            re_renders.append(_resolved_v2.id)
            return "<svg/>", float(WIDTH), ROW0_H, None

        with patch.object(
            layout_sizing, "_render_chart_to_svg", side_effect=fake_render
        ):
            _align_all_cols_in_tree(layout, render_ctx)

        assert re_renders == [], "Single-item rows must not trigger re-renders"


class TestColsBoardItemAlignment:
    """Cols layout: charts must align to max natural chart body height, not max item height.

    Bug: _align_all_cols_in_tree used max(item.height) as the alignment target.
    When a board item (chart + text caption) sat beside a direct chart, the direct
    chart was re-rendered at the board's total height (chart + text), not the chart
    body height.  _col_chart_body_target now looks into board items for chart natural
    heights; _align_all_cols_in_tree uses that as the target and updates item.height
    on direct chart items before delegating to _align_cols_heights.
    """

    def test_direct_chart_height_updated_to_natural_not_board_total(self):
        """Direct chart item.height is set to max natural chart height, not board-item.height.

        Pre-fix: target = max(item.height) = inflated board total; direct chart
        re-rendered at the wrong height.
        Post-fix: target = max natural chart body height; charts already at their
        natural height are not re-rendered.
        """
        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_all_cols_in_tree,
        )

        chart_a = _bar_chart("a")
        chart_b = _bar_chart("b")
        NATURAL_H = 300.0
        INFLATED_H = 500.0  # item.height after sizing next to a board with text
        WIDTH = 400.0

        item_a = LayoutItem(type="chart", width=WIDTH, height=INFLATED_H, chart=chart_a)
        item_b = LayoutItem(type="chart", width=WIDTH, height=INFLATED_H, chart=chart_b)

        executor = MagicMock(spec=Executor)
        rs, ctx = resolve_style_and_context(get_theme_style())

        render_calls: list[tuple[str, float]] = []

        def fake_render(
            _resolved_v2, _executor, _variables, width, *, height=None, **kwargs
        ):
            h: float = height if height is not None else NATURAL_H
            render_calls.append((_resolved_v2.id, h))
            return (
                f'<svg viewBox="0 0 {WIDTH} {h}"></svg>',
                WIDTH,
                h,
                None,
            )

        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=rs,
            chart_style_context=ctx,
            render_cache={
                ("a", WIDTH, NATURAL_H): (
                    f'<svg viewBox="0 0 {WIDTH} {NATURAL_H}"></svg>',
                    NATURAL_H,
                ),
                ("b", WIDTH, NATURAL_H): (
                    f'<svg viewBox="0 0 {WIDTH} {NATURAL_H}"></svg>',
                    NATURAL_H,
                ),
            },
            natural_heights={
                ("a", WIDTH): NATURAL_H,
                ("b", WIDTH): NATURAL_H,
            },
        )

        cols_layout = Layout.model_validate({"type": "cols", "items": [item_a, item_b]})

        with patch.object(
            layout_sizing, "_render_chart_to_svg", side_effect=fake_render
        ):
            _align_all_cols_in_tree(cols_layout, render_ctx)

        assert item_a.height == pytest.approx(NATURAL_H), (
            f"Expected item_a.height={NATURAL_H}, got {item_a.height}. "
            "Pre-fix: item.height stays at inflated board-total height."
        )
        assert item_b.height == pytest.approx(NATURAL_H)
        assert render_calls == [], (
            f"Charts at natural height must not be re-rendered; got {render_calls}. "
            f"Pre-fix: both charts re-rendered at inflated height {INFLATED_H}."
        )

    def test_col_chart_body_target_finds_natural_height_inside_board_item(self):
        """_col_chart_body_target returns max natural chart height, including sub-charts inside board items."""
        from unittest.mock import MagicMock as _MagicMock

        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _col_chart_body_target,
        )

        chart_inside = _bar_chart("inside")
        chart_direct = _bar_chart("direct")
        WIDTH = 400.0
        BOARD_NATURAL_H = 350.0
        DIRECT_NATURAL_H = 300.0

        sub_item = LayoutItem(
            type="chart", width=WIDTH, height=BOARD_NATURAL_H, chart=chart_inside
        )

        inner_layout = Layout.model_validate({"type": "rows", "items": [sub_item]})
        mock_board = _MagicMock()
        mock_board.layout = inner_layout

        board_item = LayoutItem.model_construct(
            type="board", width=WIDTH, height=500.0, board=mock_board, chart=None
        )
        direct_item = LayoutItem(
            type="chart", width=WIDTH, height=500.0, chart=chart_direct
        )

        rs, ctx = resolve_style_and_context(get_theme_style())
        render_ctx = SizingRenderCtx(
            executor=None,
            resolved_style=rs,
            chart_style_context=ctx,
            natural_heights={
                ("inside", WIDTH): BOARD_NATURAL_H,
                ("direct", WIDTH): DIRECT_NATURAL_H,
            },
        )

        target = _col_chart_body_target([board_item, direct_item], render_ctx)

        assert target == pytest.approx(BOARD_NATURAL_H), (
            f"Expected target={BOARD_NATURAL_H} (max of inside={BOARD_NATURAL_H} and "
            f"direct={DIRECT_NATURAL_H}), got {target}. "
            "_col_chart_body_target must look inside board items for chart natural heights."
        )

    def test_col_chart_body_target_uses_board_height_for_all_vega_rows_board(self):
        """_col_chart_body_target uses board item.height for an all-Vega rows board.

        When a cols row has a direct chart beside a rows-layout board whose items
        are ALL Vega charts (no text/non-chart items), the alignment target must
        equal the board's precomputed total cell height, not just the tallest
        individual sub-chart.

        Scenario: board has 2 stacked bar charts (120px each, plus a gap →
        total 248px). Direct chart renders at 200px naturally. Without this fix,
        chart_target = max(120, 200) = 200px — both columns look different heights.
        With the fix, chart_target = max(board.item.height=248, 200) = 248px, and
        the direct chart is re-rendered to fill the same cell height as the board.
        """
        from unittest.mock import MagicMock as _MagicMock

        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _col_chart_body_target,
        )

        chart_a = _bar_chart("pipeline")
        chart_b = _bar_chart("cumulative")
        chart_direct = _bar_chart("daily_activity")
        WIDTH = 400.0
        SUB_H = 120.0
        GAP = 8.0
        BOARD_TOTAL_H = SUB_H + SUB_H + GAP  # 248px
        DIRECT_NATURAL_H = 200.0

        sub_item1 = LayoutItem(type="chart", width=WIDTH, height=SUB_H, chart=chart_a)
        sub_item2 = LayoutItem(type="chart", width=WIDTH, height=SUB_H, chart=chart_b)

        inner_layout = Layout.model_validate(
            {"type": "rows", "items": [sub_item1, sub_item2]}
        )
        mock_board = _MagicMock()
        mock_board.layout = inner_layout

        # board item.height = BOARD_TOTAL_H (set by _calculate_cols_dimensions;
        # board is the tallest column since 248 > 200)
        board_item = LayoutItem.model_construct(
            type="board",
            width=WIDTH,
            height=BOARD_TOTAL_H,
            board=mock_board,
            chart=None,
        )
        direct_item = LayoutItem(
            type="chart", width=WIDTH, height=BOARD_TOTAL_H, chart=chart_direct
        )

        rs, ctx = resolve_style_and_context(get_theme_style())
        render_ctx = SizingRenderCtx(
            executor=None,
            resolved_style=rs,
            chart_style_context=ctx,
            natural_heights={
                ("pipeline", WIDTH): SUB_H,
                ("cumulative", WIDTH): SUB_H,
                ("daily_activity", WIDTH): DIRECT_NATURAL_H,
            },
        )

        target = _col_chart_body_target([board_item, direct_item], render_ctx)

        assert target == pytest.approx(BOARD_TOTAL_H), (
            f"Expected target={BOARD_TOTAL_H} (board total height) so the direct chart "
            f"fills the same cell as the stacked board, got {target}. "
            "Pre-fix: target = max(sub_h=120, direct=200) = 200 — columns misalign."
        )

    def test_col_chart_body_target_mixed_board_still_uses_natural_heights(self):
        """Board with text + chart: alignment uses chart natural height, not board total.

        Regression guard for the original fix: a board item whose rows layout
        contains non-chart items (e.g. a text caption) must contribute only its
        chart natural height, not item.height (which includes the text overhead).
        """
        from unittest.mock import MagicMock as _MagicMock

        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _col_chart_body_target,
        )

        chart_inside = _bar_chart("trend")
        chart_direct = _bar_chart("kpi_line")
        WIDTH = 400.0
        CHART_H = 300.0
        TEXT_H = 80.0
        BOARD_TOTAL_H = CHART_H + TEXT_H + 8.0  # 388px
        DIRECT_NATURAL_H = 250.0

        sub_chart_item = LayoutItem(
            type="chart", width=WIDTH, height=CHART_H, chart=chart_inside
        )
        # text/non-chart item inside the board
        sub_text_item = LayoutItem.model_construct(
            type="board", width=WIDTH, height=TEXT_H, chart=None, board=None
        )

        inner_layout = Layout.model_validate(
            {"type": "rows", "items": [sub_chart_item, sub_text_item]}
        )
        mock_board = _MagicMock()
        mock_board.layout = inner_layout

        board_item = LayoutItem.model_construct(
            type="board",
            width=WIDTH,
            height=BOARD_TOTAL_H,
            board=mock_board,
            chart=None,
        )
        direct_item = LayoutItem(
            type="chart", width=WIDTH, height=BOARD_TOTAL_H, chart=chart_direct
        )

        rs, ctx = resolve_style_and_context(get_theme_style())
        render_ctx = SizingRenderCtx(
            executor=None,
            resolved_style=rs,
            chart_style_context=ctx,
            natural_heights={
                ("trend", WIDTH): CHART_H,
                ("kpi_line", WIDTH): DIRECT_NATURAL_H,
            },
        )

        target = _col_chart_body_target([board_item, direct_item], render_ctx)

        assert target == pytest.approx(CHART_H), (
            f"Expected target={CHART_H} (chart natural height, not board total {BOARD_TOTAL_H}). "
            "Mixed-content rows boards must contribute only their chart natural height."
        )

    def test_pie_chart_does_not_grow_to_cols_alignment_target(self):
        """Pie chart in cols stays at natural height when row target is taller."""
        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_all_cols_in_tree,
        )

        pie_chart = _pie_chart("pie_chart")
        bar_chart = _bar_chart("bar_chart")
        WIDTH = 400.0
        PIE_NATURAL_H = 300.0
        BAR_NATURAL_H = 420.0
        INFLATED_H = 500.0

        pie_item = LayoutItem(
            type="chart", width=WIDTH, height=INFLATED_H, chart=pie_chart
        )
        bar_item = LayoutItem(
            type="chart", width=WIDTH, height=INFLATED_H, chart=bar_chart
        )
        layout = Layout.model_validate({"type": "cols", "items": [pie_item, bar_item]})

        executor = MagicMock(spec=Executor)
        rs, ctx = resolve_style_and_context(get_theme_style())
        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=rs,
            chart_style_context=ctx,
            natural_heights={
                ("pie_chart", WIDTH): PIE_NATURAL_H,
                ("bar_chart", WIDTH): BAR_NATURAL_H,
            },
            render_cache={
                ("pie_chart", WIDTH, PIE_NATURAL_H): ("<svg/>", PIE_NATURAL_H),
                ("bar_chart", WIDTH, BAR_NATURAL_H): ("<svg/>", BAR_NATURAL_H),
            },
        )

        render_calls: list[tuple[str, float | None]] = []

        def fake_render(
            _resolved_v2, _executor, _variables, width, *, height=None, **kwargs
        ):
            render_calls.append((_resolved_v2.id, height))
            return "<svg/>", WIDTH, float(height or 0.0), None

        with patch.object(
            layout_sizing, "_render_chart_to_svg", side_effect=fake_render
        ):
            _align_all_cols_in_tree(layout, render_ctx)

        assert pie_item.height == pytest.approx(PIE_NATURAL_H), (
            f"Pie height must stay natural ({PIE_NATURAL_H}), got {pie_item.height}."
        )
        assert bar_item.height == pytest.approx(BAR_NATURAL_H)
        assert render_calls == [], (
            f"Neither chart should re-render when already at effective targets, got {render_calls}."
        )


class TestAlignBoardChartsRowsLayout:
    """_align_board_charts behavior depends on the board's inner layout type and chart count.

    - cols board: all side-by-side charts are equalized (always).
    - rows board, exactly 1 Vega chart: the chart may be expanded (safe — it's alone).
    - rows board, 2+ Vega charts: stacked charts must not be equalized — it overflows the board.
    """

    def test_rows_layout_multi_chart_board_not_re_rendered(self):
        """Stacked charts in a rows-layout board are left at their natural heights."""
        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_board_charts,
        )

        chart_tall = _bar_chart("tall")
        chart_short = _bar_chart("short")
        WIDTH = 400.0
        TALL_H = 400.0
        SHORT_H = 200.0

        item_tall = LayoutItem(
            type="chart", width=WIDTH, height=TALL_H, chart=chart_tall
        )
        item_short = LayoutItem(
            type="chart", width=WIDTH, height=SHORT_H, chart=chart_short
        )

        inner_layout = Layout.model_validate(
            {
                "type": "rows",
                "items": [item_tall, item_short],
                "content_height": TALL_H + SHORT_H + 16.0,
            }
        )

        executor = MagicMock(spec=Executor)
        rs, ctx = resolve_style_and_context(get_theme_style())

        render_calls: list[str] = []

        def fake_render(_resolved_v2, _executor, _variables, width, **kwargs):
            render_calls.append(_resolved_v2.id)
            return "<svg/>", WIDTH, TALL_H, None

        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=rs,
            chart_style_context=ctx,
            render_cache={
                ("tall", WIDTH, TALL_H): ("<svg/>", TALL_H),
                ("short", WIDTH, SHORT_H): ("<svg/>", SHORT_H),
            },
            natural_heights={
                ("tall", WIDTH): TALL_H,
                ("short", WIDTH): SHORT_H,
            },
        )

        # Build the board item via model_construct so we can pass a real Layout.
        from unittest.mock import MagicMock as _MM

        mock_board = _MM()
        mock_board.layout = inner_layout
        board_item = LayoutItem.model_construct(
            type="board",
            width=WIDTH,
            height=TALL_H + SHORT_H + 16.0,
            board=mock_board,
            chart=None,
        )

        with patch.object(
            layout_sizing, "_render_chart_to_svg", side_effect=fake_render
        ):
            _align_board_charts(board_item, TALL_H, render_ctx)

        assert render_calls == [], (
            f"Charts in a rows-layout board must not be re-rendered; got {render_calls}. "
            "Pre-fix: the shorter chart is re-rendered at the taller chart's height."
        )
        assert item_short.height == pytest.approx(SHORT_H), (
            f"item_short.height must stay {SHORT_H}, got {item_short.height}."
        )

    def test_rows_layout_single_chart_board_is_expanded(self):
        """A rows-layout board with one Vega chart and non-chart items (e.g. text caption)
        expands the chart toward the outer target, subject to the board's available capacity.
        """
        from unittest.mock import MagicMock as _MM

        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_board_charts,
        )

        chart = _bar_chart("line_weekly")
        WIDTH = 400.0
        NATURAL_H = 300.0
        TARGET_H = 400.0
        TEXT_H = 60.0
        GAP = 16.0

        item_chart = LayoutItem(
            type="chart", width=WIDTH, height=NATURAL_H, chart=chart
        )
        # Text items compile to type="board" with no chart payload.
        item_text = LayoutItem.model_construct(
            type="board", width=WIDTH, height=TEXT_H, chart=None, board=None
        )

        inner_layout = Layout.model_validate(
            {
                "type": "rows",
                "items": [item_chart, item_text],
                "content_height": NATURAL_H + TEXT_H + GAP,
            }
        )

        executor = MagicMock(spec=Executor)
        rs, ctx = resolve_style_and_context(get_theme_style())

        render_calls: list[float] = []

        def fake_render(
            _resolved_v2, _executor, _variables, width, *, height=None, **kwargs
        ):
            assert height is not None
            render_calls.append(height)
            return (
                f'<svg viewBox="0 0 {width} {height}"></svg>',
                width,
                float(height),
                None,
            )

        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=rs,
            chart_style_context=ctx,
            render_cache={
                ("line_weekly", WIDTH, NATURAL_H, NO_TITLE_SHIFT): ("<svg/>", NATURAL_H)
            },
            natural_heights={("line_weekly", WIDTH): NATURAL_H},
            pre_resolved={"line_weekly": _resolved(chart, ctx)},
        )

        mock_board = _MM()
        mock_board.layout = inner_layout
        board_item = LayoutItem.model_construct(
            type="board",
            width=WIDTH,
            height=NATURAL_H + TEXT_H + GAP,
            board=mock_board,
            chart=None,
        )

        with patch.object(
            layout_sizing, "_render_chart_to_svg", side_effect=fake_render
        ):
            _align_board_charts(board_item, TARGET_H, render_ctx)

        board_chart_max = (NATURAL_H + TEXT_H + GAP) - TEXT_H
        expected = min(TARGET_H, board_chart_max)
        assert render_calls == [expected], (
            f"Single-chart rows board must re-render chart at {expected}; got {render_calls}. "
            "Pre-fix (rows guard): chart is not re-rendered at all."
        )
        assert item_chart.height == pytest.approx(expected)

    def test_cols_layout_board_with_table_sibling_does_not_collapse_vega_chart(self):
        """Vega chart in a cols-board alongside a table must not be collapsed to height 0.

        In a cols layout, items are side-by-side — the table's height does not
        consume the Vega chart's available vertical space. Without the fix,
        non_chart_h = table.height is subtracted from content_h, producing
        board_chart_max = 0 and collapsing the Vega chart to height 0.
        """
        from unittest.mock import MagicMock as _MM

        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_board_charts,
        )

        vega_chart = _bar_chart("vega_col")
        TABLE_H = 300.0
        VEGA_NATURAL_H = 200.0  # Vega chart is shorter than the table
        CONTENT_H = 300.0  # cols: shared height = max(TABLE_H, VEGA_NATURAL_H)
        TARGET_H = 350.0
        WIDTH = 400.0

        item_table = LayoutItem(
            type="chart",
            width=WIDTH,
            height=TABLE_H,
            chart=TableChart(
                id="tbl",
                query=SqlQuery(sql="SELECT 1", source="test"),
                query_name="q",
                type="table",
                style=None,
            ),
        )
        item_vega = LayoutItem(
            type="chart", width=WIDTH, height=VEGA_NATURAL_H, chart=vega_chart
        )

        inner_layout = Layout.model_validate(
            {
                "type": "cols",
                "items": [item_table, item_vega],
                "content_height": CONTENT_H,
            }
        )
        mock_board = _MM()
        mock_board.layout = inner_layout
        board_item = LayoutItem.model_construct(
            type="board", width=WIDTH, height=CONTENT_H, board=mock_board, chart=None
        )

        executor = MagicMock(spec=Executor)
        rs, ctx = resolve_style_and_context(get_theme_style())
        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=rs,
            chart_style_context=ctx,
            render_cache={
                ("vega_col", WIDTH, VEGA_NATURAL_H): ("<svg/>", VEGA_NATURAL_H)
            },
            natural_heights={("vega_col", WIDTH): VEGA_NATURAL_H},
            pre_resolved={"vega_col": _resolved(vega_chart, ctx)},
        )

        render_calls: list[float] = []

        def fake_render(
            _resolved_v2, _executor, _variables, width, height=None, **kwargs
        ):
            assert height is not None
            render_calls.append(height)
            return (
                f'<svg width="{width}" height="{height}"></svg>',
                width,
                float(height),
                None,
            )

        with patch.object(
            layout_sizing, "_render_chart_to_svg", side_effect=fake_render
        ):
            _align_board_charts(board_item, TARGET_H, render_ctx)

        # effective = min(TARGET_H=350, board_chart_max=CONTENT_H=300) = 300
        expected = min(TARGET_H, CONTENT_H)
        assert render_calls == [expected], (
            f"Vega chart in cols-board must be re-rendered at {expected}; got {render_calls}. "
            "Pre-fix: non_chart_h=table.height subtracts from content_h → board_chart_max=0 "
            "→ effective=0, collapsing the Vega chart to zero height."
        )
        assert item_vega.height == pytest.approx(expected)

    def test_cols_layout_board_pie_chart_does_not_grow(self):
        """Pie in a cols-board must keep natural height even when target is taller."""
        from unittest.mock import MagicMock as _MM

        from dbt_charts.core.compile.models.board.normalized import Layout
        from dbt_charts.core.render.layout_sizing import (
            SizingRenderCtx,
            _align_board_charts,
        )

        pie_chart = _pie_chart("pie_inner")
        bar_chart = _bar_chart("bar_inner")
        WIDTH = 400.0
        PIE_NATURAL_H = 300.0
        BAR_NATURAL_H = 420.0
        BOARD_CONTENT_H = BAR_NATURAL_H
        TARGET_H = 500.0

        pie_item = LayoutItem(
            type="chart", width=WIDTH, height=PIE_NATURAL_H, chart=pie_chart
        )
        bar_item = LayoutItem(
            type="chart", width=WIDTH, height=BAR_NATURAL_H, chart=bar_chart
        )
        inner_layout = Layout.model_validate(
            {
                "type": "cols",
                "items": [pie_item, bar_item],
                "content_height": BOARD_CONTENT_H,
            }
        )

        mock_board = _MM()
        mock_board.layout = inner_layout
        board_item = LayoutItem.model_construct(
            type="board",
            width=WIDTH,
            height=BOARD_CONTENT_H,
            board=mock_board,
            chart=None,
        )

        executor = MagicMock(spec=Executor)
        rs, ctx = resolve_style_and_context(get_theme_style())
        render_ctx = SizingRenderCtx(
            executor=executor,
            resolved_style=rs,
            chart_style_context=ctx,
            natural_heights={
                ("pie_inner", WIDTH): PIE_NATURAL_H,
                ("bar_inner", WIDTH): BAR_NATURAL_H,
            },
            render_cache={
                ("pie_inner", WIDTH, PIE_NATURAL_H): ("<svg/>", PIE_NATURAL_H),
                ("bar_inner", WIDTH, BAR_NATURAL_H): ("<svg/>", BAR_NATURAL_H),
            },
        )

        render_calls: list[tuple[str, float | None]] = []

        def fake_render(
            _resolved_v2, _executor, _variables, width, *, height=None, **kwargs
        ):
            render_calls.append((_resolved_v2.id, height))
            return "<svg/>", WIDTH, float(height or 0.0), None

        with patch.object(
            layout_sizing, "_render_chart_to_svg", side_effect=fake_render
        ):
            _align_board_charts(board_item, TARGET_H, render_ctx)

        assert pie_item.height == pytest.approx(PIE_NATURAL_H)
        assert bar_item.height == pytest.approx(BAR_NATURAL_H)
        assert render_calls == [], (
            f"Pie/bar at effective targets should not be re-rendered, got {render_calls}."
        )
