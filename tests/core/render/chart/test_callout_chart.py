"""Tests for callout chart type (replaces type: error)."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.authored import CalloutChart
from dbt_charts.core.compile.models.style.authored import CalloutChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.compile.resolve.style.palette import color as resolve_palette_color
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.chart.rendering import render_chart_item
from dbt_charts.core.render.chart.vega_lite import render_chart
from dbt_charts.core.render.engine import render_callout_card
from dbt_charts.core.render.renderer import render

from ..._svg_render import PINNED_RENDER_TIME
from ._callout_test_helpers import (
    callout_style_for_tone,
    compute_svg_text_fills,
    resolved_callout,
)


@pytest.fixture(autouse=True)
def reset_config_autouse():
    reset_config()
    yield
    reset_config()


# --- Schema / model tests ---


def test_callout_patch_requires_message() -> None:
    with pytest.raises(ValidationError, match="message"):
        CalloutChart.model_validate({"type": "callout"})


def test_callout_patch_accepts_message_only() -> None:
    patch = CalloutChart(type="callout", message="Something happened")
    assert patch.message == "Something happened"
    assert patch.style is None
    assert patch.title is None


def test_callout_patch_accepts_all_fields() -> None:
    patch = CalloutChart.model_validate(
        {
            "type": "callout",
            "message": "All good",
            "title": "Status",
            "style": {"tone": "positive"},
        }
    )
    assert patch.style is not None
    assert patch.style.tone == "positive"
    assert patch.title == "Status"


def test_callout_patch_accepts_all_tones() -> None:
    for tone in ("info", "negative", "positive", "warning"):
        patch = CalloutChart.model_validate(
            {"type": "callout", "message": "msg", "style": {"tone": tone}}
        )
        assert patch.style is not None
        assert patch.style.tone == tone


def test_type_error_is_rejected() -> None:
    """Parser must reject type: error — no backwards compat."""
    with pytest.raises(ValidationError):
        CalloutChart.model_validate({"type": "error", "message": "legacy"})


def test_callout_tone_at_root_rejected_with_hint() -> None:
    """tone: at callout chart root must be rejected; author must use style.tone."""
    with pytest.raises(ValidationError, match="style.tone"):
        CalloutChart.model_validate(
            {"type": "callout", "message": "msg", "tone": "warning"}
        )


# --- Color resolution by tone ---


def test_callout_default_tone_is_info(make_chart) -> None:
    """When tone is omitted, authored callouts resolve info.* palette roles."""
    chart = make_chart(
        "callout",
        x=None,
        y=None,
        query=None,
        query_name=None,
        message="Default info tone",
    )
    _rs, _ctx = resolve_style_and_context(get_theme_style("clarity"))
    svg = render_chart(
        chart,
        resolved_style=_rs,
        chart_style_context=_ctx,
        data=[],
        format="svg",
        width=220,
    )
    assert resolve_palette_color("info.bg") in svg
    assert resolve_palette_color("info.border") in svg
    assert resolve_palette_color("negative.bg") not in svg
    assert resolve_palette_color("negative.border") not in svg
    assert "info.bg" not in svg


def test_callout_warning_tone_resolves_warning_palette_roles(make_chart) -> None:
    """style.tone: warning resolves warning.bg|border|solid|text — not negative.*."""
    chart = make_chart(
        "callout",
        x=None,
        y=None,
        query=None,
        query_name=None,
        message="A migration warning",
        style=CalloutChartStylePatch.model_validate({"tone": "warning"}),
    )
    _rs, _ctx = resolve_style_and_context(get_theme_style("clarity"))
    svg = render_chart(
        chart,
        resolved_style=_rs,
        chart_style_context=_ctx,
        data=[],
        format="svg",
        width=220,
    )
    assert resolve_palette_color("warning.bg") in svg
    assert resolve_palette_color("warning.border") in svg
    # Must NOT fall through to negative colors.
    assert resolve_palette_color("negative.bg") not in svg


def test_callout_positive_tone_resolves_positive_palette_roles(make_chart) -> None:
    """style.tone: positive resolves positive.bg|border|solid|text — not negative.*."""
    chart = make_chart(
        "callout",
        x=None,
        y=None,
        query=None,
        query_name=None,
        message="All checks passed",
        style=CalloutChartStylePatch.model_validate({"tone": "positive"}),
    )
    _rs, _ctx = resolve_style_and_context(get_theme_style("clarity"))
    svg = render_chart(
        chart,
        resolved_style=_rs,
        chart_style_context=_ctx,
        data=[],
        format="svg",
        width=220,
    )
    assert resolve_palette_color("positive.bg") in svg
    assert resolve_palette_color("positive.border") in svg
    assert resolve_palette_color("negative.bg") not in svg


# --- Authored colors vs. tone: the tone fills only what the author left unset ---

_AUTHORED_COLORS = {
    "background": "#0b1f3a",
    "border": {"color": "#ff8800"},
    "title": {"font": {"color": "#fefefe"}},
    "message": {"font": {"color": "#dddddd"}},
}


def _board(style: dict):
    from dbt_charts.core.compile.models.style.authored import StylePatch

    return StylePatch.model_validate(style)


def _render_callout(make_chart, style: dict | None, *board_patches):
    """Resolve and render one callout; return (resolved style, svg)."""
    chart = make_chart(
        "callout",
        x=None,
        y=None,
        query=None,
        query_name=None,
        title="Status",
        message="Details",
        style=CalloutChartStylePatch.model_validate(style) if style else None,
    )
    rs, ctx = resolve_style_and_context(get_theme_style(), *board_patches)
    resolved = resolve(chart, [], chart_style_context=ctx)
    svg = render_chart(
        chart,
        resolved_style=rs,
        chart_style_context=ctx,
        data=[],
        format="svg",
        width=220,
    )
    return resolved.style, svg


def _colors(style) -> tuple[str, str, str, str]:
    return (
        style.background,
        style.border.color,
        style.title.font.color,
        style.message.font.color,
    )


def _tone_colors(tone: str) -> tuple[str, str, str, str]:
    bg, border, solid, text = (
        resolve_palette_color(f"{tone}.{role}")
        for role in ("bg", "border", "solid", "text")
    )
    return bg, border, solid, text


def test_authored_colors_beat_a_non_default_tone(make_chart) -> None:
    style, svg = _render_callout(make_chart, {"tone": "warning", **_AUTHORED_COLORS})

    assert _colors(style) == ("#0b1f3a", "#ff8800", "#fefefe", "#dddddd")
    assert 'fill="#0b1f3a" stroke="#ff8800"' in svg
    fills = compute_svg_text_fills(svg)
    assert fills["Status"] == "#fefefe"
    assert fills["Details"] == "#dddddd"
    assert resolve_palette_color("warning.bg") not in svg


def test_tone_fills_only_the_colors_left_unset(make_chart) -> None:
    style, _svg = _render_callout(
        make_chart, {"tone": "warning", "background": "#0b1f3a"}
    )

    _bg, border, solid, text = _tone_colors("warning")
    assert _colors(style) == ("#0b1f3a", border, solid, text)


def test_board_callout_colors_beat_tone_and_chart_tone_rederives_the_rest(
    make_chart,
) -> None:
    """A board-authored color survives a chart-local tone change; the colors
    the board left unset follow the chart's tone, not the board's."""
    board = _board(
        {
            "charts": {
                "callout": {
                    "tone": "warning",
                    "background": "#0b1f3a",
                    "title": {"font": {"color": "#fefefe"}},
                }
            }
        }
    )
    style, svg = _render_callout(make_chart, {"tone": "positive"}, board)

    _bg, border, _solid, text = _tone_colors("positive")
    assert _colors(style) == ("#0b1f3a", border, "#fefefe", text)
    assert 'fill="#0b1f3a"' in svg


def test_board_message_color_beats_tone(make_chart) -> None:
    board = _board({"charts": {"callout": {"message": {"font": {"color": "#dddddd"}}}}})
    style, _svg = _render_callout(make_chart, {"tone": "warning"}, board)

    assert style.message.font.color == "#dddddd"


def test_board_tone_alone_colors_the_callout(make_chart) -> None:
    style, _svg = _render_callout(
        make_chart, None, _board({"charts": {"callout": {"tone": "warning"}}})
    )

    assert _colors(style) == _tone_colors("warning")


def test_chart_color_beats_board_color(make_chart) -> None:
    board = _board({"charts": {"callout": {"background": "#0b1f3a"}}})
    style, _svg = _render_callout(make_chart, {"background": "#123456"}, board)

    assert style.background == "#123456"


def test_board_font_color_does_not_override_tone_ink(make_chart) -> None:
    """A board-wide font color is not a callout color: the tone still inks
    the title and message."""
    board = _board(
        {"font": {"color": "#123456"}, "charts": {"font": {"color": "#654321"}}}
    )
    style, _svg = _render_callout(make_chart, {"tone": "warning"}, board)

    assert _colors(style) == _tone_colors("warning")


def test_chart_by_tone_follows_the_tone_after_a_board_set_a_color(make_chart) -> None:
    board = _board(
        {
            "charts": {
                "callout": {"background": "#0b1f3a", "border": {"color": "#ff8800"}}
            }
        }
    )
    style, _svg = _render_callout(
        make_chart,
        {"tone": "warning", "background": "by_tone", "border": {"color": "by_tone"}},
        board,
    )

    bg, border, solid, text = _tone_colors("warning")
    assert _colors(style) == (bg, border, solid, text)


def test_no_by_tone_survives_into_the_resolved_callout(make_chart) -> None:
    style, svg = _render_callout(make_chart, {"tone": "negative"})

    assert "by_tone" not in style.model_dump_json()
    assert "by_tone" not in svg


def test_authored_palette_tokens_resolve_to_hex(make_chart) -> None:
    board = _board({"charts": {"callout": {"border": {"color": "positive.border"}}}})
    style, svg = _render_callout(
        make_chart,
        {"background": "warning.bg", "title": {"font": {"color": "negative.solid"}}},
        board,
    )

    assert style.background == resolve_palette_color("warning.bg")
    assert style.border.color == resolve_palette_color("positive.border")
    assert style.title.font.color == resolve_palette_color("negative.solid")
    assert "warning.bg" not in svg
    assert "positive.border" not in svg


def test_error_card_takes_board_callout_colors_and_negative_tone_for_the_rest(
    make_chart,
) -> None:
    """The runtime error card is a callout with tone: negative — board-authored
    callout colors apply to it like to any other callout."""
    rs, ctx = resolve_style_and_context(
        get_theme_style(), _board({"charts": {"callout": {"background": "#abcdef"}}})
    )
    chart = make_chart("kpi", x=None, y=None, value="value", label="Broken KPI")
    executor = MagicMock(spec=Executor)
    svg, _height = render_chart_item(
        resolve(chart, [{"value": 1}, {"value": 2}], chart_style_context=ctx),
        executor,
        variables={},
        available_width=220,
        available_height=300,
        resolved_style=rs,
        render_cache={},
    )

    assert "dbt-chart-callout" in svg
    assert 'fill="#abcdef"' in svg
    assert f'stroke="{resolve_palette_color("negative.border")}"' in svg


# --- Render behavior ---


def test_callout_renders_message_and_title(make_chart) -> None:
    from dbt_charts.core.compile.config import get_theme_style

    chart = make_chart(
        "callout",
        x=None,
        y=None,
        query=None,
        query_name=None,
        title="Important Notice",
        message="This chart requires review.",
    )
    _rs, _ctx = resolve_style_and_context(get_theme_style())
    svg = render_chart(
        chart,
        resolved_style=_rs,
        chart_style_context=_ctx,
        data=[],
        format="svg",
        width=220,
    )
    assert "Important Notice" in svg
    assert "This chart requires review" in svg
    assert "add data" not in svg.lower()


def test_callout_skips_query_execution(make_chart) -> None:
    from dbt_charts.core.compile.config import get_theme_style

    chart = make_chart(
        "callout",
        x=None,
        y=None,
        query_name=None,
        title="No Query",
        message="No query should run",
    )
    executor = MagicMock(spec=Executor)
    executor.execute_chart.side_effect = AssertionError(
        "callout must not execute queries"
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
    assert "No query should run" in svg_text
    assert height > 60


def test_callout_compiles_successfully() -> None:
    result = compile(
        """
