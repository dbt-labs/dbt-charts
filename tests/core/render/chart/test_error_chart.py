"""Tests for callout chart rendering (type: callout) and runtime error fallback.

Migrated from type: error to type: callout (Task F — consolidate-palettes).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.authored import CalloutChart
from dbt_charts.core.compile.models.primitives import BoardLinks
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.compile.resolve.style.palette import color as resolve_palette_color
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.execute.query_results import QueryResults
from dbt_charts.core.project import Project
from dbt_charts.core.render.chart.rendering import render_chart_item
from dbt_charts.core.render.chart.vega_lite import render_chart
from dbt_charts.core.render.engine import render_callout_card
from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout
from dbt_charts.core.render.svg_utils import extract_svg_dimensions

from ...._results import rows_result
from ._callout_test_helpers import resolved_callout


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
    callout_style = resolved_callout(resolve_chart_style_context(get_theme_style()))
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
        data=rows_result([]),
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
        data=rows_result([]),
        format="svg",
        width=220,
    )

    assert "Inline Failure" in svg
    assert svg.count("<text") >= 3
    assert "add data" not in svg.lower()
    assert extract_svg_dimensions(svg).height > 60


def test_explicit_callout_chart_title_uses_full_width_budget() -> None:
    title = "Explicit callout chart: long sentence should wrap across several lines"
    svg = render_callout_card(
        title=title,
        message="Short message",
        width=574,
        style=resolve_style(get_theme_style()).chart_defaults.callout_error,
        chart_id="callout",
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
    svg = render_callout_card(
        title="Explicit callout chart: long sentence should wrap across several lines",
        message=message,
        width=574,
        style=resolve_style(get_theme_style()).chart_defaults.callout_error,
        chart_id="callout",
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
    executor = MagicMock(spec=QueryResults, board=SimpleNamespace(links=BoardLinks()))
    executor.execute_query.side_effect = AssertionError(
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

    executor.execute_query.assert_not_called()
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
    executor = MagicMock(spec=QueryResults, board=SimpleNamespace(links=BoardLinks()))

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
    executor = MagicMock(spec=QueryResults, board=SimpleNamespace(links=BoardLinks()))

    pre_resolved = {
        name: resolve(v2, [], chart_style_context=board.chart_style_context)
        for name, v2 in board.charts.items()
    }
    sized_board, _ = calculate_data_aware_layout(
        board, executor, {}, render_first=False, pre_resolved=pre_resolved
    )
    item = sized_board.layout.items[0]

    # Render without a title to match the YAML chart (which has no title:).
    rendered = render_callout_card(
        message=(
            "The renderer should use as much of the visible card width as it safely can, "
            "while still keeping the error block readable and visually balanced inside a "
            "multi-column layout, even when the sentence is long enough to force a more "
            "obvious wrap pattern than a short status message would."
        ),
        width=item.width,
        style=board.chart_style_context.callout_error,
        chart_id="callout",
    )

    assert item.height == pytest.approx(extract_svg_dimensions(rendered).height)


def test_chart_data_error_fallback_wraps_message(make_chart) -> None:
    chart = make_chart("kpi", x=None, y=None, value="value", label="Broken KPI")
    executor = MagicMock(spec=QueryResults, board=SimpleNamespace(links=BoardLinks()))
    executor.execute_query.return_value = [{"value": 1}, {"value": 2}]
    executor.execute_query_result.return_value = rows_result(
        executor.execute_query.return_value
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
    from dbt_charts.core.render.chart.rendering import render_layout_item

    from ...._run import Run
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
    executor = Run(
        board,
        env=build_exec_env(local_project(Path.cwd())),
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
    from dbt_charts.core.render.chart.rendering import render_layout_item

    from ...._run import Run
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
    executor = Run(
        board,
        env=build_exec_env(local_project(Path.cwd())),
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


def test_callout_card_reads_structural_style_from_charts_callout() -> None:
    """render_callout_card uses style.charts.callout for structural properties (font, padding)."""
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
    svg_custom = render_callout_card(
        title="Migration test",
        message="structural style must come from charts.callout",
        width=300,
        style=resolved.chart_defaults.callout_error,
        chart_id="callout",
    )
    svg_default = render_callout_card(
        title="Migration test",
        message="structural style must come from charts.callout",
        width=300,
        style=default_resolved.chart_defaults.callout_error,
        chart_id="callout",
    )
    assert (
        extract_svg_dimensions(svg_custom).height
        > extract_svg_dimensions(svg_default).height
    )


def test_vl_chart_error_tile_renders_through_engine_with_code_hint_and_error_link(
    make_chart, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dbt_charts import _engine
    from dbt_charts.core.diagnostics.chart_data import ChartDataError
    from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_MODEL_COLUMN_MISSING

    engine_calls: list[str] = []
    real_render = _engine.render_chart

    def spy(payload: str) -> tuple[str, list[dict[str, object]], float | None]:
        engine_calls.append(payload)
        return real_render(payload)

    monkeypatch.setattr(_engine, "render_chart", spy)
    chart = make_chart("bar", x="region", y="revenue")
    executor = MagicMock(spec=QueryResults, board=SimpleNamespace(links=BoardLinks()))
    executor.execute_query.side_effect = ChartDataError.from_code(
        ERR_DBT_MODEL_COLUMN_MISSING,
        query_name="q",
        column_name="revenu",
        model="orders",
        available=["revenue", "region"],
    )
    executor.execute_query_result.side_effect = executor.execute_query.side_effect
    rs, ctx = resolve_style_and_context(get_theme_style())

    svg, _ = render_chart_item(
        resolve(chart, [], chart_style_context=ctx),
        executor,
        variables={},
        available_width=400,
        available_height=300,
        resolved_style=rs,
        render_cache={},
    )

    assert len(engine_calls) == 1
    assert "ERR-DBT-MODEL-COLUMN-MISSING" in svg
    assert "Did you mean" in svg
    assert (
        "https://docs.dbtcharts.com/reference/errors/#err-dbt-model-column-missing"
        in svg
    )
    assert "About this error" in svg


def _failed_tile_svg(make_chart, theme: str, title: str | None) -> str:
    from dbt_charts.core.diagnostics.chart_data import ChartDataError
    from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_MODEL_COLUMN_MISSING

    chart = make_chart("bar", x="region", y="revenue", title=title)
    executor = MagicMock(spec=QueryResults, board=SimpleNamespace(links=BoardLinks()))
    executor.execute_query.side_effect = ChartDataError.from_code(
        ERR_DBT_MODEL_COLUMN_MISSING,
        query_name="q",
        column_name="revenu",
        model="orders",
        available=["revenue", "region"],
    )
    executor.execute_query_result.side_effect = executor.execute_query.side_effect
    rs, ctx = resolve_style_and_context(get_theme_style(theme))
    svg, _ = render_chart_item(
        resolve(chart, [], chart_style_context=ctx),
        executor,
        variables={},
        available_width=400,
        available_height=300,
        resolved_style=rs,
        render_cache={},
    )
    return svg


@pytest.mark.parametrize("theme", ["clarity", "paper", "stark", "vivid", "neon"])
def test_failed_tile_is_quiet_not_negative_tone(make_chart, theme: str) -> None:
    svg = _failed_tile_svg(make_chart, theme, "Revenue by region").lower()

    for role in ("bg", "border", "solid", "text"):
        assert resolve_palette_color(f"negative.{role}").lower() not in svg
    assert 'stroke-width="0"' in svg


def test_failed_tile_leads_with_the_code_and_ends_on_a_link_to_its_entry(
    make_chart,
) -> None:
    from dbt_charts.core.diagnostics import REGISTRY
    from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_MODEL_COLUMN_MISSING

    svg = _failed_tile_svg(make_chart, "clarity", "Revenue by region")

    code = ERR_DBT_MODEL_COLUMN_MISSING.code
    assert svg.index(code) < svg.index("Did you mean") < svg.index("About this error")
    # The registry title line is gone: the code headline names the error.
    assert REGISTRY.get(code).title not in svg
    assert "See the docs" not in svg
    # The code is not a link; the one link is the closing one, on the code's anchor.
    assert f"{code}</text></a>" not in svg
    assert svg.count("<a ") == 1
    link = re.search(r'<a href="([^"]+)"[^>]*><text[^>]*>About this error', svg)
    assert link is not None and link.group(1).endswith(f"#{code.lower()}")


@pytest.mark.parametrize("theme", ["clarity", "paper", "stark", "vivid", "neon"])
def test_failed_tile_is_striped_with_a_stripe_off_the_panel(
    make_chart, theme: str
) -> None:
    svg = _failed_tile_svg(make_chart, theme, "Revenue by region")
    style = resolve_style(get_theme_style(theme)).chart_defaults.callout_error

    assert "<pattern" in svg and 'patternTransform="rotate(45)"' in svg
    assert 'fill="url(#err-stripes-' in svg
    assert style.stripe_color != style.background
    assert style.stripe_color.lower() in svg.lower()


def _luminance(color: str) -> float:
    channels = [int(color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    lighter, darker = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_every_theme_shows_its_stripes_with_about_the_same_contrast() -> None:
    ratios = {}
    for theme in ("clarity", "paper", "stark", "vivid", "neon"):
        style = resolve_style(get_theme_style(theme)).chart_defaults.callout_error
        ratios[theme] = _contrast(style.stripe_color, style.background)

    assert min(ratios.values()) > 1.15, ratios
    assert max(ratios.values()) < 1.3 * min(ratios.values()), ratios


def test_failed_tile_headline_is_set_in_the_themes_code_family(make_chart) -> None:
    svg = _failed_tile_svg(make_chart, "clarity", "Revenue by region")
    style = resolve_style(get_theme_style("clarity"))
    family = style.chart_defaults.callout_error.code_family
    from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_MODEL_COLUMN_MISSING

    assert "monospace" in family and family != "monospace"
    escaped = family.replace("'", "&apos;")
    code = ERR_DBT_MODEL_COLUMN_MISSING.code
    assert f'font-family="{escaped}" fill=' in svg and code in svg


def test_failed_tile_draws_its_warning_glyph_from_primitives(make_chart) -> None:
    svg = _failed_tile_svg(make_chart, "clarity", "Revenue by region")

    assert "<circle" in svg and 'stroke-linejoin="round"' in svg
    assert "\u26a0" not in svg


def test_failed_tile_keeps_the_charts_authored_title_above_the_tile(
    make_chart,
) -> None:
    svg = _failed_tile_svg(make_chart, "clarity", "revenue by region")

    assert svg.index("revenue by region") < svg.index("ERR-DBT-MODEL-COLUMN-MISSING")


_ROW_BOARD = """
queries:
  q:
    type: values
    rows:
      - {channel: Email, share: 48}
      - {channel: Chat, share: 31}
