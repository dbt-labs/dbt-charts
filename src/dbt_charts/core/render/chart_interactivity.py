"""Core-owned chart hover interactivity runtime."""

import json
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.render.comment_stripping import strip_js_comments
from dbt_charts.core.render.svg_utils import attr_name, escape_attr
from dbt_charts.core.text.format_d3 import NULL_DISPLAY

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle


def _build_tooltip_style_dict(resolved_style: "ResolvedStyle") -> dict[str, Any]:
    """Build the camelCase JSON dict consumed by the JS tooltip runtime."""
    t = resolved_style.chart_defaults.tooltip
    return {
        "background": t.background,
        "lineHeight": t.line_height,
        "maxWidth": t.max_width,
        "gap": t.gap,
        "font": {"size": t.font.size},
        "padding": {
            "top": t.padding.top,
            "bottom": t.padding.bottom,
            "left": t.padding.left,
            "right": t.padding.right,
        },
        "label": {
            "font": {
                "color": t.label.font.color,
                "weight": t.label.font.weight,
            }
        },
        "value": {
            "font": {
                "color": t.value.font.color,
                "weight": t.value.font.weight,
            }
        },
        "border": {
            "color": t.border.color,
            "width": t.border.width,
            "radius": t.border.radius,
        },
        "shadow": {"visible": t.shadow.visible},
        "swatch": {"size": t.swatch.size, "radius": t.swatch.radius},
        "activeMarker": t.active_marker,
    }


def _build_hover_emphasis_dict(
    resolved_style: "ResolvedStyle",
) -> dict[str, bool | float | str]:
    """Build the JSON dict consumed by the JS hover-emphasis runtime.

    Three sources, deliberately: `visible` and the drop-line color/width are
    theme-cascaded (a theme decides whether hover emphasis happens at all,
    and picks its own neutral drop-line paint), while `opacity` is engine
    config (picking the bar/arc recession strength is ours -- there is no
    wide range of settings that read well).

    The bar/arc recession color still rides no wire of its own: it is the
    chart's own canvas, stamped on each chart wrapper as
    ``data-dbt-chart-canvas`` and read there by the runtime. The drop line
    is a different mechanism (a scaffold line added by the runtime, not a
    recede-in-place), so its paint has no such rendered fact to read off and
    is carried explicitly instead.
    """
    hover_emphasis = resolved_style.chart_defaults.hover_emphasis
    return {
        "visible": hover_emphasis.visible,
        "opacity": get_chart_rendering().hover_emphasis.dimmed_opacity,
        "dropLineColor": hover_emphasis.drop_line_color,
        "dropLineWidth": hover_emphasis.drop_line_width,
    }


@cache
def hover_runtime_source() -> str:
    """The chart hover runtime, as JS for a host to ship in its page.

    One static script for every board: the theme values it needs ride on the
    board root as data attributes (``hover_runtime_attributes``), read at mount.
    ``NULL_DISPLAY`` is a package constant, not a theme value, so it is the one
    substitution left — made once and cached, like the controls runtime.
    """
    script = (
        files("dbt_charts.core.render")
        / "templates"
        / "scripts"
        / "chart_interactivity.js"
    ).read_text(encoding="utf-8")
    return strip_js_comments(
        script.replace('"__DCT_NULL_DISPLAY__"', json.dumps(NULL_DISPLAY))
    )


def hover_runtime_attributes(resolved_style: "ResolvedStyle") -> str:
    """The theme facts the hover runtime reads off the board root.

    Published as data, never templated into the script: a board is a picture,
    and code never ships inside it. Leading space included, for the ``<svg``
    open tag.
    """
    values = {
        "data-dbt-font-family": str(resolved_style.font.family),
        "data-dbt-tooltip-style": json.dumps(_build_tooltip_style_dict(resolved_style)),
        "data-dbt-hover-emphasis": json.dumps(
            _build_hover_emphasis_dict(resolved_style)
        ),
    }
    return "".join(f' {attr_name(k)}="{escape_attr(v)}"' for k, v in values.items())