charts:
  notice:
    type: callout
    message: "This dashboard is under construction."
rows:
  - notice
"""
    )
    assert result.success


def test_callout_with_tone_compiles_successfully() -> None:
    result = compile(
        """
charts:
  warn:
    type: callout
    style:
      tone: warning
    message: "Some data may be incomplete."
rows:
  - warn
"""
    )
    assert result.success


# --- Runtime ChartDataError fallback ---


def test_chart_data_error_fallback_renders_as_callout_negative_tone(make_chart) -> None:
    """Runtime ChartDataError fallback must use negative tone (callout card)."""
    from dbt_charts.core.compile.config import get_theme_style

    chart = make_chart("kpi", x=None, y=None, value="value", label="Broken KPI")
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = [{"value": 1}, {"value": 2}]

    rs, ctx = resolve_style_and_context(get_theme_style())
    svg, height = render_chart_item(
        resolve(chart, [{"value": 1}, {"value": 2}], chart_style_context=ctx),
        executor,
        variables={},
        available_width=220,
        available_height=300,
        resolved_style=rs,
        render_cache={},
    )

    svg_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", svg)).strip()
    assert "expects exactly 1 row" in svg_text
    assert "dbt-chart-callout" in svg
    assert resolve_palette_color("negative.bg") in svg
    assert resolve_palette_color("negative.border") in svg
    assert resolve_palette_color("info.bg") not in svg
    assert height > 60


# --- render_callout_card direct API ---


def test_callout_card_reads_structural_style_from_charts_callout() -> None:
    """render_callout_card uses style.charts.callout for structural properties (font, padding)."""
    from dbt_charts.core.compile.config import get_theme_style

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
    resolved_ctx = resolve_chart_style_context(seed)

    # The section_gap affects SVG element y-positions. We verify the SVG contains
    # more vertical space than the minimal default would produce.
    default_ctx = resolve_chart_style_context(get_theme_style())
    svg_custom = render_callout_card(
        title="Migration test",
        message="structural style must come from charts.callout",
        width=300,
        style=resolved_callout(resolved_ctx),
        chart_id="callout",
    )
    svg_default = render_callout_card(
        title="Migration test",
        message="structural style must come from charts.callout",
        width=300,
        style=resolved_callout(default_ctx),
        chart_id="callout",
    )
    # Custom section_gap=42 makes the card taller than the default section_gap.
    from dbt_charts.core.render.svg_utils import extract_svg_dimensions

    assert (
        extract_svg_dimensions(svg_custom).height
        > extract_svg_dimensions(svg_default).height
    )


def test_callout_card_renders_resolved_tone_colors() -> None:
    """A callout resolved for tone='warning' renders warning.* palette colors —
    render_callout_card takes no tone parameter; the tone's colors must
    already be baked onto callout_style before this call."""
    svg = render_callout_card(
        message="This is a warning",
        width=300,
        style=callout_style_for_tone("warning"),
        chart_id="callout",
    )

    assert resolve_palette_color("warning.bg") in svg
    assert resolve_palette_color("warning.border") in svg


def test_callout_card_without_title_skips_fabricated_heading() -> None:
    """title=None means message-only; the renderer must not invent a chart-id heading."""
    svg = render_callout_card(
        message="All values are reported in USD.",
        width=300,
        style=callout_style_for_tone("info"),
        chart_id="callout",
    )

    assert "All values are reported in USD." in svg
    assert "Callout:" not in svg
    assert "quiet_note" not in svg


def test_callout_card_wraps_code_badge_in_doc_link() -> None:
    from dbt_charts.core.diagnostics.registry import build_doc_url

    ctx = resolve_chart_style_context(get_theme_style())

    svg = render_callout_card(
        message="Something went wrong.",
        width=300,
        style=resolved_callout(ctx),
        placard={
            "code": "ERR-BAR-DUPLICATE-ROWS",
            "code_url": build_doc_url("ERR-BAR-DUPLICATE-ROWS"),
            "hint": None,
            "doc_url": None,
        },
        chart_id="callout",
    )

    doc_url = build_doc_url("ERR-BAR-DUPLICATE-ROWS")
    assert f'<a href="{doc_url}" target="_blank">' in svg
    assert "ERR-BAR-DUPLICATE-ROWS</text></a>" in svg


def test_callout_card_without_code_has_no_doc_link() -> None:
    ctx = resolve_chart_style_context(get_theme_style())

    svg = render_callout_card(
        message="Something went wrong.",
        width=300,
        style=resolved_callout(ctx),
        chart_id="callout",
    )

    assert "<a href=" not in svg


def test_callout_card_renders_doc_url_as_see_docs_link() -> None:
    ctx = resolve_chart_style_context(get_theme_style())
    doc_url = "https://docs.dbtcharts.com/reference/errors/#err-test"

    svg = render_callout_card(
        message="Something went wrong.",
        width=300,
        style=resolved_callout(ctx),
        placard={
            "code": "ERR-TEST",
            "code_url": doc_url,
            "hint": None,
            "doc_url": doc_url,
        },
        chart_id="callout",
    )

    assert f'<a href="{doc_url}" target="_blank">' in svg
    assert "See the docs</text></a>" in svg
    assert doc_url not in svg.replace(f'<a href="{doc_url}" target="_blank">', "")


def test_callout_card_with_code_badge_converts_to_png() -> None:
    """The badge's <a> must use plain SVG2 href — xlink:href without an
    xmlns:xlink declaration crashes vl_convert's PNG rasterization."""
    from dbt_charts.core.diagnostics.registry import build_doc_url
    from dbt_charts.core.render.converters.png import to_png

    ctx = resolve_chart_style_context(get_theme_style())

    svg = render_callout_card(
        message="Something went wrong.",
        width=300,
        style=resolved_callout(ctx),
        placard={
            "code": "ERR-BAR-DUPLICATE-ROWS",
            "code_url": build_doc_url("ERR-BAR-DUPLICATE-ROWS"),
            "hint": None,
            "doc_url": None,
        },
        chart_id="callout",
    )

    png_bytes = to_png(svg)
    assert png_bytes.startswith(b"\x89PNG")