charts:
  ok:
    title: tickets by channel
    query: q
    type: bar
    x: channel
    y: share
  broken:
    title: median first reply
    query: q
    type: bar
    x: channel
    y: missing_column
cols:
  - ok
  - broken
"""


def _title_baselines(svg: str) -> dict[str, tuple[str, float]]:
    """Chart id to its title text and absolute first-baseline y."""
    import xml.etree.ElementTree as ET

    ns = "{http://www.w3.org/2000/svg}"
    found: dict[str, tuple[str, float]] = {}

    def walk(node: ET.Element, y: float, chart_id: str | None) -> None:
        match = re.search(
            r"translate\(([-\d.]+)[ ,]+([-\d.]+)\)", node.get("transform", "")
        )
        y += float(match.group(2)) if match else 0.0
        if node.tag == f"{ns}svg":
            y += float(node.get("y", 0))
        chart_id = node.get("data-chart-id", chart_id)
        if node.tag == f"{ns}text" and node.get("data-authored-kind") == "title":
            assert chart_id is not None
            found[chart_id] = (
                "".join(node.itertext()).strip(),
                y + float(node.get("y", 0)),
            )
        for child in node:
            walk(child, y, chart_id)

    walk(ET.fromstring(svg), 0.0, None)
    return found


_TITLE_CASE_STYLE = "style:\n  fonts:\n    heading:\n      case: title\n"


@pytest.mark.parametrize("theme", ["clarity", "paper", "stark", "vivid", "neon"])
@pytest.mark.parametrize(
    ("style", "ok_title", "broken_title"),
    [
        ("", "tickets by channel", "median first reply"),
        (_TITLE_CASE_STYLE, "Tickets by Channel", "Median First Reply"),
    ],
    ids=["as_typed", "title_case"],
)
def test_failed_tile_title_shares_the_row_baseline_and_casing_of_its_neighbor(
    theme: str, style: str, ok_title: str, broken_title: str
) -> None:
    from ..._svg_render import render_board_to_svg

    titles = _title_baselines(
        render_board_to_svg(f"extends: {theme}\n{style}{_ROW_BOARD}")
    )

    assert titles["ok"][0] == ok_title
    assert titles["broken"][0] == broken_title
    assert titles["broken"][1] == pytest.approx(titles["ok"][1])


_THREE_ACROSS = _ROW_BOARD.replace(
    "  - ok\n  - broken", "  - ok\n  - ok2\n  - broken"
).replace(
    "charts:\n",
    "charts:\n  ok2:\n    title: tickets again\n    query: q\n    type: bar\n    x: channel\n    y: share\n",
    1,
)


def _title_fonts(svg: str) -> dict[str, tuple[str, str, str]]:
    import html
    import xml.etree.ElementTree as ET

    ns = "{http://www.w3.org/2000/svg}"
    fonts: dict[str, tuple[str, str, str]] = {}

    def walk(node: ET.Element, chart_id: str | None) -> None:
        chart_id = node.get("data-chart-id", chart_id)
        if node.tag == f"{ns}text" and node.get("data-authored-kind") == "title":
            assert chart_id is not None
            fonts[chart_id] = (
                html.unescape(node.get("font-family", "")),
                str(float(node.get("font-size", 0))),
                str(node.get("font-weight")),
            )
        for child in node:
            walk(child, chart_id)

    walk(ET.fromstring(svg), None)
    return fonts


@pytest.mark.parametrize("board", [_ROW_BOARD, _THREE_ACROSS], ids=["two", "three"])
def test_failed_tile_title_takes_the_font_tier_its_card_width_gives_a_healthy_title(
    board: str,
) -> None:
    from ..._svg_render import render_board_to_svg

    fonts = _title_fonts(render_board_to_svg(f"extends: clarity\n{board}"))

    assert fonts["broken"] == fonts["ok"]


def _row_board(ok_extra: str = "", broken_title: str = "median first reply") -> str:
    return _ROW_BOARD.replace(
        "    title: tickets by channel\n", f"    title: tickets by channel\n{ok_extra}"
    ).replace("    title: median first reply", f"    title: {broken_title}")


def _group(svg: str, chart_id: str):
    import xml.etree.ElementTree as ET

    for node in ET.fromstring(svg).iter():
        if node.get("data-chart-id") == chart_id:
            return node
    raise AssertionError(f"no chart {chart_id}")


def test_failed_title_joins_a_row_band_whose_neighbor_title_is_bigger() -> None:
    from ..._svg_render import render_board_to_svg

    big_title = "    style:\n      title:\n        font:\n          size: 30\n"
    svg = render_board_to_svg(f"extends: clarity\n{_row_board(big_title)}")
    titles = _title_baselines(svg)

    assert titles["broken"][1] == pytest.approx(titles["ok"][1])
    healthy_size = float(
        next(
            n
            for n in _group(svg, "ok").iter()
            if n.get("data-authored-kind") == "title"
        ).get("font-size")
    )
    assert healthy_size == 30


def test_failed_title_fills_its_template_like_a_healthy_one() -> None:
    from ..._svg_render import render_board_to_svg

    svg = render_board_to_svg(
        f"extends: clarity\n{_row_board(broken_title=chr(34) + 'sales for {{ 1 + 1 }}' + chr(34))}"
    )

    assert "{{" not in svg
    assert _title_baselines(svg)["broken"][0] == "sales for 2"


def test_failed_title_with_an_unfillable_template_paints_no_raw_jinja() -> None:
    from ..._svg_render import render_board_to_svg

    svg = render_board_to_svg(
        f"extends: clarity\n{_row_board(broken_title=chr(34) + 'sales {{ 1 / 0 }}' + chr(34))}"
    )

    assert "{{" not in svg and "1 / 0" not in svg
    assert "broken" not in _title_baselines(svg)


def test_a_failed_chart_with_its_own_title_font_uses_it() -> None:
    from ..._svg_render import render_board_to_svg

    own = "    style:\n      title:\n        font:\n          size: 30\n"
    board = _ROW_BOARD.replace(
        "    y: missing_column\n", f"    y: missing_column\n{own}"
    )
    svg = render_board_to_svg(f"extends: clarity\n{board}")

    failed_title = next(
        n
        for n in _group(svg, "broken").iter()
        if n.get("data-authored-kind") == "title"
    )
    assert float(failed_title.get("font-size")) == 30


@pytest.mark.parametrize("theme", ["clarity", "neon"])
def test_a_board_that_draws_cards_frames_the_failed_tile_too(theme: str) -> None:
    from ..._svg_render import render_board_to_svg

    carded = (
        "style:\n  box_shadow: 0 2px 6px rgba(0,0,0,0.3)\n"
        "  charts:\n    border: 2px solid #336699\n"
    )
    svg = render_board_to_svg(f"extends: {theme}\n{carded}{_ROW_BOARD}")

    for chart_id in ("ok", "broken"):
        classes = {n.get("class") for n in _group(svg, chart_id).iter()}
        assert "dbt-card-border" in classes and "dbt-card-shadow" in classes, chart_id


def test_a_render_stage_failure_draws_its_tile_where_the_band_puts_the_body(
    make_chart,
) -> None:
    from dbt_charts.core.diagnostics.chart_data import ChartDataError
    from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_MODEL_COLUMN_MISSING
    from dbt_charts.core.render.chart.spec_builders import additive_padding
    from dbt_charts.core.render.title_band import chart_title_metrics

    chart = make_chart(
        "bar", x="region", y="revenue", title="Revenue", subtitle="By region, 2026"
    )
    executor = MagicMock(spec=QueryResults, board=SimpleNamespace(links=BoardLinks()))
    executor.execute_query.side_effect = ChartDataError.from_code(
        ERR_DBT_MODEL_COLUMN_MISSING,
        query_name="q",
        column_name="revenu",
        model="orders",
        available=["revenue"],
    )
    executor.execute_query_result.side_effect = executor.execute_query.side_effect
    rs, ctx = resolve_style_and_context(get_theme_style())
    resolved = resolve(chart, [], chart_style_context=ctx)
    card_padding = float(rs.frame.card_padding)
    padding = additive_padding(card_padding, resolved.layout_padding)

    svg, _ = render_chart_item(
        resolved,
        executor,
        variables={},
        available_width=400,
        available_height=300,
        resolved_style=rs,
        render_cache={},
        padding=padding,
    )

    metrics = chart_title_metrics(
        resolved, card_padding, 400 - padding["left"] - padding["right"], None
    )
    assert metrics is not None
    stripes = re.search(r'<rect x="0" y="([\d.]+)"[^>]*fill="url\(#err-stripes', svg)
    assert stripes is not None
    assert float(stripes.group(1)) == pytest.approx(metrics.ascent + metrics.tail)


def test_a_failed_callout_with_its_own_style_still_draws_its_tile() -> None:
    from ..._svg_render import render_board_to_svg

    svg = render_board_to_svg(
        """
charts:
  broken_callout:
    type: callout
    style:
      tone: warning
    title: Fallback ratio
    message: |
      ![alt](assets/missing.png){width=120}
rows:
  - broken_callout
"""
    )

    assert "ERR-IMAGE-REF-UNRESOLVED" in svg
