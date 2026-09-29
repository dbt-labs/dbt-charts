"""Tests for callout chart rendering (type: callout) and runtime error fallback.

Migrated from type: error to type: callout (Task F — consolidate-palettes).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.authored import CalloutChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.compile.resolve.style.palette import color as resolve_palette_color
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.project import Project
from dbt_charts.core.render.chart.callout import render_callout_svg
from dbt_charts.core.render.chart.rendering import render_chart_item
from dbt_charts.core.render.chart.vega_lite import render_chart
from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout
from dbt_charts.core.render.svg_utils import extract_svg_dimensions


@pytest.fixture(autouse=True)
def reset_config_autouse():
    reset_config()
    yield
    reset_config()


def test_default_theme_callout_uses_tone_info_palette() -> None:
    """Callout background is a non-empty hex color resolved from the info tone palette.

    Tests structure and cascade behavior (info vs warning produce different colors)
    rather than pinning absolute hex values that break on any legitimate palette tweak.
    """
    callout_style = get_theme_style().charts.callout
    bg = callout_style.background
    # Must be a non-empty string that looks like a hex color
    assert bg and bg.startswith("#"), (
        f"callout background must be a hex color, got {bg!r}"
    )

    # info tone and warning tone must produce different backgrounds — the cascade is wired
    info_bg = resolve_palette_color("info.bg")
    warning_bg = resolve_palette_color("warning.bg")
    assert info_bg != warning_bg, (
        "info.bg and warning.bg are identical — palette tone differentiation is broken"
    )
    # The default callout uses the info tone, so its background must match info.bg
    assert bg.lower() == info_bg.lower(), (
        f"Default callout background {bg!r} does not match info.bg {info_bg!r} — "
        "theme wiring for callout tone is broken"
    )


def test_default_theme_callout_chart_resolves_tone_colors_in_svg(make_chart) -> None:
    chart = make_chart(
        "callout",
        x=None,
        y=None,
        query=None,
        query_name=None,
        message="Info tone palette should resolve to concrete SVG colors",
    )

    svg = render_chart(
        chart,
        resolved_style=resolve_style(get_theme_style("clarity")),
        chart_style_context=resolve_chart_style_context(get_theme_style("clarity")),
        data=[],
        format="svg",
        width=220,
    )

    assert resolve_palette_color("info.bg") in svg
    assert resolve_palette_color("info.border") in svg
    assert resolve_palette_color("negative.bg") not in svg
    assert resolve_palette_color("negative.border") not in svg
    assert "info.bg" not in svg
    assert "info.border" not in svg


def test_explicit_callout_chart_renders_wrapped_message(make_chart) -> None:
    chart = make_chart(
        "callout",
        x=None,
        y=None,
        query=None,
        query_name=None,
        title="Inline Failure",
        message=(
            "This is a deliberately long error message for compact cards so the "
            "renderer has to wrap multiple lines instead of overflowing sideways."
        ),
    )

    svg = render_chart(
        chart,
        resolved_style=resolve_style(get_theme_style()),
        chart_style_context=resolve_chart_style_context(get_theme_style()),
        data=[],
        format="svg",
        width=220,
    )

    assert "Inline Failure" in svg
    assert svg.count("<text") >= 3
    assert "add data" not in svg.lower()
    assert extract_svg_dimensions(svg).height > 60


def test_explicit_callout_chart_raises_when_font_measurer_is_unavailable() -> None:
    from dbt_charts.core import font_measure

    original = font_measure._load_measurer
    font_measure._load_measurer = lambda _font_path: (_ for _ in ()).throw(
        RuntimeError("boom")
    )
    try:
        with pytest.raises(RuntimeError, match="boom"):
            render_callout_svg(
                title="Explicit callout chart: long sentence should wrap across several lines",
                message="Short message",
                width=574,
                callout_style=resolve_style(get_theme_style()).chart_defaults.callout,
            )
    finally:
        font_measure._load_measurer = original


def test_explicit_callout_chart_title_uses_full_width_budget() -> None:
    title = "Explicit callout chart: long sentence should wrap across several lines"
    svg = render_callout_svg(
        title=title,
        message="Short message",
        width=574,
        callout_style=resolve_style(get_theme_style()).chart_defaults.callout,
    )

    assert f">{title}</text>" in svg
    assert ">lines</text>" not in svg


def test_explicit_callout_chart_message_does_not_emit_overflowing_line() -> None:
    message = (
        "The renderer should use as much of the visible card width as it safely can, "
        "while still keeping the error block readable and visually balanced inside a "
        "multi-column layout, even when the sentence is long enough to force a more "
        "obvious wrap pattern than a short status message would."
    )
    svg = render_callout_svg(
        title="Explicit callout chart: long sentence should wrap across several lines",
        message=message,
        width=574,
        callout_style=resolve_style(get_theme_style()).chart_defaults.callout,
    )

    # Message must be split into multiple <text> elements (wrapped), not emitted
    # as one long unbroken line. The exact break points are renderer-dependent.
    import re as _re

    text_lines = _re.findall(r"<text[^>]*>([^<]+)</text>", svg)
    # Filter to lines that contain words from our message (excluding the title)
    msg_lines = [line for line in text_lines if "renderer" in line or "column" in line]
    assert len(msg_lines) >= 2, "Long message must wrap into at least 2 text lines"
    full_sentence = (
        "keeping the error block readable and visually balanced inside a "
        "multi-column layout, even when the sentence is long enough to force a more "
        "obvious wrap pattern than a short status message would."
    )
    # No single line should contain the full un-wrapped sentence
    assert all(full_sentence not in line for line in text_lines)


def test_explicit_callout_chart_skips_query_execution(make_chart) -> None:
    chart = make_chart(
        "callout",
        query_name=None,
        title="Execution Not Needed",
        message="Rendered directly from authored message",
    )
    executor = MagicMock(spec=Executor)
    executor.execute_chart.side_effect = AssertionError(
        "callout charts should not execute queries"
    )

    rs, ctx = resolve_style_and_context(get_theme_style())
    svg, height = render_chart_item(
        resolve(chart, [], chart_style_context=ctx),
        executor,
        variables={},
        available_width=220,
        available_height=300,
        resolved_style=rs,
        render_cache={},
    )

    executor.execute_chart.assert_not_called()
    svg_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", svg)).strip()
    assert "Rendered directly from" in svg_text
    assert "authored message" in svg_text
    assert (
        '<svg xmlns="http://www.w3.org/2000/svg" class="dbt-chart-callout"' not in svg
    )
    assert height > 60
    assert svg.count('id="chart-') == 1


def test_explicit_callout_chart_never_shrinks_below_layout_slot_height() -> None:
    from dbt_charts.core.compile.models.chart.normalized import (
        CalloutChart as CalloutChart,
    )

    callout_v2 = CalloutChart(
        id="explicit_callout_sentence",
        type="callout",
        title="Explicit callout chart: long sentence should wrap across several lines",
        message=(
            "The renderer should use as much of the visible card width as it safely can, "
            "while still keeping the error block readable and visually balanced inside a "
            "multi-column layout, even when the sentence is long enough to force a more "
            "obvious wrap pattern than a short status message would."
        ),
        query_is_inline=False,
    )
    executor = MagicMock(spec=Executor)

    rs, ctx = resolve_style_and_context(get_theme_style())
    svg, height = render_chart_item(
        resolve(callout_v2, [], chart_style_context=ctx),
        executor,
        variables={},
        available_width=384,
        available_height=300,
        resolved_style=rs,
        render_cache={},
    )

    # The layout already reserved a 300px slot for this row; callout must not
    # render shorter than that and leave the row visually inconsistent with
    # its siblings — even though the message's own natural height is smaller.
    assert height == pytest.approx(300.0)
    assert 'data-chart-height="300.0"' in svg


def test_explicit_callout_chart_static_layout_height_matches_rendered_height() -> None:
    result = compile(
        """