# --- Markdown rendering in title and message ---


def test_message_renders_bold_and_inline_code() -> None:
    """**bold** and `code` in message must produce SVG styling, not literal markdown syntax."""
    ctx = resolve_chart_style_context(get_theme_style())
    svg = render_callout_card(
        message="This is **bold** and `code` text.",
        width=300,
        style=resolved_callout(ctx),
        chart_id="callout",
    )
    assert "**bold**" not in svg
    assert "`code`" not in svg
    assert "bold" in svg
    assert "code" in svg
    # mdsvg emits font-weight inline for bold spans
    assert "font-weight: bold" in svg or "font-weight:bold" in svg


def test_title_supports_markdown() -> None:
    """title with **bold** must produce bold SVG styling, not literal asterisks."""
    ctx = resolve_chart_style_context(get_theme_style())
    svg = render_callout_card(
        title="**Important** Notice",
        message="Details here.",
        width=300,
        style=resolved_callout(ctx),
        chart_id="callout",
    )
    assert "**Important**" not in svg
    assert "Important" in svg
    assert "font-weight: bold" in svg or "font-weight:bold" in svg


def test_message_with_link_produces_anchor_element() -> None:
    """[text](url) in message must produce an SVG <a> element, not literal bracket syntax."""
    ctx = resolve_chart_style_context(get_theme_style())
    svg = render_callout_card(
        message="See the [docs](https://example.com/docs) for details.",
        width=300,
        style=resolved_callout(ctx),
        chart_id="callout",
    )
    assert "[docs]" not in svg
    assert "docs" in svg
    assert "<a " in svg
    assert "example.com" in svg


