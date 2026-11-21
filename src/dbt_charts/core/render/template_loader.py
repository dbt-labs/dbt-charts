"""Template loader for SVG/HTML templates.

Stage: RENDER
Purpose: Load and render Jinja2 templates for SVG and HTML rendering.

This module provides a single function for loading templates from the
render/templates directory and rendering them with Jinja2.

Note: The global _jinja_env uses lazy initialization without locking.
This is acceptable for single-threaded use. In multi-threaded contexts
(e.g., Django), the worst case is redundant Environment creation.
"""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

# Lazy-initialized Jinja environment (not thread-safe, but acceptable)
_jinja_env: Environment | None = None


def _get_jinja_env() -> Environment:
    """Get or create Jinja environment for templates.

    Holds templates only. The ``@font-face`` block used to be compiled in here as a
    DictLoader entry, which froze one answer for the life of the process — fine while
    every board declared the whole registry, wrong once a board declares the boards it
    paints with. It is rendered per board now and passed in as a value; see
    ``render/font_selection.py``.
    """
    global _jinja_env

    if _jinja_env is None:
        _jinja_env = Environment(
            loader=PackageLoader("dbt_charts.core.render", "templates"),
            autoescape=select_autoescape(["html", "xml", "svg"]),
        )

    return _jinja_env


def render_template(template_path: str, **context: Any) -> str:
    """Render a template with the given context.

    This is the ONLY function needed for template loading. Use it for:
    - Templates with variables: render_template("svg/grid_pattern.svg", **ctx)
    - Static templates: render_template("svg/grid_pattern.svg")

    Args:
        template_path: Path to template relative to templates/ directory
                      (e.g., "svg/grid_pattern.svg")
        **context: Template context variables (optional)

    Returns:
        Rendered template string
    """
    env = _get_jinja_env()
    template = env.get_template(template_path)
    return template.render(**context)
