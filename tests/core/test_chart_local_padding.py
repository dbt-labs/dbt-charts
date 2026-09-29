"""Chart-local ``style.<family>.padding`` regression.

Without InheritSlot wiring + the per-family read in rendering.py, an authored
``style: {bar: {padding: {top: 40}}}`` is silently dropped: per-family padding
sits at None and the render path reads board-level padding only.

This test asserts the wired contract end-to-end:

1. Per-family ``padding`` on _CartesianChartStyle / _RadialChartStyle /
   _GeoChartStyle carries InheritSlot(from_path="Style.charts.padding"),
   so ``apply_inherit`` fills every side from the board default.
2. ``build_chart_style_context`` merges any chart-local
   ``style.<family>.padding`` patch over the per-family base.
3. ``resolve()`` bakes the effective per-chart padding directly into
   ``ResolvedChart.layout_padding`` — the first and only construction, no
   later copy — for both a Vega family (bar) and an SVG family (table), plus
   callout's separate envelope.
4. ``render_layout_item`` reads padding off the resolved chart's
   ``layout_padding`` (not board-level), so the override flows into the Vega
   padding dict.
5. Sizing and render geometries agree for SVG-family — chart-local padding
   contributes to the slot height the same way it contributes to the
   render's SVG wrap (no drift between natural_heights and the rendered
   output).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_inherit_slot_fills_per_family_padding_from_board():
    """apply_inherit fills per-family padding sides from Style.charts.padding."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_resolved = resolve_chart_style_context(get_theme_style("clarity"))

    # Board padding is the canonical source.
    board_padding = board_resolved.padding
    # Every chart family carries an InheritSlot on padding — apply_inherit copies
    # the board padding into each family slot so every side matches.
    for family in (
        "bar",
        "line",
        "area",
        "scatter",
        "histogram",
        "heatmap",
        "pie",
        "geoshape",
        "point_map",
        "kpi",
        "table",
        "spark_bar",
    ):
        family_padding = getattr(board_resolved, family).padding
        assert family_padding.top == board_padding.top, family
        assert family_padding.right == board_padding.right, family
        assert family_padding.bottom == board_padding.bottom, family
        assert family_padding.left == board_padding.left, family


def test_chart_local_padding_merges_into_per_family_resolved():
    """chart.style.bar.padding={top: 40} merges into effective.bar.padding."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import (
        BarChart,
    )
    from dbt_charts.core.compile.models.style.authored import (
        BarChartStylePatch,
        PaddingStylePatch,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.resolve.style.chart_context import (
        build_chart_style_context,
    )

    board_resolved = resolve_chart_style_context(get_theme_style("clarity"))
    board_padding = board_resolved.padding

    chart = BarChart(
        id="t",
        type="bar",
        style=BarChartStylePatch(padding=PaddingStylePatch(top=40)),
    )
    effective = build_chart_style_context(board_resolved, chart)

    # Authored side wins.
    assert effective.bar.padding.top == 40.0
    # Other sides keep board default — that's the InheritSlot contract.
    assert effective.bar.padding.right == board_padding.right
    assert effective.bar.padding.bottom == board_padding.bottom
    assert effective.bar.padding.left == board_padding.left


def test_resolve_bakes_final_layout_padding_for_vega_family():
    """resolve() alone bakes board+family+chart-local padding onto a bar chart.

    No _require_resolved, no model_copy — the very first resolve() call must
    already carry the final ``layout_padding``. Every ResolvedChart is born
    with its final layout padding; nothing downstream may replace it.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.style.authored import (
        BarChartStylePatch,
        PaddingStylePatch,
    )
    from dbt_charts.core.compile.models.style.theme import PaddingStyle
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_resolved = resolve_chart_style_context(get_theme_style("clarity"))
    board_padding = board_resolved.padding

    chart = BarChart(
        id="t",
        type="bar",
        query_name="q",
        style=BarChartStylePatch(padding=PaddingStylePatch(top=40)),
    )
    resolved_chart = resolve(chart, [], chart_style_context=board_resolved)

    assert type(resolved_chart.layout_padding) is PaddingStyle
    assert resolved_chart.layout_padding.top == 40.0
    assert resolved_chart.layout_padding.right == board_padding.right
    assert resolved_chart.layout_padding.bottom == board_padding.bottom
    assert resolved_chart.layout_padding.left == board_padding.left