def test_height_accounts_for_markdown_list_content() -> None:
    """A bullet list in message must produce a taller card than a single-line message."""
    from dbt_charts.core.render.svg_utils import extract_svg_dimensions

    ctx = resolve_chart_style_context(get_theme_style())
    svg_single = render_callout_card(
        message="Short message.",
        width=300,
        style=resolved_callout(ctx),
        chart_id="callout",
    )
    svg_list = render_callout_card(
        message="Issues found:\n- First issue here\n- Second issue here\n- Third issue here",
        width=300,
        style=resolved_callout(ctx),
        chart_id="callout",
    )
    assert (
        extract_svg_dimensions(svg_list).height
        > extract_svg_dimensions(svg_single).height
    )


def test_authored_callout_message_renders_markdown() -> None:
    """Authored callouts (markdown=True, the default) parse bold and inline code."""
    ctx = resolve_chart_style_context(get_theme_style())
    svg = render_callout_card(
        message="**Column not found** in source table. Column: `revenue`.",
        width=320,
        style=resolved_callout(ctx),
        chart_id="callout",
    )
    assert "**Column" not in svg
    assert "`revenue`" not in svg
    assert "revenue" in svg
    assert "font-weight: bold" in svg or "font-weight:bold" in svg


def test_runtime_error_callout_underscores_render_as_literal_text() -> None:
    """Runtime error callouts (markdown=False) must not parse _name_ as italic.

    Python identifiers like _variable_name_, SQL table names, and traceback
    fragments routinely contain underscores and asterisks that Markdown would
    otherwise interpret as italic/bold markers.
    """
    ctx = resolve_chart_style_context(get_theme_style())
    svg = render_callout_card(
        message="Column _revenue_total_ not found in table *fact_orders*.",
        width=320,
        style=resolved_callout(ctx),
        placard={
            "code": "ERR-TEST",
            "code_url": "https://docs.dbtcharts.com/reference/errors/#err-test",
            "hint": None,
            "doc_url": None,
        },
        chart_id="callout",
    )
    # Underscores and asterisks must survive as literal text, not trigger italic/bold
    assert "_revenue_total_" in svg or "revenue_total" in svg
    assert "fact_orders" in svg
    # No italic tspan should be emitted for these identifiers
    assert "font-style: italic" not in svg or svg.count("font-style: italic") == 0


