"""V2 callout SVG golden tests.

Verifies render_callout_chart_svg produces valid, non-empty SVG output
for a range of callout configurations. The v1 oracle (render_callout_chart_svg)
has been deleted; these tests pin v2 output directly.

  v2: normalize_chart -> resolve() -> render_callout_chart_svg
"""

from __future__ import annotations

import re
from typing import Any

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.callout import render_callout_svg

from ._callout_test_helpers import callout_style_for_tone, compute_svg_text_fills


def _rs_and_ctx():
    return resolve_style_and_context(get_theme_style(get_default_theme_name()))


def _chart_def(
    message: str, title: str | None = None, style: dict[str, Any] | None = None
) -> dict[str, Any]:
    chart_def: dict[str, Any] = {"type": "callout", "message": message}
    if title is not None:
        chart_def["title"] = title
    if style is not None:
        chart_def["style"] = style
    return chart_def


def _v2_svg(
    message: str, title: str | None = None, style: dict[str, Any] | None = None
) -> str:
    """v2 path: authored dict -> normalize_chart -> resolve() -> render_callout_chart_svg."""
    from dbt_charts.core.compile.models.chart.resolved.callout import (
        ResolvedCalloutChart,
    )
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.callout import render_callout_chart_svg

    _, ctx = _rs_and_ctx()
    normalized = normalize_chart(
        "co", _chart_def(message, title, style), {}, sources={}
    )
    resolved = resolve(normalized, [], ctx)
    assert isinstance(resolved, ResolvedCalloutChart)
    return render_callout_chart_svg(resolved, [])


def test_v2_callout_message_only_produces_svg() -> None:
    message = "This dashboard is under construction."
    svg = _v2_svg(message)
    assert "<svg" in svg
    assert message in svg


def test_v2_callout_message_and_title_produces_svg() -> None:
    message = "This chart requires review."
    title = "Heads up"
    svg = _v2_svg(message, title)
    assert "<svg" in svg
    assert message in svg
    assert title in svg


def test_v2_callout_with_chart_local_style_override_produces_svg() -> None:
    message = "Some data may be incomplete."
    style = {
        "tone": "warning",
        "padding": {"left": 12, "right": 12, "top": 8, "bottom": 8},
        "border": {"width": 2, "color": "#ff0000", "radius": 4},
    }
    svg = _v2_svg(message, style=style)
    assert "<svg" in svg
    assert message in svg


def test_v2_callout_border_dash_array_emits_svg_dasharray() -> None:
    message = "Dashed callout border."
    style = {
        "border": {
            "width": 2,
            "color": "#ff0000",
            "radius": 4,
            "dash_array": [4, 4],
            "line_cap": "round",
        },
    }
    svg = _v2_svg(message, style=style)
    assert 'stroke-dasharray="4,4"' in svg
    assert 'stroke-linecap="round"' in svg


def test_v2_callout_bold_message_uses_theme_bold_weight() -> None:
    """A **bold** span inside a callout message renders at the theme's bold
    weight, not mdsvg's hardcoded default. render/chart/callout.py builds its
    own mdsvg.Style rather than going through compact_style_kwargs(), so this
    pins that bold_font_weight is actually threaded through, not left stale."""
    from dbt_charts.core.compile.models.style.theme import font_weight_as_css

    rs, _ = _rs_and_ctx()
    expected_weight = font_weight_as_css(rs.text.bold.weight)
    svg = _v2_svg("A **bold** word.")
    assert f"font-weight: {expected_weight}" in svg


def test_v2_callout_bold_title_matches_title_base_weight() -> None:
    """A **bold** span inside a callout title must not render lighter than
    the title's own (non-bold) text around it -- the two weights share one
    theme token, so a bold run and the surrounding title text always agree."""
    from dbt_charts.core.compile.models.style.theme import font_weight_as_css

    rs, ctx = _rs_and_ctx()
    expected_bold = font_weight_as_css(rs.text.bold.weight)
    expected_title = font_weight_as_css(ctx.callout.title.font.weight)
    assert expected_bold == expected_title, (
        "callout.title.font.weight must match text.bold.weight — otherwise "
        "a **bold** span inside a title renders at a different weight than "
        "the title's own base text, inverting emphasis on the same line."
    )
    svg = _v2_svg("Plain title with **bold** word", title="A **bold** title")
    assert f"font-weight: {expected_bold}" in svg


