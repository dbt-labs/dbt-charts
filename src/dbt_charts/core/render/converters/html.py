"""HTML format conversion.

Stage: RENDER
Purpose: Convert the canonical SVG output to a standalone HTML page.
"""

import re
from typing import Any

from dbt_charts.core.diagnostics import ERR_INPUT_INVALID
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.svg_utils import attr_name, escape_css_string

# Not a color validator: rejects only characters that can break out of the CSS
# declaration or <style> block, so var()/oklch()/color-mix() still pass.
_CSS_BREAKING_CHARS_RE = re.compile(r'[<>;{}"\'\\*\x00-\x1f\x7f]')

# CSS generic font-family keywords must stay bare, unquoted keywords -- quoting
# one (e.g. "sans-serif") makes the browser search for a font literally named
# that instead of falling back to the platform's generic sans-serif font.
_CSS_GENERIC_FONT_FAMILIES = {
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-serif",
    "ui-sans-serif",
    "ui-monospace",
    "ui-rounded",
    "math",
    "emoji",
    "fangsong",
}


def _css_font_family_stack(stack: str) -> str:
    """Render an authored font-family stack as safe CSS.

    Each comma-separated entry becomes a quoted, escaped CSS string, except a
    generic family keyword, which stays bare -- see the module-level comment.
    """
    families = []
    for raw in stack.split(","):
        name = raw.strip().strip("'\"")
        if not name:
            continue
        if name.lower() in _CSS_GENERIC_FONT_FAMILIES:
            families.append(name.lower())
        else:
            families.append(escape_css_string(name))
    return ", ".join(families)


def to_html(
    svg_content: str,
    livereload: bool = False,
    controls: bool = False,
    **options: Any,
) -> str:
    """Wrap a self-describing canonical SVG in a standalone HTML page.

    HTML format wraps the SVG artifact without changing its body. The SVG
    carries all chart rendering and layout, including the variable controls the
    server draws — a host adds behavior to them, not markup.

    Args:
        svg_content: The SVG content to wrap
        livereload: When True, embed the ``dct serve`` live-reload script that
            reloads the tab on a file change. Off for static render / Cloud / MCP.
        controls: True when the caller is a host that can re-run a board's
            queries. The page then carries the control runtime, which is what
            binds the drawn controls; the host stylesheet ships either way,
            since a static export is still a page in a browser, but not the
            runtime: a filter that cannot filter is worse than no filter.
            The runtime is not only for variables — it also intercepts the
            ``<a href="?...">`` links a tabbed board renders — so a live host
            ships it even for a board with no variables at all.
        **options: Additional options; reads ``chrome`` (default ``""``) —
            a trusted HTML string emitted at the top of ``<body>`` before the
            board wrapper.  ``dct serve`` passes the nav fragment here;
            static render / Cloud / MCP omit it.
    Returns:
        Complete HTML document as string

    """
    import html as html_module

    from dbt_charts.core.render.chart_interactivity import hover_runtime_source
    from dbt_charts.core.render.controls import (
        controls_runtime_source,
        controls_stylesheet,
    )
    from dbt_charts.core.render.template_loader import render_template

    opening_tag = re.match(r"\s*<svg\b([^>]*)>", svg_content)
    if opening_tag is None:
        raise RenderError.from_code(
            ERR_INPUT_INVALID,
            message="Canonical dashboard artifact must start with an <svg> tag",
        )
    metadata: dict[str, str] = {}
    for name in ("page-title", "font-family", "page-background"):
        match = re.search(
            rf'\bdata-dbt-{attr_name(name)}="([^"]*)"', opening_tag.group(1)
        )
        if match is None:
            raise RenderError.from_code(
                ERR_INPUT_INVALID,
                message=f"Canonical dashboard SVG is missing data-dbt-{name}",
            )
        metadata[name] = html_module.unescape(match.group(1))

    page_title = metadata["page-title"]
    font_family = _css_font_family_stack(metadata["font-family"])
    board_background = metadata["page-background"]
    if _CSS_BREAKING_CHARS_RE.search(board_background):
        raise RenderError.from_code(
            ERR_INPUT_INVALID,
            message=f"style.background is not valid CSS: {board_background!r}",
        )
    escaped_title = html_module.escape(page_title)
    bg_style = f"background-color: {board_background};"

    chrome = options.get("chrome", "")

    return render_template(
        "page.html",
        title=escaped_title,
        font_family=font_family,
        bg_style=bg_style,
        svg=svg_content,
        chrome=chrome,
        livereload=livereload,
        # The stylesheet always: a static HTML export is a page in a browser
        # with real <a href> links, a text cursor and a hover underline, and no
        # runtime to grant them — the board itself ships none (a board is a
        # picture). Only the runtime is gated on a host that can act on it.
        controls_css=controls_stylesheet(),
        controls_runtime=controls_runtime_source()
        if controls
        else hover_runtime_source(),
    )