def test_resolve_bakes_final_layout_padding_for_svg_family():
    """Same contract for an SVG-family chart (table) — the sizing-critical case."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import TableChart
    from dbt_charts.core.compile.models.style.authored import (
        PaddingStylePatch,
        TableChartStylePatch,
    )
    from dbt_charts.core.compile.models.style.theme import PaddingStyle
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_resolved = resolve_chart_style_context(get_theme_style("clarity"))
    board_padding = board_resolved.padding

    chart = TableChart(
        id="t",
        type="table",
        query_name="q",
        style=TableChartStylePatch(padding=PaddingStylePatch(bottom=30)),
    )
    resolved_chart = resolve(chart, [], chart_style_context=board_resolved)

    assert type(resolved_chart.layout_padding) is PaddingStyle
    assert resolved_chart.layout_padding.bottom == 30.0
    assert resolved_chart.layout_padding.top == board_padding.top
    assert resolved_chart.layout_padding.left == board_padding.left
    assert resolved_chart.layout_padding.right == board_padding.right


def test_resolve_bakes_family_padding_for_type_aliases():
    """A type alias (donut/bubble_map) honors ITS family's padding, not board-level.

    Regression: the deleted effective_chart_padding() looked the family up by
    the literal normalized chart.type ("donut", "bubble_map"), which has no
    matching attribute on ResolvedChartsStyle (only "pie"/"point_map" do) — so
    it silently fell back to board-level padding for every alias type, dropping
    any chart-local style.padding override. Each resolver now reads the
    family-slot object it already built for its own style slice (pie.padding,
    point_map.padding), so the alias resolves through the same family as its
    canonical name.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import (
        PieChart,
        PointMapChart,
    )
    from dbt_charts.core.compile.models.style.authored import (
        PaddingStylePatch,
        PieChartStylePatch,
        PointMapChartStylePatch,
    )
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_resolved = resolve_chart_style_context(get_theme_style("clarity"))

    donut = PieChart(
        id="d",
        type="donut",
        theta="share",
        style=PieChartStylePatch(padding=PaddingStylePatch(top=40)),
    )
    resolved_donut = resolve(donut, [], chart_style_context=board_resolved)
    assert resolved_donut.layout_padding.top == 40.0, (
        "donut must honor pie's chart-local padding override, not silently "
        "fall back to board-level padding"
    )

    bubble_map = PointMapChart(
        id="bm",
        type="bubble_map",
        style=PointMapChartStylePatch(padding=PaddingStylePatch(top=40)),
    )
    resolved_bubble_map = resolve(bubble_map, [], chart_style_context=board_resolved)
    assert resolved_bubble_map.layout_padding.top == 40.0, (
        "bubble_map must honor point_map's chart-local padding override, not "
        "silently fall back to board-level padding"
    )


def test_render_layout_item_threads_per_family_padding_to_vega_render():
    """render_layout_item threads layout_padding from the resolved chart to render_chart_item.

    layout_padding is baked into ResolvedChart by resolve() itself — the sole
    construction, no later copy. This test verifies that render_layout_item
    reads item.chart.layout_padding (not board-level padding) and passes it
    additively with card_padding to render_chart_item.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.style.authored import (
        BarChartStylePatch,
        PaddingStylePatch,
    )
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
    from dbt_charts.core.render.chart import rendering as rendering_mod

    board_resolved, board_context = resolve_style_and_context(
        get_theme_style("clarity")
    )
    card_pad = float(board_resolved.frame.card_padding)

    bar_v2 = BarChart(
        id="test_chart",
        type="bar",
        query_name="q",
        title="",
        subtitle="",
        style=BarChartStylePatch(padding=PaddingStylePatch(top=40)),
    )
    resolved_chart = resolve(bar_v2, [], chart_style_context=board_context)

    item = ResolvedLayoutItem(
        type="chart",
        chart=resolved_chart,
        board=None,
        x=0.0,
        y=0.0,
        width=400.0,
        height=200.0,
    )

    mock_executor = MagicMock()
    with patch.object(
        rendering_mod, "render_chart_item", return_value=("<svg/>", 200.0)
    ) as spy:
        rendering_mod.render_layout_item(
            item,
            mock_executor,
            {},
            card_gap=0.0,
            available_width=400.0,
            available_height=200.0,
            resolved_style=board_resolved,
            render_cache={},
            painted_canvas=None,
        )

    padding_arg = spy.call_args.kwargs["padding"]
    assert padding_arg["top"] == card_pad + 40.0, (
        f"chart-local padding.top=40 should thread through to render_chart_item; "
        f"got padding={padding_arg!r}. If this is card_pad alone, render_layout_item "
        f"is not reading item.chart.layout_padding."
    )
    assert padding_arg["right"] == card_pad
    assert padding_arg["bottom"] == card_pad
    assert padding_arg["left"] == card_pad


def test_svg_family_render_threads_chart_local_padding():
    """SVG-family (table) render path receives the same additive padding dict as Vega.

    Table charts travel the same render_layout_item → render_chart_item path as
    bar/line/area; chart-local style.table.padding must thread through identically.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
    from dbt_charts.core.compile.models.chart.normalized import TableChart
    from dbt_charts.core.compile.models.style.authored import (
        PaddingStylePatch,
        TableChartStylePatch,
    )
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
    from dbt_charts.core.render.chart import rendering as rendering_mod

    board_resolved, board_context = resolve_style_and_context(
        get_theme_style("clarity")
    )
    card_pad = float(board_resolved.frame.card_padding)

    table_v2 = TableChart(
        id="t",
        type="table",
        query_name="q",
        title="",
        style=TableChartStylePatch(padding=PaddingStylePatch(top=30)),
    )
    resolved_chart = resolve(table_v2, [], chart_style_context=board_context)

    item = ResolvedLayoutItem(
        type="chart",
        chart=resolved_chart,
        board=None,
        x=0.0,
        y=0.0,
        width=400.0,
        height=200.0,
    )

    mock_executor = MagicMock()
    with patch.object(
        rendering_mod, "render_chart_item", return_value=("<svg/>", 200.0)
    ) as spy:
        rendering_mod.render_layout_item(
            item,
            mock_executor,
            {},
            card_gap=0.0,
            available_width=400.0,
            available_height=200.0,
            resolved_style=board_resolved,
            render_cache={},
            painted_canvas=None,
        )

    padding_arg = spy.call_args.kwargs["padding"]
    assert padding_arg is not None, (
        "SVG-family chart must receive a 4-sided padding dict — same path as Vega"
    )
    assert padding_arg["top"] == card_pad + 30.0
    assert padding_arg["right"] == card_pad
    assert padding_arg["bottom"] == card_pad
    assert padding_arg["left"] == card_pad