def test_long_doc_url_renders_as_short_link_label() -> None:
    """A long doc_url must stay in href, not as visible card text."""
    import re as _re

    ctx = resolve_chart_style_context(get_theme_style("clarity"))
    width = 280.0
    long_url = "https://docs.example.com/guides/error-reference/column-not-found-in-source-table"

    svg = render_callout_card(
        message="Column not found.",
        width=width,
        style=resolved_callout(ctx),
        placard={
            "code": "ERR-TEST",
            "code_url": long_url,
            "hint": None,
            "doc_url": long_url,
        },
        chart_id="callout",
    )

    assert f'<a href="{long_url}" target="_blank">' in svg
    text_contents = _re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    assert "See the docs" in text_contents
    assert long_url not in text_contents


def test_title_renders_in_tone_solid_color_not_message_color() -> None:
    """Title must use {tone}.solid color, not {tone}.text (message color).

    Regression: when only the message style_block was emitted, both title and
    message elements shared the same .md-text CSS rule, so the title rendered in
    the wrong color. The fix (mdsvg scopes each renderer's `.md-*` classes to a
    hash of its own style) gives title and message distinct scopes whenever
    their colors differ, so the two rules coexist without collision.
    """
    title_color = resolve_palette_color("warning.solid")
    message_color = resolve_palette_color("warning.text")
    assert title_color != message_color, "pre-condition: warning.solid ≠ warning.text"

    svg = render_callout_card(
        title="**Important** Warning",
        message="Check your settings.",
        width=320,
        style=callout_style_for_tone("warning"),
        chart_id="callout",
    )

    # Title and message text land on distinct scoped classes, not one shared rule.
    text_classes = set(re.findall(r'class="(md-[0-9a-f]{8}-text)"', svg))
    assert len(text_classes) == 2, f"expected two distinct .md-*-text scopes: {svg}"
    # Both tone colors appear in the SVG (in their respective scoped CSS rules).
    assert title_color in svg, f"title color {title_color!r} must appear in SVG"
    assert message_color in svg, f"message color {message_color!r} must appear in SVG"


