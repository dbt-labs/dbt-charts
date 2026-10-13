"""Shared helpers for the callout style-dedup tests (test_callout_svg.py,
test_callout_chart.py) -- tone-scoped resolved styles, and a constrained CSS
resolver that answers "which color actually wins" instead of the weaker
"does this substring appear somewhere" a mdsvg cascade collision can pass by
accident.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.normalized import CalloutChart
from dbt_charts.core.compile.models.style.authored import CalloutChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


def resolved_callout(ctx, tone: str | None = None):
    """The ResolvedCalloutStyle a ``type: callout`` chart resolves to under
    ``ctx`` (tone is baked in at chart-resolve time, not passed to the renderer)."""
    chart = CalloutChart(
        id="callout",
        type="callout",
        message="-",
        style=CalloutChartStylePatch(tone=tone) if tone is not None else None,
    )
    return resolve(chart, [], chart_style_context=ctx).style


def callout_style_for_tone(tone: str, theme: str | None = None):
    theme_style = get_theme_style(theme or get_default_theme_name())
    return resolved_callout(resolve_chart_style_context(theme_style), tone)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def compute_svg_text_fills(svg: str) -> dict[str, str]:
    """Resolve the winning `fill` for every <text> element in `svg`, keyed by
    its own direct text content.

    Not a general CSS engine -- every rule the callout engine emits is a single-class
    selector already unique to its own declaration (content-addressed class
    renaming), and the one exception in these tests -- a hand-written
    stand-in for render/prose.py's bare `.md-text` rule -- is also a single
    class selector. So resolving "which rule applies to this element" needs
    nothing beyond a direct lookup of the element's own `class` attribute: no
    ancestor walk, no specificity, no source-order tiebreak.
    """
    rules: dict[str, str] = {}
    for style_match in re.finditer(r"<style>(.*?)</style>", svg, re.DOTALL):
        for line in style_match.group(1).splitlines():
            line = line.strip()
            if not line or "{" not in line:
                continue
            selector, _, rest = line.partition("{")
            decl = rest.rstrip("}").strip()
            rules[selector.strip().lstrip(".")] = decl

    root = ET.fromstring(
        svg if svg.lstrip().startswith("<svg") else f"<svg>{svg}</svg>"
    )

    results: dict[str, str] = {}
    for el in root.iter():
        if _local(el.tag) != "text":
            continue
        text_content = (el.text or "").strip()
        if not text_content:
            continue
        for cls in (el.get("class") or "").split():
            decl = rules.get(cls)
            if decl is None:
                continue
            fill_match = re.search(r"fill:\s*([^;]+)", decl)
            if fill_match:
                results[text_content] = fill_match.group(1).strip()
    return results
