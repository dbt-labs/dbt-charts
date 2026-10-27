"""Chart output conversion helpers."""

from __future__ import annotations

import base64
import json

from dbt_charts.core.render.chart.artifacts import RenderArtifact
from dbt_charts.core.render.converters.pdf import to_pdf
from dbt_charts.core.render.converters.png import to_png


def render_svg_content(svg_content: str, format: str, *, scale: float = 1.0) -> str:
    """Convert SVG content to the requested encoded output."""
    if format == "svg":
        return svg_content
    if format == "png":
        return base64.b64encode(to_png(svg_content, scale=scale)).decode("utf-8")
    if format == "pdf":
        return base64.b64encode(to_pdf(svg_content)).decode("utf-8")
    raise ValueError(f"Unsupported SVG format: {format}")


def render_chart_artifact(artifact: RenderArtifact, format: str) -> str:
    """Render a chart-domain artifact into the requested output format."""
    if artifact.kind == "json":
        if format != "json":
            raise ValueError(f"JSON artifact cannot render as {format}")
        return json.dumps(artifact.payload, indent=2)
    return render_svg_content(str(artifact.payload), format)