def test_two_tone_callouts_use_distinct_css_class_names() -> None:
    """Sibling callouts of different tones must not share CSS class names.

    Regression: when all callouts used the same .md-text/.t-md-text class names,
    the last-rendered tone's CSS rule won for every callout in the board SVG root.
    """
    positive_color = resolve_palette_color("positive.text")
    negative_color = resolve_palette_color("negative.text")
    assert positive_color != negative_color, "pre-condition: tones must differ"

    positive_svg = render_callout_card(
        message="All checks passed.",
        width=320,
        style=callout_style_for_tone("positive"),
        chart_id="callout",
    )
    negative_svg = render_callout_card(
        message="Pipeline failed.",
        width=320,
        style=callout_style_for_tone("negative"),
        chart_id="callout",
    )

    # Extract all scoped md-* class names from each callout.
    pos_md_classes = set(re.findall(r'class="([^"]*md-[^"]*)"', positive_svg))
    neg_md_classes = set(re.findall(r'class="([^"]*md-[^"]*)"', negative_svg))
    shared = pos_md_classes & neg_md_classes
    assert not shared, (
        f"Callouts share CSS class names — cross-tone collision in shared SVG root: {shared}"
    )

    # Composite both callouts into one SVG root (as the board renderer does).
    # In a shared root, CSS <style> rules are document-global; scoping prevents collision.
    composite = (
        f'<svg xmlns="http://www.w3.org/2000/svg">{positive_svg}{negative_svg}</svg>'
    )

    # Both tone colors appear in the composite — each in its own scoped CSS rule.
    assert positive_color in composite
    assert negative_color in composite

    # The two scoped color rules must be distinct: the positive color must not appear
    # in the negative callout's style block, and vice-versa.
    assert negative_color not in positive_svg, (
        "Negative tone color leaked into positive callout — CSS scoping broken"
    )
    assert positive_color not in negative_svg, (
        "Positive tone color leaked into negative callout — CSS scoping broken"
    )


