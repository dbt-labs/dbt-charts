"""KPI value and label font colors are authorable, and the specific key wins.

``style.value.font.color`` and ``style.label.font.color`` both validated and
were both discarded: the value fill read only ``style.color`` and the label
fill was hardcoded to the theme ink. These pin that each key now paints its own
slot, and that the more specific key beats the shared whole-card
``style.font.color`` lever (``style.color`` itself was later deleted — the
family's sole bare-string outlier — see
``dbt-charts/tests/core/compile/test_kpi_style_color_deletion.py``).
"""

from __future__ import annotations

import re
from typing import Any

from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.resolve import resolve

_ROWS = [{"revenue": 128_000}]
_QUERY_REGISTRY = {"q": ValuesQuery(rows=_ROWS)}


def _theme_ink() -> str:
    """The ink the KPI cascade lands on when nothing nearer names a color.

    Read from the resolved theme rather than pinned as a hex: this file is
    about slot precedence, and a token tweak in ``_base.yaml`` is not a
    regression in it.
    """
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    _, board_ctx = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    return board_ctx.kpi.font.color


def _render(style: dict[str, Any] | None) -> str:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    from ..._engine_render import render_kpi_svg

    board_rs, board_ctx = resolve_style_and_context(
        get_theme_style(get_default_theme_name())
    )
    chart_def: dict[str, Any] = {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Revenue",
    }
    if style is not None:
        chart_def["style"] = style
    flat = normalize_chart("chart1", chart_def, _QUERY_REGISTRY, sources={})
    resolved = resolve(flat, _ROWS, chart_style_context=board_ctx)
    return render_kpi_svg(resolved, _ROWS, board_style=board_rs)


def _value_fill(svg: str) -> str:
    """Fill on the headline value's first tspan."""
    head = svg[: svg.index('data-authored-kind="label"')]
    match = re.search(r'<tspan[^>]*fill="([^"]+)"', head)
    assert match is not None, f"no value tspan fill in {head!r}"
    return match.group(1)


def _label_fill(svg: str) -> str:
    match = re.search(r'data-authored-kind="label"[^>]*fill="([^"]+)"', svg)
    assert match is not None, "no label fill in the rendered KPI"
    return match.group(1)


def test_value_font_color_paints_the_headline() -> None:
    svg = _render({"value": {"font": {"color": "#FF0000"}}})
    assert _value_fill(svg) == "#FF0000"


def test_label_font_color_paints_the_label() -> None:
    svg = _render({"label": {"font": {"color": "#FF0000"}}})
    assert _label_fill(svg) == "#FF0000"


def test_the_specific_value_font_color_beats_the_shared_font_color() -> None:
    """`style.font.color` is the whole-card slot; `style.value.font.color` names one
    element. The more specific key wins, as it does for every other style patch —
    the reverse was the sharpest part of the original bug.
    """
    svg = _render(
        {"font": {"color": "#FF0000"}, "value": {"font": {"color": "#00A000"}}}
    )
    assert _value_fill(svg) == "#00A000"


def test_shared_font_color_alone_paints_both_value_and_label() -> None:
    """`style.font.color` is the whole-card lever — unlike the deleted
    `style.color` (which only ever reached the value), it paints both the
    value and the label when neither names its own slot."""
    svg = _render({"font": {"color": "#FF0000"}})
    assert _value_fill(svg) == "#FF0000"
    assert _label_fill(svg) == "#FF0000"


def test_an_unstyled_kpi_paints_both_slots_in_theme_ink() -> None:
    svg = _render(None)
    assert _value_fill(svg) == _theme_ink()
    assert _label_fill(svg) == _theme_ink()


def test_value_and_label_font_colors_are_independent() -> None:
    svg = _render(
        {
            "value": {"font": {"color": "#FF0000"}},
            "label": {"font": {"color": "#0000FF"}},
        }
    )
    assert _value_fill(svg) == "#FF0000"
    assert _label_fill(svg) == "#0000FF"
