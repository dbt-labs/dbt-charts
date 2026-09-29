"""Tests for the page.html Jinja template + to_html chrome slot.

TDD gate for:
  1. Byte-identical output with chrome="" vs the prior f-string (golden test in
     TestHtmlPageByteIdentity); structural element checks in TestHtmlPageTemplateStructure.
  2. Chrome slot: non-empty chrome appears raw at the top of <body>.
  3. Static render has no nav / no chrome by default.
  4. Serve passes nav via chrome= and no string-replace artifact remains.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.project import Project

_BOARD_YAML = """\
title: "Test Board"
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
"""


@pytest.fixture
def compiled_board_and_executor(tmp_path: Path, local_project: Callable[..., Project]):
    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.execute.adapters import build_adapter_registry

    result = compile(_BOARD_YAML)
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(tmp_path)),
    )
    return result.board, executor


class TestHtmlPageTemplateStructure:
    """page.html with chrome='' carries the same structural elements as the old
    f-string — DOCTYPE, title, style, body wrapper. (Byte-identity is pinned
    separately in TestHtmlPageByteIdentity.)"""

    def test_html_starts_with_doctype(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        assert html.startswith("<!DOCTYPE html>")

    def test_html_has_charset_meta(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        assert 'charset="UTF-8"' in html

    def test_html_title_contains_board_title(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        assert "<title>Test Board</title>" in html

    def test_html_wrapper_reads_metadata_from_canonical_svg(
        self, compiled_board_and_executor
    ) -> None:
        from dbt_charts.core.render import render
        from dbt_charts.core.render.converters.html import to_html

        board, executor = compiled_board_and_executor
        svg = render(board, executor, format="svg").output
        assert isinstance(svg, str)

        html = to_html(svg)

        assert "<title>Test Board</title>" in html
        assert f"background-color: {board.resolved_style.background};" in html

    def test_html_wrapper_emits_font_family_declaration(self) -> None:
        from dbt_charts.core.render.converters.html import to_html

        svg = (
            '<svg data-dbt-page-title="T" '
            "data-dbt-font-family=\"'Inter', sans-serif\" "
            'data-dbt-page-background="#fff"></svg>'
        )
        assert 'font-family: "Inter", sans-serif;' in to_html(svg)

    def test_html_wrapper_does_not_parse_the_svg_body_as_xml(self) -> None:
        from dbt_charts.core.render.converters.html import to_html

        svg = (
            '<svg data-dbt-page-title="Errors &amp; warnings" '
            'data-dbt-font-family="Inter" data-dbt-page-background="#fff">'
            "<script>if (error && warning) { show(); }</script>"
            "</svg>"
        )

        html = to_html(svg)

        assert "<title>Errors &amp; warnings</title>" in html
        assert svg in html

    def test_html_wrapper_rejects_non_svg_artifact(self) -> None:
        from dbt_charts.core.render.converters.html import to_html
        from dbt_charts.core.render.errors import RenderError

        with pytest.raises(RenderError, match="must start with an <svg> tag"):
            to_html("<div>not a dashboard artifact</div>")

    def test_html_wrapper_rejects_svg_missing_artifact_metadata(self) -> None:
        from dbt_charts.core.render.converters.html import to_html
        from dbt_charts.core.render.errors import RenderError

        with pytest.raises(RenderError, match="missing data-dbt-page-title"):
            to_html("<svg></svg>")

    def test_html_title_is_escaped(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        result = compile('title: "A & B <test>"\ntext: hello\n')
        assert result.board is not None
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(tmp_path)),
        )
        rendered = render(result.board, executor, format="html")
        html = rendered.output
        assert isinstance(html, str)
        assert "<title>A &amp; B &lt;test&gt;</title>" in html
        assert "<title>A & B <test></title>" not in html

    def test_html_has_inline_style_block(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        assert "<style>" in html

    def test_style_contains_emoji_font_face(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        # The emoji font-face CSS should be present in the style block
        assert "Noto Emoji" in html

    def test_style_contains_box_sizing(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        assert "box-sizing: border-box" in html

    def test_style_contains_body_rule(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        assert "margin: 0" in html
        assert "padding: 0" in html

    def test_html_has_dbt_charts_wrapper(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        assert 'class="dbt-charts-wrapper"' in html

    def test_html_has_svg_container(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        assert "dbt-charts-svg-container" in html

    def test_svg_sizing_rule_targets_only_the_root_svg(
        self, compiled_board_and_executor
    ) -> None:
        """The responsive sizing rule must use the child combinator.

        A descendant selector (`.dbt-charts-svg-container svg`) also matches the
        nested per-row/per-chart `<svg>` elements inside the board. CSS geometry
        properties override their width/height attributes, so `height: auto`
        inflates every nested viewport to the full board height and the default
        preserveAspectRatio then re-centers each row's viewBox vertically —
        pushing rows out of their layout slots and clipping the board bottom.
        """
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        assert ".dbt-charts-svg-container > svg" in html
        assert ".dbt-charts-svg-container svg" not in html

    def test_root_svg_fills_the_page_width(self, compiled_board_and_executor) -> None:
        """The HTML page is a fit-width host: the board scales to the window.

        The root SVG carries its content-measured width as a presentation
        attribute, which a stylesheet ``width`` outranks; with the ``viewBox``
        supplying the ratio, that scales the whole board to the container.
        Anchored to the start of the declaration because a plain substring test
        cannot tell ``width: 100%`` from ``max-width: 100%`` — the latter only
        ever shrinks the board and is what this rule replaced.
        """
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        rule_start = html.index(".dbt-charts-svg-container > svg")
        rule = html[rule_start : html.index("}", rule_start)]
        assert re.search(r"(?m)^\s*width: 100%;", rule)

    def test_chrome_empty_adds_nothing_before_wrapper(
        self, compiled_board_and_executor
    ) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        result = render(board, executor, format="html")
        html = result.output
        assert isinstance(html, str)
        # With no chrome, the body should open directly to the wrapper div
        # (no extra content between <body> and <div class="dbt-charts-wrapper">)
        body_start = html.index("<body>")
        wrapper_start = html.index('<div class="dbt-charts-wrapper">')
        between = html[body_start + len("<body>") : wrapper_start].strip()
        assert between == "", f"Unexpected content before wrapper: {between!r}"


class TestCssFontFamilyStack:
    def test_quotes_named_families_but_not_generic_keywords(self) -> None:
        from dbt_charts.core.render.converters.html import _css_font_family_stack

        assert _css_font_family_stack("'Inter', sans-serif") == '"Inter", sans-serif'


class TestChromeSlot:
    """chrome= value must appear raw at the top of <body>, before dbt-charts-wrapper."""

    def test_chrome_appears_in_body(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        chrome_html = "<div class='dbt-nav'>Nav Here</div>"
        result = render(board, executor, format="html", chrome=chrome_html)
        html = result.output
        assert isinstance(html, str)
        assert chrome_html in html

    def test_chrome_is_not_escaped(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        chrome_html = "<div class='dbt-nav'>X</div>"
        result = render(board, executor, format="html", chrome=chrome_html)
        html = result.output
        assert isinstance(html, str)
        # Must appear as raw HTML, not escaped
        assert "<div class='dbt-nav'>" in html
        assert "&lt;div" not in html

    def test_chrome_appears_before_wrapper(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        chrome_html = "<div class='dbt-nav'>Nav</div>"
        result = render(board, executor, format="html", chrome=chrome_html)
        html = result.output
        assert isinstance(html, str)
        chrome_pos = html.index(chrome_html)
        wrapper_pos = html.index('class="dbt-charts-wrapper"')
        assert chrome_pos < wrapper_pos, "chrome must appear before dbt-charts-wrapper"

    def test_chrome_appears_after_body_tag(self, compiled_board_and_executor) -> None:
        from dbt_charts.core.render import render

        board, executor = compiled_board_and_executor
        chrome_html = "<div class='dbt-nav'>Nav</div>"
        result = render(board, executor, format="html", chrome=chrome_html)
        html = result.output
        assert isinstance(html, str)
        body_pos = html.index("<body>")
        chrome_pos = html.index(chrome_html)
        assert body_pos < chrome_pos, "chrome must appear after <body>"

    def test_to_html_accepts_chrome_kwarg(self, compiled_board_and_executor) -> None:
        """to_html reads chrome from **options."""
        from dbt_charts.core.render import render
        from dbt_charts.core.render.converters.html import to_html

        board, executor = compiled_board_and_executor
        chrome_html = "<nav>CHROME</nav>"
        svg = render(board, executor, format="svg").output
        assert isinstance(svg, str)
        html = to_html(svg, chrome=chrome_html)
        assert "<nav>CHROME</nav>" in html
        assert "&lt;nav&gt;" not in html

    def test_to_html_default_chrome_is_empty(self, compiled_board_and_executor) -> None:
        """to_html with no chrome= must produce no extra content before wrapper."""
        from dbt_charts.core.render import render
        from dbt_charts.core.render.converters.html import to_html

        board, executor = compiled_board_and_executor
        svg = render(board, executor, format="svg").output
        assert isinstance(svg, str)
        html = to_html(svg)
        body_start = html.index("<body>")
        wrapper_start = html.index('<div class="dbt-charts-wrapper">')
        between = html[body_start + len("<body>") : wrapper_start].strip()
        assert between == ""


class TestHtmlPageEscaping:
    """Security-critical HTML escaping must hold for titles with special characters."""

    def test_title_with_special_chars_is_html_escaped(
        self, tmp_path: Path, local_project: Callable[..., Project]
    ) -> None:
        import re

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render
        from dbt_charts.core.render.converters.html import to_html

        # Title with a quote + ampersand + angle bracket exercises the escaping.
        result = compile("title: 'Sales \"Q3\" & <co>'\ntext: hi\n")
        assert result.board is not None
        board = result.board
        executor = Executor(
            board, adapter_registry=build_adapter_registry(local_project(tmp_path))
        )

        svg = render(board, executor, format="svg").output
        assert isinstance(svg, str)
        html = to_html(svg, chrome="")

        # charset and structural elements must be present
        assert 'charset="UTF-8"' in html
        assert 'class="dbt-charts-wrapper"' in html
        assert "Noto Emoji" in html

        # Security-critical: the <title> element specifically must not contain raw
        # quote, angle, or ampersand characters — they must all be entity-escaped.
        title_match = re.search(r"<title>([^<]*)</title>", html)
        assert title_match is not None, "Expected a <title> element in rendered HTML"
        title_inner = title_match.group(1)
        assert '"' not in title_inner
        assert "<" not in title_inner
        # Every `&` must be the start of an HTML entity — no bare ampersand survives.
        assert re.search(r"&(?!amp;|lt;|gt;|quot;|#)", title_inner) is None
        # Title content survives escaping (sanity check on the happy path).
        assert "Sales" in title_inner