charts:
  explicit_callout_sentence:
    type: callout
    message: >
      The renderer should use as much of the visible card width as it safely can,
      while still keeping the error block readable and visually balanced inside a
      multi-column layout, even when the sentence is long enough to force a more
      obvious wrap pattern than a short status message would.
rows:
  - explicit_callout_sentence
"""
    )
    assert result.success

    board = result.board
    assert board is not None
    executor = MagicMock(spec=Executor)

    pre_resolved = {
        name: resolve(v2, [], chart_style_context=board.chart_style_context)
        for name, v2 in board.charts.items()
    }
    sized_board, _ = calculate_data_aware_layout(
        board, executor, {}, render_first=False, pre_resolved=pre_resolved
    )
    item = sized_board.layout.items[0]

    # Render without a title to match the YAML chart (which has no title:).
    rendered = render_callout_svg(
        message=(
            "The renderer should use as much of the visible card width as it safely can, "
            "while still keeping the error block readable and visually balanced inside a "
            "multi-column layout, even when the sentence is long enough to force a more "
            "obvious wrap pattern than a short status message would."
        ),
        width=item.width,
        callout_style=board.chart_style_context.callout,
    )

    assert item.height == pytest.approx(extract_svg_dimensions(rendered).height)


def test_chart_data_error_fallback_wraps_message(make_chart) -> None:
    chart = make_chart("kpi", x=None, y=None, value="value", label="Broken KPI")
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = [{"value": 1}, {"value": 2}]

    rs, ctx = resolve_style_and_context(get_theme_style())
    svg, height = render_chart_item(
        resolve(chart, [], chart_style_context=ctx),
        executor,
        variables={},
        available_width=220,
        available_height=300,
        resolved_style=rs,
        render_cache={},
    )

    svg_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", svg)).strip()
    assert "expects exactly 1 row" in svg_text
    assert svg.count("<text") >= 3
    assert height > 60
    assert "dbt-chart-callout" in svg
    assert (
        '<svg xmlns="http://www.w3.org/2000/svg" class="dbt-chart-callout"' not in svg
    )


def test_kpi_error_fallback_layout_item_matches_authored_callout_padding(
    local_project: Callable[..., Project],
) -> None:
    from dbt_charts.core.compile import compile
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.chart.rendering import render_layout_item

    from ..._board_utils import apply_static_layout

    yaml_content = """
