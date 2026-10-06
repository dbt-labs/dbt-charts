"""Allowlist checking for SVG a ``type: custom`` template emits.

A template is user code and its output lands inline in an HTML page that may be
shared, so the output is checked against an allowlist and rejected whole on the
first violation; nothing is stripped. What ships is the checked tree serialized
again, never the template's string: XML and HTML disagree about comments,
processing instructions, and attribute-name case, so a string that passes an XML
check can still carry live markup to a browser.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from dbt_charts.core.render.svg_utils import attr_name, escape_attr

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"

# Declares the namespaces a template's bare content is written against.
_OPEN = '<root xmlns="' + _SVG_NS + '" xmlns:xlink="' + _XLINK_NS + '">'

ALLOWED_ELEMENTS = frozenset(
    {
        "g",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "path",
        "text",
        "tspan",
        "textPath",
        "defs",
        "linearGradient",
        "radialGradient",
        "stop",
        "pattern",
        "clipPath",
        "mask",
        "marker",
        "symbol",
        "use",
        "title",
        "desc",
    }
)

_MARKUP = re.compile(
    r"(?P<comment><!--)|(?P<pi><\?)|(?P<cdata><!\[CDATA\[)"
    r"|<!\s*(?P<declaration>DOCTYPE|ENTITY)",
    re.IGNORECASE,
)
_URL_CALL = re.compile(r"url\(", re.IGNORECASE)
_LOCAL_URL = re.compile(r"url\(\s*(['\"]?)#[^)'\"\s]+\1\s*\)", re.IGNORECASE)
# CSS functions besides url() that fetch: image-set() and its prefixed form,
# cross-fade(), image(), element(), src().
_CSS_FETCH = re.compile(
    r"(-webkit-image-set|image-set|cross-fade|image|element|src)\s*\(", re.IGNORECASE
)
_JAVASCRIPT = re.compile(r"j\s*a\s*v\s*a\s*s\s*c\s*r\s*i\s*p\s*t\s*:", re.IGNORECASE)


class SvgViolation(Exception):
    """The SVG is malformed or uses a disallowed construct; ``str(exc)`` names it."""


def checked_svg_content(content: str) -> str:
    """Return ``content`` re-serialized from its checked tree, or raise ``SvgViolation``.

    ``content`` is what goes inside a root ``<svg>``: any number of sibling
    elements.
    """
    markup = _MARKUP.search(content)
    if markup:
        if markup["declaration"]:
            raise SvgViolation(f"<!{markup['declaration'].upper()} is not allowed")
        kind = {
            "comment": "a comment",
            "pi": "a processing instruction",
            "cdata": "a CDATA section",
        }[str(markup.lastgroup)]
        raise SvgViolation(f"{kind} ({markup.group(0)!r}) is not allowed")
    try:
        root = ElementTree.fromstring(_OPEN + content + "</root>")
    except ElementTree.ParseError as exc:
        raise SvgViolation(f"output is not well-formed XML: {exc}") from exc
    return _serialize_children(root)


def _serialize_children(parent: ElementTree.Element) -> str:
    out = _text(parent.text)
    for element in parent:
        out += _serialize(element) + _text(element.tail)
    return out


def _text(text: str | None) -> str:
    """Escaped text; ElementTree reports absent text as ``None``."""
    return "" if text is None else escape(text)


def _serialize(element: ElementTree.Element) -> str:
    namespace, _, name = element.tag.removeprefix("{").rpartition("}")
    if namespace != _SVG_NS or name not in ALLOWED_ELEMENTS:
        raise SvgViolation(f"<{name}> is not an allowed element")
    attrs = "".join(
        f" {attr_name(_checked_attribute(name, attribute, value))}"
        f'="{escape_attr(value)}"'
        for attribute, value in element.attrib.items()
    )
    inner = _text(element.text) + "".join(
        _serialize(child) + _text(child.tail) for child in element
    )
    return f"<{name}{attrs}>{inner}</{name}>" if inner else f"<{name}{attrs}/>"


def _checked_attribute(element: str, attribute: str, value: str) -> str:
    """Return the attribute's output name, or raise if the attribute is not allowed."""
    namespace, _, local = attribute.removeprefix("{").rpartition("}")
    where = f"{local!r} on <{element}>"
    # HTML lowercases attribute names, so every name check is case-blind.
    lowered = local.lower()
    if namespace not in ("", _XLINK_NS) or (
        namespace == _XLINK_NS and lowered != "href"
    ):
        raise SvgViolation(f"namespaced attribute {where} is not allowed")
    if lowered.startswith("on"):
        raise SvgViolation(f"event attribute {where} is not allowed")
    if lowered == "href" and not value.startswith("#"):
        raise SvgViolation(f"href {value!r} on <{element}> must be a #id reference")
    if _JAVASCRIPT.search(value):
        raise SvgViolation(f"javascript: in {where} is not allowed")
    if "\\" in value:
        raise SvgViolation(f"a backslash (CSS escape) in {where} is not allowed")
    fetch = _CSS_FETCH.search(value)
    if fetch:
        raise SvgViolation(f"{fetch.group(1)}() in {where} is not allowed")
    urls = len(_URL_CALL.findall(value))
    if urls != len(_LOCAL_URL.findall(value)):
        raise SvgViolation(f"url() in {where} must be url(#id)")
    return "href" if lowered == "href" else local