def test_link_href_with_md_path_segment_is_not_corrupted() -> None:
    """Class scoping must never rewrite .md-* substrings in link hrefs or text.

    Regression: an earlier implementation applied a CSS-selector regex (`.md-X`)
    to rendered elements (not just the style block), which would silently
    corrupt any href or text containing a `.md-<letters>` substring.
    """
    ctx = resolve_chart_style_context(get_theme_style())
    url = "https://docs.example.com/.md-guide/config"
    svg = render_callout_card(
        message=f"See the [guide]({url}) for details.",
        width=320,
        style=resolved_callout(ctx),
        chart_id="callout",
    )
    assert url in svg, f"href {url!r} must be preserved verbatim; got: {svg[:800]}"


def test_javascript_link_href_is_stripped() -> None:
    """javascript: hrefs must not render as live anchors — XSS sink.

    Regression: mdsvg applies no URL-scheme filtering; callout SVGs are
    inlined into the Cloud HTML page so a live <a href="javascript:...">
    is directly clickable by any user viewing the dashboard.
    """
    ctx = resolve_chart_style_context(get_theme_style())
    svg = render_callout_card(
        message="[click here](javascript:alert(document.cookie))",
        width=320,
        style=resolved_callout(ctx),
        chart_id="callout",
    )
    assert "javascript:" not in svg, (
        "javascript: href must be stripped from callout SVG"
    )


def test_different_width_same_content_produces_distinct_clip_ids() -> None:
    """Same title/message/tone at different widths must not share clip-path ids.

    Regression: width was excluded from callout_hash, so identical-content
    callouts at different column widths shared the same clip-path id. In a
    shared SVG root, duplicate ids resolve to the first definition, causing
    the second callout to clip to the wrong geometry.
    """
    ctx = resolve_chart_style_context(get_theme_style())
    long_message = "word " * 150  # long enough to trigger clipping at narrow widths

    svg_narrow = render_callout_card(
        message=long_message,
        width=180,
        style=resolved_callout(ctx),
        chart_id="callout",
    )
    svg_wide = render_callout_card(
        message=long_message,
        width=480,
        style=resolved_callout(ctx),
        chart_id="callout",
    )

    narrow_clip_ids = set(re.findall(r'id="((?:msg|title)-clip-[^"]+)"', svg_narrow))
    wide_clip_ids = set(re.findall(r'id="((?:msg|title)-clip-[^"]+)"', svg_wide))

    # If either produces clip-path defs (long enough to overflow), ids must differ.
    if narrow_clip_ids and wide_clip_ids:
        shared = narrow_clip_ids & wide_clip_ids
        assert not shared, f"Clip-path id collision at different widths: {shared}"


