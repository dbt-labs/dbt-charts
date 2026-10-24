"""Test-only helper: render the nav strip from a dir_ctx triple.

Production code calls ``nav_context`` and ``render_template`` directly through
``core.render.nav.render_nav_html`` (which renders the full nav-fragment
with styles + script). Tests need just the strip markup in isolation — this
two-line helper composes the two pieces, kept here so the test files don't
each redefine it.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.render.nav import nav_context
from dbt_charts.core.render.template_loader import render_template


def nav_html(
    current_label: str,
    current_url: str,
    dir_ctx: dict[str, Any],
) -> str:
    ctx = nav_context(current_label, current_url, dir_ctx)
    return (
        render_template("nav/nav.html", **ctx, show_actions=True)
        if ctx is not None
        else ""
    )