queries:
  kpi_bad:
    columns: [revenue]
    values:
      - [124000]
      - [98000]
charts:
  implicit_error_revenue:
    type: kpi
    query: kpi_bad
    value: revenue
rows:
  - implicit_error_revenue
"""
    result = compile(yaml_content)
    assert result.success and result.board is not None

    board = apply_static_layout(result.board)
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    from dbt_charts.core.render.board_resolve import (
        build_resolved_board_static as resolve_board,
    )

    resolved_board = resolve_board(board)
    item = resolved_board.layout.items[0]
    svg, _ = render_layout_item(
        item,
        executor,
        variables={},
        card_gap=0.0,
        available_width=item.width,
        available_height=item.height,
        resolved_style=resolve_style(get_theme_style()),
        render_cache={},
        painted_canvas=None,
    )

    assert f'data-chart-width="{item.width}"' in svg
    assert f'width="{item.width}"' in svg


def test_callout_chart_layout_item_uses_full_item_width_without_outer_card_padding(
    local_project: Callable[..., Project],
) -> None:
    from dbt_charts.core.compile import compile
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.chart.rendering import render_layout_item

    from ..._board_utils import apply_static_layout

    yaml_content = """
charts:
  e:
    type: callout
    message: The renderer should use as much of the visible card width as it safely can.
rows:
  - e
"""
    result = compile(yaml_content)
    assert result.success and result.board is not None

    board = apply_static_layout(result.board)
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    from dbt_charts.core.render.board_resolve import (
        build_resolved_board_static as resolve_board,
    )

    resolved_board = resolve_board(board)
    item = resolved_board.layout.items[0]
    svg, _ = render_layout_item(
        item,
        executor,
        variables={},
        card_gap=0.0,
        available_width=item.width,
        available_height=item.height,
        resolved_style=resolve_style(get_theme_style()),
        render_cache={},
        painted_canvas=None,
    )

    assert '<g transform="translate(16.0, 16.0)">' not in svg
    assert f'width="{item.width}"' in svg


def test_chart_patch_callout_requires_message() -> None:
    with pytest.raises(ValidationError, match="message"):
        CalloutChart.model_validate({"type": "callout"})


def test_render_callout_svg_reads_structural_style_from_charts_callout() -> None:
    """render_callout_svg uses style.charts.callout for structural properties (font, padding)."""
    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "callout": base.charts.callout.model_copy(
                        update={"section_gap": 42.0}
                    )
                }
            )
        }
    )
    resolved = resolve_style(seed)

    default_resolved = resolve_style(get_theme_style())
    svg_custom = render_callout_svg(
        title="Migration test",
        message="structural style must come from charts.callout",
        width=300,
        callout_style=resolved.chart_defaults.callout,
    )
    svg_default = render_callout_svg(
        title="Migration test",
        message="structural style must come from charts.callout",
        width=300,
        callout_style=default_resolved.chart_defaults.callout,
    )
    assert (
        extract_svg_dimensions(svg_custom).height
        > extract_svg_dimensions(svg_default).height
    )