def _render_board_svg(yaml_src: str) -> str:
    result = compile(yaml_src)
    assert result.success, result.errors
    assert result.board is not None

    executor = MagicMock(spec=Executor)
    executor.execute_chart.return_value = []
    executor.cache_hit_ats = []
    executor.rendered_at = PINNED_RENDER_TIME

    svg = render(result.board, executor, format="svg").output
    assert isinstance(svg, str)
    return svg


def test_board_with_same_tone_callouts_shares_one_style_block() -> None:
    """End-to-end: two same-tone callouts on one board, rendered through the
    real render() pipeline, share one style block -- pins that
    boards.render_board_svg's style-block dedup pass actually runs on a real
    board (each callout emits its own complete, content-addressed CSS on its
    own, via mdsvg's own class scoping; the pass collapses the two identical
    copies once they land in one document). Resolves the actual cascade
    winner for each callout's text, not just that both colors appear
    somewhere in the document, and that the dedup pass didn't drop a rule
    some element still needs."""
    svg = _render_board_svg(
        """
title: Callout Dedup Board
charts:
  c1:
    type: callout
    message: First message
  c2:
    type: callout
    message: Second message
cols: [c1, c2]
"""
    )
    assert "First message" in svg
    assert "Second message" in svg

    style = callout_style_for_tone("info")  # theme default tone for callouts
    rendered = compute_svg_text_fills(svg)
    assert rendered["First message"] == style.message.font.color
    assert rendered["Second message"] == style.message.font.color

    # The actual dedup: the two callouts' identical CSS collapses to one block.
    charts_svg = svg.split('id="chart-c1"', 1)[1]
    assert charts_svg.count("<style>") == 1


def test_board_dedup_pass_never_drops_a_rule_a_class_still_needs() -> None:
    """End-to-end completeness check: on a board with several callouts (some
    sharing a tone, some not) mixed with `text:` prose, every content-derived
    `class="md-<hash>-*"` token an element actually carries resolves to some
    rule still present in the final SVG -- and, since this board also mixes
    tones, that each callout's text actually resolves to its own tone's
    color through the real dedup pass, not a hand-concatenated string."""
    svg = _render_board_svg(
        """
text: Board intro copy
charts:
  c1:
    type: callout
    message: First info callout
  c2:
    type: callout
    message: Second info callout
  c3:
    type: callout
    message: A warning callout
    style:
      tone: warning
cols: [c1, c2, c3]
"""
    )
    used = {
        cls
        for m in re.finditer(r'class="([^"]*)"', svg)
        for cls in m.group(1).split()
        if cls.startswith("md-")
    }
    defined = set(re.findall(r"\.(md-[0-9a-f]+-[a-z]+)\s*\{", svg))
    assert used, "test setup should exercise at least one mdsvg class"
    assert used <= defined, f"classes used with no matching rule: {used - defined}"

    info_color = callout_style_for_tone("info").message.font.color
    warning_color = callout_style_for_tone("warning").message.font.color
    assert info_color != warning_color
    rendered = compute_svg_text_fills(svg)
    assert rendered["First info callout"] == info_color
    assert rendered["Second info callout"] == info_color
    assert rendered["A warning callout"] == warning_color


def test_visible_callout_is_unaffected_by_a_hidden_sibling_sharing_its_style() -> None:
    """The dedup pass runs on the fully assembled board SVG, after layout has
    already decided what's visible -- a `visible: false` item's content never
    reaches that string in the first place, so it can neither contribute a
    rule to dedupe against nor be depended on by anything that does ship."""
    svg = _render_board_svg(
        """
title: Hidden Sibling Dedup
charts:
  visible_callout:
    type: callout
    message: Visible message
  hidden_callout:
    type: callout
    message: Hidden message
rows:
  - visible_callout
  - visible: false
    rows:
      - hidden_callout
"""
    )
    style = callout_style_for_tone("info")  # theme default tone for callouts

    assert "Visible message" in svg
    assert "Hidden message" not in svg

    rendered = compute_svg_text_fills(svg)
    assert rendered["Visible message"] == style.message.font.color