def test_two_callouts_same_tone_are_byte_identical_css() -> None:
    """Two callouts with identical Style (same tone) each render their own
    complete CSS, and it is byte-for-byte identical -- the shared-text
    property `boards.render_board_svg`'s dedup pass depends on to collapse
    them into one block once they share a document. No cache or coordination
    is involved: each call is independent."""
    style = callout_style_for_tone("positive")
    svg1 = render_callout_svg(
        message="First", width=300, callout_style=style, base_dir=None
    )
    svg2 = render_callout_svg(
        message="Second", width=300, callout_style=style, base_dir=None
    )

    assert "First" in svg1
    assert "Second" in svg2
    assert svg1.count("<style>") == 1
    assert svg2.count("<style>") == 1

    match1 = re.search(r"  <style>.*?  </style>", svg1, re.DOTALL)
    match2 = re.search(r"  <style>.*?  </style>", svg2, re.DOTALL)
    assert match1 is not None
    assert match2 is not None
    assert match1.group(0) == match2.group(0)


def test_two_callouts_different_tones_render_correct_colors_with_no_bleed() -> None:
    """Acceptance: two callouts with different tones still render each tone's
    correct color (no cross-callout bleed) once composited into one document."""
    success_style = callout_style_for_tone("positive")
    error_style = callout_style_for_tone("negative")
    svg1 = render_callout_svg(
        message="All good", width=300, callout_style=success_style, base_dir=None
    )
    svg2 = render_callout_svg(
        message="Uh oh", width=300, callout_style=error_style, base_dir=None
    )
    document = f'<svg xmlns="http://www.w3.org/2000/svg">{svg1}{svg2}</svg>'

    success_color = success_style.message.font.color
    error_color = error_style.message.font.color
    assert success_color != error_color

    rendered = compute_svg_text_fills(document)
    assert rendered["All good"] == success_color
    assert rendered["Uh oh"] == error_color


def test_callout_never_emits_a_bare_md_class_selector() -> None:
    """No callout CSS rule is ever a bare `.md-x` selector -- `<style>` in SVG
    is document-scoped, not limited to the nested `<svg>`/`<g>` it sits
    inside, so a bare rule would repaint any other mdsvg-rendered text
    sharing the document. mdsvg's own class scoping (`SVGRenderer._scoped_class`)
    is what prevents this today; this pins that callout.py doesn't do anything
    that would defeat it."""
    style = callout_style_for_tone("positive")
    svg = render_callout_svg(
        message="Body copy",
        title="Card title",
        width=300,
        callout_style=style,
        base_dir=None,
    )
    # `[a-z]+` alone would also match the hex-letter run inside a real
    # `md-<hash>-text` selector (e.g. "abcdef") right up to its own trailing
    # `-text`; requiring the selector to end at `{` excludes that false match.
    assert re.search(r"^\s*\.md-[a-z]+\s*\{", svg, re.MULTILINE) is None


def test_callout_title_and_message_do_not_collide() -> None:
    """A single callout's title and message both render <text class="...">
    with mdsvg's own scoped class name for "text". Title and message resolve
    to different colors (accent vs. body text), so their `Style` objects
    differ and mdsvg's per-renderer class-prefix hash (`SVGRenderer._scoped_class`)
    gives them distinct classes automatically -- this pins that callout.py's
    two separate mdsvg renderers actually get built with those differing
    colors, not that anything in callout.py itself does the scoping."""
    style = callout_style_for_tone("warning")
    svg = render_callout_svg(
        message="Body copy",
        title="Card title",
        width=300,
        callout_style=style,
        base_dir=None,
    )

    rendered = compute_svg_text_fills(svg)
    assert rendered["Card title"] == style.title.font.color
    assert rendered["Body copy"] == style.message.font.color
    assert style.title.font.color != style.message.font.color


def test_callout_does_not_bleed_onto_unscoped_prose_text() -> None:
    """A callout's CSS must never be a bare selector, or it would repaint
    unrelated text sharing the document the instant both are composited
    together -- simulated here with a hand-written bare `.md-text` rule
    standing in for markup from a source that predates mdsvg's own class
    scoping (or from outside mdsvg entirely)."""
    prose_svg = (
        "<style>.md-text { fill: #222222; }</style>"
        '<text class="md-text">Board intro paragraph</text>'
    )
    style = callout_style_for_tone("negative")
    callout_svg = render_callout_svg(
        message="Something broke", width=300, callout_style=style, base_dir=None
    )
    document = f"<svg>{prose_svg}{callout_svg}</svg>"

    rendered = compute_svg_text_fills(document)
    assert rendered["Board intro paragraph"] == "#222222"
    assert rendered["Something broke"] == style.message.font.color


def test_every_class_used_on_an_element_has_a_matching_rule() -> None:
    """Every `class="..."` token an element actually carries resolves to some
    rule somewhere in the same SVG."""
    style = callout_style_for_tone("info")
    svg = render_callout_svg(
        message="Details in the [docs](https://example.com).",
        title="Note",
        width=300,
        callout_style=style,
        base_dir=None,
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
