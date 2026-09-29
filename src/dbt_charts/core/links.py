"""Link-scheme safety: is a URL a scheme dbt charts allows a browser to visit.

Leaf node — shared by ``compile.validate.links`` (rejecting an unsafe scheme
at compile) and ``render.svg_utils`` (re-checking a scheme at render, where a
table cell/row link or a chart link's sentinel-stripped value only exists
after the query has run or Vega has rendered). Neither compile nor render
reaches into the other's layer to validate a link scheme; both depend on
this instead.
"""

from __future__ import annotations

import re

# Schemes a browser may navigate to from a rendered board. Matches mdsvg's
# _SAFE_LINK_SCHEMES (libs/markdown-svg/src/mdsvg/renderer.py). Deliberately
# NOT derived from render.board_links._PASSTHROUGH_PREFIXES: that list answers
# "skip board-path rewriting?", not "safe to click?", and includes command:/
# cursor://vscode://file:// — editor deeplinks with no place in a link an
# author or a query row can produce.
_SAFE_SCHEMES = frozenset({"http", "https", "mailto"})
_SCHEME_RE = re.compile(r"[a-z][a-z0-9+.\-]*(?=:)")
_URL_IGNORED_RE = re.compile(r"[\t\n\r]")
_C0_OR_SPACE = "".join(map(chr, range(0x21)))


def link_scheme(href: str) -> str | None:
    """The scheme a browser navigates ``href`` with; ``None`` when relative.

    Mirrors WHATWG URL parsing: leading C0/space stripped, tab/LF/CR removed
    anywhere, case-folded — so ``" JaVa\\tScript:x"`` is ``"javascript"``,
    not a relative link that happens to start with a space.
    """
    normalized = _URL_IGNORED_RE.sub("", href.lstrip(_C0_OR_SPACE)).lower()
    m = _SCHEME_RE.match(normalized)
    return m.group(0) if m else None


def is_safe_href(href: str) -> bool:
    """Whether a browser navigating ``href`` lands on a scheme dbt charts
    allows. ``None`` scheme (relative / board-path / query-only / fragment)
    is always safe — only an explicit scheme can be dangerous."""
    scheme = link_scheme(href)
    return scheme is None or scheme in _SAFE_SCHEMES
