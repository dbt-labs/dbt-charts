"""Absolute baselines of every ``<text>`` in a rendered board SVG."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

_TRANSLATE = re.compile(r"translate\(\s*([-\d.e]+)[ ,]+\s*([-\d.e]+)\s*\)")


@dataclass(frozen=True)
class SvgText:
    text: str
    x: float
    y: float
    font_size: float


def _shift(transform: str | None) -> tuple[float, float]:
    m = _TRANSLATE.search(transform or "")
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def svg_texts(svg: str) -> list[SvgText]:
    """Every ``<text>`` with its translated-to-root x/y and font size, in document order."""
    found: list[SvgText] = []

    def walk(node: ET.Element, ox: float, oy: float) -> None:
        dx, dy = _shift(node.get("transform"))
        ox, oy = ox + dx, oy + dy
        if node.tag.endswith("}text") or node.tag == "text":
            size = node.get("font-size", "0").removesuffix("px")
            y = (
                oy
                + float(node.get("y", "0"))
                + float(node.get("dy", "0").removesuffix("px"))
            )
            body = "".join(node.itertext()).strip()
            found.append(SvgText(body, ox + float(node.get("x", "0")), y, float(size)))
            return
        for child in node:
            walk(child, ox, oy)

    walk(ET.fromstring(svg), 0.0, 0.0)
    return found
