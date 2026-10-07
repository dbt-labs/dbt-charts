"""CSS-context escaping and validation for values written into CSS."""

from __future__ import annotations

import re

# Not a color validator: rejects only characters that can break out of the CSS
# declaration or <style> block, so var()/oklch()/color-mix() still pass.
CSS_BREAKING_CHARS_RE = re.compile(r'[<>;{}"\'\\*\x00-\x1f\x7f]')

_CSS_STRING_SAFE_RE = re.compile(r"[A-Za-z0-9 _-]")
_SAFE_FAMILY_ENTRY_RE = re.compile(r"\s*([\"']?)[\w -]+\1\s*")

# Generic keywords must stay unquoted: a quoted "sans-serif" names a literal font.
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


def escape_css_string(value: str) -> str:
    """Render ``value`` as a quoted CSS string, hex-escaping unsafe chars."""
    escaped = "".join(
        ch if _CSS_STRING_SAFE_RE.fullmatch(ch) else f"\\{ord(ch):x} " for ch in value
    )
    return f'"{escaped}"'


def css_font_family_stack(stack: str) -> str:
    """Render an authored font-family stack as safe CSS; plain stacks are unchanged."""
    if all(_SAFE_FAMILY_ENTRY_RE.fullmatch(entry) for entry in stack.split(",")):
        return stack
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
