"""Shared presentation helpers for chart rendering.

Leaf module: compile-layer imports only.
"""

from __future__ import annotations

import copy
from typing import Any

from dbt_charts.core.compile.merge import deep_merge_dict

# Internal field name for the calculate-transform output backing the href
# click-interactivity encoding. Consumed by translate.py.
_HREF_FIELD = "__df_href__"


def apply_presentation_defaults(
    spec: dict[str, Any],
    background: str,
    effective_vega_config: dict[str, Any],
) -> dict[str, Any]:
    """Apply compiled presentation defaults to an emitted Vega-Lite spec once.

    Uses ``effective_vega_config`` (the board's fully-baked VL config, which
    already carries ``style_to_vega_lite(resolved_chart_style)`` — see
    ``effective_vega_config()`` in ``compile/vega_lite/mapping.py``) as the
    base config, then re-applies the authored per-chart config at highest
    priority. One merge, no intermediate delta.

    ``spec`` is mutated and returned in place — its caller (``assemble_final_vl``)
    builds it fresh per chart via ``translate_to_vl``, so nothing else holds a
    reference. ``deep_merge_dict`` never mutates its ``base``/``overlay``
    arguments, so ``authored_config`` needs no defensive copy either.

    ``effective_vega_config`` (``board_style.vega_config``) is different: it is
    cached and shared well beyond one board render — ``get_theme_style()`` memoizes
    per theme name and the no-patch cascade result is cached on object identity, so
    every board using a given theme with no style patch shares one ``vega_config``
    for the process lifetime. It is deep-copied here so ``result_spec["config"]``
    is always this call's private object; nothing downstream may assume otherwise.
    """
    result_spec = spec
    if "config" not in result_spec:
        result_spec["config"] = {}
    elif not isinstance(result_spec["config"], dict):
        raise TypeError("Chart config must be a dictionary")
    authored_config = result_spec["config"]
    compiled = copy.deepcopy(effective_vega_config)

    result_spec["config"] = compiled

    # Background cascade: background is the chart's own effective background
    # (chart-local style.<family>.background when authored, otherwise the
    # board/board background), baked at resolve time. Skip for geo charts that
    # keep a transparent spec root (projection present, no authored background).
    preserve_transparent_background = (
        result_spec.get("background") is None and "projection" in result_spec
    )
    if not preserve_transparent_background:
        result_spec["background"] = background

    result_spec["config"] = deep_merge_dict(result_spec["config"], authored_config)
    return result_spec
