from __future__ import annotations

from xml.etree import ElementTree

from dbt_charts.core.compile.config import get_theme_style


def _svg_text_content(svg: str) -> str:
    return "".join(ElementTree.fromstring(svg).itertext())


def test_text_jinja_resolution_skips_markdown_code_fences() -> None:
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.boards import _render_text_svg

    svg, _height = _render_text_svg(
        """
Current segment: **{{ segment }}**

```yaml
text: "{{ segment }}"
```
""",
        {"segment": "Enterprise"},
        500.0,
        resolved_style=resolve_style(get_theme_style()),
        text_style=resolve_style(get_theme_style()).text,
        painted_canvas=None,
    )

    text_content = _svg_text_content(svg)

    assert "Current segment:" in text_content
    assert "Enterprise" in text_content
    assert "{{ segment }}" in text_content
    assert 'text: "Enterprise"' not in text_content