def test_chart_local_callout_padding_flows_without_tone():
    """``style.callout.padding`` reaches the renderer even when no tone is set.

    Pre-fix, style_cascade.py gated the whole callout patch on
    ``tone is not None`` — so ``style.callout.padding: {top: 40}`` (with no
    tone) was silently dropped. Removing the gate makes callout symmetric
    with every other family. Asserted against resolve()'s own callout
    envelope (ResolvedCalloutChart has no _base_kwargs, but still gets a
    final layout_padding at construction).
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import CalloutChart
    from dbt_charts.core.compile.models.style.authored import (
        CalloutChartStylePatch,
        PaddingStylePatch,
    )
    from dbt_charts.core.compile.models.style.theme import PaddingStyle
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    board_resolved = resolve_chart_style_context(get_theme_style("clarity"))
    chart = CalloutChart(
        id="c",
        type="callout",
        message="hello",
        style=CalloutChartStylePatch(padding=PaddingStylePatch(top=40)),
    )
    resolved_chart = resolve(chart, [], chart_style_context=board_resolved)
    pad = resolved_chart.layout_padding

    assert type(pad) is PaddingStyle
    assert pad.top == 40.0, "chart-local callout.padding.top=40 must reach the renderer"
    # Other sides keep the theme callout default.
    assert pad.left == board_resolved.callout.padding.left
    assert pad.right == board_resolved.callout.padding.right
    assert pad.bottom == board_resolved.callout.padding.bottom


def test_svg_family_sizing_and_render_agree_on_chart_local_padding():
    """Sizing provider's vertical inset must include chart-local top+bottom.

    Reviewer-flagged HIGH: pre-fix, the table/kpi/spark_bar sizing branches
    used a bare ``2 * card_pad``, but render added the chart-local override
    on top — so authoring ``style.table.padding.bottom = 40`` would
    short-change natural_heights by 40px and drift cross-column alignment.
    The sizing path now reads ``resolved.layout_padding`` off the same
    resolve() result the render path consumes — no separate cascade call.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.chart.normalized import TableChart
    from dbt_charts.core.compile.models.style.authored import (
        PaddingStylePatch,
        TableChartStylePatch,
    )
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    board_resolved, board_context = resolve_style_and_context(
        get_theme_style("clarity")
    )
    card_pad = float(board_resolved.frame.card_padding)

    # No chart-local override → matches the legacy 2 * card_pad.
    plain_chart = TableChart(id="t1", type="table")
    plain_pad = resolve(
        plain_chart, [], chart_style_context=board_context
    ).layout_padding
    plain_inset = 2 * card_pad + plain_pad.top + plain_pad.bottom
    assert plain_inset == 2 * card_pad, (
        "no chart-local override should leave the slot reservation at 2*card_pad"
    )

    # Chart-local bottom=40 → matches the additive padding the render path emits.
    bottom_chart = TableChart(
        id="t2",
        type="table",
        style=TableChartStylePatch(padding=PaddingStylePatch(bottom=40)),
    )
    bottom_pad = resolve(
        bottom_chart, [], chart_style_context=board_context
    ).layout_padding
    bottom_inset = 2 * card_pad + bottom_pad.top + bottom_pad.bottom
    assert bottom_inset == 2 * card_pad + 40.0, (
        "chart-local table.padding.bottom=40 must add 40px to the slot reservation"
    )
