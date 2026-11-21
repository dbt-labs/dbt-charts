"""Format conversion functions for rendered output.

Stage: RENDER
Purpose: Convert SVG output to other formats (HTML, PNG, PDF).

This package provides format converters:
- html.py: SVG to HTML conversion (with wrapper page)
- png.py: SVG to PNG conversion (using vl-convert)
- pdf.py: SVG to PDF conversion (using vl-convert)

Dependencies:
    - vl-convert-python (for PNG/PDF export)
"""

from __future__ import annotations

from dbt_charts.core.render.converters.html import to_html
from dbt_charts.core.render.converters.pdf import to_pdf
from dbt_charts.core.render.converters.png import to_png

__all__ = [
    "to_html",
    "to_png",
    "to_pdf",
]
